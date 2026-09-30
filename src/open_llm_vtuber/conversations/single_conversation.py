from typing import Union, List, Dict, Any, Optional
import asyncio
import json
from loguru import logger
import numpy as np

from .conversation_utils import (
    create_batch_input,
    process_agent_output,
    send_conversation_start_signals,
    process_user_input,
    finalize_conversation_turn,
    cleanup_conversation,
    EMOJI_LIST,
)
from .types import WebSocketSend
from .tts_manager import TTSTaskManager
from ..chat_history_manager import store_message, get_history
from ..service_context import ServiceContext

# Long-term memory (AI companion memory) — optional module, degrades gracefully
try:
    from src.open_llm_vtuber import long_term_memory as _ltm
except ImportError:  # direct in-package import (no src. prefix)
    from .. import long_term_memory as _ltm

# Experience memory (Phase 4) — concrete interaction episodes; optional,
# deterministic capture, never blocks or breaks the chat turn
try:
    from src.open_llm_vtuber import experience as _xp
except ImportError:  # direct in-package import (no src. prefix)
    from .. import experience as _xp

# strong references to fire-and-forget extraction tasks so they are not
# garbage-collected mid-flight
_ltm_extraction_tasks = set()
_xp_capture_tasks = set()

# Import necessary types from agent outputs
from ..agent.output_types import SentenceOutput, AudioOutput


async def process_single_conversation(
    context: ServiceContext,
    websocket_send: WebSocketSend,
    client_uid: str,
    user_input: Union[str, np.ndarray],
    images: Optional[List[Dict[str, Any]]] = None,
    session_emoji: str = np.random.choice(EMOJI_LIST),
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Process a single-user conversation turn

    Args:
        context: Service context containing all configurations and engines
        websocket_send: WebSocket send function
        client_uid: Client unique identifier
        user_input: Text or audio input from user
        images: Optional list of image data
        session_emoji: Emoji identifier for the conversation
        metadata: Optional metadata for special processing flags

    Returns:
        str: Complete response text
    """
    # Create TTSTaskManager for this conversation
    tts_manager = TTSTaskManager()
    full_response = ""  # Initialize full_response here

    # Experience capture (Phase 4): one engine per turn, deterministic
    # records fed by structures this function already produces
    _xp_engine = _xp.ExperienceEngine.start(
        context.character_config.conf_uid,
        context.history_uid or "",
        "proactive" if (metadata or {}).get("skip_memory", False) else "chat",
    )
    _xp_tool_calls: List[Dict[str, str]] = []
    _xp_agent_errored = False

    try:
        # Send initial signals
        await send_conversation_start_signals(websocket_send)
        logger.info(f"New Conversation Chain {session_emoji} started!")

        # Process user input
        input_text = await process_user_input(
            user_input, context.asr_engine, websocket_send
        )

        # Long-term memory: sync pre-chat retrieval (keyword-based, fast).
        # Result travels to the agent via batch_input.metadata["ltm_context"];
        # any failure just skips injection (graceful degradation).
        # NOTE: metadata is None for normal user turns (only proactive-speak
        # sets it), so create it here when memory has something to inject.
        if metadata is None or not metadata.get("skip_memory", False):
            try:
                ltm_context = _ltm.build_retrieval_context(
                    conf_uid=context.character_config.conf_uid,
                    user_text=input_text,
                )
                if ltm_context is not None:
                    if metadata is None:
                        metadata = {}
                    metadata["ltm_context"] = ltm_context
            except Exception as ltm_err:
                logger.debug(f"[LTM] retrieval skipped: {ltm_err}")

        # Create batch input
        batch_input = create_batch_input(
            input_text=input_text,
            images=images,
            from_name=context.character_config.human_name,
            metadata=metadata,
        )

        # Store user message (check if we should skip storing to history)
        skip_history = metadata and metadata.get("skip_history", False)
        if context.history_uid and not skip_history:
            store_message(
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="human",
                content=input_text,
                name=context.character_config.human_name,
            )

        if skip_history:
            logger.debug("Skipping storing user input to history (proactive speak)")

        logger.info(f"User input: {input_text}")
        if images:
            logger.info(f"With {len(images)} images")
        _xp_engine.record_user_input(input_text)

        try:
            # agent.chat yields Union[SentenceOutput, Dict[str, Any]]
            agent_output_stream = context.agent_engine.chat(batch_input)

            async for output_item in agent_output_stream:
                if (
                    isinstance(output_item, dict)
                    and output_item.get("type") == "tool_call_status"
                ):
                    # Handle tool status event: send WebSocket message
                    output_item["name"] = context.character_config.character_name
                    logger.debug(f"Sending tool status update: {output_item}")

                    # Experience capture: log the tool event (name+status only)
                    _xp_engine.record_tool(
                        output_item.get("tool_name", ""),
                        output_item.get("status", ""),
                    )

                    await websocket_send(json.dumps(output_item))

                elif isinstance(output_item, (SentenceOutput, AudioOutput)):
                    # Handle SentenceOutput or AudioOutput
                    response_part = await process_agent_output(
                        output=output_item,
                        character_config=context.character_config,
                        live2d_model=context.live2d_model,
                        tts_engine=context.tts_engine,
                        websocket_send=websocket_send,  # Pass websocket_send for audio/tts messages
                        tts_manager=tts_manager,
                        translate_engine=context.translate_engine,
                    )
                    # Ensure response_part is treated as a string before concatenation
                    response_part_str = (
                        str(response_part) if response_part is not None else ""
                    )
                    full_response += response_part_str  # Accumulate text response
                else:
                    logger.warning(
                        f"Received unexpected item type from agent chat stream: {type(output_item)}"
                    )
                    logger.debug(f"Unexpected item content: {output_item}")

        except Exception as e:
            logger.exception(
                f"Error processing agent response stream: {e}"
            )  # Log with stack trace
            _xp_agent_errored = True  # experience outcome marker
            await websocket_send(
                json.dumps(
                    {
                        "type": "error",
                        "message": f"Error processing agent response: {str(e)}",
                    }
                )
            )
            # full_response will contain partial response before error
        # --- End processing agent response ---

        # Wait for any pending TTS tasks
        if tts_manager.task_list:
            await asyncio.gather(*tts_manager.task_list)
            await websocket_send(json.dumps({"type": "backend-synth-complete"}))

        await finalize_conversation_turn(
            tts_manager=tts_manager,
            websocket_send=websocket_send,
            client_uid=client_uid,
        )

        if context.history_uid and full_response:  # Check full_response before storing
            store_message(
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="ai",
                content=full_response,
                name=context.character_config.character_name,
                avatar=context.character_config.avatar,
            )
            logger.info(f"AI response: {full_response}")

        # Long-term memory: async post-reply extraction (fire-and-forget).
        # Reuses the live agent's stateless LLM so the extractor stays
        # provider-agnostic; never blocks or breaks the chat turn.
        # Runs independently of persistent history (LTM keeps its own
        # per-conf_uid DB). Skipped for proactive-speak (skip_memory) and
        # empty replies.
        if full_response and (metadata is None or not metadata.get("skip_memory", False)):
            try:
                _agent_llm = getattr(context.agent_engine, "_llm", None)
                _extraction_task = asyncio.create_task(
                    _ltm.extract_from_turn(
                        conf_uid=context.character_config.conf_uid,
                        user_text=input_text,
                        ai_text=full_response,
                        llm=_agent_llm,
                        history_snapshot=get_history(
                            context.character_config.conf_uid,
                            context.history_uid,
                        ),
                    )
                )
                _ltm_extraction_tasks.add(_extraction_task)
                _extraction_task.add_done_callback(_ltm_extraction_tasks.discard)
            except Exception as ltm_err:
                logger.debug(f"[LTM] extraction scheduling failed: {ltm_err}")

        # Experience capture (Phase 4): finalize the episode and persist in
        # a fire-and-forget task (same discipline as the LTM hook above —
        # strong ref + done callback; failures log a warning, never break
        # the chat turn). Deterministic capture only: no LLM, no analysis.
        try:
            _xp_engine.record_ai_response(full_response)
            _xp_engine.record_outcome(
                "AI 已回复，用户可继续对话"
                if full_response
                else "AI 未产生文本回复"
            )
            if _xp_agent_errored and not full_response:
                _xp_outcome_type = "ai_error"
            else:
                _xp_outcome_type = "turn_complete" if full_response else "empty_reply"
            _xp_record = _xp_engine.finalize(_xp_outcome_type)
            # persist_record is synchronous (SQLite/local IO) — run it on
            # a worker thread so the event loop (chat latency) is untouched
            _xp_task = asyncio.create_task(
                asyncio.to_thread(_xp.persist_record, _xp_record))
            _xp_capture_tasks.add(_xp_task)
            _xp_task.add_done_callback(_xp_capture_tasks.discard)
        except Exception as xp_err:
            logger.warning(f"[XP] capture scheduling failed: {xp_err}")

        return full_response  # Return accumulated full_response

    except asyncio.CancelledError:
        logger.info(f"🤡👍 Conversation {session_emoji} cancelled because interrupted.")
        raise
    except Exception as e:
        logger.error(f"Error in conversation chain: {e}")
        await websocket_send(
            json.dumps({"type": "error", "message": f"Conversation error: {str(e)}"})
        )
        raise
    finally:
        cleanup_conversation(tts_manager, session_emoji)

"""Per-turn LLM message assembly for BasicMemoryAgent.

Split out of ``agents/basic_memory_agent.py`` (multi-responsibility
refactor, no behavior change). Turns a :class:`BatchInput` plus the
rolling session memory into the ``messages`` list handed to the LLM,
and records the user turn back into memory.
"""

from typing import Any, Dict, List

from loguru import logger

from ...input_types import BatchInput, TextSource
from .session_memory import SessionMemory


def to_text_prompt(input_data: BatchInput) -> str:
    """Format input data to text prompt."""
    message_parts = []

    for text_data in input_data.texts:
        if text_data.source == TextSource.INPUT:
            message_parts.append(text_data.content)
        elif text_data.source == TextSource.CLIPBOARD:
            message_parts.append(
                f"[User shared content from clipboard: {text_data.content}]"
            )

    if input_data.images:
        message_parts.append("\n[User has also provided images]")

    return "\n".join(message_parts).strip()


def to_messages(
    memory: SessionMemory, input_data: BatchInput
) -> List[Dict[str, Any]]:
    """Prepare messages for LLM API call."""
    messages = memory.messages.copy()
    user_content = []
    text_prompt = to_text_prompt(input_data)

    # Long-term memory injection (per-turn, from the conversation layer
    # via metadata["ltm_context"]). Placed BEFORE the user's own text so
    # the freshest input stays last. Added ONLY to the API message —
    # never to self._memory — so each turn gets fresh retrieval without
    # bloating the rolling short-term history. Already sanitized and
    # bracketed as non-instruction reference data by the memory module.
    ltm_injection = None
    try:
        if input_data.metadata:
            ltm_ctx = input_data.metadata.get("ltm_context")
            if isinstance(ltm_ctx, dict):
                ltm_injection = ltm_ctx.get("injection_text")
    except Exception:
        ltm_injection = None
    if ltm_injection:
        user_content.append({"type": "text", "text": ltm_injection})

    if text_prompt:
        user_content.append({"type": "text", "text": text_prompt})

    if input_data.images:
        image_added = False
        for img_data in input_data.images:
            if isinstance(img_data.data, str) and img_data.data.startswith(
                "data:image"
            ):
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": img_data.data, "detail": "auto"},
                    }
                )
                image_added = True
            else:
                logger.error(
                    f"Invalid image data format: {type(img_data.data)}. Skipping image."
                )

        if not image_added and not text_prompt:
            logger.warning(
                "User input contains images but none could be processed."
            )

    if user_content:
        user_message = {"role": "user", "content": user_content}
        messages.append(user_message)

        skip_memory = False
        if input_data.metadata and input_data.metadata.get("skip_memory", False):
            skip_memory = True

        if not skip_memory and (text_prompt or input_data.images):
            memory.add_message(
                text_prompt if text_prompt else "[User provided image(s)]", "user"
            )
    else:
        logger.warning("No content generated for user message.")

    return messages

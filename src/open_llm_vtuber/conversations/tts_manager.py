import asyncio
import json
import re
import uuid
from datetime import datetime
from typing import List, Optional, Dict
from loguru import logger

from ..agent.output_types import DisplayText, Actions
from ..live2d_model import Live2dModel
from ..tts.tts_interface import TTSInterface
from ..utils.stream_audio import prepare_audio_payload
from .types import WebSocketSend


class TTSTaskManager:
    """Manages TTS tasks and ensures ordered delivery to frontend while allowing parallel TTS generation"""

    def __init__(self) -> None:
        self.task_list: List[asyncio.Task] = []
        self._lock = asyncio.Lock()
        # Queue to store ordered payloads
        self._payload_queue: asyncio.Queue[Dict] = asyncio.Queue()
        # Task to handle sending payloads in order
        self._sender_task: Optional[asyncio.Task] = None
        # Counter for maintaining order
        self._sequence_counter = 0
        self._next_sequence_to_send = 0

    async def speak(
        self,
        tts_text: str,
        display_text: DisplayText,
        actions: Optional[Actions],
        live2d_model: Live2dModel,
        tts_engine: TTSInterface,
        websocket_send: WebSocketSend,
    ) -> None:
        """
        Queue a TTS task while maintaining order of delivery.

        Args:
            tts_text: Text to synthesize
            display_text: Text to display in UI
            actions: Live2D model actions
            live2d_model: Live2D model instance
            tts_engine: TTS engine instance
            websocket_send: WebSocket send function
        """
        if len(re.sub(r'[\s.,!?，。！？\'"』」）】\s]+', "", tts_text)) == 0:
            logger.debug("Empty TTS text, sending silent display payload")
            # Get current sequence number for silent payload
            current_sequence = self._sequence_counter
            self._sequence_counter += 1

            # Start sender task if not running
            if not self._sender_task or self._sender_task.done():
                self._sender_task = asyncio.create_task(
                    self._process_payload_queue(websocket_send)
                )

            await self._send_silent_payload(display_text, actions, current_sequence)
            return

        logger.debug(
            f"🏃Queuing TTS task for: '''{tts_text}''' (by {display_text.name})"
        )

        # Get current sequence number
        current_sequence = self._sequence_counter
        self._sequence_counter += 1

        # Start sender task if not running
        if not self._sender_task or self._sender_task.done():
            self._sender_task = asyncio.create_task(
                self._process_payload_queue(websocket_send)
            )

        # STREAMED-FIRST: when the engine supports streaming
        # synthesis, push chunks directly (in sequence order);
        # non-streaming engines keep the queued path.
        gen = getattr(tts_engine,
                      "async_generate_audio_streamed", None)
        if gen is not None:
            task = asyncio.create_task(
                self._streamed_ordered(
                    tts_text=tts_text,
                    display_text=display_text,
                    actions=actions,
                    live2d_model=live2d_model,
                    tts_engine=tts_engine,
                    sequence_number=current_sequence,
                    websocket_send=websocket_send,
                )
            )
        else:
            task = asyncio.create_task(
                self._process_tts(
                    tts_text=tts_text,
                    display_text=display_text,
                    actions=actions,
                    live2d_model=live2d_model,
                    tts_engine=tts_engine,
                    sequence_number=current_sequence,
                )
            )
        self.task_list.append(task)

    async def _process_payload_queue(self, websocket_send: WebSocketSend) -> None:
        """
        Process and send payloads in correct order.
        Runs continuously until all payloads are processed.
        """
        buffered_payloads: Dict[int, Dict] = {}

        while True:
            try:
                # Get payload from queue
                payload, sequence_number = await self._payload_queue.get()
                buffered_payloads[sequence_number] = payload

                # Send payloads in order
                while self._next_sequence_to_send in buffered_payloads:
                    next_payload = buffered_payloads.pop(self._next_sequence_to_send)
                    await websocket_send(json.dumps(next_payload))
                    self._next_sequence_to_send += 1

                self._payload_queue.task_done()

            except asyncio.CancelledError:
                break

    async def _send_silent_payload(
        self,
        display_text: DisplayText,
        actions: Optional[Actions],
        sequence_number: int,
    ) -> None:
        """Queue a silent audio payload"""
        audio_payload = prepare_audio_payload(
            audio_path=None,
            display_text=display_text,
            actions=actions,
        )
        await self._payload_queue.put((audio_payload, sequence_number))


    async def _streamed_ordered(self, **kwargs):
        # Stream sentences in sequence order: wait until all
        # earlier sequences have been delivered, then run the
        # streaming send. Preserves the original queue's
        # ordering contract while enabling chunk streaming.
        seq = kwargs["sequence_number"]
        while self._next_sequence_to_send < seq:
            await asyncio.sleep(0.05)
        await self._process_tts_streamed(**kwargs)
        self._next_sequence_to_send = max(
            self._next_sequence_to_send, seq + 1)

    async def _process_tts_streamed(
        self,
        tts_text: str,
        display_text: DisplayText,
        actions: Optional[Actions],
        live2d_model: Live2dModel,
        tts_engine: TTSInterface,
        sequence_number: int,
        websocket_send: WebSocketSend,
    ) -> None:
        """STREAMING variant: synthesize with the streaming engine and
        push the sentence audio to the client in CHUNKS while later
        chunks are still synthesizing.

        Wire protocol (additive, backward compatible):
          {type: "audio", stream: "start",  sequence, ...meta}
          {type: "audio", stream: "chunk",  sequence, chunk_index,
           audio: <base64 wav chunk>, slice_length}
          {type: "audio", stream: "end",    sequence, volumes}
        Legacy clients that ignore the `stream` field still work: the
        FINAL message carries the full payload shape minus the whole
        audio blob, and non-streaming engines keep the old path.
        """
        import base64 as _b64
        from ..utils.stream_audio import prepare_audio_payload
        audio_file_path = None
        try:
            # 1) streaming synthesis when the engine supports it
            gen = getattr(tts_engine,
                          "async_generate_audio_streamed", None)
            if gen is not None:
                stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                uid = str(uuid.uuid4())[:8]
                audio_file_path, first_ms = await gen(
                    text=tts_text,
                    file_name_no_ext="%s_%s" % (stamp, uid),
                )
            else:
                audio_file_path = await self._generate_audio(
                    tts_engine, tts_text)
                first_ms = None
            if not audio_file_path:
                raise ValueError("TTS produced no audio")

            # 2) load + chunk + stream
            from pydub import AudioSegment
            audio = AudioSegment.from_file(audio_file_path)
            wav_bytes = audio.export(format="wav").read()
            chunk_ms = 2000   # 2s slices
            n_chunks = max(1, len(audio) // chunk_ms +
                           (1 if len(audio) % chunk_ms else 0))
            meta = {
                "type": "audio",
                "stream": "start",
                "sequence": sequence_number,
                "chunks": n_chunks,
                "first_chunk_ms": first_ms,
                "display_text": (display_text.to_dict()
                                 if display_text else None),
                "actions": actions.to_dict() if actions else None,
                "forwarded": False,
            }
            await websocket_send(json.dumps(meta))
            byte_step = (len(wav_bytes) + n_chunks - 1) // n_chunks
            for i in range(n_chunks):
                piece = wav_bytes[i * byte_step:(i + 1) * byte_step]
                msg = {
                    "type": "audio",
                    "stream": "chunk",
                    "sequence": sequence_number,
                    "chunk_index": i,
                    "audio": _b64.b64encode(piece).decode("utf-8"),
                    "slice_length": chunk_ms,
                }
                await websocket_send(json.dumps(msg))
            end = {
                "type": "audio",
                "stream": "end",
                "sequence": sequence_number,
                "volumes": _volumes(audio, 20),
            }
            await websocket_send(json.dumps(end))
        except Exception as e:
            logger.error("streaming TTS failed, fallback silent: %s" % e)
            payload = prepare_audio_payload(
                audio_path=None, display_text=display_text,
                actions=actions)
            payload["sequence"] = sequence_number
            await self._payload_queue.put((payload, sequence_number))
        finally:
            if audio_file_path:
                tts_engine.remove_file(audio_file_path)


    async def _process_tts(
        self,
        tts_text: str,
        display_text: DisplayText,
        actions: Optional[Actions],
        live2d_model: Live2dModel,
        tts_engine: TTSInterface,
        sequence_number: int,
    ) -> None:
        """Process TTS generation and queue the result for ordered delivery"""
        audio_file_path = None
        try:
            audio_file_path = await self._generate_audio(tts_engine, tts_text)
            payload = prepare_audio_payload(
                audio_path=audio_file_path,
                display_text=display_text,
                actions=actions,
            )
            # Queue the payload with its sequence number
            await self._payload_queue.put((payload, sequence_number))

        except Exception as e:
            logger.error(f"Error preparing audio payload: {e}")
            # Queue silent payload for error case
            payload = prepare_audio_payload(
                audio_path=None,
                display_text=display_text,
                actions=actions,
            )
            await self._payload_queue.put((payload, sequence_number))

        finally:
            if audio_file_path:
                tts_engine.remove_file(audio_file_path)
                logger.debug("Audio cache file cleaned.")

    async def _generate_audio(self, tts_engine: TTSInterface, text: str) -> str:
        """Generate audio file from text"""
        logger.debug(f"🏃Generating audio for '''{text}'''...")
        return await tts_engine.async_generate_audio(
            text=text,
            file_name_no_ext=f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:8]}",
        )

    def clear(self) -> None:
        """Clear all pending tasks and reset state"""
        self.task_list.clear()
        if self._sender_task:
            self._sender_task.cancel()
        self._sequence_counter = 0
        self._next_sequence_to_send = 0
        # Create a new queue to clear any pending items
        self._payload_queue = asyncio.Queue()



def _volumes(audio, chunk_ms):
    from pydub.utils import make_chunks
    chunks = make_chunks(audio, chunk_ms)
    vols = [c.rms for c in chunks]
    top = max(vols) if vols else 0
    if top == 0:
        return []
    return [v / top for v in vols]


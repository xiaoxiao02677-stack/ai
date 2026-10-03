import sys
import os

import edge_tts
from loguru import logger
from .tts_interface import TTSInterface

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)


# Check out doc at https://github.com/rany2/edge-tts
# Use `edge-tts --list-voices` to list all available voices


class TTSEngine(TTSInterface):
    def __init__(self, voice="en-US-AvaMultilingualNeural"):
        self.voice = voice

        self.temp_audio_file = "temp"
        self.file_extension = "mp3"
        self.new_audio_dir = "cache"

        if not os.path.exists(self.new_audio_dir):
            os.makedirs(self.new_audio_dir)

    def generate_audio(self, text, file_name_no_ext=None):
        """
        Generate speech audio file using TTS.
        text: str
            the text to speak
        file_name_no_ext: str
            name of the file without extension


        Returns:
        str: the path to the generated audio file

        """
        file_name = self.generate_cache_file_name(file_name_no_ext, self.file_extension)

        try:
            communicate = edge_tts.Communicate(text, self.voice)
            communicate.save_sync(file_name)
        except Exception as e:
            logger.critical(f"\nError: edge-tts unable to generate audio: {e}")
            logger.critical("It's possible that edge-tts is blocked in your region.")
            return None

        return file_name
    async def async_generate_audio_streamed(self, text: str,
                                             file_name_no_ext=None):
        """STREAMING synthesis: consume edge-tts audio chunks as they
        arrive and write them to disk incrementally.

        Returns (file_name, first_chunk_latency_ms). The file is
        COMPLETE when the coroutine returns (same contract as
        async_generate_audio), but the first bytes hit the cache
        early, so downstream chunked sending can start before the
        whole sentence is synthesized. On failure the partial file
        is removed and (None, None) is returned (fail-closed, no
        partial audio exposed).
        """
        import time as _time
        file_name = self.generate_cache_file_name(file_name_no_ext,
                                                  self.file_extension)
        t0 = _time.time()
        first_ms = None
        try:
            communicate = edge_tts.Communicate(text, self.voice)
            with open(file_name, "wb") as f:
                async for chunk in communicate.stream():
                    # edge-tts 7.x message: {type: "audio",
                    # data: <bytes>} (older 6.x emitted the
                    # audio under the "audio" key)
                    data = None
                    if isinstance(chunk, dict):
                        if chunk.get("type") == "audio":
                            data = chunk.get("data")
                        elif chunk.get("audio"):
                            data = chunk.get("audio")
                    if data:
                        if first_ms is None:
                            first_ms = int(
                                (_time.time() - t0) * 1000)
                        f.write(data)
        except Exception as e:
            logger.critical(
                "edge-tts streaming synthesis failed: %s" % e)
            try:
                os.remove(file_name)
            except OSError:
                pass
            return None, None
        if first_ms is None or not os.path.getsize(file_name):
            try:
                os.remove(file_name)
            except OSError:
                pass
            return None, None
        return file_name, first_ms



# en-US-AvaMultilingualNeural
# en-US-EmmaMultilingualNeural
# en-US-JennyNeural

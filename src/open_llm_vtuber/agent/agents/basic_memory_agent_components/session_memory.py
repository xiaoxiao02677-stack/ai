"""Rolling short-term session memory for BasicMemoryAgent.

Split out of ``agents/basic_memory_agent.py`` (multi-responsibility
refactor, no behavior change). Owns the per-session message list:

* appending messages with text extraction and dedup,
* loading memory from persisted chat history,
* rewriting memory on user interruption.

The companion interrupt role is decided by the agent (it depends on the
agent's ``interrupt_method`` setting); the once-per-turn interrupt
guard flag is kept by the agent itself.
"""

from typing import Any, Dict, List, Union

from loguru import logger

from ....chat_history_manager import get_history
from ...output_types import DisplayText


class SessionMemory:
    """Manages the rolling list of short-term conversation messages."""

    def __init__(self) -> None:
        self._memory: List[Dict[str, Any]] = []

    @property
    def messages(self) -> List[Dict[str, Any]]:
        """The raw rolling message list (live reference)."""
        return self._memory

    def add_message(
        self,
        message: Union[str, List[Dict[str, Any]]],
        role: str,
        display_text: DisplayText | None = None,
        skip_memory: bool = False,
    ):
        """Add message to memory."""
        if skip_memory:
            return

        text_content = ""
        if isinstance(message, list):
            for item in message:
                if item.get("type") == "text":
                    text_content += item["text"] + " "
            text_content = text_content.strip()
        elif isinstance(message, str):
            text_content = message
        else:
            logger.warning(
                f"_add_message received unexpected message type: {type(message)}"
            )
            text_content = str(message)

        if not text_content and role == "assistant":
            return

        message_data = {
            "role": role,
            "content": text_content,
        }

        if display_text:
            if display_text.name:
                message_data["name"] = display_text.name
            if display_text.avatar:
                message_data["avatar"] = display_text.avatar

        if (
            self._memory
            and self._memory[-1]["role"] == role
            and self._memory[-1]["content"] == text_content
        ):
            return

        self._memory.append(message_data)

    def set_from_history(self, conf_uid: str, history_uid: str) -> None:
        """Load memory from chat history."""
        messages = get_history(conf_uid, history_uid)

        self._memory = []
        for msg in messages:
            role = "user" if msg["role"] == "human" else "assistant"
            content = msg["content"]
            if isinstance(content, str) and content:
                self._memory.append(
                    {
                        "role": role,
                        "content": content,
                    }
                )
            else:
                logger.warning(f"Skipping invalid message from history: {msg}")
        logger.info(f"Loaded {len(self._memory)} messages from history.")

    def handle_interrupt(self, heard_response: str, interrupt_role: str) -> None:
        """Handle user interruption.

        Args:
            heard_response: The part of the response the user heard before
                interrupting.
            interrupt_role: ``"system"`` or ``"user"`` — the role the
                ``[Interrupted by user]`` marker is written as.
        """
        if self._memory and self._memory[-1]["role"] == "assistant":
            if not self._memory[-1]["content"].endswith("..."):
                self._memory[-1]["content"] = heard_response + "..."
            else:
                self._memory[-1]["content"] = heard_response + "..."
        else:
            if heard_response:
                self._memory.append(
                    {
                        "role": "assistant",
                        "content": heard_response + "..."
                    }
                )

        self._memory.append(
            {
                "role": interrupt_role,
                "content": "[Interrupted by user]",
            }
        )
        logger.info(f"Handled interrupt with role '{interrupt_role}'.")

    def append(self, message: Dict[str, Any]) -> None:
        """Append a raw message dict to the rolling memory."""
        self._memory.append(message)

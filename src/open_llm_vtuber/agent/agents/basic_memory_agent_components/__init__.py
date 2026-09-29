"""Split-out components of BasicMemoryAgent.

Each component owns one responsibility that used to live in the single
720-line ``agents/basic_memory_agent.py``:

* :mod:`.session_memory` — rolling short-term session memory
  (append/dedup, history reload, interrupt handling).
* :mod:`.prompt_assembler` — per-turn LLM message list assembly,
  including the long-term-memory injection block.
* :mod:`.tool_interaction` — the Claude / OpenAI / prompt-mode
  tool-calling interaction loops.

``agents/basic_memory_agent.py`` remains the orchestrator and the only
public entry point; nothing outside this package should import these
components directly.
"""

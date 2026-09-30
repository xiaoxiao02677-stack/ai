"""MemoryManager: facade wiring store/extractor/dedup/conflict/retriever.

One manager per conf_uid. Provides the two high-level operations used by
the conversation layer:

  retrieve_for_prompt(user_text) -> dict placed in metadata["ltm_context"]
  extract_memories(user_text, ai_text, ...) -> await after reply (async task)

Also owns: keyword upserts, turn counting, rolling summary maintenance and
user-state persistence. All public methods swallow exceptions (graceful
degradation per spec).
"""

import asyncio
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from .schemas import MemoryRecord, UserState
from .store import MemoryStore
from .extractor import LLMExtractor
from .deduplicator import MemoryDeduplicator, MemoryConflictResolver
from .retriever import MemoryRetriever
from .prompt_builder import build_injection_text
from .keyword_extractor import extract_keywords


class MemoryManager:
    def __init__(self, conf_uid: str, config: Dict[str, Any], llm=None):
        self.conf_uid = conf_uid
        self.config = config
        self.llm = llm
        # config flows into the store so the configured storage backend
        # (storage.provider: sqlite | hermes) is honored per deployment
        self.store = MemoryStore(conf_uid, config=config)
        self.extractor = LLMExtractor(config, llm)
        self.deduplicator = MemoryDeduplicator()
        self.conflict_resolver = MemoryConflictResolver()
        self.retriever = MemoryRetriever(config, self.store)
        self._last_extraction_at = 0.0
        # lightweight in-process summary cache
        self._summary_cache: Optional[str] = None
        self._summary_dirty = False

    def attach_llm(self, llm) -> None:
        """Late-bind an LLM for extraction.

        Managers are typically first created by the retrieval hook, which has
        no LLM; the extraction call upgrades them later. Both the facade and
        the extractor must see the LLM, or extraction silently degrades to
        rule fallback (the extractor keeps its own reference).

        Callers resolve the effective LLM (dedicated vs live agent) upstream
        in __init__._effective_extraction_llm; this method only swaps when
        the identity actually changed, so config-driven rebuilds propagate.
        """
        if llm is None or self.llm is llm:
            return
        self.llm = llm
        self.extractor.llm = llm

    # ------------------------------------------------------------------ chat

    def retrieve_for_prompt(self, user_text: str) -> Optional[Dict[str, Any]]:
        """Pre-chat retrieval (sync, fast). Result goes into metadata."""
        try:
            result = self.retriever.build_prompt_block(user_text)
            if result is None:
                return None
            state = self.store.get_state()
            summary = self.store.get_summary()
            injection_text = build_injection_text(
                memory_lines=result["block"].split("\n"),
                user_state=state.to_dict() if state else None,
                conversation_summary=summary if self._should_inject_summary() else "",
            )
            if not injection_text:
                return None
            self.store.mark_used(result["ids"])
            payload = {
                "injection_text": injection_text,
                "memory_ids": result["ids"],
                "scores": result["scores"],
                "matched": result["matched"],
            }
            if self.config.get("memory_debug", False):
                logger.debug(
                    f"[LTM] injected {len(result['ids'])} memories "
                    f"(scores={result['scores']}) for conf={self.conf_uid}"
                )
            return payload
        except Exception as e:
            logger.warning(f"[LTM] retrieve_for_prompt failed: {e}")
            return None

    def _should_inject_summary(self) -> bool:
        """Inject the rolling summary only when it exists and is fresh."""
        summary = self.store.get_summary()
        return bool(summary and summary.strip())

    # ---------------------------------------------------------------- extract

    async def extract_memories(
        self,
        user_text: str,
        ai_text: str,
        history_snapshot: Optional[List[Dict[str, Any]]] = None,
    ) -> List[MemoryRecord]:
        """Post-reply extraction pipeline. Never raises."""
        stored: List[MemoryRecord] = []
        try:
            # keyword table update (rule-based, cheap — runs even during cooldown)
            for kw in extract_keywords(user_text):
                self.store.upsert_keyword(kw["keyword"], kw["category"])

            # turn counting + state topic (local, per-turn)
            self._bump_turn_and_topic(user_text)

            # cooldown guard gates only the expensive LLM path; rule
            # fallback (no LLM) is local regex — always run it
            cooldown = float(self.config.get("extraction_cooldown", 1.0))
            now = time.time()
            if self.extractor.llm is not None and now - self._last_extraction_at < cooldown:
                return stored
            self._last_extraction_at = now

            # LLM extraction (or rule fallback)
            candidates = await self.extractor.extract(
                conf_uid=self.conf_uid,
                user_text=user_text,
                ai_text=ai_text,
            )
            existing = self.store.list_memories(status="active")

            for cand in candidates:
                try:
                    # LLM candidates sometimes arrive with empty keyword
                    # lists; enrich from the rule extractor so dedup /
                    # conflict subject checks don't silently miss
                    if not cand.keywords:
                        cand.keywords = [
                            k["keyword"] for k in extract_keywords(user_text)
                        ][:6]
                    dup = self.deduplicator.find_duplicate(existing, cand)
                    if dup is not None:
                        reinforced = self.deduplicator.reinforce(dup, cand)
                        self.store.update_memory(reinforced)
                        logger.debug(f"[LTM] reinforced memory: {reinforced.content[:40]}")
                        continue
                    conflict = self.conflict_resolver.find_conflict(existing, cand)
                    if conflict is not None:
                        old, new = self.conflict_resolver.resolve(conflict, cand)
                        self.store.update_memory(old)
                        self.store.add_memory(new)
                        # drop the deprecated record from the working set so
                        # later candidates in this batch can't conflict with
                        # it again (double history entries)
                        existing = [
                            e for e in existing if e.memory_id != old.memory_id
                        ]
                        existing.append(new)
                        logger.info(
                            f"[LTM] conflict resolved: '{old.content[:30]}' -> '{new.content[:30]}'"
                        )
                        stored.append(new)
                        continue
                    self.store.add_memory(cand)
                    existing.append(cand)
                    stored.append(cand)
                    logger.info(f"[LTM] new memory ({cand.memory_type}): {cand.content[:50]}")
                except Exception as e:
                    logger.warning(f"[LTM] storing candidate failed: {e}")

            # rolling summary maintenance
            self._maybe_update_summary(history_snapshot)

            return stored
        except Exception as e:
            logger.error(f"[LTM] extract_memories failed: {e}")
            return stored

    def _bump_turn_and_topic(self, user_text: str) -> None:
        try:
            self.store.bump_turn_count()
            state = self.store.get_state() or UserState(conf_uid=self.conf_uid)
            topic_kws = [
                k for k in extract_keywords(user_text) if k["category"] != "interest"
            ]
            if topic_kws:
                state.current_topic = topic_kws[0]["keyword"]
            self.store.save_state(state)
        except Exception as e:
            logger.debug(f"[LTM] turn/topic update failed: {e}")

    def _maybe_update_summary(
        self, history_snapshot: Optional[List[Dict[str, Any]]]
    ) -> None:
        """Keep the rolling conversation summary from the live history.

        Phase-1 approach: maintain a compact tail-summary from the most
        recent messages in the snapshot (no extra LLM call). Deeper LLM
        summarization is a phase-2 hook.
        """
        try:
            interval = int(self.config.get("summary_interval", 20))
            turns = self.store.get_turn_count()
            if turns == 0 or turns % interval != 0:
                return
            msgs = history_snapshot or []
            recent = [
                m
                for m in msgs[-20:]
                if isinstance(m, dict) and m.get("role") in ("human", "ai")
            ]
            if not recent:
                return
            lines = []
            for m in recent:
                role = "用户" if m.get("role") == "human" else "AI"
                content = str(m.get("content", "")).strip().replace("\n", " ")
                if content:
                    lines.append(f"{role}: {content[:60]}")
            if lines:
                self.store.save_summary("\n".join(lines[-10:]), turns)
                self._summary_dirty = True
        except Exception as e:
            logger.debug(f"[LTM] summary update failed: {e}")

    # ------------------------------------------------------- management API

    def as_debug_dict(self) -> Dict[str, Any]:
        try:
            return {
                "conf_uid": self.conf_uid,
                "stats": self.store.stats(),
                "config": {
                    k: self.config.get(k) for k in ("enabled", "max_injected_memories", "min_total_score")
                },
                "llm_attached": self.llm is not None,
                "state": (self.store.get_state().to_dict() if self.store.get_state() else None),
                "summary": self.store.get_summary()[:200],
            }
        except Exception as e:
            return {"conf_uid": self.conf_uid, "error": str(e)}

    def close(self) -> None:
        self.store.close()

"""Hermes storage provider: adapter over the ai-companion Hermes Memory REST API.

Phase 3 integration. Hermes (as deployed in this project, see
``/root/桌面/ai-companion`` on the server) is an HTTP service exposing
``/api/agents/{agent_id}/memories`` (CRUD + vector search + stats) backed
by PostgreSQL+pgvector. This adapter implements the aggregate
``StorageProvider`` Protocol (``provider.py``) so the whole LTM stack —
repositories, retriever, manager, panel — runs unchanged when the factory
selects ``hermes``.

Design rules (per the Phase-3 spec):

* Only this file may import the Hermes client / know the Hermes API shape.
  Manager / Store / Repository / Retriever never see Hermes.
* ``conf_uid`` maps to the Hermes ``agent_id`` namespace; the HTTP header
  ``X-User-Id`` carries a fixed local user (configurable).
* ID mapping: local ``memory_id`` (16-hex) <-> Hermes uuid. The mapping is
  kept in the provider (in-memory dict) **and** round-tripped through the
  Hermes record ``metadata.ltm`` block, so a provider restart rebuilds the
  map by listing memories once (lazy, on first id lookup).
* Capability differences are explicit, never silent:

    - ``keywords`` / ``user_state`` / ``summary`` / ``turn_count`` have no
      Hermes counterpart. They are LTM-domain aggregates, not memory rows,
      so the provider persists them in a small sidecar store (a JSON file
      next to the LTM data dir, one per conf_uid). This is a
      provider-internal implementation detail; the Protocol surface is
      identical to SQLite's.
    - ``mark_used`` cannot bump Hermes ``access_count`` directly (Hermes
      only counts search hits). The provider keeps ``use_count`` /
      ``last_used_at`` in the sidecar and stamps them onto records on
      read, preserving LTM scoring semantics.
    - ``find_active_by_content`` / ``count_memories(status)``: emulated
      via list + client-side filtering (dataset is per-character small).

* Status mapping: LTM ``deprecated`` <-> Hermes ``superseded`` (lifecycle
  transition ``active -> superseded`` is legal on the Hermes side).
* Errors: transport failures raise ``HermesUnavailableError``; the module
  import failure (httpx missing) raises at factory time with a clear
  message. No silent fallback to SQLite — the factory only swaps when the
  config says so, and an unhealthy Hermes surfaces as explicit errors in
  ``[LTM]`` logs (the manager layer already degrades gracefully).
"""

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from ..schemas import KeywordRecord, MemoryRecord, UserState

try:  # the HTTP client is the single Hermes-facing dependency
    import httpx
except ImportError:  # pragma: no cover - factory reports this clearly
    httpx = None  # type: ignore[assignment]


class HermesUnavailableError(RuntimeError):
    """Hermes API unreachable / errored. Surfaced explicitly, never swallowed."""


# LTM status <-> Hermes lifecycle status. LTM only has active/deprecated;
# Hermes additionally has expired/archived/deleted. Records in those
# terminal/hidden states are surfaced as LTM "deprecated" (never active),
# so retrieval/dedup correctly ignores them.
_STATUS_TO_HERMES = {"active": "active", "deprecated": "superseded"}
_STATUS_FROM_HERMES = {
    "active": "active",
    "superseded": "deprecated",
    "expired": "deprecated",
    "archived": "deprecated",
    "deleted": "deprecated",
}
# Hermes statuses that make a record invisible to LTM entirely (deleted
# is a soft-delete tombstone — get_memory must report it as gone)
_HIDDEN_HERMES_STATUS = {"deleted"}

# Hermes memory_type vocabulary (ai_companion MemoryType enum). LTM types
# that have no direct Hermes counterpart land on the closest bucket and
# keep the exact LTM type inside the metadata block for round-tripping.
_TYPE_TO_HERMES = {
    "identity": "profile",
    "preference": "preference",
    "habit": "profile",
    "experience": "episodic",
    "goal": "semantic",
    "fact": "semantic",
    "event": "episodic",
    "relationship": "relationship",
}


class _Sidecar:
    """Tiny JSON store for LTM-domain aggregates Hermes has no concept of
    (keywords / user_state / summary / turn_count / use stats).

    One file per conf_uid under the LTM data dir; single-writer lock;
    write-through persistence so a provider restart loses nothing.
    """

    def __init__(self, conf_uid: str):
        data_dir = os.path.join(os.getcwd(), "long_term_memory_data")
        os.makedirs(data_dir, exist_ok=True)
        safe = "".join(c for c in conf_uid if c.isalnum() or c in "-_")
        self._path = os.path.join(data_dir, f"{safe}.hermes_sidecar.json")
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {
            "id_map": {},        # local memory_id -> hermes uuid
            "rev_map": {},       # hermes uuid -> local memory_id
            "use_stats": {},     # local memory_id -> {use_count, last_used_at}
            "keywords": [],      # [{keyword, category, hit_count, first, last}]
            "state": None,       # UserState dict or None
            "summary": "",
            "turn_count": 0,
            "experiences": [],   # Phase 4: ExperienceRecord dicts (LTM-only aggregate)
            "reflections": [],   # Phase 5: ReflectionRecord dicts (LTM-only aggregate)
            "lessons": [],        # Phase 6: LessonRecord dicts (LTM-only aggregate)
            "strategies": [],     # Phase 7: StrategyRecord dicts (LTM-only aggregate)
            "evaluations": [],    # Phase 8: EvaluationRecord dicts (LTM-only aggregate)
            "decisions": [],      # Phase 9: DecisionRecord dicts (LTM-only aggregate)
            "actions": [],        # Phase 10: ActionIntentRecord dicts (LTM-only aggregate)
        }
        self._load()

    def _load(self) -> None:
        try:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                for k in self._data:
                    if k in loaded:
                        self._data[k] = loaded[k]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[LTM][hermes] sidecar load failed: {e}")

    def _flush(self) -> None:
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False)
        os.replace(tmp, self._path)

    # -- id mapping ----------------------------------------------------------

    def map_id(self, local_id: str, hermes_id: str) -> None:
        with self._lock:
            self._data["id_map"][local_id] = hermes_id
            self._data["rev_map"][hermes_id] = local_id
            self._flush()

    def hermes_id_of(self, local_id: str) -> Optional[str]:
        with self._lock:
            return self._data["id_map"].get(local_id)

    def local_id_of(self, hermes_id: str) -> Optional[str]:
        with self._lock:
            return self._data["rev_map"].get(hermes_id)

    def drop_id(self, local_id: str) -> None:
        with self._lock:
            hid = self._data["id_map"].pop(local_id, None)
            if hid:
                self._data["rev_map"].pop(hid, None)
            self._data["use_stats"].pop(local_id, None)
            self._flush()

    # -- use stats (mark_used) ------------------------------------------------

    def bump_used(self, local_ids: Sequence[str]) -> None:
        now = time.time()
        with self._lock:
            for mid in local_ids:
                st = self._data["use_stats"].setdefault(
                    mid, {"use_count": 0, "last_used_at": now})
                st["use_count"] += 1
                st["last_used_at"] = now
            self._flush()

    def use_stats(self, local_id: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data["use_stats"].get(local_id)
                        or {"use_count": 0, "last_used_at": 0.0})

    # -- keywords -------------------------------------------------------------

    def upsert_keyword(self, keyword: str, category: str) -> None:
        now = time.time()
        with self._lock:
            for k in self._data["keywords"]:
                if k["keyword"] == keyword and k["category"] == category:
                    k["hit_count"] += 1
                    k["last"] = now
                    self._flush()
                    return
            self._data["keywords"].append(
                {"keyword": keyword, "category": category, "hit_count": 1,
                 "first": now, "last": now})
            self._flush()

    def list_keywords(self, limit: int) -> List[Dict[str, Any]]:
        with self._lock:
            kws = list(self._data["keywords"])
        kws.sort(key=lambda k: (-k["hit_count"], -k["last"]))
        return kws[:limit]

    def delete_keyword(self, keyword: str, category: str) -> bool:
        with self._lock:
            before = len(self._data["keywords"])
            self._data["keywords"] = [
                k for k in self._data["keywords"]
                if not (k["keyword"] == keyword and k["category"] == category)]
            changed = len(self._data["keywords"]) != before
            if changed:
                self._flush()
        return changed

    def count_keywords(self) -> int:
        with self._lock:
            return len(self._data["keywords"])

    # -- state / summary / turns -----------------------------------------------

    def get_state(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._data["state"]

    def save_state(self, state: Dict[str, Any]) -> None:
        with self._lock:
            self._data["state"] = state
            self._flush()

    def get_summary(self) -> str:
        with self._lock:
            return self._data["summary"]

    def save_summary(self, text: str, turn_count: int) -> None:
        with self._lock:
            self._data["summary"] = text
            self._data["turn_count"] = turn_count
            self._flush()

    def get_turn_count(self) -> int:
        with self._lock:
            return int(self._data["turn_count"])

    def bump_turn_count(self) -> int:
        with self._lock:
            self._data["turn_count"] = int(self._data["turn_count"]) + 1
            self._flush()
            return self._data["turn_count"]

    # -- experiences (Phase 4) ---------------------------------------------------

    def save_experience(self, record_dict: Dict[str, Any]) -> None:
        with self._lock:
            exps = self._data["experiences"]
            for i, e in enumerate(exps):
                if e.get("experience_id") == record_dict["experience_id"]:
                    exps[i] = record_dict   # overwrite (update path)
                    self._flush()
                    return
            exps.append(record_dict)
            self._flush()

    def list_experiences(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            out = list(self._data["experiences"])
        out.sort(key=lambda e: (-(e.get("finalized_at") or 0.0),
                                -(e.get("started_at") or 0.0)))
        return out[:limit]

    def delete_experience(self, experience_id: str) -> bool:
        with self._lock:
            before = len(self._data["experiences"])
            self._data["experiences"] = [
                e for e in self._data["experiences"]
                if e.get("experience_id") != experience_id]
            changed = len(self._data["experiences"]) != before
            if changed:
                self._flush()
        return changed

    def count_experiences(self) -> int:
        with self._lock:
            return len(self._data["experiences"])

    # -- reflections (Phase 5) ------------------------------------------------------

    def save_reflection(self, record_dict: Dict[str, Any]) -> None:
        with self._lock:
            fls = self._data["reflections"]
            for i, f in enumerate(fls):
                if f.get("reflection_id") == record_dict["reflection_id"]:
                    fls[i] = record_dict   # overwrite (update path)
                    self._flush()
                    return
            fls.append(record_dict)
            self._flush()

    def list_reflections(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            out = list(self._data["reflections"])
        out.sort(key=lambda f: -(f.get("created_at") or 0.0))
        return out[:limit]

    def delete_reflection(self, reflection_id: str) -> bool:
        with self._lock:
            before = len(self._data["reflections"])
            self._data["reflections"] = [
                f for f in self._data["reflections"]
                if f.get("reflection_id") != reflection_id]
            changed = len(self._data["reflections"]) != before
            if changed:
                self._flush()
        return changed

    def count_reflections(self) -> int:
        with self._lock:
            return len(self._data["reflections"])

    # -- lessons (Phase 6) ----------------------------------------------------------

    def save_lesson(self, record_dict: Dict[str, Any]) -> None:
        with self._lock:
            lss = self._data["lessons"]
            for i, l in enumerate(lss):
                if l.get("lesson_id") == record_dict["lesson_id"]:
                    lss[i] = record_dict   # overwrite (update path)
                    self._flush()
                    return
            lss.append(record_dict)
            self._flush()

    def list_lessons(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            out = list(self._data["lessons"])
        out.sort(key=lambda l: -(l.get("created_at") or 0.0))
        return out[:limit]

    def delete_lesson(self, lesson_id: str) -> bool:
        with self._lock:
            before = len(self._data["lessons"])
            self._data["lessons"] = [
                l for l in self._data["lessons"]
                if l.get("lesson_id") != lesson_id]
            changed = len(self._data["lessons"]) != before
            if changed:
                self._flush()
        return changed

    def count_lessons(self) -> int:
        with self._lock:
            return len(self._data["lessons"])

    # -- strategies (Phase 7) ---------------------------------------------------------

    def save_strategy(self, record_dict: Dict[str, Any]) -> None:
        with self._lock:
            sts = self._data["strategies"]
            for i, s in enumerate(sts):
                if s.get("strategy_id") == record_dict["strategy_id"]:
                    sts[i] = record_dict   # overwrite (update path)
                    self._flush()
                    return
            sts.append(record_dict)
            self._flush()

    def list_strategies(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            out = list(self._data["strategies"])
        out.sort(key=lambda s: -(s.get("created_at") or 0.0))
        return out[:limit]

    def delete_strategy(self, strategy_id: str) -> bool:
        with self._lock:
            before = len(self._data["strategies"])
            self._data["strategies"] = [
                s for s in self._data["strategies"]
                if s.get("strategy_id") != strategy_id]
            changed = len(self._data["strategies"]) != before
            if changed:
                self._flush()
        return changed

    def count_strategies(self) -> int:
        with self._lock:
            return len(self._data["strategies"])

    # -- evaluations (Phase 8) ---------------------------------------------------------

    def save_evaluation(self, record_dict: Dict[str, Any]) -> None:
        with self._lock:
            evs = self._data["evaluations"]
            for i, ev in enumerate(evs):
                if ev.get("evaluation_id") == record_dict["evaluation_id"]:
                    evs[i] = record_dict   # overwrite (update path)
                    self._flush()
                    return
            evs.append(record_dict)
            self._flush()

    def list_evaluations(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            out = list(self._data["evaluations"])
        out.sort(key=lambda e: -(e.get("created_at") or 0.0))
        return out[:limit]

    def list_evaluations_by_strategy(self, strategy_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            out = [e for e in self._data["evaluations"]
                   if e.get("strategy_id") == strategy_id]
        out.sort(key=lambda e: -(e.get("created_at") or 0.0))
        return out

    def delete_evaluation(self, evaluation_id: str) -> bool:
        with self._lock:
            before = len(self._data["evaluations"])
            self._data["evaluations"] = [
                e for e in self._data["evaluations"]
                if e.get("evaluation_id") != evaluation_id]
            changed = len(self._data["evaluations"]) != before
            if changed:
                self._flush()
        return changed

    def count_evaluations(self) -> int:
        with self._lock:
            return len(self._data["evaluations"])

    # -- decisions (Phase 9) ---------------------------------------------------------

    def save_decision(self, record_dict: Dict[str, Any]) -> None:
        with self._lock:
            dcs = self._data["decisions"]
            for i, dc in enumerate(dcs):
                if dc.get("decision_id") == record_dict["decision_id"]:
                    dcs[i] = record_dict   # overwrite (update path)
                    self._flush()
                    return
            dcs.append(record_dict)
            self._flush()

    def list_decisions(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            out = list(self._data["decisions"])
        out.sort(key=lambda d: -(d.get("created_at") or 0.0))
        return out[:limit]

    def list_decisions_by_strategy(self, strategy_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            out = [d for d in self._data["decisions"]
                   if d.get("selected_strategy_id") == strategy_id]
        out.sort(key=lambda d: -(d.get("created_at") or 0.0))
        return out

    def list_decisions_by_evaluation(self, evaluation_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            out = [d for d in self._data["decisions"]
                   if d.get("selected_evaluation_id") == evaluation_id]
        out.sort(key=lambda d: -(d.get("created_at") or 0.0))
        return out

    def delete_decision(self, decision_id: str) -> bool:
        with self._lock:
            before = len(self._data["decisions"])
            self._data["decisions"] = [
                d for d in self._data["decisions"]
                if d.get("decision_id") != decision_id]
            changed = len(self._data["decisions"]) != before
            if changed:
                self._flush()
        return changed

    def count_decisions(self) -> int:
        with self._lock:
            return len(self._data["decisions"])

    # -- actions (Phase 10) ---------------------------------------------------------

    def save_action(self, record_dict: Dict[str, Any]) -> None:
        with self._lock:
            acts = self._data["actions"]
            for i, a in enumerate(acts):
                if a.get("action_id") == record_dict["action_id"]:
                    acts[i] = record_dict   # overwrite (update path)
                    self._flush()
                    return
            acts.append(record_dict)
            self._flush()

    def list_actions(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            out = list(self._data["actions"])
        out.sort(key=lambda a: -(a.get("created_at") or 0.0))
        return out[:limit]

    def list_actions_by_decision(self, decision_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            out = [a for a in self._data["actions"]
                   if a.get("decision_id") == decision_id]
        out.sort(key=lambda a: -(a.get("created_at") or 0.0))
        return out

    def list_actions_by_evaluation(self, evaluation_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            out = [a for a in self._data["actions"]
                   if a.get("evaluation_id") == evaluation_id]
        out.sort(key=lambda a: -(a.get("created_at") or 0.0))
        return out

    def delete_action(self, action_id: str) -> bool:
        with self._lock:
            before = len(self._data["actions"])
            self._data["actions"] = [
                a for a in self._data["actions"]
                if a.get("action_id") != action_id]
            changed = len(self._data["actions"]) != before
            if changed:
                self._flush()
        return changed

    def count_actions(self) -> int:
        with self._lock:
            return len(self._data["actions"])


class HermesStorageProvider:
    """``StorageProvider`` Protocol implementation backed by the Hermes
    Memory REST API (+ a JSON sidecar for LTM-only aggregates).

    Construct via the factory (``provider_factory.create_storage_provider``)
    with a ``hermes`` config block; never instantiate by hand in business
    code.
    """

    def __init__(self, conf_uid: str, *, base_url: str,
                 user_id: str = "vtuber", timeout: float = 10.0,
                 api_key: Optional[str] = None, verify: bool = True):
        if httpx is None:
            raise HermesUnavailableError(
                "hermes provider requires the httpx package "
                "(uv add httpx / pip install httpx)")
        self.conf_uid = conf_uid
        self.db_path = f"hermes://{base_url}/agents/{conf_uid}"
        self._base_url = base_url.rstrip("/")
        self._agent_path = f"/api/agents/{conf_uid}/memories"
        self._user_id = user_id or "vtuber"
        self._timeout = timeout
        self._headers: Dict[str, str] = {"X-User-Id": self._user_id}
        if api_key:
            # auth token from env/config only; never logged
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            base_url=self._base_url, timeout=self._timeout,
            headers=self._headers)
        self._sidecar = _Sidecar(conf_uid)
        self._id_map_synced = False
        if verify:
            # fail fast when the configured backend is down: a config that
            # says hermes must not silently degrade into "no memory" —
            # the manager layer logs it and chat keeps running, but the
            # failure is loud and attributable (acceptance §12)
            try:
                self._client.get("/api/health").raise_for_status()
            except httpx.HTTPError as e:
                self._client.close()
                raise HermesUnavailableError(
                    f"hermes backend at {self._base_url} is not reachable "
                    f"(health check failed: {e}) — check that the "
                    f"ai-companion service is running and "
                    f"storage.hermes.base_url is correct") from e
        logger.info(
            f"[LTM] HermesStorageProvider ready (agent={conf_uid}, "
            f"user={self._user_id}, endpoint={base_url})")

    # -- transport ------------------------------------------------------------

    def _request(self, method: str, path: str, *,
                 json_body: Any = None, params: Any = None) -> Any:
        try:
            r = self._client.request(method, path, json=json_body,
                                     params=params)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise HermesUnavailableError(
                f"hermes {method} {path} failed: {e}") from e
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    # -- id mapping -----------------------------------------------------------

    def _ensure_id_map(self) -> None:
        """Rebuild local<->hermes id maps from Hermes metadata (once per
        process; the sidecar usually already has it after the first run).

        Deleted (tombstoned) Hermes records are skipped: their local ids
        were dropped from the sidecar at delete time, and re-mapping them
        would resurrect stale tombstone entries.
        """
        if self._id_map_synced:
            return
        page = 0
        while True:
            batch = self._request(
                "GET", self._agent_path,
                params={"limit": 200, "offset": page * 200}) or {}
            items = batch.get("items", [])
            for it in items:
                if it.get("status") in _HIDDEN_HERMES_STATUS:
                    continue
                hid = it.get("id", "")
                ltm = (it.get("metadata") or {}).get("ltm") or {}
                local_id = ltm.get("memory_id")
                if hid and local_id and self._sidecar.local_id_of(hid) is None:
                    self._sidecar.map_id(local_id, hid)
            if len(items) < 200:
                break
            page += 1
        self._id_map_synced = True

    # -- record mapping ---------------------------------------------------------

    @staticmethod
    def _ltm_meta(record: MemoryRecord) -> Dict[str, Any]:
        """The metadata block preserving LTM fields Hermes has no column for."""
        return {
            "memory_id": record.memory_id,
            "ltm_type": record.memory_type,
            "keywords": record.keywords,
            "source_history_uid": record.source_history_uid,
            "use_count": record.use_count,
            "created_at": record.created_at,
            "history": record.history,
        }

    def _to_hermes_payload(self, record: MemoryRecord) -> Dict[str, Any]:
        return {
            "content": record.content,
            "memory_type": _TYPE_TO_HERMES.get(record.memory_type, "semantic"),
            "importance": record.importance,
            "confidence": record.confidence,
            "source": "conversation",
            "metadata": {"ltm": self._ltm_meta(record)},
        }

    def _from_hermes(self, item: Dict[str, Any]) -> MemoryRecord:
        ltm = (item.get("metadata") or {}).get("ltm") or {}
        local_id = ltm.get("memory_id") or item.get("id", "")
        use = self._sidecar.use_stats(local_id)
        created_at = _parse_ts(item.get("created_at")) or float(
            ltm.get("created_at") or 0.0)
        return MemoryRecord(
            memory_id=local_id,
            conf_uid=self.conf_uid,
            memory_type=ltm.get("ltm_type") or item.get("memory_type") or "fact",
            content=item.get("content", ""),
            keywords=list(ltm.get("keywords") or []),
            source_history_uid=str(ltm.get("source_history_uid") or ""),
            importance=float(item.get("importance", 0.5)),
            confidence=float(item.get("confidence", 0.8)),
            status=_STATUS_FROM_HERMES.get(item.get("status", "active"),
                                           "active"),
            use_count=int(use.get("use_count", ltm.get("use_count", 0))),
            created_at=created_at,
            updated_at=_parse_ts(item.get("updated_at")) or 0.0,
            last_used_at=float(use.get("last_used_at", 0.0)),
            history=list(ltm.get("history") or []),
        )

    # -- memories aggregate ------------------------------------------------------

    def save_memory(self, record: MemoryRecord) -> None:
        hid = self._sidecar.hermes_id_of(record.memory_id)
        if hid is None:
            self._ensure_id_map()
            hid = self._sidecar.hermes_id_of(record.memory_id)
        if hid is None:
            created = self._request(
                "POST", self._agent_path,
                json_body=self._to_hermes_payload(record))
            self._sidecar.map_id(record.memory_id, created["id"])
        else:
            # PATCH: content/importance/confidence/metadata; status via
            # lifecycle-legal transitions only
            current = self._request("GET", f"{self._agent_path}/{hid}")
            cur_status = current.get("status", "active")
            new_status = _STATUS_TO_HERMES.get(record.status, "active")
            body: Dict[str, Any] = {
                "content": record.content,
                "importance": record.importance,
                "confidence": record.confidence,
                "metadata": {"ltm": self._ltm_meta(record)},
            }
            if new_status != cur_status:
                body["status"] = new_status
            try:
                self._request("PATCH", f"{self._agent_path}/{hid}",
                              json_body=body)
            except HermesUnavailableError:
                # illegal lifecycle transition (e.g. superseded -> active)
                # — retry as pure field update, keeping Hermes status law
                body.pop("status", None)
                self._request("PATCH", f"{self._agent_path}/{hid}",
                              json_body=body)
                logger.warning(
                    f"[LTM][hermes] status {cur_status}->{new_status} not "
                    f"lifecycle-legal; field update only "
                    f"(memory {record.memory_id})")

    def get_memory(self, memory_id: str) -> Optional[MemoryRecord]:
        self._ensure_id_map()
        hid = self._sidecar.hermes_id_of(memory_id)
        if hid is None:
            return None
        try:
            item = self._request("GET", f"{self._agent_path}/{hid}")
        except HermesUnavailableError as e:
            if "404" in str(e):
                return None
            raise
        if item.get("status") in _HIDDEN_HERMES_STATUS:
            return None
        return self._from_hermes(item)

    def list_memories(
        self, status: str = "active", memory_type: Optional[str] = None
    ) -> List[MemoryRecord]:
        recs = self._list_all()
        out = [r for r in recs if r.status == status]
        if memory_type:
            out = [r for r in out if r.memory_type == memory_type]
        out.sort(key=lambda r: (-r.importance, -r.updated_at))
        return out

    def list_all_memories(self) -> List[MemoryRecord]:
        recs = self._list_all()
        recs.sort(key=lambda r: (r.status, -r.importance, -r.updated_at))
        return recs

    def _list_all(self) -> List[MemoryRecord]:
        self._ensure_id_map()
        out: List[MemoryRecord] = []
        page = 0
        while True:
            batch = self._request(
                "GET", self._agent_path,
                params={"limit": 200, "offset": page * 200}) or {}
            items = batch.get("items", [])
            out.extend(
                self._from_hermes(it) for it in items
                if it.get("status") not in _HIDDEN_HERMES_STATUS)
            if len(items) < 200:
                break
            page += 1
        return out

    def delete_memory(self, memory_id: str) -> bool:
        self._ensure_id_map()
        hid = self._sidecar.hermes_id_of(memory_id)
        if hid is None:
            return False
        try:
            self._request("DELETE", f"{self._agent_path}/{hid}")
        except HermesUnavailableError as e:
            if "404" in str(e):
                return False
            raise
        # Hermes soft-deletes (status=deleted); drop the id mapping so the
        # record reads as fully gone on the LTM side
        self._sidecar.drop_id(memory_id)
        return True

    def find_active_by_content(self, content: str) -> Optional[MemoryRecord]:
        for rec in self.list_memories(status="active"):
            if rec.content == content:
                return rec
        return None

    def count_memories(self, status: str = "active") -> int:
        return len(self.list_memories(status=status))

    def mark_used(self, memory_ids: Sequence[str]) -> None:
        # Hermes counts only search hits (access_count); LTM use stats live
        # in the sidecar so scoring semantics stay identical
        self._sidecar.bump_used(memory_ids)

    # -- keywords aggregate --------------------------------------------------------

    def upsert_keyword(self, keyword: str, category: str) -> None:
        self._sidecar.upsert_keyword(keyword, category)

    def list_keywords(self, limit: int = 200) -> List[KeywordRecord]:
        return [
            KeywordRecord(
                keyword=k["keyword"], category=k["category"],
                conf_uid=self.conf_uid, hit_count=k["hit_count"],
                first_seen_at=k["first"], last_seen_at=k["last"])
            for k in self._sidecar.list_keywords(limit)
        ]

    def delete_keyword(self, keyword: str, category: str) -> bool:
        return self._sidecar.delete_keyword(keyword, category)

    def count_keywords(self) -> int:
        return self._sidecar.count_keywords()

    # -- user state aggregate --------------------------------------------------------

    def get_state(self) -> Optional[UserState]:
        d = self._sidecar.get_state()
        return UserState.from_dict(d) if d else None

    def save_state(self, state: UserState) -> None:
        self._sidecar.save_state(state.to_dict())

    # -- summary aggregate ------------------------------------------------------------

    def get_summary(self) -> str:
        return self._sidecar.get_summary()

    def save_summary(self, text: str, turn_count: int) -> None:
        self._sidecar.save_summary(text, turn_count)

    def get_turn_count(self) -> int:
        return self._sidecar.get_turn_count()

    def bump_turn_count(self) -> int:
        return self._sidecar.bump_turn_count()

    # -- experiences aggregate (Phase 4) ------------------------------------------

    def save_experience(self, record) -> None:
        # Hermes has no interaction-episode concept; experiences are an
        # LTM-domain aggregate persisted in the sidecar (same pattern as
        # keywords/state/summary). Explicit, no silent cross-backend writes.
        self._sidecar.save_experience(record.to_dict())

    def get_experience(self, experience_id: str):
        from ...experience.schemas import ExperienceRecord  # lazy: avoid cycle
        for d in self._sidecar.list_experiences(limit=10000):
            if d.get("experience_id") == experience_id:
                return ExperienceRecord.from_dict(d)
        return None

    def list_experiences(self, limit: int = 200) -> List:
        from ...experience.schemas import ExperienceRecord  # lazy: avoid cycle
        return [ExperienceRecord.from_dict(d)
                for d in self._sidecar.list_experiences(limit=limit)]

    def delete_experience(self, experience_id: str) -> bool:
        return self._sidecar.delete_experience(experience_id)

    def count_experiences(self) -> int:
        return self._sidecar.count_experiences()

    # -- reflections aggregate (Phase 5) ------------------------------------------

    def save_reflection(self, record) -> None:
        # Hermes has no reflection concept; reflections are an LTM-domain
        # aggregate persisted in the sidecar (same pattern as experiences)
        self._sidecar.save_reflection(record.to_dict())

    def get_reflection(self, reflection_id: str):
        from ...reflection.schemas import ReflectionRecord  # lazy: avoid cycle
        for d in self._sidecar.list_reflections(limit=10000):
            if d.get("reflection_id") == reflection_id:
                return ReflectionRecord.from_dict(d)
        return None

    def list_reflections(self, limit: int = 200) -> List:
        from ...reflection.schemas import ReflectionRecord  # lazy: avoid cycle
        return [ReflectionRecord.from_dict(d)
                for d in self._sidecar.list_reflections(limit=limit)]

    def delete_reflection(self, reflection_id: str) -> bool:
        return self._sidecar.delete_reflection(reflection_id)

    def count_reflections(self) -> int:
        return self._sidecar.count_reflections()

    # -- lessons aggregate (Phase 6) -----------------------------------------------

    def save_lesson(self, record) -> None:
        # Hermes has no lesson concept; lessons are an LTM-domain
        # aggregate persisted in the sidecar (same pattern as reflections)
        self._sidecar.save_lesson(record.to_dict())

    def get_lesson(self, lesson_id: str):
        from ...lesson.schemas import LessonRecord  # lazy: avoid cycle
        for d in self._sidecar.list_lessons(limit=10000):
            if d.get("lesson_id") == lesson_id:
                return LessonRecord.from_dict(d)
        return None

    def list_lessons(self, limit: int = 200) -> List:
        from ...lesson.schemas import LessonRecord  # lazy: avoid cycle
        return [LessonRecord.from_dict(d)
                for d in self._sidecar.list_lessons(limit=limit)]

    def list_lessons_by_reflection(self, reflection_id: str) -> List:
        out = self.list_lessons(limit=10000)
        return [r for r in out if reflection_id in r.source_reflection_ids]

    def delete_lesson(self, lesson_id: str) -> bool:
        return self._sidecar.delete_lesson(lesson_id)

    def count_lessons(self) -> int:
        return self._sidecar.count_lessons()

    # -- strategies aggregate (Phase 7) ----------------------------------------------

    def save_strategy(self, record) -> None:
        # Hermes has no strategy concept; strategies are an LTM-domain
        # aggregate persisted in the sidecar (same pattern as lessons)
        self._sidecar.save_strategy(record.to_dict())

    def get_strategy(self, strategy_id: str):
        from ...strategy.schemas import StrategyRecord  # lazy: avoid cycle
        for d in self._sidecar.list_strategies(limit=10000):
            if d.get("strategy_id") == strategy_id:
                return StrategyRecord.from_dict(d)
        return None

    def list_strategies(self, limit: int = 200) -> List:
        from ...strategy.schemas import StrategyRecord  # lazy: avoid cycle
        return [StrategyRecord.from_dict(d)
                for d in self._sidecar.list_strategies(limit=limit)]

    def list_strategies_by_lesson(self, lesson_id: str) -> List:
        out = self.list_strategies(limit=10000)
        return [r for r in out if lesson_id in r.source_lesson_ids]

    def delete_strategy(self, strategy_id: str) -> bool:
        return self._sidecar.delete_strategy(strategy_id)

    def count_strategies(self) -> int:
        return self._sidecar.count_strategies()

    # -- evaluations aggregate (Phase 8) ----------------------------------------------

    def save_evaluation(self, record) -> None:
        # Hermes has no evaluation concept; evaluations are an LTM-domain
        # aggregate persisted in the sidecar (same pattern as strategies)
        self._sidecar.save_evaluation(record.to_dict())

    def get_evaluation(self, evaluation_id: str):
        from ...evaluation.schemas import EvaluationRecord  # lazy: avoid cycle
        for d in self._sidecar.list_evaluations(limit=10000):
            if d.get("evaluation_id") == evaluation_id:
                return EvaluationRecord.from_dict(d)
        return None

    def list_evaluations(self, limit: int = 200) -> List:
        from ...evaluation.schemas import EvaluationRecord  # lazy: avoid cycle
        return [EvaluationRecord.from_dict(d)
                for d in self._sidecar.list_evaluations(limit=limit)]

    def list_evaluations_by_strategy(self, strategy_id: str) -> List:
        from ...evaluation.schemas import EvaluationRecord  # lazy: avoid cycle
        return [EvaluationRecord.from_dict(d)
                for d in self._sidecar.list_evaluations_by_strategy(strategy_id)]

    def delete_evaluation(self, evaluation_id: str) -> bool:
        return self._sidecar.delete_evaluation(evaluation_id)

    def count_evaluations(self) -> int:
        return self._sidecar.count_evaluations()

    # -- decisions aggregate (Phase 9) -----------------------------------------------

    def save_decision(self, record) -> None:
        # Hermes has no decision concept; decisions are an LTM-domain
        # aggregate persisted in the sidecar (same pattern as evaluations)
        self._sidecar.save_decision(record.to_dict())

    def get_decision(self, decision_id: str):
        from ...decision.schemas import DecisionRecord  # lazy: avoid cycle
        for d in self._sidecar.list_decisions(limit=10000):
            if d.get("decision_id") == decision_id:
                return DecisionRecord.from_dict(d)
        return None

    def list_decisions(self, limit: int = 200) -> List:
        from ...decision.schemas import DecisionRecord  # lazy: avoid cycle
        return [DecisionRecord.from_dict(d)
                for d in self._sidecar.list_decisions(limit=limit)]

    def list_decisions_by_strategy(self, strategy_id: str) -> List:
        from ...decision.schemas import DecisionRecord  # lazy: avoid cycle
        return [DecisionRecord.from_dict(d)
                for d in self._sidecar.list_decisions_by_strategy(strategy_id)]

    def list_decisions_by_evaluation(self, evaluation_id: str) -> List:
        from ...decision.schemas import DecisionRecord  # lazy: avoid cycle
        return [DecisionRecord.from_dict(d)
                for d in self._sidecar.list_decisions_by_evaluation(evaluation_id)]

    def delete_decision(self, decision_id: str) -> bool:
        return self._sidecar.delete_decision(decision_id)

    def count_decisions(self) -> int:
        return self._sidecar.count_decisions()

    # -- actions aggregate (Phase 10) ------------------------------------------------

    def save_action(self, record) -> None:
        # Hermes has no action concept; action intents are an LTM-domain
        # aggregate persisted in the sidecar (same pattern as decisions)
        self._sidecar.save_action(record.to_dict())

    def get_action(self, action_id: str):
        from ...action.schemas import ActionIntentRecord  # lazy: avoid cycle
        for d in self._sidecar.list_actions(limit=10000):
            if d.get("action_id") == action_id:
                return ActionIntentRecord.from_dict(d)
        return None

    def list_actions(self, limit: int = 200) -> List:
        from ...action.schemas import ActionIntentRecord  # lazy: avoid cycle
        return [ActionIntentRecord.from_dict(d)
                for d in self._sidecar.list_actions(limit=limit)]

    def list_actions_by_decision(self, decision_id: str) -> List:
        from ...action.schemas import ActionIntentRecord  # lazy: avoid cycle
        return [ActionIntentRecord.from_dict(d)
                for d in self._sidecar.list_actions_by_decision(decision_id)]

    def list_actions_by_evaluation(self, evaluation_id: str) -> List:
        from ...action.schemas import ActionIntentRecord  # lazy: avoid cycle
        return [ActionIntentRecord.from_dict(d)
                for d in self._sidecar.list_actions_by_evaluation(evaluation_id)]

    def delete_action(self, action_id: str) -> bool:
        return self._sidecar.delete_action(action_id)

    def count_actions(self) -> int:
        return self._sidecar.count_actions()

    # -- lifecycle ----------------------------------------------------------------------

    def close(self) -> None:
        try:
            self._client.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[LTM][hermes] close error: {e}")


def _parse_ts(value: Any) -> float:
    """ISO-8601 (Hermes) -> epoch seconds; 0.0 when absent/unparseable.

    ai_companion serializes datetimes without a timezone suffix (its
    ``utcnow()`` helper is actually naive local/UTC per its own iso()
    calls); interpret naive stamps AS UTC — never by the local timezone,
    which would shift recency scoring by the server's UTC offset.
    """
    if not value:
        return 0.0
    try:
        from datetime import datetime, timezone
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:  # noqa: BLE001
        return 0.0

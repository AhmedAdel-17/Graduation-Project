"""Persistence for the Portfolio Assistant workspace (roadmap P0).

Two backends behind one ``WorkspaceStore`` interface:

* ``PostgresWorkspaceStore`` — the production path, using the repo's pooled
  ``tradingagents.db.connection`` against the ``pa_*`` tables (db_schema.sql §9).
* ``JsonFileWorkspaceStore`` — a no-Postgres fallback storing one JSON file per
  conversation under ``results/portfolio_chats/``. Single-user/dev only (no
  concurrency guarantees), mirroring the backtester's checkpoint pattern.

``get_workspace_store()`` picks Postgres when ``POSTGRES_URL`` is reachable, else
the JSON store. The copilot service (P3) is stateless per turn and reloads the
whole ``PortfolioWorkspace`` via ``load_workspace()`` each message.

Persistence convention
----------------------
Typed domain objects (snapshots, policies, scenarios, proposals) are stored as
the **full** Pydantic model in the row's primary JSONB column; a few fields are
denormalized into typed columns for foreign keys / SQL filtering. Reads
reconstruct from the JSONB column, so round-trip fidelity is exactly what the
schema round-trip tests already guarantee. Money stays ``float`` (see schemas.py).

Audit doctrine (CLAUDE.md §11): snapshots/policies/scenarios/proposals are
append-only; scenarios soft-delete via ``status``; conversations soft-delete via
``archived``. Hard deletes happen only through a conversation's ON DELETE CASCADE.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from tradingagents.portfolio.schemas import (
    InvestmentPolicy,
    OptimizationProposal,
    PortfolioSnapshot,
    PortfolioWorkspace,
    Scenario,
    ScenarioStatus,
)

logger = logging.getLogger("tradingagents.portfolio.workspace_store")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _jsonable(obj: Any) -> Any:
    """Recursively convert Pydantic models to JSON-safe primitives.

    Used so ChatBlock/event lists (which may arrive as models OR plain dicts)
    serialize uniformly before hitting JSONB / the JSON file.
    """
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

class WorkspaceStore(ABC):
    """Storage contract shared by the Postgres and JSON-file backends."""

    # --- conversations -----------------------------------------------------
    @abstractmethod
    def create_conversation(
        self, *, user_id: Optional[str] = None, language: str = "auto",
        title: Optional[str] = None,
    ) -> str:
        """Create a conversation+workspace; return its id (UUID string)."""

    @abstractmethod
    def get_conversation(self, conversation_id: str) -> Optional[dict]:
        ...

    @abstractmethod
    def list_conversations(
        self, *, user_id: Optional[str] = None, include_archived: bool = False,
    ) -> list[dict]:
        ...

    @abstractmethod
    def archive_conversation(self, conversation_id: str) -> None:
        """Soft delete."""

    @abstractmethod
    def set_active_ref(self, conversation_id: str, active_ref: str) -> None:
        ...

    @abstractmethod
    def set_last_proposal_id(self, conversation_id: str, proposal_id: int) -> None:
        ...

    # --- messages ----------------------------------------------------------
    @abstractmethod
    def add_message(
        self, conversation_id: str, role: str, *, text: Optional[str] = None,
        blocks: Optional[list] = None, scenario_id: Optional[int] = None,
    ) -> int:
        ...

    @abstractmethod
    def get_messages(self, conversation_id: str) -> list[dict]:
        ...

    # --- baseline snapshots ------------------------------------------------
    @abstractmethod
    def save_baseline_snapshot(
        self, conversation_id: str, snapshot: PortfolioSnapshot,
    ) -> int:
        """Persist a baseline snapshot at the next version; return its id."""

    @abstractmethod
    def get_latest_snapshot(self, conversation_id: str) -> Optional[PortfolioSnapshot]:
        ...

    @abstractmethod
    def get_snapshot(self, snapshot_id: int) -> Optional[PortfolioSnapshot]:
        ...

    # --- policies ----------------------------------------------------------
    @abstractmethod
    def save_policy(
        self, conversation_id: str, policy: InvestmentPolicy, *,
        compiled_params: Optional[dict] = None, compiler_version: Optional[str] = None,
        source_message_id: Optional[int] = None,
    ) -> int:
        ...

    @abstractmethod
    def get_latest_policy(self, conversation_id: str) -> Optional[InvestmentPolicy]:
        ...

    # --- scenarios ---------------------------------------------------------
    @abstractmethod
    def save_scenario(self, conversation_id: str, scenario: Scenario) -> int:
        ...

    @abstractmethod
    def get_scenario(self, scenario_id: int) -> Optional[Scenario]:
        ...

    @abstractmethod
    def get_scenarios(
        self, conversation_id: str, *, statuses: Optional[list[ScenarioStatus]] = None,
    ) -> list[Scenario]:
        ...

    @abstractmethod
    def update_scenario_status(self, scenario_id: int, status: ScenarioStatus) -> None:
        ...

    # --- proposals ---------------------------------------------------------
    @abstractmethod
    def save_proposal(
        self, proposal: OptimizationProposal, *, policy_id: Optional[int] = None,
    ) -> int:
        ...

    @abstractmethod
    def get_proposal(self, proposal_id: int) -> Optional[OptimizationProposal]:
        ...

    # --- events ------------------------------------------------------------
    @abstractmethod
    def log_event(
        self, conversation_id: str, event_type: str, *, stage: Optional[str] = None,
        payload: Optional[dict] = None, message_id: Optional[int] = None,
    ) -> int:
        ...

    @abstractmethod
    def get_events(self, conversation_id: str) -> list[dict]:
        ...

    # --- assembly ----------------------------------------------------------
    def load_workspace(self, conversation_id: str) -> Optional[PortfolioWorkspace]:
        """Assemble the durable workspace the copilot operates over: latest
        baseline snapshot, latest policy (or the disclosed default), the full
        scenario tree, the active_ref pointer, and last_proposal_id.

        Default implementation composes the granular getters; both backends
        share it, so there is one assembly definition.
        """
        conv = self.get_conversation(conversation_id)
        if conv is None:
            return None
        policy = self.get_latest_policy(conversation_id) or InvestmentPolicy.default_policy()
        return PortfolioWorkspace(
            conversation_id=conversation_id,
            user_id=conv.get("user_id"),
            language=conv.get("language", "auto"),
            baseline=self.get_latest_snapshot(conversation_id),
            policy=policy,
            scenarios=self.get_scenarios(conversation_id),
            active_ref=conv.get("active_ref", "baseline"),
            last_proposal_id=conv.get("last_proposal_id"),
            created_at=conv.get("created_at") or _utcnow(),
            updated_at=conv.get("updated_at") or _utcnow(),
        )


# ---------------------------------------------------------------------------
# Postgres backend
# ---------------------------------------------------------------------------

class PostgresWorkspaceStore(WorkspaceStore):
    """``pa_*`` tables via the pooled connection layer. Each public method runs
    in a single ``cursor()`` transaction (commit on success, rollback on error).
    """

    def __init__(self) -> None:
        # Imported lazily so the module is importable without psycopg2.
        from tradingagents.db import connection as _conn
        import psycopg2.extras as _extras

        self._conn = _conn
        self._Json = _extras.Json
        self._dict = dict(dict_cursor=True)

    def _j(self, obj: Any):
        """Wrap a Python structure as a psycopg2 JSONB adapter."""
        return self._Json(_jsonable(obj))

    # --- conversations -----------------------------------------------------
    def create_conversation(self, *, user_id=None, language="auto", title=None) -> str:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pa_conversations (user_id, language, title) "
                "VALUES (%s, %s, %s) RETURNING id;",
                (user_id, language, title),
            )
            return str(cur.fetchone()[0])

    def get_conversation(self, conversation_id: str) -> Optional[dict]:
        with self._conn.cursor(**self._dict) as cur:
            cur.execute(
                "SELECT id, user_id, language, title, active_ref, last_proposal_id, "
                "archived, created_at, updated_at FROM pa_conversations WHERE id = %s;",
                (conversation_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            d = dict(row)
            d["id"] = str(d["id"])
            return d

    def list_conversations(self, *, user_id=None, include_archived=False) -> list[dict]:
        clauses, params = [], []
        if user_id is not None:
            clauses.append("user_id = %s")
            params.append(user_id)
        if not include_archived:
            clauses.append("archived = FALSE")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._conn.cursor(**self._dict) as cur:
            cur.execute(
                "SELECT id, user_id, language, title, active_ref, last_proposal_id, "
                "archived, created_at, updated_at FROM pa_conversations" + where
                + " ORDER BY updated_at DESC;",
                tuple(params),
            )
            out = []
            for row in cur.fetchall():
                d = dict(row)
                d["id"] = str(d["id"])
                out.append(d)
            return out

    def archive_conversation(self, conversation_id: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE pa_conversations SET archived = TRUE, updated_at = now() WHERE id = %s;",
                (conversation_id,),
            )

    def set_active_ref(self, conversation_id: str, active_ref: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE pa_conversations SET active_ref = %s, updated_at = now() WHERE id = %s;",
                (active_ref, conversation_id),
            )

    def set_last_proposal_id(self, conversation_id: str, proposal_id: int) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE pa_conversations SET last_proposal_id = %s, updated_at = now() WHERE id = %s;",
                (proposal_id, conversation_id),
            )

    # --- messages ----------------------------------------------------------
    def add_message(self, conversation_id, role, *, text=None, blocks=None, scenario_id=None) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pa_messages (conversation_id, role, text_content, blocks, scenario_id) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id;",
                (conversation_id, role, text,
                 self._j(blocks) if blocks is not None else None, scenario_id),
            )
            return int(cur.fetchone()[0])

    def get_messages(self, conversation_id: str) -> list[dict]:
        with self._conn.cursor(**self._dict) as cur:
            cur.execute(
                "SELECT id, role, text_content, blocks, scenario_id, created_at "
                "FROM pa_messages WHERE conversation_id = %s ORDER BY id;",
                (conversation_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    # --- snapshots ---------------------------------------------------------
    def save_baseline_snapshot(self, conversation_id, snapshot: PortfolioSnapshot) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM pa_portfolio_snapshots "
                "WHERE conversation_id = %s;",
                (conversation_id,),
            )
            version = int(cur.fetchone()[0])
            cur.execute(
                "INSERT INTO pa_portfolio_snapshots "
                "(conversation_id, version, cash_egp, positions, promoted_from_scenario, "
                " confirmed_by_user) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id;",
                (conversation_id, version, snapshot.cash_egp,
                 self._j(snapshot.holdings), snapshot.promoted_from_scenario,
                 snapshot.confirmed_by_user),
            )
            return int(cur.fetchone()[0])

    def _row_to_snapshot(self, row) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            snapshot_id=int(row["id"]),
            conversation_id=str(row["conversation_id"]),
            version=int(row["version"]),
            cash_egp=float(row["cash_egp"]) if row["cash_egp"] is not None else 0.0,
            holdings=row["positions"] or [],
            confirmed_by_user=bool(row["confirmed_by_user"]),
            promoted_from_scenario=row["promoted_from_scenario"],
            created_at=row["created_at"],
        )

    def get_latest_snapshot(self, conversation_id: str) -> Optional[PortfolioSnapshot]:
        with self._conn.cursor(**self._dict) as cur:
            cur.execute(
                "SELECT * FROM pa_portfolio_snapshots WHERE conversation_id = %s "
                "ORDER BY version DESC LIMIT 1;",
                (conversation_id,),
            )
            row = cur.fetchone()
            return self._row_to_snapshot(row) if row else None

    def get_snapshot(self, snapshot_id: int) -> Optional[PortfolioSnapshot]:
        with self._conn.cursor(**self._dict) as cur:
            cur.execute("SELECT * FROM pa_portfolio_snapshots WHERE id = %s;", (snapshot_id,))
            row = cur.fetchone()
            return self._row_to_snapshot(row) if row else None

    # --- policies ----------------------------------------------------------
    def save_policy(self, conversation_id, policy, *, compiled_params=None,
                    compiler_version=None, source_message_id=None) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM pa_policies WHERE conversation_id = %s;",
                (conversation_id,),
            )
            version = int(cur.fetchone()[0])
            cur.execute(
                "INSERT INTO pa_policies (conversation_id, version, policy, compiled_params, "
                " compiler_version, source_message_id, confirmed_by_user) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id;",
                (conversation_id, version, self._j(policy),
                 self._j(compiled_params) if compiled_params is not None else None,
                 compiler_version, source_message_id, policy.confirmed_by_user),
            )
            return int(cur.fetchone()[0])

    def get_latest_policy(self, conversation_id: str) -> Optional[InvestmentPolicy]:
        with self._conn.cursor(**self._dict) as cur:
            cur.execute(
                "SELECT policy FROM pa_policies WHERE conversation_id = %s "
                "ORDER BY version DESC LIMIT 1;",
                (conversation_id,),
            )
            row = cur.fetchone()
            return InvestmentPolicy.model_validate(row["policy"]) if row else None

    # --- scenarios ---------------------------------------------------------
    def save_scenario(self, conversation_id: str, scenario: Scenario) -> int:
        # The full derived PortfolioSnapshot (positions + cash + flags) is stored
        # in the `derived_positions` JSONB column — it IS the pinned derived
        # portfolio; the column name is positional shorthand.
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pa_scenarios (conversation_id, parent_scenario_id, base_snapshot_id, "
                " patch, derived_positions, derived_policy, input_set, proposal_id, status, label) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id;",
                (conversation_id, scenario.parent_scenario_id, scenario.base_snapshot_id,
                 self._j(scenario.patch), self._j(scenario.derived_snapshot),
                 self._j(scenario.derived_policy) if scenario.derived_policy else None,
                 self._j(scenario.input_set) if scenario.input_set else None,
                 scenario.proposal_id, scenario.status.value, scenario.label),
            )
            return int(cur.fetchone()[0])

    def _row_to_scenario(self, row) -> Scenario:
        return Scenario(
            scenario_id=int(row["id"]),
            conversation_id=str(row["conversation_id"]),
            parent_scenario_id=row["parent_scenario_id"],
            base_snapshot_id=row["base_snapshot_id"],
            patch=row["patch"],
            derived_snapshot=row["derived_positions"],
            derived_policy=row["derived_policy"],
            input_set=row["input_set"],
            proposal_id=row["proposal_id"],
            status=ScenarioStatus(row["status"]),
            label=row["label"] or "",
            created_at=row["created_at"],
        )

    def get_scenario(self, scenario_id: int) -> Optional[Scenario]:
        with self._conn.cursor(**self._dict) as cur:
            cur.execute("SELECT * FROM pa_scenarios WHERE id = %s;", (scenario_id,))
            row = cur.fetchone()
            return self._row_to_scenario(row) if row else None

    def get_scenarios(self, conversation_id, *, statuses=None) -> list[Scenario]:
        q = "SELECT * FROM pa_scenarios WHERE conversation_id = %s"
        params: list[Any] = [conversation_id]
        if statuses:
            q += " AND status = ANY(%s)"
            params.append([s.value for s in statuses])
        q += " ORDER BY id;"
        with self._conn.cursor(**self._dict) as cur:
            cur.execute(q, tuple(params))
            return [self._row_to_scenario(r) for r in cur.fetchall()]

    def update_scenario_status(self, scenario_id: int, status: ScenarioStatus) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE pa_scenarios SET status = %s WHERE id = %s;",
                (status.value, scenario_id),
            )

    # --- proposals ---------------------------------------------------------
    def save_proposal(self, proposal: OptimizationProposal, *, policy_id=None) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pa_optimization_proposals "
                "(conversation_id, snapshot_id, scenario_id, policy_id, inputs, proposal, "
                " solver_status, engine_version) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id;",
                (proposal.conversation_id, proposal.snapshot_id, proposal.scenario_id,
                 policy_id, self._j(proposal.inputs_audit), self._j(proposal),
                 proposal.solver_status.value, proposal.engine_version),
            )
            return int(cur.fetchone()[0])

    def get_proposal(self, proposal_id: int) -> Optional[OptimizationProposal]:
        with self._conn.cursor(**self._dict) as cur:
            cur.execute(
                "SELECT id, proposal FROM pa_optimization_proposals WHERE id = %s;",
                (proposal_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            prop = OptimizationProposal.model_validate(row["proposal"])
            if prop.proposal_id is None:
                prop.proposal_id = int(row["id"])
            return prop

    # --- events ------------------------------------------------------------
    def log_event(self, conversation_id, event_type, *, stage=None, payload=None, message_id=None) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pa_events (conversation_id, message_id, event_type, stage, payload) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id;",
                (conversation_id, message_id, event_type, stage,
                 self._j(payload) if payload is not None else None),
            )
            return int(cur.fetchone()[0])

    def get_events(self, conversation_id: str) -> list[dict]:
        with self._conn.cursor(**self._dict) as cur:
            cur.execute(
                "SELECT id, message_id, event_type, stage, payload, logged_at "
                "FROM pa_events WHERE conversation_id = %s ORDER BY id;",
                (conversation_id,),
            )
            return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# JSON-file backend (no-Postgres fallback)
# ---------------------------------------------------------------------------

class JsonFileWorkspaceStore(WorkspaceStore):
    """One JSON file per conversation under ``base_dir``. Emulates BIGSERIAL ids
    with per-file counters. Single-user/dev only — guarded by a process lock,
    not safe across processes.
    """

    def __init__(self, base_dir: str | os.PathLike = "results/portfolio_chats") -> None:
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, conversation_id: str) -> Path:
        return self.base / f"{conversation_id}.json"

    def _load(self, conversation_id: str) -> Optional[dict]:
        p = self._path(conversation_id)
        if not p.exists():
            return None
        with p.open(encoding="utf-8") as fh:
            return json.load(fh)

    def _save(self, data: dict) -> None:
        p = self._path(data["conversation"]["id"])
        tmp = p.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        tmp.replace(p)  # atomic on the same filesystem

    @staticmethod
    def _next_id(data: dict, table: str) -> int:
        data.setdefault("_seq", {})
        nid = int(data["_seq"].get(table, 0)) + 1
        data["_seq"][table] = nid
        return nid

    # --- conversations -----------------------------------------------------
    def create_conversation(self, *, user_id=None, language="auto", title=None) -> str:
        cid = str(uuid.uuid4())
        now = _utcnow().isoformat()
        data = {
            "conversation": {
                "id": cid, "user_id": user_id, "language": language, "title": title,
                "active_ref": "baseline", "last_proposal_id": None, "archived": False,
                "created_at": now, "updated_at": now,
            },
            "messages": [], "snapshots": [], "policies": [], "scenarios": [],
            "proposals": [], "events": [], "_seq": {},
        }
        with self._lock:
            self._save(data)
        return cid

    def get_conversation(self, conversation_id: str) -> Optional[dict]:
        data = self._load(conversation_id)
        return dict(data["conversation"]) if data else None

    def list_conversations(self, *, user_id=None, include_archived=False) -> list[dict]:
        out = []
        for p in self.base.glob("*.json"):
            try:
                with p.open(encoding="utf-8") as fh:
                    conv = json.load(fh)["conversation"]
            except Exception:  # pragma: no cover — skip corrupt files
                continue
            if user_id is not None and conv.get("user_id") != user_id:
                continue
            if not include_archived and conv.get("archived"):
                continue
            out.append(conv)
        out.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
        return out

    def _mutate_conv(self, conversation_id: str, **fields) -> None:
        with self._lock:
            data = self._load(conversation_id)
            if data is None:
                raise KeyError(conversation_id)
            data["conversation"].update(fields)
            data["conversation"]["updated_at"] = _utcnow().isoformat()
            self._save(data)

    def archive_conversation(self, conversation_id: str) -> None:
        self._mutate_conv(conversation_id, archived=True)

    def set_active_ref(self, conversation_id: str, active_ref: str) -> None:
        self._mutate_conv(conversation_id, active_ref=active_ref)

    def set_last_proposal_id(self, conversation_id: str, proposal_id: int) -> None:
        self._mutate_conv(conversation_id, last_proposal_id=proposal_id)

    # --- generic append helper ---------------------------------------------
    def _append(self, conversation_id: str, table: str, record: dict) -> int:
        with self._lock:
            data = self._load(conversation_id)
            if data is None:
                raise KeyError(conversation_id)
            rid = self._next_id(data, table)
            record["id"] = rid
            data[table].append(record)
            self._save(data)
            return rid

    # --- messages ----------------------------------------------------------
    def add_message(self, conversation_id, role, *, text=None, blocks=None, scenario_id=None) -> int:
        return self._append(conversation_id, "messages", {
            "role": role, "text_content": text,
            "blocks": _jsonable(blocks) if blocks is not None else None,
            "scenario_id": scenario_id, "created_at": _utcnow().isoformat(),
        })

    def get_messages(self, conversation_id: str) -> list[dict]:
        data = self._load(conversation_id)
        return list(data["messages"]) if data else []

    # --- snapshots ---------------------------------------------------------
    def save_baseline_snapshot(self, conversation_id, snapshot: PortfolioSnapshot) -> int:
        with self._lock:
            data = self._load(conversation_id)
            if data is None:
                raise KeyError(conversation_id)
            version = max((s["version"] for s in data["snapshots"]), default=0) + 1
            rid = self._next_id(data, "snapshots")
            data["snapshots"].append({
                "id": rid, "conversation_id": conversation_id, "version": version,
                "cash_egp": snapshot.cash_egp, "positions": _jsonable(snapshot.holdings),
                "promoted_from_scenario": snapshot.promoted_from_scenario,
                "confirmed_by_user": snapshot.confirmed_by_user,
                "created_at": _utcnow().isoformat(),
            })
            self._save(data)
            return rid

    @staticmethod
    def _dict_to_snapshot(row: dict) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            snapshot_id=row["id"], conversation_id=row["conversation_id"],
            version=row["version"], cash_egp=row.get("cash_egp") or 0.0,
            holdings=row.get("positions") or [],
            confirmed_by_user=row.get("confirmed_by_user", False),
            promoted_from_scenario=row.get("promoted_from_scenario"),
            created_at=row["created_at"],
        )

    def get_latest_snapshot(self, conversation_id: str) -> Optional[PortfolioSnapshot]:
        data = self._load(conversation_id)
        if not data or not data["snapshots"]:
            return None
        row = max(data["snapshots"], key=lambda s: s["version"])
        return self._dict_to_snapshot(row)

    def get_snapshot(self, snapshot_id: int) -> Optional[PortfolioSnapshot]:
        for p in self.base.glob("*.json"):
            with p.open(encoding="utf-8") as fh:
                data = json.load(fh)
            for row in data["snapshots"]:
                if row["id"] == snapshot_id:
                    return self._dict_to_snapshot(row)
        return None

    # --- policies ----------------------------------------------------------
    def save_policy(self, conversation_id, policy, *, compiled_params=None,
                    compiler_version=None, source_message_id=None) -> int:
        with self._lock:
            data = self._load(conversation_id)
            if data is None:
                raise KeyError(conversation_id)
            version = max((p["version"] for p in data["policies"]), default=0) + 1
            rid = self._next_id(data, "policies")
            data["policies"].append({
                "id": rid, "conversation_id": conversation_id, "version": version,
                "policy": _jsonable(policy),
                "compiled_params": _jsonable(compiled_params) if compiled_params else None,
                "compiler_version": compiler_version, "source_message_id": source_message_id,
                "confirmed_by_user": policy.confirmed_by_user,
                "created_at": _utcnow().isoformat(),
            })
            self._save(data)
            return rid

    def get_latest_policy(self, conversation_id: str) -> Optional[InvestmentPolicy]:
        data = self._load(conversation_id)
        if not data or not data["policies"]:
            return None
        row = max(data["policies"], key=lambda p: p["version"])
        return InvestmentPolicy.model_validate(row["policy"])

    # --- scenarios ---------------------------------------------------------
    def save_scenario(self, conversation_id: str, scenario: Scenario) -> int:
        return self._append(conversation_id, "scenarios", {
            "conversation_id": conversation_id,
            "parent_scenario_id": scenario.parent_scenario_id,
            "base_snapshot_id": scenario.base_snapshot_id,
            "patch": _jsonable(scenario.patch),
            "derived_positions": _jsonable(scenario.derived_snapshot),
            "derived_policy": _jsonable(scenario.derived_policy) if scenario.derived_policy else None,
            "input_set": _jsonable(scenario.input_set) if scenario.input_set else None,
            "proposal_id": scenario.proposal_id, "status": scenario.status.value,
            "label": scenario.label, "created_at": _utcnow().isoformat(),
        })

    @staticmethod
    def _dict_to_scenario(row: dict) -> Scenario:
        return Scenario(
            scenario_id=row["id"], conversation_id=row["conversation_id"],
            parent_scenario_id=row.get("parent_scenario_id"),
            base_snapshot_id=row.get("base_snapshot_id"),
            patch=row["patch"], derived_snapshot=row["derived_positions"],
            derived_policy=row.get("derived_policy"), input_set=row.get("input_set"),
            proposal_id=row.get("proposal_id"),
            status=ScenarioStatus(row["status"]), label=row.get("label") or "",
            created_at=row["created_at"],
        )

    def get_scenario(self, scenario_id: int) -> Optional[Scenario]:
        for p in self.base.glob("*.json"):
            with p.open(encoding="utf-8") as fh:
                data = json.load(fh)
            for row in data["scenarios"]:
                if row["id"] == scenario_id:
                    return self._dict_to_scenario(row)
        return None

    def get_scenarios(self, conversation_id, *, statuses=None) -> list[Scenario]:
        data = self._load(conversation_id)
        if not data:
            return []
        allowed = {s.value for s in statuses} if statuses else None
        rows = [r for r in data["scenarios"] if allowed is None or r["status"] in allowed]
        rows.sort(key=lambda r: r["id"])
        return [self._dict_to_scenario(r) for r in rows]

    def update_scenario_status(self, scenario_id: int, status: ScenarioStatus) -> None:
        with self._lock:
            for p in self.base.glob("*.json"):
                with p.open(encoding="utf-8") as fh:
                    data = json.load(fh)
                changed = False
                for row in data["scenarios"]:
                    if row["id"] == scenario_id:
                        row["status"] = status.value
                        changed = True
                if changed:
                    self._save(data)
                    return

    # --- proposals ---------------------------------------------------------
    def save_proposal(self, proposal: OptimizationProposal, *, policy_id=None) -> int:
        cid = proposal.conversation_id
        if cid is None:
            raise ValueError("proposal.conversation_id is required for the JSON store")
        return self._append(cid, "proposals", {
            "conversation_id": cid, "snapshot_id": proposal.snapshot_id,
            "scenario_id": proposal.scenario_id, "policy_id": policy_id,
            "inputs": _jsonable(proposal.inputs_audit), "proposal": _jsonable(proposal),
            "solver_status": proposal.solver_status.value,
            "engine_version": proposal.engine_version, "created_at": _utcnow().isoformat(),
        })

    def get_proposal(self, proposal_id: int) -> Optional[OptimizationProposal]:
        for p in self.base.glob("*.json"):
            with p.open(encoding="utf-8") as fh:
                data = json.load(fh)
            for row in data["proposals"]:
                if row["id"] == proposal_id:
                    prop = OptimizationProposal.model_validate(row["proposal"])
                    if prop.proposal_id is None:
                        prop.proposal_id = row["id"]
                    return prop
        return None

    # --- events ------------------------------------------------------------
    def log_event(self, conversation_id, event_type, *, stage=None, payload=None, message_id=None) -> int:
        return self._append(conversation_id, "events", {
            "conversation_id": conversation_id, "message_id": message_id,
            "event_type": event_type, "stage": stage,
            "payload": _jsonable(payload) if payload is not None else None,
            "logged_at": _utcnow().isoformat(),
        })

    def get_events(self, conversation_id: str) -> list[dict]:
        data = self._load(conversation_id)
        return list(data["events"]) if data else []


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_workspace_store(*, prefer_postgres: bool = True) -> WorkspaceStore:
    """Return a Postgres-backed store when ``POSTGRES_URL`` is reachable, else
    the JSON-file fallback. Resolution: POSTGRES_URL (Supabase) → JSON file.
    """
    if prefer_postgres:
        try:
            from tradingagents.db.connection import is_postgres_available
            if is_postgres_available():
                logger.info("workspace_store: using PostgresWorkspaceStore")
                return PostgresWorkspaceStore()
        except Exception as exc:  # pragma: no cover — driver/init edge
            logger.warning("workspace_store: Postgres unavailable (%s); JSON fallback", exc)
    logger.info("workspace_store: using JsonFileWorkspaceStore")
    return JsonFileWorkspaceStore()


__all__ = [
    "WorkspaceStore", "PostgresWorkspaceStore", "JsonFileWorkspaceStore",
    "get_workspace_store",
]

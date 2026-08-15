"""Small SQLite repository; private paths never leave this module's records."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    original_name TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    page_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    private_dir TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents(sha256);

                CREATE TABLE IF NOT EXISTS analyses (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id),
                    feature TEXT NOT NULL DEFAULT 'review',
                    mode TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT,
                    technical_status TEXT NOT NULL,
                    business_status TEXT,
                    human_status TEXT NOT NULL,
                    external_processing_consent INTEGER NOT NULL DEFAULT 0,
                    draft_json TEXT,
                    rules_json TEXT,
                    provider_metadata_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finalized_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_analyses_created ON analyses(created_at DESC);

                CREATE TABLE IF NOT EXISTS decisions (
                    analysis_id TEXT NOT NULL REFERENCES analyses(id),
                    finding_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    note TEXT NOT NULL,
                    corrected_value TEXT,
                    reviewer TEXT NOT NULL,
                    decided_at TEXT NOT NULL,
                    PRIMARY KEY (analysis_id, finding_id)
                );

                CREATE TABLE IF NOT EXISTS exports (
                    id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL REFERENCES analyses(id),
                    format TEXT NOT NULL,
                    private_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS business_artifacts (
                    analysis_id TEXT PRIMARY KEY REFERENCES analyses(id),
                    facts_json TEXT NOT NULL,
                    process_plan_json TEXT,
                    prequote_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            analysis_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(analyses)").fetchall()
            }
            if "feature" not in analysis_columns:
                connection.execute(
                    "ALTER TABLE analyses ADD COLUMN feature TEXT NOT NULL DEFAULT 'review'"
                )
            connection.execute(
                """
                UPDATE analyses
                SET technical_status='failed',
                    error='Application restarted while the analysis was running. Retry explicitly.',
                    updated_at=?
                WHERE technical_status IN (
                    'queued', 'running', 'validating', 'rendering', 'calling_ai',
                    'analyzing', 'validating_output', 'applying_rules', 'processing'
                )
                """,
                (utc_now(),),
            )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def create_document(self, record: dict[str, Any]) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO documents
                    (id, original_name, sha256, size_bytes, page_count, status, error,
                     private_dir, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"], record["original_name"], record["sha256"],
                    record["size_bytes"], record["page_count"], record["status"],
                    record.get("error"), record["private_dir"], now, now,
                ),
            )
        self.audit("document", record["id"], "created", {"sha256": record["sha256"]})

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            return self._row(connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone())

    def create_analysis(self, record: dict[str, Any]) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO analyses
                    (id, document_id, feature, mode, provider, model, technical_status,
                     business_status, human_status, external_processing_consent,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"], record["document_id"], record.get("feature", "review"),
                    record["mode"], record["provider"],
                    record.get("model"), record.get("technical_status", "queued"), None,
                    "pending", int(bool(record.get("external_processing_consent"))), now, now,
                ),
            )
        self.audit(
            "analysis",
            record["id"],
            "queued",
            {"mode": record["mode"], "feature": record.get("feature", "review")},
        )

    def update_analysis(self, analysis_id: str, **updates: Any) -> None:
        allowed = {
            "technical_status", "business_status", "human_status", "draft_json",
            "rules_json", "provider_metadata_json", "error", "model", "finalized_at",
        }
        unknown = set(updates) - allowed
        if unknown:
            raise ValueError(f"unsupported analysis columns: {sorted(unknown)}")
        updates["updated_at"] = utc_now()
        assignments = ", ".join(f"{key}=?" for key in updates)
        values = [updates[key] for key in updates]
        with self.connect() as connection:
            connection.execute(
                f"UPDATE analyses SET {assignments} WHERE id=?",  # columns are allowlisted
                (*values, analysis_id),
            )

    def get_analysis(self, analysis_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            return self._row(connection.execute("SELECT * FROM analyses WHERE id=?", (analysis_id,)).fetchone())

    def list_analyses(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM analyses ORDER BY created_at DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_decision(self, analysis_id: str, finding_id: str, payload: dict[str, Any]) -> None:
        decided_at = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO decisions
                    (analysis_id, finding_id, decision, note, corrected_value, reviewer, decided_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(analysis_id, finding_id) DO UPDATE SET
                    decision=excluded.decision,
                    note=excluded.note,
                    corrected_value=excluded.corrected_value,
                    reviewer=excluded.reviewer,
                    decided_at=excluded.decided_at
                """,
                (
                    analysis_id, finding_id, payload["decision"], payload.get("note", ""),
                    payload.get("corrected_value"), payload["reviewer"], decided_at,
                ),
            )
        if finding_id.startswith("field:"):
            self.audit(
                "analysis",
                analysis_id,
                "field_corrected",
                {"field_name": finding_id.removeprefix("field:"), "reviewer": payload["reviewer"]},
            )
        else:
            self.audit(
                "analysis",
                analysis_id,
                "human_decision",
                {"finding_id": finding_id, "decision": payload["decision"]},
            )

    def list_decisions(self, analysis_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM decisions WHERE analysis_id=? ORDER BY decided_at", (analysis_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def create_export(self, record: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO exports (id, analysis_id, format, private_path, sha256, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record["id"], record["analysis_id"], record["format"],
                    record["private_path"], record["sha256"], utc_now(),
                ),
            )
        self.audit("analysis", record["analysis_id"], "exported", {"format": record["format"], "sha256": record["sha256"]})

    def get_export(self, export_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            return self._row(connection.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone())

    def get_business_artifacts(self, analysis_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            return self._row(
                connection.execute(
                    "SELECT * FROM business_artifacts WHERE analysis_id=?",
                    (analysis_id,),
                ).fetchone()
            )

    def save_process_plan(self, analysis_id: str, *, facts_json: str, process_plan_json: str) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO business_artifacts
                    (analysis_id, facts_json, process_plan_json, prequote_json, created_at, updated_at)
                VALUES (?, ?, ?, NULL, ?, ?)
                ON CONFLICT(analysis_id) DO UPDATE SET
                    facts_json=excluded.facts_json,
                    process_plan_json=excluded.process_plan_json,
                    prequote_json=NULL,
                    updated_at=excluded.updated_at
                """,
                (analysis_id, facts_json, process_plan_json, now, now),
            )
        contract_version = (json.loads(process_plan_json) or {}).get("schema_version", "1.0")
        self.audit("analysis", analysis_id, "process_plan_drafted", {"contract_version": contract_version})

    def save_prequote(self, analysis_id: str, *, facts_json: str, process_plan_json: str, prequote_json: str) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO business_artifacts
                    (analysis_id, facts_json, process_plan_json, prequote_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(analysis_id) DO UPDATE SET
                    facts_json=excluded.facts_json,
                    process_plan_json=excluded.process_plan_json,
                    prequote_json=excluded.prequote_json,
                    updated_at=excluded.updated_at
                """,
                (analysis_id, facts_json, process_plan_json, prequote_json, now, now),
            )
        self.audit("analysis", analysis_id, "prequote_calculated", {"contract_version": "1.0"})

    def save_process_plan_review(
        self,
        analysis_id: str,
        *,
        process_plan_json: str,
        reviewer: str,
        reviewer_role: str,
    ) -> None:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE business_artifacts
                SET process_plan_json=?, prequote_json=NULL, updated_at=?
                WHERE analysis_id=?
                """,
                (process_plan_json, now, analysis_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("process plan does not exist")
        contract_version = (json.loads(process_plan_json) or {}).get("schema_version", "1.0")
        self.audit(
            "analysis",
            analysis_id,
            "process_plan_reviewed",
            {"contract_version": contract_version, "reviewer": reviewer, "reviewer_role": reviewer_role},
        )

    def get_latest_audit(self, entity_type: str, entity_id: str, action: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = self._row(
                connection.execute(
                    """
                    SELECT details_json, created_at
                    FROM audit_events
                    WHERE entity_type=? AND entity_id=? AND action=?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (entity_type, entity_id, action),
                ).fetchone()
            )
        if not row:
            return None
        return {
            "details": json.loads(row["details_json"]),
            "created_at": row["created_at"],
        }

    def audit(self, entity_type: str, entity_id: str, action: str, details: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO audit_events (entity_type, entity_id, action, details_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (entity_type, entity_id, action, json.dumps(details, ensure_ascii=False), utc_now()),
            )

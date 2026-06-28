from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .models import ClarifyingQuestion, DemandField, DemandReport, DemandTask, FieldSource, TaskStatus


class DemandStorage:
    def __init__(self, base_dir: str | Path = "outputs") -> None:
        self.base_dir = Path(base_dir)
        self.report_dir = self.base_dir / "demand_reports"
        self.handoff_dir = self.base_dir / "handoff_docs"
        self.db_path = self.base_dir / "demand_agent.sqlite3"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.handoff_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def save_task(self, task: DemandTask) -> None:
        payload = task_to_payload(task)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO demand_tasks (
                    demand_task_id, project_id, status, user_input, context_json,
                    payload_json, report_id, completeness_score, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(demand_task_id) DO UPDATE SET
                    project_id=excluded.project_id,
                    status=excluded.status,
                    user_input=excluded.user_input,
                    context_json=excluded.context_json,
                    payload_json=excluded.payload_json,
                    report_id=excluded.report_id,
                    completeness_score=excluded.completeness_score,
                    updated_at=excluded.updated_at
                """,
                (
                    task.demand_task_id,
                    task.project_id,
                    task.status.value,
                    task.user_input,
                    json.dumps(task.context, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                    task.report.report_id if task.report else None,
                    task.report.completeness_score if task.report else None,
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                ),
            )

    def save_report_files(self, report: DemandReport) -> dict[str, str]:
        stem = report.report_id
        json_path = self.report_dir / f"{stem}.json"
        markdown_path = self.report_dir / f"{stem}.md"
        json_path.write_text(json.dumps(report.structured_json, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(report.markdown, encoding="utf-8")
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO demand_reports (
                    report_id, demand_task_id, version, completeness_score,
                    markdown_path, json_path, structured_json, markdown, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_id) DO UPDATE SET
                    demand_task_id=excluded.demand_task_id,
                    version=excluded.version,
                    completeness_score=excluded.completeness_score,
                    markdown_path=excluded.markdown_path,
                    json_path=excluded.json_path,
                    structured_json=excluded.structured_json,
                    markdown=excluded.markdown,
                    updated_at=excluded.updated_at
                """,
                (
                    report.report_id,
                    report.demand_task_id,
                    report.version,
                    report.completeness_score,
                    str(markdown_path),
                    str(json_path),
                    json.dumps(report.structured_json, ensure_ascii=False),
                    report.markdown,
                    datetime.now().isoformat(),
                ),
            )
        return {"json_path": str(json_path), "markdown_path": str(markdown_path)}

    def report_files(self, report_id: str) -> dict[str, str]:
        markdown_path = self.report_dir / f"{report_id}.md"
        json_path = self.report_dir / f"{report_id}.json"
        return {"json_path": str(json_path), "markdown_path": str(markdown_path)}

    def save_handoff_doc(self, report_id: str, agent: str, markdown: str) -> dict[str, str]:
        safe_agent = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in agent)
        markdown_path = self.handoff_dir / f"{report_id}_{safe_agent}.md"
        markdown_path.write_text(markdown, encoding="utf-8")
        return {"markdown_path": str(markdown_path)}

    def load_task(self, demand_task_id: str) -> DemandTask | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT payload_json FROM demand_tasks WHERE demand_task_id = ?",
                (demand_task_id,),
            ).fetchone()
        if not row:
            return None
        return task_from_payload(json.loads(row["payload_json"]))

    def load_all_tasks(self) -> dict[str, DemandTask]:
        with self._connection() as conn:
            rows = conn.execute("SELECT payload_json FROM demand_tasks").fetchall()
        tasks = [task_from_payload(json.loads(row["payload_json"])) for row in rows]
        return {task.demand_task_id: task for task in tasks}

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS demand_tasks (
                    demand_task_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    status TEXT NOT NULL,
                    user_input TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    report_id TEXT,
                    completeness_score INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS demand_reports (
                    report_id TEXT PRIMARY KEY,
                    demand_task_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    completeness_score INTEGER NOT NULL,
                    markdown_path TEXT NOT NULL,
                    json_path TEXT NOT NULL,
                    structured_json TEXT NOT NULL,
                    markdown TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )


def task_to_payload(task: DemandTask) -> dict[str, Any]:
    return {
        "demand_task_id": task.demand_task_id,
        "user_input": task.user_input,
        "context": task.context,
        "project_id": task.project_id,
        "status": task.status.value,
        "fields": {name: field.to_dict() for name, field in task.fields.items()},
        "intent": task.intent,
        "questions": [question.to_dict() for question in task.questions],
        "report": task.report.to_dict() if task.report else None,
        "analysis_mode": task.analysis_mode,
        "llm_status": task.llm_status,
        "llm_model": task.llm_model,
        "llm_error": task.llm_error,
        "llm_repair_status": task.llm_repair_status,
        "llm_repair_attempts": task.llm_repair_attempts,
        "llm_validation_issues": task.llm_validation_issues,
        "report_insights": task.report_insights,
        "extra_fields": task.extra_fields,
        "conversation_turns": task.conversation_turns,
        "version": task.version,
        "history": task.history,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


def task_from_payload(payload: dict[str, Any]) -> DemandTask:
    task = DemandTask(
        demand_task_id=payload["demand_task_id"],
        user_input=payload["user_input"],
        context=payload.get("context") or {},
        project_id=payload.get("project_id"),
        status=TaskStatus(payload.get("status", TaskStatus.CREATED.value)),
        fields={
            name: DemandField(
                field_name=value["field_name"],
                field_value=value["field_value"],
                source=FieldSource(value["source"]),
                confidence=value["confidence"],
                version=value.get("version", "v1.0"),
            )
            for name, value in (payload.get("fields") or {}).items()
        },
        intent=payload.get("intent") or {},
        questions=[
            ClarifyingQuestion(
                field=value["field"],
                question=value["question"],
                options=value["options"],
            )
            for value in payload.get("questions", [])
        ],
        analysis_mode=payload.get("analysis_mode", "rules_fallback"),
        llm_status=payload.get("llm_status", "disabled"),
        llm_model=payload.get("llm_model"),
        llm_error=payload.get("llm_error"),
        llm_repair_status=payload.get("llm_repair_status", "not_needed"),
        llm_repair_attempts=payload.get("llm_repair_attempts", 0),
        llm_validation_issues=payload.get("llm_validation_issues") or [],
        report_insights=payload.get("report_insights") or {},
        extra_fields=payload.get("extra_fields") or {},
        conversation_turns=payload.get("conversation_turns") or [],
        version=payload.get("version", "v1.0"),
        history=payload.get("history", []),
        created_at=datetime.fromisoformat(payload["created_at"]),
        updated_at=datetime.fromisoformat(payload["updated_at"]),
    )
    report_payload = payload.get("report")
    if report_payload:
        task.report = DemandReport(
            report_id=report_payload["report_id"],
            demand_task_id=report_payload["demand_task_id"],
            markdown=report_payload["markdown"],
            structured_json=report_payload["structured_json"],
            completeness_score=report_payload["completeness_score"],
            version=report_payload.get("version", "v1.0"),
        )
    return task

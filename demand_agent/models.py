from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    CREATED = "Created"
    PARSING = "Parsing"
    NEED_CLARIFICATION = "NeedClarification"
    SCORING = "Scoring"
    ASSUMPTION_MODE = "AssumptionMode"
    REPORT_READY = "ReportReady"
    CONFIRMED = "Confirmed"
    HANDOFF = "Handoff"


class FieldSource(str, Enum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    ASSUMPTION = "assumption"
    USER_CONFIRMED = "user_confirmed"


@dataclass
class DemandField:
    field_name: str
    field_value: Any
    source: FieldSource
    confidence: float
    version: str = "v1.0"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source"] = self.source.value
        return data


@dataclass
class ClarifyingQuestion:
    field: str
    question: str
    options: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DemandReport:
    report_id: str
    demand_task_id: str
    markdown: str
    structured_json: dict[str, Any]
    completeness_score: int
    version: str = "v1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DemandTask:
    demand_task_id: str
    user_input: str
    context: dict[str, Any] = field(default_factory=dict)
    project_id: str | None = None
    status: TaskStatus = TaskStatus.CREATED
    fields: dict[str, DemandField] = field(default_factory=dict)
    intent: dict[str, Any] = field(default_factory=dict)
    questions: list[ClarifyingQuestion] = field(default_factory=list)
    report: DemandReport | None = None
    version: str = "v1.0"
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    def record(self, event: str, payload: dict[str, Any] | None = None) -> None:
        self.history.append(
            {
                "event": event,
                "payload": payload or {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def to_summary(self) -> dict[str, Any]:
        return {
            "demand_task_id": self.demand_task_id,
            "project_id": self.project_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

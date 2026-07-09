from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


JsonDict = MutableDict.as_mutable(JSON().with_variant(JSONB, "postgresql"))
JsonList = MutableList.as_mutable(JSON().with_variant(JSONB, "postgresql"))


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CulturalIPTask(Base, TimestampMixin):
    __tablename__ = "cultural_ip_tasks"

    ip_task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    demand_report_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(64), index=True, default="created")
    structured_report: Mapped[dict] = mapped_column(JsonDict, default=dict)
    options: Mapped[dict] = mapped_column(JsonDict, default=dict)
    retrieval_scope: Mapped[list] = mapped_column(JsonList, default=list)
    selected_direction: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    directions: Mapped[list["IPDirection"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    reports: Mapped[list["IPReport"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class IPDirection(Base, TimestampMixin):
    __tablename__ = "ip_directions"

    direction_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ip_task_id: Mapped[str] = mapped_column(ForeignKey("cultural_ip_tasks.ip_task_id"), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    type: Mapped[str] = mapped_column(String(128))
    one_sentence: Mapped[str] = mapped_column(Text)
    story_core: Mapped[str] = mapped_column(Text)
    cultural_basis: Mapped[list] = mapped_column(JsonList, default=list)
    symbol_system: Mapped[list] = mapped_column(JsonList, default=list)
    visual_translation: Mapped[dict] = mapped_column(JsonDict, default=dict)
    risk_assessment: Mapped[dict] = mapped_column(JsonDict, default=dict)
    score: Mapped[int] = mapped_column(Integer, default=0)
    recommendation: Mapped[str] = mapped_column(String(64), default="备选")

    task: Mapped[CulturalIPTask] = relationship(back_populates="directions")


class CulturalSource(Base, TimestampMixin):
    __tablename__ = "cultural_sources"

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    source_type: Mapped[str] = mapped_column(String(128), index=True)
    credibility_level: Mapped[str] = mapped_column(String(8), default="C")
    summary: Mapped[str] = mapped_column(Text)
    url_or_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JsonList, default=list)


class SymbolItem(Base, TimestampMixin):
    __tablename__ = "symbol_items"

    symbol_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    level: Mapped[str] = mapped_column(String(64))
    meaning: Mapped[str] = mapped_column(Text)
    visual_feature: Mapped[str] = mapped_column(Text)
    design_usage: Mapped[list] = mapped_column(JsonList, default=list)
    source_ids: Mapped[list] = mapped_column(JsonList, default=list)


class RiskAssessment(Base, TimestampMixin):
    __tablename__ = "risk_assessments"

    risk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ip_task_id: Mapped[str] = mapped_column(ForeignKey("cultural_ip_tasks.ip_task_id"), index=True)
    direction_id: Mapped[str | None] = mapped_column(
        ForeignKey("ip_directions.direction_id"), nullable=True, index=True
    )
    overall_level: Mapped[str] = mapped_column(String(32))
    items: Mapped[list] = mapped_column(JsonList, default=list)


class IPReport(Base, TimestampMixin):
    __tablename__ = "ip_reports"

    ip_report_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ip_task_id: Mapped[str] = mapped_column(ForeignKey("cultural_ip_tasks.ip_task_id"), index=True)
    theme: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(64), default="completed")
    report_markdown: Mapped[str] = mapped_column(Text)
    report_json: Mapped[dict] = mapped_column(JsonDict, default=dict)
    version: Mapped[str] = mapped_column(String(32), default="v1.0")

    task: Mapped[CulturalIPTask] = relationship(back_populates="reports")


class KnowledgeChunk(Base, TimestampMixin):
    __tablename__ = "knowledge_chunks"

    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    source_type: Mapped[str] = mapped_column(String(128), index=True)
    credibility_level: Mapped[str] = mapped_column(String(8), default="C")
    content: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JsonList, default=list)
    embedding: Mapped[list] = mapped_column(JsonList, default=list)
    relevance_hint: Mapped[float] = mapped_column(Float, default=0.0)


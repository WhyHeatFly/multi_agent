"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def json_type():
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "cultural_ip_tasks",
        sa.Column("ip_task_id", sa.String(length=64), nullable=False),
        sa.Column("demand_report_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("structured_report", json_type(), nullable=False),
        sa.Column("options", json_type(), nullable=False),
        sa.Column("retrieval_scope", json_type(), nullable=False),
        sa.Column("selected_direction", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("ip_task_id"),
    )
    op.create_index("ix_cultural_ip_tasks_status", "cultural_ip_tasks", ["status"])

    op.create_table(
        "cultural_sources",
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=128), nullable=False),
        sa.Column("credibility_level", sa.String(length=8), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("url_or_reference", sa.Text(), nullable=True),
        sa.Column("tags", json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("source_id"),
    )
    op.create_index("ix_cultural_sources_title", "cultural_sources", ["title"])
    op.create_index("ix_cultural_sources_source_type", "cultural_sources", ["source_type"])

    op.create_table(
        "knowledge_chunks",
        sa.Column("chunk_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=128), nullable=False),
        sa.Column("credibility_level", sa.String(length=8), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("tags", json_type(), nullable=False),
        sa.Column("embedding", json_type(), nullable=False),
        sa.Column("relevance_hint", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("chunk_id"),
    )
    op.create_index("ix_knowledge_chunks_source_id", "knowledge_chunks", ["source_id"])
    op.create_index("ix_knowledge_chunks_title", "knowledge_chunks", ["title"])
    op.create_index("ix_knowledge_chunks_source_type", "knowledge_chunks", ["source_type"])

    op.create_table(
        "symbol_items",
        sa.Column("symbol_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("level", sa.String(length=64), nullable=False),
        sa.Column("meaning", sa.Text(), nullable=False),
        sa.Column("visual_feature", sa.Text(), nullable=False),
        sa.Column("design_usage", json_type(), nullable=False),
        sa.Column("source_ids", json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("symbol_id"),
    )
    op.create_index("ix_symbol_items_name", "symbol_items", ["name"])

    op.create_table(
        "ip_directions",
        sa.Column("direction_id", sa.String(length=64), nullable=False),
        sa.Column("ip_task_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=128), nullable=False),
        sa.Column("one_sentence", sa.Text(), nullable=False),
        sa.Column("story_core", sa.Text(), nullable=False),
        sa.Column("cultural_basis", json_type(), nullable=False),
        sa.Column("symbol_system", json_type(), nullable=False),
        sa.Column("visual_translation", json_type(), nullable=False),
        sa.Column("risk_assessment", json_type(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("recommendation", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["ip_task_id"], ["cultural_ip_tasks.ip_task_id"]),
        sa.PrimaryKeyConstraint("direction_id"),
    )
    op.create_index("ix_ip_directions_ip_task_id", "ip_directions", ["ip_task_id"])
    op.create_index("ix_ip_directions_name", "ip_directions", ["name"])

    op.create_table(
        "ip_reports",
        sa.Column("ip_report_id", sa.String(length=64), nullable=False),
        sa.Column("ip_task_id", sa.String(length=64), nullable=False),
        sa.Column("theme", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("report_markdown", sa.Text(), nullable=False),
        sa.Column("report_json", json_type(), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["ip_task_id"], ["cultural_ip_tasks.ip_task_id"]),
        sa.PrimaryKeyConstraint("ip_report_id"),
    )
    op.create_index("ix_ip_reports_ip_task_id", "ip_reports", ["ip_task_id"])

    op.create_table(
        "risk_assessments",
        sa.Column("risk_id", sa.String(length=64), nullable=False),
        sa.Column("ip_task_id", sa.String(length=64), nullable=False),
        sa.Column("direction_id", sa.String(length=64), nullable=True),
        sa.Column("overall_level", sa.String(length=32), nullable=False),
        sa.Column("items", json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["direction_id"], ["ip_directions.direction_id"]),
        sa.ForeignKeyConstraint(["ip_task_id"], ["cultural_ip_tasks.ip_task_id"]),
        sa.PrimaryKeyConstraint("risk_id"),
    )
    op.create_index("ix_risk_assessments_ip_task_id", "risk_assessments", ["ip_task_id"])
    op.create_index("ix_risk_assessments_direction_id", "risk_assessments", ["direction_id"])


def downgrade() -> None:
    op.drop_table("risk_assessments")
    op.drop_table("ip_reports")
    op.drop_table("ip_directions")
    op.drop_table("symbol_items")
    op.drop_table("knowledge_chunks")
    op.drop_table("cultural_sources")
    op.drop_table("cultural_ip_tasks")


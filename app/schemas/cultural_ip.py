from typing import Any

from pydantic import BaseModel, Field


class TaskOptions(BaseModel):
    num_directions: int = Field(default=5, ge=1, le=8)
    risk_check: bool = True
    need_visual_translation: bool = True


class CreateTaskRequest(BaseModel):
    demand_report_id: str | None = None
    structured_report: dict[str, Any]
    options: TaskOptions = Field(default_factory=TaskOptions)


class CreateTaskResponse(BaseModel):
    ip_task_id: str
    status: str


class TaskSummaryResponse(BaseModel):
    ip_task_id: str
    demand_report_id: str | None = None
    status: str
    selected_direction: str | None = None
    title: str
    target_users: list[str] = Field(default_factory=list)
    usage_scenarios: list[str] = Field(default_factory=list)
    product_categories: list[str] = Field(default_factory=list)
    cultural_preferences: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    error_message: str | None = None


class TaskListResponse(BaseModel):
    tasks: list[TaskSummaryResponse]


class DirectionResponse(BaseModel):
    direction_id: str
    direction_number: int
    name: str
    type: str
    score: int
    summary: str
    story_core: str
    risk_level: str
    recommendation: str


class DirectionsListResponse(BaseModel):
    ip_task_id: str
    status: str
    directions: list[DirectionResponse]


class FeedbackRequest(BaseModel):
    selected_direction: str
    feedback: str


class FeedbackResponse(BaseModel):
    ip_task_id: str
    selected_direction: str
    status: str
    updated_solution: dict[str, Any]


class GenerateReportRequest(BaseModel):
    selected_direction: str


class ReportFeedbackRequest(BaseModel):
    feedback: str


class ReportResponse(BaseModel):
    ip_report_id: str
    ip_task_id: str
    status: str
    report_markdown: str
    report_json: dict[str, Any]


class HandoffRequest(BaseModel):
    target_agents: list[str] = Field(default_factory=list)


class HandoffResponse(BaseModel):
    ip_task_id: str
    packages: list[dict[str, Any]]


class IngestResponse(BaseModel):
    chunks_inserted: int
    sources: list[str]


class KnowledgeChunkResponse(BaseModel):
    chunk_id: str
    source_id: str
    title: str
    source_type: str
    credibility_level: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    relevance_hint: float = 0.0
    created_at: str
    updated_at: str


class KnowledgeListResponse(BaseModel):
    chunks: list[KnowledgeChunkResponse]
    total: int


class KnowledgeSearchResponse(BaseModel):
    results: list[dict[str, Any]]


class KnowledgeDeleteResponse(BaseModel):
    source_id: str
    deleted_chunks: int

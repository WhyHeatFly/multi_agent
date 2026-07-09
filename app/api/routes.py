from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.schemas.cultural_ip import (
    CreateTaskRequest,
    CreateTaskResponse,
    DirectionsListResponse,
    FeedbackRequest,
    FeedbackResponse,
    GenerateReportRequest,
    HandoffRequest,
    HandoffResponse,
    IngestResponse,
    KnowledgeDeleteResponse,
    KnowledgeListResponse,
    KnowledgeSearchResponse,
    ReportFeedbackRequest,
    ReportResponse,
    TaskListResponse,
)
from app.services.cultural_ip import CulturalIPService
from app.services.rag import KnowledgeService

router = APIRouter()
service = CulturalIPService()


def _list_field(report: dict, key: str) -> list[str]:
    value = report.get(key, []) if isinstance(report, dict) else []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []


def _task_title(report: dict, selected_direction: str | None, fallback: str) -> str:
    if selected_direction:
        return selected_direction
    cultural = _list_field(report, "cultural_preferences")
    scenarios = _list_field(report, "usage_scenarios")
    categories = _list_field(report, "product_categories")
    parts = (cultural[:2] + scenarios[:1] + categories[:1])[:4]
    return " · ".join(parts) if parts else fallback

knowledge = KnowledgeService()


def _chunk_response(chunk) -> dict:
    return {
        "chunk_id": chunk.chunk_id,
        "source_id": chunk.source_id,
        "title": chunk.title,
        "source_type": chunk.source_type,
        "credibility_level": chunk.credibility_level,
        "summary": chunk.summary,
        "tags": [str(tag) for tag in (chunk.tags or [])],
        "relevance_hint": float(chunk.relevance_hint or 0.0),
        "created_at": chunk.created_at.isoformat(),
        "updated_at": chunk.updated_at.isoformat(),
    }


@router.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    db.execute(text("select 1"))
    return {
        "status": "ready",
        "database": "ok",
        "llm_configured": settings.llm_configured,
        "app_env": settings.app_env,
    }


@router.post("/v1/agents/cultural-ip/tasks", response_model=CreateTaskResponse)
def create_task(payload: CreateTaskRequest, db: Session = Depends(get_db)) -> CreateTaskResponse:
    try:
        task = service.create_task(db, payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return CreateTaskResponse(ip_task_id=task.ip_task_id, status=task.status)



@router.get("/v1/agents/cultural-ip/tasks", response_model=TaskListResponse)
def list_tasks(limit: int = 50, db: Session = Depends(get_db)) -> TaskListResponse:
    tasks = service.list_tasks(db, limit=limit)
    return TaskListResponse(
        tasks=[
            {
                "ip_task_id": task.ip_task_id,
                "demand_report_id": task.demand_report_id,
                "status": task.status,
                "selected_direction": task.selected_direction,
                "title": _task_title(task.structured_report, task.selected_direction, task.ip_task_id),
                "target_users": _list_field(task.structured_report, "target_users"),
                "usage_scenarios": _list_field(task.structured_report, "usage_scenarios"),
                "product_categories": _list_field(task.structured_report, "product_categories"),
                "cultural_preferences": _list_field(task.structured_report, "cultural_preferences"),
                "created_at": task.created_at.isoformat(),
                "updated_at": task.updated_at.isoformat(),
                "error_message": task.error_message,
            }
            for task in tasks
        ]
    )


@router.delete("/v1/agents/cultural-ip/tasks/{ip_task_id}")
def delete_task(ip_task_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        service.delete_task(db, ip_task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ip_task_id": ip_task_id, "deleted": True}

@router.get(
    "/v1/agents/cultural-ip/tasks/{ip_task_id}/directions",
    response_model=DirectionsListResponse,
)
def list_directions(ip_task_id: str, db: Session = Depends(get_db)) -> DirectionsListResponse:
    try:
        task = service.get_task(db, ip_task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    directions = service.list_directions(db, ip_task_id)
    return DirectionsListResponse(
        ip_task_id=ip_task_id,
        status=task.status,
        directions=[
            {
                "direction_id": item.direction_id,
                "direction_number": index,
                "name": item.name,
                "type": item.type,
                "score": item.score,
                "summary": item.one_sentence,
                "story_core": item.story_core,
                "risk_level": item.risk_assessment.get("overall_level", "Low"),
                "recommendation": item.recommendation,
            }
            for index, item in enumerate(directions, start=1)
        ],
    )


@router.post(
    "/v1/agents/cultural-ip/tasks/{ip_task_id}/feedback",
    response_model=FeedbackResponse,
)
def submit_feedback(
    ip_task_id: str,
    payload: FeedbackRequest,
    db: Session = Depends(get_db),
) -> FeedbackResponse:
    try:
        updated = service.apply_feedback(
            db,
            ip_task_id=ip_task_id,
            selected_direction=payload.selected_direction,
            feedback=payload.feedback,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    updated_direction = updated.get("direction", {}) if isinstance(updated, dict) else {}
    return FeedbackResponse(
        ip_task_id=ip_task_id,
        selected_direction=updated_direction.get("name") or payload.selected_direction,
        status="direction_revised",
        updated_solution=updated,
    )


@router.post("/v1/agents/cultural-ip/tasks/{ip_task_id}/report", response_model=ReportResponse)
def generate_report(
    ip_task_id: str,
    payload: GenerateReportRequest,
    db: Session = Depends(get_db),
) -> ReportResponse:
    try:
        report = service.generate_report(db, ip_task_id, payload.selected_direction)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    report_json = report.report_json
    report_json["ip_report_id"] = report.ip_report_id
    return ReportResponse(
        ip_report_id=report.ip_report_id,
        ip_task_id=report.ip_task_id,
        status=report.status,
        report_markdown=report.report_markdown,
        report_json=report_json,
    )


@router.get("/v1/agents/cultural-ip/tasks/{ip_task_id}/report", response_model=ReportResponse)
def get_report(ip_task_id: str, db: Session = Depends(get_db)) -> ReportResponse:
    report = service.get_latest_report(db, ip_task_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    report_json = report.report_json
    report_json["ip_report_id"] = report.ip_report_id
    return ReportResponse(
        ip_report_id=report.ip_report_id,
        ip_task_id=report.ip_task_id,
        status=report.status,
        report_markdown=report.report_markdown,
        report_json=report_json,
    )


@router.post("/v1/agents/cultural-ip/tasks/{ip_task_id}/report/feedback", response_model=ReportResponse)
def submit_report_feedback(
    ip_task_id: str,
    payload: ReportFeedbackRequest,
    db: Session = Depends(get_db),
) -> ReportResponse:
    try:
        report = service.apply_report_feedback(db, ip_task_id, payload.feedback)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    report_json = report.report_json
    report_json["ip_report_id"] = report.ip_report_id
    return ReportResponse(
        ip_report_id=report.ip_report_id,
        ip_task_id=report.ip_task_id,
        status=report.status,
        report_markdown=report.report_markdown,
        report_json=report_json,
    )


@router.post(
    "/v1/agents/cultural-ip/tasks/{ip_task_id}/handoff",
    response_model=HandoffResponse,
)
def create_handoff(
    ip_task_id: str,
    payload: HandoffRequest,
    db: Session = Depends(get_db),
) -> HandoffResponse:
    try:
        packages = service.build_handoff(db, ip_task_id, payload.target_agents)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return HandoffResponse(ip_task_id=ip_task_id, packages=packages)


@router.post(
    "/v1/admin/knowledge/ingest",
    response_model=IngestResponse,
)
def ingest_knowledge(reset: bool = False, db: Session = Depends(get_db)) -> IngestResponse:
    inserted, sources = knowledge.ingest_directory(db, reset=reset)
    return IngestResponse(chunks_inserted=inserted, sources=sources)

@router.get(
    "/v1/admin/knowledge/chunks",
    response_model=KnowledgeListResponse,
)
def list_knowledge_chunks(
    query: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> KnowledgeListResponse:
    chunks, total = knowledge.list_chunks(db, query=query, limit=limit)
    return KnowledgeListResponse(chunks=[_chunk_response(chunk) for chunk in chunks], total=total)


@router.get(
    "/v1/admin/knowledge/search",
    response_model=KnowledgeSearchResponse,
)
def search_knowledge(
    q: str,
    limit: int = 8,
    db: Session = Depends(get_db),
) -> KnowledgeSearchResponse:
    terms = [term.strip() for term in q.replace("，", ",").split(",") if term.strip()]
    if not terms:
        terms = [q]
    return KnowledgeSearchResponse(results=knowledge.search(db, terms, limit=limit))


@router.delete(
    "/v1/admin/knowledge/sources/{source_id}",
    response_model=KnowledgeDeleteResponse,
)
def delete_knowledge_source(source_id: str, db: Session = Depends(get_db)) -> KnowledgeDeleteResponse:
    deleted = knowledge.delete_source(db, source_id)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Knowledge source not found")
    return KnowledgeDeleteResponse(source_id=source_id, deleted_chunks=deleted)

@router.get(
    "/v1/admin/tasks/{ip_task_id}/debug",
)
def debug_task(ip_task_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        task = service.get_task(db, ip_task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    directions = service.list_directions(db, ip_task_id)
    report = service.get_latest_report(db, ip_task_id)
    return {
        "task": {
            "ip_task_id": task.ip_task_id,
            "status": task.status,
            "retrieval_scope": task.retrieval_scope,
            "selected_direction": task.selected_direction,
            "error_message": task.error_message,
        },
        "directions": [
            {
                "direction_id": item.direction_id,
                "name": item.name,
                "score": item.score,
                "risk_assessment": item.risk_assessment,
            }
            for item in directions
        ],
        "report": report.ip_report_id if report else None,
    }








from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="文化 IP 设计师 Agent API",
)

app.include_router(router)

static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/task", include_in_schema=False)
def task_detail() -> FileResponse:
    return FileResponse(static_dir / "task.html")


@app.get("/knowledge", include_in_schema=False)
def knowledge_admin() -> FileResponse:
    return FileResponse(static_dir / "knowledge.html")


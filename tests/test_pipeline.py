import os

os.environ["DATABASE_URL"] = "sqlite:///./test_cultural_ip.db"
os.environ["LLM_API_KEY"] = ""

import pytest
from fastapi.testclient import TestClient

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.schemas.cultural_ip import CreateTaskRequest
from app.services.cultural_ip import CulturalIPService
from app.services.llm import LLMAdapter
from app.services.rag import KnowledgeService


def setup_module() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


MOCK_LLM_RESPONSE = {
    "ip_directions": [
        {
            "name": "西湖春水同心礼",
            "type": "爱情祝福型",
            "one_sentence": "以西湖春水、并蒂莲和丝线表达新人同心与春日祝福。",
            "story_core": "三月西湖春水初生，丝线牵连两心，并蒂莲承载新人同心的祝愿。方案将湖波、莲花与喜鹊转译为丝巾主纹样和礼盒边饰，让婚礼回礼既有地域记忆，也有温柔祝福。",
            "core_symbols": ["并蒂莲", "湖波", "丝线", "喜鹊"],
            "visual_translation": {
                "pattern": {
                    "main_pattern": "并蒂莲与湖波组合",
                    "secondary_patterns": ["丝线边纹", "喜鹊小标"],
                    "composition": "丝巾中心主纹样，礼盒四边连续湖波纹。",
                },
                "color": {
                    "main_colors": [
                        {"name": "湖蓝", "hex": "#8FBFD1"},
                        {"name": "浅粉", "hex": "#EAC6C6"},
                    ],
                    "accent_colors": [{"name": "淡金", "hex": "#C8A96A"}],
                },
                "craft": ["数码印花", "局部烫金"],
                "packaging": "礼盒外层使用湖波压纹，内卡讲述同心故事。",
                "copywriting": "一方春水，两心同禧。",
            },
            "risk_assessment": {"overall_level": "Low", "items": []},
            "score": 93,
            "recommendation": "主推",
        },
        {
            "name": "江南丝语喜礼",
            "type": "非遗工艺型",
            "one_sentence": "以丝线和喜鹊构成轻盈婚礼回礼方案。",
            "story_core": "方案以江南丝绸工艺为文化起点，用丝线表达亲友见证与祝福连结，并用喜鹊强化喜事寓意。视觉上强调轻盈纹样和低饱和配色，适合丝巾、香囊和礼盒。",
            "core_symbols": ["丝线", "喜鹊", "花窗"],
            "visual_translation": {},
            "risk_assessment": {"overall_level": "Low", "items": []},
            "score": 88,
            "recommendation": "备选",
        },
        {
            "name": "湖蓝雅集回礼",
            "type": "高端礼赠型",
            "one_sentence": "以湖蓝色和江南留白打造高端婚礼礼赠。",
            "story_core": "方案将西湖水意和江南清雅留白作为审美核心，弱化传统大红大金，以湖蓝、米白和淡金建立更克制的婚礼礼赠气质。",
            "core_symbols": ["湖波", "花窗", "淡金小标"],
            "visual_translation": {},
            "risk_assessment": {"overall_level": "Low", "items": []},
            "score": 84,
            "recommendation": "备选",
        },
    ]
}


def test_cultural_ip_pipeline_requires_llm_key() -> None:
    service = CulturalIPService()
    with SessionLocal() as db:
        request = CreateTaskRequest(
            demand_report_id="dr_no_key",
            structured_report={"cultural_preferences": ["西湖", "婚嫁"]},
        )
        with pytest.raises(RuntimeError, match="LLM_API_KEY"):
            service.create_task(db, request)


def test_cultural_ip_pipeline_uses_llm_generation(monkeypatch) -> None:
    def fake_chat_json_sync(self, messages, temperature=0.7):
        return MOCK_LLM_RESPONSE

    monkeypatch.setattr(LLMAdapter, "ensure_configured", lambda self: None)
    monkeypatch.setattr(LLMAdapter, "chat_json_sync", fake_chat_json_sync)

    service = CulturalIPService()
    knowledge = KnowledgeService()
    with SessionLocal() as db:
        knowledge.ingest_directory(db, reset=True)
        request = CreateTaskRequest(
            demand_report_id="dr_test",
            structured_report={
                "target_users": ["新婚夫妇", "婚礼宾客"],
                "usage_scenarios": ["婚礼回礼", "西湖春游纪念"],
                "product_categories": ["丝巾", "香囊", "礼盒"],
                "cultural_preferences": ["西湖", "婚嫁", "丝绸", "江南"],
                "emotional_keywords": ["浪漫", "吉祥", "温柔", "春日"],
            },
        )
        task = service.create_task(db, request)
        directions = service.list_directions(db, task.ip_task_id)
        report = service.get_latest_report(db, task.ip_task_id)
        status_after_directions = task.status
        direction_count = len(directions)
        first_direction_name = directions[0].name
        first_direction_story = directions[0].story_core
        generated_report = service.generate_report(db, task.ip_task_id, first_direction_name)
        db.refresh(task)
        status_after_report = task.status

    assert status_after_directions == "directions_ready"
    assert status_after_report == "completed"
    assert direction_count >= 3
    assert first_direction_name == "西湖春水同心礼"
    assert first_direction_story
    assert report is None
    assert generated_report is not None
    assert "西湖春水同心礼" in generated_report.report_markdown
    assert generated_report.report_json["visual_translation"]["color"]["main_colors"]


def test_frontend_root_serves_html() -> None:
    from app.main import app

    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert "文化 IP 设计师 Agent" in response.text
    assert "/static/app.js" in response.text





def test_task_list_api_returns_history(monkeypatch) -> None:
    def fake_chat_json_sync(self, messages, temperature=0.7):
        return MOCK_LLM_RESPONSE

    monkeypatch.setattr(LLMAdapter, "ensure_configured", lambda self: None)
    monkeypatch.setattr(LLMAdapter, "chat_json_sync", fake_chat_json_sync)

    service = CulturalIPService()
    with SessionLocal() as db:
        request = CreateTaskRequest(
            demand_report_id="dr_history",
            structured_report={
                "target_users": ["新婚夫妇"],
                "usage_scenarios": ["婚礼回礼"],
                "product_categories": ["丝巾"],
                "cultural_preferences": ["西湖", "婚嫁"],
            },
        )
        task = service.create_task(db, request)

    from app.main import app

    client = TestClient(app)
    response = client.get("/v1/agents/cultural-ip/tasks")

    assert response.status_code == 200
    data = response.json()
    task_ids = [item["ip_task_id"] for item in data["tasks"]]
    assert task.ip_task_id in task_ids
    matched = next(item for item in data["tasks"] if item["ip_task_id"] == task.ip_task_id)
    assert matched["title"] == "西湖 · 婚嫁 · 婚礼回礼 · 丝巾"
    assert matched["usage_scenarios"] == ["婚礼回礼"]



def test_admin_knowledge_management_api() -> None:
    from app.main import app

    client = TestClient(app)
    ingest_response = client.post(
        "/v1/admin/knowledge/ingest?reset=true",
    )
    assert ingest_response.status_code == 200
    assert ingest_response.json()["chunks_inserted"] >= 5

    list_response = client.get(
        "/v1/admin/knowledge/chunks?limit=100",
    )
    assert list_response.status_code == 200
    chunks = list_response.json()["chunks"]
    assert chunks
    source_id = chunks[0]["source_id"]

    search_response = client.get(
        "/v1/admin/knowledge/search?q=西湖,婚嫁",
    )
    assert search_response.status_code == 200
    assert search_response.json()["results"]

    delete_response = client.delete(
        f"/v1/admin/knowledge/sources/{source_id}",
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted_chunks"] >= 1



def test_task_delete_api_removes_history(monkeypatch) -> None:
    def fake_chat_json_sync(self, messages, temperature=0.7):
        return MOCK_LLM_RESPONSE

    monkeypatch.setattr(LLMAdapter, "ensure_configured", lambda self: None)
    monkeypatch.setattr(LLMAdapter, "chat_json_sync", fake_chat_json_sync)

    service = CulturalIPService()
    with SessionLocal() as db:
        request = CreateTaskRequest(
            demand_report_id="dr_delete",
            structured_report={
                "target_users": ["新婚夫妇"],
                "usage_scenarios": ["婚礼回礼"],
                "product_categories": ["丝巾"],
                "cultural_preferences": ["西湖", "婚嫁"],
            },
        )
        task = service.create_task(db, request)
        task_id = task.ip_task_id

    from app.main import app

    client = TestClient(app)
    delete_response = client.delete(f"/v1/agents/cultural-ip/tasks/{task_id}")
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True

    list_response = client.get("/v1/agents/cultural-ip/tasks")
    assert list_response.status_code == 200
    task_ids = [item["ip_task_id"] for item in list_response.json()["tasks"]]
    assert task_id not in task_ids






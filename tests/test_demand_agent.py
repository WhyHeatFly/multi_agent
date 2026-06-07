import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from demand_agent import DemandAnalysisService
import demand_agent.api as api_module
from demand_agent.api import DemandAgentHandler
from demand_agent.llm_analyzer import LLMAnalyzer
from demand_agent.llm_client import DeepSeekClient, LLMClientResult
from demand_agent.models import DemandField, FieldSource, TaskStatus
from demand_agent.rules import parse_intent


class FakeLLMClient:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def complete_json(self, messages, max_tokens=3000):
        self.calls += 1
        return self.result


class SequenceFakeLLMClient:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def complete_json(self, messages, max_tokens=3000):
        self.calls += 1
        if self.results:
            return self.results.pop(0)
        return LLMClientResult(status="disabled", model="test")


def minimal_report_insights():
    return {
        "personas": [
            {
                "name": "新婚人群",
                "motivation": "纪念旅行与婚礼节点",
                "design_implication": "强调浪漫、轻便和纪念价值",
            }
        ],
        "scenario_map": [
            {
                "scenario": "春游礼赠",
                "user_goal": "在旅行中完成体面赠礼和拍照传播",
                "product_requirements": ["轻便", "有祝福寓意"],
            }
        ],
        "product_recommendations": {
            "recommended": [
                {
                    "category": "真丝小方巾",
                    "role": "主产品",
                    "reason": "适合承载西湖纹样并兼具使用价值",
                }
            ]
        },
        "constraints": {"channels": ["小红书"]},
        "risk_notes": ["避免悲情爱情典故"],
        "trend_summary": {
            "data_sources": ["LLM语义推断"],
            "suggestion": "未接入实时趋势数据",
        },
    }


def risk_report_insights():
    insights = minimal_report_insights()
    insights["risk_notes"] = [
        {
            "risk": "实用性定义模糊，需进一步明确具体方向（如日常使用、便携、多功能）",
            "severity": "medium",
            "mitigation": "建议通过追问或用户反馈细化实用性要求",
        }
    ]
    return insights


def complete_llm_fields():
    return {
        "target_users": {"value": ["新婚人群"], "source": "explicit", "confidence": 0.95},
        "usage_scenarios": {"value": ["西湖春游"], "source": "explicit", "confidence": 0.95},
        "product_categories": {"value": ["丝绸伴手礼"], "source": "explicit", "confidence": 0.95},
        "budget_range": {"value": "300-500元", "source": "explicit", "confidence": 0.95},
        "aesthetic_preferences": {"value": ["中式喜庆"], "source": "explicit", "confidence": 0.95},
        "cultural_preferences": {"value": ["西湖", "丝绸"], "source": "explicit", "confidence": 0.95},
        "channel_suggestions": {"value": ["线下文旅店"], "source": "explicit", "confidence": 0.95},
    }


class FakeHandler(DemandAgentHandler):
    def __init__(self, path="/", payload=None):
        self.wfile = BytesIO()
        self.status = None
        self.response_headers = {}
        self.path = path
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        self.rfile = BytesIO(body)
        self.headers = {"Content-Length": str(len(body))}

    def send_response(self, code, message=None):
        self.status = code

    def send_header(self, keyword, value):
        self.response_headers[keyword] = value

    def end_headers(self):
        return


class DemandAnalysisServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.storage_dir = Path(self.tmp.name)
        self.service = DemandAnalysisService(
            storage_dir=self.storage_dir,
            analyzer=LLMAnalyzer(FakeLLMClient(LLMClientResult(status="disabled", model="test"))),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_one_sentence_requirement_extracts_core_fields(self):
        task = self.service.create_task("为 3 月西湖春游的新婚人群设计一套丝绸伴手礼。")

        self.assertIn("target_users", task.fields)
        self.assertIn("time", task.fields)
        self.assertIn("location", task.fields)
        self.assertIn("usage_scenarios", task.fields)
        self.assertIn("product_categories", task.fields)
        self.assertIn("materials", task.fields)
        self.assertIn("cultural_preferences", task.fields)
        self.assertIn(task.status, {TaskStatus.NEED_CLARIFICATION, TaskStatus.REPORT_READY})

    def test_missing_fields_generate_at_most_three_bound_questions(self):
        task = self.service.create_task("帮我做一个文创礼品。")
        questions = self.service.get_questions(task.demand_task_id)

        self.assertTrue(questions["need_clarification"])
        self.assertLessEqual(len(questions["questions"]), 3)
        self.assertEqual(
            [question["field"] for question in questions["questions"]],
            ["usage_scenarios", "target_users", "budget_range"],
        )

    def test_answers_update_fields_as_user_confirmed_and_raise_score(self):
        task = self.service.create_task("帮我做一个文创礼品。")
        before = self.service.get_report(task.demand_task_id)["completeness_score"]

        result = self.service.submit_answers(
            task.demand_task_id,
            {
                "usage_scenarios": "婚礼回礼和春游纪念",
                "budget_range": "300-500元",
                "product_categories": "丝巾、香囊、礼盒",
                "target_users": "新婚人群",
            },
        )
        after = result["completeness_score"]

        self.assertGreater(after, before)
        self.assertEqual(task.fields["budget_range"].source, FieldSource.USER_CONFIRMED)
        self.assertEqual(task.fields["product_categories"].source, FieldSource.USER_CONFIRMED)

    def test_skipped_budget_enters_assumption_mode_and_is_reported(self):
        task = self.service.create_task(
            "为西湖春游的新婚人群设计一套丝绸伴手礼。",
            {"target_channel": ["小红书"]},
        )
        self.service.submit_answers(task.demand_task_id, {"budget_range": "暂不确定"})
        report = self.service.get_report(task.demand_task_id)

        self.assertEqual(task.fields["budget_range"].source, FieldSource.ASSUMPTION)
        self.assertEqual(report["report_json"]["assumptions"]["budget_range"], "300-500元/套")
        self.assertIn("budget_range", report["report_markdown"])

    def test_report_contains_markdown_and_stable_json_sections(self):
        task = self.service.create_task(
            "为 3 月西湖春游的新婚人群设计一套丝绸伴手礼。",
            {"target_channel": ["小红书", "线下文旅店"]},
        )
        report = self.service.get_report(task.demand_task_id)

        self.assertIn("#", report["report_markdown"])
        for key in [
            "confirmed_fields",
            "assumptions",
            "personas",
            "scenario_map",
            "product_recommendations",
            "completeness_score",
        ]:
            self.assertIn(key, report["report_json"])

    def test_handoff_packages_are_agent_specific(self):
        task = self.service.create_task(
            "为 3 月西湖春游的新婚人群设计一套丝绸伴手礼。",
            {"target_channel": ["小红书"]},
        )
        result = self.service.handoff(
            task.demand_task_id,
            ["cultural_ip_agent", "designer_agent", "marketer_agent"],
        )
        packages = {package["agent"]: package for package in result["task_packages"]}

        cultural_inputs = packages["cultural_ip_agent"]["inputs"]
        designer_inputs = packages["designer_agent"]["inputs"]
        self.assertIn("cultural_keywords", cultural_inputs)
        self.assertIn("emotional_keywords", cultural_inputs)
        self.assertNotIn("product_categories", cultural_inputs)
        self.assertIn("product_categories", designer_inputs)
        self.assertIn("materials", designer_inputs)
        self.assertIn("budget_range", packages["designer_agent"]["constraints"])

    def test_handoff_filters_dirty_keywords_and_keeps_context(self):
        task = self.service.create_task(
            "为 3 月西湖春游的新婚人群设计一套丝绸伴手礼。",
            {"brand": "南浔丝绸文化产业园", "target_channel": ["小红书"]},
        )
        task.fields["cultural_preferences"] = DemandField(
            "cultural_preferences",
            ["需", "要", "丝绸"],
            FieldSource.USER_CONFIRMED,
            0.96,
        )
        task.fields["location"] = DemandField("location", ["西湖"], FieldSource.USER_CONFIRMED, 0.96)
        task.report = None

        result = self.service.handoff(task.demand_task_id, ["cultural_ip_agent", "marketer_agent"])
        packages = {package["agent"]: package for package in result["task_packages"]}
        cultural_keywords = packages["cultural_ip_agent"]["inputs"]["cultural_keywords"]
        selling_points = packages["marketer_agent"]["inputs"]["selling_points"]

        self.assertNotIn("需", cultural_keywords)
        self.assertNotIn("要", cultural_keywords)
        self.assertIn("丝绸", cultural_keywords)
        self.assertIn("西湖", cultural_keywords)
        self.assertNotIn("需", selling_points)
        self.assertEqual(packages["cultural_ip_agent"]["context"]["original_requirement"], task.user_input)
        self.assertEqual(packages["cultural_ip_agent"]["context"]["brand_context"], "南浔丝绸文化产业园")

    def test_handoff_outputs_stable_list_fields(self):
        task = self.service.create_task("为新婚人群设计浪漫唯美丝绸伴手礼。")
        task.fields["aesthetic_preferences"] = DemandField(
            "aesthetic_preferences",
            "浪漫唯美",
            FieldSource.USER_CONFIRMED,
            0.96,
        )
        task.fields["channel_suggestions"] = DemandField(
            "channel_suggestions",
            "小红书",
            FieldSource.USER_CONFIRMED,
            0.96,
        )
        task.report = None

        result = self.service.handoff(task.demand_task_id, ["designer_agent", "marketer_agent"])
        packages = {package["agent"]: package for package in result["task_packages"]}

        self.assertIsInstance(packages["designer_agent"]["inputs"]["style"], list)
        self.assertEqual(packages["designer_agent"]["inputs"]["style"], ["浪漫唯美"])
        self.assertIsInstance(packages["designer_agent"]["constraints"]["channels"], list)
        self.assertEqual(packages["designer_agent"]["constraints"]["channels"], ["小红书"])
        self.assertIsInstance(packages["marketer_agent"]["constraints"]["tone"], list)
        self.assertIsInstance(packages["marketer_agent"]["inputs"]["usage_scenarios"], list)

    def test_handoff_uses_confirmed_functional_requirements_for_designer(self):
        task = self.service.create_task("为新婚人群设计一套丝绸伴手礼。")
        task.fields["functional_requirements"] = DemandField(
            "functional_requirements",
            ["日常佩戴/使用", "便携易带"],
            FieldSource.USER_CONFIRMED,
            0.96,
        )
        task.report = None

        result = self.service.handoff(task.demand_task_id, ["designer_agent"])
        designer_inputs = result["task_packages"][0]["inputs"]

        self.assertEqual(designer_inputs["functional_requirements"], ["日常佩戴/使用", "便携易带"])
        self.assertNotEqual(designer_inputs["functional_requirements"], ["便携", "适合展示", "易保存"])

    def test_handoff_marks_pending_clarification_on_each_package(self):
        fake_client = FakeLLMClient(
            LLMClientResult(
                status="success",
                model="deepseek-test",
                content={
                    "intent": {"primary": "新品设计", "secondary": ["礼品定制"], "confidence": 0.95},
                    "fields": complete_llm_fields(),
                    "questions": [],
                    "report_insights": risk_report_insights(),
                },
            )
        )
        service = DemandAnalysisService(
            storage_dir=self.storage_dir,
            analyzer=LLMAnalyzer(fake_client),
        )
        task = service.create_task("为 3 月西湖春游的新婚人群设计一套丝绸伴手礼，要求实用。")

        result = service.handoff(
            task.demand_task_id,
            ["cultural_ip_agent", "designer_agent", "marketer_agent"],
        )

        for package in result["task_packages"]:
            self.assertEqual(package["clarification_status"], TaskStatus.NEED_CLARIFICATION.value)
            self.assertTrue(package["pending_questions"])
            self.assertEqual(package["handoff_warnings"], ["仍有待确认问题，下游产出需按假设处理"])

    def test_handoff_packages_include_executable_brief_sections(self):
        task = self.service.create_task(
            "为 3 月西湖春游的新婚人群设计一套丝绸伴手礼。",
            {"target_channel": ["小红书", "线下文旅店"]},
        )

        result = self.service.handoff(
            task.demand_task_id,
            ["cultural_ip_agent", "designer_agent", "marketer_agent"],
        )

        for package in result["task_packages"]:
            self.assertIn("execution_brief", package)
            self.assertIn("success_criteria", package)
            self.assertIn("field_sources", package)
            self.assertIn("assumptions", package)
            self.assertIn("brief_markdown", package)
            self.assertIn("brief_files", package)
            self.assertIn("任务目标", package["brief_markdown"])
            self.assertIn("验收标准", package["brief_markdown"])

    def test_handoff_agent_specific_briefs_are_distinct(self):
        task = self.service.create_task("为 3 月西湖春游的新婚人群设计一套丝绸伴手礼。")

        result = self.service.handoff(
            task.demand_task_id,
            ["cultural_ip_agent", "designer_agent", "marketer_agent"],
        )
        packages = {package["agent"]: package for package in result["task_packages"]}

        self.assertIn("creative_brief", packages["cultural_ip_agent"])
        self.assertNotIn("design_brief", packages["cultural_ip_agent"])
        self.assertIn("design_brief", packages["designer_agent"])
        self.assertNotIn("marketing_brief", packages["designer_agent"])
        self.assertIn("marketing_brief", packages["marketer_agent"])
        self.assertNotIn("creative_brief", packages["marketer_agent"])

    def test_handoff_brief_files_are_persisted(self):
        task = self.service.create_task("为 3 月西湖春游的新婚人群设计一套丝绸伴手礼。")

        result = self.service.handoff(task.demand_task_id, ["designer_agent"])
        package = result["task_packages"][0]
        json_path = Path(package["brief_files"]["json_path"])
        markdown_path = Path(package["brief_files"]["markdown_path"])

        self.assertTrue(json_path.exists())
        self.assertTrue(markdown_path.exists())
        saved_json = json.loads(json_path.read_text(encoding="utf-8"))
        saved_markdown = markdown_path.read_text(encoding="utf-8")
        self.assertEqual(saved_json["agent"], "designer_agent")
        self.assertIn(task.user_input, saved_markdown)
        self.assertIn("任务目标", saved_markdown)
        self.assertIn("验收标准", saved_markdown)
        self.assertIn("待确认问题", saved_markdown)

    def test_handoff_field_sources_and_default_assumptions_are_exposed(self):
        task = self.service.create_task(
            "为新婚人群设计一套丝绸伴手礼。",
            {"target_channel": ["小红书"]},
        )

        result = self.service.handoff(task.demand_task_id, ["designer_agent"])
        package = result["task_packages"][0]

        self.assertEqual(package["field_sources"]["target_users"], FieldSource.EXPLICIT.value)
        self.assertEqual(package["field_sources"]["channel_suggestions"], FieldSource.CONTEXT.value)
        self.assertEqual(package["assumptions"]["functional_requirements"], package["inputs"]["functional_requirements"])

    def test_report_is_persisted_to_sqlite_and_output_files(self):
        task = self.service.create_task(
            "为 3 月西湖春游的新婚人群设计一套丝绸伴手礼。",
            {"target_channel": ["小红书"]},
        )
        report = self.service.get_report(task.demand_task_id)
        files = report["report_files"]

        json_path = Path(files["json_path"])
        markdown_path = Path(files["markdown_path"])
        self.assertTrue((self.storage_dir / "demand_agent.sqlite3").exists())
        self.assertTrue(json_path.exists())
        self.assertTrue(markdown_path.exists())

        saved_json = json.loads(json_path.read_text(encoding="utf-8"))
        saved_markdown = markdown_path.read_text(encoding="utf-8")
        self.assertEqual(saved_json["demand_task_id"], task.demand_task_id)
        self.assertIn("#", saved_markdown)

        reloaded_service = DemandAnalysisService(storage_dir=self.storage_dir)
        reloaded = reloaded_service.get_report(task.demand_task_id)
        self.assertEqual(reloaded["report_id"], report["report_id"])
        self.assertEqual(reloaded["report_json"]["demand_task_id"], task.demand_task_id)

    def test_llm_success_enhances_fields_questions_and_report_insights(self):
        fake_client = FakeLLMClient(
            LLMClientResult(
                status="success",
                model="deepseek-test",
                content={
                    "intent": {
                        "primary": "新品设计",
                        "secondary": ["礼品定制", "婚庆礼品"],
                        "confidence": 0.94,
                    },
                    "fields": {
                        "target_users": {
                            "value": ["新婚人群"],
                            "source": "explicit",
                            "confidence": 0.91,
                        },
                        "usage_scenarios": {
                            "value": ["婚礼回礼"],
                            "source": "inferred",
                            "confidence": 0.8,
                        },
                    },
                    "questions": [
                        {
                            "field": "budget_range",
                            "question": "期望单套预算大致是多少？",
                            "options": ["99-199元", "300-500元", "500元以上", "暂不确定"],
                            "priority": 1,
                        }
                    ],
                    "report_insights": minimal_report_insights(),
                },
            )
        )
        service = DemandAnalysisService(
            storage_dir=self.storage_dir,
            analyzer=LLMAnalyzer(fake_client),
        )

        task = service.create_task("为 3 月西湖春游的新婚人群设计一套丝绸伴手礼。")
        report = service.get_report(task.demand_task_id)["report_json"]

        self.assertEqual(fake_client.calls, 1)
        self.assertEqual(task.analysis_mode, "llm_enhanced")
        self.assertEqual(task.llm_status, "success")
        self.assertEqual(task.llm_model, "deepseek-test")
        self.assertEqual(task.intent["primary"], "新品设计")
        self.assertEqual(task.fields["target_users"].source, FieldSource.EXPLICIT)
        self.assertEqual(task.questions[0].field, "budget_range")
        self.assertEqual(report["analysis_mode"], "llm_enhanced")
        self.assertEqual(report["risk_notes"], ["避免悲情爱情典故"])
        self.assertEqual(report["trend_summary"]["data_sources"], ["LLM语义推断"])

    def test_llm_failure_falls_back_to_rules(self):
        fake_client = FakeLLMClient(
            LLMClientResult(
                status="failed",
                model="deepseek-test",
                error="timeout",
            )
        )
        service = DemandAnalysisService(
            storage_dir=self.storage_dir,
            analyzer=LLMAnalyzer(fake_client),
        )

        task = service.create_task("为 3 月西湖春游的新婚人群设计一套丝绸伴手礼。")
        report = service.get_report(task.demand_task_id)["report_json"]

        self.assertEqual(task.analysis_mode, "rules_fallback")
        self.assertEqual(task.llm_status, "failed")
        self.assertEqual(task.llm_error, "timeout")
        self.assertIn("target_users", task.fields)
        self.assertEqual(report["analysis_mode"], "rules_fallback")
        self.assertEqual(report["llm_status"], "failed")

    def test_disabled_llm_does_not_require_api_key(self):
        task = self.service.create_task("帮我做一个文创礼品。")
        report = self.service.get_report(task.demand_task_id)["report_json"]

        self.assertEqual(task.analysis_mode, "rules_fallback")
        self.assertEqual(task.llm_status, "disabled")
        self.assertEqual(report["llm_status"], "disabled")

    def test_llm_output_is_sanitized_and_limited(self):
        fake_client = FakeLLMClient(
            LLMClientResult(
                status="success",
                model="deepseek-test",
                content={
                    "intent": {"primary": "新品设计", "secondary": [], "confidence": 2},
                    "fields": {
                        "unknown_field": {"value": "测试", "source": "explicit", "confidence": 1},
                        "budget_range": {"value": "300-500元", "source": "wrong", "confidence": -1},
                    },
                    "questions": [
                        {"field": "budget_range", "question": "预算？", "options": ["300-500元"], "priority": 1},
                        {"field": "target_users", "question": "人群？", "options": ["新婚人群"], "priority": 2},
                        {"field": "usage_scenarios", "question": "场景？", "options": ["婚礼回礼"], "priority": 3},
                        {"field": "product_categories", "question": "品类？", "options": ["丝巾"], "priority": 4},
                        {"field": "unknown_field", "question": "未知？", "options": ["A"], "priority": 0},
                    ],
                    "report_insights": minimal_report_insights(),
                },
            )
        )
        service = DemandAnalysisService(
            storage_dir=self.storage_dir,
            analyzer=LLMAnalyzer(fake_client),
        )

        task = service.create_task("做一个文创礼品。")

        self.assertIn("unknown_field", task.extra_fields)
        self.assertEqual(task.fields["budget_range"].source, FieldSource.INFERRED)
        self.assertEqual(task.fields["budget_range"].confidence, 0.0)
        self.assertEqual(len(task.questions), 3)
        self.assertNotIn("unknown_field", [question.field for question in task.questions])

    def test_llm_string_report_insights_do_not_break_markdown(self):
        fake_client = FakeLLMClient(
            LLMClientResult(
                status="success",
                model="deepseek-test",
                content={
                    "intent": {"primary": "新品设计", "secondary": [], "confidence": 0.9},
                    "fields": {
                        "target_users": {"value": ["新婚人群"], "source": "explicit", "confidence": 0.9},
                        "usage_scenarios": {"value": ["婚礼回礼"], "source": "explicit", "confidence": 0.9},
                        "product_categories": {"value": ["丝巾"], "source": "explicit", "confidence": 0.9},
                    },
                    "questions": [],
                    "report_insights": {
                        "personas": ["新婚人群", "婚礼宾客"],
                        "scenario_map": ["婚礼回礼", "春游纪念"],
                        "product_recommendations": minimal_report_insights()["product_recommendations"],
                    },
                },
            )
        )
        service = DemandAnalysisService(
            storage_dir=self.storage_dir,
            analyzer=LLMAnalyzer(fake_client),
        )

        task = service.create_task("为新婚人群设计一套丝巾。")
        report = service.get_report(task.demand_task_id)

        self.assertIn("新婚人群", report["report_markdown"])
        self.assertIn("婚礼回礼", report["report_markdown"])
        self.assertIsInstance(report["report_json"]["personas"][0], dict)
        self.assertIsInstance(report["report_json"]["scenario_map"][0], dict)

    def test_invalid_json_is_repaired_before_rules_fallback(self):
        fake_client = SequenceFakeLLMClient(
            [
                LLMClientResult(
                    status="invalid_json",
                    model="deepseek-test",
                    error="invalid json",
                    raw_text='{"intent": {"primary": "新品设计"',
                ),
                LLMClientResult(
                    status="success",
                    model="deepseek-test",
                    content={
                        "intent": {"primary": "新品设计", "secondary": ["礼品定制"], "confidence": 0.92},
                        "fields": {
                            "target_users": {"value": ["新婚人群"], "source": "explicit", "confidence": 0.9},
                            "product_categories": {"value": ["真丝小方巾"], "source": "explicit", "confidence": 0.9},
                        },
                        "questions": [],
                        "report_insights": minimal_report_insights(),
                        "extra_fields": {},
                    },
                ),
            ]
        )
        service = DemandAnalysisService(
            storage_dir=self.storage_dir,
            analyzer=LLMAnalyzer(fake_client),
        )

        task = service.create_task("为新婚人群设计真丝小方巾。")
        report = service.get_report(task.demand_task_id)["report_json"]

        self.assertEqual(fake_client.calls, 2)
        self.assertEqual(task.analysis_mode, "llm_enhanced")
        self.assertEqual(task.llm_status, "success_repaired")
        self.assertEqual(task.llm_repair_status, "success")
        self.assertEqual(task.llm_repair_attempts, 1)
        self.assertEqual(report["llm_repair_status"], "success")
        self.assertEqual(report["llm_repair_attempts"], 1)

    def test_missing_report_insights_are_repaired(self):
        fake_client = SequenceFakeLLMClient(
            [
                LLMClientResult(
                    status="success",
                    model="deepseek-test",
                    content={
                        "intent": {"primary": "新品设计", "secondary": [], "confidence": 0.9},
                        "fields": {
                            "target_users": {"value": ["新婚人群"], "source": "explicit", "confidence": 0.9}
                        },
                        "questions": [],
                    },
                ),
                LLMClientResult(
                    status="success",
                    model="deepseek-test",
                    content={
                        "intent": {"primary": "新品设计", "secondary": [], "confidence": 0.9},
                        "fields": {
                            "target_users": {"value": ["新婚人群"], "source": "explicit", "confidence": 0.9}
                        },
                        "questions": [],
                        "report_insights": minimal_report_insights(),
                        "extra_fields": {},
                    },
                ),
            ]
        )
        service = DemandAnalysisService(
            storage_dir=self.storage_dir,
            analyzer=LLMAnalyzer(fake_client),
        )

        task = service.create_task("为新婚人群设计丝绸伴手礼。")

        self.assertEqual(task.llm_status, "success_repaired")
        self.assertEqual(task.report.structured_json["llm_repair_status"], "success")
        self.assertIn("personas", task.report_insights)

    def test_repair_failure_falls_back_to_rules(self):
        fake_client = SequenceFakeLLMClient(
            [
                LLMClientResult(status="invalid_json", model="deepseek-test", error="invalid json", raw_text="{bad"),
                LLMClientResult(status="invalid_json", model="deepseek-test", error="still invalid", raw_text="{bad"),
            ]
        )
        service = DemandAnalysisService(
            storage_dir=self.storage_dir,
            analyzer=LLMAnalyzer(fake_client),
        )

        task = service.create_task("为 3 月西湖春游的新婚人群设计一套丝绸伴手礼。")
        report = service.get_report(task.demand_task_id)["report_json"]

        self.assertEqual(fake_client.calls, 2)
        self.assertEqual(task.analysis_mode, "rules_fallback")
        self.assertEqual(task.llm_status, "invalid_json")
        self.assertEqual(task.llm_repair_status, "failed")
        self.assertEqual(report["llm_repair_status"], "failed")

    def test_repair_disabled_keeps_invalid_json_fallback(self):
        fake_client = SequenceFakeLLMClient(
            [LLMClientResult(status="invalid_json", model="deepseek-test", error="invalid json", raw_text="{bad")]
        )
        service = DemandAnalysisService(
            storage_dir=self.storage_dir,
            analyzer=LLMAnalyzer(fake_client, repair_enabled=False),
        )

        task = service.create_task("为新婚人群设计丝绸伴手礼。")

        self.assertEqual(fake_client.calls, 1)
        self.assertEqual(task.analysis_mode, "rules_fallback")
        self.assertEqual(task.llm_repair_status, "disabled")
        self.assertEqual(task.llm_repair_attempts, 0)

    def test_design_requirement_primary_intent_is_new_product_design(self):
        intent = parse_intent("为 3 月西湖春游的新婚人群设计一套丝绸伴手礼。")

        self.assertEqual(intent["primary"], "新品设计")

    def test_deepseek_client_reads_local_dotenv(self):
        env_path = self.storage_dir / ".env"
        env_path.write_text(
            "\n".join(
                [
                    "DEMAND_LLM_ENABLED=true",
                    "DEEPSEEK_API_KEY=test-key",
                    "DEMAND_LLM_BASE_URL=https://example.test",
                    "DEMAND_LLM_MODEL=deepseek-test",
                    "DEMAND_LLM_TIMEOUT_SECONDS=7",
                ]
            ),
            encoding="utf-8",
        )

        old_cwd = Path.cwd()
        try:
            import os

            os.chdir(self.storage_dir)
            client = DeepSeekClient.from_env()
        finally:
            os.chdir(old_cwd)

        self.assertTrue(client.enabled)
        self.assertEqual(client.api_key, "test-key")
        self.assertEqual(client.base_url, "https://example.test")
        self.assertEqual(client.model, "deepseek-test")
        self.assertEqual(client.timeout_seconds, 7)

    def test_free_followup_updates_fields_and_refreshes_report(self):
        task = self.service.create_task("帮我做一个文创礼品。")
        before = self.service.get_report(task.demand_task_id)["completeness_score"]

        result = self.service.add_followup(
            task.demand_task_id,
            message="预算控制在300元以内，整体风格偏年轻国潮，主要在小红书销售。",
        )
        report = self.service.get_report(task.demand_task_id)["report_json"]

        self.assertGreater(result["completeness_score"], before)
        self.assertEqual(task.fields["budget_range"].field_value, "300元以内")
        self.assertEqual(task.fields["budget_range"].source, FieldSource.USER_CONFIRMED)
        self.assertIn("年轻国潮", task.fields["aesthetic_preferences"].field_value)
        self.assertEqual(len(task.conversation_turns), 1)
        self.assertEqual(report["latest_followup"]["message"], "预算控制在300元以内，整体风格偏年轻国潮，主要在小红书销售。")

    def test_answers_endpoint_reuses_followup_history(self):
        task = self.service.create_task("帮我做一个文创礼品。")

        result = self.service.submit_answers(task.demand_task_id, {"target_users": "游客"})

        self.assertEqual(task.fields["target_users"].source, FieldSource.USER_CONFIRMED)
        self.assertEqual(len(task.conversation_turns), 1)
        self.assertEqual(task.conversation_turns[0]["answers"], {"target_users": "游客"})
        self.assertIn("questions", result)

    def test_multiple_followups_are_persisted_and_reloaded(self):
        task = self.service.create_task("帮我做一个文创礼品。")
        self.service.add_followup(task.demand_task_id, message="主要面向游客。")
        self.service.add_followup(task.demand_task_id, answers={"budget_range": "300元以内"})

        reloaded_service = DemandAnalysisService(
            storage_dir=self.storage_dir,
            analyzer=LLMAnalyzer(FakeLLMClient(LLMClientResult(status="disabled", model="test"))),
        )
        reloaded = reloaded_service.get_task(task.demand_task_id)

        self.assertEqual(len(reloaded.conversation_turns), 2)
        self.assertEqual(reloaded.conversation_turns[1]["answers"], {"budget_range": "300元以内"})

    def test_llm_followup_merges_existing_fields(self):
        fake_client = SequenceFakeLLMClient(
            [
                LLMClientResult(status="disabled", model="test"),
                LLMClientResult(
                    status="success",
                    model="deepseek-test",
                    content={
                        "intent": {"primary": "新品设计", "secondary": ["礼品定制"], "confidence": 0.9},
                        "fields": {
                            "budget_range": {
                                "value": "300元以内",
                                "source": "explicit",
                                "confidence": 0.94,
                            },
                            "aesthetic_preferences": {
                                "value": ["年轻国潮"],
                                "source": "explicit",
                                "confidence": 0.9,
                            },
                        },
                        "questions": [],
                        "report_insights": minimal_report_insights(),
                    },
                ),
            ]
        )
        service = DemandAnalysisService(
            storage_dir=self.storage_dir,
            analyzer=LLMAnalyzer(fake_client),
        )
        task = service.create_task("为新婚人群设计一套丝绸伴手礼。")

        result = service.add_followup(task.demand_task_id, message="预算控制在300元以内，风格更年轻。")

        self.assertEqual(fake_client.calls, 2)
        self.assertIn("target_users", task.fields)
        self.assertEqual(task.fields["budget_range"].field_value, "300元以内")
        self.assertEqual(task.analysis_mode, "llm_enhanced")
        self.assertEqual(result["report_id"], task.report.report_id)

    def test_llm_structured_risk_generates_clarifying_question_even_with_full_score(self):
        fake_client = FakeLLMClient(
            LLMClientResult(
                status="success",
                model="deepseek-test",
                content={
                    "intent": {"primary": "新品设计", "secondary": ["礼品定制"], "confidence": 0.95},
                    "fields": complete_llm_fields(),
                    "questions": [],
                    "report_insights": risk_report_insights(),
                },
            )
        )
        service = DemandAnalysisService(
            storage_dir=self.storage_dir,
            analyzer=LLMAnalyzer(fake_client),
        )

        task = service.create_task("为 3 月西湖春游的新婚人群设计一套丝绸伴手礼，要求实用。")
        report = service.get_report(task.demand_task_id)

        self.assertEqual(task.status, TaskStatus.NEED_CLARIFICATION)
        self.assertEqual(report["completeness_score"], 100)
        self.assertEqual(task.questions[0].field, "functional_requirements")
        self.assertIn("实用性主要体现在哪些方面", task.questions[0].question)
        self.assertEqual(report["report_json"]["recommended_action"], "先确认风险问题后再进入下游交接")
        self.assertIn("pending_questions", report["report_json"])

    def test_answering_risk_question_confirms_field_and_refreshes_report(self):
        fake_client = SequenceFakeLLMClient(
            [
                LLMClientResult(
                    status="success",
                    model="deepseek-test",
                    content={
                        "intent": {"primary": "新品设计", "secondary": ["礼品定制"], "confidence": 0.95},
                        "fields": complete_llm_fields(),
                        "questions": [],
                        "report_insights": risk_report_insights(),
                    },
                ),
                LLMClientResult(
                    status="success",
                    model="deepseek-test",
                    content={
                        "intent": {"primary": "新品设计", "secondary": ["礼品定制"], "confidence": 0.95},
                        "fields": {},
                        "questions": [],
                        "report_insights": risk_report_insights(),
                    },
                ),
            ]
        )
        service = DemandAnalysisService(
            storage_dir=self.storage_dir,
            analyzer=LLMAnalyzer(fake_client),
        )
        task = service.create_task("为 3 月西湖春游的新婚人群设计一套丝绸伴手礼，要求实用。")

        result = service.submit_answers(task.demand_task_id, {"functional_requirements": "日常佩戴/使用、便携易带"})
        report = service.get_report(task.demand_task_id)["report_json"]

        self.assertEqual(task.fields["functional_requirements"].source, FieldSource.USER_CONFIRMED)
        self.assertEqual(task.status, TaskStatus.REPORT_READY)
        self.assertNotIn("functional_requirements", [question["field"] for question in result["questions"]])
        self.assertEqual(report["latest_followup"]["answers"], {"functional_requirements": "日常佩戴/使用、便携易带"})

    def test_structured_risk_markdown_is_rendered_without_python_dict_string(self):
        fake_client = FakeLLMClient(
            LLMClientResult(
                status="success",
                model="deepseek-test",
                content={
                    "intent": {"primary": "新品设计", "secondary": ["礼品定制"], "confidence": 0.95},
                    "fields": complete_llm_fields(),
                    "questions": [],
                    "report_insights": risk_report_insights(),
                },
            )
        )
        service = DemandAnalysisService(
            storage_dir=self.storage_dir,
            analyzer=LLMAnalyzer(fake_client),
        )

        task = service.create_task("为 3 月西湖春游的新婚人群设计一套丝绸伴手礼，要求实用。")
        markdown = service.get_report(task.demand_task_id)["report_markdown"]

        self.assertIn("风险：实用性定义模糊", markdown)
        self.assertIn("等级：medium", markdown)
        self.assertIn("建议：建议通过追问", markdown)
        self.assertNotIn("{'risk'", markdown)

    def test_empty_followup_is_rejected(self):
        task = self.service.create_task("帮我做一个文创礼品。")

        with self.assertRaises(ValueError):
            self.service.add_followup(task.demand_task_id)

class DemandAgentApiTest(unittest.TestCase):
    def tearDown(self):
        api_module.SERVICE = None

    def test_demo_index_is_served(self):
        handler = FakeHandler()
        handler._static("/demo")
        body = handler.wfile.getvalue().decode("utf-8")

        self.assertEqual(handler.status, 200)
        self.assertIn("需求 Agent 功能展示", body)
        self.assertEqual(handler.response_headers["Access-Control-Allow-Origin"], "*")

    def test_demo_asset_is_served(self):
        handler = FakeHandler()
        handler._static("/demo/app.js")
        body = handler.wfile.getvalue().decode("utf-8")

        self.assertEqual(handler.status, 200)
        self.assertIn("analyzeDemand", body)
        self.assertIn("application/javascript", handler.response_headers["Content-Type"])

    def test_json_response_includes_cors_header(self):
        handler = FakeHandler()
        handler._json({"error": "user_input is required"}, status=400)

        self.assertEqual(handler.status, 400)
        self.assertEqual(handler.response_headers["Access-Control-Allow-Origin"], "*")

    def test_empty_followup_request_returns_400(self):
        with TemporaryDirectory() as tmp:
            api_module.SERVICE = DemandAnalysisService(
                storage_dir=Path(tmp),
                analyzer=LLMAnalyzer(FakeLLMClient(LLMClientResult(status="disabled", model="test"))),
            )
            task = api_module.SERVICE.create_task("帮我做一个文创礼品。")
            handler = FakeHandler(
                path=f"/v1/agents/demand-analysis/tasks/{task.demand_task_id}/followups",
                payload={},
            )

            handler.do_POST()

        body = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(handler.status, 400)
        self.assertEqual(body["error"], "message or answers is required")

    def test_unknown_followup_task_returns_404(self):
        with TemporaryDirectory() as tmp:
            api_module.SERVICE = DemandAnalysisService(
                storage_dir=Path(tmp),
                analyzer=LLMAnalyzer(FakeLLMClient(LLMClientResult(status="disabled", model="test"))),
            )
            handler = FakeHandler(
                path="/v1/agents/demand-analysis/tasks/demand_missing/followups",
                payload={"message": "补充预算"},
            )

            handler.do_POST()

        self.assertEqual(handler.status, 404)


if __name__ == "__main__":
    unittest.main()

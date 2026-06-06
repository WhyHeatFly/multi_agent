import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from demand_agent import DemandAnalysisService
from demand_agent.llm_analyzer import LLMAnalyzer
from demand_agent.llm_client import DeepSeekClient, LLMClientResult
from demand_agent.models import FieldSource, TaskStatus
from demand_agent.rules import parse_intent


class FakeLLMClient:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def complete_json(self, messages, max_tokens=3000):
        self.calls += 1
        return self.result


class DemandAnalysisServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.storage_dir = Path(self.tmp.name)
        self.service = DemandAnalysisService(storage_dir=self.storage_dir)

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
                    "report_insights": {
                        "personas": [
                            {
                                "name": "重视仪式感的新婚人群",
                                "motivation": "纪念婚礼与旅行",
                                "design_implication": "强调浪漫与长期保存价值",
                            }
                        ],
                        "risk_notes": ["避免悲情爱情典故"],
                        "trend_summary": {
                            "data_sources": ["LLM语义推断"],
                            "suggestion": "未接入实时趋势数据",
                        },
                    },
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


if __name__ == "__main__":
    unittest.main()

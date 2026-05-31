import unittest

from demand_agent import DemandAnalysisService
from demand_agent.models import FieldSource, TaskStatus


class DemandAnalysisServiceTest(unittest.TestCase):
    def setUp(self):
        self.service = DemandAnalysisService()

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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .llm_analyzer import LLMAnalyzer
from .models import ClarifyingQuestion, DemandReport, DemandTask, FieldSource, TaskStatus
from .rules import (
    DEFAULT_BUDGET,
    DEFAULT_CHANNELS,
    apply_answer,
    build_questions,
    completeness_score,
    extract_fields,
)
from .storage import DemandStorage


RISK_CONFIRMATION_KEYWORDS = ("需进一步明确", "建议追问", "定义模糊", "待确认", "需明确", "不明确")
RISK_QUESTION_FIELDS = {"functional_requirements", "risk_clarifications"}
HANDOFF_STOPWORDS = {"", "需", "要", "的", "和", "及", "与", "或", "了", "在", "对", "为"}
DEFAULT_FUNCTIONAL_REQUIREMENTS = ["便携", "适合展示", "易保存"]


class DemandAnalysisService:
    def __init__(
        self,
        storage: DemandStorage | None = None,
        storage_dir: str | Path = "outputs",
        analyzer: LLMAnalyzer | None = None,
    ) -> None:
        self.storage = storage or DemandStorage(storage_dir)
        self.analyzer = analyzer or LLMAnalyzer()
        self.tasks: dict[str, DemandTask] = self.storage.load_all_tasks()

    def create_task(self, user_input: str, context: dict | None = None) -> DemandTask:
        task = DemandTask(
            demand_task_id=f"demand_{uuid4().hex[:12]}",
            user_input=user_input,
            context=context or {},
            project_id=(context or {}).get("project_id"),
        )
        task.record("created", {"user_input": user_input})
        self.tasks[task.demand_task_id] = task
        self._analyze(task)
        self._persist(task)
        return task

    def get_task(self, demand_task_id: str) -> DemandTask:
        if demand_task_id in self.tasks:
            return self.tasks[demand_task_id]
        task = self.storage.load_task(demand_task_id)
        if task:
            self.tasks[demand_task_id] = task
            return task
        raise KeyError(f"Demand task not found: {demand_task_id}")

    def get_questions(self, demand_task_id: str) -> dict:
        task = self.get_task(demand_task_id)
        return {
            "need_clarification": task.status == TaskStatus.NEED_CLARIFICATION,
            "questions": [question.to_dict() for question in task.questions],
        }

    def submit_answers(self, demand_task_id: str, answers: dict) -> dict:
        return self.add_followup(demand_task_id, answers=answers)

    def add_followup(self, demand_task_id: str, message: str | None = None, answers: dict | None = None) -> dict:
        task = self.get_task(demand_task_id)
        answers = answers or {}
        message = (message or "").strip()
        if not message and not answers:
            raise ValueError("message or answers is required")

        before_fields = self._fields_json(task)
        submitted_questions = [question.to_dict() for question in task.questions]
        for field_name, value in answers.items():
            apply_answer(task.fields, field_name, value, task.version)

        analysis_input = self._followup_analysis_input(message, answers)
        result = self.analyzer.analyze(
            analysis_input,
            task.context,
            task.version,
            existing_fields=task.fields,
            conversation_turns=task.conversation_turns,
            current_questions=task.questions,
        )

        if result.analysis_mode == "llm_enhanced":
            task.intent = result.intent
            task.fields = result.fields
            task.questions = result.questions
            task.report_insights = result.report_insights
            task.extra_fields.update(result.extra_fields)
        elif message:
            self._merge_message_fields(task, message)

        task.analysis_mode = result.analysis_mode
        task.llm_status = result.status
        task.llm_model = result.model
        task.llm_error = result.error
        task.llm_repair_status = result.llm_repair_status
        task.llm_repair_attempts = result.llm_repair_attempts
        task.llm_validation_issues = result.llm_validation_issues

        changed = self._changed_fields(before_fields, task)
        turn = {
            "turn_index": len(task.conversation_turns) + 1,
            "message": message,
            "answers": answers,
            "submitted_questions": submitted_questions,
            "changed_fields": changed,
            "analysis_mode": task.analysis_mode,
            "llm_status": task.llm_status,
            "llm_repair_status": task.llm_repair_status,
            "llm_repair_attempts": task.llm_repair_attempts,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        task.conversation_turns.append(turn)
        task.record("followup_submitted", turn)
        self._score_and_finish(task, preserve_questions=task.analysis_mode == "llm_enhanced")
        task.touch()
        self._persist(task)
        return {
            "demand_task_id": task.demand_task_id,
            "status": task.status.value,
            "changed_fields": changed,
            "questions": [question.to_dict() for question in task.questions],
            "next_action": self._next_action(task),
            "completeness_score": completeness_score(task.fields),
            "llm_repair_status": task.llm_repair_status,
            "llm_repair_attempts": task.llm_repair_attempts,
            "llm_validation_issues": task.llm_validation_issues,
            "report_id": task.report.report_id if task.report else None,
            "report_files": self.storage.report_files(task.report.report_id) if task.report else None,
        }

    def get_report(self, demand_task_id: str) -> dict:
        task = self.get_task(demand_task_id)
        if task.report is None:
            self._generate_report(task)
        self._persist(task)
        return {
            "report_id": task.report.report_id,
            "status": "completed" if task.status in {TaskStatus.REPORT_READY, TaskStatus.CONFIRMED, TaskStatus.HANDOFF} else task.status.value,
            "completeness_score": task.report.completeness_score,
            "report_markdown": task.report.markdown,
            "report_json": task.report.structured_json,
            "report_files": self.storage.report_files(task.report.report_id),
        }

    def handoff(
        self,
        demand_task_id: str,
        target_agents: list[str],
        include_brief_markdown: bool = False,
    ) -> dict:
        task = self.get_task(demand_task_id)
        if task.report is None:
            self._generate_report(task)
        clarification_status = TaskStatus.NEED_CLARIFICATION.value if task.questions else task.status.value
        packages = [self._build_handoff_package(task, agent, clarification_status) for agent in target_agents]
        for package in packages:
            package["brief_files"] = self.storage.handoff_files(task.demand_task_id, package["agent"])
            package["brief_markdown"] = self._handoff_markdown(package)
        self.storage.save_handoff_files(task.demand_task_id, packages)
        response_packages = [
            self._handoff_response_package(package, include_brief_markdown=include_brief_markdown)
            for package in packages
        ]
        task.status = TaskStatus.HANDOFF
        task.record("handoff_generated", {"target_agents": target_agents})
        task.touch()
        self._persist(task)
        return {
            "demand_task_id": task.demand_task_id,
            "status": task.status.value,
            "task_packages": response_packages,
        }

    def _analyze(self, task: DemandTask) -> None:
        task.status = TaskStatus.PARSING
        result = self.analyzer.analyze(task.user_input, task.context, task.version)
        task.intent = result.intent
        task.fields = result.fields
        task.questions = result.questions
        task.analysis_mode = result.analysis_mode
        task.llm_status = result.status
        task.llm_model = result.model
        task.llm_error = result.error
        task.llm_repair_status = result.llm_repair_status
        task.llm_repair_attempts = result.llm_repair_attempts
        task.llm_validation_issues = result.llm_validation_issues
        task.report_insights = result.report_insights
        task.extra_fields = result.extra_fields
        task.record(
            "parsed",
            {
                "analysis_mode": task.analysis_mode,
                "llm_status": task.llm_status,
                "llm_model": task.llm_model,
                "llm_error": task.llm_error,
                "llm_repair_status": task.llm_repair_status,
                "llm_repair_attempts": task.llm_repair_attempts,
                "llm_validation_issues": task.llm_validation_issues,
                "intent": task.intent,
                "fields": self._fields_json(task),
                "extra_fields": task.extra_fields,
            },
        )
        self._score_and_finish(task, preserve_questions=task.analysis_mode == "llm_enhanced")
        task.touch()

    def _score_and_finish(self, task: DemandTask, preserve_questions: bool = False) -> None:
        task.status = TaskStatus.SCORING
        score = completeness_score(task.fields)
        if not preserve_questions:
            task.questions = build_questions(task.fields)
        else:
            task.questions = self._merge_risk_questions(task.questions, self._risk_questions(task))
        has_assumption = any(field.source == FieldSource.ASSUMPTION for field in task.fields.values())
        has_risk_question = any(question.field in RISK_QUESTION_FIELDS for question in task.questions)
        if score < 60 and task.questions:
            task.status = TaskStatus.NEED_CLARIFICATION
            task.report = None
        elif has_risk_question:
            task.status = TaskStatus.NEED_CLARIFICATION
            self._generate_report(task)
        elif 60 <= score < 80 and has_assumption:
            task.status = TaskStatus.ASSUMPTION_MODE
            self._generate_report(task)
        elif 60 <= score < 80:
            task.status = TaskStatus.NEED_CLARIFICATION if task.questions else TaskStatus.REPORT_READY
            self._generate_report(task)
        else:
            task.status = TaskStatus.REPORT_READY
            self._generate_report(task)
        task.record("scored", {"score": score, "status": task.status.value})

    def _generate_report(self, task: DemandTask) -> DemandReport:
        score = completeness_score(task.fields)
        structured = self._structured_report(task, score)
        markdown = self._markdown_report(structured)
        task.report = DemandReport(
            report_id=f"dr_{task.demand_task_id.removeprefix('demand_')}",
            demand_task_id=task.demand_task_id,
            markdown=markdown,
            structured_json=structured,
            completeness_score=score,
            version=task.version,
        )
        paths = self.storage.report_files(task.report.report_id)
        task.report.structured_json["report_files"] = paths
        self.storage.save_report_files(task.report)
        return task.report

    def _persist(self, task: DemandTask) -> None:
        if task.report:
            paths = self.storage.report_files(task.report.report_id)
            task.report.structured_json["report_files"] = paths
            self.storage.save_report_files(task.report)
        self.storage.save_task(task)

    def _structured_report(self, task: DemandTask, score: int) -> dict:
        fields = self._field_values(task)
        assumptions = {
            name: field.field_value
            for name, field in task.fields.items()
            if field.source == FieldSource.ASSUMPTION
        }
        confirmed = {
            name: field.field_value
            for name, field in task.fields.items()
            if field.source in {FieldSource.EXPLICIT, FieldSource.CONTEXT, FieldSource.USER_CONFIRMED}
        }
        weak_fields = [question.field for question in task.questions]
        insights = task.report_insights or {}
        return {
            "report_id": f"dr_{task.demand_task_id.removeprefix('demand_')}",
            "demand_task_id": task.demand_task_id,
            "version": task.version,
            "analysis_mode": task.analysis_mode,
            "llm_status": task.llm_status,
            "llm_model": task.llm_model,
            "llm_error": task.llm_error,
            "llm_repair_status": task.llm_repair_status,
            "llm_repair_attempts": task.llm_repair_attempts,
            "llm_validation_issues": task.llm_validation_issues,
            "project_summary": self._project_summary(fields),
            "original_requirement": task.user_input,
            "intent": task.intent,
            "confirmed_fields": confirmed,
            "assumptions": assumptions,
            "extra_fields": task.extra_fields,
            "fields": self._fields_json(task),
            "personas": insights.get("personas") if isinstance(insights.get("personas"), list) else self._personas(fields),
            "scenario_map": insights.get("scenario_map") if isinstance(insights.get("scenario_map"), list) else self._scenario_map(fields),
            "product_recommendations": self._report_product_recommendations(fields, insights),
            "constraints": insights.get("constraints") if isinstance(insights.get("constraints"), dict) else self._constraints(fields),
            "risk_notes": insights.get("risk_notes") if isinstance(insights.get("risk_notes"), list) else [],
            "trend_summary": insights.get("trend_summary") if isinstance(insights.get("trend_summary"), dict) else self._trend_summary(fields),
            "completeness_score": score,
            "weak_fields": weak_fields,
            "pending_questions": [question.to_dict() for question in task.questions],
            "recommended_action": self._next_action(task),
            "conversation_turns": task.conversation_turns,
            "latest_followup": task.conversation_turns[-1] if task.conversation_turns else None,
        }

    def _markdown_report(self, report: dict) -> str:
        lines = [
            f"# {report['project_summary']}",
            "",
            "## 1. 原始需求",
            report["original_requirement"],
            "",
            "## 2. 需求理解",
            f"- 主意图：{report['intent']['primary']}",
            f"- 完整度评分：{report['completeness_score']}",
            f"- 建议动作：{report['recommended_action']}",
            "",
            "## 3. 目标人群画像",
        ]
        for persona in report["personas"]:
            if not isinstance(persona, dict):
                persona = {"name": str(persona), "motivation": "待补充", "design_implication": "待补充"}
            lines.append(
                f"- {persona.get('name', '目标用户')}：{persona.get('motivation', '待补充')}；"
                f"设计启示：{persona.get('design_implication', '待补充')}"
            )
        lines.extend(["", "## 4. 场景拆解"])
        for scenario in report["scenario_map"]:
            if not isinstance(scenario, dict):
                scenario = {"scenario": str(scenario), "user_goal": "待补充", "product_requirements": ["待补充"]}
            requirements = scenario.get("product_requirements", [])
            if isinstance(requirements, list):
                requirements_text = "、".join(str(item) for item in requirements)
            else:
                requirements_text = str(requirements)
            lines.append(
                f"- {scenario.get('scenario', '使用场景')}：{scenario.get('user_goal', '待补充')}；"
                f"要求：{requirements_text}"
            )
        lines.extend(["", "## 5. 产品建议"])
        for item in report.get("product_recommendations", {}).get("recommended", []):
            lines.append(
                f"- {item.get('category', '产品品类')}（{item.get('role', '推荐产品')}）："
                f"{item.get('reason', '待补充')}"
            )
        lines.extend(["", "## 6. 风险提示"])
        if report["risk_notes"]:
            for note in report["risk_notes"]:
                lines.append(f"- {format_risk_note(note)}")
        else:
            lines.append("- 暂无。")
        lines.extend(["", "## 7. 约束与假设"])
        for key, value in report["assumptions"].items():
            lines.append(f"- {key}: {value}")
        if not report["assumptions"]:
            lines.append("- 暂无系统假设。")
        lines.extend(["", "## 8. 待确认问题"])
        if report["pending_questions"]:
            for question in report["pending_questions"]:
                lines.append(f"- {question['question']}")
        else:
            lines.append("- 暂无。")
        return "\n".join(lines)

    def _build_handoff_package(self, task: DemandTask, agent: str, clarification_status: str | None = None) -> dict:
        fields = self._field_values(task)
        report = task.report.structured_json if task.report else {}
        style = clean_handoff_values(fields.get("aesthetic_preferences", []))
        channels = clean_handoff_values(fields.get("channel_suggestions", DEFAULT_CHANNELS))
        target_users = clean_handoff_values(fields.get("target_users", []))
        usage_scenarios = self._handoff_usage_scenarios(task, fields, report)
        cultural_keywords = self._handoff_cultural_keywords(fields)
        emotional_keywords = clean_handoff_values(fields.get("emotional_keywords", []))
        functional_requirements = self._handoff_functional_requirements(fields, report)
        base = {
            "agent": agent,
            "source_report_id": task.report.report_id if task.report else None,
            "source_report_version": task.version,
            "clarification_status": clarification_status or task.status.value,
            "pending_questions": [question.to_dict() for question in task.questions],
            "handoff_warnings": self._handoff_warnings(task),
            "execution_brief": self._handoff_execution_brief(agent, fields, report),
            "success_criteria": self._handoff_success_criteria(agent),
            "field_sources": self._handoff_field_sources(task),
            "assumptions": self._handoff_assumptions(task, functional_requirements),
            "context": {
                "original_requirement": task.user_input,
                "project_summary": report.get("project_summary") or self._project_summary(fields),
                "time": clean_handoff_values(fields.get("time", [])),
                "location": clean_handoff_values(fields.get("location", [])),
                "brand_context": fields.get("brand_context"),
                "completeness_score": report.get("completeness_score", completeness_score(task.fields)),
            },
        }
        if agent == "cultural_ip_agent":
            base.update(
                {
                    "task": "生成文化IP方向",
                    "inputs": {
                        "target_users": target_users,
                        "usage_scenarios": usage_scenarios,
                        "cultural_keywords": cultural_keywords,
                        "emotional_keywords": emotional_keywords,
                        "risk_hints": self._handoff_risk_hints(report),
                    },
                    "constraints": {
                        "style": style,
                        "budget_range": fields.get("budget_range", DEFAULT_BUDGET),
                    },
                    "creative_brief": {
                        "core_theme": self._handoff_core_theme(fields, usage_scenarios),
                        "cultural_directions": cultural_keywords,
                        "emotional_tone": emotional_keywords + style,
                        "avoid_directions": self._handoff_risk_hints(report),
                        "symbol_translation": [
                            "将文化关键词转译为可用于纹样、故事和包装的符号体系",
                            "优先选择喜庆、成双、春日和江南意象",
                        ],
                    },
                    "expected_outputs": ["故事内核", "符号体系", "文化依据", "禁忌风险", "设计转译建议"],
                }
            )
        elif agent == "designer_agent":
            base.update(
                {
                    "task": "生成文化产品视觉方案",
                    "inputs": {
                        "product_categories": clean_handoff_values(fields.get("product_categories", [])),
                        "style": style,
                        "functional_requirements": functional_requirements,
                        "materials": clean_handoff_values(fields.get("materials", [])),
                    },
                    "constraints": {
                        "budget_range": fields.get("budget_range", DEFAULT_BUDGET),
                        "channels": channels,
                    },
                    "design_brief": {
                        "product_positioning": self._handoff_product_positioning(fields),
                        "materials": clean_handoff_values(fields.get("materials", [])),
                        "visual_elements": unique_values(cultural_keywords + emotional_keywords + style),
                        "functional_requirements": functional_requirements,
                        "deliverable_detail": ["纹样方向", "配色方案", "包装结构", "产品效果图"],
                        "craft_constraints": report.get("constraints", {}).get("production", {}),
                    },
                    "expected_outputs": ["纹样方案", "配色方案", "包装草图", "产品效果图"],
                }
            )
        elif agent == "marketer_agent":
            base.update(
                {
                    "task": "生成营销素材方向",
                    "inputs": {
                        "target_users": target_users,
                        "channels": channels,
                        "selling_points": self._handoff_selling_points(fields, report, cultural_keywords, emotional_keywords),
                        "usage_scenarios": usage_scenarios,
                    },
                    "constraints": {"tone": style},
                    "marketing_brief": {
                        "selling_points": self._handoff_selling_points(fields, report, cultural_keywords, emotional_keywords),
                        "channels": channels,
                        "content_angles": self._handoff_content_angles(usage_scenarios, cultural_keywords, emotional_keywords),
                        "copy_tone": unique_values(style + emotional_keywords),
                        "channel_focus": self._handoff_channel_focus(channels),
                    },
                    "expected_outputs": ["社媒文案", "详情页卖点", "短视频脚本方向"],
                }
            )
        else:
            base.update(
                {
                    "task": "通用需求交接",
                    "inputs": fields,
                    "constraints": {},
                    "expected_outputs": [],
                }
            )
        return base

    def _handoff_response_package(self, package: dict, include_brief_markdown: bool = False) -> dict:
        keys = [
            "agent",
            "source_report_id",
            "source_report_version",
            "clarification_status",
            "handoff_warnings",
            "task",
            "execution_brief",
            "inputs",
            "constraints",
            "expected_outputs",
            "brief_files",
        ]
        response = {key: package[key] for key in keys if key in package}
        if include_brief_markdown and "brief_markdown" in package:
            response["brief_markdown"] = package["brief_markdown"]
        return response

    def _handoff_usage_scenarios(self, task: DemandTask, fields: dict, report: dict) -> list[str]:
        scenarios = []
        for item in report.get("scenario_map", []):
            if isinstance(item, dict):
                scenarios.append(item.get("scenario"))
            else:
                scenarios.append(item)
        scenarios.extend(clean_handoff_values(fields.get("usage_scenarios", []), allow_single_char=False))
        if not scenarios:
            scenarios.extend(["礼赠场景"])
        return unique_values(scenarios)

    def _handoff_cultural_keywords(self, fields: dict) -> list[str]:
        values = []
        values.extend(clean_handoff_values(fields.get("cultural_preferences", []), allow_single_char=False))
        values.extend(clean_handoff_values(fields.get("location", []), allow_single_char=False))
        values.extend(clean_handoff_values(fields.get("materials", []), allow_single_char=False))
        if any("新婚" in item for item in clean_handoff_values(fields.get("target_users", []))):
            values.append("婚嫁")
        if any(item in {"西湖", "杭州", "南浔", "湖州", "江南"} for item in clean_handoff_values(fields.get("location", []))):
            values.append("江南")
        return unique_values(values)

    def _handoff_functional_requirements(self, fields: dict, report: dict) -> list[str]:
        confirmed = clean_handoff_values(fields.get("functional_requirements", []))
        if confirmed:
            return confirmed

        requirements = []
        for scenario in report.get("scenario_map", []):
            if not isinstance(scenario, dict):
                continue
            requirements.extend(clean_handoff_values(scenario.get("product_requirements", [])))
        if requirements:
            return unique_values(requirements)
        return list(DEFAULT_FUNCTIONAL_REQUIREMENTS)

    def _handoff_selling_points(
        self,
        fields: dict,
        report: dict,
        cultural_keywords: list[str],
        emotional_keywords: list[str],
    ) -> list[str]:
        points = []
        points.extend(emotional_keywords)
        points.extend(cultural_keywords)
        for scenario in self._handoff_usage_scenarios_from_report(report):
            if "春游" in scenario:
                points.append("春游纪念")
            if "婚" in scenario:
                points.append("新婚祝福")
        for item in report.get("product_recommendations", {}).get("recommended", []):
            if isinstance(item, dict):
                points.append(item.get("category"))
        if any("丝" in item for item in clean_handoff_values(fields.get("materials", [])) + cultural_keywords):
            points.append("丝绸质感")
        return unique_values(clean_handoff_values(points))

    def _handoff_usage_scenarios_from_report(self, report: dict) -> list[str]:
        scenarios = []
        for item in report.get("scenario_map", []):
            if isinstance(item, dict):
                scenarios.append(item.get("scenario"))
            else:
                scenarios.append(item)
        return clean_handoff_values(scenarios)

    def _handoff_risk_hints(self, report: dict) -> list[str]:
        hints = ["避免悲情爱情典故", "避免孤独或离别意象"]
        for note in report.get("risk_notes", []):
            hints.append(format_risk_note(note))
        return unique_values(clean_handoff_values(hints))

    def _handoff_warnings(self, task: DemandTask) -> list[str]:
        if task.status == TaskStatus.NEED_CLARIFICATION or task.questions:
            return ["仍有待确认问题，下游产出需按假设处理"]
        return []

    def _handoff_execution_brief(self, agent: str, fields: dict, report: dict) -> str:
        summary = report.get("project_summary") or self._project_summary(fields)
        if agent == "cultural_ip_agent":
            return f"围绕“{summary}”生成可支撑后续视觉和营销的文化IP方向，优先保证文化依据、喜庆语义和禁忌风险清晰。"
        if agent == "designer_agent":
            return f"围绕“{summary}”生成可落地的文化产品视觉方案，优先保证材质、功能、预算和渠道展示一致。"
        if agent == "marketer_agent":
            return f"围绕“{summary}”生成面向目标渠道的营销素材方向，优先保证卖点清楚、语气一致和场景可传播。"
        return f"围绕“{summary}”完成通用下游任务交接。"

    def _handoff_success_criteria(self, agent: str) -> list[str]:
        if agent == "cultural_ip_agent":
            return ["故事内核能解释文化关键词", "符号体系可被设计转译", "明确禁忌风险和规避建议"]
        if agent == "designer_agent":
            return ["视觉方案匹配目标人群和风格", "产品功能要求可落地", "输出覆盖纹样、配色、包装和效果图方向"]
        if agent == "marketer_agent":
            return ["卖点能对应目标人群和使用场景", "内容角度适配目标渠道", "文案语气与审美风格一致"]
        return ["输出内容与源需求一致", "明确输入、约束和交付物"]

    def _handoff_field_sources(self, task: DemandTask) -> dict[str, str]:
        return {name: field.source.value for name, field in task.fields.items()}

    def _handoff_assumptions(self, task: DemandTask, functional_requirements: list[str]) -> dict[str, Any]:
        assumptions = {}
        if task.report:
            assumptions.update(task.report.structured_json.get("assumptions", {}))
        if "functional_requirements" not in task.fields:
            assumptions["functional_requirements"] = functional_requirements
        return assumptions

    def _handoff_core_theme(self, fields: dict, usage_scenarios: list[str]) -> str:
        user = first(clean_handoff_values(fields.get("target_users", [])), "目标人群")
        location = first(clean_handoff_values(fields.get("location", [])), "")
        scenario = first(usage_scenarios, "礼赠场景")
        location_part = "" if location and location in scenario else location
        return "".join(part for part in [location_part, scenario, user, "祝福"] if part)

    def _handoff_product_positioning(self, fields: dict) -> str:
        category = first(clean_handoff_values(fields.get("product_categories", [])), "文化产品")
        budget = fields.get("budget_range", DEFAULT_BUDGET)
        return f"{budget}价位的{category}"

    def _handoff_content_angles(
        self,
        usage_scenarios: list[str],
        cultural_keywords: list[str],
        emotional_keywords: list[str],
    ) -> list[str]:
        angles = []
        for scenario in usage_scenarios:
            angles.append(f"{scenario}内容角度")
        angles.extend(f"{keyword}文化记忆点" for keyword in cultural_keywords[:3])
        angles.extend(f"{keyword}情绪卖点" for keyword in emotional_keywords[:3])
        return unique_values(clean_handoff_values(angles))

    def _handoff_channel_focus(self, channels: list[str]) -> dict[str, list[str]]:
        focus = {}
        for channel in channels:
            if "小红书" in channel:
                focus[channel] = ["种草标题", "场景化图片", "礼赠理由"]
            elif "线下" in channel or "文旅" in channel:
                focus[channel] = ["陈列卖点", "包装识别", "导购话术"]
            else:
                focus[channel] = ["核心卖点", "转化文案", "渠道适配素材"]
        return focus

    def _handoff_markdown(self, package: dict) -> str:
        lines = [
            f"# {package.get('agent', 'agent')} 任务 Brief",
            "",
            f"## 任务目标",
            package.get("execution_brief", ""),
            "",
            "## 原始需求",
            package.get("context", {}).get("original_requirement", ""),
            "",
            "## 输入信息",
            "```json",
            json.dumps(package.get("inputs", {}), ensure_ascii=False, indent=2),
            "```",
            "",
            "## 约束",
            "```json",
            json.dumps(package.get("constraints", {}), ensure_ascii=False, indent=2),
            "```",
            "",
            "## 验收标准",
        ]
        lines.extend(f"- {item}" for item in package.get("success_criteria", []))
        lines.extend(["", "## 待确认问题"])
        pending = package.get("pending_questions", [])
        if pending:
            lines.extend(f"- {item.get('question', '')}" for item in pending)
        else:
            lines.append("- 暂无。")
        lines.extend(["", "## 字段来源", "```json"])
        lines.append(json.dumps(package.get("field_sources", {}), ensure_ascii=False, indent=2))
        lines.extend(["```", "", "## 系统假设", "```json"])
        lines.append(json.dumps(package.get("assumptions", {}), ensure_ascii=False, indent=2))
        lines.extend(["```", "", "## 专属 Brief", "```json"])
        dedicated = {
            key: package[key]
            for key in ("creative_brief", "design_brief", "marketing_brief")
            if key in package
        }
        lines.append(json.dumps(dedicated, ensure_ascii=False, indent=2))
        lines.append("```")
        return "\n".join(lines)

    def _risk_questions(self, task: DemandTask) -> list[ClarifyingQuestion]:
        questions: list[ClarifyingQuestion] = []
        risk_notes = task.report_insights.get("risk_notes") if isinstance(task.report_insights, dict) else None
        if not isinstance(risk_notes, list):
            return questions

        for note in risk_notes:
            if not is_confirmable_risk(note):
                continue
            field_name = risk_question_field(note)
            current = task.fields.get(field_name)
            if current and current.source in {FieldSource.USER_CONFIRMED, FieldSource.CONTEXT}:
                continue
            questions.append(risk_question_for(note, field_name))
            if len(questions) == 3:
                break
        return questions

    def _merge_risk_questions(
        self,
        current_questions: list[ClarifyingQuestion],
        risk_questions: list[ClarifyingQuestion],
    ) -> list[ClarifyingQuestion]:
        merged: list[ClarifyingQuestion] = []
        seen: set[str] = set()
        for question in current_questions + risk_questions:
            key = question.field
            if key in seen:
                continue
            seen.add(key)
            merged.append(question)
            if len(merged) == 3:
                break
        return merged

    def _followup_analysis_input(self, message: str, answers: dict[str, Any]) -> str:
        parts = []
        if message:
            parts.append(f"用户补充需求：{message}")
        if answers:
            parts.append(f"用户回答追问：{json.dumps(answers, ensure_ascii=False)}")
        return "\n".join(parts)

    def _merge_message_fields(self, task: DemandTask, message: str) -> None:
        extracted = extract_fields(message, task.context)
        for name, field in extracted.items():
            if field.source == FieldSource.CONTEXT and name in task.fields:
                continue
            if field.source == FieldSource.EXPLICIT:
                field.source = FieldSource.USER_CONFIRMED
                field.confidence = max(field.confidence, 0.92)
            current = task.fields.get(name)
            if current is None or current.source not in {FieldSource.USER_CONFIRMED, FieldSource.CONTEXT}:
                task.fields[name] = field

    def _changed_fields(self, before_fields: dict[str, dict], task: DemandTask) -> list[dict]:
        changed = []
        after_fields = self._fields_json(task)
        for name, field in after_fields.items():
            if stable_json(before_fields.get(name)) != stable_json(field):
                changed.append(field)
        return changed

    def _field_values(self, task: DemandTask) -> dict:
        return {name: field.field_value for name, field in task.fields.items()}

    def _fields_json(self, task: DemandTask) -> dict:
        return {name: field.to_dict() for name, field in task.fields.items()}

    def _project_summary(self, fields: dict) -> str:
        location = first(fields.get("location"), "")
        style = first(fields.get("aesthetic_preferences"), "")
        category = first(fields.get("product_categories"), "文化产品")
        user = first(fields.get("target_users"), "目标人群")
        parts = [location, style, user, category]
        return "".join(part for part in parts if part) or "文化产品需求分析"

    def _personas(self, fields: dict) -> list[dict]:
        users = fields.get("target_users", [])
        primary = first(users, "核心目标用户")
        if "新婚" in primary:
            return [
                {
                    "name": "重视仪式感的新婚人群",
                    "role": "购买者/使用者",
                    "motivation": "纪念婚礼、旅行与重要人生节点",
                    "pain_points": ["礼品同质化", "传统婚庆风格容易显土", "担心不实用"],
                    "design_implication": "强调浪漫、吉祥、拍照效果和长期保存价值",
                },
                {
                    "name": "婚礼宾客与亲友",
                    "role": "接受礼物者",
                    "motivation": "收到体面、有故事感的祝福礼物",
                    "pain_points": ["普通伴手礼缺少记忆点"],
                    "design_implication": "包装要有仪式感，产品应轻便且适合收藏",
                },
            ]
        return [
            {
                "name": primary,
                "role": "核心购买/使用人群",
                "motivation": "获得符合场景、审美和预算的文化产品",
                "pain_points": ["选择成本高", "担心产品缺少文化故事", "担心实用性不足"],
                "design_implication": "优先保证清晰用途、稳定品质和可传播卖点",
            },
            {
                "name": "潜在扩展人群",
                "role": "被传播或被赠送对象",
                "motivation": "被产品故事、包装和场景价值打动",
                "pain_points": ["缺少一眼可懂的价值表达"],
                "design_implication": "增强包装识别和一句话卖点",
            },
        ]

    def _scenario_map(self, fields: dict) -> list[dict]:
        scenarios = fields.get("usage_scenarios") or ["礼赠场景", "购买场景", "传播场景"]
        result = []
        for scenario in scenarios[:4]:
            result.append(
                {
                    "scenario": scenario,
                    "user_goal": scenario_goal(scenario),
                    "product_requirements": scenario_product_requirements(scenario),
                    "design_requirements": scenario_design_requirements(scenario, fields),
                }
            )
        if not any("购买" in item["scenario"] or "零售" in item["scenario"] for item in result):
            result.append(
                {
                    "scenario": "渠道购买",
                    "user_goal": "让用户在目标渠道快速理解并购买",
                    "product_requirements": ["价格带清晰", "卖点明确", "包装便于展示"],
                    "design_requirements": ["主视觉一眼识别", "适配详情页和社媒封面"],
                }
            )
        return result

    def _product_recommendations(self, fields: dict) -> dict:
        categories = fields.get("product_categories") or ["丝巾", "香囊", "礼盒"]
        recommended = []
        for index, category in enumerate(categories[:4]):
            recommended.append(
                {
                    "category": category,
                    "role": "主产品" if index == 0 else "辅助产品",
                    "reason": product_reason(category),
                    "functional_constraints": product_constraints(category),
                }
            )
        return {
            "recommended": recommended,
            "not_recommended": [
                {
                    "category": "大幅丝绸画",
                    "reason": "对大众伴手礼和移动场景不够轻便，预算门槛较高",
                }
            ],
        }

    def _report_product_recommendations(self, fields: dict, insights: dict) -> dict:
        recommendations = insights.get("product_recommendations")
        if isinstance(recommendations, dict) and isinstance(recommendations.get("recommended"), list):
            return recommendations
        return self._product_recommendations(fields)

    def _constraints(self, fields: dict) -> dict:
        budget = fields.get("budget_range", DEFAULT_BUDGET)
        channels = fields.get("channel_suggestions", DEFAULT_CHANNELS)
        return {
            "budget": budget,
            "production": {
                "recommended_craft": ["数码印花", "局部烫金"],
                "avoid_craft": ["大面积手工刺绣"],
                "reason": "MVP 默认控制在可批量生产且质感稳定的工艺范围",
            },
            "channels": channels,
        }

    def _trend_summary(self, fields: dict) -> dict:
        return {
            "data_sources": ["内部规则样本"],
            "time_range": "MVP静态规则",
            "hot_keywords": clean_handoff_values(fields.get("aesthetic_preferences", []))
            + clean_handoff_values(fields.get("emotional_keywords", [])),
            "suggestion": "实时趋势数据暂未接入，当前结论基于需求字段和内置行业规则推断。",
        }

    def _next_action(self, task: DemandTask) -> str:
        score = completeness_score(task.fields)
        if any(question.field in RISK_QUESTION_FIELDS for question in task.questions):
            return "先确认风险问题后再进入下游交接"
        if score < 60:
            return "继续澄清关键缺失信息"
        if score < 80:
            return "可推进下一阶段，但需在报告中标注假设并等待用户确认"
        return "可进入文化IP生成、设计方案生成和下游任务交接"


def first(value, default=None):
    if isinstance(value, list):
        return value[0] if value else default
    return value if value is not None else default


def stable_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def as_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def clean_handoff_values(value: Any, allow_single_char: bool = False) -> list[str]:
    cleaned = []
    for item in as_list(value):
        if isinstance(item, dict):
            item = item.get("name") or item.get("category") or item.get("scenario") or item.get("value")
        text = str(item).strip()
        if not text or text in HANDOFF_STOPWORDS:
            continue
        if len(text) == 1 and not allow_single_char:
            continue
        cleaned.append(text)
    return unique_values(cleaned)


def unique_values(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def is_confirmable_risk(note: Any) -> bool:
    text = risk_text(note)
    if isinstance(note, dict):
        severity = str(note.get("severity", "")).lower()
        if severity in {"medium", "high"}:
            return True
    return any(keyword in text for keyword in RISK_CONFIRMATION_KEYWORDS)


def risk_question_field(note: Any) -> str:
    text = risk_text(note)
    if "实用" in text or "功能" in text or "便携" in text or "多功能" in text:
        return "functional_requirements"
    return "risk_clarifications"


def risk_question_for(note: Any, field_name: str) -> ClarifyingQuestion:
    text = risk_text(note)
    if field_name == "functional_requirements":
        return ClarifyingQuestion(
            field=field_name,
            question="你希望这套伴手礼的实用性主要体现在哪些方面？",
            options=["日常佩戴/使用", "便携易带", "多功能组合", "收藏纪念"],
        )
    return ClarifyingQuestion(
        field=field_name,
        question=f"关于“{short_text(text)}”，你希望优先按哪种方式处理？",
        options=["进一步明确需求", "按系统建议处理", "暂不处理"],
    )


def risk_text(note: Any) -> str:
    if isinstance(note, dict):
        parts = [note.get("risk"), note.get("mitigation"), note.get("severity")]
        return "；".join(str(part) for part in parts if part)
    return str(note)


def format_risk_note(note: Any) -> str:
    if not isinstance(note, dict):
        return str(note)
    parts = []
    if note.get("risk"):
        parts.append(f"风险：{note['risk']}")
    if note.get("severity"):
        parts.append(f"等级：{note['severity']}")
    if note.get("mitigation"):
        parts.append(f"建议：{note['mitigation']}")
    return "；".join(parts) if parts else str(note)


def short_text(text: str, limit: int = 24) -> str:
    return text if len(text) <= limit else f"{text[:limit]}..."


def scenario_goal(scenario: str) -> str:
    if "婚" in scenario:
        return "表达祝福、体面感和仪式感"
    if "春游" in scenario or "旅" in scenario:
        return "绑定旅行记忆并适合拍照分享"
    if "零售" in scenario or "购买" in scenario:
        return "降低理解成本并促进即时购买"
    if "商务" in scenario:
        return "传递品牌背书和高端礼赠价值"
    return "满足实际使用和传播需要"


def scenario_product_requirements(scenario: str) -> list[str]:
    if "婚" in scenario:
        return ["寓意吉祥", "包装有仪式感", "适合批量采购"]
    if "春游" in scenario or "旅" in scenario:
        return ["轻便", "适合拍照", "易携带"]
    if "商务" in scenario:
        return ["质感稳定", "品牌露出克制", "礼盒体面"]
    return ["卖点清晰", "价格带明确", "便于展示"]


def scenario_design_requirements(scenario: str, fields: dict) -> list[str]:
    base = fields.get("aesthetic_preferences", ["风格清晰"])
    if isinstance(base, str):
        base = [base]
    return base[:2] + ["文化符号不过度堆砌"]


def product_reason(category: str) -> str:
    if "丝巾" in category or "方巾" in category:
        return "适合承载纹样，兼具穿搭、礼赠和纪念价值"
    if "香囊" in category:
        return "小巧且寓意明确，适合作为伴手礼组合"
    if "礼盒" in category:
        return "提升价值感，便于礼赠和线下陈列"
    if "团扇" in category:
        return "仪式感强，适合国风拍照和婚庆场景"
    return "符合文创礼品的基础承载方式，可继续细化"


def product_constraints(category: str) -> list[str]:
    if "丝巾" in category or "方巾" in category:
        return ["亲肤", "边缘工艺精致", "图案适配方形或长条构图"]
    if "香囊" in category:
        return ["香味不刺激", "图案面积有限", "适合批量生产"]
    if "礼盒" in category:
        return ["物流体积可控", "开盒体验明确", "包装成本需匹配预算"]
    return ["功能清晰", "工艺可落地", "渠道展示友好"]

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from .llm_analyzer import LLMAnalyzer
from .models import DemandReport, DemandTask, FieldSource, TaskStatus
from .rules import (
    DEFAULT_BUDGET,
    DEFAULT_CHANNELS,
    apply_answer,
    build_questions,
    completeness_score,
)
from .storage import DemandStorage


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
        task = self.get_task(demand_task_id)
        changed = []
        for field_name, value in answers.items():
            field = apply_answer(task.fields, field_name, value, task.version)
            changed.append(field.to_dict())
        task.record("answers_submitted", {"answers": answers, "changed_fields": changed})
        self._score_and_finish(task)
        task.touch()
        self._persist(task)
        return {
            "demand_task_id": task.demand_task_id,
            "status": task.status.value,
            "changed_fields": changed,
            "next_action": self._next_action(task),
            "completeness_score": completeness_score(task.fields),
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

    def handoff(self, demand_task_id: str, target_agents: list[str]) -> dict:
        task = self.get_task(demand_task_id)
        if task.report is None:
            self._generate_report(task)
        task.status = TaskStatus.HANDOFF
        packages = [self._build_handoff_package(task, agent) for agent in target_agents]
        task.record("handoff_generated", {"target_agents": target_agents})
        task.touch()
        self._persist(task)
        return {
            "demand_task_id": task.demand_task_id,
            "status": task.status.value,
            "task_packages": packages,
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
        task.report_insights = result.report_insights
        task.extra_fields = result.extra_fields
        task.record(
            "parsed",
            {
                "analysis_mode": task.analysis_mode,
                "llm_status": task.llm_status,
                "llm_model": task.llm_model,
                "llm_error": task.llm_error,
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
        has_assumption = any(field.source == FieldSource.ASSUMPTION for field in task.fields.values())
        if score < 60 and task.questions:
            task.status = TaskStatus.NEED_CLARIFICATION
            task.report = None
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
            lines.append(
                f"- {persona.get('name', '目标用户')}：{persona.get('motivation', '待补充')}；"
                f"设计启示：{persona.get('design_implication', '待补充')}"
            )
        lines.extend(["", "## 4. 场景拆解"])
        for scenario in report["scenario_map"]:
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
                lines.append(f"- {note}")
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

    def _build_handoff_package(self, task: DemandTask, agent: str) -> dict:
        fields = self._field_values(task)
        base = {
            "agent": agent,
            "source_report_id": task.report.report_id if task.report else None,
            "source_report_version": task.version,
        }
        if agent == "cultural_ip_agent":
            base.update(
                {
                    "task": "生成文化IP方向",
                    "inputs": {
                        "target_users": fields.get("target_users", []),
                        "usage_scenarios": fields.get("usage_scenarios", []),
                        "cultural_keywords": fields.get("cultural_preferences", []),
                        "emotional_keywords": fields.get("emotional_keywords", []),
                        "risk_hints": ["避免悲情爱情典故", "避免孤独或离别意象"],
                    },
                    "constraints": {
                        "style": fields.get("aesthetic_preferences", []),
                        "budget_range": fields.get("budget_range", DEFAULT_BUDGET),
                    },
                    "expected_outputs": ["故事内核", "符号体系", "文化依据", "禁忌风险", "设计转译建议"],
                }
            )
        elif agent == "designer_agent":
            base.update(
                {
                    "task": "生成文化产品视觉方案",
                    "inputs": {
                        "product_categories": fields.get("product_categories", []),
                        "style": fields.get("aesthetic_preferences", []),
                        "functional_requirements": ["便携", "适合展示", "易保存"],
                        "materials": fields.get("materials", []),
                    },
                    "constraints": {
                        "budget_range": fields.get("budget_range", DEFAULT_BUDGET),
                        "channels": fields.get("channel_suggestions", DEFAULT_CHANNELS),
                    },
                    "expected_outputs": ["纹样方案", "配色方案", "包装草图", "产品效果图"],
                }
            )
        elif agent == "marketer_agent":
            base.update(
                {
                    "task": "生成营销素材方向",
                    "inputs": {
                        "target_users": fields.get("target_users", []),
                        "channels": fields.get("channel_suggestions", DEFAULT_CHANNELS),
                        "selling_points": fields.get("emotional_keywords", []) + fields.get("cultural_preferences", []),
                        "usage_scenarios": fields.get("usage_scenarios", []),
                    },
                    "constraints": {"tone": fields.get("aesthetic_preferences", [])},
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
            "hot_keywords": fields.get("aesthetic_preferences", []) + fields.get("emotional_keywords", []),
            "suggestion": "实时趋势数据暂未接入，当前结论基于需求字段和内置行业规则推断。",
        }

    def _next_action(self, task: DemandTask) -> str:
        score = completeness_score(task.fields)
        if score < 60:
            return "继续澄清关键缺失信息"
        if score < 80:
            return "可推进下一阶段，但需在报告中标注假设并等待用户确认"
        return "可进入文化IP生成、设计方案生成和下游任务交接"


def first(value, default=None):
    if isinstance(value, list):
        return value[0] if value else default
    return value if value is not None else default


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

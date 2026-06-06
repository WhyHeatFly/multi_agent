from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .llm_client import DeepSeekClient, LLMClientResult, load_dotenv
from .models import ClarifyingQuestion, DemandField, FieldSource
from .rules import FIELD_LABELS, apply_inferences, build_questions, extract_fields, parse_intent, unique


ALLOWED_FIELD_NAMES = set(FIELD_LABELS) | {"brand_context"}
ALLOWED_SOURCES = {
    "explicit": FieldSource.EXPLICIT,
    "context": FieldSource.CONTEXT,
    "inferred": FieldSource.INFERRED,
    "assumption": FieldSource.ASSUMPTION,
}
PRIORITY_SOURCES = {FieldSource.EXPLICIT, FieldSource.CONTEXT, FieldSource.USER_CONFIRMED}


@dataclass
class SemanticAnalysisResult:
    status: str
    analysis_mode: str
    model: str | None = None
    error: str | None = None
    intent: dict[str, Any] = field(default_factory=dict)
    fields: dict[str, DemandField] = field(default_factory=dict)
    questions: list[ClarifyingQuestion] = field(default_factory=list)
    report_insights: dict[str, Any] = field(default_factory=dict)
    extra_fields: dict[str, Any] = field(default_factory=dict)
    llm_repair_status: str = "not_needed"
    llm_repair_attempts: int = 0
    llm_validation_issues: list[dict[str, Any]] = field(default_factory=list)


class LLMAnalyzer:
    def __init__(
        self,
        client: DeepSeekClient | None = None,
        repair_enabled: bool | None = None,
        max_repair_attempts: int | None = None,
    ) -> None:
        self.client = client or DeepSeekClient.from_env()
        self.repair_enabled = env_bool("DEMAND_LLM_REPAIR_ENABLED", True) if repair_enabled is None else repair_enabled
        self.max_repair_attempts = (
            env_int("DEMAND_LLM_MAX_REPAIR_ATTEMPTS", 1) if max_repair_attempts is None else max_repair_attempts
        )

    def analyze(
        self,
        user_input: str,
        context: dict[str, Any] | None = None,
        version: str = "v1.0",
        existing_fields: dict[str, DemandField] | None = None,
        conversation_turns: list[dict[str, Any]] | None = None,
        current_questions: list[ClarifyingQuestion] | None = None,
    ) -> SemanticAnalysisResult:
        context = context or {}
        rule_intent = parse_intent(user_input)
        rule_fields = self._merge_fields(existing_fields or {}, extract_fields(user_input, context))
        rule_questions = build_questions(rule_fields)

        existing_fields = existing_fields or {}
        conversation_turns = conversation_turns or []
        current_questions = current_questions or []
        messages = self._messages(user_input, context, existing_fields, conversation_turns, current_questions)
        response = self.client.complete_json(messages)
        if response.status != "success" or response.content is None:
            repaired = self._attempt_repair(
                user_input,
                context,
                existing_fields,
                conversation_turns,
                current_questions,
                response,
                [{"path": "$", "code": response.status, "message": response.error or "LLM did not return valid JSON", "severity": "error"}],
            )
            if repaired:
                return self._result_from_content(repaired, rule_intent, rule_fields, version)
            return SemanticAnalysisResult(
                status=response.status,
                analysis_mode="rules_fallback",
                model=response.model,
                error=response.error,
                intent=rule_intent,
                fields=rule_fields,
                questions=rule_questions,
                llm_repair_status=self._repair_status_for_failed_response(response),
                llm_repair_attempts=0,
                llm_validation_issues=[
                    {
                        "path": "$",
                        "code": response.status,
                        "message": response.error or "LLM did not return valid JSON",
                        "severity": "error",
                    }
                ],
            )

        try:
            issues = self._validate_content(response.content)
            if issues:
                repaired = self._attempt_repair(
                    user_input,
                    context,
                    existing_fields,
                    conversation_turns,
                    current_questions,
                    response,
                    issues,
                )
                if repaired:
                    return self._result_from_content(repaired, rule_intent, rule_fields, version)
                if has_blocking_issues(issues):
                    return SemanticAnalysisResult(
                        status="repair_failed" if self.repair_enabled else "invalid_json",
                        analysis_mode="rules_fallback",
                        model=response.model,
                        error=summarize_issues(issues),
                        intent=rule_intent,
                        fields=rule_fields,
                        questions=rule_questions,
                        llm_repair_status="failed" if self.repair_enabled and self.max_repair_attempts > 0 else "disabled",
                        llm_repair_attempts=1 if self.repair_enabled and self.max_repair_attempts > 0 else 0,
                        llm_validation_issues=issues,
                    )
            return self._result_from_content(response, rule_intent, rule_fields, version, validation_issues=issues)
        except (TypeError, ValueError) as exc:
            issues = [{"path": "$", "code": "cleaning_error", "message": str(exc)[:500], "severity": "error"}]
            repaired = self._attempt_repair(
                user_input,
                context,
                existing_fields,
                conversation_turns,
                current_questions,
                response,
                issues,
            )
            if repaired:
                return self._result_from_content(repaired, rule_intent, rule_fields, version)
            return SemanticAnalysisResult(
                status="invalid_json",
                analysis_mode="rules_fallback",
                model=response.model,
                error=str(exc)[:500],
                intent=rule_intent,
                fields=rule_fields,
                questions=rule_questions,
                llm_repair_status="failed" if self.repair_enabled else "disabled",
                llm_repair_attempts=1 if self.repair_enabled and self.max_repair_attempts > 0 else 0,
                llm_validation_issues=issues,
            )

    def _result_from_content(
        self,
        response: LLMClientResult,
        rule_intent: dict[str, Any],
        rule_fields: dict[str, DemandField],
        version: str,
        validation_issues: list[dict[str, Any]] | None = None,
    ) -> SemanticAnalysisResult:
        if response.content is None:
            raise ValueError("empty repaired content")
        intent = self._clean_intent(response.content.get("intent"), rule_intent)
        llm_fields, extra_fields = self._clean_fields(response.content.get("fields"), version)
        top_level_extra = response.content.get("extra_fields")
        if isinstance(top_level_extra, dict):
            extra_fields.update(top_level_extra)
        fields = self._merge_fields(rule_fields, llm_fields)
        apply_inferences(fields)
        questions = self._clean_questions(response.content.get("questions"))
        if not questions:
            questions = build_questions(fields)
        report_insights = self._clean_report_insights(response.content.get("report_insights"))
        was_repaired = response.status == "success_repaired"
        return SemanticAnalysisResult(
            status=response.status,
            analysis_mode="llm_enhanced",
            model=response.model,
            intent=intent,
            fields=fields,
            questions=questions[:3],
            report_insights=report_insights,
            extra_fields=extra_fields,
            llm_repair_status="success" if was_repaired else "not_needed",
            llm_repair_attempts=1 if was_repaired else 0,
            llm_validation_issues=validation_issues or [],
        )

    def _validate_content(self, content: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for key in ("intent", "fields", "questions", "report_insights"):
            if key not in content:
                issues.append(issue(f"$.{key}", "missing_required_key", f"缺少顶层字段 {key}"))
        if not isinstance(content.get("intent"), dict):
            issues.append(issue("$.intent", "invalid_type", "intent 必须是 object"))
        if not isinstance(content.get("fields"), dict):
            issues.append(issue("$.fields", "invalid_type", "fields 必须是 object"))
        if not isinstance(content.get("questions"), list):
            issues.append(issue("$.questions", "invalid_type", "questions 必须是 array"))
        if not isinstance(content.get("report_insights"), dict):
            issues.append(issue("$.report_insights", "invalid_type", "report_insights 必须是 object"))

        fields = content.get("fields")
        if isinstance(fields, dict):
            for name, payload in fields.items():
                if name not in ALLOWED_FIELD_NAMES:
                    issues.append(issue(f"$.fields.{name}", "unknown_field", "未知字段应放入 extra_fields", "warning"))
                    continue
                if not isinstance(payload, dict):
                    issues.append(issue(f"$.fields.{name}", "invalid_type", "字段值必须是 object"))
                    continue
                if str(payload.get("source", "inferred")) not in ALLOWED_SOURCES:
                    issues.append(issue(f"$.fields.{name}.source", "invalid_source", "字段 source 不在允许范围内", "warning"))
                try:
                    confidence = float(payload.get("confidence", 0.72))
                    if confidence < 0 or confidence > 1:
                        issues.append(issue(f"$.fields.{name}.confidence", "confidence_out_of_range", "置信度需在 0-1 内", "warning"))
                except (TypeError, ValueError):
                    issues.append(issue(f"$.fields.{name}.confidence", "invalid_confidence", "置信度必须是数字", "warning"))

        questions = content.get("questions")
        if isinstance(questions, list):
            if len(questions) > 3:
                issues.append(issue("$.questions", "too_many_questions", "追问最多 3 个", "warning"))
            for index, item in enumerate(questions):
                if not isinstance(item, dict):
                    issues.append(issue(f"$.questions[{index}]", "invalid_type", "追问必须是 object"))
                    continue
                if item.get("field") not in ALLOWED_FIELD_NAMES:
                    issues.append(issue(f"$.questions[{index}].field", "unknown_field", "追问字段不在白名单内", "warning"))
                if not item.get("question") or not isinstance(item.get("options"), list):
                    issues.append(issue(f"$.questions[{index}]", "invalid_question", "追问缺少 question 或 options"))

        insights = content.get("report_insights")
        if isinstance(insights, dict):
            personas = insights.get("personas")
            scenarios = insights.get("scenario_map")
            recommendations = insights.get("product_recommendations")
            if not isinstance(personas, list) or len(personas) == 0:
                issues.append(issue("$.report_insights.personas", "missing_personas", "缺少目标人群画像"))
            if not isinstance(scenarios, list) or len(scenarios) == 0:
                issues.append(issue("$.report_insights.scenario_map", "missing_scenarios", "缺少场景拆解"))
            if not isinstance(recommendations, dict) or not recommendations.get("recommended"):
                issues.append(issue("$.report_insights.product_recommendations", "missing_products", "缺少产品建议"))
            if too_many_placeholders(insights):
                issues.append(issue("$.report_insights", "low_quality_placeholders", "报告洞察中待补充内容过多"))
        return issues

    def _attempt_repair(
        self,
        user_input: str,
        context: dict[str, Any],
        existing_fields: dict[str, DemandField],
        conversation_turns: list[dict[str, Any]],
        current_questions: list[ClarifyingQuestion],
        response: LLMClientResult,
        issues: list[dict[str, Any]],
    ) -> LLMClientResult | None:
        if not self.repair_enabled or self.max_repair_attempts <= 0:
            return None
        if response.status not in {"success", "invalid_json"}:
            return None

        source_payload = response.content if response.content is not None else response.raw_text
        repair_response: LLMClientResult | None = None
        for _ in range(self.max_repair_attempts):
            repair_response = self.client.complete_json(
                self._repair_messages(user_input, context, existing_fields, conversation_turns, current_questions, source_payload, issues),
                max_tokens=3000,
            )
            if repair_response.status != "success" or repair_response.content is None:
                source_payload = repair_response.raw_text or repair_response.error or source_payload
                continue
            repaired_issues = self._validate_content(repair_response.content)
            blocking = [item for item in repaired_issues if item.get("severity") == "error"]
            if not blocking:
                repair_response.status = "success_repaired"
                return repair_response
            issues = repaired_issues
            source_payload = repair_response.content
        return None

    def _repair_status_for_failed_response(self, response: LLMClientResult) -> str:
        if not self.repair_enabled:
            return "disabled"
        if response.status == "invalid_json" and self.max_repair_attempts > 0:
            return "failed"
        return "not_needed"

    def _messages(
        self,
        user_input: str,
        context: dict[str, Any],
        existing_fields: dict[str, DemandField],
        conversation_turns: list[dict[str, Any]],
        current_questions: list[ClarifyingQuestion],
    ) -> list[dict[str, str]]:
        field_schema = {name: label for name, label in FIELD_LABELS.items()}
        field_schema["brand_context"] = "品牌上下文"
        system_prompt = (
            "你是文化产品需求分析师 Agent。请输出严格 json，不要输出 markdown。\n"
            "你的任务是从中文自然语言需求中识别意图、标准字段、澄清问题和报告洞察。\n"
            "如果提供了 existing_fields 和 conversation_turns，请在已有字段基础上吸收新增需求，不要丢失已确认字段。\n"
            "fields 只能使用给定字段名；不确定但有价值的新字段放入 extra_fields。\n"
            "字段 source 只能是 explicit、context、inferred、assumption。\n"
            "不要编造实时趋势数据；未接入外部数据时标注为 LLM语义推断。\n"
            "必须返回 json object，结构包含 intent、fields、questions、report_insights、extra_fields。"
        )
        user_payload = {
            "user_input": user_input,
            "context": context,
            "existing_fields": {name: field.to_dict() for name, field in existing_fields.items()},
            "conversation_turns": conversation_turns[-6:],
            "current_questions": [question.to_dict() for question in current_questions],
            "allowed_fields": field_schema,
            "example_json": {
                "intent": {"primary": "新品设计", "secondary": ["礼品定制"], "confidence": 0.9},
                "fields": {
                    "target_users": {
                        "value": ["新婚人群"],
                        "source": "explicit",
                        "confidence": 0.9,
                        "evidence": "新婚人群",
                    }
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
                    "personas": [],
                    "scenario_map": [],
                    "product_recommendations": {},
                    "constraints": {},
                    "risk_notes": [],
                    "trend_summary": {
                        "data_sources": ["LLM语义推断"],
                        "suggestion": "未接入实时趋势数据",
                    },
                },
                "extra_fields": {},
            },
        }
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]

    def _repair_messages(
        self,
        user_input: str,
        context: dict[str, Any],
        existing_fields: dict[str, DemandField],
        conversation_turns: list[dict[str, Any]],
        current_questions: list[ClarifyingQuestion],
        broken_output: Any,
        issues: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        field_schema = {name: label for name, label in FIELD_LABELS.items()}
        field_schema["brand_context"] = "品牌上下文"
        system_prompt = (
            "你是文化产品需求分析师 Agent 的 JSON 修复器。请只返回严格 json object，不要输出 markdown 或解释。\n"
            "你需要根据原始需求、上下文、已有字段和校验问题，修复上一轮 LLM 输出。\n"
            "必须保留用户明确表达、context 和 existing_fields 中已确认的信息，不要丢失历史补充。\n"
            "fields 只能使用 allowed_fields；新字段放入 extra_fields。\n"
            "report_insights 必须提供可直接用于报告的画像、场景、产品建议、约束、风险和趋势摘要，避免大量“待补充”。\n"
            "趋势数据只能标注为 LLM语义推断，不要编造实时数据。"
        )
        repair_payload = {
            "user_input": user_input,
            "context": context,
            "existing_fields": {name: field.to_dict() for name, field in existing_fields.items()},
            "conversation_turns": conversation_turns[-6:],
            "current_questions": [question.to_dict() for question in current_questions],
            "allowed_fields": field_schema,
            "validation_issues": issues,
            "broken_output": broken_output,
            "required_json_contract": {
                "intent": {"primary": "新品设计", "secondary": [], "confidence": 0.0},
                "fields": {},
                "questions": [],
                "report_insights": {
                    "personas": [
                        {
                            "name": "人群名称",
                            "role": "购买者/使用者",
                            "motivation": "具体动机",
                            "pain_points": ["具体顾虑"],
                            "design_implication": "设计启示",
                        }
                    ],
                    "scenario_map": [
                        {
                            "scenario": "场景名称",
                            "user_goal": "用户目标",
                            "product_requirements": ["产品要求"],
                            "design_requirements": ["设计要求"],
                        }
                    ],
                    "product_recommendations": {
                        "recommended": [
                            {
                                "category": "产品品类",
                                "role": "主产品",
                                "reason": "推荐理由",
                                "functional_constraints": ["落地约束"],
                            }
                        ],
                        "not_recommended": [],
                    },
                    "constraints": {},
                    "risk_notes": [],
                    "trend_summary": {
                        "data_sources": ["LLM语义推断"],
                        "suggestion": "未接入实时趋势数据",
                    },
                },
                "extra_fields": {},
            },
        }
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(repair_payload, ensure_ascii=False)},
        ]

    def _clean_intent(self, value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            return fallback
        primary = value.get("primary") or fallback.get("primary") or "新品设计"
        secondary = value.get("secondary") if isinstance(value.get("secondary"), list) else fallback.get("secondary", [])
        confidence = clamp_float(value.get("confidence"), fallback.get("confidence", 0.72))
        return {
            "primary": str(primary),
            "secondary": unique([str(item) for item in secondary if item]),
            "confidence": confidence,
        }

    def _clean_fields(self, value: Any, version: str) -> tuple[dict[str, DemandField], dict[str, Any]]:
        fields: dict[str, DemandField] = {}
        extra_fields: dict[str, Any] = {}
        if not isinstance(value, dict):
            return fields, extra_fields

        for name, payload in value.items():
            if name not in ALLOWED_FIELD_NAMES:
                extra_fields[name] = payload
                continue
            if not isinstance(payload, dict):
                extra_fields[name] = payload
                continue
            field_value = normalize_value(payload.get("value"))
            if not field_value:
                continue
            source = ALLOWED_SOURCES.get(str(payload.get("source", "inferred")), FieldSource.INFERRED)
            confidence = clamp_float(payload.get("confidence"), 0.72)
            fields[name] = DemandField(name, field_value, source, confidence, version)
        return fields, extra_fields

    def _merge_fields(self, rule_fields: dict[str, DemandField], llm_fields: dict[str, DemandField]) -> dict[str, DemandField]:
        merged = dict(llm_fields)
        for name, rule_field in rule_fields.items():
            llm_field = merged.get(name)
            if llm_field is None:
                merged[name] = rule_field
                continue
            if rule_field.source in PRIORITY_SOURCES and llm_field.source not in PRIORITY_SOURCES:
                merged[name] = rule_field
        return merged

    def _clean_questions(self, value: Any) -> list[ClarifyingQuestion]:
        if not isinstance(value, list):
            return []
        questions: list[ClarifyingQuestion] = []
        sorted_items = sorted(
            [item for item in value if isinstance(item, dict)],
            key=lambda item: item.get("priority", 99),
        )
        for item in sorted_items:
            field_name = item.get("field")
            question = item.get("question")
            options = item.get("options")
            if field_name not in ALLOWED_FIELD_NAMES or not question or not isinstance(options, list):
                continue
            cleaned_options = [str(option) for option in options if option]
            if not cleaned_options:
                continue
            questions.append(ClarifyingQuestion(str(field_name), str(question), cleaned_options[:6]))
            if len(questions) == 3:
                break
        return questions

    def _clean_report_insights(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        cleaned: dict[str, Any] = {}
        personas = clean_personas(value.get("personas"))
        if personas:
            cleaned["personas"] = personas
        scenarios = clean_scenarios(value.get("scenario_map"))
        if scenarios:
            cleaned["scenario_map"] = scenarios
        if isinstance(value.get("product_recommendations"), dict):
            cleaned["product_recommendations"] = value["product_recommendations"]
        if isinstance(value.get("constraints"), dict):
            cleaned["constraints"] = value["constraints"]
        if isinstance(value.get("risk_notes"), list):
            cleaned["risk_notes"] = [str(item) for item in value["risk_notes"] if item]
        if isinstance(value.get("trend_summary"), dict):
            cleaned["trend_summary"] = value["trend_summary"]
        return cleaned


def normalize_value(value: Any) -> Any:
    if value in (None, "", []):
        return None
    if isinstance(value, list):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return unique(cleaned)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    return value


def clamp_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return max(0.0, min(1.0, number))


def clean_personas(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    personas = []
    for item in value:
        if isinstance(item, dict):
            personas.append(item)
        elif isinstance(item, str) and item.strip():
            personas.append(
                {
                    "name": item.strip(),
                    "motivation": "待补充",
                    "design_implication": "待补充",
                }
            )
    return personas


def clean_scenarios(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    scenarios = []
    for item in value:
        if isinstance(item, dict):
            scenarios.append(item)
        elif isinstance(item, str) and item.strip():
            scenarios.append(
                {
                    "scenario": item.strip(),
                    "user_goal": "待补充",
                    "product_requirements": ["待补充"],
                }
            )
    return scenarios


def issue(path: str, code: str, message: str, severity: str = "error") -> dict[str, Any]:
    return {"path": path, "code": code, "message": message, "severity": severity}


def too_many_placeholders(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False)
    return text.count("待补充") >= 3


def env_bool(name: str, default: bool) -> bool:
    raw = load_dotenv().get(name, os.environ.get(name))
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = load_dotenv().get(name, os.environ.get(name))
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def has_blocking_issues(issues: list[dict[str, Any]]) -> bool:
    return any(item.get("severity") == "error" for item in issues)


def summarize_issues(issues: list[dict[str, Any]]) -> str:
    parts = []
    for item in issues[:4]:
        code = item.get("code", "validation_error")
        message = item.get("message", "")
        parts.append(f"{code}: {message}".strip())
    return "; ".join(parts)[:500]

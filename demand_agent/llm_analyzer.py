from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .llm_client import DeepSeekClient, LLMClientResult
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


class LLMAnalyzer:
    def __init__(self, client: DeepSeekClient | None = None) -> None:
        self.client = client or DeepSeekClient.from_env()

    def analyze(self, user_input: str, context: dict[str, Any] | None = None, version: str = "v1.0") -> SemanticAnalysisResult:
        context = context or {}
        rule_intent = parse_intent(user_input)
        rule_fields = extract_fields(user_input, context)
        rule_questions = build_questions(rule_fields)

        response = self.client.complete_json(self._messages(user_input, context))
        if response.status != "success" or response.content is None:
            return SemanticAnalysisResult(
                status=response.status,
                analysis_mode="rules_fallback",
                model=response.model,
                error=response.error,
                intent=rule_intent,
                fields=rule_fields,
                questions=rule_questions,
            )

        try:
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
            return SemanticAnalysisResult(
                status="success",
                analysis_mode="llm_enhanced",
                model=response.model,
                intent=intent,
                fields=fields,
                questions=questions[:3],
                report_insights=report_insights,
                extra_fields=extra_fields,
            )
        except (TypeError, ValueError) as exc:
            return SemanticAnalysisResult(
                status="invalid_json",
                analysis_mode="rules_fallback",
                model=response.model,
                error=str(exc)[:500],
                intent=rule_intent,
                fields=rule_fields,
                questions=rule_questions,
            )

    def _messages(self, user_input: str, context: dict[str, Any]) -> list[dict[str, str]]:
        field_schema = {name: label for name, label in FIELD_LABELS.items()}
        field_schema["brand_context"] = "品牌上下文"
        system_prompt = (
            "你是文化产品需求分析师 Agent。请输出严格 json，不要输出 markdown。\n"
            "你的任务是从中文自然语言需求中识别意图、标准字段、澄清问题和报告洞察。\n"
            "fields 只能使用给定字段名；不确定但有价值的新字段放入 extra_fields。\n"
            "字段 source 只能是 explicit、context、inferred、assumption。\n"
            "不要编造实时趋势数据；未接入外部数据时标注为 LLM语义推断。\n"
            "必须返回 json object，结构包含 intent、fields、questions、report_insights、extra_fields。"
        )
        user_payload = {
            "user_input": user_input,
            "context": context,
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
        allowed = {
            "personas",
            "scenario_map",
            "product_recommendations",
            "constraints",
            "risk_notes",
            "trend_summary",
        }
        return {key: value[key] for key in allowed if key in value}


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

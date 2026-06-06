from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any

from .models import ClarifyingQuestion, DemandField, FieldSource


DEFAULT_BUDGET = "300-500元/套"
DEFAULT_CHANNELS = ["小红书", "线下文旅店"]

FIELD_LABELS = {
    "target_users": "目标人群",
    "usage_scenarios": "使用场景",
    "product_categories": "产品品类",
    "budget_range": "预算区间",
    "aesthetic_preferences": "审美偏好",
    "cultural_preferences": "文化偏好",
    "channel_suggestions": "渠道建议",
    "materials": "材质",
    "time": "时间",
    "location": "地点",
    "emotional_keywords": "情绪关键词",
}

QUESTION_BANK = OrderedDict(
    [
        (
            "usage_scenarios",
            ClarifyingQuestion(
                field="usage_scenarios",
                question="这套产品主要用于婚礼回礼、景区零售，还是商务赠礼？",
                options=["婚礼回礼", "景区零售", "商务赠礼", "多场景兼用"],
            ),
        ),
        (
            "target_users",
            ClarifyingQuestion(
                field="target_users",
                question="主要面向哪类人群？",
                options=["新婚人群", "年轻女性", "游客", "商务客户"],
            ),
        ),
        (
            "budget_range",
            ClarifyingQuestion(
                field="budget_range",
                question="期望单套预算大致是多少？",
                options=["99-199元", "300-500元", "500元以上", "暂不确定"],
            ),
        ),
        (
            "product_categories",
            ClarifyingQuestion(
                field="product_categories",
                question="是否已有固定产品品类？",
                options=["丝巾+香囊+礼盒", "团扇/手帕", "家居服/晨袍", "让系统推荐"],
            ),
        ),
        (
            "aesthetic_preferences",
            ClarifyingQuestion(
                field="aesthetic_preferences",
                question="整体风格更希望偏向哪一类？",
                options=["江南清雅", "轻新中式", "年轻国潮", "高端礼赠"],
            ),
        ),
        (
            "channel_suggestions",
            ClarifyingQuestion(
                field="channel_suggestions",
                question="后续主要在哪些渠道使用或销售？",
                options=["小红书种草", "抖音电商", "线下文旅店", "商务礼赠"],
            ),
        ),
    ]
)

KEYWORDS: dict[str, list[tuple[str, str]]] = {
    "target_users": [
        ("新婚", "新婚人群"),
        ("新人", "新婚人群"),
        ("年轻女性", "年轻女性"),
        ("游客", "游客"),
        ("商务", "商务客户"),
        ("客户", "商务客户"),
        ("长辈", "亲友长辈"),
    ],
    "usage_scenarios": [
        ("春游", "春游纪念"),
        ("婚礼", "婚礼回礼"),
        ("婚庆", "婚庆礼赠"),
        ("旅拍", "旅拍纪念"),
        ("景区", "景区零售"),
        ("文旅", "文旅伴手礼"),
        ("商务", "商务赠礼"),
        ("展会", "展会赠礼"),
        ("伴手礼", "礼赠场景"),
    ],
    "product_categories": [
        ("丝绸伴手礼", "丝绸伴手礼"),
        ("文创礼品", "文创礼品"),
        ("伴手礼", "伴手礼"),
        ("丝巾", "丝巾"),
        ("方巾", "真丝小方巾"),
        ("香囊", "香囊"),
        ("团扇", "团扇"),
        ("礼盒", "礼盒"),
        ("家居服", "家居服"),
        ("晨袍", "晨袍"),
        ("手帕", "手帕"),
    ],
    "materials": [
        ("丝绸", "丝绸"),
        ("真丝", "真丝"),
        ("绸缎", "绸缎"),
        ("宋锦", "宋锦"),
        ("棉麻", "棉麻"),
    ],
    "location": [
        ("西湖", "西湖"),
        ("南浔", "南浔"),
        ("湖州", "湖州"),
        ("杭州", "杭州"),
        ("江南", "江南"),
    ],
    "aesthetic_preferences": [
        ("新中式", "新中式"),
        ("江南", "江南清雅"),
        ("国潮", "年轻国潮"),
        ("极简", "极简"),
        ("高级", "高端礼赠"),
        ("高端", "高端礼赠"),
        ("春日", "春日感"),
    ],
    "emotional_keywords": [
        ("浪漫", "浪漫"),
        ("吉祥", "吉祥"),
        ("温柔", "温柔"),
        ("喜庆", "喜庆"),
        ("松弛", "松弛感"),
        ("仪式", "仪式感"),
        ("年轻", "年轻化"),
    ],
    "cultural_preferences": [
        ("西湖", "西湖"),
        ("南浔", "南浔"),
        ("辑里湖丝", "辑里湖丝"),
        ("婚嫁", "婚嫁"),
        ("并蒂莲", "并蒂莲"),
        ("喜鹊", "喜鹊"),
        ("宋韵", "宋韵"),
        ("江南", "江南"),
        ("丝绸", "丝绸"),
    ],
    "channel_suggestions": [
        ("小红书", "小红书"),
        ("抖音", "抖音"),
        ("淘宝", "淘宝/天猫"),
        ("天猫", "淘宝/天猫"),
        ("线下", "线下文旅店"),
        ("直播", "直播电商"),
        ("电商", "电商平台"),
    ],
}


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def parse_intent(text: str) -> dict[str, Any]:
    intent_rules = [
        ("趋势调研", ["趋势", "爆款", "热词", "流行"], 4),
        ("旧方案优化", ["优化", "修改", "上次", "调整"], 4),
        ("人群洞察", ["画像", "喜欢什么"], 3),
        ("人群洞察", ["人群"], 1),
        ("品类选择", ["适合做什么", "推荐品类", "选品"], 3),
        ("商业落地", ["销量", "转化", "抖音卖", "淘宝卖", "成本"], 3),
        ("新品设计", ["设计", "开发", "做一款", "做一个", "做一套"], 5),
    ]
    scores: dict[str, int] = {}
    secondary: list[str] = []
    for label, words, weight in intent_rules:
        if any(word in text for word in words):
            scores[label] = scores.get(label, 0) + weight
            secondary.append(label)
    primary = max(scores, key=scores.get) if scores else "新品设计"
    if "伴手礼" in text or "礼品" in text:
        secondary.append("礼品定制")
    if "婚" in text:
        secondary.append("婚庆礼品")
    return {
        "primary": primary,
        "secondary": unique(secondary),
        "confidence": 0.9 if secondary else 0.72,
    }


def extract_fields(text: str, context: dict[str, Any] | None = None) -> dict[str, DemandField]:
    fields: dict[str, DemandField] = {}
    for field_name, rules in KEYWORDS.items():
        values = [value for keyword, value in rules if keyword in text]
        if values:
            fields[field_name] = DemandField(
                field_name=field_name,
                field_value=unique(values),
                source=FieldSource.EXPLICIT,
                confidence=0.88,
            )

    time_values = re.findall(r"\d{1,2}\s*月|春季|夏季|秋季|冬季|七夕|中秋|春节|婚礼季", text)
    if time_values:
        fields["time"] = DemandField("time", unique([v.replace(" ", "") for v in time_values]), FieldSource.EXPLICIT, 0.92)

    budget_match = re.search(r"(\d+\s*[-到至]\s*\d+\s*元|\d+\s*元以上|千元以上|[一二三四五六七八九]百元)", text)
    if budget_match:
        fields["budget_range"] = DemandField("budget_range", budget_match.group(1).replace(" ", ""), FieldSource.EXPLICIT, 0.9)

    if context:
        if context.get("target_channel") and "channel_suggestions" not in fields:
            fields["channel_suggestions"] = DemandField(
                "channel_suggestions",
                list(context["target_channel"]),
                FieldSource.CONTEXT,
                0.86,
            )
        if context.get("brand") and "brand_context" not in fields:
            fields["brand_context"] = DemandField("brand_context", context["brand"], FieldSource.CONTEXT, 0.86)

    apply_inferences(fields)
    return fields


def apply_inferences(fields: dict[str, DemandField]) -> None:
    if "target_users" in fields:
        users = fields["target_users"].field_value
        if "新婚人群" in users and "usage_scenarios" not in fields:
            fields["usage_scenarios"] = DemandField(
                "usage_scenarios",
                ["婚礼回礼", "旅拍纪念"],
                FieldSource.INFERRED,
                0.68,
            )
        if "新婚人群" in users and "emotional_keywords" not in fields:
            fields["emotional_keywords"] = DemandField(
                "emotional_keywords",
                ["浪漫", "吉祥", "仪式感"],
                FieldSource.INFERRED,
                0.7,
            )

    if "location" in fields and "aesthetic_preferences" not in fields:
        locations = fields["location"].field_value
        if any(location in locations for location in ["西湖", "南浔", "江南", "杭州", "湖州"]):
            fields["aesthetic_preferences"] = DemandField(
                "aesthetic_preferences",
                ["江南清雅"],
                FieldSource.INFERRED,
                0.66,
            )

    if "materials" in fields and "cultural_preferences" in fields:
        merged = unique(list(fields["cultural_preferences"].field_value) + list(fields["materials"].field_value))
        fields["cultural_preferences"].field_value = merged
    elif "materials" in fields and "cultural_preferences" not in fields:
        fields["cultural_preferences"] = DemandField(
            "cultural_preferences",
            list(fields["materials"].field_value),
            FieldSource.INFERRED,
            0.65,
        )


def apply_answer(fields: dict[str, DemandField], field_name: str, value: Any, version: str) -> DemandField:
    skipped = value in (None, "", "暂不确定", "不确定", "让系统推荐")
    if skipped and field_name == "budget_range":
        field = DemandField(field_name, DEFAULT_BUDGET, FieldSource.ASSUMPTION, 0.55, version)
    elif skipped and field_name == "channel_suggestions":
        field = DemandField(field_name, DEFAULT_CHANNELS, FieldSource.ASSUMPTION, 0.55, version)
    elif skipped and field_name == "product_categories":
        field = DemandField(field_name, ["丝巾", "香囊", "礼盒"], FieldSource.ASSUMPTION, 0.55, version)
    elif skipped:
        field = DemandField(field_name, ["待确认"], FieldSource.ASSUMPTION, 0.35, version)
    else:
        field_value = value if isinstance(value, list) else split_answer(value)
        field = DemandField(field_name, field_value, FieldSource.USER_CONFIRMED, 0.96, version)
    fields[field_name] = field
    apply_inferences(fields)
    return field


def split_answer(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    parts = re.split(r"[、,+，/和及与]+", value)
    cleaned = [part.strip() for part in parts if part.strip()]
    return cleaned if len(cleaned) > 1 else value.strip()


def build_questions(fields: dict[str, DemandField]) -> list[ClarifyingQuestion]:
    missing = []
    for field_name in QUESTION_BANK:
        if field_name not in fields or not fields[field_name].field_value:
            missing.append(QUESTION_BANK[field_name])
    return missing[:3]


def completeness_score(fields: dict[str, DemandField]) -> int:
    score = 0.0
    score += field_points(fields, "target_users", 20)
    score += field_points(fields, "usage_scenarios", 20)
    score += field_points(fields, "product_categories", 15)
    score += field_points(fields, "budget_range", 15)
    style_points = max(
        field_points(fields, "aesthetic_preferences", 12),
        0,
    ) + max(field_points(fields, "cultural_preferences", 8), 0)
    score += min(style_points, 20)
    score += field_points(fields, "channel_suggestions", 10)
    return int(round(score))


def field_points(fields: dict[str, DemandField], field_name: str, points: int) -> float:
    field = fields.get(field_name)
    if not field or not field.field_value:
        return 0
    multiplier = 0.75 if field.source == FieldSource.ASSUMPTION else 1.0
    return points * multiplier

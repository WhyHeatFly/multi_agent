from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.cultural_ip import CulturalIPTask, IPDirection, IPReport, RiskAssessment, new_id
from app.services.llm import LLMAdapter
from app.services.rag import KnowledgeService
from app.services.report import build_report_markdown
from app.services.rules import RISK_RULES, merge_expansions


class CulturalIPService:
    def __init__(self) -> None:
        self.knowledge = KnowledgeService()
        self.llm = LLMAdapter(get_settings())

    def create_task(self, db: Session, payload) -> CulturalIPTask:
        report = payload.structured_report
        keywords = self._extract_keywords(report)
        task = CulturalIPTask(
            ip_task_id=new_id("ip_task"),
            demand_report_id=payload.demand_report_id,
            status="created",
            structured_report=report,
            options=payload.options.model_dump(),
            retrieval_scope=keywords,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        self.run_pipeline(db, task.ip_task_id)
        return task

    def run_pipeline(self, db: Session, ip_task_id: str) -> CulturalIPTask:
        task = self.get_task(db, ip_task_id)
        try:
            self.llm.ensure_configured()

            task.status = "keyword_expansion"
            db.commit()
            keywords = task.retrieval_scope or self._extract_keywords(task.structured_report)
            expansions = merge_expansions(keywords)
            query_terms = keywords + expansions["strong_related"] + expansions["risk_related"]

            task.status = "retrieval"
            db.commit()
            sources = self.knowledge.search(db, query_terms, limit=8)
            if not sources:
                self.knowledge.ingest_directory(db)
                sources = self.knowledge.search(db, query_terms, limit=8)

            task.status = "llm_generating"
            db.commit()
            directions = self._generate_directions_with_llm(task, keywords, expansions, sources)
            for direction in directions:
                db.add(IPDirection(**direction))

            task.status = "directions_ready"
            task.selected_direction = None
            task.error_message = None
            db.commit()
            db.refresh(task)
            return task
        except Exception as exc:
            task.status = "failed"
            task.error_message = str(exc)
            db.commit()
            raise

    def get_task(self, db: Session, ip_task_id: str) -> CulturalIPTask:
        task = db.get(CulturalIPTask, ip_task_id)
        if not task:
            raise ValueError(f"Task not found: {ip_task_id}")
        return task



    def delete_task(self, db: Session, ip_task_id: str) -> None:
        task = self.get_task(db, ip_task_id)
        db.execute(delete(RiskAssessment).where(RiskAssessment.ip_task_id == ip_task_id))
        db.delete(task)
        db.commit()
    def list_tasks(self, db: Session, limit: int = 50) -> list[CulturalIPTask]:
        return list(
            db.scalars(
                select(CulturalIPTask)
                .order_by(CulturalIPTask.updated_at.desc())
                .limit(max(min(limit, 100), 1))
            )
        )
    def list_directions(self, db: Session, ip_task_id: str) -> list[IPDirection]:
        return list(
            db.scalars(
                select(IPDirection)
                .where(IPDirection.ip_task_id == ip_task_id)
                .order_by(IPDirection.score.desc())
            )
        )

    def get_latest_report(self, db: Session, ip_task_id: str) -> IPReport | None:
        return db.scalar(
            select(IPReport)
            .where(IPReport.ip_task_id == ip_task_id)
            .order_by(IPReport.created_at.desc())
            .limit(1)
        )

    def generate_report(self, db: Session, ip_task_id: str, selected_direction: str) -> IPReport:
        task = self.get_task(db, ip_task_id)
        direction = self._find_direction(db, ip_task_id, selected_direction)
        direction_data = self._direction_to_dict(direction)
        report_json = self._build_report_json(task, direction_data, direction.cultural_basis or [])
        report_markdown = build_report_markdown(report_json)
        report = IPReport(
            ip_report_id=new_id("ip"),
            ip_task_id=task.ip_task_id,
            theme=direction.name,
            status="completed",
            report_markdown=report_markdown,
            report_json=report_json,
        )
        db.add(report)
        task.selected_direction = direction.name
        task.status = "completed"
        task.error_message = None
        db.commit()
        db.refresh(report)
        return report

    def apply_report_feedback(self, db: Session, ip_task_id: str, feedback: str) -> IPReport:
        task = self.get_task(db, ip_task_id)
        report = self.get_latest_report(db, ip_task_id)
        if not report:
            raise ValueError("请先选择方向并生成报告，再提交报告反馈。")
        data = self.llm.chat_json_sync(
            self._build_report_feedback_messages(report.report_markdown, report.report_json, feedback),
            temperature=0.45,
        )
        markdown = str(data.get("report_markdown") or data.get("markdown") or "").strip()
        if not markdown:
            raise RuntimeError("模型未返回 report_markdown，无法更新报告。")
        report_json = data.get("report_json") if isinstance(data.get("report_json"), dict) else dict(report.report_json)
        report_json["report_feedback"] = feedback
        report.report_markdown = markdown
        report.report_json = report_json
        report.status = "revised"
        task.status = "report_revised"
        task.error_message = None
        db.commit()
        db.refresh(report)
        return report

    def apply_feedback(self, db: Session, ip_task_id: str, selected_direction: str, feedback: str) -> dict:
        task = self.get_task(db, ip_task_id)
        direction = self._find_direction(db, ip_task_id, selected_direction)
        updated = self._refine_direction_with_llm(task, direction, feedback)
        self._apply_direction_update(direction, updated)
        task.status = "direction_revised"
        task.error_message = None
        db.commit()
        db.refresh(direction)
        return {"direction": self._direction_to_dict(direction), "feedback": feedback}

    def build_handoff(self, db: Session, ip_task_id: str, target_agents: list[str]) -> list[dict]:
        task = self.get_task(db, ip_task_id)
        if not task.selected_direction:
            raise ValueError("请先选择喜欢的方向并生成报告，再生成任务包。")
        if not self.get_latest_report(db, ip_task_id):
            raise ValueError("请先生成报告，再生成下游任务包。")

        direction = self._find_direction(db, ip_task_id, task.selected_direction)
        targets = set(target_agents or ["designer_agent", "marketer_agent", "quality_controller_agent"])
        symbols = [item["symbol"] for item in direction.symbol_system if item.get("level") != "禁用符号"]
        avoid = list(dict.fromkeys(
            [item["symbol"] for item in direction.symbol_system if item.get("level") == "禁用符号"]
            + [risk["risk"] for risk in direction.risk_assessment.get("items", [])]
        ))

        def wants(key: str, zh_name: str) -> bool:
            return key in targets or zh_name in targets

        packages = []
        if wants("designer_agent", "设计师 Agent"):
            packages.append(
                {
                    "agent": "设计师 Agent",
                    "任务": "根据文化 IP 方向生成丝绸伴手礼视觉方案",
                    "IP主题": direction.name,
                    "故事内核": direction.story_core,
                    "主符号": symbols[:2],
                    "辅助符号": symbols[2:5],
                    "推荐色彩": [
                        color.get("name", "")
                        for color in direction.visual_translation.get("color", {}).get("main_colors", [])
                    ],
                    "纹样建议": direction.visual_translation.get("pattern", {}).get("secondary_patterns", []),
                    "避免事项": avoid,
                }
            )
        if wants("marketer_agent", "营销师 Agent"):
            packages.append(
                {
                    "agent": "营销师 Agent",
                    "任务": "生成文化 IP 营销文案",
                    "IP主题": direction.name,
                    "故事内核": direction.story_core,
                    "卖点": ["文化依据可追溯", "符号体系清晰", "视觉转译明确"],
                    "语气": "温柔、清雅、年轻化",
                    "文案种子": [
                        direction.visual_translation.get("copywriting", ""),
                        direction.one_sentence,
                    ],
                }
            )
        if wants("quality_controller_agent", "品控师 Agent"):
            packages.append(
                {
                    "agent": "品控师 Agent",
                    "任务": "审核设计结果是否符合文化 IP 方案",
                    "必须包含": symbols[:2],
                    "建议包含": symbols[2:5],
                    "必须避免": avoid,
                    "一致性规则": [
                        "主视觉应表达方案故事内核，不应偏离核心文化依据。",
                        "高风险或禁用符号不得进入故事主叙事、主纹样或包装核心画面。",
                    ],
                }
            )
        return packages
    def _find_direction(self, db: Session, ip_task_id: str, selected_direction: str) -> IPDirection:
        direction = db.scalar(
            select(IPDirection).where(
                IPDirection.ip_task_id == ip_task_id,
                or_(IPDirection.direction_id == selected_direction, IPDirection.name == selected_direction),
            )
        )
        if not direction:
            raise ValueError(f"Direction not found: {selected_direction}")
        return direction

    def _direction_to_dict(self, direction: IPDirection) -> dict:
        return {
            "direction_id": direction.direction_id,
            "ip_task_id": direction.ip_task_id,
            "name": direction.name,
            "type": direction.type,
            "one_sentence": direction.one_sentence,
            "story_core": direction.story_core,
            "cultural_basis": direction.cultural_basis or [],
            "symbol_system": direction.symbol_system or [],
            "visual_translation": direction.visual_translation or {},
            "risk_assessment": direction.risk_assessment or {},
            "score": direction.score,
            "recommendation": direction.recommendation,
        }

    def _refine_direction_with_llm(self, task: CulturalIPTask, direction: IPDirection, feedback: str) -> dict:
        keywords = task.retrieval_scope or self._extract_keywords(task.structured_report)
        expansions = merge_expansions(keywords)
        sources = direction.cultural_basis or []
        data = self.llm.chat_json_sync(
            self._build_direction_feedback_messages(task, direction, feedback),
            temperature=0.55,
        )
        item = data.get("direction") or data.get("updated_direction") or data
        if not isinstance(item, dict):
            raise RuntimeError("模型未返回 direction 对象，无法更新方向。")
        updated = self._normalize_llm_direction(task.ip_task_id, item, 0, expansions, sources)
        updated["direction_id"] = direction.direction_id
        return updated

    def _apply_direction_update(self, direction: IPDirection, updated: dict) -> None:
        direction.name = updated["name"]
        direction.type = updated["type"]
        direction.one_sentence = updated["one_sentence"]
        direction.story_core = updated["story_core"]
        direction.cultural_basis = updated["cultural_basis"]
        direction.symbol_system = updated["symbol_system"]
        direction.visual_translation = updated["visual_translation"]
        direction.risk_assessment = updated["risk_assessment"]
        direction.score = updated["score"]
        direction.recommendation = updated["recommendation"]

    def _build_direction_feedback_messages(self, task: CulturalIPTask, direction: IPDirection, feedback: str) -> list[dict[str, str]]:
        keywords = task.retrieval_scope or self._extract_keywords(task.structured_report)
        payload = {
            "structured_report": task.structured_report,
            "current_direction": self._direction_to_dict(direction),
            "user_feedback": feedback,
            "required_output_schema": self._build_llm_payload(
                task,
                keywords,
                merge_expansions(keywords),
                direction.cultural_basis or [],
                1,
            )["required_output_schema"]["ip_directions"][0],
        }
        return [
            {
                "role": "system",
                "content": (
                    "你是文化 IP 设计师 Agent。你要根据用户反馈改写一个既有 IP 方向，"
                    "保留真实文化依据，不得输出固定模板。输出严格 JSON，根字段为 direction。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请只更新用户反馈影响到的方向内容，同时保持故事、符号、视觉和风险审核完整。"
                    "输出 JSON：{\"direction\": {...}}。输入如下：\n"
                    f"{payload}"
                ),
            },
        ]
    def _build_report_feedback_messages(self, report_markdown: str, report_json: dict, feedback: str) -> list[dict[str, str]]:
        payload = {
            "current_report_markdown": report_markdown,
            "current_report_json": report_json,
            "user_feedback": feedback,
            "required_output_schema": {
                "report_markdown": "完整 Markdown 报告",
                "report_json": "与报告同步的结构化 JSON，可在原 JSON 基础上更新",
            },
        }
        return [
            {
                "role": "system",
                "content": (
                    "你是文化 IP 设计师 Agent 的报告编辑模块。根据用户反馈调整 Markdown 报告，"
                    "不得丢失文化来源、故事内核、符号体系、视觉建议和风险审核。输出严格 JSON。"
                ),
            },
            {
                "role": "user",
                "content": "请返回 {\"report_markdown\": string, \"report_json\": object}。输入如下：\n" f"{payload}",
            },
        ]
    def _extract_keywords(self, report: dict) -> list[str]:
        keys = []
        candidate_fields = [
            "cultural_preferences",
            "emotional_keywords",
            "usage_scenarios",
            "product_categories",
            "target_users",
            "risk_hints",
        ]
        known = ["西湖", "婚嫁", "丝绸", "江南", "春日", "南浔", "非遗", "节庆", "城市礼品"]
        for field in candidate_fields:
            value = report.get(field, [])
            if isinstance(value, str):
                value = [value]
            for item in value:
                for keyword in known:
                    if keyword in item and keyword not in keys:
                        keys.append(keyword)
                if item and item not in keys and len(keys) < 12:
                    keys.append(item)
        return keys or ["文化礼品"]

    def _generate_directions_with_llm(self, task, keywords, expansions, sources) -> list[dict]:
        num_directions = int(task.options.get("num_directions", 5))
        payload = self._build_llm_payload(task, keywords, expansions, sources, num_directions)
        data = self.llm.chat_json_sync(self._build_generation_messages(payload), temperature=0.72)
        raw_directions = data.get("ip_directions") or data.get("directions") or []
        if not isinstance(raw_directions, list) or not raw_directions:
            raise RuntimeError("模型未返回 ip_directions 列表，无法生成文化 IP 任务。")
        return [
            self._normalize_llm_direction(task.ip_task_id, item, index, expansions, sources)
            for index, item in enumerate(raw_directions[:num_directions])
        ]

    def _build_llm_payload(self, task, keywords, expansions, sources, num_directions: int) -> dict:
        return {
            "num_directions": num_directions,
            "structured_report": task.structured_report,
            "keywords": keywords,
            "expanded_keywords": expansions,
            "retrieval_sources": sources[:8],
            "required_output_schema": {
                "ip_directions": [
                    {
                        "name": "string",
                        "type": "string",
                        "one_sentence": "string",
                        "story_core": "100-200字中文故事，必须连接用户场景和产品承载",
                        "core_symbols": ["string"],
                        "symbol_system": [
                            {
                                "symbol": "string",
                                "level": "主符号/辅助符号/背景符号/禁用符号",
                                "meaning": "string",
                                "visual_feature": "string",
                                "design_usage": ["string"],
                                "source_ids": ["string"],
                            }
                        ],
                        "visual_translation": {
                            "pattern": {
                                "main_pattern": "string",
                                "secondary_patterns": ["string"],
                                "composition": "string",
                            },
                            "color": {
                                "main_colors": [{"name": "string", "hex": "#RRGGBB"}],
                                "accent_colors": [{"name": "string", "hex": "#RRGGBB"}],
                            },
                            "craft": ["string"],
                            "packaging": "string",
                            "copywriting": "string",
                        },
                        "risk_assessment": {
                            "overall_level": "Low/Medium/High/Blocked",
                            "items": [
                                {
                                    "risk": "string",
                                    "level": "Low/Medium/High/Blocked",
                                    "reason": "string",
                                    "suggestion": "string",
                                }
                            ],
                        },
                        "score": 0,
                        "recommendation": "主推/备选",
                    }
                ]
            },
        }

    def _build_generation_messages(self, payload: dict) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "你是文化产品智能工厂中的文化 IP 设计师 Agent。必须基于用户需求和检索资料真实生成方案，"
                    "不得照搬固定模板，不得编造不存在的文献来源。输出必须是严格 JSON，不要 Markdown。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请生成差异化文化 IP 方向。要求：每个方向都要有文化依据、故事内核、符号体系、视觉转译、风险审核；"
                    "符号不能堆砌，禁用符号只能进入 avoid/risk，不得进入主视觉。下面是输入 JSON：\n"
                    f"{payload}"
                ),
            },
        ]

    def _normalize_llm_direction(self, ip_task_id: str, item: dict, index: int, expansions, sources) -> dict:
        source_ids = {source["source_id"] for source in sources}
        name = str(item.get("name") or f"文化 IP 方向 {index + 1}")
        story = str(item.get("story_core") or item.get("core_story") or item.get("one_sentence") or name)
        symbols = item.get("symbol_system")
        if not isinstance(symbols, list) or not symbols:
            core_symbols = item.get("core_symbols") or item.get("symbols") or []
            symbols = self._symbol_system_from_names(core_symbols, expansions, sources)
        else:
            symbols = [self._normalize_symbol(symbol, source_ids) for symbol in symbols]
            symbols.extend(self._forbidden_symbols(expansions))
        visual = self._normalize_visual_translation(item.get("visual_translation") or {})
        risk = self._merge_risk_assessment(item.get("risk_assessment") or {}, story, symbols)
        score = int(item.get("score") or self._score_direction(index, risk, sources))
        return {
            "direction_id": new_id("dir"),
            "ip_task_id": ip_task_id,
            "name": name,
            "type": str(item.get("type") or "文化创意型"),
            "one_sentence": str(item.get("one_sentence") or story[:80]),
            "story_core": story,
            "cultural_basis": sources[:3],
            "symbol_system": symbols,
            "visual_translation": visual,
            "risk_assessment": risk,
            "score": max(min(score, 100), 0),
            "recommendation": str(item.get("recommendation") or ("主推" if index == 0 else "备选")),
        }

    def _symbol_system_from_names(self, names, expansions, sources) -> list[dict]:
        if isinstance(names, str):
            names = [names]
        source_ids = [source["source_id"] for source in sources[:2]]
        symbols = []
        for index, name in enumerate(list(names)[:5]):
            symbols.append(
                {
                    "symbol": str(name),
                    "level": "主符号" if index == 0 else "辅助符号" if index < 4 else "背景符号",
                    "meaning": "由模型根据需求生成的文化符号。",
                    "visual_feature": "需在视觉设计中进一步细化形态语言。",
                    "design_usage": ["主视觉", "包装纹样"],
                    "source_ids": source_ids,
                }
            )
        symbols.extend(self._forbidden_symbols(expansions))
        return symbols

    def _normalize_symbol(self, symbol: dict, valid_source_ids: set[str]) -> dict:
        source_ids = symbol.get("source_ids") or []
        if isinstance(source_ids, str):
            source_ids = [source_ids]
        source_ids = [source_id for source_id in source_ids if source_id in valid_source_ids]
        return {
            "symbol": str(symbol.get("symbol") or symbol.get("name") or "未命名符号"),
            "level": str(symbol.get("level") or "辅助符号"),
            "meaning": str(symbol.get("meaning") or "待补充文化寓意"),
            "visual_feature": str(symbol.get("visual_feature") or "待补充视觉特征"),
            "design_usage": symbol.get("design_usage") if isinstance(symbol.get("design_usage"), list) else [],
            "source_ids": source_ids,
        }

    def _forbidden_symbols(self, expansions) -> list[dict]:
        return [
            {
                "symbol": risk_symbol,
                "level": "禁用符号",
                "meaning": "可能造成负面联想或文化误用。",
                "visual_feature": "不建议进入主视觉。",
                "design_usage": ["禁止作为主叙事", "需人工审核"],
                "source_ids": [],
            }
            for risk_symbol in expansions.get("risk_related", [])[:3]
        ]

    def _normalize_visual_translation(self, visual: dict) -> dict:
        pattern = visual.get("pattern") if isinstance(visual.get("pattern"), dict) else {}
        color = visual.get("color") if isinstance(visual.get("color"), dict) else {}
        return {
            "pattern": {
                "main_pattern": str(pattern.get("main_pattern") or "待生成主纹样"),
                "secondary_patterns": pattern.get("secondary_patterns")
                if isinstance(pattern.get("secondary_patterns"), list)
                else [],
                "composition": str(pattern.get("composition") or "待生成构图建议"),
            },
            "color": {
                "main_colors": color.get("main_colors") if isinstance(color.get("main_colors"), list) else [],
                "accent_colors": color.get("accent_colors") if isinstance(color.get("accent_colors"), list) else [],
            },
            "craft": visual.get("craft") if isinstance(visual.get("craft"), list) else [],
            "packaging": str(visual.get("packaging") or "待生成包装建议"),
            "copywriting": str(visual.get("copywriting") or "待生成传播语"),
        }

    def _merge_risk_assessment(self, llm_risk: dict, story: str, symbol_system: list[dict]) -> dict:
        items = llm_risk.get("items") if isinstance(llm_risk.get("items"), list) else []
        normalized = []
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "risk": str(item.get("risk") or "未命名风险"),
                    "level": str(item.get("level") or "Low"),
                    "reason": str(item.get("reason") or "模型未说明原因"),
                    "suggestion": str(item.get("suggestion") or "建议人工复核"),
                }
            )
        local = self._local_risk_assessment(story, symbol_system)
        existing = {item["risk"] for item in normalized}
        normalized.extend([item for item in local["items"] if item["risk"] not in existing])
        levels = {item["level"] for item in normalized if item["level"] != "Low"}
        overall = str(llm_risk.get("overall_level") or "")
        if overall not in {"Low", "Medium", "High", "Blocked"}:
            overall = "High" if "High" in levels else "Medium" if "Medium" in levels else "Low"
        return {"overall_level": overall, "items": normalized}

    def _local_risk_assessment(self, story: str, symbol_system: list[dict]) -> dict:
        active_symbols = [item["symbol"] for item in symbol_system if item["level"] != "禁用符号"]
        forbidden_symbols = [item["symbol"] for item in symbol_system if item["level"] == "禁用符号"]
        text = story + " ".join(active_symbols)
        items = []
        for rule in RISK_RULES:
            if any(term in text for term in rule["terms"]):
                items.append(
                    {
                        "risk": rule["risk"],
                        "level": rule["level"],
                        "reason": rule["reason"],
                        "suggestion": rule["suggestion"],
                    }
                )
        existing = {item["risk"] for item in items}
        for symbol in forbidden_symbols:
            if symbol not in existing:
                items.append(
                    {
                        "risk": symbol,
                        "level": "Low",
                        "reason": "该元素已被识别为禁用或谨慎使用符号，当前方案未将其作为主视觉。",
                        "suggestion": "保持在 avoid 列表中，不进入故事主叙事、主纹样或包装核心画面。",
                    }
                )
        levels = {item["level"] for item in items if item["level"] != "Low"}
        overall = "High" if "High" in levels else "Medium" if "Medium" in levels else "Low"
        return {"overall_level": overall, "items": items}

    def _score_direction(self, index, risk_assessment, sources) -> int:
        base = 92 - index * 4
        if risk_assessment["overall_level"] == "High":
            base -= 12
        elif risk_assessment["overall_level"] == "Medium":
            base -= 6
        if any(source.get("credibility_level") in {"A", "B"} for source in sources):
            base += 3
        return max(min(base, 100), 50)

    def _build_report_json(self, task, direction, sources) -> dict:
        demand = task.structured_report
        return {
            "ip_report_id": "",
            "theme": direction["name"],
            "one_sentence": direction["one_sentence"],
            "demand_summary": (
                f"面向{', '.join(demand.get('target_users', [])) or '目标用户'}，"
                f"用于{', '.join(demand.get('usage_scenarios', [])) or '文化礼赠场景'}，"
                f"品类包括{', '.join(demand.get('product_categories', [])) or '文创产品'}。"
            ),
            "core_story": direction["story_core"],
            "cultural_sources": sources[:5],
            "symbol_system": direction["symbol_system"],
            "visual_translation": direction["visual_translation"],
            "risk_assessment": direction["risk_assessment"],
            "recommendation": direction["recommendation"],
        }





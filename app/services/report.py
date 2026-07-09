from __future__ import annotations


def build_report_markdown(report: dict) -> str:
    symbols = report.get("symbol_system", [])
    visual = report.get("visual_translation", {})
    risks = report.get("risk_assessment", {})
    sources = report.get("cultural_sources", [])
    return "\n".join(
        [
            f"# {report['theme']}",
            "",
            f"**一句话概念**：{report['one_sentence']}",
            "",
            "## 项目需求关联",
            report.get("demand_summary", ""),
            "",
            "## 文化依据与资料来源",
            *[
                f"- {source['title']}（{source['source_id']}，可信度 {source.get('credibility_level', 'C')}）：{source['summary']}"
                for source in sources
            ],
            "",
            "## 故事内核",
            report.get("core_story", ""),
            "",
            "## 符号体系",
            *[
                f"- {item['level']}：{item['symbol']}，寓意：{item['meaning']}，用法：{'；'.join(item['design_usage'])}"
                for item in symbols
            ],
            "",
            "## 视觉转译建议",
            f"- 图案：{visual.get('pattern', {}).get('main_pattern', '')}",
            f"- 构图：{visual.get('pattern', {}).get('composition', '')}",
            f"- 色彩：{', '.join(color['name'] + ' ' + color['hex'] for color in visual.get('color', {}).get('main_colors', []))}",
            f"- 工艺：{', '.join(visual.get('craft', []))}",
            f"- 包装：{visual.get('packaging', '')}",
            f"- 文案：{visual.get('copywriting', '')}",
            "",
            "## 文化禁忌与风险提示",
            f"整体风险等级：{risks.get('overall_level', 'Low')}",
            *[
                f"- {item['level']}：{item['risk']}。{item['reason']} 建议：{item['suggestion']}"
                for item in risks.get("items", [])
            ],
        ]
    )




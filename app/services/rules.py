from __future__ import annotations

KEYWORD_EXPANSIONS: dict[str, dict[str, list[str]]] = {
    "西湖": {
        "strong_related": ["湖波", "柳枝", "三潭印月", "春水", "江南烟雨"],
        "medium_related": ["桥", "花窗", "游船", "荷花", "月色"],
        "weak_related": ["祥云", "团扇", "青瓷"],
        "risk_related": ["断桥悲情叙事", "白蛇传离别联想"],
    },
    "婚嫁": {
        "strong_related": ["并蒂莲", "喜鹊", "双喜", "同心结", "合卺"],
        "medium_related": ["鸳鸯", "团圆纹", "连理枝", "如意", "花好月圆"],
        "weak_related": ["牡丹", "祥云", "锦鲤"],
        "risk_related": ["孤雁", "残荷", "黑白祭祀配色", "离别典故"],
    },
    "丝绸": {
        "strong_related": ["丝线", "蚕茧", "辑里湖丝", "宋锦", "提花"],
        "medium_related": ["刺绣", "杭罗", "绸缎光泽", "织造纹理", "锦纹"],
        "weak_related": ["团扇", "香囊", "流苏"],
        "risk_related": ["廉价塑料感", "过度仿古纹样"],
    },
    "江南": {
        "strong_related": ["水乡", "柳岸", "花窗", "粉墙黛瓦", "清雅留白"],
        "medium_related": ["石桥", "雨巷", "园林", "青绿山水", "湖蓝"],
        "weak_related": ["茶席", "团扇", "瓷器"],
        "risk_related": ["地域泛化", "符号堆砌"],
    },
    "春日": {
        "strong_related": ["嫩柳绿", "浅粉", "新生", "春水", "花信"],
        "medium_related": ["燕归", "桃花", "轻风", "踏青", "月白"],
        "weak_related": ["缃色", "藕荷", "淡金"],
        "risk_related": ["残春", "凋零", "冷灰色调"],
    },
}

RISK_RULES = [
    {
        "terms": ["断桥", "悲情", "离别", "白蛇传"],
        "risk": "断桥悲情叙事",
        "level": "High",
        "reason": "容易与离别和悲剧情节产生联想，不适合婚庆伴手礼主叙事。",
        "suggestion": "改用西湖春水、并蒂莲、喜鹊或湖波作为主叙事。",
    },
    {
        "terms": ["孤雁", "孤鸟", "孤独"],
        "risk": "孤鸟意象",
        "level": "High",
        "reason": "孤独意象与婚礼祝福场景冲突。",
        "suggestion": "改用喜鹊成双、并蒂莲、同心结等成双成对符号。",
    },
    {
        "terms": ["残荷", "枯枝", "凋零"],
        "risk": "衰败意象",
        "level": "Medium",
        "reason": "会削弱春日新生和婚嫁祝福的积极情绪。",
        "suggestion": "改用初荷、春水、嫩柳和花信等新生意象。",
    },
    {
        "terms": ["黑白", "祭祀"],
        "risk": "黑白祭祀配色",
        "level": "High",
        "reason": "黑白祭祀联想不适合婚庆与礼赠场景。",
        "suggestion": "改用湖蓝、浅粉、米白、嫩柳绿和淡金。",
    },
    {
        "terms": ["龙凤", "大红大金", "皇家"],
        "risk": "礼制误用或审美同质化",
        "level": "Medium",
        "reason": "过度传统或皇家化表达可能与年轻化江南审美不匹配。",
        "suggestion": "降低饱和度，用清雅配色和局部淡金替代。",
    },
]

DIRECTION_TEMPLATES = [
    {
        "name": "西湖并蒂春禧",
        "type": "爱情祝福型",
        "symbols": ["并蒂莲", "湖波", "喜鹊", "柳枝"],
        "sentence": "用西湖春水与并蒂莲表达新人同心和春日新生。",
    },
    {
        "name": "江南双喜丝语",
        "type": "非遗工艺型",
        "symbols": ["丝线", "双喜", "蚕茧", "花窗"],
        "sentence": "以丝线连接新人、亲友和江南祝福。",
    },
    {
        "name": "南浔湖丝良缘",
        "type": "地域产业型",
        "symbols": ["辑里湖丝", "水乡", "古桥", "同心结"],
        "sentence": "用湖丝与水乡记忆表达地方产业和新人良缘。",
    },
    {
        "name": "春水喜鹊礼",
        "type": "现代生活型",
        "symbols": ["喜鹊", "春水", "嫩柳", "浅粉花信"],
        "sentence": "把春日轻快的祝福转译成适合年轻婚礼回礼的日常礼物。",
    },
    {
        "name": "湖蓝雅集",
        "type": "高端礼赠型",
        "symbols": ["湖波", "宋韵色", "花窗", "丝绸光泽"],
        "sentence": "以克制清雅的江南美学构建高端丝绸礼赠方案。",
    },
]


def merge_expansions(keywords: list[str]) -> dict[str, list[str]]:
    merged = {
        "strong_related": [],
        "medium_related": [],
        "weak_related": [],
        "risk_related": [],
    }
    for keyword in keywords:
        rules = KEYWORD_EXPANSIONS.get(keyword, {})
        for key in merged:
            for item in rules.get(key, []):
                if item not in merged[key]:
                    merged[key].append(item)
    return merged


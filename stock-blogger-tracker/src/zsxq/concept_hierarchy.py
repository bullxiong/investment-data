#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
两层概念体系映射
L1（一级概念）：大板块
L2（二级概念）：细分标签，可多个（逗号分隔），用于跨领域股票
canonical_taxonomy.json 路径：data/zsxq/canonical_taxonomy.json

使用方式：
  from src.zsxq.concept_hierarchy import infer_l1, normalize_l2, merge_similar_l2
"""

from typing import List, Tuple

# ─── L1 → L2 关键词映射 ────────────────────────────────────────
# 每个 L1 对应一组关键词，用于从 L2 标签中推断 L1
L1_KEYWORDS: dict = {
    "光伏": ["光伏", "太阳能", "光伏组件", "光伏设备", "光伏电池", "逆变器", "电池片", "胶膜", "背板", "支架"],
    "半导体": ["半导体", "芯片", "集成电路", "晶圆", "封测", "EDA", "存储", "功率器件",
              "碳化硅", "氮化镓", "IGBT", "ABF载板", "半导体材料", "半导体设备", "半导体代工",
              "电子元件", "PCB", "覆铜板", "电子布", "玻纤", "MLCC", "铜箔", "激光设备"],
    "人工智能": ["人工智能", "AI应用", "AI硬件", "AI算力", "算力", "大模型", "GPU", "服务器", "云计算",
                "数据中心", "机器学习", "AI全链", "AI智能体", "计算机"],
    "机器人": ["机器人", "人形机器人", "协作机器人", "丝杠", "减速机", "谐波",
               "电机", "灵巧手", "执行器", "宇树", "机器人散热", "T链"],
    "新能源": ["新能源", "储能", "锂电", "电池", "风电", "氢能", "燃料电池",
               "充电桩", "电解水", "钠电池", "SAF", "绿醇", "制氢", "可持续航空"],
    "军工": ["军工", "航空", "航天", "舰船", "导弹", "军贸", "国防", "战斗机",
              "燃气轮机", "航改燃", "涡轮叶片", "军工电子"],
    "医药": ["医药", "医疗", "生物", "创新药", "仿制药", "医械", "CXO", "中药", "siRNA"],
    "消费": ["消费", "食品饮料", "食品", "饮料", "餐饮", "零售", "品牌", "家电", "纺织",
              "服装", "美妆", "酒", "白酒", "茶饮", "保温杯", "黄金", "珠宝"],
    "工程机械": ["工程机械", "挖掘机", "起重机", "叉车", "装载机", "矿机", "农机", "农业机械"],
    "化工": ["化工", "新材料", "化纤", "涤纶", "染料", "TDI", "MDI", "散热", "硝化棉",
              "碳纤维", "锂盐", "磁材", "钛合金", "特种气体", "油气", "有色金属"],
    "通信": ["通信", "光纤", "光模块", "光缆", "光通信", "5G", "卫星", "CPO", "数据通信"],
    "汽车": ["汽车", "新能源汽车", "整车", "汽车零部件", "热管理", "智能驾驶",
              "激光雷达", "底盘", "汽车电子", "固态电池"],
    "金融": ["金融", "银行", "保险", "券商", "证券", "信托", "期货", "金融科技"],
    "互联网": ["互联网", "游戏", "传媒", "短视频", "电商", "SaaS", "软件", "出海"],
    "低空经济": ["低空", "无人机", "eVTOL", "飞行汽车", "商业航天"],
    "基建": ["基建", "电力设备", "电网", "变压器", "核电", "水电", "燃气", "特高压",
              "工程建设", "建材", "建筑"],
}

# L2 合并规则：相似标签归并（key → 合并后的标准名）
L2_MERGE_MAP: dict = {
    # 光伏细分
    "光伏设备及组件": "光伏设备",
    "光伏组件及设备": "光伏设备",
    "光伏电池设备": "光伏设备",
    "光伏材料": "光伏",
    "太阳能": "光伏",
    # 半导体细分
    "半导体设备及材料": "半导体设备",
    "半导体材料": "半导体",
    "芯片设计": "半导体",
    "集成电路": "半导体",
    "算力芯片": "AI算力",
    "AI算力芯片": "AI算力",
    # 机器人细分
    "人形机器人": "机器人",
    "工业机器人": "机器人",
    "协作机器人": "机器人",
    # 储能细分
    "锂电池储能": "储能",
    "电化学储能": "储能",
    # 通信细分
    "光纤光缆": "光通信",
    "光模块": "光通信",
    "CPO": "光通信",
    # 其他
    "新能源汽车": "汽车",
    "人工智能应用": "AI应用",
    "AI应用软件": "AI应用",
    "计算机应用": "AI应用",
    "计算机应用、人工智能": "AI应用",
    "AI应用、计算机": "AI应用",
    "AI应用、传媒": "AI应用",
    "传媒、游戏": "传媒游戏",
    "军工军贸": "军工",
    "军工、新材料": "军工",
    "新材料与化工": "化工新材料",
    "新材料": "化工新材料",
    "光伏设备、半导体设备、碳化硅": "光伏设备、半导体、碳化硅",
}

# ─── 数据文件路径（相对于项目根目录）──────────────────
_CANONICAL_PATH = "data/zsxq/canonical_taxonomy.json"


def infer_l1(l2_tags: str) -> str:
    """
    从 L2 标签字符串推断 L1 一级概念
    l2_tags: "光伏设备、半导体、碳化硅" 或 "机器人"
    返回: "光伏" 或 "半导体" 或 "跨领域"（匹配多个L1）
    """
    if not l2_tags:
        return "其他"

    matched_l1s = []
    text_lower = l2_tags.lower()

    for l1, keywords in L1_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                if l1 not in matched_l1s:
                    matched_l1s.append(l1)
                break

    if not matched_l1s:
        return "其他"
    if len(matched_l1s) == 1:
        return matched_l1s[0]
    # 多个L1：取前两个，用"/"分隔
    return "/".join(matched_l1s[:2])


def normalize_l2(raw_concept: str) -> str:
    """
    标准化 L2 标签：
    1. 应用合并规则
    2. 处理分隔符（中文顿号、逗号、斜杠）
    3. 去重、去空
    返回规范化后的逗号分隔标签字符串
    """
    if not raw_concept:
        return ""

    # 先整体检查合并规则
    merged = L2_MERGE_MAP.get(raw_concept.strip())
    if merged:
        return merged

    # 拆分多标签（支持顿号、逗号、斜杠、中文书名号分隔）
    import re
    parts = re.split(r'[、,，/／\s]+', raw_concept.strip())
    parts = [p.strip() for p in parts if p.strip()]

    # 对每个标签应用合并规则
    normalized = []
    seen = set()
    for p in parts:
        p = L2_MERGE_MAP.get(p, p)
        if p and p not in seen:
            normalized.append(p)
            seen.add(p)

    return "、".join(normalized) if normalized else raw_concept


def parse_concepts(concept_str: str) -> Tuple[str, str]:
    """
    解析概念字符串，返回 (l1, l2)
    l2 可能包含多个标签（顿号分隔）
    """
    l2 = normalize_l2(concept_str)
    l1 = infer_l1(l2)
    return l1, l2


def get_all_l1_values() -> List[str]:
    """返回所有 L1 一级概念列表"""
    return list(L1_KEYWORDS.keys()) + ["其他"]


# ─── 权威概念体系（从人工确认的股票池提取）──────────────────────
# canonical_taxonomy.json 存储 {L1: [L2, ...]} 结构
# match_to_canonical() 用于将 AI 返回的概念尝试匹配到已有体系

def _load_canonical() -> dict:
    """加载权威概念体系"""
    import json
    from pathlib import Path
    f = Path(_CANONICAL_PATH)
    if f.exists():
        return json.loads(f.read_text(encoding='utf-8'))
    return {}


def match_to_canonical(ai_concept: str) -> Tuple[str, str, bool]:
    """
    将 AI 返回的概念字符串与权威体系进行匹配。
    返回: (l1, l2, matched)
      - matched=True  表示命中已有体系，可直接入池（无需人工审核）
      - matched=False 表示未命中，需要走人工审核流程
    匹配策略（按优先级）：
      1. 完全匹配 L2
      2. L2 包含关系（ai_concept 是已有 L2 的子集或超集）
      3. 关键词匹配（L2 的主要词出现在 ai_concept 中）
    """
    if not ai_concept:
        return "其他", ai_concept, False

    canon = _load_canonical()
    if not canon:
        # 没有权威体系则回退到关键词推断，不视为命中
        l1, l2 = parse_concepts(ai_concept)
        return l1, l2, False

    # 构建所有 (l1, l2) 对的平铺列表
    all_pairs = [(l1, l2) for l1, l2s in canon.items() for l2 in l2s]

    ai_lower = ai_concept.strip().lower()

    # 1. 完全匹配
    for l1, l2 in all_pairs:
        if ai_lower == l2.lower():
            return l1, l2, True

    # 2. 包含匹配：已有 L2 完整出现在 AI 概念里（或反向）
    best_l1, best_l2, best_len = None, None, 0
    for l1, l2 in all_pairs:
        l2_lower = l2.lower()
        if l2_lower in ai_lower or ai_lower in l2_lower:
            if len(l2_lower) > best_len:
                best_l1, best_l2, best_len = l1, l2, len(l2_lower)
    if best_l2:
        return best_l1, best_l2, True

    # 3. 关键词匹配：L2 主词（取顿号分割后各段）出现在 AI 概念里
    import re
    for l1, l2 in all_pairs:
        parts = re.split(r'[、,，/]', l2)
        main = max(parts, key=len).strip().lower() if parts else ''
        if main and len(main) >= 2 and main in ai_lower:
            return l1, l2, True

    # 未命中 → 用关键词推断 L1，但标记为需审核
    l1, l2 = parse_concepts(ai_concept)
    return l1, l2, False

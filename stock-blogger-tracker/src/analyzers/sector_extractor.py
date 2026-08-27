# -*- coding: utf-8 -*-
"""
板块/概念识别引擎 — 从帖子文本中提取提到的行业、概念和股票。

策略（按优先级）：
  1. 精确匹配 — 行业名/概念名/股票名直接出现在文本中
  2. 别名匹配 — 利用别名词典查找股票
  3. jieba 分词匹配 — 文本分词后逐词比对词典
  4. 子串匹配兜底 — 对仍未匹配的板块名做子串搜索

板块情感检测：
  通过位置感知的搭配词模式，判断每个板块的情感方向（看多/看空/中性），
  支持消歧规则和否定翻转。
"""

import json
import os
import logging
import re

import jieba

from src.preprocess.text_cleaner import TextCleaner

logger = logging.getLogger(__name__)


# ================================================================
# 情感检测规则
# ================================================================

# 搭配模式：紧邻组合决定方向
PROXIMITY_PATTERNS = {
    # 看空搭配 — ⚠️ 关键词情感判断已标记为废弃。
    # 2026-06-27: "割肉"/"按"/"砸"存在大量歧义（"割肉A来买B"=B bullish）
    # 情感方向改为 LLM 判断（llm_sentiment.py），关键词仅做概念匹配
    "砸": ("bearish", ["sector"]),
    "减仓": ("bearish", ["sector"]),
    "清仓": ("bearish", ["sector"]),
    "不看好": ("bearish", ["sector"]),
    "不玩": ("bearish", ["sector"]),
    "不适合": ("bearish", ["sector"]),
    "太弱": ("bearish", ["sector"]),
    "拉跨": ("bearish", ["sector"]),
    "拉胯": ("bearish", ["sector"]),
    "避": ("bearish", ["sector"]),
    "砍": ("bearish", ["sector"]),
    "跌": ("bearish", ["sector"]),
    "跳水": ("bearish", ["sector"]),
    "走弱": ("bearish", ["sector"]),
    "弱势": ("bearish", ["sector"]),
    "萎靡": ("bearish", ["sector"]),
    "按": ("bearish", ["sector"]),
    "收绿": ("bearish", ["sector"]),
    "大阴": ("bearish", ["sector"]),
    "破位": ("bearish", ["sector"]),
    # 看多搭配
    "吸": ("bullish", ["sector"]),
    "加仓": ("bullish", ["sector"]),
    "满仓": ("bullish", ["sector"]),
    "看好": ("bullish", ["sector"]),
    "炸裂": ("bullish", ["sector"]),
    "强": ("bullish", ["sector"]),
    "新高": ("bullish", ["sector"]),
    "突破": ("bullish", ["sector"]),
    "龙头": ("bullish", ["sector"]),
    "业绩好": ("bullish", ["sector"]),
    "最炸": ("bullish", ["sector"]),
    "最强": ("bullish", ["sector"]),
    "爆发": ("bullish", ["sector"]),
    "领涨": ("bullish", ["sector"]),
    "翻倍": ("bullish", ["sector"]),
    "涨": ("bullish", ["sector"]),
    "拉升": ("bullish", ["sector"]),
    "走强": ("bullish", ["sector"]),
    "强势": ("bullish", ["sector"]),
    "不错": ("bullish", ["sector"]),
    "起": ("bullish", ["sector"]),
    "V回": ("bullish", ["sector"]),
    "收红": ("bullish", ["sector"]),
    "收阳": ("bullish", ["sector"]),
    "大阳": ("bullish", ["sector"]),
    "继续": ("bullish", ["sector"]),
    "反弹": ("bullish", ["sector"]),
}

# 消歧规则：特定搭配覆盖默认
DISAMBIGUATION = {
    "砸": {"next_override": {"钱": "bullish", "锅": "bullish", "进去": "bullish", "资金": "bullish"}},
    "按": {"next_override": {"键": "neutral", "钮": "neutral", "时": "neutral"}},
}

# 否定词列表
NEGATIONS = ["不", "没", "并非", "不是"]


def _detect_sector_sentiment(text, sector_name, sector_pos):
    """检测文本中某个板块的情感方向。

    Parameters
    ----------
    text : str
        原始文本。
    sector_name : str
        板块名称。
    sector_pos : int
        板块在文本中的起始位置（通过 text.find() 获得）。

    Returns
    -------
    tuple[str, list[str]]
        (sentiment, keywords) 情感方向 + 匹配到的关键词列表。
    """
    # 取前后 ±15 字窗口
    window_start = max(0, sector_pos - 15)
    window_end = min(len(text), sector_pos + len(sector_name) + 15)
    window = text[window_start:window_end]

    keywords = []
    sentiment = "neutral"

    # 第一步：检测主模式
    for pattern_word, (direction, _) in PROXIMITY_PATTERNS.items():
        if pattern_word in window:
            # 消歧检查
            override = DISAMBIGUATION.get(pattern_word, {}).get("next_override", {})
            idx = window.find(pattern_word)
            after = window[idx + len(pattern_word):idx + len(pattern_word) + 5].strip()
            # 检查下一个词（取前2字）是否在覆盖规则中
            for check_len in [2, 3]:
                check = after[:check_len] if len(after) >= check_len else after
                if check in override:
                    sentiment = override[check]
                    keywords.append(pattern_word)
                    return (sentiment, keywords)
            # 没有覆盖规则 → 使用默认方向
            sentiment = direction
            keywords.append(pattern_word)
            break  # 找到第一个匹配就停

    # 第二步：否定翻转检测
    if sentiment != "neutral":
        # 在窗口中找否定词，且在板块名之前
        for neg in NEGATIONS:
            neg_idx = window.find(neg)
            if neg_idx != -1:
                # 否定词必须在板块名之前
                sector_start_in_window = sector_pos - window_start
                if neg_idx < sector_start_in_window:
                    # 检查否定词和板块之间是否有我们匹配到的模式词
                    for pw in PROXIMITY_PATTERNS:
                        if pw in window[neg_idx:]:
                            pw_direction = PROXIMITY_PATTERNS[pw][0]
                            if pw_direction == sentiment:
                                return ("neutral", keywords)
        return (sentiment, keywords)

    # 第三步：单独检测否定翻转（没有正面模式词的情况）
    # "不看空半导体" — 没有看多词，但有否定+看空词
    for neg in NEGATIONS:
        neg_idx = window.find(neg)
        if neg_idx != -1:
            sector_start_in_window = sector_pos - window_start
            if neg_idx < sector_start_in_window:
                for pw in PROXIMITY_PATTERNS:
                    if PROXIMITY_PATTERNS[pw][0] == "bearish" and pw in window[neg_idx:]:
                        keywords.append(pw)
                        return ("neutral", keywords)

    return (sentiment, keywords)


class SectorExtractor:
    """从 A 股相关帖子文本中识别行业、概念和股票。"""

    def __init__(self, data_dir=None):
        """
        Parameters
        ----------
        data_dir : str, optional
            stock_db 目录的绝对路径。默认自动推断。
        """
        if data_dir is None:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            data_dir = os.path.join(project_root, "data", "stock_db")

        self.data_dir = data_dir
        self._load_data()
        self._build_jieba_dict()

    # ----------------------------------------------------------------
    # 数据加载
    # ----------------------------------------------------------------

    def _load_data(self):
        """加载所有词典数据并构建查找结构。"""
        # —— 行业名 ——
        industry_list = self._load_json("industry_names.json")
        self.industry_names = {}
        for item in industry_list:
            self.industry_names[item["name"]] = {
                "code": item["code"],
                "level": "L2",
            }

        # —— 概念名 ——
        concept_list = self._load_json("concept_names.json")
        self.concept_names = {}
        for item in concept_list:
            self.concept_names[item["name"]] = {
                "code": item["code"],
            }

        # —— 股票 ——
        stock_list = self._load_json("stocks.json")
        self.stock_info = {}
        self.stock_by_name = {}
        for s in stock_list:
            name = s["name"]
            code = s["code"]
            self.stock_info[code] = s
            self.stock_by_name[name] = code

        # —— 别名 ——
        self.alias_to_code = self._load_json("alias.json")

        # —— 合并板块 (行业 + 概念 + 社区概念) ——
        self.sector_meta = {}
        for name, info in self.industry_names.items():
            self.sector_meta[name] = {
                "type": "industry",
                "level": info["level"],
                "code": info["code"],
                "parent": None,
            }
        for name, info in self.concept_names.items():
            self.sector_meta[name] = {
                "type": "concept",
                "level": None,
                "code": info["code"],
                "parent": None,
            }

        # —— 社区概念 (community_concepts.json) ——
        self._synonym_map = {}  # synonym → canonical_name
        community_path = os.path.join(self.data_dir, "community_concepts.json")
        self.community_parents = {}
        if os.path.exists(community_path):
            community = self._load_json_file(community_path)
            for cname, cinfo in community.items():
                if cname.startswith('_'):
                    continue
                parent = cinfo.get('parent', '其他')
                self.sector_meta[cname] = {
                    "type": "community",
                    "level": None,
                    "code": "",
                    "parent": parent,
                }
                self.community_parents[cname] = parent
                # Register keywords for matching
                for kw in cinfo.get('keywords', []):
                    jieba.add_word(kw)
                    if kw != cname:
                        self._synonym_map[kw] = cname
                # Register synonyms
                for syn in cinfo.get('synonyms', []):
                    jieba.add_word(syn)
                    self._synonym_map[syn] = cname
                # Also add the concept name itself
                jieba.add_word(cname)

        # 排序：按名称长度降序，优先匹配长名称
        self._sorted_sector_names = sorted(
            self.sector_meta.keys(), key=lambda x: -len(x)
        )
        self._sorted_stock_names = sorted(
            self.stock_by_name.keys(), key=lambda x: -len(x)
        )
        self._sorted_aliases = sorted(
            self.alias_to_code.keys(), key=lambda x: -len(x)
        )

    def _load_json(self, filename):
        path = os.path.join(self.data_dir, filename)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_json_file(self, filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    # ----------------------------------------------------------------
    # jieba 自定义词典
    # ----------------------------------------------------------------

    def _build_jieba_dict(self):
        """将所有行业、概念、股票名加入 jieba 分词词典，提升分词精度。"""
        for name in self.industry_names:
            jieba.add_word(name)
        for name in self.concept_names:
            jieba.add_word(name)
        for name in self.stock_by_name:
            jieba.add_word(name)
        for alias in self.alias_to_code:
            jieba.add_word(alias)

    # ----------------------------------------------------------------
    # 上下文质量分类
    # ----------------------------------------------------------------

    def _classify_context(self, text, pos, concept_name):
        """判断概念词的上下文是主动讨论还是被动列举。

        纯规则判断，不调用 LLM。

        Parameters
        ----------
        text : str
            全文。
        pos : int
            概念词在全文中的起始位置（text.find 结果）。
        concept_name : str
            概念名称。

        Returns
        -------
        str
            "primary" / "active" / "passive"
        """
        length = len(concept_name)

        # 检查当前位置是否在顿号/逗号分隔的列表中
        before = text[max(0, pos - 20):pos]
        after = text[pos + length:pos + length + 20]
        # 如果前后 10 字内有 `、` 或 `，` 或 `,`，很可能是列表项
        list_pattern = (
            re.search(r'[、，,]', before[-10:]) is not None
            and re.search(r'[、，,]', after[:10]) is not None
        )
        if list_pattern:
            return "passive"

        # 计算全文中该概念的提及次数
        count = text.count(concept_name)
        if count >= 3:
            # 检查概念是否在标题/首段（前 200 字符）
            head = text[:200]
            if concept_name in head:
                return "primary"
            return "primary"
        elif count >= 2:
            return "active"
        return "passive"

    # ----------------------------------------------------------------
    # 核心提取
    # ----------------------------------------------------------------

    def extract(self, text, pre_clean=True):
        """从单条文本中提取板块和股票，含情感方向。

        Parameters
        ----------
        text : str
            帖子正文。
        pre_clean : bool
            是否在提取前调用 TextCleaner 移除 @mention/转发标记等噪音（默认 True）。

        Returns
        -------
        dict
            {
                "sectors": [
                    {"name": "光纤", "type": "concept", "sentiment": "bearish", "keywords": ["砸"]},
                    ...
                ],
                "stocks": [...]
            }
        """
        if not text:
            return {"sectors": [], "stocks": []}

        # TD5: 移除 @mention / 转发标记 / 引用链噪音
        if pre_clean:
            text = TextCleaner.clean(text)

        found_sectors = {}  # name → {name, type, level, matched_text, sector_pos}
        found_sector_positions = {}  # name → position in text
        found_stocks = {}
        covered_ranges = []

        # —— 策略 1：精确匹配 ——
        self._exact_match_sectors(text, found_sectors, found_sector_positions)
        self._exact_match_stocks(text, found_stocks, covered_ranges)

        # —— 策略 2：别名匹配 ——
        self._alias_match(text, found_stocks, covered_ranges)

        # —— 策略 3：jieba 分词匹配 ——
        self._jieba_match(text, found_sectors, found_sector_positions, found_stocks)

        # —— 策略 4：子串匹配兜底 ——
        self._substring_fallback(text, found_sectors, found_sector_positions)

        # —— 情感检测 ——
        sectors_with_sentiment = []
        seen_canonical = set()  # dedup after synonym resolution
        for name, info in found_sectors.items():
            # Synonym resolution: map to canonical name
            canonical = self._synonym_map.get(name, name)
            if canonical in seen_canonical:
                continue
            seen_canonical.add(canonical)

            pos = text.find(name)
            if pos == -1:
                pos = found_sector_positions.get(name, 0)
            sentiment, keywords = _detect_sector_sentiment(text, name, pos)
            # ⚠️ 关键词情感已废弃，全量由 LLM 判断
            sentiment = "neutral"

            # Get parent if this is a community concept
            parent = self.community_parents.get(canonical, None)

            # occurrences: how many times this concept appears in the full text
            occurrences = text.count(canonical)
            # context_quality: passive/active/primary based on heuristic
            context_quality = self._classify_context(text, pos, canonical)

            sectors_with_sentiment.append({
                "name": canonical,
                "type": info["type"],
                "level": info.get("level"),
                "sentiment": sentiment,
                "keywords": keywords,
                "parent": parent,
                "occurrences": occurrences,
                "context_quality": context_quality,
            })

        return {
            "sectors": sorted(
                sectors_with_sentiment,
                key=lambda x: (x["type"], x["name"]),
            ),
            "stocks": sorted(
                list(found_stocks.values()),
                key=lambda x: x["code"],
            ),
        }

    @staticmethod
    def _find_all_positions(text, substring):
        positions = []
        start = 0
        while True:
            idx = text.find(substring, start)
            if idx == -1:
                break
            positions.append((idx, idx + len(substring)))
            start = idx + 1
        return positions

    @staticmethod
    def _overlaps(ranges, new_start, new_end):
        for s, e in ranges:
            if new_start < e and new_end > s:
                return True
        return False

    # ----------------------------------------------------------------
    # 策略 1：精确匹配
    # ----------------------------------------------------------------

    def _exact_match_sectors(self, text, found, positions):
        text_lower = text.lower()
        for name in self._sorted_sector_names:
            if name in found:
                continue
            meta = self.sector_meta[name]
            # Community concepts: case-insensitive match for English/mixed terms
            if meta["type"] == "community" and name != name.lower():
                if name.lower() in text_lower:
                    found[name] = {
                        "name": name,
                        "type": meta["type"],
                        "level": meta["level"],
                        "matched_text": name,
                    }
                    positions[name] = text_lower.find(name.lower())
                    continue
            # Also check keywords/synonyms for community concepts
            if meta["type"] == "community":
                matched = False
                for kw, canonical in self._synonym_map.items():
                    if canonical == name:
                        kw_lower = kw.lower()
                        if kw_lower in text_lower:
                            found[name] = {
                                "name": name,
                                "type": meta["type"],
                                "level": meta["level"],
                                "matched_text": kw,
                            }
                            positions[name] = text_lower.find(kw_lower)
                            matched = True
                            break
                if matched:
                    continue
            if name in text:
                found[name] = {
                    "name": name,
                    "type": meta["type"],
                    "level": meta["level"],
                    "matched_text": name,
                }
                positions[name] = text.find(name)

    def _exact_match_stocks(self, text, found, covered):
        for name in self._sorted_stock_names:
            code = self.stock_by_name[name]
            if code in found:
                continue
            if name in text:
                idx = text.find(name)
                covered.append((idx, idx + len(name)))
                found[code] = {
                    "code": code,
                    "name": name,
                    "matched_text": name,
                }

    # ----------------------------------------------------------------
    # 策略 2：别名匹配
    # ----------------------------------------------------------------

    def _alias_match(self, text, found, covered):
        for alias in self._sorted_aliases:
            code = self.alias_to_code[alias]
            if code in found:
                continue
            if len(alias) <= 2:
                continue
            idx = text.find(alias)
            if idx == -1:
                continue
            if self._overlaps(covered, idx, idx + len(alias)):
                continue
            stock = self.stock_info.get(code, {})
            stock_name = stock.get("name", alias)
            covered.append((idx, idx + len(alias)))
            found[code] = {
                "code": code,
                "name": stock_name,
                "matched_text": alias,
            }

    # ----------------------------------------------------------------
    # 策略 3：jieba 分词匹配
    # ----------------------------------------------------------------

    def _jieba_match(self, text, found_sectors, sector_positions, found_stocks):
        words = jieba.lcut(text)
        for word in words:
            word = word.strip()
            if not word:
                continue

            # 板块
            if word in self.sector_meta and word not in found_sectors:
                meta = self.sector_meta[word]
                found_sectors[word] = {
                    "name": word,
                    "type": meta["type"],
                    "level": meta["level"],
                    "matched_text": word,
                }
                sector_positions[word] = text.find(word)

            # 股票
            if word in self.stock_by_name:
                code = self.stock_by_name[word]
                if code not in found_stocks:
                    found_stocks[code] = {
                        "code": code,
                        "name": word,
                        "matched_text": word,
                    }

            # 股票别名
            if len(word) > 2 and word in self.alias_to_code:
                code = self.alias_to_code[word]
                if code not in found_stocks:
                    stock = self.stock_info.get(code, {})
                    found_stocks[code] = {
                        "code": code,
                        "name": stock.get("name", word),
                        "matched_text": word,
                    }

    # ----------------------------------------------------------------
    # 策略 4：子串匹配兜底
    # ----------------------------------------------------------------

    def _substring_fallback(self, text, found, positions):
        for name in self._sorted_sector_names:
            if name in found:
                continue
            if name in text:
                meta = self.sector_meta[name]
                found[name] = {
                    "name": name,
                    "type": meta["type"],
                    "level": meta["level"],
                    "matched_text": name,
                }
                positions[name] = text.find(name)

    # ----------------------------------------------------------------
    # 批量处理
    # ----------------------------------------------------------------

    def extract_from_posts(self, posts):
        """批量处理帖子列表，返回带 sectors/stocks 字段的结果。"""
        results = []
        for p in posts:
            content = p.get("content", "")
            result = dict(p)
            extracted = self.extract(content)
            result["sectors"] = extracted["sectors"]
            result["stocks"] = extracted["stocks"]
            results.append(result)
        return results


# ================================================================
# 自测
# ================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    default_data_dir = os.path.join(project_root, "data", "stock_db")

    extractor = SectorExtractor(data_dir=default_data_dir)

    # 核心测试
    test_text = (
        '截至6月18日，市场已形成清晰共识：先进封装正从\u201c概念扩产\u201d全面转向'
        '\u201c客户绑定+产能落地\u201d的兑现阶段，焦点集中于国内CoWoS-L产线将在'
        '2026年下半年进入量产窗口。关注：甬矽电子/汇成股份(CoWoS-L布局'
        '领先，业绩弹性大），长电科技/通富微电/华天科技（国内封测龙头），'
        '芯碁微装（先进封装用直写光刻设备核心供应商），华海诚科（先进封装'
        '环氧塑封料龙头），伟测科技（第三方高端测试）'
    )

    result = extractor.extract(test_text)

    print("=" * 60)
    print("板块识别结果（含情感）")
    print("=" * 60)
    print(f"\n识别到 {len(result['sectors'])} 个板块：")
    for s in result["sectors"]:
        level_str = f" [{s.get('level', '')}]" if s.get("level") else ""
        kw_str = f" (关键词: {s.get('keywords', [])})" if s.get("keywords") else ""
        print(f"  - {s['type']}{level_str}: {s['name']} → {s['sentiment']}{kw_str}")

    print(f"\n识别到 {len(result['stocks'])} 只股票：")
    for s in result["stocks"]:
        print(f"  - {s['code']} {s['name']}  (匹配文本: {s['matched_text']})")

    # 情感专项测试
    print("\n" + "=" * 60)
    print("板块情感专项测试")
    print("=" * 60)

    # 测试 1：基本看空/看多
    r1 = extractor.extract("砸光纤，吸存储，减仓半导体")
    for s in r1["sectors"]:
        print(f"  测试1: {s['name']}: {s['sentiment']} ({s['keywords']})")
    # 预期: 光纤:bearish, 存储:bullish, 半导体:bearish

    # 测试 2：否定翻转
    r2 = extractor.extract("不看空半导体，继续看好存储")
    for s in r2["sectors"]:
        print(f"  测试2: {s['name']}: {s['sentiment']}")
    # 预期: 半导体:neutral, 存储:bullish

    # 测试 3：消歧
    r3 = extractor.extract("砸钱进存储，光纤太弱了")
    for s in r3["sectors"]:
        print(f"  测试3: {s['name']}: {s['sentiment']} ({s['keywords']})")
    # 预期: 存储:bullish (砸钱被消歧), 光纤:bearish

    # 测试 4：多关键词
    r4 = extractor.extract("存储继续炸裂，龙头新高突破")
    for s in r4["sectors"]:
        print(f"  测试4: {s['name']}: {s['sentiment']} ({s['keywords']})")
    # 预期: 存储:bullish

    # 测试 5：无信号
    r5 = extractor.extract("光纤和存储今天表现一般")
    for s in r5["sectors"]:
        print(f"  测试5: {s['name']}: {s['sentiment']}")
    # 预期: 光纤:neutral, 存储:neutral

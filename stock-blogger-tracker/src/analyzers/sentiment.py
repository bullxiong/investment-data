# -*- coding: utf-8 -*-
"""
情感判断引擎 — 从帖子文本判断博主对股票的态度（看多/看空/中性）。

纯规则引擎，无外部 API 依赖。
对每只股票在文本中的出现位置提取 ±20 字上下文窗口，
在窗口内匹配情感关键词并计分，支持转折词翻转和特殊句型检测。
"""

import re
import logging

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 情感词典（内置）
# ═══════════════════════════════════════════════════════════════

# 看多关键词
_BULLISH_KEYWORDS = frozenset([
    "利好", "突破", "新高", "低吸", "布局", "看好", "关注",
    "推荐", "领涨", "龙头", "爆发", "翻倍", "超预期",
    "空间大", "弹性大", "产能落地", "量产", "拐点", "底部",
    # 扩展
    "产能", "产能持续扩张", "业绩弹性", "扩张", "兑现",
    "受益", "核心供应商", "领先", "龙头地位", "爆发",
    "重估", "利润弹性", "高价值", "巨大产能",
    "新high", "newhigh", "new high",  # 博主常用
    "高看",  # "高看几眼"
])

# 看空关键词
_BEARISH_KEYWORDS = frozenset([
    "利空", "暴跌", "出货", "减持", "风险", "高估",
    "泡沫", "见顶", "警惕", "回避", "回调", "崩盘",
    # 扩展
    "大跌", "阴包阳", "大阴棒子", "砸", "砸了",
    "差一点", "涨的少跌的多",
    "洗的死去活来", "风险大",
])

# 转折/否定词 — 翻转后续情感方向
_NEGATION_KEYWORDS = frozenset([
    "但", "但是", "然而", "虽然", "不是", "并非",
    "不", "没", "没有", "并非", "并不",
])

# 反讽/否定句式 — 发现这些句子整体视为 bearish 信号
_SARCASM_PATTERNS = [
    re.compile(r"刷存在感呢你"),
    re.compile(r"真不懂"),
    re.compile(r"我也不懂"),
    re.compile(r"没有研究.*不懂"),
]

# 道歉句式 — 道歉 = 转看多（之前的看空被打脸）
_APOLOGY_RE = re.compile(r"给(.{1,8}?)(?:锂业|光电|科技|电子|股份|数据|微电|电科)?道歉")

# 新高句式
_NEWHIGH_PATTERNS = [
    re.compile(r"(?:都?\s*)?新高(?:了)?"),
    re.compile(r"new\s*high", re.IGNORECASE),
]


def _find_stock_positions(text, stock_name):
    """查找股票名称在文本中的所有出现位置。

    Returns
    -------
    list of (start, end) tuples
    """
    positions = []
    start = 0
    while True:
        idx = text.find(stock_name, start)
        if idx == -1:
            break
        positions.append((idx, idx + len(stock_name)))
        start = idx + 1
    return positions


def _extract_context(text, stock_name, start, end):
    """提取股票名称在文本中某次出现的 ±20 字上下文窗口。

    Parameters
    ----------
    text : str
    stock_name : str
    start : int — 股票名起始位置
    end : int — 股票名结束位置

    Returns
    -------
    (before, after) — 前文、后文各最多 20 字
    """
    ctx_start = max(0, start - 20)
    ctx_end = min(len(text), end + 20)
    before = text[ctx_start:start]
    after = text[end:ctx_end]
    return before, after


def _tokenize_keywords(text_segment):
    """在文本片段中查找匹配的情感关键词。

    对多字关键词（如 "产能落地"、"弹性大"）优先匹配长词，
    避免被短词（如 "产能"、"弹性"）提前消费。

    Returns
    -------
    list of (keyword, score) tuples — score: +1 for bullish, -1 for bearish
    """
    result = []
    # 按长度降序排列关键词，优先匹配长词
    all_bullish = sorted(_BULLISH_KEYWORDS, key=lambda x: -len(x))
    all_bearish = sorted(_BEARISH_KEYWORDS, key=lambda x: -len(x))

    # 记录已匹配的字符区间，避免重复
    covered = set()

    for kw in all_bullish:
        start = 0
        while True:
            idx = text_segment.find(kw, start)
            if idx == -1:
                break
            # 检查是否与已匹配区间重叠
            kw_range = set(range(idx, idx + len(kw)))
            if not kw_range & covered:
                result.append((kw, +1))
                covered.update(kw_range)
            start = idx + 1

    for kw in all_bearish:
        start = 0
        while True:
            idx = text_segment.find(kw, start)
            if idx == -1:
                break
            kw_range = set(range(idx, idx + len(kw)))
            if not kw_range & covered:
                result.append((kw, -1))
                covered.update(kw_range)
            start = idx + 1

    return result


def _apply_negation(keywords_with_scores, context_text):
    """按转折词翻转后续关键词的情感方向。

    转折词（如 '但'、'不是'）之后的所有关键词情感方向翻转。

    Parameters
    ----------
    keywords_with_scores : list of (keyword, score) — score +1/-1
    context_text : str — 上下文文本

    Returns
    -------
    list of (keyword, adjusted_score)
    """
    if not keywords_with_scores:
        return keywords_with_scores

    # 找到转折词在上下文中的位置
    negation_positions = []
    for nw in _NEGATION_KEYWORDS:
        start = 0
        while True:
            idx = context_text.find(nw, start)
            if idx == -1:
                break
            negation_positions.append(idx)
            start = idx + 1

    if not negation_positions:
        return keywords_with_scores

    # 排序（可能有多个转折词）
    negation_positions.sort()

    # 对每个关键词，检查它是否在某个转折词之后
    result = []
    for kw, score in keywords_with_scores:
        # 找到关键词在上下文中的位置
        kw_pos = context_text.find(kw)
        # 检查是否有转折词在它前面
        flip = False
        for np in negation_positions:
            if np < kw_pos:
                flip = not flip  # 依次翻转
        if flip:
            result.append((kw, -score))
        else:
            result.append((kw, score))

    return result


class SentimentAnalyzer:
    """情感判断引擎 — 从帖子文本判断博主对股票的态度。

    纯规则引擎，内置情感词典，无外部 API 依赖。
    支持看多/看空关键词匹配、转折词翻转、道歉体检测、反讽检测。

    Examples
    --------
    >>> analyzer = SentimentAnalyzer()
    >>> result = analyzer.analyze("协创数据都新高了", [{"code": "300857", "name": "协创数据"}])
    >>> result["per_stock"]["300857"]["sentiment"]
    'bullish'
    """

    def __init__(self, use_enhanced=False):
        """初始化情感分析器。

        Parameters
        ----------
        use_enhanced : bool
            是否启用 LLM 增强引擎，对规则引擎返回 neutral 的股票进行二次判断。
        """
        self.use_enhanced = use_enhanced
        if use_enhanced:
            from src.analyzers.llm_sentiment import LLMSentimentAnalyzer
            self.enhanced = LLMSentimentAnalyzer()

    def analyze(self, text, stocks, uid=None):
        """对帖文中提到的每只股票判断情感。

        Parameters
        ----------
        text : str
            帖子正文。
        stocks : list[dict]
            股票列表，每项需包含 "code" 和 "name"。
            例如 [{"code": "688362", "name": "甬矽电子"}, ...]

        Returns
        -------
        dict
            {
                "overall": "bullish" | "bearish" | "neutral",
                "confidence": float,    # 0-1
                "per_stock": {
                    "688362": {
                        "sentiment": "bullish",
                        "confidence": 0.9,
                        "keywords": ["布局领先", "业绩弹性大"]
                    }
                }
            }
        """
        if not text or not stocks:
            return {
                "overall": "neutral",
                "confidence": 0.0,
                "per_stock": {},
            }

        per_stock = {}
        sentiments = []  # 收集每只股票的情感方向，用于计算 overall

        # 按名称长度降序排列，优先匹配长名称（避免 "汇成" 吞 "汇成股份"）
        sorted_stocks = sorted(stocks, key=lambda s: -len(s.get("name", "")))

        # 记录已匹配的文本区间，避免短名称与长名称重复匹配
        occupied_ranges = []

        for stock in sorted_stocks:
            code = stock.get("code", "")
            name = stock.get("name", "")
            if not name or not code:
                continue

            # 获取股票在文本中的所有出现位置
            positions = _find_stock_positions(text, name)

            # 过滤已被更长名称占用的位置
            valid_positions = []
            for s, e in positions:
                if not any(os <= s < oe or os < e <= oe
                           for os, oe in occupied_ranges):
                    valid_positions.append((s, e))
                    # 不占用区间——短名称可能是长名称的子串，
                    # 但如果没有被长名称占用，仍然可以独立匹配

            if not valid_positions:
                continue

            # 对每个出现位置的上下文窗口做关键词匹配
            all_keywords = []  # (keyword, score)
            for vs, ve in valid_positions:
                before, after = _extract_context(text, name, vs, ve)
                ctx = before + name + after

                # 匹配关键词
                kw_scores = _tokenize_keywords(ctx)

                # 应用转折词翻转
                kw_scores = _apply_negation(kw_scores, ctx)

                all_keywords.extend(kw_scores)

            # 汇总分数
            if not all_keywords:
                # 无关键词 → 检查特殊句式
                special = self._check_special_patterns(text, name)
                if special:
                    per_stock[code] = special
                    sentiments.append(special["sentiment"])
                else:
                    per_stock[code] = {
                        "sentiment": "neutral",
                        "confidence": 0.0,
                        "keywords": [],
                    }
                    sentiments.append("neutral")
                continue

            total_score = sum(score for _, score in all_keywords)
            keyword_count = len(all_keywords)

            # 判断情感
            if total_score > 0:
                sentiment = "bullish"
            elif total_score < 0:
                sentiment = "bearish"
            else:
                sentiment = "neutral"

            # 置信度
            confidence = round(min(1.0, abs(total_score) / (keyword_count + 1)), 2)

            # 去重关键词但保留顺序
            seen_kw = set()
            unique_kw = []
            for kw, _ in all_keywords:
                if kw not in seen_kw:
                    seen_kw.add(kw)
                    unique_kw.append(kw)

            per_stock[code] = {
                "sentiment": sentiment,
                "confidence": confidence,
                "keywords": unique_kw,
            }
            sentiments.append(sentiment)

        # ── Enhanced: LLM补刀 neutral 股票 ──
        if self.use_enhanced and self.enhanced:
            neutral_stocks = [
                s for s in stocks
                if per_stock.get(s.get('code', ''), {}).get('sentiment') == 'neutral'
            ]
            if neutral_stocks:
                enhanced = self.enhanced.analyze_post(
                    text,
                    sectors=[],
                    stock_names=[s['name'] for s in neutral_stocks],
                    uid=uid,
                )
                for sname, info in enhanced.items():
                    # Map stock name back to code via neutral_stocks
                    for s in neutral_stocks:
                        if s['name'] == sname or sname in s['name']:
                            code = s['code']
                            per_stock[code] = {
                                'sentiment': info['sentiment'],
                                'confidence': info['confidence'],
                                'keywords': [info.get('evidence', '')],
                            }
                            # Update sentiments list
                            idx = None
                            for i, st in enumerate(stocks):
                                if st.get('code') == code:
                                    idx = i
                                    break
                            if idx is not None and idx < len(sentiments):
                                sentiments[idx] = info['sentiment']
                            break

        # 计算整体情感
        if not sentiments:
            overall = "neutral"
            conf = 0.0
        else:
            bullish_count = sentiments.count("bullish")
            bearish_count = sentiments.count("bearish")
            neutral_count = sentiments.count("neutral")

            if bullish_count > bearish_count and bullish_count > neutral_count:
                overall = "bullish"
            elif bearish_count > bullish_count and bearish_count > neutral_count:
                overall = "bearish"
            elif bullish_count == bearish_count and bullish_count > 0:
                # 看多看空数量相等 → 按净分数判断
                total_score = 0
                total_kw = 0
                for sd in per_stock.values():
                    kw_count = len(set(sd.get("keywords", [])))
                    if sd["sentiment"] == "bullish":
                        total_score += sd["confidence"] * (kw_count + 1)
                    elif sd["sentiment"] == "bearish":
                        total_score -= sd["confidence"] * (kw_count + 1)
                    total_kw += kw_count
                overall = "bullish" if total_score > 0 else ("bearish" if total_score < 0 else "neutral")
            else:
                overall = "neutral"

            # 整体置信度：各股票置信度的加权平均
            confidences = [sd["confidence"] for sd in per_stock.values() if sd["sentiment"] != "neutral"]
            conf = sum(confidences) / len(confidences) if confidences else 0.0

        return {
            "overall": overall,
            "confidence": round(conf, 2),
            "per_stock": per_stock,
        }

    def _check_special_patterns(self, text, stock_name):
        """检查特殊句式（无普通关键词时）。

        Returns
        -------
        dict or None — 如果命中特殊模式，返回 sentiment 结果；否则 None
        """
        # 1. 道歉句式："给XX道歉" → bullish
        apology_match = _APOLOGY_RE.search(text)
        if apology_match:
            apologized = apology_match.group(1)
            # 检查道歉对象是否匹配当前股票名
            if (apologized in stock_name
                    or stock_name in apologized
                    or apologized + "锂业" == stock_name
                    or apologized + "光电" == stock_name
                    or apologized + "科技" == stock_name
                    or apologized + "电子" == stock_name
                    or apologized + "股份" == stock_name
                    or apologized + "数据" == stock_name
                    or apologized + "微电" == stock_name):
                return {
                    "sentiment": "bullish",
                    "confidence": 0.9,
                    "keywords": ["道歉(转看多)"],
                }
            # 通用道歉（如 "给永善锂业道歉"）
            if "道歉" in text:
                return {
                    "sentiment": "bullish",
                    "confidence": 0.85,
                    "keywords": ["道歉(转看多)"],
                }

        # 2. "都新高了" 句式 → bullish
        if "都新高了" in text or "都 newhigh" in text.lower() or "都 new high" in text.lower():
            return {
                "sentiment": "bullish",
                "confidence": 0.85,
                "keywords": ["新高"],
            }

        # 3. 反讽检测 → bearish
        for pattern in _SARCASM_PATTERNS:
            if pattern.search(text):
                return {
                    "sentiment": "bearish",
                    "confidence": 0.8,
                    "keywords": ["反讽/否定"],
                }

        # 4. 新高检测
        for pattern in _NEWHIGH_PATTERNS:
            if pattern.search(text):
                return {
                    "sentiment": "bullish",
                    "confidence": 0.75,
                    "keywords": ["新高"],
                }

        return None

    def analyze_posts(self, posts, stock_decoder=None):
        """批量分析帖子情感。

        Parameters
        ----------
        posts : list[dict]
            帖子列表，每项至少包含 "content" 字段。
            可以包含 "stocks" 字段（从 SectorExtractor/StockDecoder 解码的结果）。
        stock_decoder : StockDecoder, optional
            如果 posts 中没有预解码的 stocks，传入 StockDecoder 实例进行解码。

        Returns
        -------
        list[dict]
            每帖的分析结果，在原帖基础上附加 sentiment 字段。
        """
        results = []
        for p in posts:
            content = p.get("content", "")
            stocks = p.get("stocks", [])

            # 如果 posts 没有预解码的 stocks，且传入了 decoder，尝试解码
            if not stocks and stock_decoder:
                # 提取已识别的板块（如果有）
                sectors = p.get("sectors", None)
                decoded = stock_decoder.decode(
                    text=content,
                    sectors=sectors,
                    existing_stocks=None,
                )
                # 构建 stocks 列表供 analyze 使用
                stocks = []
                for m in decoded.get("matched", []):
                    stocks.append({
                        "code": m["code"],
                        "name": m["name"],
                    })

            sentiment = self.analyze(content, stocks)
            result = dict(p)
            result["sentiment"] = sentiment
            results.append(result)

        return results


# ================================================================
# 自测
# ================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(message)s")

    analyzer = SentimentAnalyzer()

    print("=" * 70)
    print("SentimentAnalyzer 自测")
    print("=" * 70)

    tests = []

    # ── 测试 1：协创数据新高 → bullish ──
    tests.append((
        "协创数据都新高了",
        [{"code": "300857", "name": "协创数据"}],
        "bullish",
        "协创数据新高",
    ))

    # ── 测试 2：道歉转看多 → bullish ──
    tests.append((
        "给永善锂业道歉！",
        [{"code": "603399", "name": "永善锂业"}],
        "bullish",
        "道歉转看多",
    ))

    # ── 测试 3：反讽 → bearish ──
    tests.append((
        "美银全球研究将美光科技目标价上调至1500美元。刷存在感呢你？",
        [{"code": "MUh5", "name": "美光科技"}],  # 美股，不在 stock_db 中
        "bearish",
        "反讽检测",
    ))

    # ── 测试 4：多只股票推荐关注 → bullish ──
    tests.append((
        "关注：甬矽电子/汇成股份(CoWoS-L布局领先)，长电科技/通富微电/华天科技",
        [
            {"code": "688362", "name": "甬矽电子"},
            {"code": "688403", "name": "汇成股份"},
            {"code": "600584", "name": "长电科技"},
            {"code": "002156", "name": "通富微电"},
            {"code": "002185", "name": "华天科技"},
        ],
        "bullish",
        "多股推荐关注",
    ))

    # ── 额外测试 5：风险提示 → bearish ──
    tests.append((
        "创业和kc目前风险大一些",
        [],
        "neutral",
        "风险提示(无股票)",
    ))

    # ── 额外测试 6：普通新高 ──
    tests.append((
        "协创数据newhigh 披萨吃起",
        [{"code": "300857", "name": "协创数据"}],
        "bullish",
        "newhigh句式",
    ))

    passed = 0
    failed = 0

    for i, (text, stocks, expected, desc) in enumerate(tests, 1):
        result = analyzer.analyze(text, stocks)
        actual = result["overall"]
        status = "PASS" if actual == expected else "FAIL"
        if status == "PASS":
            passed += 1
        else:
            failed += 1

        print(f"\n── 测试 {i}：{desc} ──")
        print(f"  输入: {text[:60]}{'...' if len(text) > 60 else ''}")
        if stocks:
            names = ", ".join(s["name"] for s in stocks)
            print(f"  股票: {names}")
        print(f"  预期: {expected} | 实际: {actual} | {status}")
        print(f"  整体置信度: {result['confidence']}")
        for code, sd in result.get("per_stock", {}).items():
            print(f"    {code}: {sd['sentiment']} (conf={sd['confidence']}, kw={sd['keywords']})")

    print(f"\n{'=' * 70}")
    print(f"通过: {passed}/{passed + failed}, 失败: {failed}/{passed + failed}")
    print(f"{'=' * 70}")

# -*- coding: utf-8 -*-
"""
代号解码引擎 — 从帖子文本中解码拼音缩写和别名，输出具体股票。

策略（按优先级）：
  1. 全名匹配 — 股票名称直接出现在文本中
  2. 拼音缩写解码 — 匹配 [a-z]{2,6} 纯小写字母串，查 abbr→stocks 映射
  3. 别名匹配 — 查 alias.json
  4. 上下文消歧 — 当拼音缩写对应多个候选时，用已识别的板块消歧
  5. 黑话收集 — 无法匹配的疑似代号记录到 unknown
"""

import json
import os
import re
import logging

import jieba

from src.preprocess.text_cleaner import TextCleaner

logger = logging.getLogger(__name__)

# ── 英文停用词（常见缩写，不应被当作股票代号） ──────────────────────────
_ENGLISH_STOPWORDS = frozenset([
    "is", "are", "the", "and", "or", "not", "but", "for", "with",
    "from", "this", "that", "these", "those", "have", "has", "had",
    "was", "were", "been", "can", "will", "would", "could", "should",
    "may", "might", "must", "shall", "its", "his", "her", "our",
    "your", "their", "all", "any", "each", "every", "both", "few",
    "more", "most", "some", "such", "only", "own", "same", "just",
    "very", "too", "also", "than", "then", "now", "how", "what",
    "when", "where", "which", "who", "why", "about", "into", "over",
    "after", "before", "between", "under", "again", "once", "here",
    "there", "out", "off", "down", "much", "well", "get", "got",
    "let", "say", "see", "go", "do", "does", "did", "done", "make",
    "made", "take", "took", "come", "came", "know", "think", "want",
    "like", "new", "old", "big", "high", "low", "even", "still",
    "yet", "already", "always", "never", "really", "quite", "back",
    "good", "great", "bad", "right", "wrong", "need", "one", "two",
])
# 英文停用词超过100个，覆盖最常见词
_ENGLISH_STOPWORDS_LARGE = _ENGLISH_STOPWORDS | frozenset([
    "am", "an", "as", "at", "be", "by", "he", "if", "in", "it", "me",
    "my", "no", "of", "on", "so", "to", "up", "us", "we", "ab", "cd",
    "ef", "gh", "ij", "kl", "mn", "op", "qr", "st", "uv", "wx", "yz",
    "ago", "per", "via", "top", "pop", "yes", "end", "big", "far",
    "run", "set", "put", "add", "ask", "try", "buy", "sell", "hold",
    "long", "short", "call", "put", "fund", "etf", "ipo", "ceo", "cfo",
    "gdp", "cpi", "ppi", "pmi", "roe", "eps", "pe", "pb", "ps",
])

# ── 股票上下文关键词（用于别名假阳性过滤） ──────────────────────────────
STOCK_CONTEXT_TERMS = frozenset([
    # 量价词
    '涨停', '跌停', '新高', '新低', '突破', '回落', '反弹', '回调',
    # 交易词
    '买入', '卖出', '加仓', '减仓', '清仓', '满仓', '建仓', '仓位',
    # 分析词
    '业绩', '估值', '龙头', '领涨', '布局', '产能', '利润', '营收',
    # 行业词
    '板块', '概念', '赛道', '行业', '市场', '行情', '走势',
    # 推荐词
    '关注', '看好', '推荐', '逻辑', '方向', '弹性',
])


class StockDecoder:
    """解码帖子文本中的拼音缩写和别名，输出具体股票。"""

    def __init__(self, data_dir=None):
        """
        Parameters
        ----------
        data_dir : str, optional
            stock_db 目录的绝对路径。默认自动推断。
        """
        if data_dir is None:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))
            data_dir = os.path.join(project_root, "data", "stock_db")

        self.data_dir = data_dir
        self._load_data()

        # 词频统计（跨帖子）
        self._word_freq = {}     # word → count
        self._total_posts = 0
        # 未知词统计（用于人工审核队列）
        self._unknown_word_stats = {}  # word → {"count": N, "contexts": [...]}
        # 项目根目录
        self._project_root = os.path.dirname(self.data_dir)

    # ── 数据加载 ──────────────────────────────────────────────────────

    def _load_data(self):
        """加载所有词典数据并构建索引。"""
        # —— 拼音缩写索引 ——
        self.abbr_to_candidates = self._build_pinyin_abbr_index()

        # —— 常见多音字变体（博主常用但pypinyin不生成的读音） ——
        self._pinyin_overrides = {
            "payx": [{"code": "000001", "name": "平安银行"}],  # 行→xing (常见误读，实际是hang)
        }
        for abbr, candidates in self._pinyin_overrides.items():
            if abbr in self.abbr_to_candidates:
                existing = {c["code"] for c in self.abbr_to_candidates[abbr]}
                for c in candidates:
                    if c["code"] not in existing:
                        self.abbr_to_candidates[abbr].append(c)
            else:
                self.abbr_to_candidates[abbr] = candidates

        # —— 全量拼音键（含声调），用于复合词消歧 ——
        self.pinyin_to_candidates = self._build_pinyin_full_index()

        # —— 别名 ——
        self.alias_to_code = self._load_json("alias.json")

        # —— 股票全量 ——
        stock_list = self._load_json("stocks.json")
        self.code_to_name = {}      # code → name
        self.name_to_code = {}      # name → code
        self.code_to_industry = {}  # code → industry
        for s in stock_list:
            code = s["code"]
            name = s["name"]
            self.code_to_name[code] = name
            self.name_to_code[name] = code
            self.code_to_industry[code] = s.get("industry", "")

        # —— 博主专用别名（昵称/typo/代称，词典覆盖不到） ——
        self._blogger_aliases = {
            "BOA": "000725",
            "云猪": "002428",
            "国鸡": "002046",
            "寒王": "688256",
            "披萨": "001309",
            "江波虫": "301308",
            "玛莎拉蒂": "002155",
            "章鱼": "002378",
            "蒸鱼": "300953",
            "赵姨": "603986",
            "辣钨": "000657",
            "钢炼": "000969",
            "永善": "603399",   # 永善锂业→永杉锂业 (typo)
            "永善锂业": "603399",
            "汇成": "688403",   # 汇成→汇成股份 (半导体语境，非汇成真空)
            "汇成股份": "688403",
        }
        for alias, code in self._blogger_aliases.items():
            self.alias_to_code[alias] = code
            # Ensure the real stock name is used, not the alias
            if code in self.code_to_name and alias not in self.name_to_code:
                self.name_to_code[alias] = code

        # 按长度降序排列别名键，确保优先匹配长别名
        self._sorted_aliases = sorted(
            self.alias_to_code.keys(), key=lambda x: -len(x)
        )
        # 按名称长度降序排列股票名，用于全名匹配
        self._sorted_stock_names = sorted(
            self.name_to_code.keys(), key=lambda x: -len(x)
        )

        # 注入博主别名到 name_to_code（sector_extractor 也会用到）
        for alias, code in self._blogger_aliases.items():
            if alias not in self.name_to_code:
                self.name_to_code[alias] = code
                self._sorted_stock_names = sorted(
                    list(self._sorted_stock_names) + [alias], key=lambda x: -len(x)
                )

        # —— 行业分类索引（用于消歧） ——
        self._build_sector_index()

        # —— 注册别名到 jieba 分词词典（TD4: 2字边界验证需要别名被独立识别） ——
        for alias in self.alias_to_code:
            jieba.add_word(alias)
        for alias in self._blogger_aliases:
            jieba.add_word(alias)

        # —— 已知中文词（板块名+概念名），用于过滤 unknown ——
        self._known_chinese_words = set()
        industry_names = self._load_json("industry_names.json")
        for item in industry_names:
            self._known_chinese_words.add(item["name"])
        concept_names = self._load_json("concept_names.json")
        for item in concept_names:
            self._known_chinese_words.add(item["name"])
        # 股票名和别名也加入已知词表
        for name in self.name_to_code:
            self._known_chinese_words.add(name)
        for alias in self.alias_to_code:
            self._known_chinese_words.add(alias)
        # 博主专用别名也加入
        for alias in self._blogger_aliases:
            self._known_chinese_words.add(alias)

        logger.info(
            "StockDecoder loaded: %d abbreviations, %d aliases, %d stocks",
            len(self.abbr_to_candidates),
            len(self.alias_to_code),
            len(self.code_to_name),
        )

    def _load_json(self, filename):
        path = os.path.join(self.data_dir, filename)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _merge_duplicates(self, pairs):
        """处理 pinyin_stocks.json 中的重复键，合并数组。"""
        result = {}
        for key, value in pairs:
            if key in result:
                result[key].extend(value)
            else:
                result[key] = value
        return result

    def _build_pinyin_abbr_index(self):
        """构建拼音缩写 → 候选股票列表索引。

        pinyin_stocks.json 中包含纯 ASCII 小写键（无音调），即缩写形式。
        使用 object_pairs_hook 处理重复键。
        """
        path = os.path.join(self.data_dir, "pinyin_stocks.json")
        with open(path, "r", encoding="utf-8") as f:
            full_pinyin = json.load(f, object_pairs_hook=self._merge_duplicates)

        abbr_index = {}
        for key, candidates in full_pinyin.items():
            # 只保留纯 ASCII 小写键（无音调标记），长度 2-10
            if key.isascii() and key.islower() and 2 <= len(key) <= 10:
                # 去重：同一 code 只保留一次
                seen = set()
                unique = []
                for c in candidates:
                    if c["code"] not in seen:
                        seen.add(c["code"])
                        unique.append(c)
                if unique:
                    abbr_index[key] = unique

        return abbr_index

    def _build_pinyin_full_index(self):
        """构建全量拼音（含声调）→ 候选列表索引，保留用于高级消歧。"""
        path = os.path.join(self.data_dir, "pinyin_stocks.json")
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f, object_pairs_hook=self._merge_duplicates)

        # 只保留有中文名且有匹配的条目
        idx = {}
        for key, candidates in raw.items():
            seen = set()
            unique = []
            for c in candidates:
                if c["code"] not in seen:
                    seen.add(c["code"])
                    unique.append(c)
            if unique:
                idx[key] = unique
        return idx

    def _build_sector_index(self):
        """构建 code → 行业分类集合 的反向索引，用于上下文消歧。

        从 industries.json（申万二级）提取行业→股票代码映射。
        """
        industries = self._load_json("industries.json")
        # code → set of sector names
        self.code_to_sectors = {}
        # sector name → set of codes (for sector lookup)
        self.sector_to_codes = {}

        # industries.json 结构: {"申万二级": {"半导体": [...codes...], ...}, ...}
        for level_name, level_data in industries.items():
            if not isinstance(level_data, dict):
                continue
            for sector_name, codes in level_data.items():
                if not isinstance(codes, list):
                    continue
                code_set = set(codes)
                self.sector_to_codes[sector_name] = code_set
                for code in codes:
                    if code not in self.code_to_sectors:
                        self.code_to_sectors[code] = set()
                    self.code_to_sectors[code].add(sector_name)

        # 也加入 stocks.json 中的 industry 字段
        for code, industry in self.code_to_industry.items():
            if code not in self.code_to_sectors:
                self.code_to_sectors[code] = set()
            self.code_to_sectors[code].add(industry)

    # ── 核心解码 ──────────────────────────────────────────────────────

    def decode(self, text, sectors=None, existing_stocks=None,
               stock_correlation_codes=None, pre_clean=True):
        """从帖子文本中解码代号。

        Parameters
        ----------
        text : str
            帖子正文。
        sectors : list[dict] or list[str], optional
            已识别的板块列表（来自 SectorExtractor）。
            每个元素可以是 {"name": "半导体", ...} 或纯字符串 "半导体"。
        existing_stocks : list[dict], optional
            已由 SectorExtractor 识别的股票（全名匹配），直接导入。
            每个元素格式 {"code": "000001", "name": "平安银行"}。
        stock_correlation_codes : list[str], optional
            API 提供的关联股票代码列表，这些代码不应被 re-decode。
        pre_clean : bool
            是否在解码前调用 TextCleaner 移除 @mention/转发标记等噪音（默认 True）。

        Returns
        -------
        dict
            {"matched": [...], "ambiguous": [...], "unknown": [...]}
        """
        if not text:
            return {"matched": [], "ambiguous": [], "unknown": []}

        # TD5: 移除 @mention / 转发标记 / 引用链噪音
        if pre_clean:
            text = TextCleaner.clean(text)

        matched = []
        ambiguous = []
        unknown = []
        matched_codes = set()   # 已匹配的 code，避免重复
        matched_texts = set()   # 已匹配的文本，避免重复

        # 提取板块名列表（统一为字符串列表）
        sector_names = self._normalize_sectors(sectors)

        # 提取已有的 stockCorrelation code 集合
        corr_codes = set(stock_correlation_codes or [])

        # ── 策略 1：导入已有全名匹配结果 ──
        if existing_stocks:
            for s in existing_stocks:
                if isinstance(s, dict):
                    code = s.get("code", "")
                    name = s.get("name", s.get("matched_text", ""))
                    matched_text = s.get("matched_text", name)
                else:
                    # 兼容纯字符串 code 格式
                    code = str(s)
                    name = code
                    matched_text = code
                if code and code not in matched_codes:
                    matched.append({
                        "code": code,
                        "name": name,
                        "matched_text": matched_text,
                        "method": "fullname",
                        "confidence": "high",
                    })
                    matched_codes.add(code)
                    matched_texts.add(matched_text)

        # 如果文本中没有已识别的股票，尝试自己做全名匹配
        if not existing_stocks:
            self._fullname_match(text, matched, matched_codes, matched_texts)

        # ── 策略 2：拼音缩写解码 ──
        abbrs = self._extract_abbreviations(text)
        for abbr in abbrs:
            if abbr in matched_texts:
                continue
            if abbr in _ENGLISH_STOPWORDS_LARGE:
                continue
            if abbr in self.abbr_to_candidates:
                candidates = self.abbr_to_candidates[abbr]
                # 过滤已在 stockCorrelation 中的股票
                candidates = [c for c in candidates
                              if c["code"] not in corr_codes]
                if not candidates:
                    continue

                if len(candidates) == 1:
                    code, name = candidates[0]["code"], candidates[0]["name"]
                    if code not in matched_codes:
                        matched.append({
                            "code": code,
                            "name": name,
                            "matched_text": abbr,
                            "method": "pinyin",
                        })
                        matched_codes.add(code)
                        matched_texts.add(abbr)
                else:
                    # 多候选 → 尝试上下文消歧（板块 + 文本窗口双重消歧）
                    best = self._disambiguate(candidates, sector_names)
                    if not best:
                        # 板块消歧失败 → 用文本窗口上下文消歧
                        best = self._disambiguate_by_context(text, abbr, candidates)
                    if best:
                        code, name = best
                        if code not in matched_codes:
                            matched.append({
                                "code": code,
                                "name": name,
                                "matched_text": abbr,
                                "method": "pinyin",
                            })
                            matched_codes.add(code)
                            matched_texts.add(abbr)
                    else:
                        # 仍歧义 → 记录
                        ambiguous.append({
                            "matched_text": abbr,
                            "candidates": [
                                {"code": c["code"], "name": c["name"]}
                                for c in candidates
                            ],
                        })
                        matched_texts.add(abbr)
            else:
                # 不在拼音映射中 → 候选 unknown（后面再判断）
                pass

        # ── 策略 3：别名匹配 ──
        self._alias_match(text, matched, matched_codes, matched_texts)

        # ── 策略 5：黑话收集 ──
        # 收集未匹配的缩写
        for abbr in abbrs:
            if (abbr not in matched_texts
                    and abbr not in _ENGLISH_STOPWORDS_LARGE
                    and abbr not in corr_codes
                    and abbr not in self.abbr_to_candidates):
                if abbr not in unknown:
                    unknown.append(abbr)

        # 收集疑似中文黑话（2-4 字中文，不在已知词表中）
        self._collect_chinese_jargon(text, matched_texts, unknown)

        # ── 置信度打分 ──
        for m in matched:
            method = m.get("method", "")
            matched_text = m.get("matched_text", "")
            if method == "fullname":
                m["confidence_score"] = "high"
                m["confidence_detail"] = "股票全名匹配"
            elif method == "pinyin":
                m["confidence_score"] = "high"
                m["confidence_detail"] = "拼音缩写解码"
            elif method == "alias":
                alias_len = len(matched_text)
                if alias_len >= 4:
                    m["confidence_score"] = "high"
                    m["confidence_detail"] = "长别名匹配(≥4字)"
                elif alias_len >= 2:
                    m["confidence_score"] = "medium"
                    m["confidence_detail"] = "短别名匹配(已通过上下文验证)"
                else:
                    m["confidence_score"] = "low"
                    m["confidence_detail"] = "短别名匹配(未验证上下文)"
            else:
                m["confidence_score"] = "medium"
                m["confidence_detail"] = "通用匹配"

        # ── 更新词频统计 ──
        freq_words = []
        for m in matched:
            freq_words.append(m.get("matched_text", ""))
        freq_words.extend(unknown)
        self._update_word_freq(freq_words)

        result = {
            "matched": matched,
            "ambiguous": ambiguous,
            "unknown": unknown,
        }

        # ── 人工审核队列 ──
        if unknown:
            from datetime import datetime as dt
            today = dt.now().strftime("%Y-%m-%d")
            review_items = []
            for word in unknown:
                stats = self._unknown_word_stats.get(word, {})
                review_items.append({
                    "word": word,
                    "contexts": stats.get("contexts", [])[:5],
                    "count": stats.get("count", 0),
                    "date": today,
                })
            result["review_queue"] = review_items

        return result

    def decode_from_posts(self, posts):
        """批量处理帖子列表。

        Parameters
        ----------
        posts : list[dict]
            帖子列表，每个元素至少包含 "content" 字段。
            可选字段：sectors, stocks, stockCorrelation。

        Returns
        -------
        list[dict]
            每帖的解码结果，在原帖基础上附加 decoded 字段。
        """
        results = []
        for p in posts:
            content = p.get("content", "")
            sectors = p.get("sectors", None)
            existing_stocks = p.get("stocks", None)
            stock_corr = p.get("stockCorrelation", None)

            decoded = self.decode(
                text=content,
                sectors=sectors,
                existing_stocks=existing_stocks,
                stock_correlation_codes=stock_corr,
            )
            result = dict(p)
            result["decoded"] = decoded
            results.append(result)
        return results

    # ── 文本提取 ──────────────────────────────────────────────────────

    def _extract_abbreviations(self, text):
        """提取文本中的拼音缩写候选。

        规则：
        - 纯小写字母，2-6 位
        - 不在 URL 中（不被 / 包围）
        - 不是常见英文单词
        """
        # 提取 2-6 位连续小写字母（前后不接小写字母），避免 \b 在中文语境失效
        pattern = re.compile(r'(?<![a-z])[a-z]{2,6}(?![a-z])')
        return pattern.findall(text)

    # ── 全名匹配 ──────────────────────────────────────────────────────

    def _fullname_match(self, text, matched, matched_codes, matched_texts):
        """在文本中直接搜索股票全名。"""
        for name in self._sorted_stock_names:
            code = self.name_to_code[name]
            if code in matched_codes:
                continue
            if name in text:
                matched.append({
                    "code": code,
                    "name": name,
                    "matched_text": name,
                    "method": "fullname",
                })
                matched_codes.add(code)
                matched_texts.add(name)

    # ── 别名匹配 ──────────────────────────────────────────────────────

    def _has_stock_context(self, text, match_start, match_end, window=40):
        """检查匹配位置周围是否存在股票讨论语境。"""
        before = text[max(0, match_start - window):match_start]
        after = text[match_end:match_end + window]
        context = before + after

        # 1. 检查是否有股票语境关键词
        for term in STOCK_CONTEXT_TERMS:
            if term in context:
                return True

        # 2. 检查是否出现了其他已知股票名
        matched_text = text[match_start:match_end]
        for name in self._sorted_stock_names:
            if name != matched_text and name in context:
                return True

        # 3. 检查是否有行业名/概念名
        for ind_name in self._known_chinese_words:
            if len(ind_name) >= 2 and ind_name in context:
                return True

        return False

    def _alias_match(self, text, matched, matched_codes, matched_texts):
        """在文本中查找别名（含上下文验证，过滤假阳性）。

        TD4: 当短别名是其他别名/股票名的前缀时（如"华天"同时是华天酒店和华天科技的前缀），
        检测是否存在前缀冲突候选，若有则用上下文消歧代替直接匹配。
        """
        for alias in self._sorted_aliases:
            code = self.alias_to_code[alias]
            if code in matched_codes:
                continue
            # 跳过过短别名（≤1 字）或常见中文词，避免误匹配
            if len(alias) <= 1:
                continue
            if alias in self._COMMON_CHINESE:
                continue
            if alias not in text:
                continue

            # 定位匹配位置
            pos = text.find(alias)
            alias_len = len(alias)

            # ── 2 字别名边界验证 (TD4) ──
            # 防止跨词假阳性: "今天华天" -> "天华" 跨词命中 (今**天华**天)
            # 2 字别名必须被 jieba 分词作为独立词识别，否则跳过
            # （别名词典已注册到 jieba，跨词碎片不会被识别）
            if alias_len == 2:
                tokens = set(jieba.lcut(text))
                if alias not in tokens:
                    continue  # 不是自然词边界 → 跨词碎片

            # ── TD4: 前缀冲突检测 ──
            # 检查是否存在以该别名为前缀、但指向不同股票的别名/股票名
            conflict_codes = self._find_prefix_conflicts(alias, code)
            if conflict_codes:
                # 存在前缀冲突 → 用上下文消歧
                best_code = self._disambiguate_alias_prefix(text, alias, code, conflict_codes)
                if best_code and best_code != code:
                    code = best_code
                elif best_code is None:
                    # 上下文无法消歧 → 检查是否有更具体的前缀在文本中
                    # 例如 "华天科技" 全名出现时，优先匹配华天科技
                    longer_match = self._find_longer_prefix_match(text, alias, conflict_codes)
                    if longer_match and longer_match != code:
                        code = longer_match
                    elif longer_match is None:
                        # 仍无法消歧 → 标记为 ambiguous
                        stock_name = self.code_to_name.get(code, alias)
                        ambiguous_candidates = [
                            {"code": code, "name": stock_name}
                        ]
                        for cc in conflict_codes:
                            ambiguous_candidates.append({
                                "code": cc,
                                "name": self.code_to_name.get(cc, cc)
                            })
                        logger.debug(
                            "Alias '%s' has prefix conflicts (%s vs %s), cannot disambiguate in \"%.40s\"",
                            alias, code, ','.join(conflict_codes), text
                        )
                        # Don't match — let the ambiguous cases be picked up
                        continue

            # ── 上下文验证 ──
            # ≥4字别名 或 股票全名：跳过验证（大概率是真的）
            # 2-3字别名：必须通过上下文验证
            if alias_len < 4:
                if not self._has_stock_context(text, pos, pos + alias_len):
                    continue  # 拒绝无上下文的短别名匹配

            name = self.code_to_name.get(code, alias)
            matched.append({
                "code": code,
                "name": name,
                "matched_text": alias,
                "method": "alias",
                "confidence": "high" if alias_len >= 4 else "medium",
            })
            matched_codes.add(code)
            matched_texts.add(alias)

    def _find_prefix_conflicts(self, alias, code):
        """查找以 alias 为前缀、但指向不同股票的别名/股票名。

        例如：alias='华天', code='000428'(华天酒店) → 返回 ['002185'](华天科技)
        """
        conflict_codes = set()

        # 检查别名：是否有其他别名以 alias 为前缀且指向不同股票
        for other_alias, other_code in self.alias_to_code.items():
            if other_code == code:
                continue
            if other_alias.startswith(alias) and len(other_alias) > len(alias):
                conflict_codes.add(other_code)

        # 检查股票全名：是否有全名以 alias 为前缀
        for stock_name, scode in self.name_to_code.items():
            if scode == code:
                continue
            if stock_name.startswith(alias) and len(stock_name) > len(alias):
                conflict_codes.add(scode)

        return list(conflict_codes)

    def _find_longer_prefix_match(self, text, alias, conflict_codes):
        """在文本中查找是否有以 alias 为前缀的更具体名称出现。

        例如：alias='华天', text='华天科技是封测龙头' → 返回 '002185'
        如果更长名称没有直接出现在文本中（如"华天这个票"），返回 None。
        """
        # 优先检查股票全名
        for stock_name, scode in self.name_to_code.items():
            if scode in conflict_codes and stock_name in text:
                return scode
        # 再检查更长别名
        for other_alias, other_code in self.alias_to_code.items():
            if (other_code in conflict_codes
                    and other_alias.startswith(alias)
                    and len(other_alias) > len(alias)
                    and other_alias in text):
                return other_code
        return None

    def _disambiguate_alias_prefix(self, text, alias, default_code, conflict_codes):
        """用上下文窗口消歧别名前缀冲突。

        在别名周围搜索概念关键词，找到与各候选股票业务最匹配的。

        Returns:
            best_code 或 None（无法消歧）
        """
        pos = text.find(alias)
        if pos < 0:
            return None

        # 取 50 字上下文窗口
        window = text[max(0, pos - 25):pos + len(alias) + 25]

        # 提取窗口中的概念关键词
        sectors = self._quick_extract_concepts(window)
        if not sectors:
            # 扩大窗口再试
            window2 = text[max(0, pos - 50):pos + len(alias) + 50]
            sectors = self._quick_extract_concepts(window2)

        if not sectors:
            return None

        all_codes = [default_code] + list(conflict_codes)
        scored = []
        for c in all_codes:
            stock_concepts = self._get_stock_concepts(c)
            score = sum(1 for s in sectors if s in stock_concepts)
            scored.append((score, c))

        scored.sort(key=lambda x: -x[0])
        max_score = scored[0][0]

        if max_score > 0:
            return scored[0][1]
        return None

    # ── 上下文消歧 ────────────────────────────────────────────────────

    def _disambiguate_by_context(self, text, abbr, candidates):
        """用匹配位置附近的文本窗口上下文消歧多候选。

        当拼音缩写对应多个股票候选时（如"华天"覆盖华天科技+华天酒店），
        在缩写附近提取概念关键词，与各候选股票的业务概念交叉比对，
        选出最匹配的候选。

        Parameters
        ----------
        text : str
            帖子全文。
        abbr : str
            匹配到的缩写文本（用于定位）。
        candidates : list[dict]
            候选股票列表 [{"code": "...", "name": "..."}, ...]。

        Returns
        -------
        tuple or None
            (code, name) 如果可以消歧，否则 None。
        """
        if len(candidates) <= 1:
            return None

        # 在文本中定位缩写
        pos = text.find(abbr)
        if pos < 0:
            return None

        # 50 字窗口（25 前 + 25 后）
        window = text[max(0, pos - 25):pos + 25]

        # 快速提取窗口中的概念关键词
        sectors = self._quick_extract_concepts(window)
        if not sectors:
            # 扩大窗口到 100 字再试
            window2 = text[max(0, pos - 50):pos + 50]
            sectors = self._quick_extract_concepts(window2)
        if not sectors:
            return None

        # 为每个候选股票打分
        scored = []
        for c in candidates:
            code = c['code']
            stock_concepts = self._get_stock_concepts(code)
            score = sum(1 for s in sectors if s in stock_concepts)
            scored.append((score, c))

        scored.sort(key=lambda x: -x[0])
        max_score = scored[0][0]

        if max_score > 0:
            # 只保留得分最高的候选（去歧义成功）
            return (scored[0][1]['code'], scored[0][1]['name'])

        # 无法消歧
        return None

    def _quick_extract_concepts(self, text):
        """从短文本窗口中快速提取概念关键词。

        不使用 SectorExtractor（避免循环依赖），
        直接分词 + 关键词词典匹配。
        """
        if not hasattr(self, '_concept_keywords_index'):
            self._build_concept_keywords_index()

        words = set(jieba.lcut(text))
        found = []
        seen = set()

        # 分词结果匹配
        for word in words:
            word = word.strip()
            if len(word) < 2:
                continue
            if word in self._concept_keywords_index:
                canonical = self._concept_keywords_index[word]
                if canonical not in seen:
                    found.append(canonical)
                    seen.add(canonical)

        # 子串匹配兜底（处理 jieba 切分不好的情况）
        for kw, canonical in self._concept_keywords_long:
            if kw in text and canonical not in seen:
                found.append(canonical)
                seen.add(canonical)

        return found

    def _build_concept_keywords_index(self):
        """构建概念关键词 → 规范名的快速查找索引。"""
        self._concept_keywords_index = {}
        self._concept_keywords_long = []  # 长关键词（≥3字），用于子串匹配

        # 1. 从 community_concepts.json 加载
        community_path = os.path.join(self.data_dir, 'community_concepts.json')
        if os.path.exists(community_path):
            with open(community_path, 'r', encoding='utf-8') as f:
                community = json.load(f)
            for cname, cinfo in community.items():
                if cname.startswith('_'):
                    continue
                for kw in cinfo.get('keywords', []):
                    self._concept_keywords_index[kw] = cname
                    if len(kw) >= 3:
                        self._concept_keywords_long.append((kw, cname))
                for syn in cinfo.get('synonyms', []):
                    self._concept_keywords_index[syn] = cname
                    if len(syn) >= 3:
                        self._concept_keywords_long.append((syn, cname))
                self._concept_keywords_index[cname] = cname
                if len(cname) >= 3:
                    self._concept_keywords_long.append((cname, cname))

        # 2. 从 concept_names.json 加载
        cn_path = os.path.join(self.data_dir, 'concept_names.json')
        if os.path.exists(cn_path):
            with open(cn_path, 'r', encoding='utf-8') as f:
                for item in json.load(f):
                    name = item['name']
                    self._concept_keywords_index[name] = name
                    if len(name) >= 3:
                        self._concept_keywords_long.append((name, name))

        # 3. 从 industry_names.json 加载
        in_path = os.path.join(self.data_dir, 'industry_names.json')
        if os.path.exists(in_path):
            with open(in_path, 'r', encoding='utf-8') as f:
                for item in json.load(f):
                    name = item['name']
                    self._concept_keywords_index[name] = name
                    if len(name) >= 3:
                        self._concept_keywords_long.append((name, name))

        # 按长度降序排列，优先匹配长关键词
        self._concept_keywords_long.sort(key=lambda x: -len(x[0]))

    def _get_stock_concepts(self, code):
        """获取股票代码关联的所有概念/行业名称集合。

        包含三层数据：
        1. 申万二级行业（sector_to_codes 反向索引）
        2. stocks.json 的 industry 字段
        3. 社区概念→股票映射（concept_stocks_cleaned_v2.json 反向索引）
        """
        concepts = set()
        if code in self.code_to_sectors:
            concepts.update(self.code_to_sectors[code])
        if code in self.code_to_industry:
            concepts.add(self.code_to_industry[code])

        # TD4: 加载社区概念→股票反向索引，使消歧能识别 "封测→华天科技" 等关系
        if not hasattr(self, '_concept_stocks_index'):
            self._build_concept_stocks_index()
        if code in self._concept_stocks_index:
            concepts.update(self._concept_stocks_index[code])

        return concepts

    def _build_concept_stocks_index(self):
        """构建 code → set(concept_names) 反向索引。

        从 data/cross_blogger/concept_stocks_cleaned_v2.json 加载，
        建立 "封测→华天科技" 等社区概念归于股票的关系。
        """
        self._concept_stocks_index = {}

        project_dir = os.path.dirname(self._project_root)
        cs_paths = [
            os.path.join(self._project_root, 'cross_blogger', 'concept_stocks_cleaned_v2.json'),
            os.path.join(project_dir, 'data', 'cross_blogger', 'concept_stocks_cleaned_v2.json'),
        ]
        cs_path = None
        for p in cs_paths:
            if os.path.exists(p):
                cs_path = p
                break

        if not cs_path:
            return

        with open(cs_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        for concept_name, cinfo in data.items():
            if not isinstance(cinfo, dict):
                continue
            stocks = cinfo.get('all_stocks', [])
            if not stocks:
                continue
            for code in stocks:
                if code not in self._concept_stocks_index:
                    self._concept_stocks_index[code] = set()
                self._concept_stocks_index[code].add(concept_name)

        logger.debug(
            "Loaded concept_stocks_index: %d stocks with concept links",
            len(self._concept_stocks_index)
        )

    def _disambiguate(self, candidates, sector_names):
        """用已识别的板块上下文消歧多候选。

        Parameters
        ----------
        candidates : list[dict]
            候选股票列表，每个元素 {"code": "...", "name": "..."}。
        sector_names : list[str]
            当前帖子已识别的板块名称列表。

        Returns
        -------
        tuple or None
            (code, name) 如果可以消歧，否则 None。
        """
        if not sector_names:
            return None

        # 收集板块中的所有股票 code
        sector_codes = set()
        for sname in sector_names:
            codes = self.sector_to_codes.get(sname, set())
            sector_codes.update(codes)

        # 如果没有板块数据，用 stocks.json 的 industry 字段
        if not sector_codes:
            for sname in sector_names:
                for code, industry in self.code_to_industry.items():
                    if industry == sname:
                        sector_codes.add(code)

        if not sector_codes:
            return None

        # 找出候选股中属于该板块的
        matches = []
        for c in candidates:
            if c["code"] in sector_codes:
                matches.append(c)

        if len(matches) == 1:
            return (matches[0]["code"], matches[0]["name"])

        # 如果多个匹配，检查 code_to_sectors 看看谁更匹配
        if len(matches) > 1:
            best = None
            best_score = 0
            for c in matches:
                stock_sectors = self.code_to_sectors.get(c["code"], set())
                overlap = stock_sectors & set(sector_names)
                if len(overlap) > best_score:
                    best_score = len(overlap)
                    best = c
            if best and best_score > 0:
                return (best["code"], best["name"])

        return None

    # ── 黑话收集 ──────────────────────────────────────────────────────

    # 常见中文通用词（非黑话）
    _COMMON_CHINESE = frozenset([
        # 代词/连词/介词
        "可以", "不错", "表现", "今天", "看看", "也能", "都行",
        "很好", "不行", "吃起", "应该", "已经", "可能", "一个",
        "这个", "那个", "什么", "怎么", "还有", "不是", "就是",
        "一样", "不过", "因为", "所以", "如果", "虽然", "但是",
        "起来", "出来", "进去", "过来", "过去", "下来", "上去",
        "已经", "没有", "自己", "他们", "我们", "你们", "还是",
        "知道", "觉得", "认为", "能够", "不能", "不会",
        "一定", "必须", "需要", "当然", "然后", "或者",
        # 常用名词/动词
        "都有", "很好", "领涨", "领跌", "涨停",
        "跌停", "大涨", "大跌", "上涨", "下跌", "反弹", "回调",
        "行情", "走势", "趋势", "板块", "市场", "机会", "风险",
        "关注", "注意", "提醒", "建议", "推荐", "持有", "买入",
        "卖出", "加仓", "减仓", "清仓", "满仓", "建仓", "仓位",
        "短线", "中线", "长线", "波段", "突破", "支撑", "压力",
        "利好", "利空", "消息", "业绩", "预期", "估值", "逻辑",
        "资金", "主力", "散户", "机构", "北向", "外资", "内资",
        "龙头", "妖股", "白马", "黑马", "题材", "热点", "概念",
        "策略", "方向", "节奏", "日内", "开盘", "收盘", "放量",
        "缩量", "换手", "底仓", "做T", "打板", "连板", "首板",
        "二板", "三板", "高度", "低吸", "高抛", "追高", "抄底",
        # 口语/填充词
        "比较", "非常", "特别", "真的", "确实", "基本",
        "一般", "有点", "其实", "反正", "总之", "另外", "而且",
        "此外", "最后", "首先", "其次",
        "主要", "重点", "核心", "关键", "本质", "根本", "基础",
        # 高频日常词（jieba 常标为名词）
        "大家", "别人", "感觉", "东西", "问题", "事情", "地方",
        "方面", "关系", "感觉", "情况", "原因", "对话", "作品",
        "作文", "语文", "现实", "意思", "专心", "个人", "低吸",
        # 股市讨论中高频但非代号
        "低位", "高位", "金属", "稀土", "股神", "散户", "大户",
        # 时间/数量
        "昨天", "明天", "下周", "本月", "年底", "年初", "最近",
        "前段", "后续", "之前", "之后", "目前", "当前", "未来",
        "第一", "第二", "第三", "很多", "一些", "所有", "整个",
        "部分", "全部", "足够", "至少", "最多", "最少", "左右",
        # 股票口语片段
        "都好", "也都", "还行", "也行", "真不",
        "再说", "等等", "之类", "以上", "以下",
        "大量", "少量", "适量", "试试", "考虑",
    ])

    def _collect_chinese_jargon(self, text, matched_texts, unknown):
        """收集疑似中文黑话（2-4 字中文短词，不在已知词表中）。

        使用 jieba.posseg 分词+词性标注，过滤非名词。
        使用词频统计过滤高频通用词。
        """
        import jieba.posseg as pseg
        NOUN_TAGS = {'n', 'ns', 'nt', 'nz', 'ng', 'eng'}

        words = pseg.cut(text)
        for word, flag in words:
            word = word.strip()
            # 只关注 2-4 字纯中文
            if len(word) < 2 or len(word) > 4:
                continue
            if not all('\u4e00' <= c <= '\u9fff' for c in word):
                continue
            # 已匹配过
            if word in matched_texts:
                continue
            # 在已知词表中（股票名/别名/板块名/概念名）
            if word in self._known_chinese_words:
                continue
            # 常见通用词
            if word in self._COMMON_CHINESE:
                continue

            # ⭐ 词性过滤：只保留名词类
            if flag not in NOUN_TAGS:
                continue

            # ⭐ 词频过滤：跳过高频通用词（出现在 >30% 帖子中）
            if self._is_common_word(word):
                continue

            # 记录上下文片段（用于人工审核队列）
            pos = text.find(word)
            if pos >= 0:
                snippet = text[max(0, pos - 10):pos + len(word) + 10]
                if word not in self._unknown_word_stats:
                    self._unknown_word_stats[word] = {"count": 0, "contexts": []}
                self._unknown_word_stats[word]["count"] += 1
                if len(self._unknown_word_stats[word]["contexts"]) < 5:
                    if snippet not in self._unknown_word_stats[word]["contexts"]:
                        self._unknown_word_stats[word]["contexts"].append(snippet)

            if word not in unknown:
                unknown.append(word)

    def _update_word_freq(self, words):
        """更新词频统计（每处理一帖调用一次）。"""
        self._total_posts += 1
        for w in set(words):
            self._word_freq[w] = self._word_freq.get(w, 0) + 1

    def _is_common_word(self, word, threshold=0.3):
        """判断是否高频通用词。出现在超过阈值比例的帖子中即为通用词。"""
        if self._total_posts == 0:
            return False
        freq = self._word_freq.get(word, 0) / self._total_posts
        return freq > threshold

    def save_review_queue(self, date=None):
        """保存待审核的 unknown 词队列到 data/review/pending.json。"""
        from datetime import datetime as dt
        if date is None:
            date = dt.now().strftime("%Y-%m-%d")

        review_dir = os.path.join(self._project_root, "review")
        os.makedirs(review_dir, exist_ok=True)
        review_path = os.path.join(review_dir, "pending.json")

        items = []
        for word, stats in self._unknown_word_stats.items():
            if stats.get("count", 0) > 0:
                items.append({
                    "word": word,
                    "contexts": stats.get("contexts", []),
                    "count": stats.get("count", 0),
                    "date": date,
                })
        items.sort(key=lambda x: -x["count"])

        with open(review_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

        logger.info("审核队列已保存: %s (%d 条目)", review_path, len(items))
        return review_path

    # ── 辅助方法 ──────────────────────────────────────────────────────

    @staticmethod
    def _normalize_sectors(sectors):
        """统一板块表示为字符串列表。"""
        if not sectors:
            return []
        result = []
        for s in sectors:
            if isinstance(s, str):
                result.append(s)
            elif isinstance(s, dict):
                name = s.get("name", "")
                if name:
                    result.append(name)
        return result

    def get_pinyin_variants(self, code):
        """获取指定股票代码的所有拼音变体键（调试用）。"""
        variants = []
        for key, candidates in self.pinyin_to_candidates.items():
            if any(c["code"] == code for c in candidates):
                variants.append(key)
        return variants


# ================================================================
# 自测
# ================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(message)s")

    # 推理 data_dir
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    default_data_dir = os.path.join(project_root, "data", "stock_db")

    decoder = StockDecoder(data_dir=default_data_dir)

    print("=" * 70)
    print("StockDecoder 自测")
    print("=" * 70)

    # ── 测试 1：拼音缩写 ──
    print("\n── 测试 1：拼音缩写 ──")
    text1 = "今天rcgf表现不错"
    result1 = decoder.decode(text1)
    print("输入:", text1)
    print("matched:")
    for m in result1["matched"]:
        print("  {0} {1} ← {2} [{3}]".format(
            m["code"], m["name"], m["matched_text"], m["method"]))
    if result1["ambiguous"]:
        print("ambiguous:")
        for a in result1["ambiguous"]:
            names = ", ".join("{0}({1})".format(c["code"], c["name"])
                              for c in a["candidates"])
            print("  {0} → [{1}]".format(a["matched_text"], names))
    if result1["unknown"]:
        print("unknown:", result1["unknown"])

    # ── 测试 2：上下文消歧 ──
    print("\n── 测试 2：上下文消歧 ──")
    text2 = "半导体板块今天hckj领涨"
    # 模拟 sectors = ["半导体"]
    sectors2 = [{"name": "半导体", "type": "industry", "level": "L2"}]
    result2 = decoder.decode(text2, sectors=sectors2)
    print("输入:", text2)
    print("sectors:", [s["name"] for s in sectors2])
    print("matched:")
    for m in result2["matched"]:
        print("  {0} {1} ← {2} [{3}] confidence={4}".format(
            m["code"], m["name"], m["matched_text"], m["method"],
            m["confidence"]))
    if result2["ambiguous"]:
        print("ambiguous:")
        for a in result2["ambiguous"]:
            names = ", ".join("{0}({1})".format(c["code"], c["name"])
                              for c in a["candidates"])
            print("  {0} → [{1}]".format(a["matched_text"], names))
    if result2["unknown"]:
        print("unknown:", result2["unknown"])

    # ── 测试 2b：无 sectors 的 hckj（应表示歧义） ──
    print("\n── 测试 2b：hckj 无上下文（应表示歧义）──")
    result2b = decoder.decode("今天hckj领涨")
    print("ambiguous:")
    for a in result2b["ambiguous"]:
        names = ", ".join("{0}({1})".format(c["code"], c["name"])
                          for c in a["candidates"])
        print("  {0} → [{1}]".format(a["matched_text"], names))

    # ── 测试 3：黑话识别 ──
    print("\n── 测试 3：黑话识别 ──")
    text3 = "披萨吃起，蓝旗不行"
    result3 = decoder.decode(text3)
    print("输入:", text3)
    print("matched:")
    for m in result3["matched"]:
        print("  {0} {1} ← {2} [{3}]".format(
            m["code"], m["name"], m["matched_text"], m["method"]))
    if result3["ambiguous"]:
        print("ambiguous:", result3["ambiguous"])
    if result3["unknown"]:
        print("unknown:", result3["unknown"])

    # ── 测试 4：混合 ──
    print("\n── 测试 4：混合 ──")
    text4 = "payh和zgpa都可以看看，披萨也不错"
    result4 = decoder.decode(text4)
    print("输入:", text4)
    print("matched:")
    for m in result4["matched"]:
        print("  {0} {1} ← {2} [{3}]".format(
            m["code"], m["name"], m["matched_text"], m["method"]))
    if result4["ambiguous"]:
        print("ambiguous:", result4["ambiguous"])
    if result4["unknown"]:
        print("unknown:", result4["unknown"])

    # ── 测试 4b：使用 payx（原任务测试用例，但词典中无此缩写） ──
    print("\n── 测试 4b：payx（不在词典中的缩写）──")
    text4b = "payx和zgpa都可以看看，披萨也不错"
    result4b = decoder.decode(text4b)
    print("输入:", text4b)
    print("matched:")
    for m in result4b["matched"]:
        print("  {0} {1} ← {2} [{3}]".format(
            m["code"], m["name"], m["matched_text"], m["method"]))
    if result4b["ambiguous"]:
        print("ambiguous:", result4b["ambiguous"])
    if result4b["unknown"]:
        print("unknown:", result4b["unknown"])

    # ── 统计 ──
    print("\n── 引擎统计 ──")
    print("缩写映射数:", len(decoder.abbr_to_candidates))
    print("多义缩写数:",
          sum(1 for v in decoder.abbr_to_candidates.values() if len(v) > 1))
    print("单义缩写数:",
          sum(1 for v in decoder.abbr_to_candidates.values() if len(v) == 1))
    print("别名数:", len(decoder.alias_to_code))
    print("板块数:", len(decoder.sector_to_codes))
    print("行业覆盖: {0}/{1} stocks".format(
        len(decoder.code_to_sectors), len(decoder.code_to_name)))

# -*- coding: utf-8 -*-
"""
观点状态管理 — 追踪博主对股票的看多/看空观点变化。

组合 StockDecoder + SentimentAnalyzer，处理一批帖子后：
- 输出每只股票的当前观点状态
- 与历史数据对比，检测观点变化
- 保存到 data/views/{uid}/ 目录
"""

import json
import os
import sys
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# 添加项目根目录到路径以便导入同目录模块
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_CURRENT_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from analyzers.stock_decoder import StockDecoder
from analyzers.sentiment import SentimentAnalyzer


class ViewTracker:
    """观点状态管理 — 追踪博主对股票的看多/看空观点变化。

    处理一批帖子后，解码股票代号 → 判断情感 → 聚合观点 →
    与历史对比检测变化 → 持久化到 JSON。

    Examples
    --------
    >>> tracker = ViewTracker(uid="7251377368", data_dir="./data")
    >>> result = tracker.track(posts, date="2026-06-24")
    >>> print(result["changes"])
    """

    def __init__(self, uid, data_dir=None, stock_decoder=None, sentiment_analyzer=None):
        """初始化观点追踪器。

        Parameters
        ----------
        uid : str
            博主 ID（对应 data/posts/{uid}/ 和 data/views/{uid}/）。
        data_dir : str, optional
            项目 data 目录路径。默认自动推断。
        stock_decoder : StockDecoder, optional
            可复用已有实例，否则自动创建。
        sentiment_analyzer : SentimentAnalyzer, optional
            可复用已有实例，否则自动创建。
        """
        self.uid = uid

        if data_dir is None:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))
            data_dir = os.path.join(project_root, "data")

        self.data_dir = data_dir
        self.views_dir = os.path.join(data_dir, "views", uid)
        os.makedirs(self.views_dir, exist_ok=True)

        # 解码器和分析器
        if stock_decoder is None:
            stock_db_dir = os.path.join(data_dir, "stock_db")
            self.decoder = StockDecoder(data_dir=stock_db_dir)
        else:
            self.decoder = stock_decoder

        if sentiment_analyzer is None:
            self.analyzer = SentimentAnalyzer()
        else:
            self.analyzer = sentiment_analyzer

        # 加载历史观点
        self._previous_views = self._load_latest()

    # ── 数据加载/保存 ────────────────────────────────────────────────

    def _load_latest(self):
        """加载最新一次保存的观点状态。

        Returns
        -------
        dict — 格式 {"688362": {"name": ..., "current": ..., ...}, ...}
            如果没有历史数据，返回空 dict。
        """
        path = os.path.join(self.views_dir, "latest.json")
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("无法加载 latest.json: %s", e)
            return {}

    def _save(self, views, date_str):
        """保存观点状态到 daily 文件和 latest.json。

        Parameters
        ----------
        views : dict — code → view_info
        date_str : str — ISO 日期 "YYYY-MM-DD"
        """
        # 保存 daily 快照
        daily_path = os.path.join(self.views_dir, f"{date_str}.json")
        with open(daily_path, "w", encoding="utf-8") as f:
            json.dump(views, f, ensure_ascii=False, indent=2)

        # 更新 latest.json
        latest_path = os.path.join(self.views_dir, "latest.json")
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump(views, f, ensure_ascii=False, indent=2)

        logger.info("观点数据已保存: daily=%s, latest=%s", daily_path, latest_path)

    # ── 核心追踪 ────────────────────────────────────────────────────

    def track(self, posts, date=None):
        """处理一批帖子，返回每只股票的当前观点状态和变化列表。

        Parameters
        ----------
        posts : list[dict]
            帖子列表，每项至少包含 "content" 字段。
            可选字段："title"、"created_at"、"id"、"sectors"、"stocks"。
        date : str, optional
            日期字符串 "YYYY-MM-DD"。默认使用帖子中最晚的 created_at。

        Returns
        -------
        dict
            {
                "date": "2026-06-24",
                "views": {code: view_info, ...},
                "changes": [{code, name, from, to, reason, post_id}, ...]
            }
        """
        # 确定日期
        if date is None:
            date = self._extract_date(posts)
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        # ── Step 1：解码股票 ──
        decoded_posts = self.decoder.decode_from_posts(posts)

        # ── Step 2：情感分析 ──
        for i, p in enumerate(decoded_posts):
            # 构建 stocks 列表供情感分析器使用
            stocks = []
            for m in p.get("decoded", {}).get("matched", []):
                stocks.append({
                    "code": m["code"],
                    "name": m["name"],
                })

            content = p.get("content", "")
            sentiment = self.analyzer.analyze(content, stocks)

            # 注入 post_id（优先用帖子原始 id，否则用 index）
            post_id = p.get("id", i)

            # 为每个 per_stock 条目附加 post_id
            per_stock = sentiment.get("per_stock", {})
            for code in per_stock:
                per_stock[code]["_post_id"] = post_id
                per_stock[code]["_post_title"] = p.get("title", "")

            p["sentiment"] = sentiment

        # ── Step 3：聚合观点 ──
        current_views = self._aggregate(decoded_posts)

        # ── Step 4：检测变化 ──
        changes = self._detect_changes(current_views)

        # ── Step 5：保存 ──
        # 清理内部字段后保存
        clean_views = {}
        for code, info in current_views.items():
            clean_views[code] = {
                k: v for k, v in info.items()
                if not k.startswith("_")
            }
        self._save(clean_views, date)

        # 更新内存中的历史
        self._previous_views = clean_views

        return {
            "date": date,
            "views": clean_views,
            "changes": changes,
        }

    def _extract_date(self, posts):
        """从帖子列表中提取日期。"""
        dates = set()
        for p in posts:
            created = p.get("created_at", "")
            if created:
                # "2026-06-24T10:54:00+08:00" → "2026-06-24"
                dates.add(created[:10])
        if len(dates) == 1:
            return dates.pop()
        elif dates:
            return max(dates)  # 取最晚日期
        return None

    # ── 聚合逻辑 ────────────────────────────────────────────────────

    def _aggregate(self, decoded_posts):
        """将多帖中的情感数据聚合为每只股票的统一观点。

        策略：对每只股票，收集所有帖子中的情感和置信度，
        按置信度加权平均，选主导情感。

        Parameters
        ----------
        decoded_posts : list[dict] — 每帖含 "decoded" 和 "sentiment"

        Returns
        -------
        dict — code → view_info
        """
        # code → {"sentiments": [...], "confidences": [...], "keywords": set, "post_ids": set, "name": str}
        stock_accum = {}

        for p in decoded_posts:
            sentiment = p.get("sentiment", {})
            per_stock = sentiment.get("per_stock", {})

            for code, sd in per_stock.items():
                if code not in stock_accum:
                    # 从 decoder 结果中获取股票名
                    name = self._find_stock_name(p.get("decoded", {}), code)
                    stock_accum[code] = {
                        "sentiments": [],
                        "confidences": [],
                        "keywords": set(),
                        "post_ids": set(),
                        "name": name or code,
                    }

                acc = stock_accum[code]
                acc["sentiments"].append(sd.get("sentiment", "neutral"))
                acc["confidences"].append(sd.get("confidence", 0.0))
                for kw in sd.get("keywords", []):
                    acc["keywords"].add(kw)
                post_id = sd.get("_post_id")
                if post_id is not None:
                    acc["post_ids"].add(post_id)

        # 对每只股票计算聚合观点
        views = {}
        for code, acc in stock_accum.items():
            # 加权投票
            weighted = {"bullish": 0.0, "bearish": 0.0, "neutral": 0.0}
            for sent, conf in zip(acc["sentiments"], acc["confidences"]):
                weighted[sent] += conf

            # 选最高分的
            best_sent = max(weighted, key=weighted.get)
            best_score = weighted[best_sent]

            # 置信度
            total_conf = sum(acc["confidences"])
            confidence = best_score / total_conf if total_conf > 0 else 0.0

            views[code] = {
                "name": acc["name"],
                "current": best_sent,
                "confidence": round(min(1.0, confidence), 2),
                "keywords": sorted(acc["keywords"]),
                "post_ids": sorted(acc["post_ids"]),
                "post_count": len(acc["sentiments"]),
            }

        return views

    def _find_stock_name(self, decoded, code):
        """从解码结果中通过 code 查找股票名。"""
        for m in decoded.get("matched", []):
            if m.get("code") == code:
                return m.get("name", code)
        return code

    # ── 变化检测 ────────────────────────────────────────────────────

    def _detect_changes(self, current_views):
        """检测观点变化（与上一次保存的状态对比）。

        Parameters
        ----------
        current_views : dict — code → view_info

        Returns
        -------
        list[dict] — 观点变化的条目
        """
        changes = []

        for code, info in current_views.items():
            curr_sent = info.get("current", "neutral")
            prev = self._previous_views.get(code)

            if prev is None:
                # 首次出现，不算变化
                continue

            prev_sent = prev.get("current", "neutral")

            if curr_sent != prev_sent and curr_sent != "neutral" and prev_sent != "neutral":
                # 方向变了
                reason = self._explain_change(code, prev_sent, curr_sent, info)

                changes.append({
                    "code": code,
                    "name": info.get("name", code),
                    "from": prev_sent,
                    "to": curr_sent,
                    "reason": reason,
                    "post_ids": info.get("post_ids", []),
                })

        return changes

    def _explain_change(self, code, from_sent, to_sent, info):
        """生成观点变化的解释文本。"""
        keywords = info.get("keywords", [])
        keyword_str = "、".join(keywords[:3]) if keywords else "关键词变化"

        if from_sent == "bearish" and to_sent == "bullish":
            # 转多：常见于道歉/新高/超预期
            if any("道歉" in kw for kw in keywords):
                return "道歉帖：博主此前的看空被打脸，转看多"
            if any("新高" in kw for kw in keywords):
                return f"股价创新高，博主转看多: {keyword_str}"
            return f"观点转多: {keyword_str}"

        elif from_sent == "bullish" and to_sent == "bearish":
            # 转空
            if any(kw in ("风险", "风险大", "警惕", "回避", "见顶", "崩盘") for kw in keywords):
                return f"博主提示风险: {keyword_str}"
            return f"观点转空: {keyword_str}"

        return f"观点从{from_sent}转为{to_sent}: {keyword_str}"

    def detect_changes(self):
        """手动检测当前已加载的 views 中是否有观点变化。

        Returns
        -------
        list[dict] — 观点变化的条目
        """
        return self._detect_changes(self._previous_views)

    # ── 历史查询 ────────────────────────────────────────────────────

    def get_view_history(self, code):
        """获取某只股票的观点变化历史。

        遍历 views/{uid}/ 目录下所有日期文件，
        提取该股票在每一天的观点状态。

        Parameters
        ----------
        code : str — 股票代码

        Returns
        -------
        list[dict]
            [{"date": "2026-06-24", "current": "bullish", "confidence": 0.9, "keywords": [...]}, ...]
            按日期升序排列。
        """
        history = []
        if not os.path.isdir(self.views_dir):
            return history

        for filename in sorted(os.listdir(self.views_dir)):
            if not filename.endswith(".json") or filename == "latest.json":
                continue
            date_str = filename.replace(".json", "")
            path = os.path.join(self.views_dir, filename)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if code in data:
                    entry = dict(data[code])
                    entry["date"] = date_str
                    history.append(entry)
            except (json.JSONDecodeError, IOError):
                continue

        return history

    def get_all_views(self):
        """获取当前所有股票的观点状态。

        Returns
        -------
        dict — code → view_info
        """
        return dict(self._previous_views)


# ================================================================
# 自测
# ================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(message)s")

    # 推断项目根
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))

    # 加载真实帖子数据
    posts_path = os.path.join(
        project_root, "data", "posts", "7251377368", "2026-06-24.json"
    )
    if not os.path.exists(posts_path):
        print(f"帖子数据不存在: {posts_path}")
        print("跳过 ViewTracker 自测（需要先运行爬虫产出帖子数据）")
        sys.exit(0)

    with open(posts_path, "r", encoding="utf-8") as f:
        posts = json.load(f)

    print("=" * 70)
    print("ViewTracker 自测 — 使用真实帖子数据")
    print(f"帖子数量: {len(posts)}")
    print("=" * 70)

    # 创建 tracker
    data_dir = os.path.join(project_root, "data")
    tracker = ViewTracker(uid="7251377368", data_dir=data_dir)

    # 执行追踪
    result = tracker.track(posts, date="2026-06-24")

    print(f"\n追踪日期: {result['date']}")
    print(f"识别股票数: {len(result['views'])}")
    print(f"观点变化数: {len(result['changes'])}")

    print("\n── 各股票观点 ──")
    for code, info in sorted(result["views"].items()):
        print(f"  {code} {info['name']}: {info['current']} "
              f"(conf={info['confidence']}, posts={info['post_count']})")
        if info.get("keywords"):
            print(f"    关键词: {', '.join(info['keywords'][:5])}")

    if result["changes"]:
        print("\n── 观点变化 ──")
        for ch in result["changes"]:
            print(f"  {ch['code']} {ch['name']}: {ch['from']} → {ch['to']}")
            print(f"    原因: {ch['reason']}")

    # 测试历史查询
    print("\n── 历史查询 ──")
    for code in list(result["views"].keys())[:3]:
        history = tracker.get_view_history(code)
        name = result["views"][code]["name"]
        print(f"  {code} {name}: {len(history)} 条历史记录")

    # 手动验证四个测试用例
    print("\n── 手动验证 4 个测试用例 ──")
    test_cases = [
        ("协创数据都新高了", "bullish", "协创数据新高"),
        ("给永善锂业道歉！", "bullish", "道歉转看多"),
        ("美银全球研究将美光科技目标价上调至1500美元。刷存在感呢你？", "bearish", "反讽检测"),
        ("关注：甬矽电子/汇成股份(CoWoS-L布局领先)，长电科技/通富微电/华天科技", "bullish", "多股推荐关注"),
    ]

    sa = SentimentAnalyzer()
    sd = StockDecoder(data_dir=os.path.join(data_dir, "stock_db"))

    for text, expected, desc in test_cases:
        decoded = sd.decode(text)
        stocks = [{"code": m["code"], "name": m["name"]}
                   for m in decoded.get("matched", [])]
        sentiment = sa.analyze(text, stocks)
        actual = sentiment["overall"]
        status = "PASS" if actual == expected else "FAIL"
        print(f"  [{status}] {desc}: \"{text[:50]}\" → {actual} (expected {expected})")
        for code, sd_val in sentiment.get("per_stock", {}).items():
            print(f"         {code}: {sd_val['sentiment']} kw={sd_val['keywords']}")

    print(f"\n{'=' * 70}")
    print("ViewTracker 自测完成")
    print(f"数据已保存至: {tracker.views_dir}")
    print(f"{'=' * 70}")

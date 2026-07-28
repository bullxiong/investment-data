# -*- coding: utf-8 -*-
"""
变化检测引擎 — 从 view_timeline.json 时序数据中检测 5 类变化。

检测类型：
1. 观点翻转 (sentiment_flip) - 同一概念 bullish↔bearish
2. 首次关注 (first_mention) - 博主首次提及某概念
3. 持续强化 (intensity_surge) - 提及频次显著上升（近3天 vs 前7天，增长>100%）
4. 共振成形 (resonance_form) - 2+博主在过去N天内首次同时看好同一概念
5. 分歧出现 (divergence) - 2+博主对同一概念方向相反

Usage:
    python src/analyzers/change_detector.py
    或:
    from src.analyzers.change_detector import ChangeDetector
    detector = ChangeDetector()
    changes = detector.detect()
"""

import json
import os
import sys
from datetime import datetime, timedelta

# 项目根
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_CURRENT_DIR))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")

sys.path.insert(0, _PROJECT_ROOT)

# 博主名简称映射（从 bloggers.json + view_timeline 对照）
_SHORT_NAMES = {
    "雪球大V-1034624503": "雪球V",
    "白河愁博士": "白河愁",
    "波king（只爱甜妹/盘后信息分享）": "波king",
    "派大星皮皮": "派大星",
}

FIRST_MENTION_WINDOW = 30  # 首次关注：30天内首次出现
INTENSITY_WINDOW_RECENT = 3   # 强度检测：最近N天
INTENSITY_WINDOW_PREV = 7     # 强度检测：前M天基线
INTENSITY_SURGE_RATIO = 2.0   # 强度增长倍数阈值
RESONANCE_WINDOW = 3          # 共振窗口：N天内
RESONANCE_MIN_BLOGGERS = 2    # 共振最少博主数


class ChangeDetector:
    """变化检测引擎，基于 view_timeline.json 时序数据分析博主观点变化。"""

    def __init__(self, data_dir=None):
        if data_dir is None:
            data_dir = _DATA_DIR
        self.data_dir = data_dir
        self.timeline_path = os.path.join(
            data_dir, "cross_blogger", "view_timeline.json")
        self.output_path = os.path.join(data_dir, "changes_log.json")

    def detect(self):
        """执行全部 5 类检测，返回变化列表并写入 changes_log.json。

        Returns
        -------
        list[dict] — 所有检测到的变化条目。
        """
        if not os.path.exists(self.timeline_path):
            print(f"[change_detector] timeline 数据不存在: {self.timeline_path}")
            return []

        with open(self.timeline_path, "r", encoding="utf-8") as f:
            timeline = json.load(f)

        changes = []
        changes.extend(self._detect_sentiment_flips(timeline))
        changes.extend(self._detect_first_mentions(timeline))
        changes.extend(self._detect_intensity_surges(timeline))
        changes.extend(self._detect_resonance(timeline))
        changes.extend(self._detect_divergence(timeline))
        changes.extend(self._detect_portfolio_signals())

        # 去重（同类型+同博主+同概念/股票+同日期只保留一条）
        seen = set()
        deduped = []
        for c in changes:
            # portfolio signals have different dedup keys
            if c["type"] in ("position_enter", "position_exit"):
                key = (c["type"], c.get("blogger", ""), c.get("stock", ""),
                       c.get("date", ""))
            elif c["type"] in ("sector_rotation",):
                key = (c["type"], c.get("blogger", ""), c.get("from_sector", ""),
                       c.get("to_sector", ""), c.get("date", ""))
            else:
                key = (c["type"], c.get("blogger", ""), c.get("concept", ""),
                       c.get("date", ""), c.get("from", ""), c.get("to", ""))
            if key not in seen:
                seen.add(key)
                deduped.append(c)
        changes = deduped

        # 🆕 为共振/分歧信号生成叙事解释
        self._enrich_narratives(changes, timeline)

        # 写入
        updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        output = {
            "changes": changes,
            "updated": updated,
            "count": {
                "sentiment_flip": sum(1 for c in changes if c["type"] == "sentiment_flip"),
                "first_mention": sum(1 for c in changes if c["type"] == "first_mention"),
                "intensity_surge": sum(1 for c in changes if c["type"] == "intensity_surge"),
                "resonance_form": sum(1 for c in changes if c["type"] == "resonance_form"),
                "divergence": sum(1 for c in changes if c["type"] == "divergence"),
                "position_enter": sum(1 for c in changes if c["type"] == "position_enter"),
                "position_exit": sum(1 for c in changes if c["type"] == "position_exit"),
                "sector_rotation": sum(1 for c in changes if c["type"] == "sector_rotation"),
                "concentration_surge": sum(1 for c in changes if c["type"] == "concentration_surge"),
                "concentration_drop": sum(1 for c in changes if c["type"] == "concentration_drop"),
                "rapid_turnover": sum(1 for c in changes if c["type"] == "rapid_turnover"),
                "total": len(changes),
            },
        }
        with open(self.output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"[change_detector] 检测到 {len(changes)} 条变化 → {self.output_path}")
        return changes

    # ── 1. 观点翻转 ────────────────────────────────────────────────

    def _detect_sentiment_flips(self, timeline):
        """检测同一概念的 consecutive periods 中 sentiment 变化。"""
        changes = []
        for entry in timeline.get("timeline", []):
            blogger = entry["blogger"]
            items = sorted(entry["items"], key=lambda x: x["start"])

            # 按 concept 分组
            by_concept = {}
            for item in items:
                c = item["concept"]
                if c not in by_concept:
                    by_concept[c] = []
                by_concept[c].append(item)

            for concept, citems in by_concept.items():
                citems.sort(key=lambda x: x["start"])
                for i in range(len(citems) - 1):
                    a, b = citems[i], citems[i + 1]
                    # 两个时间段 sentiment 不同
                    if a["sentiment"] != b["sentiment"]:
                        # 只关注 bullish↔bearish 的真正翻转（不关心与 neutral 之间的变化）
                        sentiments = {a["sentiment"], b["sentiment"]}
                        if "bullish" in sentiments and "bearish" in sentiments:
                            changes.append({
                                "type": "sentiment_flip",
                                "blogger": blogger,
                                "concept": concept,
                                "from": a["sentiment"],
                                "to": b["sentiment"],
                                "from_date": a["start"],
                                "date": b["start"],
                                "detail": (
                                    f"{_short(blogger)} 对 {concept} 观点翻转: "
                                    f"{_sent_label(a['sentiment'])} → {_sent_label(b['sentiment'])}"
                                ),
                            })
        return changes

    # ── 2. 首次关注 ────────────────────────────────────────────────

    def _detect_first_mentions(self, timeline):
        """检测博主在近期窗口内首次提及某概念。
        
        逻辑：如果博主在 FIRST_MENTION_WINDOW 天内第一次（或在较长间隔后重新）
        提及某个 concept，且该 concept 的历史最早记录就在此窗口内，则认为是首次关注。
        """
        changes = []
        now_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        window_start = now_date - timedelta(days=FIRST_MENTION_WINDOW)

        for entry in timeline.get("timeline", []):
            blogger = entry["blogger"]
            items = sorted(entry["items"], key=lambda x: x["start"])

            # 按 concept 分组，找到每个 concept 的首次出现时间
            concept_first = {}
            for item in items:
                c = item["concept"]
                if c not in concept_first:
                    concept_first[c] = item["start"]

            for concept, first_date_str in concept_first.items():
                first_date = datetime.strptime(first_date_str, "%Y-%m-%d")
                # 只有首次出现在窗口内的才算
                if first_date >= window_start:
                    changes.append({
                        "type": "first_mention",
                        "blogger": blogger,
                        "concept": concept,
                        "date": first_date_str,
                        "detail": (
                            f"{_short(blogger)} 首次关注 {concept}"
                        ),
                    })
        return changes

    # ── 3. 持续强化 ────────────────────────────────────────────────

    def _detect_intensity_surges(self, timeline):
        """检测提及频次显著上升（近3天 vs 前7天，增长>100%）。
        
        按 concept 分组，计算最近 INTENSITY_WINDOW_RECENT 天内的日均提及数，
        对比前 INTENSITY_WINDOW_PREV 天内的日均提及数。
        """
        changes = []
        # 找数据中最近日期
        all_dates = set()
        for entry in timeline.get("timeline", []):
            for item in entry["items"]:
                all_dates.add(item["start"])
                all_dates.add(item["end"])

        if not all_dates:
            return changes

        latest_date_str = max(all_dates)
        latest_date = datetime.strptime(latest_date_str, "%Y-%m-%d")

        for entry in timeline.get("timeline", []):
            blogger = entry["blogger"]
            items = entry["items"]

            # 按 concept 分组
            by_concept = {}
            for item in items:
                c = item["concept"]
                if c not in by_concept:
                    by_concept[c] = []
                by_concept[c].append(item)

            for concept, citems in by_concept.items():
                # 计算最近3天的提及数
                recent_start = latest_date - timedelta(days=INTENSITY_WINDOW_RECENT)
                prev_start = latest_date - timedelta(days=INTENSITY_WINDOW_PREV + INTENSITY_WINDOW_RECENT)
                prev_end = latest_date - timedelta(days=INTENSITY_WINDOW_RECENT + 1)

                recent_mentions = 0
                prev_mentions = 0

                for item in citems:
                    s = datetime.strptime(item["start"], "%Y-%m-%d")
                    e = datetime.strptime(item["end"], "%Y-%m-%d")

                    # 时间窗口重叠计算
                    # 最近窗口: [recent_start, latest_date]
                    overlap_recent = _overlap_days(s, e, recent_start, latest_date)
                    if overlap_recent > 0:
                        # 按窗口内天数比例计算提及数
                        total_days_in_item = (e - s).days + 1
                        recent_mentions += item["mentions"] * (overlap_recent / max(total_days_in_item, 1))

                    # 前窗口: [prev_start, prev_end]
                    overlap_prev = _overlap_days(s, e, prev_start, prev_end)
                    if overlap_prev > 0:
                        total_days_in_item = (e - s).days + 1
                        prev_mentions += item["mentions"] * (overlap_prev / max(total_days_in_item, 1))

                # 日均计算
                recent_daily = recent_mentions / INTENSITY_WINDOW_RECENT
                prev_daily = prev_mentions / max(INTENSITY_WINDOW_PREV, 1)

                if prev_daily > 0 and recent_daily / prev_daily >= INTENSITY_SURGE_RATIO:
                    surge_pct = int((recent_daily / prev_daily - 1) * 100)
                    # 计算连续天数
                    consecutive_days = 0
                    for item in sorted(citems, key=lambda x: x["start"]):
                        s = datetime.strptime(item["start"], "%Y-%m-%d")
                        e = datetime.strptime(item["end"], "%Y-%m-%d")
                        overlap = _overlap_days(s, e, recent_start, latest_date)
                        consecutive_days += overlap

                    changes.append({
                        "type": "intensity_surge",
                        "blogger": blogger,
                        "concept": concept,
                        "date": latest_date_str,
                        "consecutive_days": min(consecutive_days, INTENSITY_WINDOW_RECENT),
                        "prev_avg": round(prev_daily, 1),
                        "recent_avg": round(recent_daily, 1),
                        "surge_pct": surge_pct,
                        "detail": (
                            f"{_short(blogger)} 对 {concept} 持续强化 "
                            f"({prev_daily:.1f}→{recent_daily:.1f}次/天, ↑{surge_pct}%)"
                        ),
                    })

        return changes

    # ── 4. 共振成形 ────────────────────────────────────────────────

    def _detect_resonance(self, timeline):
        """检测 2+ 博主在过去 N 天内首次同时看好同一概念。
        
        认定为"共振"的条件：
        - 2+ 不同博主在同一 concept 上的最近活跃时间都在 RESONANCE_WINDOW 天内
        - 且 sentiment 都是 bullish
        """
        changes = []
        now_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        window_start = now_date - timedelta(days=RESONANCE_WINDOW)

        # 收集每个 concept 下最近看好的博主
        concept_recent_bullish = {}
        for entry in timeline.get("timeline", []):
            blogger = entry["blogger"]
            for item in entry["items"]:
                if item["sentiment"] != "bullish":
                    continue
                end_date = datetime.strptime(item["end"], "%Y-%m-%d")
                if end_date >= window_start:
                    concept = item["concept"]
                    if concept not in concept_recent_bullish:
                        concept_recent_bullish[concept] = set()
                    concept_recent_bullish[concept].add(blogger)

        # 如果某个 concept 有 2+ 博主在窗口内看好
        for concept, bloggers in concept_recent_bullish.items():
            if len(bloggers) >= RESONANCE_MIN_BLOGGERS:
                blogger_short = [_short(b) for b in sorted(bloggers)]
                changes.append({
                    "type": "resonance_form",
                    "concept": concept,
                    "bloggers": sorted(bloggers),
                    "blogger_count": len(bloggers),
                    "date": now_date.strftime("%Y-%m-%d"),
                    "detail": (
                        f"{concept}: {'+'.join(blogger_short)} 共振看好"
                    ),
                })

        return changes

    # ── 5. 分歧出现 ────────────────────────────────────────────────

    def _detect_divergence(self, timeline):
        """检测 2+ 博主对同一概念方向相反。
        
        最近活跃的博主中，有人 bullish 有人 bearish 于同一概念。
        """
        changes = []
        now_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        # 取最近14天的数据做判断
        window_start = now_date - timedelta(days=14)

        # 收集每个 concept 下各博主的当前 sentiment
        concept_blogger_sentiment = {}
        for entry in timeline.get("timeline", []):
            blogger = entry["blogger"]
            for item in entry["items"]:
                # 只看最近活动过的
                end_date = datetime.strptime(item["end"], "%Y-%m-%d")
                if end_date >= window_start:
                    concept = item["concept"]
                    if concept not in concept_blogger_sentiment:
                        concept_blogger_sentiment[concept] = {}
                    concept_blogger_sentiment[concept][blogger] = item["sentiment"]

        for concept, blogger_sents in concept_blogger_sentiment.items():
            bullish_bloggers = [b for b, s in blogger_sents.items() if s == "bullish"]
            bearish_bloggers = [b for b, s in blogger_sents.items() if s == "bearish"]

            if bullish_bloggers and bearish_bloggers:
                bullish_short = [_short(b) for b in sorted(bullish_bloggers)]
                bearish_short = [_short(b) for b in sorted(bearish_bloggers)]
                changes.append({
                    "type": "divergence",
                    "concept": concept,
                    "bullish_bloggers": sorted(bullish_bloggers),
                    "bearish_bloggers": sorted(bearish_bloggers),
                    "date": now_date.strftime("%Y-%m-%d"),
                    "detail": (
                        f"{concept}: " +
                        f"{'、'.join(bullish_short)} 看多 vs " +
                        f"{'、'.join(bearish_short)} 看空"
                    ),
                })

        return changes

    # ── 6. 组合调仓 ────────────────────────────────────────────────

    def _detect_portfolio_signals(self):
        bloggers_path = os.path.join(_PROJECT_ROOT, "bloggers.json")
        if not os.path.exists(bloggers_path):
            return []

        with open(bloggers_path, "r", encoding="utf-8") as f:
            bloggers = json.load(f)

        all_signals = []
        for uid, info in bloggers.items():
            cubes = info.get("cubes", {})
            if not cubes:
                continue
            try:
                from src.analyzers.portfolio_tracker import generate_portfolio_signals
                signals = generate_portfolio_signals(uid, days=7)
                all_signals.extend(signals)
            except Exception as e:
                print(f"[change_detector] portfolio_tracker error for {uid}: {e}")

        return all_signals

    # ── 叙事增强 ────────────────────────────────────────────────

    def _enrich_narratives(self, changes, timeline):
        """为共振/分歧/言行矛盾信号调用叙事引擎生成解释文本。

        优雅降级：任何异常都不影响主流程，信号仍保留但无 narrative 字段。
        """
        try:
            from analyzers.narrative_engine import narrate
        except ImportError:
            print("[Narrative] narrative_engine 模块不可用，跳过叙事增强")
            return

        for signal in changes:
            stype = signal.get("type", "")

            if stype in ("resonance_form", "divergence"):
                # 构建共振/分歧叙事数据
                concept = signal.get("concept", "")
                bloggers = signal.get("bloggers", signal.get("bullish_bloggers", []) +
                                      signal.get("bearish_bloggers", []))

                if stype == "resonance_form":
                    resonance_detail = signal.get("detail", "")
                    divergence_detail = "暂无"
                else:
                    resonance_detail = "暂无"
                    divergence_detail = signal.get("detail", "")

                # 尝试获取关联个股
                stocks_text = self._get_concept_stocks(concept)

                try:
                    signal["narrative"] = narrate("concept_consensus", {
                        "concept": concept,
                        "bloggers": "、".join([_short(b) for b in bloggers]),
                        "resonance_detail": resonance_detail,
                        "divergence_detail": divergence_detail,
                        "stocks": stocks_text,
                    }, max_tokens=600)
                except Exception as e:
                    print(f"[Narrative] concept_consensus error: {e}")

            elif stype == "divergence_extra":
                try:
                    signal["narrative"] = narrate("divergence_explain", {
                        "blogger": signal.get("blogger", ""),
                        "stated_view": signal.get("stated_view", signal.get("detail", "")),
                        "actual_action": signal.get("actual_action", signal.get("detail", "")),
                        "date": signal.get("date", ""),
                    }, max_tokens=600)
                except Exception as e:
                    print(f"[Narrative] divergence_explain error: {e}")

    def _get_concept_stocks(self, concept):
        """从 concept_stocks.json 获取某概念的 overlap 个股。"""
        stocks_path = os.path.join(
            _DATA_DIR, "cross_blogger", "concept_stocks.json")
        if not os.path.exists(stocks_path):
            return "暂无"

        try:
            with open(stocks_path, "r", encoding="utf-8") as f:
                concept_stocks = json.load(f)

            stocks_db_path = os.path.join(_DATA_DIR, "stock_db", "stocks.json")
            code_to_name = {}
            if os.path.exists(stocks_db_path):
                with open(stocks_db_path, encoding="utf-8") as f:
                    for s in json.load(f):
                        code_to_name[s["code"]] = s["name"]

            cdata = concept_stocks.get(concept, {})
            overlap_codes = cdata.get("overlap", [])
            names = [code_to_name.get(c, c) for c in overlap_codes]
            return "、".join(names) if names else "暂无"
        except Exception:
            return "暂无"


# ── Helpers ───────────────────────────────────────────────────────

def _short(name):
    """博主全名 → 简称。"""
    return _SHORT_NAMES.get(name, name)


def _sent_label(sent):
    """情感标签。"""
    return {"bullish": "看多", "bearish": "看空", "neutral": "中性"}.get(sent, sent)


def _overlap_days(start1, end1, start2, end2):
    """计算两个日期区间的重叠天数（含首尾）。"""
    latest_start = max(start1, start2)
    earliest_end = min(end1, end2)
    if latest_start > earliest_end:
        return 0
    return (earliest_end - latest_start).days + 1


# ================================================================
# 自测 & CLI
# ================================================================

if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    detector = ChangeDetector()
    changes = detector.detect()

    print("\n" + "=" * 60)
    print("ChangeDetector 自测")
    print("=" * 60)

    by_type = {}
    for c in changes:
        t = c["type"]
        if t not in by_type:
            by_type[t] = []
        by_type[t].append(c)

    type_labels = {
        "sentiment_flip": "🔥 观点翻转",
        "first_mention": "🆕 首次关注",
        "intensity_surge": "📈 持续强化",
        "resonance_form": "🤝 共振成形",
        "divergence": "⚡ 分歧出现",
        "position_enter": "🟢 建仓",
        "position_exit": "🔴 清仓",
        "sector_rotation": "🔄 板块轮动",
        "concentration_surge": "🎯 集中度↑",
        "concentration_drop": "🔽 集中度↓",
        "rapid_turnover": "⚡ 快速换仓",
    }

    for t, label in type_labels.items():
        items = by_type.get(t, [])
        if not items:
            continue
        print(f"\n{label} ({len(items)}):")
        for c in items[:8]:
            print(f"  · {c['detail']}")

    print(f"\n{'=' * 60}")
    print(f"总计: {len(changes)} 条变化")
    print(f"输出: {detector.output_path}")
    print(f"{'=' * 60}")

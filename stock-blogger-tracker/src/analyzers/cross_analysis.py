#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交叉分析引擎 — 博主观点 vs 研报基准线
研报是尺子，不是第五个博主。

输入：
  - 博主观点数据：data/views/*/latest.json
  - 研报数据：data/zsxq/stock_pool.xlsx + stock_pool_edits.json

输出：
  - data/cross_blogger/research_baseline.json
  - data/cross_blogger/cross_signals.json
"""

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

TZ = timezone(timedelta(hours=8))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SIGNAL_RULES = {
    "共振_强信号": "博主和研报都提同一个概念且同股票 → bullish_alignment",
    "共振_弱信号": "博主和研报都提同一个概念但不同股票 → sector_consensus",
    "博主超前": "博主首次提及早于研报首次提及 → 前瞻性",
    "博主滞后": "研报早于博主 → 跟随性",
    "博主独有": "博主提了但研报库没有 → 潜在盲区或独立判断",
    "研报独有": "研报推了但没有任何博主提 → 机构圈内共识，待民间验证",
}


class CrossAnalyzer:
    def __init__(self, project_root: str = None):
        self.project_root = project_root or PROJECT_ROOT
        self.data_dir = os.path.join(self.project_root, 'data')
        self.zsxq_dir = os.path.join(self.data_dir, 'zsxq')
        self.cross_dir = os.path.join(self.data_dir, 'cross_blogger')
        self.views_dir = os.path.join(self.data_dir, 'views')

        Path(self.cross_dir).mkdir(parents=True, exist_ok=True)

    def build_baseline(self) -> Dict:
        """
        从研报股池构建机构观点基线
        返回 baselines dict: {concept_l1/concept_l2: {stock_count, mention_count, ...}}
        """
        baseline = {}
        edits = self._load_edits()

        # 从 Excel 加载股票池
        try:
            import pandas as pd
            excel_path = os.path.join(self.zsxq_dir, 'stock_pool.xlsx')
            if os.path.exists(excel_path):
                df = pd.read_excel(excel_path, dtype={'股票代码': str})
            else:
                return baseline
        except Exception as e:
            print(f"[CrossAnalyzer] 无法加载股票池: {e}")
            return baseline

        # 按概念分组聚合
        for _, row in df.iterrows():
            code = str(row.get('股票代码', ''))
            name = str(row.get('股票名称', ''))
            concept_l1 = str(row.get('一级概念', '') or '')
            concept_l2 = str(row.get('概念板块', '') or '')
            first_date = str(row.get('加入日期', '') or '')
            last_date = str(row.get('最后提及日期', '') or '')
            mention_count = int(row.get('提及次数', 0) or 0)
            link = str(row.get('星球链接', '') or '')

            # 检查是否被人工删除
            if code in edits and edits[code].get('human_deleted'):
                continue

            if not name or name == 'nan':
                continue

            # 构建概念key
            concept_key = concept_l2 if concept_l2 else '未分类'
            if concept_l1 and concept_l1 != 'nan':
                concept_key = f"{concept_l1}/{concept_l2}" if concept_l2 else concept_l1

            if concept_key not in baseline:
                baseline[concept_key] = {
                    'stock_count': 0,
                    'mention_count': 0,
                    'stocks': [],
                    'first_mentioned': first_date,
                    'last_mentioned': last_date,
                }

            entry = baseline[concept_key]
            entry['stock_count'] += 1
            entry['mention_count'] += mention_count
            entry['stocks'].append({
                'name': name,
                'code': code,
                'mention_count': mention_count,
                'first_mentioned': first_date,
                'last_mentioned': last_date,
            })

            # 更新日期范围
            if first_date and (not entry['first_mentioned'] or first_date < entry['first_mentioned']):
                entry['first_mentioned'] = first_date
            if last_date and (not entry['last_mentioned'] or last_date > entry['last_mentioned']):
                entry['last_mentioned'] = last_date

        # 排序 stocks 并按提及次数取 top
        for key in baseline:
            entry = baseline[key]
            entry['stocks'].sort(key=lambda s: s['mention_count'], reverse=True)
            entry['top_stocks'] = [s['name'] for s in entry['stocks'][:5]]

        return baseline

    def compare_with_blogger(self, blogger_uid: str, blogger_name: str,
                             blogger_views: Dict, baseline: Dict) -> Dict:
        """
        对比单博主观点与研报基线，返回信号列表
        blogger_views: 从 latest.json 加载的观点数据

        返回: {signals: [...], summary: {...}}
        """
        signals = []
        blogger_stocks = {}  # {concept: [stock_names]}

        # 从博主观点中提取板块→股票映射
        # latest.json 格式: {stocks: {code: {name, sentiment, ...}}, sectors: [...]}
        if isinstance(blogger_views, dict):
            sectors = blogger_views.get('sectors', [])
            stocks = blogger_views.get('stocks', {})

            for sector_name in sectors:
                if isinstance(sector_name, str):
                    blogger_stocks.setdefault(sector_name, [])

            for code, stock_info in stocks.items():
                name = stock_info.get('name', code)
                # 尝试匹配到概念
                matched_concept = self._match_stock_to_concept(name, baseline)
                if matched_concept:
                    blogger_stocks.setdefault(matched_concept, []).append(name)

        # 对比每个概念
        for concept_key, baseline_entry in baseline.items():
            baseline_stocks = {s['name'] for s in baseline_entry['stocks']}
            blog_stocks_in_concept = set()

            # 找博主在这个概念下提到的股票
            for b_concept, b_stocks in blogger_stocks.items():
                if self._concepts_overlap(concept_key, b_concept):
                    blog_stocks_in_concept.update(b_stocks)

            if not blog_stocks_in_concept and not any(
                self._concepts_overlap(concept_key, bc) for bc in blogger_stocks
            ):
                continue

            # 找共同股票
            same_stocks = blog_stocks_in_concept & baseline_stocks

            # 获取博主最早提及日期
            blogger_first = blogger_views.get('first_seen', '') or ''
            research_first = baseline_entry.get('first_mentioned', '')

            if same_stocks:
                signal_type = "共振_强信号"
                signal_detail = f"博主{blogger_name}和研报都关注 {', '.join(same_stocks)}"
            else:
                signal_type = "共振_弱信号"
                signal_detail = f"博主{blogger_name}和研报都关注{concept_key}但具体股票不同"

            signals.append({
                'type': signal_type,
                'concept': concept_key,
                'blogger': blogger_name,
                'blogger_uid': blogger_uid,
                'same_stocks': list(same_stocks),
                'blogger_stocks': list(blog_stocks_in_concept),
                'research_stocks': list(baseline_stocks)[:10],
                'blogger_first': blogger_first,
                'research_first': research_first,
                'detail': signal_detail,
            })

        # 博主独有概念：博客提了但研报没有
        for b_concept, b_stocks in blogger_stocks.items():
            if b_stocks and not any(
                self._concepts_overlap(b_concept, bc) for bc in baseline
            ):
                signals.append({
                    'type': '博主独有',
                    'concept': b_concept,
                    'blogger': blogger_name,
                    'blogger_uid': blogger_uid,
                    'blogger_stocks': list(b_stocks),
                    'detail': f"博主{blogger_name}关注{b_concept}({', '.join(b_stocks)})，研报库未覆盖",
                })

        # 研报独有概念（标记，在后面 generate_signals 中汇总）
        return signals

    def generate_signals(self) -> List[Dict]:
        """
        为所有博主生成交叉信号
        返回 signals 列表
        """
        # 构建基线
        baseline = self.build_baseline()
        if not baseline:
            print("[CrossAnalyzer] 研报基线为空，跳过交叉分析")
            return []

        # 保存基线
        baseline_output = {
            'generated': datetime.now(TZ).strftime('%Y-%m-%d'),
            'baseline': baseline,
        }
        baseline_path = os.path.join(self.cross_dir, 'research_baseline.json')
        with open(baseline_path, 'w', encoding='utf-8') as f:
            json.dump(baseline_output, f, ensure_ascii=False, indent=2)
        print(f"[CrossAnalyzer] 研报基线已保存: {baseline_path}")

        # 加载所有博主观点
        all_signals = []
        all_blogger_concepts = set()

        if os.path.isdir(self.views_dir):
            for uid in os.listdir(self.views_dir):
                latest_path = os.path.join(self.views_dir, uid, 'latest.json')
                if not os.path.exists(latest_path):
                    continue

                try:
                    with open(latest_path, 'r', encoding='utf-8') as f:
                        views = json.load(f)
                except Exception:
                    continue

                # 尝试从 bloggers.json 获取名称
                blogger_name = uid
                bloggers_path = os.path.join(self.project_root, 'bloggers.json')
                if os.path.exists(bloggers_path):
                    try:
                        with open(bloggers_path, 'r', encoding='utf-8') as f:
                            bloggers = json.load(f)
                        if uid in bloggers:
                            blogger_name = bloggers[uid].get('name', uid)
                    except Exception:
                        pass

                signals = self.compare_with_blogger(uid, blogger_name, views, baseline)
                all_signals.extend(signals)

                # 收集博主提到的概念
                for s in signals:
                    all_blogger_concepts.add(s.get('concept', ''))

        # 研报独有信号：研报概念中，任何博主都没提过的
        for concept_key in baseline:
            if not any(self._concepts_overlap(concept_key, bc) for bc in all_blogger_concepts):
                entry = baseline[concept_key]
                all_signals.append({
                    'type': '研报独有',
                    'concept': concept_key,
                    'blogger': '(所有博主)',
                    'blogger_uid': '',
                    'stock_count': entry['stock_count'],
                    'mention_count': entry['mention_count'],
                    'top_stocks': entry.get('top_stocks', []),
                    'detail': f"研报关注{concept_key}({entry['stock_count']}只股票, {entry['mention_count']}次提及)，博主未覆盖",
                })

        # 保存信号
        signals_output = {
            'generated': datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S'),
            'total_signals': len(all_signals),
            'signals': all_signals,
        }
        signals_path = os.path.join(self.cross_dir, 'cross_signals.json')
        with open(signals_path, 'w', encoding='utf-8') as f:
            json.dump(signals_output, f, ensure_ascii=False, indent=2)
        print(f"[CrossAnalyzer] 交叉信号已保存: {signals_path} ({len(all_signals)} 条)")

        return all_signals

    def _load_edits(self) -> Dict:
        """加载人工编辑记录"""
        edits_path = os.path.join(self.zsxq_dir, 'stock_pool_edits.json')
        if os.path.exists(edits_path):
            try:
                with open(edits_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _concepts_overlap(self, concept_a: str, concept_b: str) -> bool:
        """判断两个概念是否重叠"""
        if not concept_a or not concept_b:
            return False
        a_lower = concept_a.lower().strip()
        b_lower = concept_b.lower().strip()
        if a_lower == b_lower:
            return True
        if a_lower in b_lower or b_lower in a_lower:
            return True
        # 检查关键词重叠
        import re
        a_parts = set(re.split(r'[/、,，]', a_lower))
        b_parts = set(re.split(r'[/、,，]', b_lower))
        return bool(a_parts & b_parts)

    def _match_stock_to_concept(self, stock_name: str, baseline: Dict):
        return None

    # == DEPRECATED (old version kept for reference) ==
    def _match_stock_to_concept_old(self, stock_name: str, baseline: Dict):
        """匹配股票到基线概念"""
        for concept_key, entry in baseline.items():
            for s in entry.get('stocks', []):
                if s['name'] == stock_name:
                    return concept_key
        return None


if __name__ == "__main__":
    ca = CrossAnalyzer()
    signals = ca.generate_signals()
    print(f"\n生成 {len(signals)} 条交叉信号")

    # 按类型分组统计
    from collections import Counter
    type_counts = Counter(s['type'] for s in signals)
    print("\n信号类型分布:")
    for stype, count in type_counts.most_common():
        desc = SIGNAL_RULES.get(stype, '')
        print(f"  {stype}: {count}条 - {desc}")

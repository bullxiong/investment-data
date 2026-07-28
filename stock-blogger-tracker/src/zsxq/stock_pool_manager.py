#!/usr/bin/env python3
"""股票池管理系统
适配路径：
  config → data/zsxq/config.json
  excel → data/zsxq/stock_pool.xlsx
"""

import pandas as pd
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import re

class StockPoolManager:
    def __init__(self, config_path: str = "data/zsxq/config.json"):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)

        self.excel_file = self.config['stock_pool']['excel_file']
        self.cold_thresholds = self.config['stock_pool']['cold_thresholds']

        self.columns = [
            '股票代码', '股票名称', '一级概念', '概念板块', '详细介绍',
            '星球链接', '加入日期', '最后提及日期', '冷却标记', '提及次数'
        ]

        self._load_or_create_pool()

    def _load_or_create_pool(self):
        """加载或创建股票池"""
        if Path(self.excel_file).exists():
            self.df = pd.read_excel(self.excel_file, dtype={'股票代码': str})
            for col in self.columns:
                if col not in self.df.columns:
                    self.df[col] = None
            if '股票代码' in self.df.columns:
                self.df['股票代码'] = self.df['股票代码'].apply(self.normalize_stock_code)

            # 确保字符串列为object类型
            for col in ['一级概念', '详细介绍', '星球链接', '冷却标记']:
                if col in self.df.columns:
                    self.df[col] = self.df[col].astype('object')
            # 确保提及次数为数值
            if '提及次数' in self.df.columns:
                self.df['提及次数'] = pd.to_numeric(self.df['提及次数'], errors='coerce').fillna(0).astype(int)
        else:
            self.df = pd.DataFrame(columns=self.columns)

    def normalize_stock_code(self, code: str) -> str:
        """标准化股票代码"""
        code = str(code).strip()
        if code.isdigit() and len(code) <= 6:
            return code.zfill(6)
        return code

    def add_or_update_stock(self, stock_code: str, stock_name: str,
                           concept: str, description: str = "",
                           zsxq_link: str = "",
                           concept_l1: str = "",
                           first_mention_date: str = "",
                           last_mention_date: str = "") -> Tuple[bool, str]:
        """
        添加或更新股票
        concept: L2 细分概念（可含多个，顿号分隔）
        concept_l1: L1 一级概念（可选，自动推断）
        first_mention_date: 首次提及日期（来自帖子时间，为空则用今天）
        last_mention_date: 最近提及日期（来自帖子时间，为空则用今天）
        返回: (是否为新股票, 操作消息)
        """
        from src.zsxq.concept_hierarchy import parse_concepts
        stock_code = self.normalize_stock_code(stock_code)
        current_date = datetime.now().strftime('%Y-%m-%d')
        effective_last = last_mention_date or current_date
        effective_first = first_mention_date or current_date

        # 自动推断 L1
        if not concept_l1 and concept:
            concept_l1, _ = parse_concepts(concept)

        self.df['股票代码'] = self.df['股票代码'].astype(str)
        existing = self.df[self.df['股票代码'] == stock_code]

        if len(existing) > 0:
            idx = existing.index[0]
            self.df.loc[idx, '最后提及日期'] = effective_last
            # 提及次数+1
            current_count = self.df.loc[idx, '提及次数']
            self.df.loc[idx, '提及次数'] = (int(current_count) if pd.notna(current_count) else 0) + 1
            if description:
                self.df.loc[idx, '详细介绍'] = description
            if zsxq_link:
                self.df.loc[idx, '星球链接'] = zsxq_link
            if concept:
                self.df.loc[idx, '概念板块'] = concept
            if concept_l1:
                self.df.loc[idx, '一级概念'] = concept_l1
            self.df.loc[idx, '冷却标记'] = ''
            return False, f"更新股票: {stock_name}({stock_code})"
        else:
            new_row = {
                '股票代码': stock_code,
                '股票名称': stock_name,
                '一级概念': concept_l1 or "",
                '概念板块': concept,
                '详细介绍': description,
                '星球链接': zsxq_link,
                '加入日期': effective_first,
                '最后提及日期': effective_last,
                '冷却标记': '',
                '提及次数': 1
            }
            self.df = pd.concat([self.df, pd.DataFrame([new_row])], ignore_index=True)
            return True, f"新增股票: {stock_name}({stock_code}) - [{concept_l1}]{concept}"

    def update_cold_marks(self):
        """更新冷却标记"""
        if len(self.df) == 0:
            return []

        today = datetime.now()
        warning_days = self.cold_thresholds['warning_days']
        critical_days = self.cold_thresholds['critical_days']

        updates = []

        for idx, row in self.df.iterrows():
            last_mention = row['最后提及日期']
            if pd.isna(last_mention):
                continue

            if isinstance(last_mention, str):
                last_date = datetime.strptime(last_mention, '%Y-%m-%d')
            else:
                last_date = pd.to_datetime(last_mention)

            days_diff = (today - last_date).days

            old_mark = row['冷却标记']
            new_mark = ''

            if days_diff >= critical_days:
                new_mark = f'⚠️ {critical_days}天未提及'
            elif days_diff >= warning_days:
                new_mark = f'⚡ {warning_days}天未提及'

            if new_mark != old_mark:
                self.df.loc[idx, '冷却标记'] = new_mark
                if new_mark:
                    updates.append(f"{row['股票名称']}({row['股票代码']}): {new_mark}")

        return updates

    def sort_by_concept(self):
        """按一级概念、二级概念排序"""
        if len(self.df) > 0:
            sort_cols = ['一级概念', '概念板块', '加入日期'] if '一级概念' in self.df.columns else ['概念板块', '加入日期']
            self.df = self.df.sort_values(
                by=sort_cols,
                na_position='last'
            ).reset_index(drop=True)

    def save(self):
        """保存到Excel"""
        self.sort_by_concept()
        Path(self.excel_file).parent.mkdir(parents=True, exist_ok=True)
        self.df.to_excel(self.excel_file, index=False, engine='openpyxl')

    def get_existing_concepts(self) -> List[str]:
        """获取所有已存在的概念板块"""
        concepts = self.df['概念板块'].dropna().unique().tolist()
        return sorted(concepts)

    def find_similar_concepts(self, new_concept: str, threshold: float = 0.7) -> List[str]:
        """查找相似的概念板块"""
        existing = self.get_existing_concepts()
        similar = []

        for concept in existing:
            similarity = self._calculate_similarity(new_concept, concept)
            if similarity >= threshold:
                similar.append(concept)

        return similar

    def _calculate_similarity(self, s1: str, s2: str) -> float:
        """计算两个字符串的相似度"""
        s1 = s1.lower().strip()
        s2 = s2.lower().strip()

        if s1 == s2:
            return 1.0

        set1 = set(s1)
        set2 = set(s2)
        intersection = set1 & set2
        union = set1 | set2

        if not union:
            return 0.0

        return len(intersection) / len(union)

    def merge_from_external(self, external_df: pd.DataFrame) -> Dict:
        """
        从外部表格合并数据（去重）
        返回: {added: int, updated: int, skipped: int}
        """
        result = {'added': 0, 'updated': 0, 'skipped': 0}

        for _, row in external_df.iterrows():
            stock_code = self.normalize_stock_code(row.get('股票代码', ''))
            stock_name = row.get('股票名称', '')
            concept = row.get('概念板块', row.get('概念', ''))

            if not stock_code or not stock_name:
                result['skipped'] += 1
                continue

            is_new, msg = self.add_or_update_stock(
                stock_code=stock_code,
                stock_name=stock_name,
                concept=concept,
                description=row.get('详细介绍', ''),
                zsxq_link=row.get('星球链接', '')
            )

            if is_new:
                result['added'] += 1
            else:
                result['updated'] += 1

        return result

    def get_stock_by_name(self, name: str) -> Optional[Dict]:
        """根据股票名称（包括简写）查找股票"""
        name = name.strip()

        # 完全匹配
        match = self.df[self.df['股票名称'] == name]
        if len(match) > 0:
            return match.iloc[0].to_dict()

        # 2字简写匹配
        if len(name) == 2:
            match = self.df[self.df['股票名称'].str.contains(name, na=False)]
            if len(match) > 0:
                return match.iloc[0].to_dict()

        # 模糊匹配
        match = self.df[self.df['股票名称'].str.contains(name, na=False, regex=False)]
        if len(match) > 0:
            return match.iloc[0].to_dict()

        return None


if __name__ == "__main__":
    manager = StockPoolManager()
    print(f"股票池已加载，共 {len(manager.df)} 只股票")
    print(f"概念板块: {manager.get_existing_concepts()}")

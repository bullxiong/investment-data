#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI股票分析器 v2
- 全文直接送 DeepSeek 分析，不依赖前置候选名单提取
- 分析完成后自动通过东方财富API查询股票代码并缓存
- 依赖路径已适配项目结构
"""

import json
import os
import sys
import re
from typing import Dict, List, Optional, Tuple
from openai import OpenAI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from prompt_registry import load_prompt

from src.zsxq.stock_code_lookup import batch_lookup
from src.zsxq.concept_hierarchy import parse_concepts


class StockAnalyzer:
    def __init__(self, api_key: str = None, config_path: str = "data/zsxq/config.json"):
        if not api_key:
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    api_key = config.get('deepseek', {}).get('api_key', '')
            except Exception:
                api_key = ''

        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com"
        )

    def analyze_post(self, post_content: str, post_title: str) -> List[Dict]:
        """
        直接分析帖子全文，提取股票信息并查询代码
        返回: [{stock_name, stock_code, concept, analysis}]

        不再需要传入候选名单——全文直接交给 DeepSeek 判断
        """
        # 全文过长时截断（DeepSeek context限制，一般不会触发）
        max_content_len = 4000
        if len(post_content) > max_content_len:
            post_content = post_content[:max_content_len] + "\n...(内容截断)"

        template = load_prompt("stock_analyzer")
        prompt = template.format(post_title=post_title, post_content=post_content)

        try:
            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": "你是专业的A股市场分析专家，擅长从文本中识别和提取个股信息。只返回JSON，不返回其他内容。"},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=2048,
                temperature=0.1
            )

            response_text = response.choices[0].message.content.strip()

            # 提取JSON数组
            json_match = re.search(r'\[.*\]', response_text, re.DOTALL)
            if json_match:
                response_text = json_match.group(0)

            stocks = json.loads(response_text)
            if not isinstance(stocks, list):
                return []

            # 过滤掉没有 stock_name 的条目
            stocks = [s for s in stocks if s.get('stock_name')]

            if not stocks:
                return stocks

            # 批量查询股票代码（自动缓存）
            names = [s['stock_name'] for s in stocks]
            code_map = batch_lookup(names)

            for stock in stocks:
                name = stock['stock_name']
                code = code_map.get(name)
                stock['stock_code'] = code if code else 'unknown'
                # 解析两层概念
                raw_concept = stock.get('concept', '')
                l1, l2 = parse_concepts(raw_concept)
                stock['concept_l1'] = l1    # 一级概念（大板块）
                stock['concept_l2'] = l2    # 二级概念（细分标签，可多个）
                stock['concept'] = l2       # 保持向后兼容，concept 存 l2

            return stocks

        except Exception as e:
            print(f"[Analyzer] AI分析出错: {e}")
            return []

    def match_or_query_concept(self, proposed_concept: str,
                               existing_concepts: List[str]) -> Tuple[str, bool]:
        """
        匹配或建议新概念
        返回: (最终概念名称, 是否需要人工审核)
        """
        if not proposed_concept:
            return "待分类", True

        proposed_lower = proposed_concept.lower().strip()

        for existing in existing_concepts:
            if proposed_lower == existing.lower().strip():
                return existing, False
            if self._is_similar_concept(proposed_concept, existing):
                return existing, False

        return proposed_concept, True

    def _is_similar_concept(self, concept1: str, concept2: str, threshold: float = 0.7) -> bool:
        """判断两个概念是否相似（基于字符集合Jaccard相似度）"""
        c1 = set(concept1.lower().strip())
        c2 = set(concept2.lower().strip())
        if not c1 or not c2:
            return False
        intersection = c1 & c2
        union = c1 | c2
        return len(intersection) / len(union) >= threshold


if __name__ == "__main__":
    analyzer = StockAnalyzer()

    test_content = """
投资建议：
重点关注机器人散热标的：银轮股份、三花智控、拓普集团

看好机器人宇树链（鸣志电器、美湖股份、中大力德、绿的谐波）、
银河通用链（亿和控股、兆威机电、雷赛智能）、
本体看好优必选，T链（五洲新春、浙江荣泰、震裕科技）
"""

    print("测试全文分析...")
    result = analyzer.analyze_post(test_content, "机器人板块推荐")
    print(f"\n识别到 {len(result)} 只股票:")
    for s in result:
        print(f"  {s['stock_name']} ({s['stock_code']}) - {s['concept']}")
        print(f"    {s['analysis']}")

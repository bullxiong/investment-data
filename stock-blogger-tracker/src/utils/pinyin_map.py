# -*- coding: utf-8 -*-
"""
拼音映射表生成器
================
为股票名称和行业/概念名称生成拼音映射，支持博主拼音缩写解码。

功能：
  build_pinyin_map()    → 生成拼音→股票代码映射 (pinyin_stocks.json)
  build_concept_pinyin() → 生成板块拼音映射 (pinyin_industries.json)
  search_pinyin(text)   → 根据拼音输入搜索匹配的股票/板块

多音字处理：
  - 使用 pypinyin 的 heteronym=True 获取每个字的所有可能读音
  - 生成所有读音组合的全拼和首字母缩写
  - 默认读音（lazy_pinyin）优先，多音变体作为补充
"""

import json
import os
import sys
from collections import defaultdict
from itertools import product

from pypinyin import lazy_pinyin, pinyin, Style

# ── 路径配置 ─────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data", "stock_db")

STOCKS_PATH     = os.path.join(DATA_DIR, "stocks.json")
INDUSTRY_NAMES  = os.path.join(DATA_DIR, "industry_names.json")
CONCEPT_NAMES   = os.path.join(DATA_DIR, "concept_names.json")

OUT_STOCKS      = os.path.join(DATA_DIR, "pinyin_stocks.json")
OUT_INDUSTRIES  = os.path.join(DATA_DIR, "pinyin_industries.json")


# ── 工具函数 ─────────────────────────────────────────────

def _all_pinyin_variants(name: str):
    """
    返回一个中文名称的所有拼音变体。
    
    返回值: (variants_full, variants_abbr)
      - variants_full: [(full_pinyin_str, reading_tuple), ...]  全拼变体
      - variants_abbr: [(abbr_str, reading_tuple), ...]         首字母缩写变体
    
    每个变体都标注了使用的读音元组，方便调试。
    """
    # 获取每个字的所有可能读音（去重）
    char_readings = []
    for py_list in pinyin(name, heteronym=True):
        # 去重并保持稳定顺序
        seen = set()
        unique = []
        for r in py_list:
            if r not in seen:
                seen.add(r)
                unique.append(r)
        char_readings.append(unique)
    
    # 生成所有读音组合
    variants_full = []
    variants_abbr = []
    
    for combo in product(*char_readings):
        full = "".join(combo)
        abbr = "".join(r[0] for r in combo)
        variants_full.append((full, combo))
        variants_abbr.append((abbr, combo))
    
    return variants_full, variants_abbr


def _default_reading(name: str):
    """返回默认读音（lazy_pinyin 的结果）。"""
    full_chars = lazy_pinyin(name)
    full = "".join(full_chars)
    abbr = "".join(c[0] for c in full_chars)
    return full, abbr


# ── 核心函数 ─────────────────────────────────────────────

def build_pinyin_map():
    """
    从 stocks.json 读取股票数据，生成拼音→股票映射表。
    
    输出格式:
    {
      "payh": [{"code": "000001", "name": "平安银行"}],
      "zgpa": [{"code": "601318", "name": "中国平安"}],
      ...
    }
    
    同时包含拼音全拼映射和首字母缩写映射。
    """
    with open(STOCKS_PATH, "r", encoding="utf-8") as f:
        stocks = json.load(f)
    
    # 拼音→股票列表
    full_map = defaultdict(list)   # 全拼映射
    abbr_map = defaultdict(list)   # 首字母缩写映射
    
    # 统计
    total_stocks = len(stocks)
    polyphone_count = 0
    total_variants = 0
    
    for stock in stocks:
        code = stock["code"]
        name = stock["name"]
        entry = {"code": code, "name": name}
        
        # 默认读音
        default_full, default_abbr = _default_reading(name)
        full_map[default_full].append(entry)
        abbr_map[default_abbr].append(entry)
        
        # 多音字变体
        variants_full, variants_abbr = _all_pinyin_variants(name)
        
        # 如果有多音字（组合数 > 1 说明至少一个字有多个读音），标注并添加变体
        has_polyphone = len(variants_full) > 1
        if has_polyphone:
            polyphone_count += 1
            for v_full, _ in variants_full:
                if v_full != default_full:
                    full_map[v_full].append(entry)
                    total_variants += 1
            for v_abbr, _ in variants_abbr:
                if v_abbr != default_abbr:
                    abbr_map[v_abbr].append(entry)
    
    # 合并：full_map 和 abbr_map 的去重合并
    # 使用 dict，key 为拼音，value 为去重后的股票列表
    merged = {}
    
    def add_to_merged(pinyin_key, entries):
        if pinyin_key not in merged:
            merged[pinyin_key] = []
        existing_codes = {e["code"] for e in merged[pinyin_key]}
        for e in entries:
            if e["code"] not in existing_codes:
                merged[pinyin_key].append(e)
                existing_codes.add(e["code"])
    
    for k, v in full_map.items():
        add_to_merged(k, v)
    for k, v in abbr_map.items():
        add_to_merged(k, v)
    
    # 输出
    with open(OUT_STOCKS, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    
    # 统计
    unique_keys = len(merged)
    
    # 找到冲突最多的（同一拼音对应多只股票）
    conflicts = [(k, len(v)) for k, v in merged.items() if len(v) > 1]
    conflicts.sort(key=lambda x: -x[1])
    top_conflicts = conflicts[:10]
    
    print(f"[pinyin_stocks] 完成！")
    print(f"  总股票数:      {total_stocks}")
    print(f"  含多音字股票:  {polyphone_count}")
    print(f"  多音字变体数:  {total_variants}")
    print(f"  总映射条目:    {sum(len(v) for v in merged.values())}")
    print(f"  唯一拼音键:    {unique_keys}")
    print(f"  冲突映射数:    {len(conflicts)}")
    print(f"  冲突最多的前 5 个拼音:")
    for pinyin_key, count in top_conflicts[:5]:
        names = [e["name"] for e in merged[pinyin_key]]
        print(f"    {pinyin_key} ({count}只): {', '.join(names[:5])}{'...' if len(names) > 5 else ''}")
    
    return merged


def build_concept_pinyin():
    """
    从 industry_names.json 和 concept_names.json 读取行业/概念名称，
    生成拼音→板块映射表。
    
    输出格式:
    {
      "bankuai_pinyin": [{"code": "881156", "name": "银行"}],
      ...
    }
    """
    result = {}
    
    # 读取行业名称
    with open(INDUSTRY_NAMES, "r", encoding="utf-8") as f:
        industries = json.load(f)
    
    # 读取概念名称
    with open(CONCEPT_NAMES, "r", encoding="utf-8") as f:
        concepts = json.load(f)
    
    all_concepts = industries + concepts
    
    for item in all_concepts:
        name = item["name"]
        code = item["code"]
        entry = {"code": code, "name": name}
        
        # 默认读音
        default_full, default_abbr = _default_reading(name)
        
        # 添加映射
        for key in {default_full, default_abbr}:
            if key not in result:
                result[key] = []
            # 去重
            if entry not in result[key]:
                result[key].append(entry)
        
        # 多音字变体
        variants_full, variants_abbr = _all_pinyin_variants(name)
        
        for v_full, _ in variants_full:
            if v_full != default_full and v_full not in result:
                result[v_full] = []
            if v_full != default_full and entry not in result.get(v_full, []):
                result.setdefault(v_full, []).append(entry)
        
        for v_abbr, _ in variants_abbr:
            if v_abbr != default_abbr and entry not in result.get(v_abbr, []):
                result.setdefault(v_abbr, []).append(entry)
    
    with open(OUT_INDUSTRIES, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    unique_keys = len(result)
    conflicts = [(k, len(v)) for k, v in result.items() if len(v) > 1]
    conflicts.sort(key=lambda x: -x[1])
    
    print(f"\n[pinyin_industries] 完成！")
    print(f"  行业数:        {len(industries)}")
    print(f"  概念数:        {len(concepts)}")
    print(f"  总板块数:      {len(all_concepts)}")
    print(f"  唯一拼音键:    {unique_keys}")
    print(f"  冲突映射数:    {len(conflicts)}")
    if conflicts:
        print(f"  冲突最多的前 5 个拼音:")
        for pinyin_key, count in conflicts[:5]:
            names = [e["name"] for e in result[pinyin_key]]
            print(f"    {pinyin_key} ({count}个): {', '.join(names[:5])}")
    
    return result


def search_pinyin(text: str, db_type: str = "stocks"):
    """
    根据拼音字符串搜索匹配的股票或板块。
    
    参数:
      text: 拼音字符串，如 "payh" 或 "zsyy"
      db_type: "stocks" 或 "industries"
    
    返回:
      匹配结果列表，每项包含 code 和 name
    """
    if db_type == "stocks":
        map_path = OUT_STOCKS
    elif db_type == "industries":
        map_path = OUT_INDUSTRIES
    else:
        raise ValueError(f"未知的 db_type: {db_type}，可选 stocks/industries")
    
    if not os.path.exists(map_path):
        print(f"[search_pinyin] 映射文件不存在，请先运行 build_pinyin_map() 或 build_concept_pinyin()")
        return []
    
    with open(map_path, "r", encoding="utf-8") as f:
        pinyin_map = json.load(f)
    
    text_lower = text.lower().strip()
    
    # 精确匹配
    if text_lower in pinyin_map:
        return pinyin_map[text_lower]
    
    # 前缀匹配（如输入 "zg" 匹配所有以 "zg" 开头的股票）
    results = []
    for key, entries in pinyin_map.items():
        if key.startswith(text_lower):
            for e in entries:
                if e not in results:
                    results.append(e)
    
    return results


# ── 主程序 ───────────────────────────────────────────────

if __name__ == "__main__":
    # 确保输出使用 UTF-8
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    
    print("=" * 60)
    print("  拼音映射表生成器")
    print("=" * 60)
    
    # 1. 生成股票拼音映射
    print("\n>>> 生成股票拼音映射...")
    stock_map = build_pinyin_map()
    
    # 2. 生成板块拼音映射
    print("\n>>> 生成板块拼音映射...")
    industry_map = build_concept_pinyin()
    
    # 3. 测试搜索
    print("\n>>> 搜索测试...")
    
    test_queries = ["payh", "zsyy", "zgpa", "gzmj", "zg"]
    for q in test_queries:
        results = search_pinyin(q, "stocks")
        if results:
            names = [r["name"] for r in results[:5]]
            print(f"  '{q}' → {names}{'...' if len(results) > 5 else ''}  ({len(results)} 结果)")
        else:
            print(f"  '{q}' → 无结果")
    
    print("\n" + "=" * 60)
    print("  全部完成！")
    print("=" * 60)

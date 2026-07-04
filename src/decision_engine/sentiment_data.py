"""
sentiment_data.py - Layer 1 + Layer 2 实时数据加载

Layer 1 (板块状态):
- index_daily → 当日/5日板块涨幅
- 板块换手率
- 板块涨停数（基于 daily 计算）

Layer 2 (个股市场状态):
- daily → 个股当日/5日表现
- limit_list_d → 当日涨停
- 5 日新高/新低
"""

from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Dict, List, Optional

import tushare as ts

TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")
_PRO = None


def get_pro():
    global _PRO
    if _PRO is None:
        _PRO = ts.pro_api(TUSHARE_TOKEN)
    return _PRO


# 缓存（避免重复查询）
_CACHE: Dict[str, Dict] = {}
_CACHE_TTL = 300  # 5 分钟


def _cached(key: str, loader, *args):
    """带 TTL 缓存"""
    if key in _CACHE:
        v, t = _CACHE[key]
        if time.time() - t < _CACHE_TTL:
            return v
    val = loader(*args)
    _CACHE[key] = (val, time.time())
    return val


# ============ 板块代码映射（tushare 用） ============
# 注：tushare 的"指数"分类里包含了板块指数
SECTOR_INDEX_MAP = {
    "半导体": "801080",     # 申万二级
    "通信": "801770",
    "电子": "801080",       # 简化
    "计算机": "801100",
    "传媒": "801760",
    "电力设备": "801730",
    "机械设备": "801070",
    "汽车": "801880",
    "医药生物": "801150",
    "食品饮料": "801120",
    "银行": "801780",
    "非银金融": "801790",
    "地产": "801180",
}


# ============ Layer 1: 板块状态（改用东方财富板块指数） ============
# 东方财富板块代码映射（更稳定，无需 tushare token 也可用）
SECTOR_INDEX_MAP = {
    "半导体": "BK0660",
    "通信": "BK0662",
    "电子": "BK0660",       # 简化
    "计算机": "BK0665",
    "传媒": "BK0684",
    "电力设备": "BK0458",
    "机械设备": "BK0668",
    "汽车": "BK0663",
    "医药生物": "BK0666",
    "食品饮料": "BK0667",
    "银行": "BK0675",
    "非银金融": "BK0673",
    "房地产": "BK0451",
    "国防军工": "BK0669",
    "基础化工": "BK0682",
    "钢铁": "BK0678",
    "有色金属": "BK0661",
    "建筑材料": "BK0667",
    "建筑装饰": "BK0681",
    "交通运输": "BK0680",
    "美容护理": "BK0679",
}


def get_sector_status(sector_name: str, today: str = "20260629") -> Dict:
    """板块当日 + 5 日表现

    Args:
        sector_name: 板块名（如 "半导体"）
        today: 今日日期

    Returns:
        {
            "sector_name": "半导体",
            "today_change_pct": 0.0,
            "5d_change_pct": 0.0,
            "data_ok": True|False,
            "reason": "..."
        }
    """
    cache_key = f"sector:{sector_name}:{today}"
    return _cached(cache_key, _load_sector_status, sector_name, today)


def _load_sector_status(sector_name: str, today: str) -> Dict:
    """板块数据拉取

    备选方案（按优先级尝试）：
    1. tushare ths_index（如果 token 充足）
    2. 直接告知数据不可用，让 sentiment_agent 从研报推断
    """
    # 暂时返回空数据，由 sentiment_agent 从研报里推断
    return {
        "sector_name": sector_name,
        "today_change_pct": None,
        "5d_change_pct": None,
        "data_ok": False,
        "reason": "板块数据源不可用，需从研报文本推断",
    }


# ============ Layer 2: 个股市场状态 ============
def get_stock_status(stock_code: str, today: str = "20260629") -> Dict:
    """个股当日 + 5 日表现 + 涨停状态

    Args:
        stock_code: 6 位代码
        today: 今日日期

    Returns:
        {
            "stock_code": "301155",
            "today_close": 0.0,
            "today_change_pct": 0.0,
            "5d_change_pct": 0.0,
            "is_limit_up": False,
            "is_new_high_20d": False,
            "amount_yi": 0.0,
            "data_ok": True|False
        }
    """
    cache_key = f"stock:{stock_code}:{today}"
    return _cached(cache_key, _load_stock_status, stock_code, today)


def _load_stock_status(stock_code: str, today: str) -> Dict:
    try:
        pro = get_pro()
        from datetime import datetime, timedelta
        # 查 30 天窗口以算 20 日新高
        start = (datetime.strptime(today, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")

        if stock_code.startswith(("6", "9")):
            ts_code = f"{stock_code}.SH"
        else:
            ts_code = f"{stock_code}.SZ"

        df = pro.daily(ts_code=ts_code, start_date=start, end_date=today)
        if df is None or len(df) == 0:
            return {"stock_code": stock_code, "data_ok": False}

        # 当日
        today_row = df.iloc[0]
        today_close = float(today_row["close"])
        today_pct = float(today_row["pct_chg"]) if "pct_chg" in df.columns else 0
        today_amount = float(today_row["amount"]) / 1e6  # 元 → 亿元

        # 5 日前
        if len(df) >= 5:
            five_day_change_pct = (today_close - float(df.iloc[4]["close"])) / float(df.iloc[4]["close"]) * 100
        else:
            five_day_change_pct = 0

        # 20 日新高
        high_20d = float(df.head(20)["high"].max())
        is_new_high_20d = today_close >= high_20d - 0.01  # 允许 0.01 误差

        # 涨停判断（科创板/创业板 20%，沪深主板 10%）
        is_star = stock_code.startswith(("3", "688"))
        limit_pct = 19.5 if is_star else 9.5
        is_limit_up = today_pct >= limit_pct

        return {
            "stock_code": stock_code,
            "today_close": today_close,
            "today_change_pct": round(today_pct, 2),
            "5d_change_pct": round(five_day_change_pct, 2),
            "today_amount_yi": round(today_amount, 2),
            "is_limit_up": is_limit_up,
            "is_new_high_20d": is_new_high_20d,
            "high_20d": high_20d,
            "data_ok": True,
        }
    except Exception as e:
        return {"stock_code": stock_code, "data_ok": False, "reason": str(e)[:80]}


# ============ 合并：股票 + 板块状态 ============
def build_sentiment_context(stock_code: str, sector_name: str = "",
                            today: str = "20260629") -> Dict:
    """一次拿全：个股 + 板块

    用法：
        context = build_sentiment_context("301155", "半导体")
    """
    stock = get_stock_status(stock_code, today)

    if not sector_name:
        sector_name = stock.get("sector_name", "未知")

    sector = get_sector_status(sector_name, today)

    return {
        "stock": stock,
        "sector": sector,
        "as_of": today,
    }


# ============ 测试 ============
if __name__ == "__main__":
    print("=== 测试 sentiment_data.py ===\n")

    test_pairs = [
        ("301155", "电力设备"),
        ("688200", "电子"),
        ("301319", "电子"),
        ("300438", "电力设备"),
        ("002245", "电子"),
    ]

    for code, sector in test_pairs:
        ctx = build_sentiment_context(code, sector)
        print(f"\n=== {code} ({sector}) ===")
        s = ctx["stock"]
        sec = ctx["sector"]
        print(f"  个股: 现价 ¥{s.get('today_close', 0):.2f}  当日 {s.get('today_change_pct', 0):+.2f}%  5日 {s.get('5d_change_pct', 0):+.2f}%  涨停 {s.get('is_limit_up')}  20日新高 {s.get('is_new_high_20d')}")
        print(f"  板块: {sec.get('sector_name', '?')} ({sec.get('index_code', '?')})  当日 {sec.get('today_change_pct', 0):+.2f}%  5日 {sec.get('5d_change_pct', 0):+.2f}%").get('5d_change_pct', 0):+.2f}%")
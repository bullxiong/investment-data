"""
market_cap_parser.py - 研报市值描述 → 目标股价 转换器

功能：
1. 从研报 content 用正则提取"目标市值 N 亿"
2. 从 tushare 拿总股本
3. 转成"目标股价 = 目标市值 / 总股本"
4. 距离当前价多少 %
"""

from __future__ import annotations
import re
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional


# ============ 1. 提取研报中的目标市值 ============
# 注意："亿" 和 "e+" 都表示"亿"（研报里口语化用 e+）
TARGET_MC_PATTERNS = [
    # "目标市值 200 亿"
    r'目标市值\s*(\d+\.?\d*)\s*亿',
    # "目标市值 100e"
    r'目标市值\s*(\d+\.?\d*)\s*e',
    # "看到 400 亿"
    r'看到\s*(\d+\.?\d*)\s*亿',
    # "看到 700e+"
    r'看到\s*(\d+\.?\d*)\s*e\+?',
    # "目标 400 亿" / "目标至 400 亿"
    r'目标.{0,3}(?:至|到)?\s*(\d+\.?\d*)\s*亿',
    # "目标 100e+"（e+ 表示"亿+"）
    r'目标.{0,3}(\d+\.?\d*)\s*e\+',
    # "第一目标市值 500" / "第一目标 500"
    r'第一目标市值?\s*(\d+\.?\d*)',
    # "对应 N 亿市值"
    r'对应.{0,8}(\d+\.?\d*)\s*亿.{0,3}市值',
    # "目标 N 亿"（中文逗号分隔）
    r'目标[，,]\s*(\d+\.?\d*)\s*亿',
    # "市值 200 亿"（不带"目标"前缀也试）
    r'(?<![营销利增售])市值\s*(\d+\.?\d*)\s*亿',
]

# 排除：营收/利润/出货量/行业规模中的"亿"
EXCLUDE_CONTEXT = [
    '营收', '收入', '销售', '出货', '出货量', '产量', '产能',
    '利润', '净利', '净利润', '毛利', 'EPR', '增速', '增长',
    '票房', '票房收入', '产值', '市场规模', '规模', '行业',
    '市场规模达', '行业规模', '我国', '全国', '全球',
]

# A 股个股市值上限（用 2000 亿过滤异常）
MAX_SINGLE_MC_YI = 2000


def extract_target_market_cap(content: str, max_n: int = 5) -> List[float]:
    """提取研报中的"目标市值（亿元）"

    返回所有可能的目标市值列表（去重，倒序），最多 max_n 个
    """
    if not content:
        return []

    candidates = []

    for pat in TARGET_MC_PATTERNS:
        for m in re.finditer(pat, content):
            val = float(m.group(1))
            # 收紧过滤：5 亿 - 2000 亿（A股单股市值上限）
            if val < 5 or val > MAX_SINGLE_MC_YI:
                continue

            # 看上下文：是否在"营收/利润/行业"句子中
            start = max(0, m.start() - 50)
            end = min(len(content), m.end() + 50)
            context = content[start:end]

            is_excluded = any(kw in context for kw in EXCLUDE_CONTEXT)
            if is_excluded:
                continue

            candidates.append(val)

    # 去重，倒序
    candidates = sorted(set(candidates), reverse=True)
    return candidates[:max_n]


# ============ 2. 拿总股本（tushare） ============
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")
_TUSHARE_PRO = None


def get_tushare_pro():
    global _TUSHARE_PRO
    if _TUSHARE_PRO is None:
        import tushare as ts
        _TUSHARE_PRO = ts.pro_api(TUSHARE_TOKEN)
    return _TUSHARE_PRO


# 总股本缓存（避免重复查）
_TOTAL_SHARE_CACHE: Dict[str, Optional[float]] = {}


def get_total_share(stock_code: str, trade_date: str = None) -> Optional[float]:
    """拿总股本（亿股）

    Args:
        stock_code: 6 位代码
        trade_date: 交易日期（默认最近一个交易日）

    Returns:
        总股本（亿股），如 5.5；None 表示失败
    """
    if stock_code in _TOTAL_SHARE_CACHE:
        return _TOTAL_SHARE_CACHE[stock_code]

    try:
        pro = get_tushare_pro()
        if stock_code.startswith(("6", "9")):
            ts_code = f"{stock_code}.SH"
        else:
            ts_code = f"{stock_code}.SZ"

        # 自动找最近的交易日
        if trade_date:
            df = pro.daily_basic(
                ts_code=ts_code,
                trade_date=trade_date,
                fields="ts_code,total_share,total_mv"
            )
        else:
            # 不带 trade_date，返回最新
            df = pro.daily_basic(
                ts_code=ts_code,
                fields="ts_code,total_share,total_mv,trade_date",
                limit=5  # 取最近 5 天
            )

        if df is not None and len(df) > 0:
            # total_share 单位是"万股"
            shares_in_wan = float(df.iloc[0]["total_share"])
            shares_in_yi = shares_in_wan / 10000  # 万股 → 亿股
            _TOTAL_SHARE_CACHE[stock_code] = shares_in_yi
            return shares_in_yi
    except Exception as e:
        print(f"  [总股本查询失败 {stock_code}: {str(e)[:60]}]")

    _TOTAL_SHARE_CACHE[stock_code] = None
    return None


# ============ 3. 市值 → 股价 转换 ============
def market_cap_to_price(market_cap_yi: float, total_share_yi: float) -> float:
    """市值（亿） → 股价（元）"""
    if total_share_yi <= 0:
        return 0
    return round(market_cap_yi / total_share_yi, 2)


def calc_return_pct(current_price: float, target_price: float) -> float:
    """当前价 → 目标价 的预期收益率（%）"""
    if current_price <= 0 or target_price <= 0:
        return 0
    return round((target_price - current_price) / current_price * 100, 2)


# ============ 4. 主入口：从研报 + 股价 → 目标价 + 距离 ============
def analyze_price_targets(
    stock_code: str,
    stock_name: str,
    research_contents: List[str],
    current_price: float,
) -> Dict:
    """分析研报中的价格目标

    Args:
        stock_code: 6 位代码
        stock_name: 名称
        research_contents: 研报正文列表
        current_price: 当前价

    Returns:
        {
            "target_prices": [{"source": "...", "market_cap_yi": 200, "price": 36.4, "return_pct": 12.5}],
            "best_target": {...},  # 最乐观的
            "conservative_target": {...},  # 最保守的
            "summary": "...",
        }
    """
    # 1) 拿总股本
    total_share = get_total_share(stock_code)

    if total_share is None:
        return {
            "target_prices": [],
            "best_target": None,
            "conservative_target": None,
            "summary": f"{stock_name} 无总股本数据，跳过市值换算",
        }

    # 2) 提取所有研报中的目标市值
    all_mc_targets = []
    for content in research_contents:
        if not content:
            continue
        targets = extract_target_market_cap(content)
        if targets:
            # 取每条研报的最大目标（最乐观的）
            snippet = content[:80].replace("\n", " ")
            all_mc_targets.append({
                "mc_yi": max(targets),
                "snippet": snippet,
                "all_targets": targets,
            })

    # 3) 转股价
    target_prices = []
    for item in all_mc_targets:
        price = market_cap_to_price(item["mc_yi"], total_share)
        if price > 0:
            ret = calc_return_pct(current_price, price)
            target_prices.append({
                "source": item["snippet"],
                "market_cap_yi": item["mc_yi"],
                "price": price,
                "return_pct": ret,
                "suspicious": abs(ret) > 50,  # 上下行超 50% 标记可疑
            })

    # 4) 排序
    target_prices.sort(key=lambda x: x["price"], reverse=True)

    # 优先选非可疑的"最佳"目标
    non_suspect = [t for t in target_prices if not t["suspicious"]]
    if non_suspect:
        best = non_suspect[0]
        conservative = non_suspect[-1]
    else:
        # 全部可疑：仍返回最大值（带 suspicious 标记），让 arbiter 二次判断
        best = target_prices[0] if target_prices else None
        conservative = target_prices[-1] if target_prices else None

    n_suspicious = sum(1 for t in target_prices if t["suspicious"])
    summary = f"基于 {len(research_contents)} 条研报"
    if best:
        summary += f"，目标市值 {best['market_cap_yi']:.0f}亿 → 目标价 ¥{best['price']:.2f}（+{best['return_pct']:.1f}%）"
        if n_suspicious:
            summary += f"，{n_suspicious} 个候选被标记可疑（超 ±50%）"
    else:
        summary += "未发现市值目标，LLM 自行判断"

    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "total_share_yi": total_share,
        "target_prices": target_prices,
        "best_target": best,
        "conservative_target": conservative,
        "summary": summary,
    }


# ============ 测试 ============
if __name__ == "__main__":
    print("=== 测试 1：纯正则提取 ===")
    test_contents = [
        "我们认为公司 27 年估值对应第一目标 400 亿市值，继续重点推荐",
        "目标市值 200 亿，对应目标价 36 元",
        "20X 看到 700e+，主业 700 亿",
        "公司当前市值 100 亿，目标市值 500 亿",
        "营收 18.2 亿元，目标 700e+",  # 营收应被排除
        "据测算，行业规模 1000 亿",  # 规模应被排除
    ]
    for c in test_contents:
        targets = extract_target_market_cap(c)
        print(f"  '{c[:30]}...' → {targets}")

    print("\n=== 测试 2：拿总股本 ===")
    for code in ['301155', '688200', '301319', '688469']:
        s = get_total_share(code)
        print(f"  {code}: {s} 亿股")

    print("\n=== 测试 3：从 stock_pool 加载真实研报 + 算目标价 ===")
    db_path = "/workspace/user_input_files/stock_pool.db"
    if Path(db_path).exists():
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # 拿 5 只的研报
        cur.execute("""SELECT DISTINCT stock_code, stock_name FROM research_records
                       WHERE stock_code GLOB '[0-9]*' AND length(stock_code) = 6
                       LIMIT 5""")
        stocks = cur.fetchall()

        for code, name in stocks:
            cur.execute("""SELECT content FROM research_records WHERE stock_code = ?""", (code,))
            contents = [r[0] for r in cur.fetchall() if r[0]]
            result = analyze_price_targets(code, name, contents, current_price=0)
            print(f"\n[{code} {name}] 总股本 {result.get('total_share_yi', '?')} 亿股")
            print(f"  {result['summary']}")
            for tp in result["target_prices"][:3]:
                print(f"    - {tp['market_cap_yi']:.0f}亿 → ¥{tp['price']:.2f} (return {tp['return_pct']:+.1f}%)")

        conn.close()
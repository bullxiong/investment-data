#!/usr/bin/env python3
"""
src/technical_analysis/__init__.py
技术面分析模块 (Kimi 负责)

暴露接口:
    sync_stock_data(stocks, start_date, end_date, conn) -> None
    run_strategies(stocks, strategies, end_date, conn) -> List[Dict]
    run_backtest(signals, window_days, conn) -> List[Dict]

技术栈: baostock + pandas + numpy
策略: turtle_trade (海龟), ma_volume (均量), rps_breakout (RPS突破)
"""

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("technical_analysis")


def _baostock_code(code: str) -> str:
    """标准化股票代码为 baostock 格式"""
    if "." in code:
        return code  # 已经是 baostock 格式如 "sh.600519"
    if code.startswith("sh") or code.startswith("sz") or code.startswith("bj"):
        return code  # 前缀正确
    # 推断交易所
    if code.startswith("6"):
        return f"sh.{code}"
    elif code.startswith("3") or code.startswith("0"):
        return f"sz.{code}"
    elif code.startswith("8") or code.startswith("4"):
        return f"bj.{code}"
    return code


def sync_stock_data(
    stocks: List[str],
    start_date: str,
    end_date: str,
    conn: sqlite3.Connection,
) -> None:
    """
    使用 baostock 同步日K数据到 stock_daily 表。
    
    Args:
        stocks: 股票代码列表 ["sh.600519", "sz.000001"]
        start_date: "YYYY-MM-DD"
        end_date: "YYYY-MM-DD"
        conn: sqlite3 连接
    """
    try:
        import baostock as bs
    except ImportError:
        logger.warning("baostock 未安装，尝试 pip install baostock")
        logger.info("使用 mock 数据填充（演示模式）")
        _mock_sync_stock_data(stocks, start_date, end_date, conn)
        return

    bs.login()
    logger.info(f"同步日K数据: stocks={stocks}, range=[{start_date}, {end_date}]")

    for code in stocks:
        bscode = _baostock_code(code)
        # baostock 日期格式 YYYY-MM-DD
        rs = bs.query_history_k_data_plus(
            bscode,
            "date,open,high,low,close,volume,amount,turn,pctChg",
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            frequency="d",
            adjustflag="2",  # 后复权
        )

        rows = []
        while (rs.error_code == '0') & rs.next():
            row = rs.get_row_data()
            rows.append({
                "code": bscode,
                "trade_date": row[0],
                "open": float(row[1]) if row[1] else 0.0,
                "high": float(row[2]) if row[2] else 0.0,
                "low": float(row[3]) if row[3] else 0.0,
                "close": float(row[4]) if row[4] else 0.0,
                "volume": int(float(row[5])) if row[5] else 0,
                "amount": float(row[6]) if row[6] else 0.0,
                "turn": float(row[7]) if row[7] else 0.0,
                "pctChg": float(row[8]) if row[8] else 0.0,
                "adjustflag": 2,
            })

        if rows:
            # 批量写入，忽略重复
            for r in rows:
                conn.execute("""
                    INSERT OR IGNORE INTO stock_daily
                    (code, trade_date, open, high, low, close, volume, amount, turn, pctChg, adjustflag)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    r["code"], r["trade_date"], r["open"], r["high"], r["low"],
                    r["close"], r["volume"], r["amount"], r["turn"], r["pctChg"], r["adjustflag"]
                ))
            conn.commit()
            logger.info(f"  {bscode}: 写入 {len(rows)} 条日K数据")
        else:
            logger.warning(f"  {bscode}: 无数据")

    bs.logout()


def _mock_sync_stock_data(stocks, start_date, end_date, conn):
    """演示模式：用随机生成的 mock 数据填充"""
    logger.info("Mock 模式: 生成模拟日K数据")
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    dates = pd.date_range(start, end, freq="B")

    for code in stocks:
        bscode = _baostock_code(code)
        np.random.seed(hash(code) % 2**31)
        n = len(dates)
        # 生成随机 walks
        returns = np.random.normal(0.001, 0.02, n)
        prices = 100 * np.exp(np.cumsum(returns))

        rows = []
        for i, d in enumerate(dates):
            p = prices[i]
            v = np.random.randint(1000000, 10000000)
            rows.append({
                "code": bscode,
                "trade_date": d.strftime("%Y-%m-%d"),
                "open": round(p * (1 + np.random.normal(0, 0.005)), 2),
                "high": round(p * (1 + abs(np.random.normal(0, 0.01))), 2),
                "low": round(p * (1 - abs(np.random.normal(0, 0.01))), 2),
                "close": round(p, 2),
                "volume": v,
                "amount": round(p * v, 2),
                "turn": round(np.random.uniform(0.5, 5.0), 2),
                "pctChg": round(returns[i] * 100, 2),
                "adjustflag": 2,
            })

        for r in rows:
            conn.execute("""
                INSERT OR IGNORE INTO stock_daily
                (code, trade_date, open, high, low, close, volume, amount, turn, pctChg, adjustflag)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r["code"], r["trade_date"], r["open"], r["high"], r["low"],
                r["close"], r["volume"], r["amount"], r["turn"], r["pctChg"], r["adjustflag"]
            ))
        conn.commit()
        logger.info(f"  {bscode}: mock {len(rows)} 条日K数据")


# ============================================================
# 策略实现
# ============================================================

def _get_stock_df(conn: sqlite3.Connection, code: str, days: int = 60) -> pd.DataFrame:
    """从数据库读取最近 N 天数据为 DataFrame"""
    cursor = conn.execute("""
        SELECT * FROM stock_daily
        WHERE code = ?
        ORDER BY trade_date DESC
        LIMIT ?
    """, (code, days))
    rows = [dict(row) for row in cursor.fetchall()]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date")
    return df


def _turtle_trade(df: pd.DataFrame) -> Optional[Dict]:
    """
    海龟交易法: 20日突破买入，10日跌破卖出。
    
    返回: {"action": "buy"|"sell"|"hold", "trigger_price", "target_price", "stop_price", ...}
    """
    if len(df) < 25:
        return None
    
    recent = df.tail(20)
    high_20 = recent["high"].max()
    low_10 = df.tail(10)["low"].min()
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest
    
    action = "hold"
    trigger = latest["close"]
    
    # 突破20日高点
    if latest["close"] > high_20 and prev["close"] <= high_20:
        action = "buy"
        trigger = latest["close"]
        target = round(high_20 * 1.1, 2)  # +10%
        stop = round(low_10, 2)
    # 跌破10日低点
    elif latest["close"] < low_10 and prev["close"] >= low_10:
        action = "sell"
        trigger = latest["close"]
        target = round(low_10 * 0.9, 2)
        stop = round(high_20, 2)
    else:
        return None  # 无信号
    
    strength = min(abs(latest["close"] - high_20) / high_20 * 10, 1.0) if high_20 > 0 else 0.5
    
    return {
        "code": latest["code"],
        "strategy_name": "turtle_trade",
        "signal_date": latest["trade_date"].strftime("%Y-%m-%d"),
        "action": action,
        "trigger_price": round(trigger, 2),
        "target_price": round(target, 2),
        "stop_price": round(stop, 2),
        "strength": round(strength, 2),
        "description": f"海龟20日突破: 20日高={high_20}, 10日低={low_10}",
    }


def _ma_volume(df: pd.DataFrame) -> Optional[Dict]:
    """
    均量突破: 放量突破MA20，量价齐升。
    """
    if len(df) < 25:
        return None
    
    df["ma20"] = df["close"].rolling(window=20).mean()
    df["vol_ma20"] = df["volume"].rolling(window=20).mean()
    
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest
    
    # 放量突破MA20
    if (latest["close"] > latest["ma20"] and 
        prev["close"] <= prev["ma20"] and 
        latest["volume"] > latest["vol_ma20"] * 1.2):
        
        action = "buy"
        trigger = round(latest["close"], 2)
        target = round(latest["close"] * 1.08, 2)
        stop = round(latest["ma20"] * 0.95, 2)
        strength = min((latest["volume"] / latest["vol_ma20"] - 1) * 2, 1.0)
        
        return {
            "code": latest["code"],
            "strategy_name": "ma_volume",
            "signal_date": latest["trade_date"].strftime("%Y-%m-%d"),
            "action": action,
            "trigger_price": trigger,
            "target_price": target,
            "stop_price": stop,
            "strength": round(strength, 2),
            "description": f"均量突破MA20: close={trigger} > ma20={round(latest['ma20'],2)}, vol={latest['volume']} > vol_ma20={round(latest['vol_ma20'])}",
        }
    
    return None


def _rps_breakout(df: pd.DataFrame, all_codes: List[str], conn: sqlite3.Connection) -> Optional[Dict]:
    """
    RPS 相对强度突破: 120日涨幅排名前10%，且突破20日高点。
    """
    if len(df) < 120:
        return None
    
    # 计算自身120日涨幅
    price_120d_ago = df.iloc[-120]["close"]
    latest = df.iloc[-1]
    return_120d = (latest["close"] - price_120d_ago) / price_120d_ago if price_120d_ago > 0 else 0
    
    # 简化：模拟 RPS 排名（实际需对比全市场，这里用自身阈值）
    if return_120d < 0.15:  # 120日涨幅 < 15% 不入选
        return None
    
    # 突破20日高点
    recent = df.tail(20)
    high_20 = recent["high"].max()
    if latest["close"] > high_20:
        action = "buy"
        trigger = round(latest["close"], 2)
        target = round(trigger * 1.12, 2)
        stop = round(df.tail(20)["low"].min(), 2)
        strength = min(return_120d * 3, 1.0)  # 涨幅越大强度越高
        
        return {
            "code": latest["code"],
            "strategy_name": "rps_breakout",
            "signal_date": latest["trade_date"].strftime("%Y-%m-%d"),
            "action": action,
            "trigger_price": trigger,
            "target_price": target,
            "stop_price": stop,
            "strength": round(strength, 2),
            "description": f"RPS突破: 120日涨幅={return_120d:.1%}, 突破20日高={high_20}",
        }
    
    return None


def run_strategies(
    stocks: List[str],
    strategies: List[str],
    end_date: str,
    conn: sqlite3.Connection,
) -> List[Dict]:
    """
    运行策略扫描，返回信号列表。
    
    Args:
        stocks: 股票代码列表
        strategies: 策略名称列表 ["turtle_trade", "ma_volume", "rps_breakout"]
        end_date: 扫描截止日期
        conn: sqlite3 连接
    
    Returns:
        List[Dict] 策略信号，每条包含 code/strategy_name/action/... 字段
    """
    logger.info(f"运行策略扫描: stocks={stocks}, strategies={strategies}")
    signals = []
    
    strategy_map = {
        "turtle_trade": _turtle_trade,
        "ma_volume": _ma_volume,
        "rps_breakout": _rps_breakout,
    }
    
    for code in stocks:
        df = _get_stock_df(conn, code, days=150)
        if df.empty:
            logger.warning(f"  {code}: 无数据，跳过")
            continue
        
        for sname in strategies:
            func = strategy_map.get(sname)
            if not func:
                continue
            
            try:
                if sname == "rps_breakout":
                    sig = func(df, stocks, conn)
                else:
                    sig = func(df)
                
                if sig and sig.get("action") in ("buy", "sell", "watch"):
                    signals.append(sig)
                    logger.info(f"  {code} / {sname}: {sig['action']} @ {sig['trigger_price']}")
            except Exception as e:
                logger.warning(f"  {code} / {sname} 异常: {e}")
    
    logger.info(f"策略扫描完成，共 {len(signals)} 条信号")
    return signals


# ============================================================
# 回测
# ============================================================

def run_backtest(
    signals: List[Dict],
    window_days: int,
    conn: sqlite3.Connection,
) -> List[Dict]:
    """
    简单回测：模拟持有 signal_date 到 end_date 的收益。
    
    Args:
        signals: 策略信号列表
        window_days: 回测窗口天数
        conn: sqlite3 连接
    
    Returns:
        List[Dict] 回测结果
    """
    logger.info(f"回测: {len(signals)} 条信号, 窗口={window_days}天")
    results = []
    
    for sig in signals:
        code = sig["code"]
        entry_date = sig["signal_date"]
        action = sig.get("action", "hold")
        
        if action not in ("buy",):
            continue
        
        entry_price = sig.get("trigger_price", 0)
        target = sig.get("target_price", 0)
        stop = sig.get("stop_price", 0)
        
        # 获取 entry_date 之后的 window_days 数据
        cursor = conn.execute("""
            SELECT trade_date, close, high, low
            FROM stock_daily
            WHERE code = ? AND trade_date > ?
            ORDER BY trade_date
            LIMIT ?
        """, (code, entry_date, window_days))
        rows = [dict(row) for row in cursor.fetchall()]
        
        if not rows:
            continue
        
        # 模拟逐日检查
        exit_date = rows[-1]["trade_date"]
        exit_price = rows[-1]["close"]
        exit_reason = "窗口结束"
        max_drawdown = 0.0
        peak = entry_price
        
        for row in rows:
            price = row["close"]
            high = row["high"]
            low = row["low"]
            
            # 更新 peak 和 drawdown
            if price > peak:
                peak = price
            dd = (peak - price) / peak if peak > 0 else 0
            if dd > max_drawdown:
                max_drawdown = dd
            
            # 止盈
            if target > 0 and high >= target:
                exit_price = target
                exit_date = row["trade_date"]
                exit_reason = "止盈"
                break
            # 止损
            if stop > 0 and low <= stop:
                exit_price = stop
                exit_date = row["trade_date"]
                exit_reason = "止损"
                break
        
        return_pct = (exit_price - entry_price) / entry_price if entry_price > 0 else 0
        holding_days = len(rows) if exit_date == rows[-1]["trade_date"] else (
            datetime.strptime(exit_date, "%Y-%m-%d") - datetime.strptime(entry_date, "%Y-%m-%d")
        ).days
        
        results.append({
            "strategy_name": sig.get("strategy_name", "unknown"),
            "code": code,
            "start_date": entry_date,
            "end_date": exit_date,
            "total_return": round(return_pct, 4),
            "max_drawdown": round(max_drawdown, 4),
            "sharpe_ratio": None,  # 简化计算
            "win_rate": 1.0 if return_pct > 0 else 0.0,
            "trade_count": 1,
            "params_json": json.dumps({
                "entry": entry_price, "target": target, "stop": stop,
                "exit_reason": exit_reason, "holding_days": holding_days,
            }, ensure_ascii=False),
        })
        
        # 写入 backtest_results
        conn.execute("""
            INSERT INTO backtest_results
            (strategy_name, code, start_date, end_date, total_return, max_drawdown, sharpe_ratio, win_rate, trade_count, params_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            results[-1]["strategy_name"], results[-1]["code"],
            results[-1]["start_date"], results[-1]["end_date"],
            results[-1]["total_return"], results[-1]["max_drawdown"],
            results[-1]["sharpe_ratio"], results[-1]["win_rate"],
            results[-1]["trade_count"], results[-1]["params_json"]
        ))
    
    conn.commit()
    logger.info(f"回测完成: {len(results)} 条结果")
    return results

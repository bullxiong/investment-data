"""
kelly.py - Kelly 仓位算法 + 风控门控

公式: f* = (b*p - q) / b
  b = upside / downside  赔率
  p = confidence         胜率
  q = 1 - p              败率

半 Kelly + 风控门控（保守）
"""

from __future__ import annotations
import math
from typing import Any, Dict, Optional


def calculate_kelly(
    confidence: float,
    upside: float,
    downside: float,
    use_half: bool = True,
    max_position: float = 0.25,
) -> float:
    """
    计算 Kelly 仓位

    Args:
        confidence: 胜率 (0-1)
        upside: 预期上涨幅度 (元/比例，正数)
        downside: 预期下跌幅度 (元/比例，正数)
        use_half: 是否用半 Kelly（默认 True，更保守）
        max_position: 单标的仓位上限（默认 25%）

    Returns:
        建议仓位 (0-1)
    """
    if downside <= 0 or upside <= 0:
        return 0.0

    p = max(0.0, min(1.0, confidence))
    q = 1.0 - p
    b = upside / downside

    f_star = (b * p - q) / b

    if f_star <= 0:
        return 0.0

    kelly = f_star * 0.5 if use_half else f_star
    return min(kelly, max_position)


def risk_check(
    decision: Dict[str, Any],
    portfolio: Dict[str, Any],
) -> Dict[str, Any]:
    """
    风控门控

    Args:
        decision: {direction, confidence, position_pct, sector, ...}
        portfolio: {total_assets, cash, positions, sector_exposure, constraints}

    Returns:
        {
            passed: bool,
            adjusted_position: float,
            violations: [str],
            reason: str
        }
    """
    constraints = portfolio.get("constraints", {})
    max_single = constraints.get("max_single_position_pct", 0.25)
    max_sector = constraints.get("max_sector_pct", 0.30)
    max_drawdown = constraints.get("max_drawdown_threshold", 0.15)

    violations = []
    requested = decision.get("position_pct", 0.0)

    # 1. 单标的仓位上限
    adjusted = min(requested, max_single)
    if requested > max_single:
        violations.append(f"单标的仓位 {requested:.2%} > 上限 {max_single:.2%}")

    # 2. 板块集中度
    sector = decision.get("sector", "未知")
    current_sector_pct = portfolio.get("sector_exposure", {}).get(sector, 0.0)
    if current_sector_pct + adjusted > max_sector:
        # 超过板块上限，等比缩减
        allowed = max(0.0, max_sector - current_sector_pct)
        if allowed < adjusted:
            violations.append(
                f"板块 {sector} 当前 {current_sector_pct:.2%} + 本次 {adjusted:.2%} "
                f"> 上限 {max_sector:.2%}，调整到 {allowed:.2%}"
            )
            adjusted = allowed

    # 3. 回撤检查（如果 portfolio 提供当前回撤）
    current_drawdown = portfolio.get("current_drawdown", 0.0)
    if current_drawdown >= max_drawdown:
        violations.append(
            f"当前组合回撤 {current_drawdown:.2%} >= 阈值 {max_drawdown:.2%}，禁止开仓"
        )
        adjusted = 0.0

    # 4. 现金检查（不能加杠杆超 100%）
    cash = portfolio.get("cash", 0.0)
    total_assets = portfolio.get("total_assets", 1.0)
    cash_pct = cash / total_assets if total_assets > 0 else 0.0
    if adjusted > cash_pct:
        violations.append(
            f"可用现金 {cash_pct:.2%} < 请求仓位 {adjusted:.2%}，调整到 {cash_pct:.2%}"
        )
        adjusted = cash_pct

    passed = len(violations) == 0 and adjusted > 0
    reason = "通过" if passed else f"{len(violations)} 项违规，已调整"

    return {
        "passed": passed,
        "adjusted_position": round(adjusted, 4),
        "violations": violations,
        "reason": reason,
    }


# ---------- CLI 测试 ----------
if __name__ == "__main__":
    # 测试 Kelly
    cases = [
        # (confidence, upside, downside, expected)
        (0.7, 0.20, 0.10, "高胜率高赔率 → 应该正仓位"),
        (0.5, 0.10, 0.10, "50% 胜率等赔率 → 应该是 0"),
        (0.3, 0.20, 0.10, "低胜率高赔率 → 应该是 0"),
        (0.9, 0.05, 0.10, "高胜率低赔率 → 小仓位"),
    ]
    print("=== Kelly 测试 ===")
    for p, up, dn, desc in cases:
        f = calculate_kelly(p, up, dn)
        print(f"  p={p} up={up} dn={dn} → f={f:.4f}  ({desc})")

    # 测试风控
    print("\n=== 风控测试 ===")
    test_decision = {
        "direction": "bullish",
        "confidence": 0.7,
        "position_pct": 0.20,
        "sector": "液冷",
    }
    test_portfolio = {
        "total_assets": 1_000_000.0,
        "cash": 200_000.0,
        "positions": [],
        "sector_exposure": {"液冷": 0.10},  # 已持有 10% 液冷
        "constraints": {
            "max_single_position_pct": 0.25,
            "max_sector_pct": 0.30,
            "max_drawdown_threshold": 0.15,
        },
        "current_drawdown": 0.05,
    }
    result = risk_check(test_decision, test_portfolio)
    print(f"  决策仓位: {test_decision['position_pct']:.2%}")
    print(f"  调整仓位: {result['adjusted_position']:.2%}")
    print(f"  通过: {result['passed']}")
    print(f"  违规: {result['violations']}")
    print(f"  原因: {result['reason']}")
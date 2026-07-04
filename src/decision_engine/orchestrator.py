"""
orchestrator.py - Decision Engine 主调度器

对外暴露 run_full_decision(text_signals, tech_signals, concept, date, llm_config) -> Dict
包含：6 角色辩论 → Kelly 仓位 → 风控门控 → 完整决策
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from . import run_debate, calculate_kelly, risk_check


logger = logging.getLogger("decision_engine")


DEFAULT_RISK_CONFIG = {
    "max_single_stock_ratio": 0.25,
    "max_sector_ratio": 0.30,
    "max_drawdown_stop": 0.15,
}


def run_full_decision(
    text_signals: List[Dict],
    tech_signals: List[Dict],
    target_concept: str,
    target_date: str,
    llm_config: Optional[Dict] = None,
    portfolio: Optional[Dict] = None,
    risk_config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """一站式决策流程

    流程：
      1. 6 角色辩论（run_debate）→ direction / confidence / 三件套
      2. 计算 Kelly 理论仓位
      3. 风控门控（risk_check）→ 阻断/通过
      4. 返回完整决策

    Returns:
        {
            "concept": str,
            "direction": "long"|"short"|"neutral",
            "confidence": float,
            "entry_price": float,
            "target_price": float,
            "stop_loss": float,
            "risk_level": "low"|"medium"|"high",
            "kelly_position": float,
            "final_position": float,
            "risk_passed": bool,
            "rationale": str,
            "claims": List[Dict],
        }
    """
    logger.info(f"[orchestrator] 开始决策 {target_concept} @ {target_date}")

    # 1. 6 角色辩论
    debate = run_debate(
        text_signals=text_signals,
        tech_signals=tech_signals,
        target_concept=target_concept,
        target_date=target_date,
        llm_config=llm_config or {},
    )

    direction = debate["direction"]
    confidence = debate["confidence"]
    entry = debate["entry_price"]
    target = debate["target_price"]
    stop = debate["stop_loss"]

    # 2. Kelly（半 Kelly + 25% 上限）
    upside = abs(target - entry)
    downside = abs(entry - stop) if stop > 0 else max(abs(entry) * 0.05, 1.0)
    kelly = calculate_kelly(confidence, upside, downside)
    logger.info(f"[orchestrator] Kelly 仓位 = {kelly*100:.2f}%")

    # 3. 风控门控
    final_position = kelly
    risk_passed = True
    if portfolio is not None:
        risk_passed = risk_check(
            position=kelly,
            portfolio=portfolio,
            risk_config=risk_config or DEFAULT_RISK_CONFIG,
        )
        if not risk_passed:
            logger.warning(f"[orchestrator] 风控拦截，仓位降为 0")
            final_position = 0.0

    return {
        "concept": target_concept,
        "direction": direction,
        "confidence": confidence,
        "entry_price": entry,
        "target_price": target,
        "stop_loss": stop,
        "risk_level": debate["risk_level"],
        "kelly_position": kelly,
        "final_position": final_position,
        "risk_passed": risk_passed,
        "rationale": debate["rationale"],
        "claims": debate["claims"],
    }


__all__ = ["run_full_decision"] 
#!/usr/bin/env python3
"""
src/decision_engine/__init__.py
投研决策模块 (MiniMax 负责)

对外暴露接口（适配 main.py 调用）：
    run_debate(text_signals, tech_signals, target_concept, target_date, llm_config, max_rounds) -> Dict
    calculate_kelly(confidence, upside, downside) -> float
    risk_check(position, portfolio, risk_config) -> bool

内部实现：
    - 调用 debate.py 的 run_debate 执行多Agent辩论
    - 调用 kelly.py 的 calculate_kelly 和 risk_check
    - 做方向映射和 claims 转换
"""

import json
import logging
from typing import Any, Dict, List, Optional

from .kelly import calculate_kelly as _calc_kelly, risk_check as _risk_check

logger = logging.getLogger("decision_engine")


# ============ 兼容映射 ============

def _norm_direction(d: str) -> str:
    """统一方向映射：内部 bullish/bearish → 外部 long/short"""
    m = {
        "bullish": "long",
        "bearish": "short",
        "long": "long",
        "short": "short",
        "neutral": "neutral",
    }
    return m.get(str(d).lower().strip(), "neutral")


def _risk_level(confidence: float) -> str:
    """基于置信度计算风险等级（先用 confidence 口径，后续可升级）"""
    if confidence >= 0.75:
        return "low"
    if confidence >= 0.55:
        return "medium"
    return "high"


def _extract_claims(debate_result: Dict) -> List[Dict]:
    """
    从 debate.py 输出中提取 claims 列表（适配 main.py 的 debate_logs 表写入）。
    
    debate.py 返回格式：
    {
        "decision": {...},
        "all_agents": {
            "intel_agent": {"status": "ok", "output": {...}},
            "bull_agent": {"status": "ok", "output": {...}},
            ...
        },
        "timeline": [...]
    }
    
    转成：
    [
        {"round": 1, "agent_role": "intel_agent", "claim_type": "neutral", "content": "...", "confidence": 0.7},
        ...
    ]
    """
    claims = []
    all_agents = debate_result.get("all_agents", {})
    
    for agent_name, agent_result in all_agents.items():
        if not isinstance(agent_result, dict):
            continue
        if agent_name in ("research_target_prices",):
            continue
            
        output = agent_result.get("output", {})
        if not output:
            continue
        
        # 推断 claim_type
        if "bull" in agent_name.lower():
            claim_type = "bull"
        elif "bear" in agent_name.lower():
            claim_type = "bear"
        elif "risk" in agent_name.lower():
            claim_type = "bear"  # 风控通常偏空
        elif "arbiter" in agent_name.lower():
            claim_type = "neutral"  # 仲裁是中性的
        else:
            claim_type = "neutral"
        
        # 构造 content
        if isinstance(output, dict):
            content = json.dumps(output, ensure_ascii=False, indent=2)[:800]
            confidence = output.get("confidence", 0.5)
        else:
            content = str(output)[:800]
            confidence = 0.5
        
        # 从 timeline 推断 round
        timeline = debate_result.get("timeline", [])
        round_num = 1
        for t in timeline:
            if t.get("agent") == agent_name:
                stage = t.get("stage", "")
                if stage.startswith("2"):
                    round_num = 2
                elif stage.startswith("3"):
                    round_num = 3
                elif stage.startswith("4"):
                    round_num = 4
                elif stage.startswith("5"):
                    round_num = 5
                break
        
        claims.append({
            "round": round_num,
            "agent_role": agent_name,
            "claim_type": claim_type,
            "content": content,
            "confidence": confidence,
        })
    
    return claims


# ============ 暴露接口 ============

def run_debate(
    text_signals: List[Dict],
    tech_signals: List[Dict],
    target_concept: str,
    target_date: str,
    llm_config: Dict,
    max_rounds: int = 3,
) -> Dict[str, Any]:
    """
    运行多Agent辩论，返回决策结果。
    
    适配层：将 debate.py 的输出格式转换为 main.py 期望的格式。
    
    Args:
        text_signals: List[Dict] 从 text_signals 表读取
        tech_signals: List[Dict] 从 strategy_signals 表读取
        target_concept: 目标概念
        target_date: 目标日期
        llm_config: LLM配置 {provider, model, api_key, ...}
        max_rounds: 辩论轮次（debate.py 内部已实现5步，此参数暂不生效）
    
    Returns:
        Dict {
            "concept": str,
            "direction": "long"|"short"|"neutral",
            "confidence": float,
            "entry_price": float,
            "target_price": float,
            "stop_loss": float,
            "risk_level": "low"|"medium"|"high",
            "claims": List[Dict],  # 用于写入 debate_logs
            "rationale": str,
        }
    """
    logger.info(f"[decision_engine] 开始辩论: {target_concept} @ {target_date}")
    
    # 构造 debate.py 需要的输入（dict 格式）
    text_signals_dict = {}
    for i, sig in enumerate(text_signals):
        text_signals_dict[f"signal_{i}"] = sig
    
    tech_signals_dict = {}
    for i, sig in enumerate(tech_signals):
        tech_signals_dict[f"signal_{i}"] = sig
    
    try:
        from .debate import run_debate as _run_debate
        result = _run_debate(
            target_concept=target_concept,
            target_company=target_concept,
            text_signals=text_signals_dict,
            tech_signals=tech_signals_dict,
            provider=llm_config.get("provider", "minimax"),
        )
    except Exception as e:
        logger.exception(f"[decision_engine] 辩论失败: {e}")
        return {
            "concept": target_concept,
            "direction": "neutral",
            "confidence": 0.5,
            "entry_price": 0.0,
            "target_price": 0.0,
            "stop_loss": 0.0,
            "risk_level": "medium",
            "claims": [],
            "rationale": f"辩论失败: {e}",
        }
    
    decision = result.get("decision", {})
    
    # 映射 direction
    direction = _norm_direction(decision.get("direction", "neutral"))
    
    # 提取三件套
    entry = float(decision.get("entry_price", 0.0) or 0.0)
    target = float(decision.get("target_price", 0.0) or 0.0)
    stop = float(decision.get("stop_loss", 0.0) or 0.0)
    
    # 如果 LLM 没给三件套，从 tech_signals 推断兜底
    if entry == 0 and tech_signals:
        for sig in tech_signals:
            if sig.get("trigger_price"):
                entry = float(sig.get("trigger_price", 0))
                break
        if entry > 0 and target == 0:
            target = round(entry * 1.15, 2)  # 默认 +15%
        if entry > 0 and stop == 0:
            stop = round(entry * 0.93, 2)  # 默认 -7%
    
    # 计算 confidence 和 risk_level
    confidence = float(decision.get("confidence", 0.5) or 0.5)
    risk_level = _risk_level(confidence)
    
    # 提取 claims
    claims = _extract_claims(result)
    
    # rationale
    rationale = decision.get("reasoning", "")
    if not rationale:
        rationale = f"多Agent辩论结果: {direction}, confidence={confidence:.2f}"
    
    return {
        "concept": target_concept,
        "direction": direction,
        "confidence": confidence,
        "entry_price": entry,
        "target_price": target,
        "stop_loss": stop,
        "risk_level": risk_level,
        "claims": claims,
        "rationale": rationale,
    }


def calculate_kelly(confidence: float, upside: float, downside: float) -> float:
    """
    计算 Kelly 仓位。
    
    Args:
        confidence: 胜率 p (0~1)
        upside: 赔率 b（目标收益/最大亏损，或价格涨幅比例）
        downside: 止损比例（或最大亏损比例）
    
    Returns:
        float 建议仓位比例 (0~1)，已应用半Kelly和25%上限
    """
    kelly = _calc_kelly(confidence, upside, downside)
    # 半 Kelly
    half_kelly = kelly * 0.5
    # 上限 25%
    return min(half_kelly, 0.25)


def risk_check(position: float, portfolio: Dict, risk_config: Dict) -> bool:
    """
    风控门控。
    
    Args:
        position: float 建议仓位
        portfolio: Dict 当前组合
        risk_config: Dict 风控参数
    
    Returns:
        bool True=通过, False=拦截
    """
    return _risk_check(position, portfolio, risk_config)


# 可选：导出 orchestrator 供内部使用
from .orchestrator import run_full_decision  # noqa: F401

__all__ = [
    "run_debate",
    "calculate_kelly",
    "risk_check",
    "run_full_decision",
]

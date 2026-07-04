"""
debate.py - 多 Agent 辩论编排

流程：
  Step 1: intel_agent
  Step 2: [industry ∥ company ∥ valuation]   (基本面 3 角色并行)
  Step 3: [bull ∥ bear ∥ sentiment]          (多空 + 情绪并行)
  Step 4: risk_agent
  Step 5: arbiter_agent
"""

from __future__ import annotations
import json
import time
from typing import Any, Dict, List, Optional

from .agents import (
    IntelAgent, IndustryAgent, CompanyAgent, ValuationAgent,
    BullAgent, BearAgent, SentimentAgent,
    RiskAgent, ArbiterAgent,
)
from .kelly import calculate_kelly, risk_check


def run_debate(
    target_concept: str,
    target_company: str = "",
    text_signals: Dict = None,
    tech_signals: Dict = None,
    research_signals: Dict = None,
    market_data: Dict = None,
    portfolio: Dict = None,
    sentiment_data: Dict = None,
    financials: Dict = None,
    industry_avg: Dict = None,
    valuation_history: Dict = None,
    provider: str = "minimax",
) -> Dict[str, Any]:
    """
    主入口：跑一次完整辩论

    Args:
        target_concept: 目标概念/行业
        target_company: 目标公司
        text_signals: 社交媒体信号
        tech_signals: 技术信号
        research_signals: 研报信号
        market_data: 市场数据
        portfolio: 组合数据
        sentiment_data: 情绪数据
        financials: 财务数据
        industry_avg: 行业平均
        valuation_history: 历史估值
        provider: LLM provider

    Returns:
        {
            decision: {...},
            all_agents: {...},
            timeline: [{stage, agent, latency_ms}],
            total_cost_estimate: 0.0
        }
    """
    text_signals = text_signals or {}
    tech_signals = tech_signals or {}
    research_signals = research_signals or {}
    market_data = market_data or {}
    portfolio = portfolio or {}
    sentiment_data = sentiment_data or {}
    financials = financials or {}
    industry_avg = industry_avg or {}
    valuation_history = valuation_history or {}

    timeline = []
    all_agents = {}

    def _run_agent(agent_cls, input_data, stage):
        """辅助：跑一个 Agent，记录耗时"""
        start = time.time()
        agent = agent_cls(provider=provider)
        result = agent.run(input_data)
        elapsed = int((time.time() - start) * 1000)
        timeline.append({"stage": stage, "agent": result["agent"], "latency_ms": elapsed})
        all_agents[result["agent"]] = result
        return result

    # ============ Step 1: 情报整合 ============
    intel_result = _run_agent(
        IntelAgent,
        {
            "text_signals": text_signals,
            "tech_signals": tech_signals,
            "research_signals": research_signals,
            "sentiment_data": sentiment_data,
        },
        stage="1-intel",
    )
    fact_card = intel_result.get("output", {})

    # ============ Step 2: 基本面 3 角色并行 ============
    # 当前是串行（受限于 OpenClaw 同步执行），可后续改 asyncio
    industry_result = _run_agent(
        IndustryAgent,
        {
            "concept": target_concept,
            "research_signals": research_signals,
            "industry_news": market_data.get("industry_news", ""),
        },
        stage="2-industry",
    )
    company_result = _run_agent(
        CompanyAgent,
        {
            "target_company": target_company or target_concept,
            "company_research": research_signals,
            "financials": financials,
            "competitors": "",
        },
        stage="2-company",
    )
    valuation_result = _run_agent(
        ValuationAgent,
        {
            "target_company": target_company or target_concept,
            "financials": financials,
            "industry_avg": industry_avg,
            "valuation_history": valuation_history,
        },
        stage="2-valuation",
    )

    # ============ Step 3: 多空 + 情绪 ============
    bull_input = {
        "fact_card": fact_card,
        "industry_result": industry_result.get("output", {}),
        "company_result": company_result.get("output", {}),
        "valuation_result": valuation_result.get("output", {}),
    }
    bear_input = {**bull_input}  # 输入相同但独立思考

    bull_result = _run_agent(BullAgent, bull_input, stage="3-bull")
    bear_result = _run_agent(BearAgent, bear_input, stage="3-bear")
    sentiment_result = _run_agent(
        SentimentAgent,
        {
            "concept": target_concept,
            "market_data": market_data,
            "sector_data": tech_signals.get("sector", {}),
        },
        stage="3-sentiment",
    )

    # ============ Step 4: 风控 ============
    risk_input = {
        "all_agents": all_agents,
        "portfolio": portfolio,
    }
    risk_result = _run_agent(RiskAgent, risk_input, stage="4-risk")

    # ============ Step 5: 仲裁 ============
    arbiter_input = {
        "all_agents": all_agents,
        "risk_result": risk_result.get("output", {}),
    }
    arbiter_result = _run_agent(ArbiterAgent, arbiter_input, stage="5-arbiter")
    decision = arbiter_result.get("output", {})

    # ============ Step 6: Kelly 二次校准（基于 arbiter 输出） ============
    if decision.get("direction") in ("bullish", "bearish") and decision.get("entry_price"):
        try:
            current = decision.get("current_price", decision["entry_price"])
            target = decision["target_price"]
            stop = decision["stop_loss"]
            conf = decision.get("confidence", 0.5)

            if decision["direction"] == "bullish":
                upside = abs(target - current) / current
                downside = abs(current - stop) / current
            else:
                upside = abs(stop - current) / current
                downside = abs(target - current) / current

            kelly = calculate_kelly(conf, upside, downside)
            decision["kelly_position"] = kelly

            # 用风控规则再校准
            risk_decision = {
                "direction": decision["direction"],
                "confidence": conf,
                "position_pct": decision.get("position_pct", kelly),
                "sector": target_concept,
            }
            risk_validation = risk_check(risk_decision, portfolio)
            decision["risk_validation"] = risk_validation
            decision["final_position_pct"] = risk_validation["adjusted_position"]
        except Exception as e:
            decision["kelly_error"] = str(e)

    return {
        "decision": decision,
        "all_agents": all_agents,
        "timeline": timeline,
        "total_latency_ms": sum(t["latency_ms"] for t in timeline),
        "agent_count": len(all_agents),
    }


# ============ 批量入口 ============
def batch_run(targets: List[Dict], provider: str = "minimax") -> List[Dict]:
    """批量跑多个标的"""
    results = []
    for i, target in enumerate(targets):
        print(f"[{i+1}/{len(targets)}] 分析: {target.get('target_concept', '?')}")
        result = run_debate(provider=provider, **target)
        result["target_meta"] = target
        results.append(result)
        print(f"  决策: {result['decision'].get('direction')} "
              f"置信度 {result['decision'].get('confidence', 0):.2f} "
              f"耗时 {result['total_latency_ms']}ms")
    return results


if __name__ == "__main__":
    # 简单冒烟测试（不调 LLM，只验证 import/编排）
    print("=== decision_engine 辩论编排模块 ===")
    print("导入成功")
    print(f"run_debate() 可用：{'是'}")
    print(f"batch_run() 可用：{'是'}")
    print("\n下一步：传入真实信号数据跑一次辩论")
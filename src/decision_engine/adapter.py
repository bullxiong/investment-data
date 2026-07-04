"""
adapter.py - 6 角色接口适配层

作用：把现有 9 角色 V2 暴露为 6 角色 V6 公开接口
- 接口签名：run_debate_v6(text_signals, tech_signals, concept, date, llm_config)
- 输出：标准决策 Dict

6 角色（你定的）：
  1. intel_researcher       情报整合
  2. bull_researcher        多头论证
  3. bear_researcher        空头论证
  4. value_chain_agent      产业链推理（合并 industry+company+valuation）
  5. risk_researcher        风控评估
  6. arbiter_researcher     决策仲裁
"""

from __future__ import annotations
import json
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# ============ LLM 调用（内联简版，避免依赖 llm_client 复杂配置） ============
TUSHARE_TOKEN = "7f6ec8a8c8616b14fc77829fb42555a7d3d6f0cf549530d583a1cad7"
DEEPSEEK_API_KEY = ""
# 从 config.json 读 DeepSeek
_config_path = Path("/workspace/config.json")
if _config_path.exists():
    try:
        _cfg = json.loads(_config_path.read_text())
        DEEPSEEK_API_KEY = _cfg.get("deepseek", {}).get("api_key", "")
    except Exception:
        pass


def call_llm(system_prompt: str, user_prompt: str,
             temperature: float = 0.2, max_tokens: int = 2000) -> Dict:
    """调 DeepSeek Chat（直接 HTTP，避免 SDK 依赖）"""
    if not DEEPSEEK_API_KEY:
        # fallback 到 minimax/auto（先 stub）
        return {"status": "error", "error": "no_deepseek_key", "output": {}}

    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        content = data["choices"][0]["message"]["content"]
        m = re.search(r"\{[\s\S]*\}", content)
        if m:
            return {"status": "ok", "output": json.loads(m.group(0))}
        return {"status": "ok", "output": {"raw": content}}
    except Exception as e:
        return {"status": "error", "error": str(e)[:200], "output": {}}


def load_prompt(role: str) -> str:
    """从 prompts/ 目录加载提示词"""
    p = Path(__file__).parent / "prompts" / f"{role}.txt"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return f"你是 {role}。"


# ============ 6 角色独立实现（不依赖 9 角色） ============
def intel_researcher(text_signals: List[Dict], tech_signals: List[Dict],
                      concept: str, date: str) -> Dict:
    """Step 1: 情报整合

    输入：
        text_signals: GLM 输出的媒体/社交/新闻信号
        tech_signals: Kimi 输出的技术指标信号
    """
    system_prompt = load_prompt("intel_agent")
    user_prompt = f"""整合以下信号为事实卡：
目标概念: {concept}
日期: {date}

【GLM text_signals】(社交/媒体/新闻，共 {len(text_signals)} 条)
{json.dumps(text_signals[:10], ensure_ascii=False, indent=2)}

【Kimi tech_signals】(技术指标，共 {len(tech_signals)} 条)
{json.dumps(tech_signals[:10], ensure_ascii=False, indent=2)}

按 intel_agent schema 输出 JSON。"""
    return call_llm(system_prompt, user_prompt)


def bull_researcher(fact_card: Dict, concept: str) -> Dict:
    """Step 2: 多头论证"""
    system_prompt = load_prompt("bull_agent")
    user_prompt = f"""基于事实卡从多头角度论证（看不到空头观点）：
概念: {concept}
事实卡: {json.dumps(fact_card, ensure_ascii=False)[:2000]}

按 bull_agent schema 输出 JSON。"""
    return call_llm(system_prompt, user_prompt)


def bear_researcher(fact_card: Dict, concept: str) -> Dict:
    """Step 3: 空头论证"""
    system_prompt = load_prompt("bear_agent")
    user_prompt = f"""基于事实卡从空头角度论证（看不到多头观点）：
概念: {concept}
事实卡: {json.dumps(fact_card, ensure_ascii=False)[:2000]}

按 bear_agent schema 输出 JSON。"""
    return call_llm(system_prompt, user_prompt)


def value_chain_agent(fact_card: Dict, concept: str) -> Dict:
    """Step 4: 产业链推理（合并 industry+company+valuation）"""
    # 用一个统一的"产业链推理"提示词
    system_prompt = """你是产业链推理专家。综合考虑行业景气度、公司在产业链中的位置、估值合理性。

【输入】
- 事实卡（含 text_signals 媒体信号 + tech_signals 技术信号）

【输出 Schema】
```json
{
  "industry_phase": "启动|成长|成熟|衰退",
  "industry_outlook": "positive|neutral|negative",
  "competitive_position": "龙头|前列|跟随|落后",
  "moat_strength": "宽|窄|无",
  "valuation_level": "低估|合理|高估",
  "industry_growth_1y": 0.0-1.0,
  "value_chain_health": "健康|一般|恶化",
  "key_supply_chain_signals": ["..."],
  "confidence": 0.0-1.0,
  "reasoning": "综合行业景气+公司护城河+估值的完整推理"
}
```"""
    user_prompt = f"""分析 {concept} 行业产业链：

事实卡: {json.dumps(fact_card, ensure_ascii=False)[:2500]}

按 schema 输出 JSON。"""
    return call_llm(system_prompt, user_prompt)


def risk_researcher(bull: Dict, bear: Dict, value_chain: Dict,
                     arbiter_candidate: Dict) -> Dict:
    """Step 5: 风控评估

    输入：bull + bear + value_chain + 候选决策
    输出：Kelly 仓位 + 风控门控结果
    """
    system_prompt = load_prompt("risk_agent")
    # 计算 Kelly（直接调用）
    try:
        from .kelly import calculate_kelly
    except ImportError:
        from kelly import calculate_kelly
    confidence = arbiter_candidate.get("confidence", 0.5)
    direction = arbiter_candidate.get("direction", "neutral")
    entry = arbiter_candidate.get("entry_price", 0)
    target = arbiter_candidate.get("target_price", 0)
    stop = arbiter_candidate.get("stop_loss", 0)

    upside = abs(target - entry) if target and entry else 0
    downside = abs(entry - stop) if entry and stop else 1
    kelly = calculate_kelly(confidence, upside, downside, use_half=True, max_position=0.25)

    user_prompt = f"""综合所有 Agent 输出做风控：

候选决策: {json.dumps(arbiter_candidate, ensure_ascii=False)}
Kelly 算出的理论仓位: {kelly:.4f} ({kelly*100:.1f}%)

Bull: {json.dumps(bull, ensure_ascii=False)[:600]}
Bear: {json.dumps(bear, ensure_ascii=False)[:600]}
Value Chain: {json.dumps(value_chain, ensure_ascii=False)[:600]}

按 risk_agent schema 输出 JSON（包含 raw_kelly={kelly:.4f}）。"""
    return call_llm(system_prompt, user_prompt)


def arbiter_researcher(bull: Dict, bear: Dict, value_chain: Dict,
                        risk: Dict, concept: str) -> Dict:
    """Step 6: 决策仲裁"""
    system_prompt = load_prompt("arbiter_agent")
    user_prompt = f"""基于所有 Agent 输出给最终决策：
概念: {concept}

Bull: {json.dumps(bull, ensure_ascii=False)[:500]}
Bear: {json.dumps(bear, ensure_ascii=False)[:500]}
Value Chain: {json.dumps(value_chain, ensure_ascii=False)[:500]}
Risk: {json.dumps(risk, ensure_ascii=False)[:500]}

按 arbiter_agent schema 输出 JSON。"""
    return call_llm(system_prompt, user_prompt)


# ============ 主入口：6 角色辩论 ============
def run_debate_v6(
    text_signals: List[Dict],
    tech_signals: List[Dict],
    target_concept: str,
    target_date: str,
    llm_config: Optional[Dict] = None,
) -> Dict:
    """6 角色多 Agent 辩论

    Args:
        text_signals: GLM 输出的信号
        tech_signals: Kimi 输出的技术信号
        target_concept: 板块/概念名
        target_date: 目标日期
        llm_config: LLM 配置（暂未使用，保留接口）

    Returns:
        {
            "direction": "bullish|bearish|neutral",
            "confidence": 0.0-1.0,
            "entry": 0.0,
            "target": 0.0,
            "stop": 0.0,
            "kelly_position": 0.0-1.0,
            "final_position": 0.0-1.0,
            "rationale": "...",
            "debate_summary": {
                "intel": {...}, "bull": {...}, "bear": {...},
                "value_chain": {...}, "risk": {...}, "arbiter": {...}
            },
            "debate_logs": [
                {"agent_name": "intel_researcher", "claim": "...", "evidence": "...", "confidence": 0.7, "round": 1}
            ]
        }
    """
    started = time.time()

    # 1) 情报整合
    print(f"  [1/6] intel_researcher...")
    intel = intel_researcher(text_signals, tech_signals, target_concept, target_date)
    fact_card = intel.get("output", {})
    print(f"    {intel.get('status')}")

    # 2-4) 多头 + 空头 + 产业链推理（三个互不依赖，并行执行）
    print(f"  [2-4/6] 并行 bull/bear/value_chain (3 路并发)...")
    concurrent_start = time.time()
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _bull_task():
        return ("bull", bull_researcher(fact_card, target_concept))

    def _bear_task():
        return ("bear", bear_researcher(fact_card, target_concept))

    def _vc_task():
        return ("value_chain", value_chain_agent(fact_card, target_concept))

    bull = {"status": "pending", "output": {}}
    bear = {"status": "pending", "output": {}}
    value_chain = {"status": "pending", "output": {}}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(_bull_task),
            pool.submit(_bear_task),
            pool.submit(_vc_task),
        ]
        for fut in as_completed(futures):
            try:
                name, result = fut.result(timeout=120)
                if name == "bull":
                    bull = result
                elif name == "bear":
                    bear = result
                elif name == "value_chain":
                    value_chain = result
                print(f"    ✅ {name}: {result.get('status')}")
            except Exception as e:
                print(f"    ⚠️ task failed: {e}")
    print(f"    并行耗时: {(time.time() - concurrent_start)*1000:.0f}ms")

    # 5) 风控（先构造 arbiter 候选 decision）
    # bull / bear 哪个更强势？
    bull_score = bull.get("output", {}).get("overall_score", 0.5)
    bear_score = bear.get("output", {}).get("overall_score", 0.5)
    preliminary_direction = "bullish" if bull_score > bear_score else ("bearish" if bear_score > bull_score else "neutral")
    preliminary_conf = max(bull_score, bear_score, 0.5)

    # 暂时 entry/target/stop 用占位（用 100/110/95 默认）
    arbiter_candidate = {
        "direction": preliminary_direction,
        "confidence": preliminary_conf,
        "entry_price": 100,
        "target_price": 110,
        "stop_loss": 95,
    }

    print(f"  [5/6] risk_researcher...")
    risk = risk_researcher(bull, bear, value_chain, arbiter_candidate)
    print(f"    {risk.get('status')}")

    # 6) 决策仲裁
    print(f"  [6/6] arbiter_researcher...")
    arbiter = arbiter_researcher(bull, bear, value_chain, risk, target_concept)
    print(f"    {arbiter.get('status')}")

    final = arbiter.get("output", {})

    # 7) 计算最终仓位
    direction = final.get("direction", "neutral")
    confidence = float(final.get("confidence", 0.5) or 0.5)
    entry = float(final.get("entry_price", 0) or 0)
    target = float(final.get("target_price", 0) or 0)
    stop = float(final.get("stop_loss", 0) or 0)

    # Kelly（如果 arbiter 没给）
    kelly_pos = final.get("position_pct", 0)
    if not kelly_pos or kelly_pos == 0:
        try:
            from .kelly import calculate_kelly
        except ImportError:
            from kelly import calculate_kelly
        upside = abs(target - entry) if target > 0 and entry > 0 else 0
        downside = abs(entry - stop) if entry > 0 and stop > 0 else 1
        kelly_pos = calculate_kelly(confidence, upside, downside, use_half=True, max_position=0.25)

    # 构造 debate_logs（每轮的 claim + evidence）
    debate_logs = _build_debate_logs(fact_card, bull, bear, value_chain, risk, arbiter)

    # rationale
    rationale = final.get("reasoning", "") or final.get("final_decision_basis", "")

    elapsed = int((time.time() - started) * 1000)
    print(f"  ✅ 完成 ({elapsed}ms): {direction} conf={confidence:.2f} 仓位={kelly_pos*100:.1f}%")

    return {
        "date": target_date,
        "concept": target_concept,
        "direction": direction,
        "confidence": confidence,
        "entry_price": entry,
        "target_price": target,
        "stop_loss": stop,
        "kelly_position": kelly_pos,
        "final_position": kelly_pos,  # 简化：未做风控门控，等真实组合数据
        "rationale": rationale,
        "debate_summary": {
            "intel": fact_card,
            "bull": bull.get("output", {}),
            "bear": bear.get("output", {}),
            "value_chain": value_chain.get("output", {}),
            "risk": risk.get("output", {}),
            "arbiter": final,
        },
        "debate_logs": debate_logs,
        "total_latency_ms": elapsed,
    }


def _build_debate_logs(fact_card: Dict, bull: Dict, bear: Dict,
                        value_chain: Dict, risk: Dict, arbiter: Dict) -> List[Dict]:
    """从 6 角色输出构造 debate_logs（数据库友好格式）"""
    logs = []

    # Round 1: 情报整合
    fc = fact_card
    logs.append({
        "agent_name": "intel_researcher",
        "claim": fc.get("summary") or "整合多源信号成事实卡",
        "evidence": json.dumps(fc.get("key_facts", []), ensure_ascii=False)[:500],
        "confidence": fc.get("uncertainty") and (1.0 - float(fc.get("uncertainty", 0.5))) or 0.5,
        "round": 1,
    })

    # Round 2: 多头
    b = bull.get("output", {})
    bull_claims = b.get("claims", [])
    logs.append({
        "agent_name": "bull_researcher",
        "claim": "; ".join([c.get("claim", "") for c in bull_claims[:3]]),
        "evidence": "; ".join([c.get("evidence", [""])[0] if c.get("evidence") else "" for c in bull_claims[:3]]),
        "confidence": float(b.get("confidence", 0.5) or 0.5),
        "round": 2,
    })

    # Round 3: 空头
    br = bear.get("output", {})
    bear_claims = br.get("claims", [])
    logs.append({
        "agent_name": "bear_researcher",
        "claim": "; ".join([c.get("claim", "") for c in bear_claims[:3]]),
        "evidence": "; ".join([c.get("evidence", [""])[0] if c.get("evidence") else "" for c in bear_claims[:3]]),
        "confidence": float(br.get("confidence", 0.5) or 0.5),
        "round": 3,
    })

    # Round 4: 产业链
    vc = value_chain.get("output", {})
    logs.append({
        "agent_name": "value_chain_agent",
        "claim": f"行业 {vc.get('industry_phase', '?')} / 估值 {vc.get('valuation_level', '?')} / 位置 {vc.get('competitive_position', '?')}",
        "evidence": vc.get("reasoning", ""),
        "confidence": float(vc.get("confidence", 0.5) or 0.5),
        "round": 4,
    })

    # Round 5: 风控
    r = risk.get("output", {})
    logs.append({
        "agent_name": "risk_researcher",
        "claim": f"半 Kelly {float(r.get('adjusted_position', 0))*100:.1f}% / 风险评分 {float(r.get('risk_score', 0)):.2f}",
        "evidence": "; ".join(r.get("violations", []) or []) or "无违规",
        "confidence": float(r.get("confidence_aggregated", 0.5) or 0.5),
        "round": 5,
    })

    # Round 6: 仲裁
    a = arbiter.get("output", {})
    logs.append({
        "agent_name": "arbiter_researcher",
        "claim": f"最终 {a.get('direction', 'neutral')} / 置信度 {float(a.get('confidence', 0.5)):.2f}",
        "evidence": a.get("reasoning", "") or a.get("final_decision_basis", ""),
        "confidence": float(a.get("confidence", 0.5) or 0.5),
        "round": 6,
    })

    return logs


# ============ 测试 ============
if __name__ == "__main__":
    print("=== 测试 6 角色辩论 ===\n")

    # mock 信号
    text_signals = [
        {"source": "雪球", "content": f"半导体行业 28nm 国产替代加速，多家公司扩产", "sentiment": "positive"},
        {"source": "微博", "content": f"GPU 涨价潮持续，供不应求", "sentiment": "positive"},
        {"source": "财联社", "content": f"AI 算力需求强劲，光模块订单饱满", "sentiment": "positive"},
    ]

    tech_signals = [
        {"indicator": "MA20", "value": 45.2, "signal": "多头排列"},
        {"indicator": "RSI", "value": 65, "signal": "中性偏多"},
        {"indicator": "MACD", "value": 0.85, "signal": "金叉"},
    ]

    result = run_debate_v6(
        text_signals=text_signals,
        tech_signals=tech_signals,
        target_concept="半导体",
        target_date="2026-07-01",
    )

    print(f"\n=== 最终决策 ===")
    print(f"  方向: {result['direction']}")
    print(f"  置信度: {result['confidence']:.2f}")
    print(f"  Kelly 仓位: {result['kelly_position']*100:.1f}%")

    print(f"\n=== 辩论日志（{len(result['debate_logs'])} 条）===")
    for log in result['debate_logs']:
        print(f"  Round {log['round']} {log['agent_name']}: {log['claim'][:60]}")

    # 写数据库测试
    from db_schema import init_db, save_decision, save_debate_logs
    conn = init_db()
    decision_id = save_decision(conn, result)
    save_debate_logs(conn, decision_id, result['debate_logs'])
    print(f"\n✅ 写库成功 decision_id={decision_id}")
    conn.close()
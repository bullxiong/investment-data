"""
run_9agent.py - 跑完整 9 Agent 辩论，生成战报

与 run_real_reports.py 不同：完整 9 agent 流程，bull + bear 独立思考。
"""

from __future__ import annotations
import json
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent))

from research_loader import get_stocks_with_research, build_research_signals
from market_cap_parser import analyze_price_targets, calc_return_pct
from sentiment_data import build_sentiment_context
from investoday_client import InvestodayClient


# ============ 真实价格（多源 fallback） ============
def get_current_price(stock_code: str) -> float:
    """从多个数据源拉最新收盘价（带 fallback）
    优先级: tushare > 新浪 > 腾讯 > akshare
    """
    import urllib.request
    import socket

    # 1) tushare（最权威）
    try:
        import tushare as ts
        token = "7f6ec8a8c8616b14fc77829fb42555a7d3d6f0cf549530d583a1cad7"
        pro = ts.pro_api(token)
        # 加市场后缀
        if stock_code.startswith(("6", "9")):
            ts_code = f"{stock_code}.SH"
        else:
            ts_code = f"{stock_code}.SZ"
        from datetime import datetime, timedelta
        end = datetime.now().strftime("%Y%m%d")
        # 拉更长时间窗口（30 天），保证能找到数据
        start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
        if df is not None and len(df) > 0:
            return float(df.iloc[0]["close"])
    except Exception:
        pass

    # 2) 新浪
    try:
        if stock_code.startswith(("6", "9")):
            prefix = "sh"
        else:
            prefix = "sz"
        url = f"https://hq.sinajs.cn/list={prefix}{stock_code}"
        socket.setdefaulttimeout(8)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "*/*",
                "Referer": "https://finance.sina.com.cn",
            }
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("gbk", errors="ignore")
        if "=" in raw:
            data_part = raw.split('"')[1] if '"' in raw else ""
            if data_part:
                fields = data_part.split(",")
                if len(fields) > 3 and fields[3]:
                    return float(fields[3])
    except Exception:
        pass

    # 3) 腾讯
    try:
        if stock_code.startswith(("6", "9")):
            prefix = "sh"
        else:
            prefix = "sz"
        url = f"https://qt.gtimg.cn/q={prefix}{stock_code}"
        socket.setdefaulttimeout(8)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        if "~" in raw:
            data_part = raw.split('"')[1] if '"' in raw else ""
            if data_part:
                fields = data_part.split("~")
                if len(fields) > 3 and fields[3]:
                    return float(fields[3])
    except Exception:
        pass

    # 4) akshare 兜底
    try:
        import akshare as ak
        from datetime import datetime, timedelta
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=stock_code, period="daily",
                                start_date=start, end_date=end, adjust="qfq")
        if df is not None and len(df) > 0:
            return float(df.iloc[-1]["收盘"])
    except Exception:
        pass

    return 0.0


# ============ 市值 → 目标价 提取 ============
def get_research_target_prices(stock_code: str, stock_name: str,
                                research: Dict, current_price: float) -> Dict:
    """从研报内容中提取"目标市值 N 亿"+ 转股价

    Returns:
        {
            "candidate_targets": [
                {"mc_yi": 700, "price": 322.0, "return_pct": 113.5, "source": "..."}
            ],
            "best_target": {...},
            "conservative_target": {...},
            "summary": "...",
        }
    """
    contents = []
    for s in research.get("signals", []):
        if s.get("content"):
            contents.append(s["content"])

    if not contents:
        return {"candidate_targets": [], "best_target": None,
                "conservative_target": None, "summary": "无研报内容"}

    result = analyze_price_targets(stock_code, stock_name, contents, current_price)

    return {
        "candidate_targets": result.get("target_prices", []),
        "best_target": result.get("best_target"),
        "conservative_target": result.get("conservative_target"),
        "total_share_yi": result.get("total_share_yi"),
        "summary": result.get("summary", ""),
    }


# ============ DeepSeek 调用 ============
def call_deepseek(system_prompt: str, user_prompt: str,
                  temperature: float = 0.2, max_tokens: int = 2000) -> str:
    """调 DeepSeek API（直接用，避免 llm_task 不可用）"""
    config_path = Path("/workspace/config.json")
    api_key = ""
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            api_key = cfg.get("deepseek", {}).get("api_key", "")
        except Exception:
            pass

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
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def call_agent(role: str, system_prompt_path: str, user_prompt: str) -> Dict:
    """通用 Agent 调用"""
    sp_path = Path("/workspace/src/decision_engine/prompts") / f"{role}.txt"
    system_prompt = sp_path.read_text(encoding="utf-8") if sp_path.exists() else "你是助手"

    start = time.time()
    try:
        content = call_deepseek(system_prompt, user_prompt)
        elapsed = int((time.time() - start) * 1000)

        m = re.search(r"\{[\s\S]*\}", content)
        if m:
            try:
                output = json.loads(m.group(0))
            except Exception:
                output = {"raw": content}
        else:
            output = {"raw": content}

        return {"status": "ok", "output": output, "latency_ms": elapsed}
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        return {"status": "error", "error": str(e), "latency_ms": elapsed}


# ============ 9 Agent 辩论 ============
def run_9_agent_debate(stock_code: str, stock_name: str,
                       research: Dict, current_price: float = None) -> Dict:
    """跑一次完整 9 Agent 辩论"""

    # 准备公共输入
    research_signals = research
    stock_info = f"代码: {stock_code}\n名称: {stock_name}"
    if current_price:
        stock_info += f"\n最新价: ¥{current_price:.2f}"

    # 提前算"研报候选目标价"（market_cap_parser）
    if current_price:
        target_analysis = get_research_target_prices(stock_code, stock_name, research, current_price)
    else:
        target_analysis = {"candidate_targets": [], "best_target": None,
                           "conservative_target": None, "summary": "无当前价"}

    timeline = []
    all_agents = {}

    # 把候选目标价存到 all_agents 顶部，便于后续引用
    all_agents["research_target_prices"] = {
        "status": "ok",
        "latency_ms": 0,
        "output": target_analysis,
    }

    # Step 1: intel_agent
    print(f"  [1/9] intel_agent...", end=" ")
    user_prompt = f"""整合以下信号输出事实卡：
{stock_info}
行业: {research.get('industry', '未知')}
板块: {research.get('sector', '未知')}
研报/新闻 ({research.get('signal_count', 0)} 条):
{chr(10).join([f"[{i+1}] {(s.get('title') or '无标题')[:50]} | {s.get('author')} | {s.get('content', '')[:200]}" for i, s in enumerate(research.get('signals', [])[:5])])}"""
    res = call_agent("intel_agent", "", user_prompt)
    timeline.append({"stage": "1-intel", "agent": "intel_agent", "latency_ms": res["latency_ms"]})
    all_agents["intel_agent"] = res
    fact_card = res.get("output", {})
    print(f"{res['latency_ms']}ms")

    # Step 2: 基本面三角色（串行，但逻辑并行）
    # industry_agent
    print(f"  [2/9] industry_agent...", end=" ")
    user_prompt = f"""分析 {stock_info} 所在行业（{research.get('sector', '未知')}）:
研报摘要：
{chr(10).join([(s.get('content', '')[:400]) for s in research.get('signals', [])[:3]])}"""
    res = call_agent("industry_agent", "", user_prompt)
    timeline.append({"stage": "2-industry", "agent": "industry_agent", "latency_ms": res["latency_ms"]})
    all_agents["industry_agent"] = res
    industry_result = res.get("output", {})
    print(f"{res['latency_ms']}ms")

    # company_agent（接入 investoday 财务数据）
    print(f"  [3/9] company_agent...", end=" ")
    it_client = InvestodayClient()
    financial_report = it_client.build_financial_report(stock_code)

    financial_section = ""
    if financial_report.get("data_ok"):
        signals = financial_report.get("strategy_signals", [])
        signals_text = "\n".join([f"  - {s['signal']} [{s['strength']}]" for s in signals]) if signals else "  无特殊触发"
        financial_section = (
            f"\n📊 **investoday 财务分析 5 步框架**：\n"
            f"- 行业：{financial_report['company_image'].get('industry', '?')}\n"
            f"- 报告期：{financial_report.get('data_period', '?')}\n"
            f"- 盈利：毛利 {financial_report['profitability'].get('gross_margin')}%  净利率 {financial_report['profitability'].get('net_margin')}%  ROE {financial_report['profitability'].get('roe_diluted')}%\n"
            f"- 利润质量：经营现金流/净利润 = {financial_report['profit_quality'].get('cfo_to_net_profit')}\n"
            f"- 成长：营收 +{financial_report['growth'].get('revenue_growth_1y', 0)*100:.1f}%  净利 +{financial_report['growth'].get('net_profit_growth_1y', 0)*100:.1f}%\n"
            f"- 安全：负债率 {financial_report['safety'].get('debt_asset_ratio')}%  利息保障 {financial_report['safety'].get('ebit_interest_coverage'):.1f}\n"
            f"- 策略信号：\n{signals_text}\n"
        )
    else:
        financial_section = "\n📊 财务数据暂不可用，按研报推断。"

    user_prompt = f"""分析 {stock_info}:
简介: {research.get('stock_description', '无')[:300]}
{financial_section}
研报摘要：
{chr(10).join([(s.get('content', '')[:400]) for s in research.get('signals', [])[:3]])}"""
    res = call_agent("company_agent", "", user_prompt)
    timeline.append({"stage": "2-company", "agent": "company_agent", "latency_ms": res["latency_ms"]})
    all_agents["company_agent"] = res
    company_result = res.get("output", {})
    # 把财务报告也存到 all_agents 里
    if financial_report.get("data_ok"):
        all_agents["company_agent"]["financial_data"] = financial_report
    print(f"{res['latency_ms']}ms")

    # valuation_agent
    print(f"  [4/9] valuation_agent...", end=" ")
    user_prompt = f"""评估 {stock_info} 估值（{f"最新价 ¥{current_price:.2f}" if current_price else "未知价"}）:
研报中的估值信息：
{chr(10).join([(s.get('content', '')[:300]) for s in research.get('signals', [])[:3]])}"""
    res = call_agent("valuation_agent", "", user_prompt)
    timeline.append({"stage": "2-valuation", "agent": "valuation_agent", "latency_ms": res["latency_ms"]})
    all_agents["valuation_agent"] = res
    valuation_result = res.get("output", {})
    print(f"{res['latency_ms']}ms")

    # Step 3: bull + bear + sentiment（逻辑上独立）
    # bull_agent
    print(f"  [5/9] bull_agent...", end=" ")
    user_prompt = f"""基于事实卡 + 基本面分析，从多头角度论证（你看不到空头观点）：

事实卡: {json.dumps(fact_card, ensure_ascii=False)[:500]}
行业: {json.dumps(industry_result, ensure_ascii=False)[:300]}
公司: {json.dumps(company_result, ensure_ascii=False)[:300]}
估值: {json.dumps(valuation_result, ensure_ascii=False)[:300]}"""
    res = call_agent("bull_agent", "", user_prompt)
    timeline.append({"stage": "3-bull", "agent": "bull_agent", "latency_ms": res["latency_ms"]})
    all_agents["bull_agent"] = res
    print(f"{res['latency_ms']}ms")

    # bear_agent
    print(f"  [6/9] bear_agent...", end=" ")
    user_prompt = f"""基于事实卡 + 基本面分析，从空头角度论证（你看不到多头观点）：

事实卡: {json.dumps(fact_card, ensure_ascii=False)[:500]}
行业: {json.dumps(industry_result, ensure_ascii=False)[:300]}
公司: {json.dumps(company_result, ensure_ascii=False)[:300]}
估值: {json.dumps(valuation_result, ensure_ascii=False)[:300]}"""
    res = call_agent("bear_agent", "", user_prompt)
    timeline.append({"stage": "3-bear", "agent": "bear_agent", "latency_ms": res["latency_ms"]})
    all_agents["bear_agent"] = res
    print(f"{res['latency_ms']}ms")

    # sentiment_agent
    print(f"  [7/9] sentiment_agent...", end=" ")
    # 加载实时数据（Layer 1 + Layer 2）
    sentiment_ctx = build_sentiment_context(
        stock_code,
        sector_name=research.get("sector", "") or "",
        today="20260629",
    )

    user_prompt = f"""判断 {stock_info} 的市场情绪周期：

📊 **实时个股数据**：
{json.dumps(sentiment_ctx.get('stock', {}), ensure_ascii=False, indent=2)}

📊 **实时板块数据**：
{json.dumps(sentiment_ctx.get('sector', {}), ensure_ascii=False, indent=2)}

📄 **研报摘要**：
{chr(10).join([(s.get('content', '')[:200]) for s in research.get('signals', [])[:3]])}"""
    res = call_agent("sentiment_agent", "", user_prompt)
    timeline.append({"stage": "3-sentiment", "agent": "sentiment_agent", "latency_ms": res["latency_ms"]})
    all_agents["sentiment_agent"] = res
    print(f"{res['latency_ms']}ms")

    # Step 4: risk_agent
    print(f"  [8/9] risk_agent...", end=" ")
    user_prompt = f"""综合所有 Agent 输出做风控：
bull: {json.dumps(all_agents['bull_agent'].get('output', {}), ensure_ascii=False)[:300]}
bear: {json.dumps(all_agents['bear_agent'].get('output', {}), ensure_ascii=False)[:300]}
sentiment: {json.dumps(all_agents['sentiment_agent'].get('output', {}), ensure_ascii=False)[:300]}"""
    res = call_agent("risk_agent", "", user_prompt)
    timeline.append({"stage": "4-risk", "agent": "risk_agent", "latency_ms": res["latency_ms"]})
    all_agents["risk_agent"] = res
    print(f"{res['latency_ms']}ms")

    # Step 5: arbiter_agent
    print(f"  [9/9] arbiter_agent...", end=" ")

    # 构造"研报候选目标价"文本段
    candidate_section = ""
    if target_analysis.get("candidate_targets"):
        cands = target_analysis["candidate_targets"][:3]
        candidate_lines = []
        for i, c in enumerate(cands, 1):
            candidate_lines.append(
                f"  {i}) 目标市值 {c['market_cap_yi']:.0f} 亿 → 目标价 ¥{c['price']:.2f} "
                f"({c['return_pct']:+.1f}%) — 依据: {c['source'][:60]}"
            )
        candidate_section = (
            "📌 **研报中提取的候选目标价（自动解析市值 → 股价）**：\n"
            f"  当前总股本 {target_analysis.get('total_share_yi', '?')} 亿股\n"
            + "\n".join(candidate_lines)
            + "\n  请根据这些线索 + Agent 综合判断决定最终 target_price。"
        )
    else:
        candidate_section = "📌 研报中未提取到明确市值目标，请自行判断 target_price。"

    user_prompt = f"""基于所有 Agent 输出给最终决策：
{f"最新价: ¥{current_price:.2f}" if current_price else "未知价"}

{candidate_section}

所有 Agent 输出:
- bull: {json.dumps(all_agents['bull_agent'].get('output', {}), ensure_ascii=False)[:300]}
- bear: {json.dumps(all_agents['bear_agent'].get('output', {}), ensure_ascii=False)[:300]}
- sentiment: {json.dumps(all_agents['sentiment_agent'].get('output', {}), ensure_ascii=False)[:300]}
- risk: {json.dumps(all_agents['risk_agent'].get('output', {}), ensure_ascii=False)[:300]}

必须严格 JSON 输出：
{{
  "direction": "bullish|bearish|neutral",
  "confidence": 0.0,
  "entry_price": 数字,
  "target_price": 数字,
  "stop_loss": 数字,
  "position_pct": 0.0,
  "time_horizon": "短线|中线|长线",
  "key_catalysts": ["..."],
  "key_risks": ["..."],
  "reasoning": "...",
  "agent_agreement": {{"bull_score": 0.0, "bear_score": 0.0, "fundamental_score": 0.0, "sentiment_score": 0.0}}
}}"""
    res = call_agent("arbiter_agent", "", user_prompt)
    timeline.append({"stage": "5-arbiter", "agent": "arbiter_agent", "latency_ms": res["latency_ms"]})
    all_agents["arbiter_agent"] = res
    print(f"{res['latency_ms']}ms")

    decision = res.get("output", {})

    # 三件套兜底（如果 LLM 没填）
    if current_price:
        if not decision.get("entry_price") or decision.get("entry_price") == 0:
            decision["entry_price"] = current_price
        if not decision.get("target_price") or decision.get("target_price") == 0:
            direction = decision.get("direction", "neutral")
            if direction == "bullish":
                decision["target_price"] = round(current_price * 1.15, 2)
            elif direction == "bearish":
                decision["target_price"] = round(current_price * 0.90, 2)
            else:
                decision["target_price"] = round(current_price * 1.05, 2)
        if not decision.get("stop_loss") or decision.get("stop_loss") == 0:
            direction = decision.get("direction", "neutral")
            if direction == "bullish":
                decision["stop_loss"] = round(current_price * 0.93, 2)
            elif direction == "bearish":
                decision["stop_loss"] = round(current_price * 1.05, 2)
            else:
                decision["stop_loss"] = round(current_price * 0.97, 2)

    return {
        "decision": decision,
        "all_agents": all_agents,
        "research_target_prices": target_analysis,
        "timeline": timeline,
        "total_latency_ms": sum(t["latency_ms"] for t in timeline),
    }


# ============ 主流程 ============
def main(limit: int = 3):
    """主入口：跑前 N 只"""
    print(f"=== 9 Agent 真实战报（限 {limit} 只）===\n")

    # 拉研报数据
    stocks = get_stocks_with_research()

    # 过滤：只保留 6 位数字代码（排除 BK 板块）
    target_stocks = [
        s for s in stocks
        if s["code"].isdigit() and len(s["code"]) == 6
    ][:limit]

    print(f"目标股数: {len(target_stocks)}\n")

    reports = []
    for i, stock in enumerate(target_stocks):
        code = stock["code"]
        name = stock.get("name", "")

        print(f"[{i+1}/{len(target_stocks)}] {code} {name}")
        research = build_research_signals(code)

        # 拉真实价格
        current_price = get_current_price(code)
        print(f"  现价: ¥{current_price:.2f}" if current_price else "  现价: 未知")

        # 跑 9 agent
        result = run_9_agent_debate(code, name, research, current_price)
        decision = result["decision"]

        report = {
            "report_id": f"RPT-{datetime.now().strftime('%Y%m%d')}-{code}",
            "timestamp": datetime.now().isoformat(),
            "target_concept": stock.get("sector", "") or stock.get("industry", ""),
            "target_company": name,
            "target_code": code,
            "decision": decision.get("direction", "neutral"),
            "confidence": float(decision.get("confidence", 0) or 0),
            "entry_price": float(decision.get("entry_price", 0) or 0),
            "target_price": float(decision.get("target_price", 0) or 0),
            "stop_loss": float(decision.get("stop_loss", 0) or 0),
            "position_pct": float(decision.get("position_pct", 0) or 0),
            "time_horizon": decision.get("time_horizon", ""),
            "all_agents": result["all_agents"],
            "research_target_prices": result["research_target_prices"],
            "timeline": result["timeline"],
            "total_latency_ms": result["total_latency_ms"],
        }
        reports.append(report)

        d = report["decision"]
        c = report["confidence"]
        print(f"  → {d} 置信度 {c:.2f} 总耗时 {result['total_latency_ms']}ms\n")

    # 保存
    payload = {
        "generated_at": datetime.now().isoformat(),
        "count": len(reports),
        "reports": reports,
    }

    paths = [
        Path("data/reports.json"),
        Path("dashboard/public/data/reports.json"),
        Path("dashboard/dist/data/reports.json"),
    ]
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 保存 {len(reports)} 条到:")
    for p in paths:
        print(f"   {p}")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    main(limit)
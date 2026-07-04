"""
run_real_reports.py - 用真实 SQLite 数据跑决策引擎，生成真实战报

为 Dashboard 提供 reports.json
"""

from __future__ import annotations
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# 让 import 找到 modules
sys.path.insert(0, str(Path(__file__).parent))

from research_loader import get_stocks_with_research, build_research_signals
from debate import run_debate
import urllib.request
import re


# 用 minimax/auto 调 LLM（已配置在 llm_client.py）
# 但因为平台 llm_task 是同步阻塞，单次决策 9 个 LLM 调用会很慢
# 简化方案：每个股票用 1 个综合 LLM 调用（agent 合并）


def quick_analyze(stock_code: str, stock_name: str, research: Dict) -> Dict[str, Any]:
    """
    单次 LLM 综合判断（不走完整 9 agent 辩论，省时间）
    实际生产应该用 debate.py 完整流程
    """
    import json
    import urllib.request
    from pathlib import Path

    # 直接用 DeepSeek（避免 llm_task 不可用问题）
    config_path = Path("/workspace/config.json")
    api_key = ""
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            api_key = cfg.get("deepseek", {}).get("api_key", "")
        except Exception:
            pass

    if not api_key:
        # fallback
        elapsed = 0
        return {
            "decision": {
                "direction": "neutral",
                "confidence": 0.0,
                "key_catalysts": [],
                "key_risks": ["无 DeepSeek API key"],
                "reasoning": "配置缺失",
            },
            "timeline": [{"stage": "1-llm-skip", "agent": "deepseek", "latency_ms": 0}],
            "total_latency_ms": 0,
            "status": "error",
            "error": "no_api_key",
        }

    client = None  # 不再用 llm_client

    system_prompt = """你是 A 股投研决策专家。给定股票基本信息 + 研报/新闻内容，给出投资决策。
请严格按 JSON 输出。
"""

    signals = research.get('signals', [])[:5]
    signal_lines = []
    for i, s in enumerate(signals):
        line = f"[{i+1}] {s['title']} | {s['author']} | {s['publish_time'][:10]}\n{s['content'][:500]}"
        signal_lines.append(line)
    signals_text = "\n".join(signal_lines)

    user_prompt = f"""【股票信息】
代码: {stock_code}
名称: {stock_name}
行业: {research.get('industry', '未知')}
板块: {research.get('sector', '未知')}
简介: {research.get('stock_description', '无')[:200]}

【研报/新闻摘要】（共 {research.get('signal_count', 0)} 条）
{signals_text}

【决策要求】
基于以上真实数据，给出：
- direction: bullish / bearish / neutral
- confidence: 0.0-1.0
- key_catalysts: 关键催化（最多 3 个）
- key_risks: 关键风险（最多 3 个）
- reasoning: 综合分析（200 字内）

严格 JSON 输出：
{{
  "direction": "...",
  "confidence": 0.0,
  "key_catalysts": ["...", "..."],
  "key_risks": ["...", "..."],
  "reasoning": "..."
}}"""

    timeline = []
    start = time.time()

    try:
        # 直接调 DeepSeek
        body = json.dumps({
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 1500,
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

        elapsed = int((time.time() - start) * 1000)
        timeline.append({"stage": "1-llm", "agent": "deepseek", "latency_ms": elapsed})

        content = data["choices"][0]["message"]["content"]
        # 提取 JSON
        m = re.search(r"\{[\s\S]*\}", content)
        if m:
            try:
                decision = json.loads(m.group(0))
            except Exception:
                decision = {"raw": content, "parse_error": True}
        else:
            decision = {"raw": content}

        return {
            "decision": decision,
            "timeline": timeline,
            "total_latency_ms": elapsed,
            "status": "ok",
        }
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        timeline.append({"stage": "1-llm-error", "agent": "deepseek", "latency_ms": elapsed})
        return {
            "decision": {
                "direction": "neutral",
                "confidence": 0.0,
                "key_catalysts": [],
                "key_risks": [f"DeepSeek 错误: {str(e)[:200]}"],
                "reasoning": "分析失败",
            },
            "timeline": timeline,
            "total_latency_ms": elapsed,
            "status": "error",
            "error": str(e),
        }


def build_report(stock: Dict, analysis: Dict) -> Dict:
    """构造战报 record"""
    decision = analysis.get("decision", {})
    direction = decision.get("direction", "neutral")
    confidence = float(decision.get("confidence", 0) or 0)

    # 构造模拟三件套（基于决策方向）
    # 真实场景应该从数据源（akshare）拉
    entry = 0
    target = 0
    stop = 0
    position = 0.0
    if direction == "bullish":
        position = round(0.05 + confidence * 0.10, 3)  # 5%-15% 仓位
    elif direction == "bearish":
        position = 0.0  # 不做空，只观望
    else:
        position = 0.0

    return {
        "report_id": f"RPT-{datetime.now().strftime('%Y%m%d')}-{stock['code']}",
        "timestamp": datetime.now().isoformat(),
        "target_concept": stock.get("sector", "") or stock.get("industry", ""),
        "target_company": stock.get("name", ""),
        "target_code": stock.get("code", ""),
        "decision": direction,
        "confidence": confidence,
        "entry_price": entry,
        "target_price": target,
        "stop_loss": stop,
        "position_pct": position,
        "all_agents": {
            "research_signals": {
                "status": "ok",
                "output": {
                    "signal_count": stock.get("research_count", 0),
                    "signals": [],
                },
            },
            "decision_llm": {
                "status": analysis.get("status", "ok"),
                "output": decision,
            },
        },
        "timeline": analysis.get("timeline", []),
        "total_latency_ms": analysis.get("total_latency_ms", 0),
    }


def main():
    print("=== 真实战报生成 ===\n")

    # 1) 找有研报的股票
    stocks = get_stocks_with_research()
    print(f"找到 {len(stocks)} 只有研报的股票\n")

    # 2) 取前 10 只（避免 LLM 调太多）
    target_stocks = stocks[:10]

    reports = []
    for i, stock in enumerate(target_stocks):
        code = stock["code"]
        name = stock.get("name", "")

        # 跳过无效代码（如 BK0666 = 板块指数，不是真实股票）
        if not code.isdigit() or len(code) != 6:
            print(f"[{i+1}/{len(target_stocks)}] {code} {name}  跳过（非个股代码）")
            continue

        print(f"[{i+1}/{len(target_stocks)}] {code} {name} 研报 {stock['research_count']} 条", end=" ... ")

        # 加载研报
        research = build_research_signals(code)

        # 调 LLM
        analysis = quick_analyze(code, name, research)

        # 构造战报
        report = build_report(stock, analysis)
        reports.append(report)

        d = report["decision"]
        c = report["confidence"]
        print(f"{d} 置信度 {c:.2f}  耗时 {report['total_latency_ms']}ms")

    # 3) 保存
    out_path = Path("data/reports.json")
    payload = {
        "generated_at": datetime.now().isoformat(),
        "count": len(reports),
        "reports": reports,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # 同时复制到 dashboard public/data
    public_path = Path("dashboard/public/data/reports.json")
    public_path.parent.mkdir(parents=True, exist_ok=True)
    with open(public_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # dist/data 也复制（部署用）
    dist_path = Path("dashboard/dist/data/reports.json")
    dist_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dist_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 保存 {len(reports)} 条战报到:")
    print(f"   {out_path}")
    print(f"   {public_path}")
    print(f"   {dist_path}")


if __name__ == "__main__":
    main()
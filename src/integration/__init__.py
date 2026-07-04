#!/usr/bin/env python3
"""
src/integration/__init__.py
集成层 (Kimi 负责)

暴露接口:
    fusion_report(conn, target_date) -> Dict
    generate_dashboard(report, output_path) -> None

功能:
    1. 从数据库读取 GLM 的 text_signals + Kimi 的 strategy_signals + MiniMax 的 decisions
    2. 融合生成 "三因子报告"
    3. 生成 HTML Dashboard
"""

import json
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("integration")


def fusion_report(conn: sqlite3.Connection, target_date: str) -> Dict[str, Any]:
    """
    三因子融合报告：基本面(GLM) + 技术面(Kimi) + 资金面(MiniMax) -> 综合决策。

    Args:
        conn: sqlite3 连接
        target_date: 目标日期 YYYY-MM-DD

    Returns:
        Dict {
            "target_date": str,
            "text_signals": List[Dict],      # GLM 文本信号
            "tech_signals": List[Dict],      # Kimi 技术面信号
            "decisions": List[Dict],          # MiniMax 决策
            "fusion_summary": str,           # LLM 总结（简化版直接拼接）
            "recommendations": List[Dict],    # 最终推荐
        }
    """
    logger.info(f"生成融合报告: {target_date}")

    # 1. 读取 GLM 的 text_signals
    cursor = conn.execute("""
        SELECT concept, related_stocks, sentiment, direction, confidence, extracted_date
        FROM text_signals
        WHERE extracted_date = ?
        ORDER BY confidence DESC
    """, (target_date,))
    text_signals = [dict(row) for row in cursor.fetchall()]

    # 2. 读取 Kimi 的 strategy_signals
    cursor = conn.execute("""
        SELECT code, strategy_name, action, trigger_price, target_price, stop_price, strength, signal_date
        FROM strategy_signals
        WHERE signal_date = ?
        ORDER BY strength DESC
    """, (target_date,))
    tech_signals = [dict(row) for row in cursor.fetchall()]

    # 3. 读取 MiniMax 的 decisions
    cursor = conn.execute("""
        SELECT concept, code, direction, confidence, kelly_ratio, final_position, entry_price, target_price, stop_loss, risk_level
        FROM decisions
        WHERE target_date = ? AND status = 'active'
        ORDER BY confidence DESC
    """, (target_date,))
    decisions = [dict(row) for row in cursor.fetchall()]

    # 4. 融合分析（简化版：按概念聚合，技术面+基本面共振加分）
    recommendations = []

    # 按概念分组
    concept_map = {}
    for sig in text_signals:
        c = sig["concept"]
        if c not in concept_map:
            concept_map[c] = {"text": [], "tech": [], "decision": None}
        concept_map[c]["text"].append(sig)

    for sig in tech_signals:
        # 从 code 推断概念（简化：用 decisions 关联）
        code = sig["code"]
        for c, data in concept_map.items():
            # 检查 code 是否在 related_stocks 中
            for ts in data["text"]:
                stocks = ts.get("related_stocks", "[]")
                try:
                    stock_list = json.loads(stocks) if isinstance(stocks, str) else stocks
                    if code in stock_list:
                        data["tech"].append(sig)
                        break
                except:
                    pass
            if sig in data["tech"]:
                break

    for dec in decisions:
        c = dec["concept"]
        if c in concept_map:
            concept_map[c]["decision"] = dec

    # 生成推荐
    for concept, data in concept_map.items():
        text = data["text"]
        tech = data["tech"]
        dec = data["decision"]

        # 计算综合得分
        text_score = sum(s.get("confidence", 0) for s in text) / len(text) if text else 0
        tech_score = sum(s.get("strength", 0) for s in tech) / len(tech) if tech else 0
        decision_score = dec.get("confidence", 0) if dec else 0

        # 共振：技术面+基本面同时看多
        has_bullish_text = any(s.get("sentiment") == "bullish" for s in text)
        has_bullish_tech = any(s.get("action") == "buy" for s in tech)
        resonance = has_bullish_text and has_bullish_tech

        composite_score = (text_score + tech_score + decision_score) / 3
        if resonance:
            composite_score = min(composite_score + 0.15, 1.0)

        # 推荐等级
        if composite_score >= 0.75:
            level = "强烈推荐"
        elif composite_score >= 0.55:
            level = "推荐"
        elif composite_score >= 0.35:
            level = "观望"
        else:
            level = "回避"

        recommendations.append({
            "concept": concept,
            "composite_score": round(composite_score, 3),
            "text_score": round(text_score, 3),
            "tech_score": round(tech_score, 3),
            "decision_score": round(decision_score, 3),
            "resonance": resonance,
            "level": level,
            "direction": dec.get("direction", "neutral") if dec else "neutral",
            "position": dec.get("final_position", 0) if dec else 0,
            "entry": dec.get("entry_price", 0) if dec else 0,
            "target": dec.get("target_price", 0) if dec else 0,
            "stop": dec.get("stop_loss", 0) if dec else 0,
        })

    recommendations.sort(key=lambda x: x["composite_score"], reverse=True)

    # 5. 生成总结文本
    summary = f"""三因子融合报告 ({target_date})

基本面信号: {len(text_signals)} 条
技术面信号: {len(tech_signals)} 条
投研决策: {len(decisions)} 条

推荐列表:
"""
    for r in recommendations[:5]:
        summary += f"  {r['level']} | {r['concept']} | 综合得分={r['composite_score']:.2f} | "
        if r['direction'] != 'neutral':
            summary += f"方向={r['direction']} 仓位={r['position']:.1%}"
        else:
            summary += "方向=中性"
        summary += f" 共振={'是' if r['resonance'] else '否'}\n"

    report = {
        "target_date": target_date,
        "text_signals_count": len(text_signals),
        "tech_signals_count": len(tech_signals),
        "decisions_count": len(decisions),
        "recommendations": recommendations,
        "fusion_summary": summary,
        "generated_at": datetime.now().isoformat(),
    }

    logger.info(f"融合报告完成: {len(recommendations)} 条推荐")
    return report


def generate_dashboard(report: Dict, output_path: str) -> None:
    """
    生成 HTML Dashboard。

    Args:
        report: fusion_report 的输出
        output_path: HTML 输出路径
    """
    logger.info(f"生成 Dashboard: {output_path}")

    target_date = report.get("target_date", str(date.today()))
    recommendations = report.get("recommendations", [])
    summary = report.get("fusion_summary", "")

    # 颜色映射
    def level_color(level: str) -> str:
        colors = {
            "强烈推荐": "#4caf50",
            "推荐": "#8bc34a",
            "观望": "#ff9800",
            "回避": "#f44336",
        }
        return colors.get(level, "#999")

    def direction_icon(d: str) -> str:
        return {"long": "📈", "short": "📉", "neutral": "➡️"}.get(d, "➡️")

    # 构建推荐表格行
    rows_html = ""
    for r in recommendations:
        bg = f"background-color: {level_color(r['level'])}; color: white;" if r["level"] in ("强烈推荐", "推荐") else ""
        rows_html += f"""
        <tr style="{bg}">
            <td><strong>{r['concept']}</strong></td>
            <td>{direction_icon(r['direction'])} {r['direction']}</td>
            <td>{r['level']}</td>
            <td>{r['composite_score']:.2f}</td>
            <td>{r['text_score']:.2f}</td>
            <td>{r['tech_score']:.2f}</td>
            <td>{r['decision_score']:.2f}</td>
            <td>{'是' if r['resonance'] else '否'}</td>
            <td>{r['position']:.1%}</td>
            <td>{r['entry']:.2f} / {r['target']:.2f} / {r['stop']:.2f}</td>
        </tr>
        """

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>3AI 投研决策 - {target_date}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; border-radius: 12px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
        h1 {{ color: #333; margin-bottom: 8px; }}
        .meta {{ color: #888; font-size: 14px; margin-bottom: 24px; }}
        .stats {{ display: flex; gap: 20px; margin-bottom: 24px; }}
        .stat-card {{ flex: 1; background: #fafafa; border-radius: 8px; padding: 16px; text-align: center; }}
        .stat-card .number {{ font-size: 28px; font-weight: bold; color: #2196f3; }}
        .stat-card .label {{ font-size: 12px; color: #888; margin-top: 4px; }}
        .summary {{ background: #fff8e1; border-left: 4px solid #ffc107; padding: 16px; margin-bottom: 24px; border-radius: 4px; white-space: pre-wrap; font-size: 14px; line-height: 1.6; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
        th {{ background: #2196f3; color: white; padding: 12px; text-align: left; font-weight: 500; }}
        td {{ padding: 10px 12px; border-bottom: 1px solid #eee; }}
        tr:hover {{ background: #f5f5f5; }}
        .footer {{ margin-top: 24px; color: #aaa; font-size: 12px; text-align: center; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>3AI 投研决策报告</h1>
        <div class="meta">目标日期: {target_date} | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>

        <div class="stats">
            <div class="stat-card">
                <div class="number">{report.get('text_signals_count', 0)}</div>
                <div class="label">基本面信号</div>
            </div>
            <div class="stat-card">
                <div class="number">{report.get('tech_signals_count', 0)}</div>
                <div class="label">技术面信号</div>
            </div>
            <div class="stat-card">
                <div class="number">{report.get('decisions_count', 0)}</div>
                <div class="label">投研决策</div>
            </div>
            <div class="stat-card">
                <div class="number">{len(recommendations)}</div>
                <div class="label">融合推荐</div>
            </div>
        </div>

        <div class="summary">
{summary}
        </div>

        <h2>推荐列表</h2>
        <table>
            <thead>
                <tr>
                    <th>概念</th>
                    <th>方向</th>
                    <th>等级</th>
                    <th>综合</th>
                    <th>基本面</th>
                    <th>技术面</th>
                    <th>决策</th>
                    <th>共振</th>
                    <th>仓位</th>
                    <th>入场/目标/止损</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>

        <div class="footer">
            3AI 协作投资系统 | GLM + Kimi + MiniMax
        </div>
    </div>
</body>
</html>"""

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info(f"Dashboard 已生成: {output_path}")

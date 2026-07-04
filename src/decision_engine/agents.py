"""
agents.py - 6 个 Agent 基类与实现

每个 Agent 接收 input_dict → 调 LLM → 输出 JSON
"""

from __future__ import annotations
import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from .llm_client import get_client


PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    """加载 Prompt 模板"""
    path = PROMPTS_DIR / f"{name}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Prompt 不存在: {path}")


# ============ Agent 基类 ============
class BaseAgent(ABC):
    """Agent 基类"""

    name: str = "base_agent"
    weight: float = 0.0

    def __init__(self, provider: str = "minimax"):
        self.client = get_client(provider)

    @abstractmethod
    def system_prompt(self) -> str:
        """返回系统 Prompt"""
        ...

    @abstractmethod
    def build_user_prompt(self, input_data: Dict) -> str:
        """构建用户 Prompt"""
        ...

    @abstractmethod
    def parse_output(self, raw: str) -> Dict[str, Any]:
        """解析 LLM 输出"""
        ...

    def run(self, input_data: Dict) -> Dict[str, Any]:
        """主入口：执行一次 Agent"""
        sys_p = self.system_prompt()
        usr_p = self.build_user_prompt(input_data)

        try:
            result = self.client.chat(sys_p, usr_p, temperature=0.2, max_tokens=2500)
            content = result.get("content", "")
            if isinstance(content, dict):
                return {"agent": self.name, "status": "ok", "output": self.parse_output(json.dumps(content))}
            return {"agent": self.name, "status": "ok", "output": self.parse_output(content)}
        except Exception as e:
            return {"agent": self.name, "status": "error", "error": str(e)}


# ============ 1. intel_agent（情报整合） ============
class IntelAgent(BaseAgent):
    name = "intel_agent"
    weight = 0.10

    def system_prompt(self) -> str:
        return load_prompt("intel_agent")

    def build_user_prompt(self, input_data: Dict) -> str:
        return f"""请整合以下信号源，输出事实卡。

【text_signals（社交媒体/新闻）】
{json.dumps(input_data.get("text_signals", {}), ensure_ascii=False, indent=2)}

【tech_signals（技术指标）】
{json.dumps(input_data.get("tech_signals", {}), ensure_ascii=False, indent=2)}

【research_signals（研报，基本面）】
{json.dumps(input_data.get("research_signals", {}), ensure_ascii=False, indent=2)}

【情绪指标】
{json.dumps(input_data.get("sentiment_data", {}), ensure_ascii=False, indent=2)}

请按 system prompt 定义的 fact_card JSON Schema 输出。"""

    def parse_output(self, raw: str) -> Dict:
        # 提取 JSON 块
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"raw": raw}


# ============ 2. industry_agent（行业分析） ============
class IndustryAgent(BaseAgent):
    name = "industry_agent"
    weight = 0.12

    def system_prompt(self) -> str:
        return load_prompt("industry_agent")

    def build_user_prompt(self, input_data: Dict) -> str:
        return f"""请分析以下行业的基本面。

【目标概念/行业】
{input_data.get("concept", "未知")}

【研报库摘要（行业相关）】
{json.dumps(input_data.get("research_signals", {}), ensure_ascii=False, indent=2)}

【行业政策/新闻（如有）】
{input_data.get("industry_news", "无")}

请输出行业景气度 + 政策方向 + 估值水位 + 周期位置。"""

    def parse_output(self, raw: str) -> Dict:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"raw": raw}


# ============ 3. company_agent（公司分析） ============
class CompanyAgent(BaseAgent):
    name = "company_agent"
    weight = 0.13

    def system_prompt(self) -> str:
        return load_prompt("company_agent")

    def build_user_prompt(self, input_data: Dict) -> str:
        return f"""请分析以下公司的护城河和业绩。

【目标公司】
{input_data.get("target_company", "未知")}

【公司研报摘要】
{json.dumps(input_data.get("company_research", {}), ensure_ascii=False, indent=2)}

【财务数据】
{json.dumps(input_data.get("financials", {}), ensure_ascii=False, indent=2)}

【竞争对手信息】
{input_data.get("competitors", "无")}

请输出护城河评估 + 业绩拐点 + 竞争格局。"""

    def parse_output(self, raw: str) -> Dict:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"raw": raw}


# ============ 4. valuation_agent（估值分析） ============
class ValuationAgent(BaseAgent):
    name = "valuation_agent"
    weight = 0.10

    def system_prompt(self) -> str:
        return load_prompt("valuation_agent")

    def build_user_prompt(self, input_data: Dict) -> str:
        return f"""请评估以下标的的估值水平。

【目标公司】
{input_data.get("target_company", "未知")}

【财务/估值数据】
{json.dumps(input_data.get("financials", {}), ensure_ascii=False, indent=2)}

【行业平均估值】
{json.dumps(input_data.get("industry_avg", {}), ensure_ascii=False, indent=2)}

【历史估值分位】
{json.dumps(input_data.get("valuation_history", {}), ensure_ascii=False, indent=2)}

请输出 PE/PB/PEG 估值判断 + 历史分位 + 估值合理性。"""

    def parse_output(self, raw: str) -> Dict:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"raw": raw}


# ============ 5. bull_agent（多头论证） ============
class BullAgent(BaseAgent):
    name = "bull_agent"
    weight = 0.15

    def system_prompt(self) -> str:
        return load_prompt("bull_agent")

    def build_user_prompt(self, input_data: Dict) -> str:
        return f"""请基于事实卡，从多头角度论证。

【事实卡】
{json.dumps(input_data.get("fact_card", {}), ensure_ascii=False, indent=2)}

【行业分析】
{json.dumps(input_data.get("industry_result", {}), ensure_ascii=False, indent=2)}

【公司分析】
{json.dumps(input_data.get("company_result", {}), ensure_ascii=False, indent=2)}

【估值分析】
{json.dumps(input_data.get("valuation_result", {}), ensure_ascii=False, indent=2)}

⚠️ 你看不到空头观点。请独立思考，只输出多方 Claim 和证据。"""

    def parse_output(self, raw: str) -> Dict:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"raw": raw}


# ============ 6. bear_agent（空头论证） ============
class BearAgent(BaseAgent):
    name = "bear_agent"
    weight = 0.15

    def system_prompt(self) -> str:
        return load_prompt("bear_agent")

    def build_user_prompt(self, input_data: Dict) -> str:
        return f"""请基于事实卡，从空头角度论证。

【事实卡】
{json.dumps(input_data.get("fact_card", {}), ensure_ascii=False, indent=2)}

【行业分析】
{json.dumps(input_data.get("industry_result", {}), ensure_ascii=False, indent=2)}

【公司分析】
{json.dumps(input_data.get("company_result", {}), ensure_ascii=False, indent=2)}

【估值分析】
{json.dumps(input_data.get("valuation_result", {}), ensure_ascii=False, indent=2)}

⚠️ 你看不到多头观点。请独立思考，只输出空方 Claim 和证据。"""

    def parse_output(self, raw: str) -> Dict:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"raw": raw}


# ============ 7. sentiment_agent（游资情绪） ============
class SentimentAgent(BaseAgent):
    name = "sentiment_agent"
    weight = 0.10

    def system_prompt(self) -> str:
        return load_prompt("sentiment_agent")

    def build_user_prompt(self, input_data: Dict) -> str:
        return f"""请评估以下标的/板块的市场情绪和资金动向。

【目标概念】
{input_data.get("concept", "未知")}

【市场状态】
{json.dumps(input_data.get("market_data", {}), ensure_ascii=False, indent=2)}

【板块状态】
{json.dumps(input_data.get("sector_data", {}), ensure_ascii=False, indent=2)}

请输出情绪周期（启动/高潮/退潮/冰点）+ 资金动向 + 梯队评分。"""

    def parse_output(self, raw: str) -> Dict:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"raw": raw}


# ============ 8. risk_agent（风控） ============
class RiskAgent(BaseAgent):
    name = "risk_agent"
    weight = 0.15

    def system_prompt(self) -> str:
        return load_prompt("risk_agent")

    def build_user_prompt(self, input_data: Dict) -> str:
        return f"""请基于所有 Agent 输出做风控评估。

【所有前序 Agent 输出】
{json.dumps(input_data.get("all_agents", {}), ensure_ascii=False, indent=2)}

【当前组合】
{json.dumps(input_data.get("portfolio", {}), ensure_ascii=False, indent=2)}

请输出 Kelly 仓位 + 集中度检查 + 风险点。"""

    def parse_output(self, raw: str) -> Dict:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"raw": raw}


# ============ 9. arbiter_agent（仲裁） ============
class ArbiterAgent(BaseAgent):
    name = "arbiter_agent"
    weight = 0.0  # 仲裁不算权重

    def system_prompt(self) -> str:
        return load_prompt("arbiter_agent")

    def build_user_prompt(self, input_data: Dict) -> str:
        return f"""请基于所有 Agent 输出做最终决策。

【所有 Agent 输出】
{json.dumps(input_data.get("all_agents", {}), ensure_ascii=False, indent=2)}

【风控结果】
{json.dumps(input_data.get("risk_result", {}), ensure_ascii=False, indent=2)}

请输出最终决策：
- direction (bullish/bearish/neutral)
- confidence (0-1)
- entry_price
- target_price
- stop_loss
- position_pct
- key_catalysts
- key_risks
- reasoning"""

    def parse_output(self, raw: str) -> Dict:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"raw": raw}


# ============ 所有 Agent 注册表 ============
ALL_AGENTS = {
    "intel": IntelAgent,
    "industry": IndustryAgent,
    "company": CompanyAgent,
    "valuation": ValuationAgent,
    "bull": BullAgent,
    "bear": BearAgent,
    "sentiment": SentimentAgent,
    "risk": RiskAgent,
    "arbiter": ArbiterAgent,
}


if __name__ == "__main__":
    print("Agents 注册：")
    for name, cls in ALL_AGENTS.items():
        print(f"  {name:12s} {cls.name:18s} weight={cls.weight}")
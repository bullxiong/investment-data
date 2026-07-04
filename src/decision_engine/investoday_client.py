"""
investoday_client.py - 今日投资 API Python 客户端

直接调用 investoday-api CLI（已装 @investoday/investoday-api）。

用法：
    from investoday_client import InvestodayClient
    c = InvestodayClient()
    data = c.financial_derivatives("300750")
"""

from __future__ import annotations
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class InvestodayClient:
    """今日投资 API 客户端

    已默认 5 步财务分析框架所需接口：
    - get_basic_info: Step 1 公司画像
    - get_financial_derivatives: Step 2-5 盈利/利润质量/成长/安全
    """

    def __init__(self, cli_path: str = "investoday-api"):
        self.cli = cli_path

    def _call(self, endpoint: str, params: Optional[Dict] = None,
              method: str = "GET", body: Optional[Dict] = None,
              timeout: int = 30) -> Any:
        """调用 investoday API"""
        cmd = [self.cli, endpoint]
        if params:
            for k, v in params.items():
                cmd.append(f"{k}={v}")
        if method.upper() == "POST":
            cmd.append("--method")
            cmd.append("POST")
        if body:
            cmd.append("--body-json")
            cmd.append(json.dumps(body, ensure_ascii=False))

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                check=False
            )
            if result.returncode != 0:
                return {"error": result.stderr.strip()[:200]}
            stdout = result.stdout.strip()
            if not stdout:
                return {"error": "empty response"}
            return json.loads(stdout)
        except subprocess.TimeoutExpired:
            return {"error": "timeout"}
        except json.JSONDecodeError as e:
            return {"error": f"json decode: {str(e)[:100]}"}
        except Exception as e:
            return {"error": f"exception: {str(e)[:100]}"}

    # ============ Step 1: 公司画像 ============
    def get_basic_info(self, stock_code: str) -> Dict:
        """获取股票基本信息（Step 1 公司画像）"""
        return self._call("stock/basic-info", {"stockCode": stock_code})

    # ============ Step 2-5: 财务核心 ============
    def get_financial_derivatives(self, stock_code: str,
                                   begin_date: str = "2025-01-01") -> List[Dict]:
        """财务衍生指标（Step 2-5 全部）

        包含：毛利率、净利率、ROE、营收增速、利息保障倍数、负债率、流动比率...
        """
        result = self._call(
            "stock/fin-der-inds",
            method="POST",
            body={
                "stockCode": stock_code,
                "beginDate": begin_date,
            }
        )
        if isinstance(result, list):
            return result
        return []

    def get_financial_strength(self, stock_code: str) -> Dict:
        """财务实力指标（ROE/ROA 排行 + 财务安全）"""
        return self._call(
            "stock/finance/financial-strength",
            {"stockCode": stock_code}
        )

    def get_growth_ability(self, stock_code: str) -> Dict:
        """成长能力指标"""
        return self._call(
            "stock/finance/growth-ability",
            {"stockCode": stock_code}
        )

    def get_profit_ability(self, stock_code: str) -> Dict:
        """盈利能力指标（不同口径）"""
        return self._call(
            "stock/finance/profit-ability",
            {"stockCode": stock_code}
        )

    # ============ 三大报表 ============
    def get_income_statements(self, stock_code: str, page_num: int = 1,
                               page_size: int = 4) -> List[Dict]:
        """利润表（最近 4 期）"""
        result = self._call(
            "stock/income-statements",
            {"stockCode": stock_code, "pageNum": page_num, "pageSize": page_size}
        )
        if isinstance(result, list):
            return result
        return []

    def get_balance_sheets(self, stock_code: str, page_num: int = 1,
                           page_size: int = 4) -> List[Dict]:
        """资产负债表（最近 4 期）"""
        result = self._call(
            "stock/balance-sheets",
            {"stockCode": stock_code, "pageNum": page_num, "pageSize": page_size}
        )
        if isinstance(result, list):
            return result
        return []

    def get_cash_flows(self, stock_code: str, page_num: int = 1,
                        page_size: int = 4) -> List[Dict]:
        """现金流量表（最近 4 期）"""
        result = self._call(
            "stock/cash-flows",
            {"stockCode": stock_code, "pageNum": page_num, "pageSize": page_size}
        )
        if isinstance(result, list):
            return result
        return []

    # ============ 5 步框架汇总 ============
    def build_financial_report(self, stock_code: str) -> Dict:
        """按 investoday 5 步框架整理财务数据

        Step 1: 公司画像
        Step 2: 赚钱能力
        Step 3: 利润质量
        Step 4: 成长持续性
        Step 5: 财务安全性
        """
        basic = self.get_basic_info(stock_code)
        # basic 是 list（多个 record）
        if isinstance(basic, list) and basic:
            basic = basic[0]

        derivatives = self.get_financial_derivatives(stock_code)
        # 取最新一期
        latest = derivatives[0] if derivatives else {}

        # 关键指标按 5 步框架分类
        report = {
            "stock_code": stock_code,
            "company_image": {
                "name": basic.get("stockName") or basic.get("name"),
                "industry": basic.get("boardName"),
                "listing_date": basic.get("listDate"),
                "main_business": (basic.get("mainBusiness") or "")[:200],
                "report_date": basic.get("reportDate"),
            },
            "profitability": {
                "gross_margin": latest.get("grossMargin"),
                "operating_profit_margin": latest.get("operatingProfitMarginPct"),
                "net_margin": latest.get("netMargin"),
                "roe_diluted": latest.get("roeDiluted"),
                "roa": latest.get("roa"),
                "roic": latest.get("roic"),
            },
            "profit_quality": {
                "operating_cash_flow": latest.get("netOperatingCashFlow"),
                "cfo_per_share": latest.get("cfoPs"),
                "cfo_to_net_profit": latest.get("ocfToNetProfitRatio"),
                "cash_to_revenue": latest.get("cashReceivedSalesToRevenue"),
                "non_recurring_ratio": latest.get("nonRecurringPnlRatio"),
            },
            "growth": {
                "eps_growth_1y": latest.get("epsGrowth1y"),
                "revenue_growth_1y": latest.get("revGrowth1y"),
                "revenue_growth_3y": latest.get("revenueGrowth3yPct"),
                "net_profit_growth_1y": latest.get("npGrowth1y"),
                "operating_profit_growth_1y": latest.get("opProfitGrowth1y"),
            },
            "safety": {
                "debt_asset_ratio": latest.get("debtAssetRatioPct"),
                "current_ratio": latest.get("currentRatio"),
                "quick_ratio": latest.get("quickRatio"),
                "ebit_interest_coverage": latest.get("ebitInterestCoverage"),
                "cash_debt_ratio": latest.get("cashDebtRatioPct"),
                "equity_ratio": latest.get("equityRatioPct"),
                "f_score": latest.get("fScore"),
                "z_score": latest.get("zScore"),
            },
            "data_ok": bool(basic and latest),
            "data_period": latest.get("reportPeriodEnd"),
        }

        # 应用 investoday 11 条策略逻辑汇总
        rules = self._apply_strategy_rules(report)
        report["strategy_signals"] = rules

        return report

    def _apply_strategy_rules(self, r: Dict) -> List[Dict]:
        """按 investoday 的 11 条策略逻辑给出信号"""
        signals = []
        prof = r.get("profitability", {})
        qual = r.get("profit_quality", {})
        grow = r.get("growth", {})
        safe = r.get("safety", {})

        # ROE > 15% + 净利率 > 10% → 积极
        roe = prof.get("roe_diluted") or 0
        net_margin = prof.get("net_margin") or 0
        if roe > 15 and net_margin > 10:
            signals.append({"signal": "盈利能力较强", "rule": "ROE+净利率", "strength": "积极"})

        # 毛利率同比提升
        gross = prof.get("gross_margin") or 0
        if gross > 20:
            signals.append({"signal": f"毛利率 {gross:.1f}% 良好", "rule": "毛利率", "strength": "积极"})

        # 现金流匹配
        cfo_npr = qual.get("cfo_to_net_profit") or 0
        if cfo_npr > 1:
            signals.append({"signal": f"经营现金流/净利润 = {cfo_npr:.2f} > 1", "rule": "现金流", "strength": "积极"})
        elif cfo_npr < 0.7:
            signals.append({"signal": f"经营现金流/净利润 = {cfo_npr:.2f} < 0.7 警惕", "rule": "现金流", "strength": "⚠️"})

        # 营收增速 > 20% + 净利润增速 > 20% → 高成长
        rev_g = (grow.get("revenue_growth_1y") or 0) * 100
        np_g = (grow.get("net_profit_growth_1y") or 0) * 100
        if rev_g > 20 and np_g > 20:
            signals.append({"signal": f"营收 +{rev_g:.1f}% / 净利 +{np_g:.1f}% 高成长", "rule": "双增长", "strength": "积极"})
        elif (rev_g > 0) and (np_g < 0):
            signals.append({"signal": f"营收 +{rev_g:.1f}% 但净利下滑", "rule": "增长质量", "strength": "🟡"})

        # 资产负债率 > 70% → 警惕
        debt = safe.get("debt_asset_ratio") or 0
        if debt > 70:
            signals.append({"signal": f"资产负债率 {debt:.1f}% 偏高", "rule": "杠杆", "strength": "⚠️"})
        elif debt < 50:
            signals.append({"signal": f"资产负债率 {debt:.1f}% 健康", "rule": "杠杆", "strength": "✅"})

        # 利息保障 < 2 → 高风险
        ic = safe.get("ebit_interest_coverage") or 0
        if ic < 2:
            signals.append({"signal": f"EBIT 利息保障 {ic:.1f} < 2 高风险", "rule": "偿债", "strength": "🔴"})
        elif ic > 5:
            signals.append({"signal": f"EBIT 利息保障 {ic:.1f} 强", "rule": "偿债", "strength": "✅"})

        return signals


# ============ 测试 ============
if __name__ == "__main__":
    c = InvestodayClient()

    print("=== 宁德时代 300750 完整财务报告 ===\n")
    report = c.build_financial_report("300750")

    print(json.dumps(report, ensure_ascii=False, indent=2))
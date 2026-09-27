"""
历史回测引擎
用历史数据验证涨停连板策略的有效性
"""

from dataclasses import dataclass, field
from datetime import datetime

import akshare as ak
from loguru import logger

from src.collectors.em_client import get_em_client


@dataclass
class BacktestTrade:
    """单笔交易记录"""
    code: str
    name: str
    buy_date: str
    buy_price: float
    sell_date: str = ""
    sell_price: float = 0.0
    pnl_pct: float = 0.0
    pnl_amount: float = 0.0
    hold_days: int = 0
    reason: str = ""


@dataclass
class BacktestResult:
    """回测结果"""
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return_pct: float = 0.0
    annual_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_pnl_pct: float = 0.0
    avg_hold_days: float = 0.0
    sharpe_ratio: float = 0.0
    trades: list = field(default_factory=list)

    def summary(self) -> str:
        """生成回测报告"""
        return f"""
========== 回测报告 ==========
回测区间: {self.start_date} ~ {self.end_date}
初始资金: {self.initial_capital:,.0f}
最终资金: {self.final_capital:,.0f}
总收益率: {self.total_return_pct:.2f}%
年化收益: {self.annual_return_pct:.2f}%
最大回撤: {self.max_drawdown_pct:.2f}%
胜率: {self.win_rate:.1f}%
总交易次数: {self.total_trades}
盈利次数: {self.winning_trades}
亏损次数: {self.losing_trades}
平均收益: {self.avg_pnl_pct:.2f}%
平均持仓天数: {self.avg_hold_days:.1f}
================================
"""


class BacktestEngine:
    """回测引擎"""

    def __init__(
        self,
        initial_capital: float = 1_000_000,
        max_position_pct: float = 0.20,
        stop_loss_pct: float = -0.05,
        take_profit_pct: float = 0.15,
        max_hold_days: int = 5,
    ):
        self.initial_capital = initial_capital
        self.max_position_pct = max_position_pct
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.max_hold_days = max_hold_days

    def run(
        self,
        start_date: str,
        end_date: str,
        top_n: int = 5,
    ) -> BacktestResult:
        """
        运行回测

        策略逻辑：
        1. 每日获取涨停板数据
        2. 按连板天数排序，取 top_n 只
        3. 次日开盘买入
        4. 满足止损/止盈/最大持仓天数条件时卖出

        Args:
            start_date: 回测开始日期 YYYY-MM-DD
            end_date: 回测结束日期 YYYY-MM-DD
            top_n: 每日选取的股票数

        Returns:
            回测结果
        """
        logger.info(f"开始回测: {start_date} ~ {end_date}, top_n={top_n}")

        capital = self.initial_capital
        trades: list[BacktestTrade] = []
        holdings: dict[str, BacktestTrade] = {}  # code -> trade
        capital_history = [capital]

        # 获取交易日列表
        trade_dates = self._get_trade_dates(start_date, end_date)
        if not trade_dates:
            logger.warning("未获取到交易日数据")
            return BacktestResult(
                start_date=start_date, end_date=end_date,
                initial_capital=self.initial_capital, final_capital=capital,
            )

        logger.info(f"交易日数量: {len(trade_dates)}")

        for i, td in enumerate(trade_dates):
            # 1. 检查持仓是否需要卖出
            to_sell = []
            for code, trade in holdings.items():
                current_price = self._get_price(code, td, "close")
                if current_price is None:
                    continue

                trade.hold_days += 1
                pnl_pct = (current_price - trade.buy_price) / trade.buy_price

                should_sell = False
                reason = ""

                if pnl_pct <= self.stop_loss_pct:
                    should_sell = True
                    reason = "止损"
                elif pnl_pct >= self.take_profit_pct:
                    should_sell = True
                    reason = "止盈"
                elif trade.hold_days >= self.max_hold_days:
                    should_sell = True
                    reason = "持仓到期"

                if should_sell:
                    trade.sell_date = td
                    trade.sell_price = current_price
                    trade.pnl_pct = pnl_pct * 100
                    trade.pnl_amount = (current_price - trade.buy_price) * (
                        self.initial_capital * self.max_position_pct / trade.buy_price
                    )
                    trade.reason = reason
                    capital += trade.pnl_amount
                    to_sell.append(code)
                    trades.append(trade)

            for code in to_sell:
                del holdings[code]

            # 2. 选股并买入（使用前一日涨停数据）
            if i > 0:
                prev_date = trade_dates[i - 1]
                limit_up_stocks = self._get_limit_up_stocks(prev_date)

                if limit_up_stocks:
                    # 过滤已持有的
                    candidates = [s for s in limit_up_stocks if s["code"] not in holdings]
                    # 取 top_n
                    candidates = candidates[:top_n]

                    for stock in candidates:
                        if len(holdings) >= top_n:
                            break

                        open_price = self._get_price(stock["code"], td, "open")
                        if open_price is None or open_price <= 0:
                            continue

                        # 检查是否一字涨停（无法买入）
                        high = self._get_price(stock["code"], td, "high")
                        low = self._get_price(stock["code"], td, "low")
                        if high and low and high == low:
                            continue  # 一字板买不到

                        trade = BacktestTrade(
                            code=stock["code"],
                            name=stock.get("name", ""),
                            buy_date=td,
                            buy_price=open_price,
                        )
                        holdings[trade.code] = trade

            capital_history.append(capital)

        # 强制平仓剩余持仓
        for code, trade in holdings.items():
            price = self._get_price(code, trade_dates[-1], "close") if trade_dates else trade.buy_price
            if price:
                trade.sell_date = trade_dates[-1] if trade_dates else end_date
                trade.sell_price = price
                trade.pnl_pct = ((price - trade.buy_price) / trade.buy_price) * 100
                trade.reason = "回测结束平仓"
                trades.append(trade)

        # 计算回测指标
        result = self._calculate_metrics(
            start_date, end_date, trades, capital, capital_history,
        )
        logger.info(result.summary())
        return result

    def _get_trade_dates(self, start_date: str, end_date: str) -> list[str]:
        """获取交易日列表"""
        try:
            df = ak.tool_trade_date_hist_sina()
            if df is not None and not df.empty:
                col = df.columns[0]
                dates = df[col].astype(str).tolist()
                return [d for d in dates if start_date <= d <= end_date]
        except Exception as e:
            logger.error(f"获取交易日列表失败: {e}")
        return []

    def _get_limit_up_stocks(self, trade_date: str) -> list[dict]:
        """获取指定日期的涨停股"""
        try:
            df = get_em_client().stock_zt_pool_em(date=trade_date.replace("-", ""))
            if df is not None and not df.empty:
                stocks = []
                for _, row in df.iterrows():
                    stocks.append({
                        "code": str(row.get("代码", "")),
                        "name": str(row.get("名称", "")),
                        "continuous_days": int(row.get("连板数", 1) or 1),
                    })
                # 按连板数降序
                stocks.sort(key=lambda x: x["continuous_days"], reverse=True)
                return stocks
        except Exception as e:
            logger.debug(f"获取涨停池失败 [{trade_date}]: {e}")
        return []

    def _get_price(self, code: str, trade_date: str, price_type: str = "close") -> float | None:
        """获取个股指定日期的价格"""
        try:
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=trade_date.replace("-", ""),
                end_date=trade_date.replace("-", ""),
                adjust="qfq",
            )
            if df is not None and not df.empty:
                col_map = {"open": "开盘", "close": "收盘", "high": "最高", "low": "最低"}
                col = col_map.get(price_type, "收盘")
                return float(df.iloc[0][col])
        except Exception:
            pass
        return None

    def _calculate_metrics(
        self,
        start_date: str,
        end_date: str,
        trades: list[BacktestTrade],
        final_capital: float,
        capital_history: list[float],
    ) -> BacktestResult:
        """计算回测指标"""
        total_trades = len(trades)
        winning = [t for t in trades if t.pnl_pct > 0]
        losing = [t for t in trades if t.pnl_pct <= 0]

        total_return = (final_capital - self.initial_capital) / self.initial_capital * 100

        # 年化收益
        try:
            days = (datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")).days
            annual_return = total_return * 365 / max(days, 1)
        except Exception:
            annual_return = 0

        # 最大回撤
        max_drawdown = 0
        peak = capital_history[0]
        for val in capital_history:
            peak = max(peak, val)
            dd = (peak - val) / peak * 100
            max_drawdown = max(max_drawdown, dd)

        return BacktestResult(
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_capital=final_capital,
            total_return_pct=round(total_return, 2),
            annual_return_pct=round(annual_return, 2),
            max_drawdown_pct=round(max_drawdown, 2),
            win_rate=round(len(winning) / total_trades * 100, 1) if total_trades > 0 else 0,
            total_trades=total_trades,
            winning_trades=len(winning),
            losing_trades=len(losing),
            avg_pnl_pct=round(sum(t.pnl_pct for t in trades) / total_trades, 2) if total_trades > 0 else 0,
            avg_hold_days=round(sum(t.hold_days for t in trades) / total_trades, 1) if total_trades > 0 else 0,
            trades=trades,
        )

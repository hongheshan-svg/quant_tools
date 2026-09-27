"""
风险控制模块
负责仓位管理、止损止盈、黑名单过滤、大盘熔断等
"""

from datetime import date, datetime

from loguru import logger

from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import StockDaily, TradeSignal

RAW_CODE_LEN = 6
EXCHANGE_CODE_LEN = 8
EXCHANGE_PREFIX_LEN = 2

HIGH_RISK_REASON_COUNT = 2

WEEKEND_START = 5
MORNING_SESSION_START = 925
MORNING_SESSION_END = 1130
AFTERNOON_SESSION_START = 1300
AFTERNOON_SESSION_END = 1500


class RiskManager:
    """风险控制管理器"""

    def __init__(self, config: dict = None):
        self.config = config or load_config()
        risk_cfg = self.config.get("risk", {})

        self.max_position_pct = risk_cfg.get("max_position_pct", 0.20)
        self.max_daily_buy_pct = risk_cfg.get("max_daily_buy_pct", 0.50)
        self.stop_loss_pct = risk_cfg.get("stop_loss_pct", -0.05)
        self.take_profit_pct = risk_cfg.get("take_profit_pct", 0.15)
        self.circuit_breaker_pct = risk_cfg.get("market_circuit_breaker_pct", -0.02)
        self.max_consecutive_losses = risk_cfg.get("max_consecutive_losses", 3)
        self.blacklist_keywords = risk_cfg.get("blacklist_keywords", ["ST", "*ST"])

        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")

    def filter_signals(self, signals: list[dict]) -> list[dict]:
        """
        对交易信号进行风控过滤

        Args:
            signals: 原始信号列表 [{"code": "xxx", "name": "xxx", ...}]

        Returns:
            过滤后的信号列表
        """
        filtered = []

        for signal in signals:
            code = signal.get("code", "")
            name = signal.get("name", "")

            # 1. 黑名单过滤
            if self._is_blacklisted(name):
                logger.debug(f"[风控] {name}({code}) 命中黑名单，过滤")
                continue

            # 2. 大盘熔断检查
            if self._is_market_circuit_breaker():
                logger.warning("[风控] 大盘触发熔断阈值，暂停所有买入信号")
                return []

            # 3. 连续亏损保护
            if self._is_consecutive_loss_exceeded():
                logger.warning("[风控] 连续止损次数超限，暂停交易")
                return []

            # 4. 价格和市值过滤
            if not self._check_stock_pool(code):
                logger.debug(f"[风控] {name}({code}) 不在股票池范围内，过滤")
                continue

            filtered.append(signal)

        logger.info(f"[风控] 信号过滤: {len(signals)} -> {len(filtered)}")
        return filtered

    def calculate_position_size(
        self,
        total_capital: float,
        current_positions: dict[str, float],
        stock_code: str,
        signal_strength: float = 0.5,
    ) -> float:
        """
        计算建仓金额

        Args:
            total_capital: 总资金
            current_positions: 当前持仓 {"code": 市值}
            stock_code: 待买入股票代码
            signal_strength: 信号强度 0-1

        Returns:
            建议买入金额
        """
        # 单只股票最大仓位
        max_single = total_capital * self.max_position_pct

        # 已持有该股的仓位
        current_holding = current_positions.get(stock_code, 0)
        remaining = max_single - current_holding
        if remaining <= 0:
            logger.debug(f"[风控] {stock_code} 仓位已满")
            return 0

        # 当日已买入总额限制
        today_bought = sum(
            v for k, v in current_positions.items()
            if k.startswith("today_")
        )
        daily_remaining = total_capital * self.max_daily_buy_pct - today_bought
        if daily_remaining <= 0:
            logger.debug("[风控] 当日买入额度已用完")
            return 0

        # 根据信号强度调整
        base_amount = min(remaining, daily_remaining)
        adjusted = base_amount * signal_strength

        return max(0, adjusted)

    def check_stop_loss(self, buy_price: float, current_price: float) -> bool:
        """
        检查是否触发止损

        Returns:
            True = 应该止损
        """
        if buy_price <= 0:
            return False
        pnl_pct = (current_price - buy_price) / buy_price
        return pnl_pct <= self.stop_loss_pct

    def check_take_profit(self, buy_price: float, current_price: float) -> bool:
        """
        检查是否触发止盈

        Returns:
            True = 应该止盈
        """
        if buy_price <= 0:
            return False
        pnl_pct = (current_price - buy_price) / buy_price
        return pnl_pct >= self.take_profit_pct

    def _is_blacklisted(self, stock_name: str) -> bool:
        """检查是否在黑名单中"""
        return any(kw in stock_name for kw in self.blacklist_keywords)

    def _is_market_circuit_breaker(self) -> bool:
        """检查大盘是否触发熔断阈值"""
        try:
            with get_db_session(self.db_path) as session:
                today = date.today().strftime("%Y-%m-%d")
                # 查询上证指数（代码 000001）
                sh_index = (
                    session.query(StockDaily)
                    .filter(
                        StockDaily.code == "000001",
                        StockDaily.trade_date == today,
                    )
                    .first()
                )
                if (
                    sh_index
                    and sh_index.change_pct is not None
                    and sh_index.change_pct / 100 <= self.circuit_breaker_pct
                ):
                    return True
        except Exception as e:
            logger.debug(f"大盘熔断检查失败: {e}")
        return False

    def _is_consecutive_loss_exceeded(self) -> bool:
        """检查是否连续止损次数超限"""
        # 简化实现：检查最近N个卖出信号是否都是止损
        try:
            with get_db_session(self.db_path) as session:
                recent_sells = (
                    session.query(TradeSignal)
                    .filter(TradeSignal.signal_type == "sell")
                    .order_by(TradeSignal.created_at.desc())
                    .limit(self.max_consecutive_losses)
                    .all()
                )
                if len(recent_sells) >= self.max_consecutive_losses:
                    return all(
                        "止损" in (s.reason or "") for s in recent_sells
                    )
        except Exception:
            pass
        return False

    def _check_stock_pool(self, stock_code: str) -> bool:
        """检查股票是否在合规股票池中"""
        try:
            pool_cfg = load_config("config/stock_pool.yaml")
            if not pool_cfg:
                return True  # 无配置则不过滤

            blacklist_codes = pool_cfg.get("blacklist", {}).get("codes", [])
            if stock_code in blacklist_codes:
                return False

            # 简单检查：如果有价格/市值过滤，通过数据库验证
            with get_db_session(self.db_path) as session:
                raw = (stock_code or "").strip().lower()
                cands = [raw]
                if len(raw) == RAW_CODE_LEN and raw.isdigit():
                    cands.extend([f"sh{raw}", f"sz{raw}", f"bj{raw}"])
                elif len(raw) == EXCHANGE_CODE_LEN and raw[:EXCHANGE_PREFIX_LEN] in {"sh", "sz", "bj"} and raw[EXCHANGE_PREFIX_LEN:].isdigit():
                    bare = raw[EXCHANGE_PREFIX_LEN:]
                    cands.extend([bare, f"sh{bare}", f"sz{bare}", f"bj{bare}"])
                cands = list(dict.fromkeys(cands))
                stock = (
                    session.query(StockDaily)
                    .filter(StockDaily.code.in_(cands))
                    .order_by(StockDaily.trade_date.desc())
                    .first()
                )
                if stock:
                    price_cfg = pool_cfg.get("price", {})
                    min_price = price_cfg.get("min", 0)
                    max_price = price_cfg.get("max", 99999)
                    if stock.close and (stock.close < min_price or stock.close > max_price):
                        return False

                    cap_cfg = pool_cfg.get("market_cap", {})
                    min_cap = cap_cfg.get("min", 0) * 100_000_000  # 亿 -> 元
                    max_cap = cap_cfg.get("max", 999999) * 100_000_000
                    if stock.circ_mv and (stock.circ_mv < min_cap or stock.circ_mv > max_cap):
                        return False

        except Exception as e:
            logger.debug(f"股票池检查失败 [{stock_code}]: {e}")

        return True

    def validate_order_intent(self, intent: dict) -> dict:
        """
        交易执行前校验。
        输入示例:
            {
              "code": "000001",
              "name": "平安银行",
              "side": "buy",
              "price": 10.5,
              "quantity": 1000
            }
        """
        code = str(intent.get("code") or "").strip()
        name = str(intent.get("name") or "").strip()
        side = str(intent.get("side") or "").lower().strip()
        price = float(intent.get("price") or 0)
        quantity = int(intent.get("quantity") or 0)

        reasons: list[str] = []

        if not code:
            reasons.append("code empty")
        if side not in ("buy", "sell"):
            reasons.append("invalid side")
        if price <= 0:
            reasons.append("price must > 0")
        if quantity <= 0:
            reasons.append("quantity must > 0")
        if quantity % 100 != 0:
            reasons.append("quantity must be 100-lot")
        if name and self._is_blacklisted(name):
            reasons.append("stock in blacklist")
        if code and not self._check_stock_pool(code):
            reasons.append("stock not in stock_pool")

        if side == "buy" and self._is_market_circuit_breaker():
            reasons.append("market circuit breaker")
        if side == "buy" and self._is_consecutive_loss_exceeded():
            reasons.append("consecutive losses exceeded")

        strict_session = bool(self.config.get("trading", {}).get("strict_session_check", False))
        if strict_session and not self._is_trade_session_now():
            reasons.append("out of trade session")

        passed = len(reasons) == 0
        risk_level = "low" if passed else ("high" if len(reasons) >= HIGH_RISK_REASON_COUNT else "medium")
        return {
            "passed": passed,
            "risk_level": risk_level,
            "reasons": reasons,
        }

    @staticmethod
    def _is_trade_session_now(now: datetime | None = None) -> bool:
        dt = now or datetime.now()
        if dt.weekday() >= WEEKEND_START:
            return False
        hhmm = dt.hour * 100 + dt.minute
        return (MORNING_SESSION_START <= hhmm <= MORNING_SESSION_END) or (
            AFTERNOON_SESSION_START <= hhmm <= AFTERNOON_SESSION_END
        )

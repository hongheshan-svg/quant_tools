"""
综合评分引擎
整合舆情、涨停、资金、技术面、国际因子等多维因子
输出 Top N 选股结果和交易信号
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from loguru import logger

from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import LimitUpStock, StockDaily, StockScore, TradeSignal
from src.strategy.capital_score import calculate_capital_score
from src.strategy.global_score import calculate_global_score
from src.strategy.limit_up_score import calculate_limit_up_score
from src.strategy.sentiment_score import calculate_sentiment_score
from src.strategy.tech_score import calculate_tech_score

TIME_RAW_WITH_SECONDS_LEN = 6
TIME_RAW_WITH_MINUTES_LEN = 4

YIZI_PRICE_TOLERANCE = 0.01
YIZI_LIMIT_UP_PCT = 9.9

STRONG_BUY_TOP_PCT = 0.05
STRONG_BUY_SCORE = 75
BUY_TOP_PCT = 0.15
BUY_SCORE = 60
HOLD_TOP_PCT = 0.50
HOLD_SCORE = 45

EXCHANGE_CODE_LEN = 8
EXCHANGE_PREFIX_LEN = 2
NEUTRAL_BASE_SCORE = 50


def _normalize_time(raw: str) -> str:
    """
    将涨停时间统一为 'HH:MM:SS' 格式，兼容 '092500' / '09:25:00' / '09:25' 等。
    """
    raw = raw.strip().replace(":", "")
    if len(raw) >= TIME_RAW_WITH_SECONDS_LEN:
        return f"{raw[:2]}:{raw[2:4]}:{raw[4:6]}"
    if len(raw) >= TIME_RAW_WITH_MINUTES_LEN:
        return f"{raw[:2]}:{raw[2:4]}:00"
    return raw


def _is_yizi_ban(limit_up: LimitUpStock, daily: StockDaily | None = None) -> bool:
    """
    判定是否为一字板（开盘即涨停封死，无法买入的股票）。
    判定条件（满足任一即为一字板）：
      1) open_count==0 且 首次涨停时间 <= 09:35（开盘5分钟内封板且全天未打开）
      2) 日线 open==high 且涨幅 >= 9.9%（开盘价就是最高价=涨停价）
    """
    open_count = limit_up.open_count or 0

    # 方法1：涨停池特征
    if open_count == 0:
        raw_time = (limit_up.first_limit_time or "").strip()
        if raw_time:
            normalized = _normalize_time(raw_time)
            # 09:35:00 以内封板且全天未打开 → 一字板
            if normalized <= "09:35:00":
                return True

    # 方法2：日线数据交叉验证
    return (
        daily
        and daily.open
        and daily.high
        and daily.change_pct
        and abs(daily.open - daily.high) < YIZI_PRICE_TOLERANCE
        and daily.change_pct >= YIZI_LIMIT_UP_PCT
    )


class CompositeScorer:
    """综合评分引擎"""

    def __init__(self, config: dict = None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        strategy_cfg = self.config.get("strategy", {})
        self.base_weights = strategy_cfg.get("weights", {})
        self.adaptive_weights = strategy_cfg.get("adaptive_weights", {})
        self.learning_cfg = strategy_cfg.get("learning", {})
        self.top_n = strategy_cfg.get("top_n", 10)
        self.score_workers = int(strategy_cfg.get("score_workers", 6))

        self.weights = self._resolve_effective_weights()
        self.w_sentiment = self.weights.get("sentiment_score", 0.25)
        self.w_limit_up = self.weights.get("limit_up_score", 0.15)
        self.w_seal = self.weights.get("seal_strength", 0.15)
        self.w_sector = self.weights.get("sector_effect", 0.12)
        self.w_capital = self.weights.get("capital_flow", 0.10)
        self.w_tech = self.weights.get("technical", 0.08)
        self.w_emotion = self.weights.get("market_emotion", 0.05)
        self.w_global = self.weights.get("global_score", 0.10)

    def _resolve_effective_weights(self) -> dict[str, float]:
        """
        生成最终生效权重：
        - 默认使用 strategy.weights
        - 若开启自学习并存在 adaptive_weights，则按 blend_ratio 融合
        """
        defaults = {
            "sentiment_score": 0.25,
            "limit_up_score": 0.15,
            "seal_strength": 0.15,
            "sector_effect": 0.12,
            "capital_flow": 0.10,
            "technical": 0.08,
            "market_emotion": 0.05,
            "global_score": 0.10,
        }
        base = {k: float(self.base_weights.get(k, v)) for k, v in defaults.items()}

        learning_enabled = bool(self.learning_cfg.get("enabled", True))
        blend_ratio = float(self.learning_cfg.get("blend_ratio", 0.35))
        blend_ratio = max(0.0, min(1.0, blend_ratio))

        if learning_enabled and self.adaptive_weights:
            merged = {}
            for key, base_val in base.items():
                adaptive_val = float(self.adaptive_weights.get(key, base_val))
                merged[key] = base_val * (1.0 - blend_ratio) + adaptive_val * blend_ratio
            total = sum(merged.values()) or 1.0
            merged = {k: v / total for k, v in merged.items()}
            logger.info(f"评分权重(自学习融合, blend={blend_ratio:.2f}): {merged}")
            return merged

        total = sum(base.values()) or 1.0
        return {k: v / total for k, v in base.items()}

    def score_today(self) -> list[dict]:
        """
        对今日涨停股进行综合评分，返回 Top N 列表

        Returns:
            排序后的评分列表
        """
        today = date.today().strftime("%Y-%m-%d")
        logger.info(f"开始综合评分: {today}")

        # 1. 获取今日涨停股候选池
        candidates = self._get_candidates(today)
        if not candidates:
            logger.info("无涨停股候选")
            return []

        # 2. 并行逐只评分
        scored = []
        candidate_map = {c["code"]: c for c in candidates}
        with ThreadPoolExecutor(max_workers=self.score_workers, thread_name_prefix="score") as executor:
            future_map = {
                executor.submit(self._score_stock, c["code"]): c["code"]
                for c in candidates
            }
            for future in as_completed(future_map):
                code = future_map[future]
                try:
                    scores = future.result()
                    scores["code"] = code
                    scores["name"] = candidate_map[code].get("name", "")
                    scored.append(scores)
                except Exception as e:
                    logger.error(f"评分失败 [{code}]: {e}")

        # 3. 按综合分排序
        scored.sort(key=lambda x: x["composite_score"], reverse=True)

        # 4. 设置排名 + 基于相对排名的推荐等级
        total = len(scored)
        for rank, item in enumerate(scored, 1):
            item["rank"] = rank
            # 使用相对排名 + 绝对分数综合判定
            pct = rank / total if total > 0 else 1
            cs = item["composite_score"]
            if pct <= STRONG_BUY_TOP_PCT or cs >= STRONG_BUY_SCORE:
                item["recommendation"] = "strong_buy"
            elif pct <= BUY_TOP_PCT or cs >= BUY_SCORE:
                item["recommendation"] = "buy"
            elif pct <= HOLD_TOP_PCT or cs >= HOLD_SCORE:
                item["recommendation"] = "hold"
            else:
                item["recommendation"] = "avoid"

        # 5. 保存到数据库
        self._save_scores(scored, today)

        # 6. 取 Top N
        top = scored[:self.top_n]
        logger.info(f"综合评分完成: 共 {len(scored)} 只, Top {len(top)} 已生成")

        for item in top:
            logger.info(
                f"  #{item['rank']} {item['name']}({item['code']}) "
                f"综合: {item['composite_score']:.1f} | "
                f"舆情: {item['sentiment_score']:.0f} | "
                f"涨停: {item['limit_up_score']:.0f} | "
                f"资金: {item['capital_score']:.0f} | "
                f"技术: {item['tech_score']:.0f} | "
                f"国际: {item['global_score']:.0f}"
            )

        return top

    def _get_candidates(self, trade_date: str) -> list[dict]:
        """
        获取候选股票池（今日涨停股）。
        自动剔除：
          1) ST / *ST 黑名单
          2) 一字板（开盘即涨停，全天无交易机会，无法买入）
        一字板判定：open_count==0 且 first_limit_time<="09:30"
                    或 StockDaily.open == StockDaily.high 且涨幅>=9.9%
        """
        candidates = []
        yizi_count = 0
        try:
            with get_db_session(self.db_path) as session:
                records = (
                    session.query(LimitUpStock)
                    .filter(LimitUpStock.trade_date == trade_date)
                    .all()
                )
                blacklist = self.config.get("risk", {}).get("blacklist_keywords", ["ST", "*ST"])

                # 预加载当日行情用于交叉验证一字板
                daily_map: dict[str, StockDaily] = {}
                daily_rows = (
                    session.query(StockDaily)
                    .filter(StockDaily.trade_date == trade_date)
                    .all()
                )
                for d in daily_rows:
                    daily_map[d.code] = d
                    bare = self._bare_code(d.code)
                    if bare:
                        daily_map.setdefault(bare, d)

                for r in records:
                    name = r.name or ""
                    # 过滤黑名单
                    if any(kw in name for kw in blacklist):
                        continue

                    # ---- 一字板过滤 ----
                    daily = daily_map.get(r.code) or daily_map.get(self._bare_code(r.code))
                    is_yizi = _is_yizi_ban(r, daily)

                    if is_yizi:
                        yizi_count += 1
                        first_time = r.first_limit_time or ""
                        open_count = r.open_count or 0
                        logger.debug(f"剔除一字板: {name}({r.code}) 涨停时间={first_time} 炸板={open_count}")
                        continue

                    candidates.append({
                        "code": r.code,
                        "name": name,
                        "continuous_days": r.continuous_days,
                        "sector": r.sector,
                    })
                if yizi_count:
                    logger.info(f"一字板过滤: 剔除 {yizi_count} 只无法买入的一字板股票")
        except Exception as e:
            logger.error(f"获取候选股票失败: {e}")
        return candidates

    @staticmethod
    def _bare_code(code: str | None) -> str:
        raw = (code or "").strip().lower()
        if len(raw) == EXCHANGE_CODE_LEN and raw[:EXCHANGE_PREFIX_LEN] in {"sh", "sz", "bj"} and raw[EXCHANGE_PREFIX_LEN:].isdigit():
            return raw[EXCHANGE_PREFIX_LEN:]
        return raw

    def _score_stock(self, code: str) -> dict:
        """对单只股票进行多维评分"""
        db = self.db_path

        s_sentiment = calculate_sentiment_score(code, db)
        s_limit_up = calculate_limit_up_score(code, db)
        s_capital = calculate_capital_score(code, db)
        s_tech = calculate_tech_score(code, db)
        s_global = calculate_global_score(code, db)

        # 综合加权评分（涨停相关的 limit_up + seal + sector 合并到 limit_up_score 中）
        composite = (
            s_sentiment * self.w_sentiment +
            s_limit_up * (self.w_limit_up + self.w_seal + self.w_sector) +
            s_capital * self.w_capital +
            s_tech * self.w_tech +
            s_global * self.w_global +
            NEUTRAL_BASE_SCORE * self.w_emotion  # 市场情绪使用基准值，后续可扩展
        )

        # 推荐等级（在 score_today 中根据相对排名重新设定）
        recommendation = "hold"  # 默认值，后续按排名调整

        return {
            "sentiment_score": s_sentiment,
            "limit_up_score": s_limit_up,
            "capital_score": s_capital,
            "tech_score": s_tech,
            "global_score": s_global,
            "composite_score": round(composite, 2),
            "recommendation": recommendation,
        }

    def _save_scores(self, scored: list[dict], score_date: str):
        """保存评分结果到数据库"""
        try:
            with get_db_session(self.db_path) as session:
                existing_records = (
                    session.query(StockScore)
                    .filter(StockScore.score_date == score_date)
                    .all()
                )
                existing_map = {r.code: r for r in existing_records}
                for item in scored:
                    existing = existing_map.get(item["code"])

                    data = {
                        "name": item.get("name", ""),
                        "sentiment_score": item.get("sentiment_score", 0),
                        "limit_up_score": item.get("limit_up_score", 0),
                        "capital_flow_score": item.get("capital_score", 0),
                        "technical_score": item.get("tech_score", 0),
                        "global_score": item.get("global_score", 0),
                        "composite_score": item.get("composite_score", 0),
                        "rank": item.get("rank"),
                        "recommendation": item.get("recommendation"),
                    }

                    if existing:
                        for k, v in data.items():
                            setattr(existing, k, v)
                    else:
                        record = StockScore(code=item["code"], score_date=score_date, **data)
                        session.add(record)

            logger.info(f"评分结果已保存: {len(scored)} 条")
        except Exception as e:
            logger.error(f"评分结果保存失败: {e}")

    def generate_signals(self):
        """
        生成交易信号
        - Top N 中 strong_buy / buy 的生成买入信号
        - hold 也生成关注信号
        - avoid 不生成信号
        """
        today = date.today().strftime("%Y-%m-%d")
        logger.info("生成交易信号...")

        try:
            with get_db_session(self.db_path) as session:
                # 先清除今日旧信号
                session.query(TradeSignal).filter(
                    TradeSignal.signal_date == today
                ).delete()
                session.flush()

                # 获取 Top N 评分
                top_scores = (
                    session.query(StockScore)
                    .filter(StockScore.score_date == today)
                    .order_by(StockScore.composite_score.desc())
                    .limit(self.top_n)
                    .all()
                )

                codes = [s.code for s in top_scores]
                limit_up_records = (
                    session.query(LimitUpStock)
                    .filter(
                        LimitUpStock.trade_date == today,
                        LimitUpStock.code.in_(codes),
                    )
                    .all()
                )
                limit_map = {r.code: r for r in limit_up_records}

                signals = []
                for score in top_scores:
                    # 决定信号类型和强度
                    rec = score.recommendation
                    if rec == "strong_buy":
                        signal_type = "buy"
                        strength = min(1.0, score.composite_score / 80)
                    elif rec == "buy":
                        signal_type = "buy"
                        strength = min(0.9, score.composite_score / 90)
                    elif rec == "hold":
                        signal_type = "buy"
                        strength = min(0.7, score.composite_score / 100)
                    else:
                        continue  # avoid 不生成信号

                    # 构建涨停原因
                    limit_up = limit_map.get(score.code)
                    limit_info = ""
                    if limit_up:
                        if limit_up.continuous_days and limit_up.continuous_days > 1:
                            limit_info = f"{limit_up.continuous_days}连板"
                        else:
                            limit_info = "首板"
                        if limit_up.reason:
                            limit_info += f" ({limit_up.reason})"
                        if limit_up.sector:
                            limit_info += f" [{limit_up.sector}]"

                    reason_parts = [f"综合{score.composite_score:.1f}分"]
                    if limit_info:
                        reason_parts.append(limit_info)
                    if score.sentiment_score != NEUTRAL_BASE_SCORE:
                        reason_parts.append(f"舆情{score.sentiment_score:.0f}")
                    if score.global_score != NEUTRAL_BASE_SCORE:
                        reason_parts.append(f"国际{score.global_score:.0f}")

                    signal = TradeSignal(
                        code=score.code,
                        name=score.name,
                        signal_date=today,
                        signal_type=signal_type,
                        signal_strength=round(strength, 3),
                        composite_score=score.composite_score,
                        reason=" | ".join(reason_parts),
                    )
                    session.add(signal)
                    signals.append(signal)

                session.commit()

                # 在 session 内打印信号（避免 detached instance 错误）
                logger.info(f"交易信号已生成: {len(signals)} 个信号")
                signal_info = []
                for sig in signals:
                    info = {
                        "signal_type": sig.signal_type,
                        "name": sig.name,
                        "code": sig.code,
                        "signal_strength": sig.signal_strength,
                        "reason": sig.reason,
                    }
                    signal_info.append(info)
                    logger.info(
                        f"  {sig.signal_type.upper()} {sig.name}({sig.code}) "
                        f"强度{sig.signal_strength:.0%} | {sig.reason}"
                    )

            return signal_info

        except Exception as e:
            logger.error(f"交易信号生成失败: {e}")
            return []

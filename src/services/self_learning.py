"""
自学习服务：
1) 评估历史信号效果（涨跌幅/涨停命中）
2) 评估因子与次日收益相关性
3) 生成并落地自适应权重 + 来源置信度
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from typing import Any

from loguru import logger

from src.config_loader import load_config, save_config
from src.database.db import get_db_session, init_db
from src.database.models import (
    LearningSnapshot,
    LimitUpStock,
    SignalOutcome,
    StockDaily,
    StockScore,
    TradeSignal,
)

MIN_CORRELATION_SAMPLE_SIZE = 3
STOCK_CODE_LENGTH = 6
PREFIXED_STOCK_CODE_LENGTH = 8
PEARSON_EPSILON = 1e-12


class SelfLearningService:
    """系统自学习入口。"""

    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.config_path = self.config.get("_config_path", "config/settings.yaml")
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        self.strategy_cfg = self.config.get("strategy", {})
        self.learning_cfg = self.strategy_cfg.get("learning", {})
        init_db(self.db_path)

    def run_daily_learning(self, as_of_date: str | None = None) -> dict[str, Any]:
        """
        运行一次完整自学习闭环。
        as_of_date: 统计截止日（YYYY-MM-DD），默认今天。
        """
        if as_of_date is None:
            as_of_date = date.today().strftime("%Y-%m-%d")
        lookback_days = int(self.learning_cfg.get("lookback_days", 20))
        enabled = bool(self.learning_cfg.get("enabled", True))
        if not enabled:
            return {"status": "disabled", "date": as_of_date}

        logger.info(f"自学习启动: 截止={as_of_date}, 回看={lookback_days}天")

        outcomes = self._evaluate_signal_outcomes(as_of_date, lookback_days)
        factor_stats = self._compute_factor_stats(as_of_date, lookback_days)
        adaptive_weights = self._derive_adaptive_weights(factor_stats)
        source_confidence = self._derive_source_confidence(outcomes)

        self._persist_learning_config(adaptive_weights, source_confidence, len(outcomes))
        self._save_snapshot(
            as_of_date=as_of_date,
            lookback_days=lookback_days,
            sample_count=len(outcomes),
            adaptive_weights=adaptive_weights,
            source_confidence=source_confidence,
            factor_stats=factor_stats,
        )

        result = {
            "status": "ok",
            "date": as_of_date,
            "lookback_days": lookback_days,
            "signal_samples": len(outcomes),
            "adaptive_weights": adaptive_weights,
            "source_confidence": source_confidence,
        }
        logger.info(
            f"自学习完成: 样本={len(outcomes)} "
            f"| 权重={adaptive_weights} | 来源置信度={source_confidence}"
        )
        return result

    def _evaluate_signal_outcomes(self, as_of_date: str, lookback_days: int) -> list[dict[str, Any]]:
        """评估最近信号效果并写入 SignalOutcome。"""
        start_date = (datetime.strptime(as_of_date, "%Y-%m-%d") - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        rows: list[dict[str, Any]] = []

        with get_db_session(self.db_path) as session:
            signals = (
                session.query(TradeSignal)
                .filter(
                    TradeSignal.signal_date >= start_date,
                    TradeSignal.signal_date <= as_of_date,
                )
                .order_by(TradeSignal.signal_date.desc())
                .all()
            )

            for sig in signals:
                daily = self._find_stock_daily(session, sig.code, sig.signal_date)
                if not daily or daily.change_pct is None:
                    continue

                source, predict_type = self._parse_reason(sig.reason or "")
                confidence = float(sig.signal_strength or 0) * 10.0
                change_pct = float(daily.change_pct or 0.0)
                hit_limit_up = self._is_limit_up(session, sig.code, sig.signal_date)
                outcome_score = self._calc_outcome_score(
                    change_pct=change_pct,
                    hit_limit_up=hit_limit_up,
                    signal_type=(sig.signal_type or "").lower(),
                    confidence=confidence,
                )

                existing = None
                if sig.id is not None:
                    existing = (
                        session.query(SignalOutcome)
                        .filter(SignalOutcome.signal_id == sig.id)
                        .first()
                    )
                if existing:
                    existing.signal_date = sig.signal_date
                    existing.code = sig.code
                    existing.signal_type = sig.signal_type or "buy"
                    existing.source = source
                    existing.predict_type = predict_type
                    existing.confidence = confidence
                    existing.realized_change_pct = change_pct
                    existing.hit_limit_up = bool(hit_limit_up)
                    existing.outcome_score = outcome_score
                    existing.evaluated_at = datetime.now()
                else:
                    session.add(
                        SignalOutcome(
                            signal_id=sig.id,
                            signal_date=sig.signal_date,
                            code=sig.code,
                            signal_type=sig.signal_type or "buy",
                            source=source,
                            predict_type=predict_type,
                            confidence=confidence,
                            realized_change_pct=change_pct,
                            hit_limit_up=bool(hit_limit_up),
                            outcome_score=outcome_score,
                        )
                    )

                rows.append(
                    {
                        "signal_date": sig.signal_date,
                        "code": sig.code,
                        "signal_type": sig.signal_type or "buy",
                        "source": source,
                        "predict_type": predict_type,
                        "confidence": confidence,
                        "realized_change_pct": change_pct,
                        "hit_limit_up": bool(hit_limit_up),
                        "outcome_score": outcome_score,
                    }
                )
        return rows

    def _compute_factor_stats(self, as_of_date: str, lookback_days: int) -> dict[str, dict[str, float]]:
        """计算因子分数与次日收益相关性。"""
        start_date = (datetime.strptime(as_of_date, "%Y-%m-%d") - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        factor_values: dict[str, list[float]] = {
            "sentiment_score": [],
            "limit_up_score": [],
            "capital_flow_score": [],
            "technical_score": [],
            "global_score": [],
            "composite_score": [],
        }
        returns: list[float] = []

        with get_db_session(self.db_path) as session:
            trade_dates = [
                d[0]
                for d in (
                    session.query(StockDaily.trade_date)
                    .distinct()
                    .order_by(StockDaily.trade_date.asc())
                    .all()
                )
                if d and d[0]
            ]
            next_map = {trade_dates[i]: trade_dates[i + 1] for i in range(len(trade_dates) - 1)}

            score_rows = (
                session.query(StockScore)
                .filter(
                    StockScore.score_date >= start_date,
                    StockScore.score_date <= as_of_date,
                )
                .all()
            )

            for row in score_rows:
                next_date = next_map.get(row.score_date)
                if not next_date:
                    continue
                nxt = self._find_stock_daily(session, row.code, next_date)
                if not nxt or nxt.change_pct is None:
                    continue
                ret = float(nxt.change_pct or 0.0)
                returns.append(ret)
                factor_values["sentiment_score"].append(float(row.sentiment_score or 0.0))
                factor_values["limit_up_score"].append(float(row.limit_up_score or 0.0))
                factor_values["capital_flow_score"].append(float(row.capital_flow_score or 0.0))
                factor_values["technical_score"].append(float(row.technical_score or 0.0))
                factor_values["global_score"].append(float(row.global_score or 0.0))
                factor_values["composite_score"].append(float(row.composite_score or 0.0))

        stats: dict[str, dict[str, float]] = {}
        samples = len(returns)
        avg_ret = sum(returns) / samples if samples else 0.0
        for factor, values in factor_values.items():
            corr = self._pearson(values, returns) if len(values) == samples and samples > MIN_CORRELATION_SAMPLE_SIZE else 0.0
            stats[factor] = {
                "samples": float(samples),
                "corr": round(corr, 4),
                "avg_return": round(avg_ret, 4),
            }
        return stats

    def _derive_adaptive_weights(self, factor_stats: dict[str, dict[str, float]]) -> dict[str, float]:
        """根据历史相关性生成自适应权重（归一化后输出）。"""
        base = self.strategy_cfg.get("weights", {})
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
        merged_base = {k: float(base.get(k, v)) for k, v in defaults.items()}
        min_samples = int(self.learning_cfg.get("min_samples", 40))
        corr_cap = float(self.learning_cfg.get("corr_adjust_cap", 0.25))
        corr_cap = max(0.05, min(corr_cap, 0.5))

        factor_key_map = {
            "sentiment_score": "sentiment_score",
            "limit_up_score": "limit_up_score",
            "capital_flow": "capital_flow_score",
            "technical": "technical_score",
            "global_score": "global_score",
        }

        weighted = dict(merged_base)
        for weight_key, stat_key in factor_key_map.items():
            stat = factor_stats.get(stat_key, {})
            samples = int(stat.get("samples", 0))
            corr = float(stat.get("corr", 0.0))
            if samples < min_samples:
                continue
            corr = max(-corr_cap, min(corr, corr_cap))
            multiplier = 1.0 + corr
            weighted[weight_key] = merged_base[weight_key] * multiplier

        total = sum(weighted.values()) or 1.0
        return {k: round(v / total, 6) for k, v in weighted.items()}

    def _derive_source_confidence(self, outcomes: list[dict[str, Any]]) -> dict[str, float]:
        """根据来源历史命中率/收益率生成来源置信度。"""
        if not outcomes:
            return {}

        min_samples = int(self.learning_cfg.get("source_min_samples", 3))
        conf_floor = float(self.learning_cfg.get("source_conf_floor", 0.8))
        conf_ceil = float(self.learning_cfg.get("source_conf_ceil", 1.2))
        conf_floor = max(0.5, min(conf_floor, 1.0))
        conf_ceil = max(1.0, min(conf_ceil, 1.8))

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in outcomes:
            source = row.get("source") or "综合评分"
            grouped.setdefault(source, []).append(row)

        confidence: dict[str, float] = {}
        for source, rows in grouped.items():
            n = len(rows)
            hit_rate = sum(1 for r in rows if r.get("hit_limit_up")) / n
            avg_change = sum(float(r.get("realized_change_pct") or 0.0) for r in rows) / n
            avg_score = sum(float(r.get("outcome_score") or 0.0) for r in rows) / n

            # 核心评分：命中率 + 次日收益 + 评分，缩放到约 [0.8, 1.2]
            raw = 1.0 + (hit_rate - 0.35) * 0.45 + (avg_change / 10.0) * 0.35 + ((avg_score - 50.0) / 100.0) * 0.25
            # 小样本收缩，避免过拟合
            shrink = n / (n + 6.0)
            conf = 1.0 + (raw - 1.0) * shrink
            if n < min_samples:
                conf = 1.0
            conf = max(conf_floor, min(conf, conf_ceil))
            confidence[source] = round(conf, 4)
        return confidence

    def _persist_learning_config(
        self,
        adaptive_weights: dict[str, float],
        source_confidence: dict[str, float],
        sample_count: int,
    ) -> None:
        """将学习结果写回 settings.yaml。"""
        cfg = dict(self.config)
        strategy = cfg.setdefault("strategy", {})
        learning = strategy.setdefault("learning", {})

        strategy["adaptive_weights"] = adaptive_weights
        strategy["source_confidence"] = source_confidence
        learning["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        learning["last_sample_count"] = int(sample_count)

        if bool(learning.get("persist_to_yaml", True)):
            save_config(cfg, self.config_path)
        # 保持本实例配置同步
        self.config = cfg
        self.strategy_cfg = strategy
        self.learning_cfg = learning

    def _save_snapshot(
        self,
        as_of_date: str,
        lookback_days: int,
        sample_count: int,
        adaptive_weights: dict[str, float],
        source_confidence: dict[str, float],
        factor_stats: dict[str, dict[str, float]],
    ) -> None:
        """保存学习快照到数据库。"""
        with get_db_session(self.db_path) as session:
            rec = (
                session.query(LearningSnapshot)
                .filter(LearningSnapshot.snapshot_date == as_of_date)
                .first()
            )
            payload = {
                "lookback_days": lookback_days,
                "sample_count": sample_count,
                "adaptive_weights": json.dumps(adaptive_weights, ensure_ascii=False),
                "source_confidence": json.dumps(source_confidence, ensure_ascii=False),
                "factor_stats": json.dumps(factor_stats, ensure_ascii=False),
                "notes": "auto-learn",
            }
            if rec:
                for k, v in payload.items():
                    setattr(rec, k, v)
            else:
                session.add(
                    LearningSnapshot(
                        snapshot_date=as_of_date,
                        **payload,
                    )
                )

    @staticmethod
    def _parse_reason(reason: str) -> tuple[str, str]:
        """从 reason 中解析 source/predict_type。"""
        source = "综合评分"
        predict_type = ""
        text = reason or ""
        if text.startswith("##TYPE:"):
            try:
                _, rest = text.split("##TYPE:", 1)
                predict_type, rest = rest.split("##TIME:", 1)
                if "##SRC:" in rest:
                    _, rest = rest.split("##SRC:", 1)
                    source, _ = rest.split("##", 1)
                source = source.strip() or "涨停板"
                predict_type = predict_type.strip()
                return source, predict_type
            except Exception:
                pass

        if "热点" in text:
            source = "热点驱动"
        elif "全市场" in text:
            source = "全市场"
        elif "涨停" in text:
            source = "涨停板"
        return source, predict_type

    @staticmethod
    def _calc_outcome_score(
        change_pct: float,
        hit_limit_up: bool,
        signal_type: str,
        confidence: float,
    ) -> float:
        """将结果映射为 0-100 分，供来源和历史表现统计。"""
        score = 50.0 + change_pct * 2.4
        if hit_limit_up:
            score += 18.0
        if signal_type in ("buy", "premarket") and change_pct < 0:
            score -= 5.0
        # 高信心但实际较差会被轻微惩罚；高信心且表现好则略奖励
        conf_bias = (confidence - 5.0) * (0.4 if change_pct >= 0 else -0.3)
        score += conf_bias
        return round(max(0.0, min(100.0, score)), 3)

    @staticmethod
    def _candidate_codes(code: str) -> list[str]:
        raw = (code or "").strip().lower()
        if not raw:
            return []
        cands = [raw]
        if len(raw) == STOCK_CODE_LENGTH and raw.isdigit():
            cands.extend([f"sh{raw}", f"sz{raw}", f"bj{raw}"])
        elif len(raw) == PREFIXED_STOCK_CODE_LENGTH and raw[:2] in {"sh", "sz", "bj"} and raw[2:].isdigit():
            bare = raw[2:]
            cands.extend([bare, f"sh{bare}", f"sz{bare}", f"bj{bare}"])
        # 去重保持顺序
        return list(dict.fromkeys(cands))

    def _find_stock_daily(self, session, code: str, trade_date: str) -> StockDaily | None:
        cands = self._candidate_codes(code)
        if not cands:
            return None
        return (
            session.query(StockDaily)
            .filter(StockDaily.trade_date == trade_date, StockDaily.code.in_(cands))
            .first()
        )

    def _is_limit_up(self, session, code: str, trade_date: str) -> bool:
        cands = self._candidate_codes(code)
        if not cands:
            return False
        row = (
            session.query(LimitUpStock.id)
            .filter(LimitUpStock.trade_date == trade_date, LimitUpStock.code.in_(cands))
            .first()
        )
        return row is not None

    @staticmethod
    def _pearson(xs: list[float], ys: list[float]) -> float:
        """皮尔逊相关系数。"""
        n = min(len(xs), len(ys))
        if n < MIN_CORRELATION_SAMPLE_SIZE:
            return 0.0
        x = xs[:n]
        y = ys[:n]
        mx = sum(x) / n
        my = sum(y) / n
        cov = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=False))
        vx = sum((a - mx) ** 2 for a in x)
        vy = sum((b - my) ** 2 for b in y)
        if vx <= PEARSON_EPSILON or vy <= PEARSON_EPSILON:
            return 0.0
        return cov / math.sqrt(vx * vy)

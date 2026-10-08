"""海外个股研究：行情独立缓存、市场日历、原币展示和可追溯报告。"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone, timedelta
import json
import math
from zoneinfo import ZoneInfo

from src.database.db import get_db_session
from src.database.models import ResearchCache, StockDiagnosis
from src.utils.stock_code import resolve_identity
from src.services.overseas_market import phase, quote_quality, MARKETS


class OverseasResearchService:
    def __init__(self, config: dict):
        self.config = config
        self.db_path = config.get("database", {}).get("sqlite_path", "data/quant.db")

    def cached(self, key: str) -> dict:
        with get_db_session(self.db_path) as session:
            row = session.get(ResearchCache, key)
            return json.loads(row.payload_json) if row else {}

    def save_cache(self, key: str, payload: dict) -> None:
        with get_db_session(self.db_path) as session:
            row = session.get(ResearchCache, key)
            if row is None:
                row = ResearchCache(key=key)
                session.add(row)
            row.payload_json = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            row.updated_at = datetime.now()

    def _yahoo(self, identity) -> list[dict]:
        import yfinance as yf
        ticker = str(int(identity.code.split(".")[0])).zfill(4) + ".HK" if identity.market == "HK" else identity.code
        frame = yf.Ticker(ticker).history(period="1y", interval="1d", auto_adjust=False, timeout=15, raise_errors=True)
        rows = []
        fetched = datetime.now(timezone.utc).isoformat()
        for index, record in frame.iterrows():
            values = {key: float(record.get(label, float("nan"))) for key, label in
                      (("open", "Open"), ("high", "High"), ("low", "Low"), ("close", "Close"), ("volume", "Volume"))}
            if not all(math.isfinite(v) for v in values.values()) or values["close"] <= 0:
                continue
            day = index.tz_convert(MARKETS[identity.market][1]).strftime("%Y-%m-%d") if index.tzinfo else index.strftime("%Y-%m-%d")
            rows.append({**values, "code": identity.code, "name": identity.code, "trade_date": day,
                         "source": "Yahoo Finance", "currency": identity.currency, "price_adjustment": "raw",
                         "provider_timestamp": index.isoformat(), "fetched_at": fetched,
                         "stock_splits": float(record.get("Stock Splits", 0) or 0)})
        return rows

    def _longbridge(self, identity) -> list[dict]:
        cfg = (self.config.get("integrations") or {}).get("longbridge") or {}
        if identity.market not in ("US", "HK") or not all(cfg.get(k) for k in ("app_key", "app_secret", "access_token")):
            return []
        from longbridge.openapi import Config, QuoteContext, Period, AdjustType
        factory = getattr(Config, "from_apikey", Config)
        context = QuoteContext(factory(cfg["app_key"], cfg["app_secret"], cfg["access_token"]))
        try:
            symbol = identity.code if identity.market == "HK" else identity.code + ".US"
            candles = context.history_candlesticks_by_offset(symbol=symbol, period=Period.Day, adjust_type=AdjustType.NoAdjust,
                forward=False, time=datetime.now(ZoneInfo(MARKETS[identity.market][1])), count=250)
            fetched = datetime.now(timezone.utc).isoformat()
            rows = []
            for bar in candles:
                stamp = bar.timestamp
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=ZoneInfo(MARKETS[identity.market][1]))
                rows.append({"code": identity.code, "name": identity.code, "trade_date": stamp.astimezone(ZoneInfo(MARKETS[identity.market][1])).strftime("%Y-%m-%d"),
                    **{key: float(getattr(bar, key)) for key in ("open", "high", "low", "close", "volume")},
                    "source": "Longbridge", "currency": identity.currency, "price_adjustment": "raw",
                    "provider_timestamp": stamp.isoformat(), "fetched_at": fetched})
            return rows
        finally:
            close = getattr(context, "close", None)
            if close:
                close()

    def refresh(self, code: str, enrich: bool = False, realtime: bool = False) -> dict:
        identity = resolve_identity(code)
        if identity.market == "CN":
            raise ValueError("请使用国内证券研究入口")
        attempts, rows = [], []
        for provider, fetch in (("Yahoo Finance", self._yahoo), ("Longbridge", self._longbridge)):
            try:
                from src.collectors.request_budget import bounded_call
                rows = bounded_call(lambda fetch=fetch: fetch(identity), 15, quarantine_key=('overseas_bars', provider))
                attempts.append({"provider": provider, "status": "available" if rows else "unavailable"})
                if rows:
                    break
            except Exception:
                attempts.append({"provider": provider, "status": "fetch_failed"})
        if rows:
            rows = sorted(rows, key=lambda row: row["trade_date"])
            for i, row in enumerate(rows):
                row["change_pct"] = round((row["close"] / rows[i-1]["close"] - 1) * 100, 4) if i else None
            self.save_cache("overseas:" + identity.code, {"bars": rows, "attempts": attempts})
        if enrich:
            from src.services.overseas_evidence import OverseasEvidenceService
            OverseasEvidenceService(self.config).refresh(identity.code)
        if realtime:
            self.refresh_realtime(identity.code)
        result = self.snapshot(identity.code)
        result["attempts"] = attempts
        if not rows:
            result["refresh_status"] = "fetch_failed"
        return result

    def refresh_realtime(self, code: str) -> dict:
        from src.collectors.request_budget import bounded_call
        from src.services.overseas_realtime import fetch_quote, normalize
        identity = resolve_identity(code)
        if identity.market == 'CN':
            raise ValueError('仅支持海外证券快照')
        try:
            info = bounded_call(lambda: fetch_quote(identity.code), 8, quarantine_key=('overseas_realtime', 'yahoo'))
            quote = normalize(info if isinstance(info, dict) else {}, identity.code)
            if quote['price'] is None or 'unknown_provider_time' in quote['limitations'] or 'future_timestamp' in quote['limitations']:
                raise ValueError('报价或供应商时间不可用')
            previous = self.cached('overseas:realtime:' + identity.code)
            if previous.get('provider_timestamp') and quote['provider_timestamp'] < previous['provider_timestamp']:
                raise ValueError('供应商返回较旧快照')
            self.save_cache('overseas:realtime:' + identity.code, quote)
            return quote
        except Exception:
            previous = self.cached('overseas:realtime:' + identity.code)
            if previous:
                self.save_cache('overseas:realtime:' + identity.code, {**previous, 'refresh_status': 'fetch_failed'})
            return {**self.realtime(identity.code), 'refresh_status': 'fetch_failed'}

    def realtime(self, code: str) -> dict:
        from src.services.overseas_realtime import quality
        cached = self.cached('overseas:realtime:' + code)
        return quality(cached) if cached else {'status': 'missing', 'limitations': ['missing_quote']}

    def snapshot(self, code: str) -> dict:
        identity = resolve_identity(code)
        if identity.market not in MARKETS:
            raise ValueError("该入口仅支持海外证券")
        cached = self.cached("overseas:" + identity.code)
        state = phase(identity.market)
        bars = cached.get("bars", [])
        completed = [r for r in bars if state["effective_daily_bar_date"] and r["trade_date"] <= state["effective_daily_bar_date"]]
        quote = completed[-1] if completed else {}
        quality = quote_quality(quote, identity.market)
        from src.services.overseas_evidence import OverseasEvidenceService
        from src.services.overseas_symbols import name_for
        evidence = OverseasEvidenceService(self.config).cached(identity.code)
        realtime_quote = self.realtime(identity.code)
        name = (evidence.get('fundamentals', {}).get('profile') or {}).get('longName') or name_for(identity.code)
        technical = {}
        if completed:
            closes = [b["close"] for b in completed]
            technical = {f"ma{window}": round(sum(closes[-window:]) / window, 4) if len(closes) >= window else None for window in (5, 20, 60)}
        from src.services.research_artifact import build_context_pack
        context = {**asdict(identity), "name": name, "quote": quote, "phase": state,
                   "realtime_quote": realtime_quote,
                   "fundamentals": evidence.get('fundamentals'),
                   "news_evidence": evidence.get('news', {}).get('items', []),
                   "news_status": evidence.get('news', {}).get('status', 'missing'),
                   "daily": {"bars": completed[-60:], "bar_count": len(completed)}, "technical": technical,
                   "data_quality": quality}
        return {**asdict(identity), "name": name, "quote": quote, "bars": bars, "phase": state, "evidence": evidence,
                "realtime_quote": realtime_quote,
                "quality": quality, "technical": technical, "context_pack": build_context_pack(context),
                "attempts": cached.get("attempts", []), "research_only": True}

    def diagnose(self, code: str, force: bool = False) -> dict:
        identity = resolve_identity(code)
        from src.services.stock_diagnosis import StockDiagnosisService
        from src.services.report_language import report_language, language_directive
        lang = report_language(self.config)
        previous = StockDiagnosisService(self.config).latest(identity.code, max_age_minutes=30)
        if previous and not force and previous.get("language", "zh") == lang:
            return previous
        snapshot = self.refresh(identity.code, enrich=(self.config.get('diagnosis') or {}).get('fundamentals', True), realtime=True)
        if not snapshot["quote"]:
            return {"code": identity.code, "error": "海外行情不可用，请检查数据源后重试", "data_quality": snapshot["quality"]}
        from src.services.social_sentiment import social_context
        from src.analyzers.llm_client import LLMClient
        from src.services.opinion_validity import valid_score
        from src.services.research_artifact import build_research_artifact
        social = social_context(self.config, identity.code)
        prompt = ('根据证据生成 JSON：score(0-100), action(buy/add/hold/watch/reduce/sell/avoid), confidence(高/中/低), '
                  'one_sentence, trend, core_logic, risk_warning, invalidation, operation_advice。保持 JSON 键和枚举。'
                  '只作海外证券研究，价格使用原币。不要套用 A 股涨跌停、T+1 或交易日规则；新闻和财务缺失要说明，'
                  '不得编造未提供的数据，社交讨论仅代表观点。' + language_directive(lang, 'action; confidence=高/中/低'))
        raw = LLMClient(self.config.get("llm") or {}).chat_json(json.dumps({"research": snapshot, "social": social}, ensure_ascii=False), prompt)
        if not isinstance(raw, dict) or not raw:
            return {"code": identity.code, "error": "模型未返回海外研究结果"}
        score = valid_score(raw.get("score"))
        limited = snapshot["quality"]["status"] != "available" or score is None
        action = raw.get("action") if raw.get("action") in ("buy", "add", "hold", "watch", "reduce", "sell", "avoid") else "watch"
        if limited:
            action = "watch"
        labels = {"buy": "买入", "add": "加仓", "hold": "持有", "watch": "观望", "reduce": "减仓", "sell": "卖出", "avoid": "回避"}
        result = {**{key: str(raw.get(key) or "")[:6000] for key in ("one_sentence", "trend", "core_logic", "risk_warning", "invalidation", "operation_advice")},
            "code": identity.code, "name": snapshot['name'], "kind": "stock", "market": identity.market, "currency": identity.currency,
            "language": lang, "action": action, "action_label": labels[action], "score": score if score is not None else 50,
            "confidence": "低", "research_only": True, "trade_date": snapshot["quote"]["trade_date"], "quote": snapshot["quote"],
            "context_pack": snapshot["context_pack"], "social_evidence": social, "data_quality": snapshot["quality"],
            "guardrails": ["海外研究仅供参考；轻量基本面与新闻不能替代完整尽调，信心保持低。"], "battle_plan": {},
            "created_at": datetime.now(timezone.utc).isoformat(), "operation_suggestions": {}}
        result["structured_report"] = build_research_artifact(result)
        with get_db_session(self.db_path) as session:
            row = StockDiagnosis(code=identity.code, name=snapshot['name'], trade_date=result["trade_date"], action=action,
                                 score=result["score"], result_json=json.dumps(result, ensure_ascii=False))
            session.add(row)
            session.flush()
            result["diagnosis_id"] = row.id
            result["structured_report"] = build_research_artifact(result, report_id=row.id)
            row.result_json = json.dumps(result, ensure_ascii=False)
        return result

    def fx_rate(self, currency: str, *, refresh: bool = False) -> dict:
        if currency == "CNY":
            return {"status": "available", "rate": 1.0, "currency": currency, "base_currency": "CNY", "source": "identity"}
        if currency not in ("USD", "HKD", "JPY", "KRW", "TWD"):
            return {"status": "unavailable", "rate": None, "limitations": ["unsupported_currency"]}
        key = "overseas:fx:" + currency
        rate = self.cached(key)
        if refresh:
            try:
                import yfinance as yf
                frame = yf.Ticker(currency + "CNY=X").history(period="5d", auto_adjust=False, timeout=10, raise_errors=True)
                number = float(frame["Close"].iloc[-1])
                if not math.isfinite(number) or number <= 0:
                    raise ValueError("invalid_fx_rate")
                rate = {"rate": number, "as_of": frame.index[-1].strftime("%Y-%m-%d"),
                        "fetched_at": datetime.now(timezone.utc).isoformat(), "source": "Yahoo Finance"}
                self.save_cache(key, rate)
            except Exception:
                pass
        now = datetime.now(timezone.utc)
        try:
            age = (now.date() - datetime.fromisoformat(rate["as_of"]).date()).days
            valid = 0 <= age <= 3 and math.isfinite(rate["rate"]) and rate["rate"] > 0
        except (KeyError, ValueError, TypeError):
            valid = False
        return {**rate, "currency": currency, "base_currency": "CNY", "status": "available" if valid else "unavailable",
                "limitations": [] if valid else ["missing_or_stale_fx"]}

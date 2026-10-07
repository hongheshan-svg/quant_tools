"""美股社交证据：逐渠道返回来源及质量，不把讨论热度当作行情。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import httpx


def social_context(config: dict, code: str) -> dict:
    from src.utils.stock_code import resolve_identity
    identity = resolve_identity(code)
    if identity.market != "US":
        return {"status": "not_supported", "channels": [], "limitations": ["us_only"]}
    key = ((config.get("integrations") or {}).get("adanos") or {}).get("api_key")
    if not key:
        return {"status": "unavailable", "channels": [], "limitations": ["missing_api_key"]}
    def fetch(channel: str) -> dict:
        path = f"/reddit/stocks/v1/report/{identity.code}" if channel == "reddit" else f"/{channel}/stocks/v1/trending"
        try:
            response = httpx.get("https://api.adanos.org" + path, headers={"X-API-Key": key, "Accept": "application/json"}, timeout=8)
            response.raise_for_status()
            payload = response.json()
            if channel != "reddit":
                rows = payload if isinstance(payload, list) else payload.get("trending", payload.get("data", []))
                payload = [row for row in rows if str(row.get("ticker", row.get("symbol", ""))).upper() == identity.code]
            return {"source": channel, "status": "available" if payload else "missing", "data": payload,
                    "fetched_at": datetime.now(timezone.utc).isoformat(), "url": "https://api.adanos.org" + path}
        except Exception:
            return {"source": channel, "status": "fetch_failed", "data": None}
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(fetch, ("reddit", "x", "polymarket")))
    return {"status": "available" if all(r["status"] == "available" for r in rows) else "partial", "channels": rows,
            "limitations": ["social_opinions_not_verified_facts"]}

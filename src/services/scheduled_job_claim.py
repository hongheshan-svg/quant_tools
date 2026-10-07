"""共享数据库中的计划认领；手动补跑不认领，失败保留认领避免重复推送。"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo


def claim(job_id: str, config: dict, scheduled_for: datetime) -> tuple[bool, dict]:
    snapshot = deepcopy(config)
    # 去重依据是工作内容；密钥轮换、界面端口及不同进程的线程设置不应造成重复工作。
    from src.utils.redaction import redact
    workload = {key: snapshot.get(key) for key in ("watchlist", "strategy", "report", "notifier", "market_review", "diagnosis", "alerts")}
    path = Path((snapshot.get("database") or {}).get("sqlite_path", "data/quant.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if job_id == "watchlist_report":
        with sqlite3.connect(str(path), timeout=10) as connection:
            exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='watchlist'").fetchone()
            workload["stocks"] = [r[0] for r in connection.execute("SELECT code FROM watchlist ORDER BY code")] if exists else []
        snapshot["_scheduled_stock_codes"] = workload["stocks"]
    key = hashlib.sha256(json.dumps({"job": job_id, "content": redact(workload)}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if scheduled_for.tzinfo is None:
        scheduled_for = scheduled_for.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    occurrence = scheduled_for.astimezone(timezone.utc).isoformat()
    with sqlite3.connect(str(path), timeout=10) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS scheduled_job_claim (occurrence TEXT NOT NULL, workload TEXT NOT NULL, claimed_at REAL NOT NULL, PRIMARY KEY(occurrence, workload))")
        cursor = connection.execute("INSERT OR IGNORE INTO scheduled_job_claim VALUES (?, ?, ?)", (occurrence, key, time.time()))
        acquired = cursor.rowcount == 1
        if acquired:
            connection.execute("DELETE FROM scheduled_job_claim WHERE claimed_at < ?", (time.time() - 7 * 86400,))
    return acquired, snapshot

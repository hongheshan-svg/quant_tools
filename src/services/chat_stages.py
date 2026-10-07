"""流式阶段记录；未知标识照常透传，耗时使用单调时钟，关闭流也结算运行阶段。"""

from datetime import datetime, timezone
import time


class StageTracker:
    def __init__(self, prefix, budget=None):
        self.prefix, self.events, self.active, self.count = prefix, [], {}, 0
        self.budget = budget

    def start(self, name, scope=None):
        self.count += 1
        key = f"{self.prefix}:{self.count}"
        self.active[key] = time.monotonic()
        event = {"type": "stage", "stage_id": key, "name": name, "status": "started", "elapsed_ms": 0,
                 "occurred_at": datetime.now(timezone.utc).isoformat(), "scope": scope}
        if self.budget: event['remaining_ms'] = round(self.budget.remaining() * 1000)
        self.events.append(event)
        return event

    def finish(self, key, status="completed", reason=None):
        start = self.active.pop(key, None)
        if start is None: return None
        original = next(event for event in self.events if event["stage_id"] == key)
        event = {**original, "status": status, "elapsed_ms": round((time.monotonic() - start) * 1000),
                 "occurred_at": datetime.now(timezone.utc).isoformat(), "reason": reason}
        if self.budget: event['remaining_ms'] = round(self.budget.remaining() * 1000)
        self.events.append(event)
        return event

    def close(self, status="cancelled", reason=None):
        return [self.finish(key, status, reason) for key in list(self.active)]

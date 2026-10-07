"""选股运行的隔离来源轨迹：采集尝试与本地读取分别保存，不借用全局健康累计值。"""
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from threading import RLock
from uuid import uuid4

from src.utils.redaction import redact

_CAPTURE = ContextVar('screening_source_capture', default=None)


class SourceCapture:
    def __init__(self):
        self.run_id = uuid4().hex
        self.started_at = datetime.now().isoformat()
        self.attempts = []
        self.local = []
        self.lock = RLock()
        self.closed = False
        self.dropped = 0

    def append(self, dataset, attempt):
        with self.lock:
            if self.closed:
                return
            if len(self.attempts) >= 500:
                self.dropped += 1
                return
            self.attempts.append(redact({'dataset': dataset, **attempt, 'at': datetime.now().isoformat()}))

    def finish(self):
        with self.lock:
            self.closed = True
            failures = {a['dataset'] for a in self.attempts if not a['ok']}
            recovered = {a['dataset'] for a in self.attempts if a['ok']} & failures
            return {'version': 1, 'run_id': self.run_id, 'started_at': self.started_at,
                    'finished_at': datetime.now().isoformat(), 'attempts': list(self.attempts),
                    'local_reads': self.local, 'dropped': self.dropped,
                    'success': sum(bool(a['ok']) for a in self.attempts),
                    'failure': sum(not a['ok'] for a in self.attempts),
                    'fallback_datasets': sorted(recovered),
                    'status': 'partial' if self.dropped else 'recorded' if self.attempts or self.local else 'no_observations'}


@contextmanager
def capture_sources():
    capture = SourceCapture()
    token = _CAPTURE.set(capture)
    try:
        yield capture
    finally:
        capture.finish()
        _CAPTURE.reset(token)


def source_attempt(dataset, attempt):
    capture = _CAPTURE.get()
    if capture:
        capture.append(dataset, attempt)


def local_snapshot(rows):
    capture = _CAPTURE.get()
    if capture:
        counts = Counter((r.source or 'unknown', r.trade_date) for r in rows)
        capture.local = [{'dataset': '个股日线', 'source': source, 'trade_date': day, 'rows': count}
                         for (source, day), count in sorted(counts.items())]

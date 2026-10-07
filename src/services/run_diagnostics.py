"""历史日志与后台任务共用的脱敏排障摘要、稳定节点和完成顺序关系。"""
from src.utils.redaction import redact, redact_text

LANES = ('data', 'provider', 'llm', 'note', 'save', 'notify', 'task')


def snapshot(log: dict | None = None, *, task: dict | None = None) -> dict:
    task = task or {}
    log = log or {}
    trace = log.get('trace_id') or task.get('trace_id') or 'legacy'
    events = task.get('flow_events') or []
    # 已完成日志是报告的权威步骤；未完成/失败任务用落库事件恢复。
    raw = log.get('steps') or [e for e in events if e.get('type') in {'step', 'source_health', 'progress'}]
    nodes = []
    for index, event in enumerate(raw):
        kind = event.get('kind') or ('provider' if event.get('type') == 'source_health' else 'task')
        nodes.append({'id': event.get('id') or f'{trace}:step:{index + 1}', 'lane': kind,
            'name': redact_text(event.get('name') or event.get('text') or event.get('source') or '任务进度', 200),
            'status': 'unknown' if 'ok' not in event else 'success' if event['ok'] else 'failed',
            'ms': event.get('ms'), 'started_at': event.get('started_at'),
            'ended_at': event.get('ended_at') or event.get('at'),
            'detail': redact_text(event.get('detail') or event.get('error') or '', 200),
            'metadata': redact(event.get('metadata') or {k: event[k] for k in ('source', 'dataset') if k in event})})
    if not nodes:
        nodes = [{'id': f'{trace}:task', 'lane': 'task', 'name': redact_text(task.get('label') or '历史运行记录缺失', 200),
                  'status': task.get('status', 'unknown'), 'ms': None, 'started_at': task.get('started_at'),
                  'ended_at': task.get('finished_at'), 'detail': redact_text(task.get('error', ''), 200), 'metadata': {}}]
    status = ('failed' if task.get('status') == 'error' else task['status'] if task.get('status') in {'pending', 'running'}
              else 'unknown' if not raw else 'degraded' if any(n['status'] == 'failed' for n in nodes) else 'success')
    sources = {}
    for node in nodes:
        meta = node['metadata']
        if node['lane'] == 'provider':
            key = (meta.get('dataset') or 'unknown', meta.get('source') or node['name'])
            group = sources.setdefault(key, {'dataset': key[0], 'source': key[1], 'success': 0, 'failure': 0})
            if node['status'] in {'success', 'failed'}:
                group['success' if node['status'] == 'success' else 'failure'] += 1
    text = [f'运行 {trace} · {status}']
    text.extend(f"{n['name']} · {n['status']} · {n['ms'] if n['ms'] is not None else '未知'}ms · {n['detail']}" for n in nodes)
    if task.get('error'):
        text.append(redact_text(task['error'], 500))
    return {'version': 1, 'trace_id': trace, 'status': status, 'lanes': list(LANES), 'nodes': nodes,
            'edges': [{'from': a['id'], 'to': b['id'], 'kind': 'completion_order'} for a, b in zip(nodes, nodes[1:])],
            'events': redact(events), 'truncated': bool(task.get('flow_events_dropped')) or bool(log.get('truncated')),
            'sources': list(sources.values()), 'copy_text': '\n'.join(text)}

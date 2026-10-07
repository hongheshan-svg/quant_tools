"""自选行的只读状态映射，不把查不到报告和从未分析合并成同一种状态。"""
from datetime import datetime
from zoneinfo import ZoneInfo


def row_status(diagnosis, quote_quality, task=None, query_error=None, today=None):
    today = today or datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d')
    task = task or {}
    state = task.get('status')
    if state in {'pending', 'running'}:
        return {'status': 'running', 'label': '分析运行中', 'next_action': 'wait', 'reason': task.get('label') or '等待分析完成', 'task_id': task.get('id')}
    if query_error:
        return {'status': 'unknown', 'label': '报告查询未知', 'next_action': 'retry_query', 'reason': query_error}
    report_time = str((diagnosis or {}).get('created_at') or '')
    if state == 'error' and str(task.get('created_at') or '').replace('T', ' ') > report_time:
        return {'status': 'failed', 'label': '上次分析失败', 'next_action': 'analyze', 'reason': task.get('error') or '请重试分析'}
    if not diagnosis:
        return {'status': 'missing', 'label': '尚无报告', 'next_action': 'analyze', 'reason': '生成第一份报告'}
    if report_time.startswith(today):
        return {'status': 'updated', 'label': '今日已更新', 'next_action': 'view_report', 'reason': '可查看报告；报价质量需单独核对'}
    return {'status': 'historical', 'label': '历史报告待更新', 'next_action': 'analyze', 'reason': '最近报告：' + (report_time or '时间未知')}

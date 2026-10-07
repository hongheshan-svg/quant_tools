from src.services.watchlist_status import row_status


def test_next_action_maps_each_distinct_state():
    quality = {'status': 'available'}
    report = {'created_at': '2026-10-07 16:00'}
    assert row_status(report, quality, today='2026-10-07')['next_action'] == 'view_report'
    assert row_status(report, quality, today='2026-10-08')['status'] == 'historical'
    assert row_status(None, quality)['status'] == 'missing'
    assert row_status(None, quality, query_error='读取失败')['next_action'] == 'retry_query'
    assert row_status(report, quality, {'id': 'a', 'status': 'running'})['next_action'] == 'wait'
    assert row_status(report, quality, {'status': 'error', 'created_at': '2026-10-08T12:00', 'error': '调用失败'})['status'] == 'failed'
    assert row_status(report, quality, {'status': 'error', 'created_at': '2026-10-06T12:00'}, today='2026-10-07')['status'] == 'updated'

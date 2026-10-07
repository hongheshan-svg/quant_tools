"""safe_collect 捕获 SDK 异常后，CLI/定时入口仍必须返回失败而不是成功。"""
import pytest
from src import scheduler


@pytest.mark.parametrize('status', ['partial', 'fetch_failed'])
def test_partial_collection_reaches_cli_and_once_failure(monkeypatch, status):
    import main
    from src.collectors import cailianshe
    from src import config_loader
    class Collector:
        SOURCE_NAME = '离线财联社'
        last_result = {'status': status, 'errors': ['模拟接口超时']}
        def __init__(self, *_): pass
        def safe_collect(self): return []
    monkeypatch.setattr(cailianshe, 'CailiansheCollector', Collector)
    monkeypatch.setattr(config_loader, 'load_config', lambda: {})
    monkeypatch.setattr(main, 'setup_logging', lambda *_: None)
    reports = []
    monkeypatch.setattr(scheduler, '_report_error', lambda *args: reports.append(args))
    assert scheduler.run_job_entry('cailianshe') == scheduler.JOB_FAILED_EXIT
    assert reports and '模拟接口超时' in str(reports)
    monkeypatch.setitem(scheduler.ONCE_STEPS, 'collect', ('采集', (scheduler._run_cailianshe_collection,)))
    with pytest.raises(RuntimeError, match='模拟接口超时'):
        scheduler.run_once({}, ['collect'])

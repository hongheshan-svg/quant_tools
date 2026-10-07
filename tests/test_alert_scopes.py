import json
from types import SimpleNamespace
import pytest
from tests.test_alerts import db_path  # noqa: F401
from src.services.alert_service import AlertService, validate_rule
from src.services.portfolio_risk import PortfolioRiskService
from src.services.alert_rule_sources import rule_sources
from src.services.watchlist import WatchlistService


def test_scope_matrix_and_parent_id():
    rule = validate_rule({'scope': 'watchlist', 'type': 'price_cross', 'price': 10})
    assert rule['code'] == '' and rule['scope'] == 'watchlist'
    assert rule['id'] == validate_rule({**rule, 'note': '改备注'})['id']
    assert validate_rule({'scope': 'portfolio_account', 'type': 'account_drawdown', 'account': 'real:家人'})['account'] == 'real:家人'
    for raw in [{'scope': 'stock', 'type': 'account_drawdown', 'code': '600519'}, {'scope': 'portfolio_account', 'type': 'price_cross', 'price': 10}, {'scope': 'portfolio_account', 'type': 'account_drawdown', 'account': 'other'}]:
        with pytest.raises(ValueError): validate_rule(raw)


def test_watchlist_expands_dynamically_and_keeps_parent_id(db_path, monkeypatch):
    members = [{'code': '300003', 'name': '持仓股', 'kind': 'stock'}]
    monkeypatch.setattr(WatchlistService, 'list', lambda self: members)
    service = AlertService({'database': {'sqlite_path': db_path}, 'alerts': {'rules': [{'scope': 'watchlist', 'type': 'price_cross', 'price': 9, 'direction': 'above'}]}})
    first = [event for event in service.evaluate() if event.rule_id]
    assert [event.code for event in first] == ['300003']
    parent_id = first[0].rule_id
    members[:] = [{'code': '600001', 'name': '涨停股', 'kind': 'stock'}]
    second = [event for event in service.evaluate() if event.rule_id]
    assert [event.code for event in second] == ['600001'] and second[0].rule_id == parent_id
    assert service.test_rule(service.rules[0])['members'] == 1


def test_account_risk_quality_and_auto_stop_dedup(db_path, monkeypatch):
    report = {'quality': {'valuation': 'available'}, 'cash_known': True,
              'positions': [{'code': '300003', 'name': '持仓股', 'weight': 50, 'stop_gap': -1, 'price_quality': {'status': 'available'}}],
              'drawdown': {'current_drawdown': -12, 'quality': {'status': 'available'}}}
    accounts = []
    monkeypatch.setattr(PortfolioRiskService, 'report', lambda self: accounts.append(self.account) or report)
    rules = [{'scope': 'portfolio_account', 'account': 'real:家人', 'type': 'account_concentration'},
             {'scope': 'portfolio_account', 'type': 'account_drawdown'},
             {'scope': 'portfolio_holdings', 'type': 'stop_risk'}]
    service = AlertService({'database': {'sqlite_path': db_path}, 'alerts': {'rules': rules}})
    events = service.evaluate()
    assert len([event for event in events if event.alert_type in {'stop_loss', 'near_stop', 'stop_risk'}]) == 1
    assert any(event.code == 'account:real:家人' for event in events)
    assert 'real:家人' in accounts
    report['cash_known'] = False
    assert not service.test_rule(service.rules[0])['available']
    report['drawdown']['quality']['status'] = 'partial'
    assert not service.test_rule(service.rules[1])['available']
    report['positions'][0]['price_quality']['status'] = 'stale'
    assert service.test_rule({'scope': 'portfolio_account', 'type': 'account_prices_stale'})['triggered']


def test_source_counts_and_environment_does_not_expose_values(monkeypatch):
    import src.services.alert_rule_sources as module
    valid = {'code': '600519', 'type': 'price_cross', 'price': 100}
    monkeypatch.setattr(module, 'read_settings', lambda: {'alerts': {'rules': [valid]}})
    monkeypatch.setenv('QUANT__ALERTS__RULES', json.dumps([valid, {**valid, 'enabled': False}, {'bad': 'secret'}]))
    result = rule_sources({'alerts': {'rules': [valid, {**valid, 'enabled': False}, {'bad': 'secret'}]}})
    assert result['summary'] == {'source': 'environment', 'readonly': True, 'configured': 3, 'valid': 2, 'invalid': 1, 'disabled': 1, 'effective': 1, 'file_configured': 1, 'environment_override': True}
    assert 'secret' not in json.dumps(result)

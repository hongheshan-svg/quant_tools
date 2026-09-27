from src.utils.stock_code import bare_code, board_of, code_candidates, daily_limit_pct, exchange_of, prefixed_code


def test_bare_and_candidates():
    assert bare_code(" SH600519 ") == "600519"
    assert bare_code("600519") == "600519"
    assert code_candidates("sz000001") == ["sz000001", "000001", "sh000001", "bj000001"]
    assert code_candidates("600519") == ["600519", "sh600519", "sz600519", "bj600519"]
    assert code_candidates("") == []


def test_exchange_and_board():
    assert [exchange_of(c) for c in ("600519", "688001", "000001", "300750", "830799", "430047", "920000")] == [
        "sh", "sh", "sz", "sz", "bj", "bj", "bj",  # 92 开头是北交所新代码，不是上交所
    ]
    assert prefixed_code("920000") == "bj920000"
    assert [board_of(c) for c in ("600519", "300750", "688001", "920000")] == ["主板", "创业板", "科创板", "北交所"]
    assert daily_limit_pct("688001") == 0.20 and daily_limit_pct("600519", "*ST样本") == 0.05

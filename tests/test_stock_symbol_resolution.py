import pandas as pd
import pytest

from app.stock import resolve_yahoo_symbol


class DummyTicker:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def history(self, period: str = "5d"):
        if self.symbol in {"2330.TW", "2454.TW", "3211.TWO"}:
            return pd.DataFrame({"Close": [100.0, 101.0]})
        return pd.DataFrame()


@pytest.fixture(autouse=True)
def clear_symbol_cache():
    resolve_yahoo_symbol.cache_clear()
    yield
    resolve_yahoo_symbol.cache_clear()


def test_resolve_yahoo_symbol_2330_is_tw(monkeypatch):
    monkeypatch.setattr("app.stock.yf.Ticker", DummyTicker)
    assert resolve_yahoo_symbol("2330") == "2330.TW"


def test_resolve_yahoo_symbol_2454_is_tw(monkeypatch):
    monkeypatch.setattr("app.stock.yf.Ticker", DummyTicker)
    assert resolve_yahoo_symbol("2454") == "2454.TW"


def test_resolve_yahoo_symbol_3211_is_two(monkeypatch):
    monkeypatch.setattr("app.stock.yf.Ticker", DummyTicker)
    assert resolve_yahoo_symbol("3211") == "3211.TWO"


def test_resolve_yahoo_symbol_invalid_code_raises_value_error(monkeypatch):
    monkeypatch.setattr("app.stock.yf.Ticker", DummyTicker)
    with pytest.raises(ValueError, match=r"999999.*\.TW.*\.TWO.*無有效歷史資料"):
        resolve_yahoo_symbol("999999")

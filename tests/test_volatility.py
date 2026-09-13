import importlib
import json

import numpy as np
import pandas as pd
import pytest

from app.volatility import (
    calculate_atr, calculate_atr_percent, classify_volatility,
    calculate_atr_distance, calculate_atr_signed_distance,
    calculate_zone_width_atr, summarize_volatility,
)
from app.support_resistance_analysis import SupportResistanceEngine


def bars(count=40):
    close = 100 + np.sin(np.arange(count)) * 5
    return pd.DataFrame(dict(Open=close, High=close + 2, Low=close - 2,
                             Close=close, Volume=1000),
                        index=pd.date_range('2026-01-01', periods=count))


def test_wilder_seed_gaps_and_recurrence():
    data = pd.DataFrame(dict(High=[10, 13, 12, 16, 15],
                             Low=[8, 11, 9, 14, 10], Close=[9, 12, 10, 15, 11]))
    # TR = missing, 4, 3, 6, 5; seed = 13/3; next = (13/3*2+5)/3.
    actual = calculate_atr(data, 3)
    assert actual.iloc[:3].isna().all()
    assert actual.iloc[3] == pytest.approx(13 / 3)
    assert actual.iloc[4] == pytest.approx(41 / 9)


def test_normal_summary_and_precision():
    data = bars()
    result = summarize_volatility(data)
    assert result['atr'] == calculate_atr(data).iloc[-1]
    assert result['atr_percent'] == result['atr'] / data.Close.iloc[-1] * 100
    assert result['volatility_level'] == classify_volatility(result['atr_percent'])
    assert calculate_atr_percent(15, 500) == 3
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('count', [0, 1, 13, 14])
def test_insufficient(count):
    assert summarize_volatility(bars(count)) == dict(
        atr=None, atr_percent=None, volatility_level='資料不足')


@pytest.mark.parametrize('value,label', [(0, '低波動'), (1.499, '低波動'),
    (1.5, '中等波動'), (2.999, '中等波動'), (3, '高波動'), (4.999, '高波動'), (5, '極高波動')])
def test_thresholds(value, label):
    assert classify_volatility(value) == label


@pytest.mark.parametrize('bad', [None, float('nan'), float('inf'), -1, 0, 'bad', [], 10**1000])
def test_invalid_distances(bad):
    assert calculate_atr_distance(500, 485, bad) is None
    assert calculate_atr_distance(bad, 485, 10) is None
    assert calculate_atr_signed_distance(500, bad, 10) is None
    assert calculate_zone_width_atr(480, 490, bad) is None
    assert calculate_atr_percent(10, bad) is None


def test_distances_and_width():
    assert calculate_atr_distance(500, 485, 10) == 1.5
    assert calculate_atr_signed_distance(515, 500, 10) == 1.5
    assert calculate_atr_signed_distance(485, 500, 10) == -1.5
    assert calculate_zone_width_atr(480, 490, 10) == 1
    assert calculate_zone_width_atr(490, 480, 10) is None
    assert calculate_zone_width_atr(480, 480, 10) == 0
    assert calculate_atr_distance(1e308, 1, 1e-308) is None


@pytest.mark.parametrize('bad', [None, float('nan'), float('inf'), -1, 'bad'])
def test_invalid_percent_and_classification(bad):
    assert calculate_atr_percent(bad, 500) is None
    assert classify_volatility(bad) == '資料不足'


def test_flat_market():
    data = bars()
    data[['High', 'Low', 'Close']] = 100.0
    assert summarize_volatility(data) == dict(atr=0, atr_percent=0, volatility_level='低波動')
    assert calculate_atr_distance(100, 100, 0) is None


@pytest.mark.parametrize('column', ['High', 'Low', 'Close'])
def test_missing_invalid_bar_and_recovery(column):
    data = bars(50)
    data.loc[data.index[20], column] = float('nan')
    actual = calculate_atr(data)
    assert actual.iloc[20:35].isna().all()
    assert pd.notna(actual.iloc[35])
    data.loc[data.index[-1], column] = float('nan')
    assert summarize_volatility(data)['atr'] is None
    assert calculate_atr(data.drop(columns=column)).isna().all()


def test_index_alignment_and_no_lookahead():
    data = bars()
    original = data.copy(deep=True)
    full = calculate_atr(data)
    for end in range(1, len(data) + 1):
        pd.testing.assert_series_equal(calculate_atr(data.iloc[:end]), full.iloc[:end])
    pd.testing.assert_frame_equal(data, original)
    assert calculate_atr(data.iloc[::-1]).isna().all()
    duplicate = data.copy()
    duplicate.index = [0] * len(data)
    assert calculate_atr(duplicate).isna().all()


def test_sr_metadata_and_cutoff(monkeypatch):
    from app.support_resistance_analysis.models import Candidate
    import app.support_resistance_analysis.engine as engine
    data = bars()
    data[['High', 'Low', 'Close']] = [505, 495, 500]
    monkeypatch.setattr(engine, 'detect_swings', lambda *args: [
        Candidate(485, 'swing_low', 'swing', 1, 2, low=480, high=490),
        Candidate(515, 'swing_high', 'swing', 1, 2, low=510, high=520)])
    monkeypatch.setattr(engine, 'build_profile', lambda *args: ([], {}))
    monkeypatch.setattr(engine, 'detect_vwap', lambda *args: [])
    monkeypatch.setattr(engine, 'cluster_prices', lambda *args: [])
    detector = SupportResistanceEngine()
    result = detector.detect(data)
    assert result['atr'] == 10
    assert result['nearest_support_distance_atr'] == 1
    assert result['nearest_resistance_distance_atr'] == 1
    assert all(z['zone_width_atr'] == 1 for z in result['summary_key_levels'])
    assert detector.detect(data, as_of=data.index[20]) == detector.detect(data.iloc[:21])
    empty = detector.detect(data.iloc[:0])
    assert empty['atr'] is None and empty['nearest_support_distance_atr'] is None
    json.dumps(result, allow_nan=False)


def test_stock_integration_no_extra_download_or_rule_changes(monkeypatch):
    import app.stock as stock
    data = bars(80)
    calls = []
    class Ticker:
        def history(self, **kwargs):
            calls.append(kwargs)
            return data.copy()
    monkeypatch.setattr(stock, 'resolve_yahoo_symbol', lambda code: code)
    monkeypatch.setattr(stock.yf, 'Ticker', lambda symbol: Ticker())
    monkeypatch.setattr(stock, 'get_realtime_price', lambda code: {})
    monkeypatch.setattr(stock, 'get_market_change_percent', lambda: 0)
    result = stock.get_stock_analysis('TEST')
    assert len(calls) == 1
    assert result['atr'] == result['support_resistance']['atr']
    assert 'ATR14：' in result['technical_summary']
    assert stock.get_stock_indicators('TEST')['atr'] == result['atr']
    monkeypatch.setattr(stock, 'summarize_volatility', lambda data: {})
    legacy = stock.get_stock_analysis('TEST')
    for key, value in legacy.items():
        if key == 'technical_summary':
            assert result[key].startswith(value + ' / ATR14：')
        else:
            assert result[key] == value


def test_api_startup_with_isolated_database(tmp_path, monkeypatch):
    import app.database as database
    monkeypatch.setattr(database, 'DATABASE_PATH', tmp_path / 'startup.db')
    main = importlib.import_module('app.main')
    from fastapi.testclient import TestClient
    # Real lifespan starts/stops the scheduler; immediate GET causes no monitor run.
    with TestClient(main.app) as client:
        assert client.get('/').json() == {'message': '台股監測助手啟動成功'}

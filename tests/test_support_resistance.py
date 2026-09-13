import json
from dataclasses import replace
import numpy as np
import pandas as pd
import pytest
from app.support_resistance_analysis import SupportResistanceConfig, SupportResistanceEngine, format_support_resistance_output
from app.support_resistance_analysis.models import Candidate
from app.support_resistance_analysis.engine import merge_candidates, classify_zone, strength_score, touch_statistics
from app.support_resistance_analysis.swing import detect_swings
from app.support_resistance_analysis.volume_profile import build_profile
from app.support_resistance_analysis.vwap import detect_vwap
from app.support_resistance_analysis.clustering import cluster_prices


def bars(prices=None):
    prices = np.array(prices if prices is not None else [100, 103, 110, 104, 100, 96, 90, 95, 100, 101, 100], dtype=float)
    return pd.DataFrame(dict(Open=prices, High=prices+0.5, Low=prices-0.5,
                             Close=prices, Volume=np.full(len(prices), 1000.)),
                        index=pd.date_range('2026-01-01', periods=len(prices)))


def point(price, family='swing'):
    return Candidate(price, family, family, 0, 0)


@pytest.fixture
def config():
    return SupportResistanceConfig(swing_window=2)


def test_obvious_high_resistance(config):
    result = SupportResistanceEngine(config).detect(bars())
    assert any(z['low'] <= 110.5 <= z['high'] and 'swing_high' in z['methods'] for z in result['resistance_zones'])


def test_obvious_low_support(config):
    result = SupportResistanceEngine(config).detect(bars())
    assert any(z['low'] <= 89.5 <= z['high'] and 'swing_low' in z['methods'] for z in result['support_zones'])


def test_close_candidates_merge(config):
    assert len(merge_candidates([point(425), point(427), point(426.5)], 440, config)) == 1


def test_far_candidates_and_chain_do_not_merge(config):
    assert len(merge_candidates([point(90), point(110)], 100, config)) == 2
    assert len(merge_candidates([point(100), point(100.8), point(101.6)], 100, config)) == 2


@pytest.mark.parametrize('data', [None, pd.DataFrame(), pd.DataFrame({'Volume': [0]})])
def test_empty_and_missing_do_not_crash(data):
    result = SupportResistanceEngine().detect(data)
    assert result['support_zones'] == []
    json.dumps(result, allow_nan=False)


def test_small_kmeans(config):
    assert cluster_prices([], config) == []
    assert cluster_prices([point(100)]*5, config) == []
    assert SupportResistanceEngine(config).detect(bars([100]))['current_price'] == 100


def test_zone_active_and_neutral_margin(config):
    assert classify_zone(425, 430, 428, config) == 'active'
    assert classify_zone(425, 430, 430.1, config) == 'active'
    result = SupportResistanceEngine(config).detect(bars([100]*10))
    assert result['active_zones']
    assert '目前測試區' in format_support_resistance_output(result)


def test_consensus_score_and_family_deduplication(config):
    single = strength_score(['swing'], 1, 2, 20, config)
    assert strength_score(['swing', 'swing'], 1, 2, 20, config) == single
    assert strength_score(['swing', 'volume_profile', 'kmeans'], 1, 2, 20, config) > single


def test_swing_confirmation_and_as_of(config):
    data = bars()
    assert not detect_swings(data.iloc[:4], config)
    pivot = detect_swings(data.iloc[:5], config)[0]
    assert pivot.position == 2 and pivot.confirmed_position == 4
    engine = SupportResistanceEngine(config)
    expected = engine.detect(data.iloc[:8])
    future = pd.concat([data, bars([1000000]).set_axis([pd.Timestamp('2027-01-01')])])
    assert engine.detect(future, as_of=data.index[7]) == expected


def test_volume_conservation_and_bin_width(config):
    data = bars()
    for c in (config, replace(config, vp_bin_size=2)):
        nodes, profile = build_profile(data, c)
        assert sum(profile['volumes']) == pytest.approx(data.Volume.sum())
        assert profile['poc'] == nodes[[p.method for p in nodes].index('volume_profile_poc')].price
    data = bars([100])
    data.loc[:, 'Low'] = 90
    data.loc[:, 'High'] = 110
    _, profile = build_profile(data, replace(config, vp_bins=4))
    assert profile['volumes'] == pytest.approx([250]*4)


def test_vwap_uses_actual_confirmed_anchor(config):
    data = bars()
    swings = detect_swings(data, config)
    result = {p.method: p.price for p in detect_vwap(data, swings, config)}
    assert result['anchored_vwap_swing_low'] == pytest.approx(data.Close.iloc[6:].mean())
    assert result['anchored_vwap_swing_high'] == pytest.approx(data.Close.iloc[2:].mean())


def test_touch_residence_and_unconfirmed_reaction(config):
    data = bars([102, 100, 100, 100, 103, 104, 100, 103])
    count, last, confirmed, _ = touch_statistics(data, 99, 101, config)
    assert count == 2 and last == 6 and confirmed == 7
    assert touch_statistics(data.iloc[:7], 99, 101, config)[0] == 1
    assert touch_statistics(bars([100]*20), 99, 101, config)[0] == 0


def test_invalid_prices_zero_volume_and_input_not_mutated(config):
    data = bars()
    data['Volume'] = 0
    data.loc[data.index[-1], ['High', 'Low', 'Close']] = np.inf
    original = data.copy(deep=True)
    result = SupportResistanceEngine(config).detect(data)
    pd.testing.assert_frame_equal(data, original)
    assert any(p['family'] == 'swing' for p in result['candidates'])
    assert not any(p['family'] in ('vwap', 'volume_profile') for p in result['candidates'])
    json.dumps(result, allow_nan=False)


def test_method_failure_isolated(config, monkeypatch):
    import app.support_resistance_analysis.engine as module
    def fail(*args):
        raise RuntimeError('unavailable')
    monkeypatch.setattr(module, 'cluster_prices', fail)
    result = SupportResistanceEngine(config).detect(bars())
    assert 'kmeans unavailable' in result['warnings']
    assert result['support_zones'] and result['resistance_zones']


def test_clustering_is_deterministic_and_meaningful(config):
    points = [point(v) for v in [90, 91, 92, 110, 111, 112]]
    result = cluster_prices(points, config)
    assert [p.price for p in result] == pytest.approx([91, 111])
    assert result == cluster_prices(points, config)


def test_recency_floor_and_touch_volume_weights(config):
    base = strength_score(['swing'], 0, 1, 0, config)
    assert strength_score(['swing'], 3, 3, 0, config) > base
    old = strength_score(['swing'], 0, 1, 10000, config)
    assert 0 < old < base


def test_integration_does_not_change_existing_scores(monkeypatch):
    import app.stock as stock
    class Ticker:
        def history(self, **kwargs):
            return bars(np.tile([100, 103, 110, 104, 100, 96, 90, 95, 100, 101], 8))
    monkeypatch.setattr(stock, 'resolve_yahoo_symbol', lambda code: code)
    monkeypatch.setattr(stock.yf, 'Ticker', lambda symbol: Ticker())
    monkeypatch.setattr(stock, 'get_realtime_price', lambda code: {})
    monkeypatch.setattr(stock, 'get_market_change_percent', lambda: 0)
    original_detect = stock.SupportResistanceEngine.detect
    calls = []
    def counted_detect(self, data):
        calls.append(1)
        return original_detect(self, data)
    monkeypatch.setattr(stock.SupportResistanceEngine, 'detect', counted_detect)
    with_sr = stock.get_stock_analysis('TEST')
    assert len(calls) == 1
    monkeypatch.setattr(stock.SupportResistanceEngine, 'detect', lambda self, data: {})
    without_sr = stock.get_stock_analysis('TEST')
    for key in with_sr:
        if key == 'timeframe_analysis':
            for timeframe in ('short_term', 'medium_term'):
                for field in with_sr[key][timeframe]:
                    if field != 'summary':
                        assert with_sr[key][timeframe][field] == without_sr[key][timeframe][field]
            continue
        if not key.startswith('support_resistance'):
            assert with_sr[key] == without_sr[key], key


@pytest.mark.parametrize('kwargs', [dict(vp_bins=0), dict(vp_bin_size=0),
                                  dict(recency_floor=2), dict(max_support_zones=1.5),
                                  dict(merge_tolerance_pct=float('nan'))])
def test_invalid_configuration_rejected(kwargs):
    with pytest.raises(ValueError):
        SupportResistanceConfig(**kwargs)


def test_extreme_price_and_missing_open(config):
    data = bars().drop(columns='Open')
    data.loc[data.index[-1], ['High', 'Low', 'Close']] = [100001, 99999, 100000]
    result = SupportResistanceEngine(config).detect(data)
    assert result['current_price'] == 101
    assert any('extreme' in warning for warning in result['warnings'])
    assert result['candidates']

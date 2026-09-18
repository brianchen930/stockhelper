from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import pytest
from app.decision_context import build_decision_context, attach_decision
from app.decision_engine import DecisionEngine, ActionState as A, HolderActionState as H, DecisionConfig
from app.decision_state import stabilize


def inputs():
    zone = dict(low=98., high=101., strength_score=7)
    data = pd.DataFrame(dict(Close=[100.] * 21, High=[101.] * 21, Volume=[1000.] * 21),
                        index=pd.date_range('2026-08-17', periods=21, freq='B'))
    result = dict(stock_code='SAMPLE', date='2026-09-14', close=100., realtime_price=999.,
        atr=2., atr_percent=2., volatility_level='中等波動', ma5=101., ma20=98., ma60=95.,
        rsi=40, kd_k=30, kd_d=40, macd_histogram=-1., previous_macd_histogram=-2.,
        macd_analysis=dict(position='below_signal', momentum='bearish_weakening'),
        timeframe_analysis=dict(short_term=dict(score=-4, label='偏空'), medium_term=dict(score=5, label='偏多')),
        support_resistance=dict(nearest_support=zone,
            institutional_context=dict(institutional_level='NEUTRAL', institutional_score=0, confidence=1.),
            bayesian_support_selected=dict(support_low=98., support_high=101., base_support_probability=.7,
                adjusted_support_probability=.7, adjusted_support_level='高', display=dict(model_status='ready', rating='高'))))
    return result, data, dict(nearest_support=zone)


def test_adapter_ab_acceptance_and_preserves_input():
    b, bars, prior = inputs()
    snapshot = deepcopy(b)
    cb = build_decision_context(b, bars, previous_zones=prior)
    assert b == snapshot
    a = deepcopy(b)
    a['atr'], a['atr_percent'], a['volatility_level'] = 6., 6., '極高波動'
    a['support_resistance']['institutional_context'].update(institutional_level='STRONG_PRESSURE', institutional_score=-8)
    a['support_resistance']['bayesian_support_selected']['display']['rating'] = '低'
    ca = build_decision_context(a, bars, previous_zones=prior)
    da, db = map(DecisionEngine().evaluate, (ca, cb))
    assert (da.entry_action, da.holder_action) == (A.AVOID, H.TIGHTEN_RISK)
    assert (db.entry_action, db.holder_action) == (A.WATCH_FOR_CONFIRMATION, H.HOLD_WITH_CAUTION)
    assert cb.price_above_ma5 is False  # Use daily close, never unrelated realtime price.
    assert cb.support_status == 'TESTING'
    assert cb.base_support_probability == cb.adjusted_support_probability == .7


def test_wrong_zone_or_unready_probability_is_unknown():
    result, bars, prior = inputs()
    result['support_resistance']['bayesian_support_selected'].update(support_low=50., support_high=60.)
    assert build_decision_context(result, bars, previous_zones=prior).support_probability == 'UNKNOWN'
    result, bars, prior = inputs()
    result['support_resistance']['bayesian_support_selected']['display']['model_status'] = 'warmup'
    assert build_decision_context(result, bars, previous_zones=prior).support_probability == 'UNKNOWN'


def test_incomplete_bar_cannot_confirm_and_no_volume_is_unknown():
    result, bars, prior = inputs()
    now = datetime(2026, 9, 14, 12, tzinfo=ZoneInfo('Asia/Taipei'))
    c = build_decision_context(result, bars.drop(columns='Volume'), previous_zones=prior, now=now)
    assert not c.observation_complete
    assert c.volume_state == 'UNKNOWN'


@pytest.mark.parametrize('price,previous,high,expected', [
    (102.,103.,103.,'APPROACHING'), (100.,103.,103.,'TESTING'),
    (102.,100.,103.,'HOLDING'), (97.5,100.,100.,'MINOR_BREAK'),
    (96.,100.,97.,'CONFIRMED_BREAK'), (100.,97.,101.,'RECLAIMED'),
    (96.,97.,99.,'FLIPPED_TO_RESISTANCE')])
def test_support_states_from_existing_zone(price, previous, high, expected):
    result, bars, prior = inputs()
    result['close'] = price
    bars.loc[bars.index[-2], 'Close'] = previous
    bars.loc[bars.index[-1], ['Close', 'High']] = [price, high]
    assert build_decision_context(result, bars, previous_zones=prior).support_status == expected


def test_context_debug_and_normal_format():
    from app.analysis.timeframe_summary import format_timeframe_discord
    from tests.test_decision_engine import bullish
    from app.decision_formatter import format_operation_reference
    c = bullish()
    d = DecisionEngine().evaluate(c)
    # Empty timeframe loop is not accepted by the formatter; use real analytical fixtures.
    from tests.test_timeframe_analysis import _market_data
    from app.analysis import analyze_timeframes
    tf = analyze_timeframes(_market_data())
    tf.update(operation_reference=format_operation_reference(d), trading_decision=asdict(d), decision_context=asdict(c))
    normal = '\n'.join(format_timeframe_discord(tf))
    debug = '\n'.join(format_timeframe_discord(tf, debug=True))
    assert '[DEBUG]' not in normal and 'entry_score' not in normal
    assert '[DEBUG]' in debug and 'adjusted_support_probability' in debug


def test_old_observations_do_not_rewind_saved_state():
    result, bars, prior = inputs()
    c = build_decision_context(result, bars, previous_zones=prior)
    d = DecisionEngine().evaluate(c)
    _, state = stabilize(d, c, {})
    _, old = stabilize(d, replace(c, observation_time='2026-09-11'), state)
    assert state == old


def test_config_validation():
    with pytest.raises(ValueError):
        DecisionConfig(confirmed_break_atr=0)
    with pytest.raises(ValueError):
        DecisionConfig(confirmation_required=1)

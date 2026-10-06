import copy
import csv
import json
import sqlite3
from contextlib import closing

import numpy as np
import pandas as pd
import pytest

from app.backtest import runner
from app.backtest.runner import BacktestRunner, export_rows
from app.decision_state import update_monitor_decision
from app.institutional_flow.storage import FlowStore, ReadOnlyFlowStore


@pytest.fixture
def history():
    days = pd.bdate_range('2025-01-02', periods=100, tz='Asia/Taipei')
    close = 100 + np.arange(100) * .15 + np.sin(np.arange(100) / 3) * 3
    return pd.DataFrame(dict(Open=close - .3, High=close + 1.5, Low=close - 1.5,
                             Close=close, Volume=np.full(100, 100000)), index=days)


def replay(history, tmp_path, **kwargs):
    return BacktestRunner(institutional_db=tmp_path / 'missing.db').run(
        '2408', history.index[80].date(), history.index[83].date(),
        history=history, benchmark_history=history, **kwargs)


def test_future_prices_and_benchmark_cannot_change_past_decisions(history, tmp_path):
    end = history.index[83].date()
    prefix = history.iloc[:84].copy()
    expected = replay(prefix, tmp_path)
    future = history.copy()
    future.iloc[84:, :4] *= 7
    future.iloc[84:, 4] *= 20
    actual = replay(future, tmp_path)
    assert actual == expected
    assert actual[-1]['date'] == str(end)
    assert len(actual) == 4
    assert actual[1]['state_before'] == actual[0]['state_after']
    assert actual[1]['previous_action_state']['signal_state'] == actual[0]['signal']
    assert actual[0]['state_before'] == {}
    assert actual[-1]['state_after']['last_observation_time'] == str(end)
    assert all(row['as_of'].endswith('T13:30:00+08:00') for row in actual)


def test_no_live_io_or_state_pollution(history, tmp_path, monkeypatch):
    from app import database, decision_state, stock
    from app.institutional_flow.provider import OfficialFirstProvider
    live = tmp_path / 'live.db'
    with closing(sqlite3.connect(live)) as connection:
        connection.execute('CREATE TABLE sentinel (value TEXT)')
        connection.execute("INSERT INTO sentinel VALUES ('live state must survive')")
        connection.commit()
    before = live.read_bytes()
    monkeypatch.setattr(database, 'DATABASE_PATH', live)
    def forbidden(*args, **kwargs):
        raise AssertionError('Live I/O is forbidden during replay')
    monkeypatch.setattr(database, 'get_connection', forbidden)
    monkeypatch.setattr(decision_state, 'load_decision_state', forbidden)
    monkeypatch.setattr(stock, 'get_realtime_price', forbidden)
    monkeypatch.setattr(stock, 'get_market_change_percent', forbidden)
    monkeypatch.setattr(stock, 'attach_bayesian_support', forbidden)
    monkeypatch.setattr(OfficialFirstProvider, 'fetch', forbidden)
    monkeypatch.setattr(runner.yf, 'Ticker', forbidden)
    rows = BacktestRunner().run('2408', str(history.index[80].date()), str(history.index[81].date()),
                               history=history, benchmark_history=history)
    assert live.read_bytes() == before
    assert rows[-1]['state_after']['signal_state'] == rows[-1]['signal']
    assert rows[-1]['bayesian_status'] == 'unavailable_in_replay'
    assert rows[-1]['institutional_flow']['institutional_level'] == 'UNKNOWN'


def test_production_monitor_state_machine_parity(history, tmp_path, monkeypatch):
    reference = sqlite3.connect(':memory:')
    seen = []
    original = runner.update_monitor_decision
    def verify(data, **kwargs):
        expected = copy.deepcopy(data)
        update_monitor_decision(expected, connection=reference,
                                signal_observation_time=kwargs['signal_observation_time'])
        original(data, **kwargs)
        assert data['trading_decision'] == expected['trading_decision']
        assert runner.load_state(kwargs['connection'], '2408') == runner.load_state(reference, '2408')
        seen.append(data['date'])
    monkeypatch.setattr(runner, 'update_monitor_decision', verify)
    try:
        result = replay(history, tmp_path, position_status='HOLDING')
    finally:
        reference.close()
    assert len(seen) == len(result) == 4
    assert all(r['position_status'] == 'HOLDING' for r in result)
    assert all('trade_memory' in r['state_after'] for r in result)
    assert all('signal_persistence' in r['decision_snapshot'] for r in result)
    assert all('holder_structure_memory' in r['state_after'] for r in result)


def test_symbols_and_repeat_runs_have_independent_state(history, tmp_path):
    subject = BacktestRunner(institutional_db=tmp_path / 'absent.db')
    options = dict(start=str(history.index[80].date()), end=str(history.index[81].date()),
                   history=history, benchmark_history=history)
    a = subject.run('2408', **options)
    b = subject.run('2330', **options)
    assert a[0]['state_before'] == b[0]['state_before'] == {}
    assert subject.states['2408']['symbol'] == '2408'
    assert subject.states['2330']['symbol'] == '2330'
    assert subject.run('2408', **options) == a


def test_institutional_versions_require_availability_and_observation(tmp_path):
    path = tmp_path / 'flow.db'
    FlowStore(lambda: sqlite3.connect(path))
    # Use the real version selector: an older data date alone is insufficient.
    payload = dict(symbol='2408', market='TWSE', date='2025-01-02',
                   available_at='2025-01-02T18:00:00+08:00', foreign_net=100)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute('INSERT INTO institutional_flow_versions VALUES (?,?,?,?)',
            ('2408', '2025-01-02', '2025-01-03T08:00:00+08:00', json.dumps(payload)))
        revised = dict(payload, foreign_net=-900)
        connection.execute('INSERT INTO institutional_flow_versions VALUES (?,?,?,?)',
            ('2408', '2025-01-02', '2025-02-01T08:00:00+08:00', json.dumps(revised)))
        connection.commit()
    before = path.read_bytes()
    read_only = ReadOnlyFlowStore(path)
    assert read_only.history('2408', '2025-01-02T13:30:00+08:00') == []
    assert read_only.history('2408', '2025-01-02T23:00:00+08:00') == []
    assert read_only.history('2408', '2025-01-03T13:30:00+08:00')[0]['foreign_net'] == 100
    assert read_only.history('2408', '2025-02-03T13:30:00+08:00')[0]['foreign_net'] == -900
    with closing(read_only.connect()) as connection, pytest.raises(sqlite3.OperationalError):
        connection.execute('DELETE FROM institutional_flow_versions')
    assert path.read_bytes() == before


def test_missing_benchmark_is_unknown_and_bad_close_is_rejected(history, tmp_path):
    subject = BacktestRunner(institutional_db=tmp_path / 'absent.db')
    start, end = str(history.index[80].date()), str(history.index[81].date())
    rows = subject.run('2408', start, end, history=history, benchmark_history=pd.DataFrame())
    assert all(r['relative_market']['difference'] is None for r in rows)
    assert all(r['relative_market']['label'] == '資料不足' for r in rows)
    history.iloc[80, history.columns.get_loc('Close')] = float('nan')
    with pytest.raises(ValueError, match='收盤價無效'):
        subject.run('2408', start, end, history=history, benchmark_history=pd.DataFrame())


def test_short_history_does_not_advance_valid_decision_state(history, tmp_path):
    small = history.iloc[80:84]
    rows = BacktestRunner(
        institutional_db=tmp_path / 'absent.db').run('2408', str(small.index[0].date()),
        str(small.index[-1].date()), history=small, benchmark_history=pd.DataFrame())
    assert len(rows) == 4
    assert all(r['signal'] == '無法判斷' for r in rows)
    assert all(r['state_after'] == {} for r in rows)


def test_exports_preserve_unicode_and_structured_state(history, tmp_path):
    rows = replay(history, tmp_path)
    csv_path = export_rows(rows, tmp_path / 'out.csv')
    json_path = export_rows(rows, tmp_path / 'out.json')
    assert json.loads(json_path.read_text(encoding='utf-8')) == rows
    with csv_path.open(encoding='utf-8-sig', newline='') as stream:
        loaded = list(csv.DictReader(stream))
    assert loaded[0]['action'] == rows[0]['action']
    assert json.loads(loaded[-1]['state_after']) == rows[-1]['state_after']
    assert len(loaded) == 4


def test_download_is_bounded_and_includes_end_date(monkeypatch):
    calls = []
    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol
        def history(self, **kwargs):
            calls.append((self.symbol, kwargs))
            return pd.DataFrame() if self.symbol.endswith('.TW') else pd.DataFrame({'Close': [1]})
    monkeypatch.setattr(runner.yf, 'Ticker', Ticker)
    from datetime import date
    symbol, _ = runner.download_history('3211', date(2025, 1, 1), date(2025, 2, 1))
    assert symbol == '3211.TWO'
    assert [c[0] for c in calls] == ['3211.TW', '3211.TWO']
    assert all(c[1] == dict(start='2025-01-01', end='2025-02-02', interval='1d', auto_adjust=False) for c in calls)


def test_invalid_range_fails_before_download():
    def forbidden(*args):
        raise AssertionError('Must validate before download')
    subject = BacktestRunner(history_loader=forbidden)
    with pytest.raises(ValueError, match='開始日期'):
        subject.run('2408', '2025-02-01', '2025-01-01')
    with pytest.raises(ValueError, match='歷史日期'):
        subject.run('2408', '2999-01-01', '2999-02-01')


def test_holding_sell_transitions_explain_changed_evidence(history, tmp_path):
    benchmark = history.copy()
    history.iloc[84:, :4] *= np.linspace(.85, .6, 16)[:, None]
    history.iloc[84:, 4] *= 3
    rows = BacktestRunner(institutional_db=tmp_path / 'absent.db').run(
        '2408', str(history.index[80].date()), str(history.index[89].date()),
        history=history, benchmark_history=benchmark, position_status='HOLDING')
    changes = [r for r in rows if r['action_changed']]
    assert changes
    assert any(r['action_code'] in ('CONSIDER_REDUCE', 'REDUCE', 'EXIT') for r in changes)
    for row in changes:
        assert '→' in row['action_change_reason']
        assert row['decision_rule_codes']
        assert row['decision_snapshot']['trade_evidence']['contributions']
        assert row['state_before']


def test_cli_writes_real_pipeline_output(history, tmp_path, monkeypatch, capsys):
    from backtest import main
    calls = []
    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol
        def history(self, **kwargs):
            calls.append((self.symbol, kwargs))
            return history
    monkeypatch.setattr(runner.yf, 'Ticker', Ticker)
    output = tmp_path / 'cli.json'
    assert main(['--symbol', '2408', '--start', str(history.index[80].date()),
        '--end', str(history.index[81].date()), '--position-status', 'HOLDING',
        '--output', str(output)]) == 0
    rows = json.loads(output.read_text(encoding='utf-8'))
    assert len(rows) == 2
    assert rows[0]['position_status'] == 'HOLDING'
    assert [c[0] for c in calls] == ['2408.TW', '^TWII']
    assert '已回放 2 個交易日' in capsys.readouterr().out

"""Independent real-source fill/accounting audit and every-session prefix check."""
import argparse
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import importlib
import json
from pathlib import Path
import sys

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
bt = importlib.import_module('666_strategy.backtest')


def currency(value):
    return Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def assert_saved_fields(actual, saved):
    """Old snapshots remain valid contracts when descriptive fields are added."""
    if isinstance(saved, dict):
        for key, value in saved.items():
            assert key in actual, key
            assert_saved_fields(actual[key], value)
    elif isinstance(saved, list):
        assert len(actual) == len(saved)
        for current, previous in zip(actual, saved):
            assert_saved_fields(current, previous)
    else:
        assert actual == saved, (actual, saved)


def audit_file(path, provider, original):
    path = Path(path)
    saved = json.loads(path.read_text(encoding='utf-8'))
    data = bt.fetch_backtest_data(saved['symbol'], saved['start'], saved['end'], provider=provider)
    result = bt.run_backtest(data, config=bt.BacktestConfig(**saved['config']))
    assert_saved_fields(result.to_dict(), saved)
    expected_raw = [event for event in original['signal_analysis']['signals']
                    if data.start <= pd.Timestamp(event['signal_time']).date() <= data.end]
    assert result.raw_signals == expected_raw
    cash = currency(result.config['initial_cash'])
    entry_cost, shares = None, 0
    checks = 0
    for fill in result.fills:
        stamp = pd.Timestamp(fill['execution_time'])
        assert stamp.date() >= data.start and stamp.date() <= data.end
        assert fill['price'] == float(data.history.bars.loc[stamp, 'Open'])
        if fill['action'] == 'EXIT':
            trigger = pd.Timestamp(fill['trend_close_time'])
        else:
            raw = next(event for event in result.raw_signals if event['signal_time'] == fill['signal_time'])
            trigger = pd.Timestamp(raw['bar_end'])
            assert stamp > pd.Timestamp(raw['bar_start'])
        expected_bar = data.history.bars.index[data.history.bars.index >= trigger][0]
        assert stamp == expected_bar, 'Fill is not at the next canonical bar opening'
        gross = currency(Decimal(str(fill['price'])) * fill['shares'])
        fee = currency(max(Decimal(str(result.config['minimum_commission'])),
                           gross*Decimal(str(result.config['commission_rate']))))
        selling = fill['action'] != 'BUY'
        tax = currency(gross*Decimal(str(result.config['sell_tax_rate']))) if selling else currency(0)
        assert currency(fill['gross_amount']) == gross
        assert currency(fill['commission']) == fee
        assert currency(fill['transaction_tax']) == tax
        if not selling:
            assert shares == 0
            assert data.daily.snapshot_at(stamp)['status'] == 'BULL'
            entry_cost = gross+fee
            cash -= entry_cost
            shares = fill['shares']
        else:
            assert fill['shares'] == shares
            assert currency(fill['net_profit']) == gross-fee-tax-entry_cost
            cash += gross-fee-tax
            shares = 0
        assert cash == currency(fill['cash_after']) and cash >= 0
        checks += 1
    assert currency(result.summary['ending_cash']) == cash
    if shares:
        assert result.open_position['shares'] == shares
        market_value = currency(Decimal(str(data.history.bars.iloc[-1].Close))*shares)
    else:
        assert result.open_position is None
        market_value = currency(0)
    assert currency(result.summary['ending_equity']) == cash+market_value
    assert currency(result.summary['total_net_profit']) == cash+market_value-currency(result.config['initial_cash'])
    equity=np.array([result.config['initial_cash'],*[point['equity'] for point in result.equity_curve]],dtype=float)
    peaks=np.maximum.accumulate(equity)
    expected_drawdown=float(np.max((peaks-equity)/peaks)*100)
    assert abs(result.summary['max_drawdown_percent']-expected_drawdown)<1e-9
    durations=[(pd.Timestamp(t['exit_time'])-pd.Timestamp(t['entry_time']))/pd.Timedelta(days=1) for t in result.trades]
    if durations:
        assert abs(result.summary['average_holding_days']-float(np.mean(durations)))<1e-12
    else:
        assert result.summary['average_holding_days'] is None
    prefix_checks = 0
    for day in data.window.sessions:
        prefix_data = bt.fetch_backtest_data(data.symbol, data.start, day, provider=provider)
        prefix = bt.run_backtest(prefix_data, config=bt.BacktestConfig(**saved['config']))
        expected = [fill for fill in result.fills if pd.Timestamp(fill['execution_time']).date() <= day]
        assert prefix.fills == expected, f'Future data changed fills before {day}'
        expected_trades = [trade for trade in result.trades if pd.Timestamp(trade['exit_time']).date() <= day]
        assert prefix.trades == expected_trades
        prefix_checks += 1
    return dict(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        passed=True, fills_checked=checks, session_prefix_checks=prefix_checks,
        raw_signals_unchanged=True, metrics_independently_checked=True,
        summary=result.summary, open_position=result.open_position)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('results', nargs='+', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--protected-baseline', type=Path)
    args = parser.parse_args()
    provider = bt.SnapshotProvider(args.snapshot)
    source = json.loads(args.snapshot.read_text(encoding='utf-8'))
    originals = {item['symbol']: item for item in source['results']}
    reports = [audit_file(path, provider, originals[json.loads(path.read_text(encoding='utf-8'))['symbol']])
               for path in args.results]
    result = dict(passed=True, source=provider.metadata, reports=reports)
    if args.protected_baseline:
        before = json.loads(args.protected_baseline.read_text(encoding='utf-8'))
        changed = [name for name, digest in before.items() if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest]
        assert not changed, changed
        result['protected_files'] = dict(unchanged=True, checked=len(before), sha256=before)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(dict(passed=True, reports=[dict(file=r['file'], fills_checked=r['fills_checked'],
        session_prefix_checks=r['session_prefix_checks']) for r in reports]), indent=2))


if __name__ == '__main__':
    main()

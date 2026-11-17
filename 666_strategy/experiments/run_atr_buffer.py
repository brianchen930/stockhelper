"""Six-stock A vs ATR-band runs on the existing offline Fugle snapshots.

python -B -X utf8 -m 666_strategy.experiments.run_atr_buffer [--out DIR]

k values were fixed before running: 0.5 is the primary hypothesis, 0.25 and
1.0 are sensitivity checks only (not a parameter search). A must reproduce
the stored six_stock_summary.csv exactly before any variant is reported.
"""
import argparse
import csv
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..backtest import run_backtest
from . import run_tpex_ab as base
from .atr_buffer import run_buffered_backtest

K_VALUES = (0.25, 0.5, 1.0)
PRIMARY_K = 0.5
KEYS = ('ending_equity', 'total_return_percent', 'max_drawdown_percent', 'completed_trades',
        'winning_trades', 'win_rate_percent', 'commissions', 'transaction_taxes',
        'average_holding_days', 'buy_and_hold_return_percent')


def short_trade_stats(result):
    short = [t for t in result.trades if t['holding_days'] <= 3]
    return dict(short_trades=len(short), short_trade_net=round(sum(t['net_profit'] for t in short), 2))


def exposure(result):
    curve = [e for e in result.equity_curve if e['phase'] == 'CLOSE']
    return round(100*sum(e['shares'] > 0 for e in curve)/len(curve), 4)


def one(symbol):
    with base.offline():
        _, data = base.load(symbol)
        cfg = base.config()
        rows = []
        for label, k in [('A', 0)] + [(f'k={k}', k) for k in K_VALUES]:
            result = run_backtest(data, config=cfg) if k == 0 else run_buffered_backtest(data, k=k, config=cfg)
            s = result.summary
            row = dict(symbol=symbol, variant=label, k=k, **{key: s.get(key) for key in KEYS})
            row.update(total_costs=round(s['commissions']+s['transaction_taxes'], 2),
                       exposure_percent=exposure(result), raw_signals=len(result.raw_signals),
                       **short_trade_stats(result))
            rows.append(row)
        return rows


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=base.PROJECT/'666_strategy/verification/atr_buffer_20241030_20261029')
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    stored = {}
    with open(base.ROOT/'six_stock_summary.csv', encoding='utf-8-sig') as handle:
        for r in csv.DictReader(handle):
            if r['variant'] == 'A':
                stored[r['symbol']] = r
    with ProcessPoolExecutor(max_workers=6) as pool:
        rows = [row for chunk in pool.map(one, base.ALL) for row in chunk]
    parity = {}
    for row in rows:
        if row['variant'] == 'A':
            ref = stored[row['symbol']]
            parity[row['symbol']] = (abs(row['ending_equity']-float(ref['ending_equity'])) < 0.005
                                     and row['completed_trades'] == int(ref['completed_trades']))
    assert all(parity.values()), parity
    with open(args.out/'summary.csv', 'w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (args.out/'summary.json').write_text(json.dumps(dict(k_values=K_VALUES, primary_k=PRIMARY_K,
        a_parity_with_stored_results=parity, rows=rows), ensure_ascii=False, indent=2), encoding='utf-8')
    print('A parity:', parity)
    for row in rows:
        print(row['symbol'], row['variant'], round(row['total_return_percent'], 2), round(row['max_drawdown_percent'], 2),
              row['completed_trades'], row['total_costs'], row['short_trades'], row['short_trade_net'])


if __name__ == '__main__':
    main()

"""Daily BULL redefined by the user as: daily MA60 > daily MA120 (only).

python -B -X utf8 -m 666_strategy.experiments.run_ma_bull [--out DIR]
Production daily.py is untouched; this rebuilds trend_status on a copy of the
already-validated daily bars. Warm-up/invalid windows stay UNKNOWN and any
existing DAILY_INPUT_CONFLICT stays UNKNOWN.
A  original 666 (Close>MA60, MA60>MA120, MA60 rising 5 sessions)
N  new BULL, MA60 SELL + daily NON_BULL EXIT
N2 new BULL, MA60 SELL only
N3 new BULL, MA60 SELL + volume-top exit (user's stated method)
"""
import argparse
import csv
import json
from dataclasses import replace
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..backtest import run_backtest
from . import run_tpex_ab as base
from .run_volume_top_exit import summarize
from .volume_top_exit import run_variant


def ma_bull_daily(daily):
    bars = daily.bars.copy()
    known = bars.daily_ma60.notna() & bars.daily_ma120.notna()
    conflict = bars.trend_issue.eq('DAILY_INPUT_CONFLICT')
    bars['trend_status'] = 'UNKNOWN'
    bars.loc[known, 'trend_status'] = 'NON_BULL'
    bars.loc[known & (bars.daily_ma60 > bars.daily_ma120), 'trend_status'] = 'BULL'
    bars['trend_issue'] = None
    bars.loc[~known, 'trend_issue'] = 'WARMUP_OR_INVALID_DAILY_WINDOW'
    bars.loc[conflict, ['trend_status', 'trend_issue']] = ['UNKNOWN', 'DAILY_INPUT_CONFLICT']
    return replace(daily, bars=bars, metadata=dict(daily.metadata, bull_rule='daily MA60 > daily MA120'))


def one(symbol):
    with base.offline():
        _, data = base.load(symbol)
        cfg = base.config()
        rows = [summarize(symbol, 'A', run_backtest(data, config=cfg))]
        new = replace(data, daily=ma_bull_daily(data.daily))
        days = new.daily.bars.loc[new.daily.bars.index.date >= new.start]
        bull_share = round(100*days.trend_status.eq('BULL').mean(), 2)
        old_days = data.daily.bars.loc[data.daily.bars.index.date >= data.start]
        old_share = round(100*old_days.trend_status.eq('BULL').mean(), 2)
        rows.append(summarize(symbol, 'N', run_backtest(new, config=cfg)))
        for arm, (daily_exit, volume_exit) in {'N2': (False, False), 'N3': (False, True)}.items():
            result, _ = run_variant(new, base.source(symbol), daily_exit=daily_exit,
                                    volume_exit=volume_exit, config=cfg)
            rows.append(summarize(symbol, arm, result))
        for row in rows:
            row['bull_days_percent'] = old_share if row['variant'] == 'A' else bull_share
        return rows


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=base.PROJECT/'666_strategy/verification/ma_bull_20241030_20261029')
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    stored = {}
    with open(base.ROOT/'six_stock_summary.csv', encoding='utf-8-sig') as handle:
        for r in csv.DictReader(handle):
            if r['variant'] == 'A':
                stored[r['symbol']] = r
    with ProcessPoolExecutor(max_workers=6) as pool:
        rows = [r for chunk in pool.map(one, base.ALL) for r in chunk]
    parity = {r['symbol']: abs(r['ending_equity']-float(stored[r['symbol']]['ending_equity'])) < 0.005
              and r['completed_trades'] == int(stored[r['symbol']]['completed_trades'])
              for r in rows if r['variant'] == 'A'}
    assert all(parity.values()), parity
    with open(args.out/'summary.csv', 'w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (args.out/'summary.json').write_text(json.dumps(dict(bull_rule='daily MA60 > daily MA120',
        a_parity_with_stored_results=parity, rows=rows), ensure_ascii=False, indent=2), encoding='utf-8')
    print('A parity:', parity)
    for r in rows:
        print(r['symbol'], r['variant'], round(r['total_return_percent'], 2), round(r['max_drawdown_percent'], 2),
              r['completed_trades'], r['winning_trades'], r['total_costs'], r['exits_sell'], r['exits_daily'],
              r['exits_volume'], round(r['exposure_percent'], 1), r['bull_days_percent'],
              round(r['buy_and_hold_return_percent'], 2))


if __name__ == '__main__':
    main()

"""python -B -X utf8 -m 666_strategy.experiments.run_exit_reentry [--out DIR] [--n2 PATH]

Base (N2): daily MA60>MA120, raw MA60 crossings, no daily NON_BULL exit.
Must reproduce run_ma_bull N2 exactly before variants are reported.
"""
import argparse
import csv
import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

from . import run_tpex_ab as base
from .exit_reentry import run
from .run_ma_bull import ma_bull_daily
from .run_volume_top_exit import summarize

ARMS = {'N2': ('raw', False), 'E1': ('two_bar', False), 'E2': ('atr', False),
        'R': ('raw', True), 'E1+R': ('two_bar', True), 'E2+R': ('atr', True)}


def short(result):
    s = [t for t in result.trades if t['holding_days'] <= 3]
    return dict(short_trades=len(s), short_trade_net=round(sum(t['net_profit'] for t in s), 2))


def one(symbol):
    with base.offline():
        _, data = base.load(symbol)
        cfg = base.config()
        data = replace(data, daily=ma_bull_daily(data.daily))
        rows = []
        for arm, (exit, reentry) in ARMS.items():
            result = run(data, exit=exit, reentry=reentry, config=cfg)
            row = summarize(symbol, arm, result)
            row.update(short(result), reentry_buys=sum(s.get('origin') == 'DAILY_TURN_REENTRY'
                                                       for s in result.raw_signals),
                       open_at_end=result.open_position is not None)
            rows.append(row)
        return rows


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=base.PROJECT/'666_strategy/verification/exit_reentry_20241030_20261029')
    parser.add_argument('--n2', type=Path, default=base.PROJECT/'666_strategy/verification/ma_bull_20241030_20261029/summary.json')
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    stored = {r['symbol']: r for r in json.loads(args.n2.read_text(encoding='utf-8'))['rows'] if r['variant'] == 'N2'}
    with ProcessPoolExecutor(max_workers=6) as pool:
        rows = [r for chunk in pool.map(one, base.ALL) for r in chunk]
    parity = {r['symbol']: abs(r['ending_equity']-stored[r['symbol']]['ending_equity']) < 0.005
              and r['completed_trades'] == stored[r['symbol']]['completed_trades'] for r in rows if r['variant'] == 'N2'}
    assert all(parity.values()), parity
    with open(args.out/'summary.csv', 'w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (args.out/'summary.json').write_text(json.dumps(dict(n2_parity=parity, arms=ARMS, rows=rows),
                                                    ensure_ascii=False, indent=2), encoding='utf-8')
    print('N2 parity:', parity)
    for r in rows:
        print(r['symbol'], r['variant'], round(r['total_return_percent'], 2), round(r['max_drawdown_percent'], 2),
              r['completed_trades'], r['winning_trades'], r['total_costs'], round(r['exposure_percent'], 1),
              r['short_trades'], r['short_trade_net'], r['reentry_buys'])


if __name__ == '__main__':
    main()

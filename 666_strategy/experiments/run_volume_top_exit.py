"""Six stocks x four arms; definitions fixed before running.

python -B -X utf8 -m 666_strategy.experiments.run_volume_top_exit [--out DIR]
A  original 666 (MA60 SELL + daily NON_BULL EXIT)
A2 original without daily NON_BULL EXIT
C  original + volume-top exit
D  volume-top exit + MA60 SELL, no daily NON_BULL EXIT
"""
import argparse
import csv
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..backtest import run_backtest
from . import run_tpex_ab as base
from .volume_top_exit import TAG, run_variant

ARMS = {'A2': (False, False), 'C': (True, True), 'D': (False, True)}
KEYS = ('ending_equity', 'total_return_percent', 'max_drawdown_percent', 'completed_trades',
        'winning_trades', 'win_rate_percent', 'commissions', 'transaction_taxes',
        'average_holding_days', 'buy_and_hold_return_percent')


def exposure(result):
    curve = [e for e in result.equity_curve if e['phase'] == 'CLOSE']
    return round(100*sum(e['shares'] > 0 for e in curve)/len(curve), 4)


def summarize(symbol, arm, result):
    s = result.summary
    row = dict(symbol=symbol, variant=arm, **{k: s.get(k) for k in KEYS})
    reasons = [f['decision_id'] for f in result.fills if f['action'] != 'BUY']
    row.update(total_costs=round(s['commissions']+s['transaction_taxes'], 2), exposure_percent=exposure(result),
               exits_sell=sum(':SELL:' in r for r in reasons),
               exits_daily=sum(':EXIT:' in r and TAG not in r for r in reasons),
               exits_volume=sum(TAG in r for r in reasons))
    return row


def same_position(reference, exit_time):
    """Trades of the reference arm (A) still holding when the volume exit fired."""
    return [[t['entry_time'][:16], t['exit_time'][:16], t['exit_price'], round(t['net_profit'])]
            for t in reference.trades if t['entry_time'] <= exit_time < t['exit_time']]


def next_entry(result, exit_time):
    buy = next((f for f in result.fills if f['action'] == 'BUY' and f['execution_time'] > exit_time), None)
    return None if buy is None else f"{buy['execution_time'][:16]} @{buy['price']}"


def volume_exit_details(symbol, result, table, reference=None):
    rows = []
    closes = table.Close
    dates = list(table.index)
    for t in result.trades:
        fill = next(f for f in result.fills if f['trade_id'] == t['trade_id'] and f['action'] != 'BUY')
        if TAG not in fill['decision_id']:
            continue
        signal_day = fill['decision_id'].split(TAG+':')[1][:10]
        exit_day = t['exit_time'][:10]
        i = [d.isoformat() for d in dates].index(exit_day)
        window = closes.iloc[i:i+21]
        sig = table.loc[[d for d in dates if d.isoformat() == signal_day][0]]
        rows.append(dict(symbol=symbol, signal_day=signal_day, exit_time=t['exit_time'], entry_price=t['entry_price'],
            exit_price=t['exit_price'], net_profit=round(t['net_profit'], 2),
            close_20d_later=float(window.iloc[-1]), max_close_next_20d=float(window.max()),
            min_close_next_20d=float(window.min()),
            change_20d_percent=round((window.iloc[-1]/t['exit_price']-1)*100, 2),
            volume_ratio=round(float(sig.Volume/sig.avg_vol20_prev), 2),
            pattern='long_upper_shadow' if sig.long_upper_shadow else 'long_black',
            above_ma20_percent=round(float((sig.Close/sig.ma20-1)*100), 2)))
        if reference is not None:
            rows[-1].update(A_same_position=same_position(reference, t['exit_time']),
                            C_next_entry=next_entry(result, t['exit_time']))
    return rows


def one(symbol):
    with base.offline():
        _, data = base.load(symbol)
        cfg = base.config()
        a = run_backtest(data, config=cfg)
        rows, details, c_details = [summarize(symbol, 'A', a)], [], []
        for arm, (daily_exit, volume_exit) in ARMS.items():
            result, table = run_variant(data, base.source(symbol), daily_exit=daily_exit,
                                        volume_exit=volume_exit, config=cfg)
            rows.append(summarize(symbol, arm, result))
            if volume_exit:
                for item in volume_exit_details(symbol, result, table):
                    details.append(dict(item, variant=arm))
            if arm == 'C':
                c_details = volume_exit_details(symbol, result, table, reference=a)
        signal_days = [d.isoformat() for d in table.index[table.signal.fillna(False)]
                       if base.START <= d.isoformat() <= base.END]
        return rows, details, dict(symbol=symbol, signal_days_in_period=signal_days), c_details


def write(path, rows):
    with open(path, 'w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path,
                        default=base.PROJECT/'666_strategy/verification/volume_top_exit_20241030_20261029')
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    stored = {}
    with open(base.ROOT/'six_stock_summary.csv', encoding='utf-8-sig') as handle:
        for r in csv.DictReader(handle):
            if r['variant'] == 'A':
                stored[r['symbol']] = r
    with ProcessPoolExecutor(max_workers=6) as pool:
        chunks = list(pool.map(one, base.ALL))
    rows = [r for c in chunks for r in c[0]]
    details = [r for c in chunks for r in c[1]]
    # C-arm volume exits, each compared with A's position held at that moment and C's next BUY.
    c_details = [r for c in chunks for r in c[3]]
    parity = {r['symbol']: abs(r['ending_equity']-float(stored[r['symbol']]['ending_equity'])) < 0.005
              and r['completed_trades'] == int(stored[r['symbol']]['completed_trades'])
              for r in rows if r['variant'] == 'A'}
    assert all(parity.values()), parity
    write(args.out/'summary.csv', rows)
    if details:
        write(args.out/'volume_exits.csv', details)
    (args.out/'summary.json').write_text(json.dumps(dict(a_parity_with_stored_results=parity, rows=rows,
        volume_exits=details, signal_days=[c[2] for c in chunks]), ensure_ascii=False, indent=2), encoding='utf-8')
    (args.out/'volume_exit_details.json').write_text(json.dumps(c_details, ensure_ascii=False, indent=1),
                                                     encoding='utf-8')
    print('A parity:', parity)
    for r in rows:
        print(r['symbol'], r['variant'], round(r['total_return_percent'], 2), round(r['max_drawdown_percent'], 2),
              r['completed_trades'], r['total_costs'], r['exits_sell'], r['exits_daily'], r['exits_volume'],
              round(r['exposure_percent'], 1))
    for d in details:
        print(d)


if __name__ == '__main__':
    main()

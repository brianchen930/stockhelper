"""Time the real detector on one saved history, without network access."""
import argparse
import json
from pathlib import Path
import platform
from time import perf_counter

import pandas as pd

from .support_backtest import backtest_support_events


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('history', type=Path)
    parser.add_argument('--symbol', default='2408')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    data = pd.read_csv(args.history, index_col=0, parse_dates=True)
    if len(data) < 2000:
        parser.error('At least 2000 saved bars are required; no synthetic padding is used')
    if args.output.exists():
        parser.error('Output already exists; choose a new path')
    results = []
    for count in (500, 1000, 2000):
        started = perf_counter()
        events = backtest_support_events(data.iloc[-count:], args.symbol)
        elapsed = perf_counter() - started
        results.append(dict(bars=count, seconds=elapsed, events=len(events)))
        print(f'{count} bars: {elapsed:.3f}s, {len(events)} events', flush=True)
    report = dict(symbol=args.symbol, history=str(args.history), python=platform.python_version(),
                  platform=platform.platform(), pandas=pd.__version__, repeats=1,
                  includes_network=False, detector='SupportResistanceEngine (unmodified)', results=results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as file:
        json.dump(report, file, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()

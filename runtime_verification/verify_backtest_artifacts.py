"""Independent CSV outcome audit; run from the 台股 directory."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    report = json.loads((args.directory / 'report.json').read_text(encoding='utf-8'))
    config = report['metadata']['config']
    events = pd.read_csv(args.directory / 'support_events.csv')
    history = pd.read_csv(args.directory / 'history.csv', index_col=0, parse_dates=True)
    for event in events.itertuples():
        i = event.event_index
        assert pd.Timestamp(event.event_date) == history.index[i]
        assert pd.Timestamp(event.support_as_of) == history.index[i - 1]
        window = history.iloc[i + 1:i + 1 + config['lookahead_bars']]
        assert len(window) == config['lookahead_bars']
        success = np.flatnonzero(window.High.to_numpy() >= event.entry_close + config['success_rebound_atr'] * event.atr)
        failure = np.flatnonzero(window.Close.to_numpy() < event.support_low - config['failure_breakdown_atr'] * event.atr)
        first_success = int(success[0]) if len(success) else float('inf')
        first_failure = int(failure[0]) if len(failure) else float('inf')
        if first_success == first_failure == float('inf'):
            expected = 'neutral'
        elif first_success == first_failure:
            expected = config['same_bar_policy']
        else:
            expected = 'success' if first_success < first_failure else 'failure'
        assert event.label == expected
        for offset, bar, first in ((event.bars_to_success, event.success_bar, first_success),
                                    (event.bars_to_failure, event.failure_bar, first_failure)):
            if first == float('inf'):
                assert pd.isna(offset) and pd.isna(bar)
            else:
                assert offset == first + 1 and bar == i + first + 1
        assert np.isclose(event.entry_close, history.Close.iloc[i])
        assert np.isclose(event.future_max_price, window.High.max())
        assert np.isclose(event.future_min_price, window.Low.min())
        assert np.isclose(event.max_rebound_atr, (window.High.max() - event.entry_close) / event.atr)
        assert np.isclose(event.max_breakdown_atr, (window.Low.min() - event.support_low) / event.atr)
    baseline = json.loads(Path('runtime_verification/backtest_source_baseline.json').read_text(encoding='utf-8'))
    changed = [name for name, digest in baseline.items()
               if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest]
    assert not changed, changed
    audit = dict(audited_events=len(events), outcomes_match=True, positions_match=True,
                 extrema_match=True, original_application_files_unchanged=True,
                 csv_utf8_sig=(args.directory / 'support_events.csv').read_bytes().startswith(b'\xef\xbb\xbf'))
    with (args.directory / 'verification.json').open('x', encoding='utf-8') as file:
        json.dump(audit, file, indent=2)
    print(json.dumps(audit))


if __name__ == '__main__':
    main()

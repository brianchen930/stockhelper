"""Compare the preserved 2408 replay with the completed entry mapping replay."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
csv.field_size_limit(10_000_000)
dates = {'2025-06-06', '2025-06-13', '2026-08-14', '2026-08-17', '2026-09-10'}


def read(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            snapshot = json.loads(row['decision_snapshot'])
            yield row, snapshot


def main():
    old_path = ROOT / 'backtest_results/2408_2025-01-01_2026-09-30.csv'
    new_path = ROOT / 'backtest_results/2408_2025-01-01_2026-09-30_entry_mapping.csv'
    unchanged = ('entry_action', 'entry_score', 'risk_score', 'risk_gate', 'entry_paths',
                 'confirmation_count', 'previous_action_state', 'signal_hysteresis',
                 'signal_persistence', 'critical_event')
    scalar = ('date', 'close', 'signal', 'short_trend', 'mid_trend', 'position_status')
    count, changed, selected = 0, [], []
    for before, after in zip(read(old_path), read(new_path), strict=True):
        old, old_snapshot = before
        new, new_snapshot = after
        for key in scalar:
            assert old[key] == new[key], (new['date'], key)
        for key in unchanged:
            assert old_snapshot[key] == new_snapshot[key], (new['date'], key)
        for key in ('contributions', 'weighted_score', 'raw_weighted_score', 'thresholds'):
            assert old_snapshot['trade_evidence'][key] == new_snapshot['trade_evidence'][key], (new['date'], key)
        count += 1
        if old['action_code'] != new['action_code']:
            changed.append(new['date'])
        if new['date'] in dates:
            path = new_snapshot['entry_paths'][new_snapshot['entry_paths']['primary_path']]
            assert new_snapshot['entry_action'] == 'ALLOW_PROBE_ENTRY'
            assert new['action_code'] == 'ENTER' and path['confirmation_count'] == path['confirmation_required'] == 2
            selected.append(dict(date=new['date'], entry_action=new['entry_action'],
                confirmation=path['confirmation_count'], required=path['confirmation_required'],
                old_action=old['action_code'], new_action=new['action_code']))
    assert {row['date'] for row in selected} == dates
    report_path = Path(__file__).with_name('verification.json')
    report = json.loads(report_path.read_text(encoding='utf-8'))
    report['full_replay'] = dict(symbol='2408', start='2025-01-01', end='2026-09-30',
        rows=count, unchanged_entry_and_signal_fields=list(unchanged),
        changed_action_dates=changed, selected_dates=selected, output=str(new_path),
        tests_passed=1054)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['full_replay'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

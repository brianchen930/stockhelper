"""Position decisions, execution-time rechecks and next-session exits; no P&L."""
from contextlib import redirect_stdout
from datetime import timedelta
import importlib
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from test_daily_trend import package, daily_fixture, analyze, clock
from test_signals import manual

cli = importlib.import_module('666_strategy.__main__')


def signal(at, kind='BUY'):
    return dict(symbol='2330.TW', signal_type=kind, signal_time=at.isoformat(),
                bar_end=at.isoformat(), bar_start=(at-timedelta(hours=1)).isoformat(),
                close=101., ma60=100.)


class PositionDecisionTests(unittest.TestCase):
    def setUp(self):
        self.raw, self.cal = daily_fixture()
        self.daily = analyze(self.raw, self.cal, clock(self.cal, 154))
        self.flat = package.PositionState('2330.TW')
        self.at = clock(self.cal, 125, 10, 0)

    def evaluate(self, state=None, at=None, daily=None, event=None, calendar=None):
        return package.evaluate_decision(state or self.flat, at=at if at is not None else self.at,
            daily=daily or self.daily, calendar=calendar or self.cal, signal=event)

    def confirm(self, result, at=None, daily=None, calendar=None):
        return package.confirm_execution(result.state, decision_id=result.decision.decision_id,
            at=at if at is not None else self.at, daily=daily or self.daily, calendar=calendar or self.cal)

    def test_bull_allows_only_a_new_buy_and_confirmation_changes_position(self):
        self.assertIsNone(self.evaluate().decision)
        result = self.evaluate(event=signal(self.at))
        self.assertEqual(result.decision.action, 'BUY')
        self.assertFalse(result.state.holding)
        fill = self.confirm(result)
        self.assertTrue(fill.executed)
        self.assertTrue(fill.state.holding)
        repeated = package.confirm_execution(fill.state, decision_id=result.decision.decision_id,
            at=self.at, daily=self.daily, calendar=self.cal)
        self.assertFalse(repeated.executed)

    def test_non_bull_and_unknown_block_buy(self):
        for prices, day, expected in [([100]*150, 125, 'NON_BULL'), (list(range(100, 250)), 50, 'UNKNOWN')]:
            raw, cal = daily_fixture(prices)
            at = clock(cal, day, 10, 0)
            result = self.evaluate(at=at, daily=analyze(raw, cal), event=signal(at))
            self.assertIsNone(result.decision)
            self.assertEqual(result.reason, 'BUY_BLOCKED_' + expected)

    def test_sell_is_unfiltered_including_unknown(self):
        for prices, day in [([100]*150, 125), (list(range(100, 250)), 50)]:
            raw, cal = daily_fixture(prices)
            at = clock(cal, day, 10, 0)
            daily = analyze(raw, cal)
            # NON_BULL holding uses EXIT (same exit effect, one pending decision).
            result = self.evaluate(package.PositionState('2330.TW', holding=True),
                at=at, daily=daily, event=signal(at, 'SELL'))
            self.assertIn(result.decision.action, ('SELL', 'EXIT'))
            self.assertTrue(self.confirm(result, at=at, daily=daily).executed)

    def test_unknown_does_not_force_exit(self):
        at = clock(self.cal, 50, 10, 0)
        result = self.evaluate(package.PositionState('2330.TW', holding=True), at=at)
        self.assertEqual(result.trend['status'], 'UNKNOWN')
        self.assertIsNone(result.decision)
        self.assertTrue(result.state.holding)

    def broken_trend(self):
        raw = self.raw.copy()
        raw.iloc[125, 0] = 1
        return analyze(raw, self.cal, clock(self.cal, 154))

    def test_failure_at_daily_close_schedules_next_open_not_same_close(self):
        daily = self.broken_trend()
        state = self.evaluate(package.PositionState('2330.TW', holding=True),
                              at=clock(self.cal, 125, 13, 29), daily=daily).state
        closed = clock(self.cal, 125)
        result = self.evaluate(state, at=closed, daily=daily)
        self.assertEqual(result.decision.action, 'EXIT')
        self.assertEqual(result.decision.execute_after, clock(self.cal, 126, 9, 0).isoformat())
        self.assertFalse(self.confirm(result, at=closed, daily=daily).executed)
        self.assertFalse(self.confirm(result, at=clock(self.cal, 126, 8, 59), daily=daily).executed)
        fill = self.confirm(result, at=clock(self.cal, 126, 9, 0), daily=daily)
        self.assertTrue(fill.executed)
        self.assertFalse(fill.state.holding)

    def test_each_of_three_failures_forces_exit(self):
        cases = [[100+i for i in range(119)] + [150],
                 [300]*60 + [50]*65 + list(range(100, 110)),
                 [50]*60 + [200-i for i in range(70)] + [180]]
        for prices in cases:
            raw, cal = daily_fixture(prices)
            at = clock(cal, len(prices)-1)
            result = self.evaluate(package.PositionState('2330.TW', holding=True),
                                   at=at, daily=analyze(raw, cal))
            self.assertEqual(result.decision.action, 'EXIT')

    def test_exit_skips_weekend_and_explicit_holiday(self):
        failure = next(i for i in range(120, 140) if self.cal.sessions[i].weekday() == 4)
        removed = self.cal.sessions[failure+1]
        days = tuple(day for day in self.cal.sessions if day != removed)
        calendar = package.SessionCalendar(days, self.cal.start_date, self.cal.through_date)
        raw = self.raw.drop(self.raw.index[failure+1]).copy()
        raw.loc[self.raw.index[failure], 'Close'] = 1
        daily = analyze(raw, calendar, clock(self.cal, 149))
        result = self.evaluate(package.PositionState('2330.TW', holding=True),
            at=clock(self.cal, failure), daily=daily, calendar=calendar)
        self.assertEqual(result.decision.execute_after, clock(self.cal, failure+2, 9, 0).isoformat())

    def test_exit_and_raw_sell_create_one_pending_exit_and_one_execution(self):
        daily = self.broken_trend()
        at = clock(self.cal, 125)
        result = self.evaluate(package.PositionState('2330.TW', holding=True),
            at=at, daily=daily, event=signal(at, 'SELL'))
        self.assertEqual(result.decision.action, 'EXIT')
        opened = clock(self.cal, 126, 9, 0)
        fill = self.confirm(result, at=opened, daily=daily)
        again = package.confirm_execution(fill.state, decision_id=result.decision.decision_id,
            at=opened, daily=daily, calendar=self.cal)
        self.assertFalse(again.executed)
        sell_at = clock(self.cal, 126, 10, 0)
        self.assertIsNone(self.evaluate(fill.state, at=sell_at, daily=daily,
                                       event=signal(sell_at, 'SELL')).decision)

    def test_pending_exit_survives_recovery_and_unknown(self):
        daily = self.broken_trend()
        result = self.evaluate(package.PositionState('2330.TW', holding=True),
            at=clock(self.cal, 125), daily=daily)
        later = self.evaluate(result.state, at=clock(self.cal, 127, 9, 0), daily=daily)
        self.assertEqual(later.trend['status'], 'BULL')
        self.assertEqual(later.decision.decision_id, result.decision.decision_id)
        raw = self.raw.copy()
        raw.iloc[126, 0] = float('nan')
        unknown = analyze(raw, self.cal, clock(self.cal, 149))
        later = self.evaluate(result.state, at=clock(self.cal, 127, 9, 0), daily=unknown)
        self.assertEqual(later.trend['status'], 'UNKNOWN')
        self.assertEqual(later.decision.decision_id, result.decision.decision_id)

    def test_skipped_clock_events_still_detect_intervening_failed_day(self):
        daily = self.broken_trend()
        state = self.evaluate(package.PositionState('2330.TW', holding=True),
                              at=clock(self.cal, 125, 10, 0), daily=daily).state
        result = self.evaluate(state, at=clock(self.cal, 128, 10, 0), daily=daily)
        self.assertEqual(result.trend['status'], 'BULL')
        self.assertEqual(result.decision.action, 'EXIT')
        self.assertEqual(result.decision.trend_close_time, clock(self.cal, 125).isoformat())

    def test_buy_rechecked_at_fill_cancels_on_failure_and_requires_new_buy(self):
        daily = self.broken_trend()
        pending = self.evaluate(at=self.at, daily=daily, event=signal(self.at))
        failed_at = clock(self.cal, 126, 9, 0)
        fill = self.confirm(pending, at=failed_at, daily=daily)
        self.assertFalse(fill.executed)
        self.assertFalse(fill.state.holding)
        recovered = clock(self.cal, 127, 10, 0)
        result = self.evaluate(fill.state, at=recovered, daily=daily)
        self.assertEqual(result.trend['status'], 'BULL')
        self.assertIsNone(result.decision)
        self.assertEqual(self.evaluate(result.state, at=recovered, daily=daily,
            event=signal(recovered)).decision.action, 'BUY')

    def test_fill_after_recovery_cannot_revive_buy_that_crossed_failed_day(self):
        daily = self.broken_trend()
        pending = self.evaluate(daily=daily, event=signal(self.at))
        fill = self.confirm(pending, at=clock(self.cal, 128, 10, 0), daily=daily)
        self.assertFalse(fill.executed)
        self.assertIsNone(fill.state.pending)

    def test_unknown_at_fill_cancels_buy(self):
        result = self.evaluate(event=signal(self.at))
        raw = self.raw.copy()
        raw.iloc[125, 0] = float('nan')
        daily = analyze(raw, self.cal, clock(self.cal, 149))
        self.assertFalse(self.confirm(result, at=clock(self.cal, 126, 9, 0), daily=daily).executed)

    def test_blocked_buy_not_reused_after_recovery(self):
        daily = self.broken_trend()
        at = clock(self.cal, 126, 10, 0)
        blocked = self.evaluate(at=at, daily=daily, event=signal(at))
        self.assertIsNone(blocked.decision)
        later = clock(self.cal, 127, 10, 0)
        self.assertIsNone(self.evaluate(blocked.state, at=later, daily=daily).decision)

    def test_tail_bar_buy_waits_until_next_open_and_rechecks_completed_daily_close(self):
        at = clock(self.cal, 125)
        result = self.evaluate(at=at, event=signal(at))
        self.assertEqual(result.decision.execute_after, clock(self.cal, 126, 9, 0).isoformat())
        self.assertFalse(self.confirm(result, at=at).executed)
        self.assertTrue(self.confirm(result, at=clock(self.cal, 126, 9, 0)).executed)
        # A 13:30 raw BUY sees the just-completed failure, not yesterday's BULL.
        failed = self.evaluate(at=at, daily=self.broken_trend(), event=signal(at))
        self.assertIsNone(failed.decision)

    def test_no_next_session_known_means_exit_pending_without_execution(self):
        calendar = package.SessionCalendar(self.cal.sessions[:126], self.cal.start_date, self.cal.sessions[125])
        result = self.evaluate(package.PositionState('2330.TW', holding=True),
            at=clock(self.cal, 125), daily=self.broken_trend(), calendar=calendar)
        self.assertIsNone(result.decision.execute_after)
        self.assertFalse(self.confirm(result, at=clock(self.cal, 125),
                                      daily=self.broken_trend(), calendar=calendar).executed)
        self.assertTrue(self.confirm(result, at=clock(self.cal, 126, 9, 0),
                                     daily=self.broken_trend()).executed)

    def test_consumed_buy_cannot_reopen_after_exit_at_same_time(self):
        pending = self.evaluate(event=signal(self.at))
        fill = self.confirm(pending)
        repeated = self.evaluate(fill.state, event=signal(self.at))
        self.assertIsNone(repeated.decision)
        self.assertEqual(repeated.reason, 'SIGNAL_ALREADY_CONSUMED')

    def test_raw_sell_cancels_unfilled_buy(self):
        buy = self.evaluate(event=signal(self.at))
        at = self.at + timedelta(hours=1)
        result = self.evaluate(buy.state, at=at, event=signal(at, 'SELL'))
        self.assertIsNone(result.decision)

    def test_time_order_symbol_and_signal_time_are_checked(self):
        result = self.evaluate(event=signal(self.at))
        with self.assertRaises(ValueError):
            self.evaluate(result.state, at=self.at-timedelta(seconds=1))
        with self.assertRaises(ValueError):
            self.evaluate(package.PositionState('2408.TW'))
        with self.assertRaises(ValueError):
            self.evaluate(event=signal(self.at-timedelta(days=1)))

    def test_pending_exit_restores_from_json_and_remains_idempotent(self):
        daily = self.broken_trend()
        result = self.evaluate(package.PositionState('2330.TW', holding=True),
                               at=clock(self.cal, 125), daily=daily)
        restored = package.PositionState.from_dict(json.loads(json.dumps(result.state.to_dict())))
        self.assertEqual(restored, result.state)
        filled = package.confirm_execution(restored, decision_id=result.decision.decision_id,
            at=clock(self.cal, 126, 9, 0), daily=daily, calendar=self.cal)
        self.assertTrue(filled.executed)
        self.assertFalse(package.confirm_execution(filled.state, decision_id=result.decision.decision_id,
            at=clock(self.cal, 126, 9, 0), daily=daily, calendar=self.cal).executed)

    def test_execution_without_current_daily_coverage_blocks_buy(self):
        result = self.evaluate(event=signal(self.at))
        stale = analyze(self.raw, self.cal, self.at)
        self.assertFalse(self.confirm(result, at=self.at+timedelta(hours=1), daily=stale).executed)

    def test_sell_closes_holding_before_later_trend_failure_without_second_exit(self):
        daily = self.broken_trend()
        result = self.evaluate(package.PositionState('2330.TW', holding=True),
            event=signal(self.at, 'SELL'), daily=daily)
        self.assertEqual(result.decision.action, 'SELL')
        filled = self.confirm(result, daily=daily)
        later = self.evaluate(filled.state, at=clock(self.cal, 125), daily=daily)
        self.assertIsNone(later.decision)
        self.assertFalse(later.state.holding)

    def test_adapter_keeps_raw_signal_and_60m_average_unchanged(self):
        history = manual([99, 101])
        # Move this isolated crossing to the mature daily-data range.
        history.bars.index = pd.DatetimeIndex([self.at-timedelta(hours=2), self.at-timedelta(hours=1)])
        history.bars['bar_end'] = history.bars.index + timedelta(hours=1)
        history.metadata['as_of'] = self.at.isoformat()
        signals = package.generate_signals(history)
        original = signals.bars.copy(deep=True)
        for daily in (self.daily, analyze(*daily_fixture([100]*150))):
            package.evaluate_strategy(signals, daily, self.flat, at=self.at, calendar=self.cal)
            pd.testing.assert_frame_equal(signals.bars, original)
            self.assertEqual(signals.latest_signal['signal_type'], 'BUY')
        later = self.at + timedelta(hours=1)
        result = package.evaluate_strategy(signals, self.daily, self.flat, at=later, calendar=self.cal)
        self.assertIsNone(result.decision)
        json.dumps(result.to_dict(), allow_nan=False)

    def test_decision_prefix_matches_full_future_daily_values(self):
        daily = self.broken_trend()
        state = self.flat
        prefix_state = self.flat
        for at, kind in [(self.at, 'BUY'), (clock(self.cal, 125), None),
                         (clock(self.cal, 126, 9, 0), None), (clock(self.cal, 127, 10, 0), 'BUY')]:
            event = signal(at, kind) if kind else None
            full = self.evaluate(state, at=at, daily=daily, event=event)
            raw = self.raw.copy()
            raw.iloc[125, 0] = 1
            prefix = self.evaluate(prefix_state, at=at, daily=analyze(raw, self.cal, at), event=event)
            self.assertEqual(full.to_dict(), prefix.to_dict())
            state, prefix_state = full.state, prefix.state


class DecisionCliTests(unittest.TestCase):
    def test_optional_decisions_preserve_raw_signal_and_separate_daily_output(self):
        import yfinance as yf
        raw, calendar = daily_fixture()
        at = clock(calendar, 125, 11, 0)
        history = manual([99, 101])
        history.bars.index = pd.DatetimeIndex([at-timedelta(hours=2), at-timedelta(hours=1)])
        history.bars['bar_end'] = history.bars.index + timedelta(hours=1)
        history.metadata.update(as_of=at.isoformat(), request_start=history.bars.index[0].isoformat())
        with TemporaryDirectory(prefix='666-decisions-') as temporary:
            output = Path(temporary) / 'output.json'
            with patch.object(cli, 'fetch_history', return_value=history), \
                 patch.object(cli, 'fetch_daily_trend', return_value=analyze(raw, calendar, at)), \
                 patch.object(cli, 'bundled_calendar', return_value=(calendar, {})), \
                 patch.object(cli, 'verify_sma', return_value=dict(passed=True)), \
                 patch.object(cli, 'SnapshotProvider', return_value=SimpleNamespace(snapshots=[])), \
                 patch.object(yf, 'set_tz_cache_location'), redirect_stdout(StringIO()):
                code = cli.main(['2330.TW', '--decisions', '--as-of', at.isoformat(), '--output', str(output)])
            data = json.loads(output.read_text(encoding='utf-8'))['results'][0]
        self.assertEqual(code, 0)
        self.assertEqual(data['signal_analysis']['signals'][-1]['signal_type'], 'BUY')
        self.assertEqual(data['daily_trend']['status'], 'BULL')
        self.assertEqual(data['decision_analysis']['decision']['action'], 'BUY')


if __name__ == '__main__':
    unittest.main()

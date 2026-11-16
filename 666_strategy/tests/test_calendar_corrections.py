"""Confirmed typhoon closures, narrowly scoped quote exclusions and guards."""
from datetime import date
import importlib
import unittest

import pandas as pd

from test_daily_trend import daily_fixture, analyze, clock, package
from test_decisions import signal

calendar_module = importlib.import_module('666_strategy.calendar')


class ConfirmedClosureTests(unittest.TestCase):
    def setUp(self):
        self.raw, self.original = daily_fixture()
        self.closed = self.original.sessions[125]
        self.calendar = package.SessionCalendar(
            tuple(day for day in self.original.sessions if day != self.closed),
            self.original.start_date, self.original.through_date, 'explicit verified closure')
        self.at = clock(self.original, 149)

    def test_both_typhoon_days_are_explicitly_closed(self):
        calendar, _ = calendar_module.bundled_calendar(date(2026, 7, 9), date(2026, 10, 29))
        self.assertNotIn(date(2026, 7, 10), calendar.sessions)
        self.assertNotIn(date(2026, 10, 23), calendar.sessions)
        self.assertIn(date(2026, 7, 9), calendar.sessions)
        self.assertIn(date(2026, 7, 13), calendar.sessions)
        self.assertIn(date(2026, 10, 22), calendar.sessions)
        self.assertIn(date(2026, 10, 27), calendar.sessions)

    def test_missing_confirmed_closed_day_is_not_a_missing_session(self):
        raw = self.raw.drop(self.raw.index[125])
        trend = analyze(raw, self.calendar, self.at)
        self.assertEqual(trend.status, 'BULL')
        self.assertNotIn(self.raw.index[125], trend.bars.index)
        self.assertFalse(any(i['code'] == 'MISSING_OR_INVALID_DAILY_CLOSE' for i in trend.issues))

    def test_closed_zero_quote_is_excluded_and_audited_without_mutating_source(self):
        raw = self.raw.assign(Volume=1000.)
        raw.loc[self.raw.index[125], ['Close', 'Volume']] = [999999., 0.]
        original = raw.copy(deep=True)
        result = analyze(raw, self.calendar, self.at)
        expected = analyze(raw.drop(self.raw.index[125]), self.calendar, self.at)
        pd.testing.assert_frame_equal(result.bars, expected.bars)
        pd.testing.assert_frame_equal(raw, original)
        self.assertEqual(result.status, 'BULL')
        self.assertEqual(result.metadata['excluded_confirmed_closed_rows'], 1)
        issue = result.issues[0]
        self.assertEqual(issue['kind'], 'ZERO_VOLUME_QUOTE')
        self.assertFalse(issue['blocking'])
        self.assertEqual(issue['close'], 999999.)

    def test_open_day_zero_volume_quote_is_retained(self):
        raw = self.raw.assign(Volume=1000.)
        raw.iloc[-1, raw.columns.get_loc('Volume')] = 0.
        result = analyze(raw, self.original, self.at)
        self.assertEqual(result.bars.iloc[-1].Close, raw.iloc[-1].Close)
        self.assertEqual(result.metadata['excluded_confirmed_closed_rows'], 0)
        self.assertEqual(result.status, 'BULL')

    def test_open_day_zero_volume_invalid_close_stays_unknown(self):
        raw = self.raw.assign(Volume=1000.)
        raw.iloc[-1] = [float('nan'), 0.]
        result = analyze(raw, self.original, self.at)
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertEqual(result.metadata['excluded_confirmed_closed_rows'], 0)

    def test_real_missing_open_day_still_invalidates_ma120(self):
        raw = self.raw.assign(Volume=1000.).drop(self.raw.index[124])
        raw.loc[self.raw.index[125], 'Volume'] = 0.
        result = analyze(raw, self.calendar, self.at)
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertIn(self.raw.index[124], result.bars.index)
        self.assertTrue(any(i['code'] == 'MISSING_OR_INVALID_DAILY_CLOSE' for i in result.issues))

    def test_closed_priced_rows_without_explicit_zero_volume_stay_unknown(self):
        for volume in (None, float('nan'), float('inf'), -1., 1000., False):
            with self.subTest(volume=volume):
                raw = self.raw.assign(Volume=pd.Series([volume]*len(self.raw), index=self.raw.index, dtype=object))
                result = analyze(raw, self.calendar, self.at)
                self.assertEqual(result.status, 'UNKNOWN')
                self.assertTrue(any(i.get('blocking') for i in result.issues))
        self.assertEqual(analyze(self.raw, self.calendar, self.at).status, 'UNKNOWN')

    def test_duplicate_closed_labels_are_not_hidden_by_zero_volume_exception(self):
        raw = self.raw.assign(Volume=0.)
        raw = pd.concat([raw, raw.iloc[[125]]])
        result = analyze(raw, self.calendar, self.at)
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertTrue(any(i['code'] == 'INVALID_DAILY_LABEL' for i in result.issues))

    def test_empty_closed_placeholder_is_excluded(self):
        raw = self.raw.assign(Volume=1000.)
        raw.loc[self.raw.index[125]] = [float('nan'), 0.]
        result = analyze(raw, self.calendar, self.at)
        self.assertEqual(result.status, 'BULL')
        self.assertEqual(result.issues[0]['kind'], 'EMPTY_PLACEHOLDER')

    def test_source_exclusion_is_point_in_time_and_future_values_do_not_leak(self):
        raw = self.raw.assign(Volume=1000.)
        raw.loc[self.raw.index[125], 'Volume'] = 0.
        full = analyze(raw, self.calendar, self.at)
        for i in (124, 125, 126, 149):
            at = clock(self.original, i)
            prefix = analyze(raw, self.calendar, at)
            self.assertEqual(prefix.snapshot_at(at), full.snapshot_at(at))
        before = clock(self.original, 125, 13, 29)
        original = analyze(raw, self.calendar, before)
        raw['Close'] = raw.Close.astype(float)
        raw.iloc[125:, raw.columns.get_loc('Close')] = float('inf')
        poisoned = analyze(raw, self.calendar, before)
        pd.testing.assert_frame_equal(original.bars, poisoned.bars)
        self.assertEqual(original.issues, poisoned.issues)

    def test_reference_accepts_closed_zero_quote_but_still_checks_open_days(self):
        raw = self.raw.assign(Volume=1000.)
        raw.loc[self.raw.index[125], 'Volume'] = 0.
        result = calendar_module.validate_yahoo_reference(raw, self.calendar, self.at)
        self.assertEqual(result['ignored_reference_closed_session_zero_volume_quotes'], [self.closed.isoformat()])
        with self.assertRaisesRegex(ValueError, 'expected trading session'):
            calendar_module.validate_yahoo_reference(raw.drop(raw.index[124]), self.calendar, self.at)
        raw.loc[self.raw.index[125], 'Volume'] = 1000.
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            calendar_module.validate_yahoo_reference(raw, self.calendar, self.at)

    def test_non_bull_exit_before_typhoon_waits_for_next_verified_open(self):
        calendar, _ = calendar_module.bundled_calendar(date(2026, 3, 1), date(2026, 11, 3))
        index = pd.DatetimeIndex([pd.Timestamp(day, tz='Asia/Taipei') for day in calendar.sessions])
        raw = pd.DataFrame({'Close': [100.+i for i in range(len(index))], 'Volume': 1000.}, index=index)
        failed_at = pd.Timestamp('2026-10-22T13:30:00+08:00').tz_convert('Asia/Taipei')
        raw.loc[failed_at.normalize(), 'Close'] = 1.
        raw.loc[pd.Timestamp('2026-10-23T00:00:00+08:00').tz_convert('Asia/Taipei')] = [1., 0.]
        daily = analyze(raw, calendar, pd.Timestamp('2026-10-29T13:30:00+08:00'))
        state = package.PositionState('2330.TW', holding=True)
        result = package.evaluate_decision(state, at=failed_at, daily=daily,
            calendar=calendar, signal=signal(failed_at, 'SELL'))
        self.assertEqual(result.decision.action, 'EXIT')
        self.assertEqual(result.decision.execute_after, '2026-10-27T09:00:00+08:00')
        early = package.confirm_execution(result.state, decision_id=result.decision.decision_id,
            at=failed_at, daily=daily, calendar=calendar)
        self.assertFalse(early.executed)
        opened = pd.Timestamp(result.decision.execute_after)
        filled = package.confirm_execution(early.state, decision_id=result.decision.decision_id,
            at=opened, daily=daily, calendar=calendar)
        self.assertTrue(filled.executed)
        repeated = package.confirm_execution(filled.state, decision_id=result.decision.decision_id,
            at=opened, daily=daily, calendar=calendar)
        self.assertFalse(repeated.executed)


if __name__ == '__main__':
    unittest.main()

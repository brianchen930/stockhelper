"""Daily warmup, exact inequalities and point-in-time stock-only filtering."""
from datetime import datetime, time, timedelta
import importlib
import json
import math
import unittest

import pandas as pd

package = importlib.import_module('666_strategy')
daily_module = importlib.import_module('666_strategy.daily')
TAIPEI = importlib.import_module('666_strategy.history').TAIPEI


def daily_fixture(prices=None, *, sessions=155):
    days = tuple(stamp.date() for stamp in pd.bdate_range('2026-01-05', periods=sessions))
    calendar = package.SessionCalendar(days, days[0], days[-1], 'explicit synthetic test sessions')
    prices = list(range(100, 250)) if prices is None else prices
    index = pd.DatetimeIndex([datetime.combine(day, time(), TAIPEI) for day in days[:len(prices)]])
    raw = pd.DataFrame({'Close': prices}, index=index)
    return raw, calendar


def clock(calendar, i, hour=13, minute=30):
    return pd.Timestamp(datetime.combine(calendar.sessions[i], time(hour, minute), TAIPEI))


def analyze(raw, calendar, at=None):
    return package.analyze_daily_trend(raw, symbol='2330.TW', calendar=calendar,
        request_start=datetime.combine(calendar.start_date, time(), TAIPEI),
        as_of=at if at is not None else clock(calendar, len(raw)-1))


class DailyTrendTests(unittest.TestCase):
    def test_bull_has_three_true_conditions_and_independent_averages(self):
        raw, cal = daily_fixture()
        trend = analyze(raw, cal)
        snap = trend.snapshot_at(trend.as_of)
        self.assertEqual(snap['status'], 'BULL')
        self.assertTrue(all(snap[key] for key in daily_module.CONDITIONS))
        self.assertEqual(snap['daily_ma60'], math.fsum(raw.Close.iloc[-60:]) / 60)
        self.assertEqual(snap['daily_ma120'], math.fsum(raw.Close.iloc[-120:]) / 120)
        self.assertEqual(snap['daily_ma60_5_sessions_ago'], math.fsum(raw.Close.iloc[-65:-5]) / 60)

    def test_each_failed_condition_blocks_bull(self):
        cases = [([100+i for i in range(119)] + [150], 0),
                 ([300]*60 + [50]*65 + list(range(100, 110)), 1),
                 ([50]*60 + [200-i for i in range(70)] + [180], 2)]
        for prices, failed in cases:
            with self.subTest(failed=failed):
                raw, cal = daily_fixture(prices)
                snap = analyze(raw, cal).snapshot_at(clock(cal, len(prices)-1))
                self.assertEqual(snap['status'], 'NON_BULL')
                self.assertEqual([snap[key] for key in daily_module.CONDITIONS],
                                 [i != failed for i in range(3)])

    def test_equality_is_non_bull(self):
        raw, cal = daily_fixture([100]*120)
        self.assertEqual(analyze(raw, cal).status, 'NON_BULL')

    def test_119_closes_unknown_120_sufficient(self):
        raw, cal = daily_fixture()
        self.assertEqual(analyze(raw, cal, clock(cal, 118)).status, 'UNKNOWN')
        self.assertEqual(analyze(raw, cal, clock(cal, 119)).status, 'BULL')

    def test_intraday_uses_previous_completed_daily_close(self):
        raw, cal = daily_fixture(list(range(100, 220)) + [1])
        full = analyze(raw, cal)
        self.assertEqual(full.snapshot_at(clock(cal, 120, 13, 29))['status'], 'BULL')
        self.assertEqual(full.snapshot_at(clock(cal, 120))['status'], 'NON_BULL')
        self.assertEqual(full.snapshot_at(clock(cal, 120, 9, 0))['close'], 219.)

    def test_missing_latest_session_never_falls_back(self):
        raw, cal = daily_fixture()
        trend = analyze(raw.iloc[:-1], cal, clock(cal, 149))
        self.assertEqual(trend.status, 'UNKNOWN')
        self.assertIsNone(trend.snapshot_at(trend.as_of)['close'])

    def test_gap_is_kept_until_120_new_closes(self):
        raw, cal = daily_fixture(list(range(100, 350)), sessions=255)
        missing = raw.index[125]
        result = analyze(raw.drop(missing), cal, clock(cal, 249))
        self.assertIn(missing, result.bars.index)
        self.assertEqual(result.snapshot_at(clock(cal, 244))['status'], 'UNKNOWN')
        self.assertEqual(result.snapshot_at(clock(cal, 245))['status'], 'BULL')

    def test_invalid_close_is_unknown(self):
        for value in (None, float('inf'), 0, -1, True):
            raw, cal = daily_fixture()
            raw['Close'] = raw.Close.astype(object)
            raw.iloc[-1, 0] = value
            self.assertEqual(analyze(raw, cal).status, 'UNKNOWN')

    def test_slope_uses_five_sessions_across_holidays(self):
        raw, cal = daily_fixture()
        excluded = cal.sessions[145]
        days = tuple(day for day in cal.sessions if day != excluded)
        calendar = package.SessionCalendar(days, cal.start_date, cal.through_date)
        trend = analyze(raw.drop(raw.index[145]), calendar, clock(cal, 149))
        self.assertEqual(trend.bars.iloc[-1].daily_ma60_5_sessions_ago, trend.bars.iloc[-6].daily_ma60)

    def test_daily_prefixes_match_full_and_do_not_mutate_input(self):
        raw, cal = daily_fixture()
        original = raw.copy(deep=True)
        full = analyze(raw, cal)
        for i in (118, 119, 120, 130, 149):
            at = clock(cal, i)
            prefix = analyze(raw, cal, at)
            pd.testing.assert_frame_equal(prefix.bars, full.bars.iloc[:i+1])
            self.assertEqual(prefix.snapshot_at(at), full.snapshot_at(at))
        pd.testing.assert_frame_equal(raw, original)

    def test_future_poison_duplicates_and_forming_daily_bar_excluded(self):
        raw, cal = daily_fixture()
        at = clock(cal, 130, 13, 29)
        expected = analyze(raw, cal, at)
        poisoned = raw.copy()
        poisoned.iloc[130:, 0] = -1
        poisoned = pd.concat([poisoned, poisoned.iloc[130:]])
        actual = analyze(poisoned, cal, at)
        pd.testing.assert_frame_equal(actual.bars, expected.bars)

    def test_unknown_for_malformed_daily_sources(self):
        for kind in ('naive', 'duplicate', 'no_close', 'intraday_label'):
            with self.subTest(kind=kind):
                raw, cal = daily_fixture()
                if kind == 'naive':
                    raw.index = raw.index.tz_localize(None)
                elif kind == 'duplicate':
                    raw = pd.concat([raw, raw.iloc[[125]]])
                elif kind == 'no_close':
                    raw = raw.rename(columns={'Close': 'Other'})
                else:
                    raw.index = raw.index + timedelta(hours=9)
                result = analyze(raw, cal, clock(cal, 149))
                self.assertEqual(result.status, 'UNKNOWN')
                json.dumps(result.to_dict(), allow_nan=False)

    def test_never_uses_data_after_result_asof(self):
        raw, cal = daily_fixture()
        result = analyze(raw, cal, clock(cal, 130))
        self.assertEqual(result.snapshot_at(clock(cal, 131))['status'], 'UNKNOWN')

    def test_later_source_conflict_does_not_poison_historical_snapshot(self):
        raw, cal = daily_fixture()
        duplicated = pd.concat([raw, raw.iloc[[140]]])
        full = analyze(duplicated, cal, clock(cal, 149))
        at = clock(cal, 130)
        self.assertEqual(full.status, 'UNKNOWN')
        self.assertEqual(full.snapshot_at(at), analyze(raw, cal, at).snapshot_at(at))
        # A price on an explicitly closed day is also only known after its close.
        holiday = cal.sessions[140]
        calendar = package.SessionCalendar(tuple(day for day in cal.sessions if day != holiday),
                                            cal.start_date, cal.through_date)
        conflict = analyze(raw, calendar, clock(cal, 149))
        self.assertEqual(conflict.status, 'UNKNOWN')
        self.assertEqual(conflict.snapshot_at(at), analyze(raw, calendar, at).snapshot_at(at))
        self.assertEqual(conflict.bars.trend_status.iloc[-1], 'UNKNOWN')

    def test_empty_data_and_calendar_gap_are_unknown(self):
        raw, cal = daily_fixture()
        self.assertEqual(analyze(raw.iloc[:0], cal, clock(cal, 149)).status, 'UNKNOWN')
        short = package.SessionCalendar(cal.sessions[1:], cal.sessions[1], cal.through_date)
        result = package.analyze_daily_trend(raw, symbol='2330.TW', calendar=short,
            request_start=raw.index[0], as_of=clock(cal, 149))
        self.assertEqual(result.status, 'UNKNOWN')

    def test_fetch_requests_120_prior_sessions_and_stock_daily_only(self):
        raw, cal = daily_fixture()
        calls = []
        class Provider:
            def history(self, symbol, **options):
                calls.append(dict(symbol=symbol, **options))
                return raw
        result = package.fetch_daily_trend('2330.TW', decision_start=clock(cal, 125, 9, 0),
            as_of=clock(cal, 149), provider=Provider(), calendar=cal)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['symbol'], '2330.TW')
        self.assertEqual(calls[0]['interval'], '1d')
        self.assertEqual(calls[0]['start'].date(), cal.sessions[5])
        self.assertEqual(result.snapshot_at(clock(cal, 125, 9, 0))['status'], 'BULL')
        self.assertEqual(result.metadata['requested_warmup_sessions'], 120)

    def test_short_warmup_and_provider_failure_explicit(self):
        raw, cal = daily_fixture()
        class Provider:
            def history(self, *args, **kwargs):
                return raw
        result = package.fetch_daily_trend('2330.TW', decision_start=clock(cal, 10, 9, 0),
            as_of=clock(cal, 20), provider=Provider(), calendar=cal)
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertIn('DAILY_WARMUP_COVERAGE_INSUFFICIENT', [i['code'] for i in result.issues])
        class Broken:
            def history(self, *args, **kwargs):
                raise OSError('source unavailable')
        failed = package.fetch_daily_trend('2330.TW', decision_start=clock(cal, 125, 9, 0),
            as_of=clock(cal, 149), provider=Broken(), calendar=cal)
        self.assertEqual(failed.status, 'UNKNOWN')
        json.dumps(failed.to_dict(), allow_nan=False)

    def test_default_warmup_uses_verified_year_without_unneeded_previous_year(self):
        calendar_module = importlib.import_module('666_strategy.calendar')
        cal, _ = calendar_module.bundled_calendar(pd.Timestamp('2026-01-01').date(),
                                                pd.Timestamp('2026-07-09').date())
        index = pd.DatetimeIndex([datetime.combine(day, time(), TAIPEI) for day in cal.sessions])
        raw = pd.DataFrame({'Close': [100+i for i in range(len(index))]}, index=index)
        calls = []
        class Provider:
            def history(self, symbol, **options):
                calls.append(options)
                return raw
        result = package.fetch_daily_trend('2330.TW', decision_start='2026-07-09T09:00:00+08:00',
            as_of='2026-07-09T13:30:00+08:00', provider=Provider())
        self.assertEqual(result.status, 'BULL')
        self.assertEqual(result.metadata['requested_warmup_sessions'], 120)
        self.assertEqual(calls[0]['start'].year, 2026)


if __name__ == '__main__':
    unittest.main()

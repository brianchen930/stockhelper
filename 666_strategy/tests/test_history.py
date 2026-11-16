"""Offline contract tests; no app imports, network, SQLite or market cache."""
from dataclasses import replace
from datetime import date, datetime, time, timedelta
import importlib
import json
import math
import sys
import unittest
from unittest.mock import patch

import pandas as pd

package = importlib.import_module('666_strategy')
history_module = importlib.import_module('666_strategy.history')
provider_module = importlib.import_module('666_strategy.provider')
cli = importlib.import_module('666_strategy.__main__')
calendar_module = importlib.import_module('666_strategy.calendar')
TAIPEI = history_module.TAIPEI


def fixture(days=20):
    # An explicit synthetic calendar, not a claim about actual exchange dates.
    sessions = tuple(stamp.date() for stamp in pd.bdate_range('2026-09-01', periods=days))
    calendar = package.SessionCalendar(sessions, sessions[0], sessions[-1])
    stamps = pd.DatetimeIndex([datetime.combine(day, time(hour), TAIPEI)
                              for day in sessions for hour in (9, 10, 11, 12, 13)])
    closes = [100. + i for i in range(len(stamps))]
    frame = pd.DataFrame(dict(Open=[v - .2 for v in closes], High=[v + 1 for v in closes],
                             Low=[v - 1 for v in closes], Close=closes,
                             Volume=[1000.] * len(stamps)), index=stamps)
    start = datetime.combine(sessions[0], time(), TAIPEI)
    end = datetime.combine(sessions[-1], time(13, 30), TAIPEI)
    return frame, calendar, start, end


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.frame, self.calendar, self.start, self.end = fixture()

    def analyze(self, frame=None, **options):
        arguments = dict(symbol='2330.TW', calendar=self.calendar,
                         request_start=self.start, as_of=self.end)
        arguments.update(options)
        return package.analyze_history(self.frame if frame is None else frame, **arguments)

    def test_sma_is_last_sixty_intraday_closes_not_daily_ma(self):
        result = self.analyze()
        self.assertEqual(result.status, 'OK')
        self.assertEqual(result.ma60, 169.5)
        self.assertTrue(result.bars.MA60.iloc[:59].isna().all())
        self.assertEqual(result.bars.MA60.iloc[59], 129.5)
        self.assertEqual(cli.verify_sma(result)['checked_values'], 41)
        self.assertEqual(len(result.recent()), 10)

    def test_every_reported_sma_has_an_independent_reference(self):
        for i, row in enumerate(self.analyze().bars.itertuples()):
            if row.ma60_valid:
                expected = math.fsum(self.frame.Close.iloc[i-59:i+1]) / 60
                self.assertEqual(row.MA60, expected)

    def test_short_session_bar_counts_once_and_closes_at_1330(self):
        result = self.analyze()
        self.assertEqual(len(result.bars), 100)
        self.assertEqual(result.bars.iloc[-1].duration_minutes, 30)
        self.assertTrue(result.bars.iloc[-1].is_short_session_bar)
        self.assertEqual(result.bars.iloc[-1].bar_end.hour, 13)
        self.assertEqual(result.bars.iloc[-1].bar_end.minute, 30)

    def test_optional_exclusion_of_short_bars(self):
        result = self.analyze(include_short_bar=False)
        self.assertEqual(len(result.bars), 80)
        self.assertTrue(result.bars.duration_minutes.eq(60).all())
        self.assertEqual(result.metadata['excluded_short_rows'], 20)
        closes = self.frame.loc[self.frame.index.hour != 13].Close.iloc[-60:]
        self.assertEqual(result.ma60, math.fsum(closes) / 60)

    def test_1330_is_a_real_close_boundary(self):
        before = self.analyze(as_of=self.end - timedelta(seconds=1))
        exact = self.analyze()
        self.assertEqual(len(before.bars), 99)
        self.assertEqual(before.metadata['excluded_open_rows'], 1)
        self.assertEqual(len(exact.bars), 100)

    def test_full_hour_waits_until_its_end(self):
        cutoff = self.end.replace(hour=9, minute=59)
        result = self.analyze(as_of=cutoff)
        self.assertEqual(len(result.bars), 95)
        self.assertEqual(result.metadata['excluded_open_rows'], 1)
        self.assertEqual(len(self.analyze(as_of=cutoff.replace(hour=10, minute=0)).bars), 96)

    def test_preopen_does_not_invent_today_bars(self):
        result = self.analyze(as_of=self.end.replace(hour=8, minute=30))
        self.assertEqual(len(result.bars), 95)
        self.assertEqual(result.metadata['missing_closed_bars'], 0)

    def test_utc_is_converted_to_taipei(self):
        utc = self.frame.tz_convert('UTC')
        result = self.analyze(utc, as_of=pd.Timestamp(self.end).tz_convert('UTC'))
        pd.testing.assert_frame_equal(result.bars, self.analyze().bars)
        self.assertEqual(str(result.bars.index.tz), 'Asia/Taipei')

    def test_naive_source_timestamps_fail_closed(self):
        result = self.analyze(self.frame.tz_localize(None))
        self.assertEqual(result.status, 'INVALID_DATA')
        self.assertIsNone(result.ma60)
        self.assertEqual(result.issues[0]['code'], 'INVALID_TIMESTAMPS')

    def test_naive_asof_is_a_caller_error(self):
        with self.assertRaises(ValueError):
            self.analyze(as_of=self.end.replace(tzinfo=None))

    def test_59_closes_are_insufficient(self):
        frame, calendar, start, end = fixture(12)
        result = package.analyze_history(frame, symbol='2408.TW', calendar=calendar,
            request_start=start, as_of=end.replace(hour=13, minute=0))
        self.assertEqual(len(result.bars), 59)
        self.assertEqual(result.status, 'INSUFFICIENT_DATA')
        self.assertIsNone(result.ma60)
        self.assertTrue(result.bars.MA60.isna().all())

    def test_missing_bar_is_not_skipped_to_make_sixty(self):
        result = self.analyze(self.frame.drop(self.frame.index[80]))
        self.assertEqual(result.status, 'INCOMPLETE_DATA')
        self.assertIsNone(result.ma60)
        self.assertEqual(len(result.bars), 100)
        self.assertFalse(result.bars.iloc[80].is_valid)
        self.assertTrue(result.bars.MA60.iloc[80:].isna().all())
        self.assertEqual(result.metadata['missing_closed_bars'], 1)

    def test_missing_entire_stock_session_is_detected(self):
        result = self.analyze(self.frame.drop(self.frame.index[65:70]))
        self.assertEqual(result.status, 'INCOMPLETE_DATA')
        self.assertEqual(result.metadata['missing_closed_bars'], 5)
        self.assertIsNone(result.ma60)

    def test_missing_latest_closed_bar_blocks_latest_ma(self):
        result = self.analyze(self.frame.iloc[:-1])
        self.assertEqual(result.status, 'INCOMPLETE_DATA')
        self.assertIsNone(result.ma60)
        self.assertFalse(result.bars.iloc[-1].ma60_valid)

    def test_sixty_new_valid_bars_recover_after_gap(self):
        result = self.analyze(self.frame.drop(self.frame.index[10]))
        self.assertEqual(result.status, 'OK')
        self.assertTrue(pd.isna(result.bars.MA60.iloc[69]))
        self.assertTrue(result.bars.ma60_valid.iloc[70])
        self.assertEqual(result.ma60, 169.5)
        self.assertEqual(result.metadata['missing_closed_bars'], 1)

    def test_invalid_ohlcv_never_enters_ma(self):
        cases = [('Open', float('nan')), ('High', float('inf')), ('Low', 10000),
                 ('High', 1), ('Close', 0), ('Volume', -1), ('Volume', None),
                 ('Close', True)]
        for column, value in cases:
            with self.subTest(column=column, value=value):
                frame = self.frame.astype(object)
                frame.loc[frame.index[-1], column] = value
                result = self.analyze(frame)
                self.assertIsNone(result.ma60)
                self.assertEqual(result.status, 'INCOMPLETE_DATA')

    def test_zero_volume_is_valid_observation_not_a_missing_bar(self):
        frame = self.frame.copy()
        frame.loc[frame.index[-1], 'Volume'] = 0
        self.assertEqual(self.analyze(frame).status, 'OK')

    def test_missing_column_fails_without_fill(self):
        result = self.analyze(self.frame.drop(columns='Volume'))
        self.assertIsNone(result.ma60)
        self.assertTrue(result.bars.MA60.isna().all())

    def test_duplicates_are_ambiguous_and_rejected(self):
        result = self.analyze(pd.concat([self.frame, self.frame.iloc[[-1]]]))
        self.assertEqual(result.status, 'INVALID_DATA')
        self.assertEqual(result.issues[0]['code'], 'DUPLICATE_BARS')

    def test_order_can_be_normalized_without_changing_values(self):
        pd.testing.assert_frame_equal(self.analyze(self.frame.iloc[::-1]).bars, self.analyze().bars)

    def test_non_hourly_alignment_is_not_silently_resampled(self):
        frame = self.frame.copy()
        index = list(frame.index)
        index[-1] += timedelta(minutes=5)
        frame.index = pd.DatetimeIndex(index)
        result = self.analyze(frame)
        self.assertEqual(result.status, 'INVALID_DATA')
        self.assertEqual(result.issues[0]['code'], 'MISALIGNED_BAR')

    def test_postmarket_quote_is_excluded(self):
        extra = self.frame.iloc[[-1]].copy()
        extra.index += timedelta(minutes=30)
        result = self.analyze(pd.concat([self.frame, extra]))
        self.assertEqual(result.ma60, 169.5)
        self.assertEqual(result.metadata['excluded_out_of_session_rows'], 1)

    def test_null_placeholders_only_drop_on_an_explicit_closed_day(self):
        holiday = self.calendar.sessions[-1]
        calendar = replace(self.calendar, sessions=self.calendar.sessions[:-1])
        frame = self.frame.copy()
        frame.loc[frame.index.date == holiday, ['Open', 'High', 'Low', 'Close']] = float('nan')
        frame.loc[frame.index.date == holiday, 'Volume'] = 0
        result = self.analyze(frame, calendar=calendar)
        self.assertEqual(result.status, 'OK')
        self.assertEqual(result.metadata['excluded_closed_session_rows'], 5)
        # The same source rows on an open session are gaps, not holidays.
        result = self.analyze(frame)
        self.assertEqual(result.status, 'INCOMPLETE_DATA')
        self.assertIsNone(result.ma60)

    def test_future_bad_values_and_duplicates_cannot_change_past_ma(self):
        cutoff = self.end - timedelta(days=3)
        before = self.analyze(as_of=cutoff)
        frame = self.frame.copy()
        frame.loc[frame.index > cutoff, 'Close'] = float('nan')
        frame = pd.concat([frame, frame.iloc[[-1]]])
        after = self.analyze(frame, as_of=cutoff)
        pd.testing.assert_frame_equal(before.bars, after.bars)
        self.assertEqual(before.status, after.status)
        self.assertEqual(before.issues, after.issues)

    def test_explicit_holiday_is_not_counted_as_missing(self):
        holiday = self.calendar.sessions[14]
        calendar = replace(self.calendar, sessions=tuple(day for day in self.calendar.sessions if day != holiday))
        result = self.analyze(self.frame.loc[self.frame.index.date != holiday], calendar=calendar)
        self.assertEqual(result.status, 'OK')
        self.assertEqual(result.metadata['missing_closed_bars'], 0)

    def test_bars_on_unconfirmed_session_fail_closed(self):
        calendar = replace(self.calendar, sessions=self.calendar.sessions[:-1])
        result = self.analyze(calendar=calendar)
        self.assertEqual(result.status, 'INVALID_DATA')
        self.assertEqual(result.issues[0]['code'], 'UNCONFIRMED_SESSION')

    def test_calendar_coverage_must_include_requested_period(self):
        calendar = replace(self.calendar, through_date=self.calendar.through_date - timedelta(days=1),
                           sessions=self.calendar.sessions[:-1])
        result = self.analyze(calendar=calendar)
        self.assertEqual(result.status, 'CALENDAR_ERROR')

    def test_input_is_not_mutated(self):
        original = self.frame.copy(deep=True)
        self.analyze()
        pd.testing.assert_frame_equal(self.frame, original)

    def test_empty_source_has_explicit_error(self):
        result = self.analyze(self.frame.iloc[:0])
        self.assertEqual(result.status, 'SOURCE_ERROR')
        self.assertIsNone(result.ma60)

    def test_json_has_no_nan_and_includes_quality(self):
        result = self.analyze(self.frame.drop(self.frame.index[-1]))
        payload = result.to_dict()
        json.dumps(payload, allow_nan=False)
        self.assertIsNone(payload['bars'][-1]['MA60'])
        self.assertFalse(payload['bars'][-1]['is_valid'])

    def test_imports_do_not_load_application(self):
        for name in ('app.main', 'app.stock', 'app.scheduler', 'app.database', 'app.notifier'):
            self.assertNotIn(name, sys.modules)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.frame, self.calendar, self.start, self.end = fixture()
        daily_index = pd.DatetimeIndex([datetime.combine(day, time(), TAIPEI)
                                       for day in self.calendar.sessions])
        self.daily = pd.DataFrame({'Close': [1000.] * len(daily_index)}, index=daily_index)
        # Provider tests use synthetic sessions; real holiday metadata is tested
        # separately rather than pretending every synthetic weekday was open.
        self.calendar_patch = patch.object(provider_module, 'bundled_calendar',
            side_effect=lambda start, end: (replace(self.calendar, start_date=start, through_date=end),
                dict(calendar_limitation='Injected synthetic calendar for unit tests')))
        self.calendar_patch.start()
        self.addCleanup(self.calendar_patch.stop)

    def provider(self, *, fail=None, empty=False, daily=None):
        owner = self
        class Fake:
            def __init__(self):
                self.calls = []
            def history(self, symbol, **options):
                self.calls.append((symbol, options))
                if fail == symbol:
                    raise RuntimeError('simulated Yahoo failure')
                return (owner.frame.iloc[:0] if empty else owner.frame.copy()) if symbol != '^TWII' else (
                    owner.daily.copy() if daily is None else daily.copy())
        return Fake()

    def test_only_yahoo_60m_and_index_daily_are_requested(self):
        provider = self.provider()
        result = package.fetch_history('2330.tw', as_of=self.end, provider=provider)
        self.assertEqual(result.status, 'OK')
        self.assertEqual([call[0] for call in provider.calls], ['2330.TW', '^TWII'])
        self.assertEqual([call[1]['interval'] for call in provider.calls], ['60m', '1d'])
        self.assertIn('calendar_limitation', result.metadata)
        self.assertGreater(result.metadata['valid_ma60_count'], 0)

    def test_api_failure_never_falls_back_to_another_source(self):
        provider = self.provider(fail='2408.TW')
        result = package.fetch_history('2408.TW', as_of=self.end, provider=provider)
        self.assertEqual(result.status, 'SOURCE_ERROR')
        self.assertIsNone(result.ma60)
        self.assertEqual(len(provider.calls), 1)

    def test_empty_api_response_is_not_a_success(self):
        result = package.fetch_history('2330.TW', as_of=self.end, provider=self.provider(empty=True))
        self.assertEqual(result.status, 'SOURCE_ERROR')

    def test_calendar_api_failure_blocks_ma(self):
        result = package.fetch_history('2330.TW', as_of=self.end, provider=self.provider(fail='^TWII'))
        self.assertEqual(result.status, 'CALENDAR_ERROR')
        self.assertIsNone(result.ma60)

    def test_verified_calendar_avoids_extra_download(self):
        calendar = replace(self.calendar, start_date=(self.end - timedelta(days=60)).date())
        provider = self.provider()
        result = package.fetch_history('2408.TW', as_of=self.end, provider=provider, calendar=calendar)
        self.assertEqual(result.status, 'OK')
        self.assertEqual(len(provider.calls), 1)

    def test_bad_daily_calendar_is_rejected(self):
        for daily in (self.daily.iloc[:0], self.daily.assign(Close=float('nan')),
                      pd.concat([self.daily, self.daily.iloc[[-1]]]), self.daily.tz_localize(None)):
            with self.subTest(rows=len(daily)):
                result = package.fetch_history('2330.TW', as_of=self.end, provider=self.provider(daily=daily))
                self.assertEqual(result.status, 'CALENDAR_ERROR')

    def test_no_silent_symbol_market_resolution(self):
        for symbol in ('2330', '^TWII', '../2330.TW', '', '2330.US'):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                package.fetch_history(symbol, provider=self.provider())

    def test_yfinance_request_has_no_adjustment_or_repair(self):
        calls = []
        frame = self.frame
        class FakeTicker:
            def __init__(self, symbol, **options):
                calls.append((symbol, options))
            def history(self, **options):
                calls.append(options)
                return frame
        provider_module.YFinanceProvider(ticker_factory=FakeTicker).history(
            '2330.TW', start=self.start, end=self.end, interval='60m')
        request = calls[-1]
        for key in ('auto_adjust', 'back_adjust', 'prepost', 'actions', 'repair', 'rounding'):
            self.assertFalse(request[key])
        self.assertTrue(request['keepna'])
        self.assertTrue(request['raise_errors'])


class CalendarTests(unittest.TestCase):
    def test_official_holidays_and_verified_emergency_closure(self):
        calendar, metadata = calendar_module.bundled_calendar(date(2026, 8, 27), date(2026, 10, 26))
        for day in ('2026-09-25', '2026-09-28', '2026-10-09', '2026-10-23', '2026-10-26'):
            self.assertNotIn(date.fromisoformat(day), calendar.sessions)
        self.assertIn(date(2026, 10, 22), calendar.sessions)
        self.assertEqual(len(calendar.sessions), 38)
        self.assertTrue(metadata['calendar_sources'])

    def test_unknown_year_never_uses_weekday_guess(self):
        with self.assertRaisesRegex(ValueError, 'No verified session calendar'):
            calendar_module.bundled_calendar(date(2027, 1, 1), date(2027, 1, 31))

    def reference(self):
        calendar, _ = calendar_module.bundled_calendar(date(2026, 10, 19), date(2026, 10, 26))
        index = pd.DatetimeIndex([datetime.combine(day, time(), TAIPEI) for day in calendar.sessions])
        frame = pd.DataFrame(dict(Open=100., High=101., Low=99., Close=100., Volume=1000.), index=index)
        cutoff = pd.Timestamp('2026-10-26T14:00:00+08:00')
        return calendar, frame, cutoff

    def test_missing_whole_index_session_is_not_mistaken_for_holiday(self):
        calendar, frame, cutoff = self.reference()
        with self.assertRaisesRegex(ValueError, 'expected trading session'):
            calendar_module.validate_yahoo_reference(frame.iloc[:-1], calendar, cutoff)

    def test_invalid_reference_on_open_session_fails_closed(self):
        calendar, frame, cutoff = self.reference()
        frame.loc[frame.index[-1], 'Close'] = float('nan')
        with self.assertRaisesRegex(ValueError, 'expected trading session'):
            calendar_module.validate_yahoo_reference(frame, calendar, cutoff)

    def test_empty_reference_on_known_closed_session_is_a_placeholder(self):
        calendar, frame, cutoff = self.reference()
        frame.loc[pd.Timestamp('2026-10-23T00:00:00+08:00').tz_convert(TAIPEI)] = [float('nan')] * 4 + [0.]
        result = calendar_module.validate_yahoo_reference(frame, calendar, cutoff)
        self.assertEqual(result['ignored_reference_closed_session_placeholders'], ['2026-10-23'])

    def test_priced_reference_on_closed_session_is_a_conflict(self):
        calendar, frame, cutoff = self.reference()
        frame.loc[pd.Timestamp('2026-10-23T00:00:00+08:00').tz_convert(TAIPEI)] = [100., 101., 99., 100., 1000.]
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            calendar_module.validate_yahoo_reference(frame, calendar, cutoff)


if __name__ == '__main__':
    unittest.main()

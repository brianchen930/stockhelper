"""Specified crossing cases plus integration and temporal-prefix invariance."""
from contextlib import redirect_stdout
from datetime import timedelta
import importlib
from io import StringIO
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from test_history import fixture

package = importlib.import_module('666_strategy')
cli = importlib.import_module('666_strategy.__main__')
history_module = importlib.import_module('666_strategy.history')


def manual(prices, averages=None, *, symbol='2330.TW', valid=None, ma_valid=None):
    """Handwritten first-stage output to isolate exact crossing conditions."""
    frame, _, _, _ = fixture(max(1, math.ceil(len(prices) / 5)))
    frame = frame.iloc[:len(prices)].copy()
    frame['Close'] = prices
    frame['MA60'] = [100.] * len(prices) if averages is None else averages
    frame['is_valid'] = True if valid is None else valid
    frame['ma60_valid'] = frame.MA60.notna() if ma_valid is None else ma_valid
    frame['bar_end'] = [min(stamp + timedelta(hours=1),
        stamp.normalize() + timedelta(hours=13, minutes=30)) for stamp in frame.index]
    frame['duration_minutes'] = [30 if stamp.hour == 13 else 60 for stamp in frame.index]
    frame['is_short_session_bar'] = frame.duration_minutes.eq(30)
    return package.HistoryResult(symbol, 'OK', frame, dict(interval='60m',
        as_of=frame.bar_end.iloc[-1].isoformat(), expected_closed_bars=len(frame),
        include_short_session_bar=True))


def analyze_prices(prices, *, missing=(), cutoff=None):
    frame, calendar, start, _ = fixture(math.ceil(len(prices) / 5))
    frame = frame.iloc[:len(prices)].copy()
    frame['Close'] = prices
    frame['Open'] = frame.Close
    frame['High'] = frame.Close + 1
    frame['Low'] = frame.Close - 1
    end = min(frame.index[-1] + timedelta(hours=1),
        frame.index[-1].normalize() + timedelta(hours=13, minutes=30))
    frame = frame.drop(frame.index[list(missing)])
    return package.analyze_history(frame, symbol='2408.TW', calendar=calendar,
        request_start=start, as_of=cutoff if cutoff is not None else end)


class CrossingTests(unittest.TestCase):
    def kinds(self, prices, averages=None):
        return [row['signal_type'] for row in package.generate_signals(manual(prices, averages)).signals]

    def test_upward_cross(self):
        self.assertEqual(self.kinds([99, 101]), ['BUY'])

    def test_downward_cross(self):
        self.assertEqual(self.kinds([101, 99]), ['SELL'])

    def test_previous_equality_allows_both_directions(self):
        self.assertEqual(self.kinds([100, 101]), ['BUY'])
        self.assertEqual(self.kinds([100, 99]), ['SELL'])

    def test_current_equality_does_not_trigger(self):
        for prices in ([99, 100], [101, 100], [100, 100]):
            with self.subTest(prices=prices):
                self.assertEqual(self.kinds(prices), [])

    def test_continuously_above_never_invents_a_buy(self):
        self.assertEqual(self.kinds([101, 102, 103, 104]), [])

    def test_continuously_below_never_invents_a_sell(self):
        self.assertEqual(self.kinds([99, 98, 97, 96]), [])

    def test_only_crossing_bar_is_marked(self):
        result = package.generate_signals(manual([99, 101, 102, 103]))
        self.assertEqual(result.bars.signal.tolist(), [None, 'BUY', None, None])
        self.assertIsNone(result.latest_signal)
        self.assertTrue(result.latest_signal_valid)

    def test_each_close_compares_its_own_ma(self):
        self.assertEqual(self.kinds([99, 101], [100, 102]), [])
        self.assertEqual(self.kinds([101, 99], [100, 98]), [])

    def test_cross_can_be_caused_by_moving_ma_without_price_change(self):
        self.assertEqual(self.kinds([100, 100], [101, 99]), ['BUY'])
        self.assertEqual(self.kinds([100, 100], [99, 101]), ['SELL'])

    def test_exact_comparison_has_no_extra_tolerance(self):
        self.assertEqual(self.kinds([100, 100 + 1e-12]), ['BUY'])
        self.assertEqual(self.kinds([100, 100 - 1e-12]), ['SELL'])

    def test_equality_then_new_cross_is_not_suppressed_by_position(self):
        self.assertEqual(self.kinds([99, 101, 100, 101]), ['BUY', 'BUY'])
        self.assertEqual(self.kinds([101, 99, 100, 99]), ['SELL', 'SELL'])

    def test_missing_ma_on_either_bar_blocks_cross(self):
        for ma in ([None, 100], [100, None]):
            with self.subTest(ma=ma):
                result = package.generate_signals(manual([99, 101], ma))
                self.assertEqual(result.signals, [])
                self.assertFalse(result.latest_signal_valid)

    def test_invalid_ma_flags_and_nonfinite_values_block_cross(self):
        for averages, flags in [([100, 100], [False, True]),
                ([100, 100], [True, False]), ([float('inf'), 100], [True, True]),
                ([100, float('nan')], [True, True]), ([0, 100], [True, True])]:
            with self.subTest(averages=averages, flags=flags):
                result = package.generate_signals(manual([99, 101], averages, ma_valid=flags))
                self.assertEqual(result.signals, [])
                self.assertFalse(result.latest_signal_valid)

    def test_invalid_bar_on_either_side_blocks_cross(self):
        for flags in ([False, True], [True, False]):
            with self.subTest(flags=flags):
                result = package.generate_signals(manual([99, 101], valid=flags))
                self.assertEqual(result.signals, [])

    def test_missing_middle_bar_is_never_bridged(self):
        result = package.generate_signals(manual([99, float('nan'), 101],
            valid=[True, False, True]))
        self.assertEqual(result.signals, [])
        self.assertFalse(result.latest_signal_valid)
        self.assertEqual(len(result.recent()), 2)

    def test_bar_without_ma_history_is_not_a_cross(self):
        result = package.generate_signals(manual([101]))
        self.assertEqual(result.signals, [])
        self.assertFalse(result.latest_signal_valid)
        self.assertEqual(result.status, 'INSUFFICIENT_DATA')

    def test_short_1300_bar_is_kept_and_signal_time_is_1330(self):
        result = package.generate_signals(manual([99, 99, 99, 99, 101]))
        self.assertEqual(len(result.signals), 1)
        event = result.signals[0]
        self.assertIn('T13:00:00+08:00', event['bar_start'])
        self.assertIn('T13:30:00+08:00', event['signal_time'])

    def test_overnight_adjacent_trading_bars_can_cross(self):
        result = package.generate_signals(manual([99, 99, 99, 99, 99, 101]))
        self.assertEqual([e['signal_type'] for e in result.signals], ['BUY'])
        self.assertEqual(result.bars.index[-1].hour, 9)

    def test_unclosed_bar_with_forged_valid_flags_is_rejected(self):
        history = manual([99, 99, 99, 99, 101])
        history.metadata['as_of'] = history.bars.bar_end.iloc[-1].replace(minute=29).isoformat()
        result = package.generate_signals(history)
        self.assertEqual(result.signals, [])
        self.assertFalse(result.latest_signal_valid)
        self.assertEqual(len(result.recent()), 4)

    def test_record_has_required_values_and_taipei_times(self):
        result = package.generate_signals(manual([99, 101], symbol='2408.TW'))
        event = result.latest_signal
        self.assertEqual(event['symbol'], '2408.TW')
        self.assertEqual(event['close'], 101.)
        self.assertEqual(event['ma60'], 100.)
        self.assertEqual(event['signal_type'], 'BUY')
        self.assertTrue(event['bar_start'].endswith('+08:00'))
        self.assertEqual(event['bar_end'], event['signal_time'])

    def test_repeated_calls_do_not_accumulate_signals_or_mutate_input(self):
        history = manual([99, 101, 99, 102])
        original = history.bars.copy(deep=True)
        first = package.generate_signals(history)
        second = package.generate_signals(history)
        self.assertEqual(first.signals, second.signals)
        pd.testing.assert_frame_equal(history.bars, original)
        self.assertNotIn('signal', history.bars)

    def test_empty_source_failure_stays_explicit(self):
        history = package.HistoryResult('2330.TW', 'SOURCE_ERROR',
            issues=[dict(code='YAHOO_HISTORY_FAILED')])
        result = package.generate_signals(history)
        self.assertEqual(result.status, 'SOURCE_ERROR')
        self.assertEqual(result.signals, [])
        self.assertFalse(result.latest_signal_valid)
        json.dumps(result.to_dict(), allow_nan=False)

    def test_incomplete_latest_data_keeps_valid_earlier_events(self):
        history = manual([99, 101, 102, 99, float('nan')], valid=[True, True, True, True, False])
        history.status = 'INCOMPLETE_DATA'
        result = package.generate_signals(history)
        self.assertEqual([e['signal_type'] for e in result.signals], ['BUY', 'SELL'])
        self.assertFalse(result.latest_signal_valid)

    def test_removed_grid_row_is_rejected_not_relinked(self):
        history = manual([99, 100, 101])
        history.bars = history.bars.drop(history.bars.index[1])
        result = package.generate_signals(history)
        self.assertEqual(result.status, 'INVALID_DATA')
        self.assertEqual(result.signals, [])

    def test_wrong_interval_duplicate_or_unsorted_grid_is_invalid(self):
        for change in ('interval', 'duplicate', 'unsorted'):
            with self.subTest(change=change):
                history = manual([99, 101])
                if change == 'interval':
                    history.metadata['interval'] = '1d'
                elif change == 'duplicate':
                    history.bars.index = pd.DatetimeIndex([history.bars.index[0]] * 2)
                else:
                    history.bars = history.bars.iloc[::-1]
                result = package.generate_signals(history)
                self.assertEqual(result.status, 'INVALID_DATA')
                self.assertEqual(result.signals, [])

    def test_json_preserves_none_and_validity_without_nan(self):
        result = package.generate_signals(manual([99, 101, float('nan')], valid=[True, True, False]))
        data = result.to_dict()
        json.dumps(data, allow_nan=False)
        self.assertIsNone(data['bars'][-1]['signal'])
        self.assertFalse(data['bars'][-1]['signal_valid'])

    def test_malformed_input_returns_a_serializable_error(self):
        history = manual([99, 101])
        history.bars = history.bars.drop(columns='is_valid')
        result = package.generate_signals(history)
        self.assertEqual(result.status, 'INVALID_DATA')
        self.assertEqual(result.signals, [])
        json.dumps(result.to_dict(), allow_nan=False)


class PipelineTests(unittest.TestCase):
    def test_first_possible_cross_needs_two_valid_ma_points(self):
        history = analyze_prices([100.] * 60 + [101., 102., 90.])
        result = package.generate_signals(history)
        self.assertFalse(result.bars.signal_valid.iloc[:60].any())
        self.assertEqual(result.bars.signal.iloc[60:].tolist(), ['BUY', None, 'SELL'])
        self.assertEqual(result.metadata['evaluable_pairs'], 3)

    def test_exactly_60_bars_have_ma_but_not_a_signal_pair(self):
        history = analyze_prices([100.] * 60)
        self.assertEqual(history.status, 'OK')
        result = package.generate_signals(history)
        self.assertEqual(result.status, 'INSUFFICIENT_DATA')
        self.assertEqual(result.signals, [])

    def test_gap_blocks_signals_until_both_ma_windows_recover(self):
        prices = [99., 101., 98., 102.] * 40
        result = package.generate_signals(analyze_prices(prices, missing=(70,)))
        self.assertFalse(result.bars.signal_valid.iloc[70:131].any())
        self.assertTrue(result.bars.signal_valid.iloc[131])
        self.assertTrue(result.bars.signal.iloc[60:70].notna().any())
        self.assertTrue(result.bars.signal.iloc[131:].notna().any())

    def test_short_bar_cannot_trigger_before_1330(self):
        prices = [100.] * 63 + [99., 110.]
        history = analyze_prices(prices)
        cutoff = history.bars.bar_end.iloc[-1] - timedelta(seconds=1)
        before = package.generate_signals(analyze_prices(prices, cutoff=cutoff))
        after = package.generate_signals(history)
        self.assertFalse(any(e['signal_type'] == 'BUY' for e in before.signals))
        self.assertEqual(after.signals[-1]['signal_type'], 'BUY')
        self.assertEqual(after.bars.iloc[-1].duration_minutes, 30)

    def test_every_historical_prefix_matches_full_signals(self):
        prices = [99., 101., 98., 102.] * 20
        full_history = analyze_prices(prices)
        full = package.generate_signals(full_history)
        for i in range(58, len(full.bars)):
            cutoff = full.bars.bar_end.iloc[i]
            prefix = package.generate_signals(analyze_prices(prices, cutoff=cutoff))
            expected = [s for s in full.signals if pd.Timestamp(s['signal_time']) <= cutoff]
            self.assertEqual(prefix.signals, expected)
            pd.testing.assert_series_equal(prefix.bars.signal, full.bars.signal.iloc[:i+1])
            pd.testing.assert_series_equal(prefix.bars.signal_valid, full.bars.signal_valid.iloc[:i+1])

    def test_future_poison_does_not_change_past_signals(self):
        prices = [99., 101., 98., 102.] * 20
        history = analyze_prices(prices)
        cutoff = history.bars.bar_end.iloc[69]
        expected = package.generate_signals(analyze_prices(prices, cutoff=cutoff))
        poisoned = prices[:70] + [float('nan')] * 10
        actual = package.generate_signals(analyze_prices(poisoned, cutoff=cutoff))
        self.assertEqual(actual.signals, expected.signals)
        pd.testing.assert_frame_equal(actual.bars, expected.bars)

    def test_signal_calculation_never_fetches_or_recalculates_ma(self):
        history = analyze_prices([100.] * 60 + [101., 102., 90.])
        with (patch.object(package, 'fetch_history', side_effect=AssertionError('Unexpected IO')),
              patch.object(package, 'analyze_history', side_effect=AssertionError('Unexpected MA calculation'))):
            result = package.generate_signals(history)
        pd.testing.assert_series_equal(result.bars.MA60, history.bars.MA60)
        self.assertEqual(len(result.signals), 2)


class CliTests(unittest.TestCase):
    def run_cli(self, with_signals):
        import yfinance as yf
        from types import SimpleNamespace
        history = analyze_prices([100.] * 60 + [101., 102., 90.])
        with TemporaryDirectory(prefix='666-signals-test-') as temporary:
            output = Path(temporary) / 'output.json'
            arguments = ['2408.TW', '--output', str(output)] + (['--signals'] if with_signals else [])
            with patch.object(cli, 'fetch_history', return_value=history), \
                 patch.object(cli, 'SnapshotProvider', return_value=SimpleNamespace(snapshots=[])), \
                 patch.object(yf, 'set_tz_cache_location'), redirect_stdout(StringIO()):
                code = cli.main(arguments)
            return code, json.loads(output.read_text(encoding='utf-8'))

    def test_signal_option_lists_all_events_and_annotates_recent_bars(self):
        code, output = self.run_cli(True)
        self.assertEqual(code, 0)
        item = output['results'][0]
        self.assertEqual([s['signal_type'] for s in item['signal_analysis']['signals']], ['BUY', 'SELL'])
        self.assertIn('signal', item['recent_10'][-1])
        self.assertIn('signal_valid', item['bars'][-1])

    def test_default_cli_remains_first_stage_only(self):
        code, output = self.run_cli(False)
        self.assertEqual(code, 0)
        item = output['results'][0]
        self.assertNotIn('signal_analysis', item)
        self.assertNotIn('signal', item['bars'][-1])


if __name__ == '__main__':
    unittest.main()

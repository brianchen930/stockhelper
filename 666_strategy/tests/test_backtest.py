"""Third-stage fills/accounting and strict complete historical-window contracts."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, time, timedelta
from decimal import Decimal
import importlib
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from test_daily_trend import package, daily_fixture, TAIPEI

bt = importlib.import_module('666_strategy.backtest')
data_module = importlib.import_module('666_strategy.backtest.data')
runner_module = importlib.import_module('666_strategy.backtest.runner')
cli = importlib.import_module('666_strategy.backtest.__main__')
main_cli = importlib.import_module('666_strategy.__main__')
NOW = pd.Timestamp('2026-12-01T14:00:00+08:00')
ZERO_COST = bt.BacktestConfig(initial_cash=10000, commission_rate=0, minimum_commission=0, sell_tax_rate=0)


def fixture(closes=None, opens=None, *, omitted_sessions=(), daily_failure=None):
    """Explicit synthetic calendar; daily and 60m prices intentionally independent."""
    daily, original = daily_fixture()
    sessions = tuple(day for i, day in enumerate(original.sessions) if i not in omitted_sessions)
    calendar = package.SessionCalendar(sessions, original.start_date, original.through_date, 'synthetic complete calendar')
    daily = daily.loc[[day in sessions for day in daily.index.date]]
    closes = [101., 102., 99., 99., 99.] if closes is None else list(closes)
    if len(closes) % 5:
        raise ValueError('Use complete five-bar sessions in fixtures')
    start = original.sessions[125]
    trade_days = tuple(day for day in sessions if day >= start)[:len(closes)//5]
    prior = [day for day in sessions if day < start][-12:]
    grid = pd.DatetimeIndex([datetime.combine(day, time(hour), TAIPEI)
        for day in (*prior, *trade_days) for hour in (9, 10, 11, 12, 13)])
    prices = [100.]*60 + closes
    opening = [100.]*60 + (closes if opens is None else list(opens))
    raw = pd.DataFrame(dict(Open=opening, Close=prices,
        High=[max(a,b)+1 for a,b in zip(opening,prices)],
        Low=[min(a,b)-1 for a,b in zip(opening,prices)], Volume=1000.), index=grid)
    daily['Volume'] = 1000.
    if daily_failure is not None:
        day_index, price = daily_failure
        daily.loc[daily.index.date == trade_days[day_index], 'Close'] = price
    return raw, daily, calendar, start, trade_days[-1]


def prepare(parts):
    raw, daily, calendar, start, end = parts
    return bt.prepare_data(raw, daily, symbol='2330.TW', start=start, end=end, calendar=calendar, now=NOW)


class BacktestFillTests(unittest.TestCase):
    def test_next_bar_open_is_used_instead_of_signal_close(self):
        data = prepare(fixture(opens=[101, 125, 99, 95, 99]))
        result = bt.run_backtest(data, config=ZERO_COST)
        self.assertEqual([f['action'] for f in result.fills], ['BUY', 'SELL'])
        self.assertEqual([f['price'] for f in result.fills], [125, 95])
        self.assertEqual([f['shares'] for f in result.fills], [80, 80])
        self.assertEqual(result.summary['total_net_profit'], -2400)
        for fill in result.fills:
            event = next(e for e in result.raw_signals if e['signal_time'] == fill['signal_time'])
            self.assertGreater(pd.Timestamp(fill['execution_bar_start']), pd.Timestamp(event['bar_start']))
            self.assertGreaterEqual(pd.Timestamp(fill['execution_time']), pd.Timestamp(event['bar_end']))

    def test_tail_bar_buy_fills_next_session_open(self):
        parts = fixture([100]*4+[101]+[102]*5, opens=[100]*4+[101]+[150]*5)
        result = bt.run_backtest(prepare(parts), config=ZERO_COST)
        self.assertEqual(len(result.fills), 1)
        fill = result.fills[0]
        self.assertIn('T13:30:00', fill['signal_time'])
        self.assertIn('T09:00:00', fill['execution_time'])
        self.assertEqual(fill['price'], 150)
        self.assertGreater(pd.Timestamp(fill['execution_time']).date(), pd.Timestamp(fill['signal_time']).date())

    def test_overnight_execution_skips_explicit_holiday(self):
        parts = fixture([100]*4+[101]+[102]*5, omitted_sessions=(126,))
        result = bt.run_backtest(prepare(parts), config=ZERO_COST)
        actual = pd.Timestamp(result.fills[0]['execution_time']).date()
        self.assertEqual(actual, parts[4])
        self.assertNotIn(daily_fixture()[1].sessions[126], parts[2].sessions)

    def test_sell_and_daily_exit_produce_one_next_open_exit(self):
        parts = fixture([101,102,103,104,90]+[90]*5, daily_failure=(0, 1.))
        result = bt.run_backtest(prepare(parts), config=ZERO_COST)
        self.assertEqual([f['action'] for f in result.fills], ['BUY', 'EXIT'])
        self.assertEqual(len(result.trades), 1)
        invalid = next(e for e in result.events if e.get('raw_signal') == 'SELL')
        self.assertEqual(invalid['pending_decision']['action'], 'EXIT')
        self.assertEqual(pd.Timestamp(result.fills[1]['execution_time']).date(), parts[4])
        self.assertIn('T09:00:00', result.fills[1]['execution_time'])

    def test_daily_exit_without_raw_sell(self):
        parts = fixture([101,102,103,104,105]+[106]*5, daily_failure=(0, 1.))
        result = bt.run_backtest(prepare(parts), config=ZERO_COST)
        self.assertNotIn('SELL', [e['signal_type'] for e in result.raw_signals])
        self.assertEqual([f['action'] for f in result.fills], ['BUY', 'EXIT'])

    def test_unknown_at_buy_execution_cancels_without_exit_or_later_revival(self):
        data = prepare(fixture([101,102,103,104,105]))
        original = data.daily.snapshot_at
        first_open = data.history.bars.index[61]
        observations = 0
        def snapshots(at):
            nonlocal observations
            snap = original(at)
            if pd.Timestamp(at) == first_open:
                observations += 1
                if observations >= 2:  # prior close confirmed; next open loses coverage
                    snap = dict(snap, status='UNKNOWN', reason='SOURCE_UNAVAILABLE_AT_EXECUTION')
            return snap
        # Model an execution-time source failure. Normal static historical gaps
        # fail in prepare_data; the inherited decision contract still blocks BUY.
        with patch.object(data.daily, 'snapshot_at', side_effect=snapshots):
            result = bt.run_backtest(data, config=ZERO_COST)
        self.assertEqual(result.fills, [])
        self.assertIsNone(result.open_position)
        self.assertTrue(any(e['reason'] == 'BUY_CANCELLED_TREND_RECHECK' for e in result.events))

    def test_buy_is_rechecked_using_completed_daily_data_at_execution(self):
        data = prepare(fixture([100]*4+[101]+[102]*5))
        original = runner_module.confirm_execution
        checked = []
        def confirm(state, **options):
            checked.append((options['at'], options['daily'].snapshot_at(options['at'])))
            return original(state, **options)
        with patch.object(runner_module, 'confirm_execution', side_effect=confirm):
            result = bt.run_backtest(data, config=ZERO_COST)
        self.assertEqual(len(checked), 1)
        at, snap = checked[0]
        self.assertEqual(snap['status'], 'BULL')
        self.assertLess(pd.Timestamp(snap['daily_close_time']), pd.Timestamp(at))
        self.assertEqual(snap['daily_close_time'], result.fills[0]['trend_close_time'])

    def test_non_bull_blocks_original_buy_but_keeps_signal(self):
        parts = list(fixture())
        parts[1]['Close'] = 100.
        result = bt.run_backtest(prepare(parts), config=ZERO_COST)
        self.assertTrue(any(e['signal_type'] == 'BUY' for e in result.raw_signals))
        self.assertEqual(result.fills, [])
        self.assertTrue(any(e['reason'] == 'BUY_BLOCKED_NON_BULL' for e in result.events))

    def test_no_new_signal_means_no_position_even_when_bull(self):
        result = bt.run_backtest(prepare(fixture([100]*5)), config=ZERO_COST)
        self.assertEqual(result.fills, [])
        self.assertIsNone(result.summary['win_rate_percent'])

    def test_first_day_buys_need_fresh_cross_not_above_ma_at_start(self):
        parts = list(fixture([105]*5))
        parts[0].iloc[59, parts[0].columns.get_loc('Close')] = 104.
        parts[0].iloc[59, parts[0].columns.get_loc('High')] = 105.
        result = bt.run_backtest(prepare(parts), config=ZERO_COST)
        self.assertEqual(result.fills, [])

    def test_final_position_is_marked_and_never_forced_sold(self):
        result = bt.run_backtest(prepare(fixture([101,102,103,104,105])), config=ZERO_COST)
        self.assertEqual(len(result.fills), 1)
        self.assertEqual(result.trades, [])
        self.assertEqual(result.open_position['shares'], 98)
        self.assertEqual(result.open_position['mark_price'], 105)
        self.assertAlmostEqual(result.summary['ending_equity'], 10294.)
        self.assertEqual(result.summary['unrealized_net_profit'], 294.)
        self.assertFalse(result.summary['period_end_liquidated'])

    def test_final_tail_buy_is_pending_not_filled_after_end(self):
        result = bt.run_backtest(prepare(fixture([100]*4+[101])), config=ZERO_COST)
        self.assertEqual(result.fills, [])
        self.assertIsNone(result.open_position)
        self.assertEqual(result.pending_decision['action'], 'BUY')

    def test_final_exit_is_pending_without_hypothetical_sale(self):
        result = bt.run_backtest(prepare(fixture([101,102,103,104,90], daily_failure=(0, 1.))), config=ZERO_COST)
        self.assertEqual([f['action'] for f in result.fills], ['BUY'])
        self.assertIsNotNone(result.open_position)
        self.assertEqual(result.pending_decision['action'], 'EXIT')

    def test_insufficient_cash_is_reported_and_does_not_create_holdings(self):
        config = bt.BacktestConfig(initial_cash=100, share_unit=1000)
        result = bt.run_backtest(prepare(fixture()), config=config)
        self.assertEqual(result.fills, [])
        self.assertEqual(result.summary['insufficient_cash_buys'], 1)
        self.assertEqual(result.summary['ending_cash'], 100.)

    def test_all_input_frames_and_previous_results_remain_unchanged(self):
        parts = fixture()
        original = [frame.copy(deep=True) for frame in parts[:2]]
        data = prepare(parts)
        first = bt.run_backtest(data, config=ZERO_COST)
        second = bt.run_backtest(data, config=ZERO_COST)
        self.assertEqual(first.to_dict(), second.to_dict())
        for frame, expected in zip(parts[:2], original):
            pd.testing.assert_frame_equal(frame, expected)

    def test_future_prices_and_bad_duplicates_do_not_change_prefix(self):
        parts = list(fixture([101,102,99,99,99]+[100]*5))
        first_end = parts[3]
        expected_parts = parts.copy()
        expected_parts[4] = first_end
        expected = bt.run_backtest(prepare(expected_parts), config=ZERO_COST)
        raw, daily = parts[:2]
        future = raw.index.date > first_end
        raw = raw.copy()
        raw.loc[future, 'Close'] = float('inf')
        parts[0] = pd.concat([raw, raw.loc[future]])
        daily = daily.copy()
        daily.loc[daily.index.date > first_end, 'Close'] = -999
        parts[1] = pd.concat([daily, daily.loc[daily.index.date > first_end]])
        parts[4] = first_end
        actual = bt.run_backtest(prepare(parts), config=ZERO_COST)
        self.assertEqual(expected.to_dict(), actual.to_dict())


class AccountingTests(unittest.TestCase):
    def test_known_net_profit_fees_tax_and_cash_conservation(self):
        config = bt.BacktestConfig(initial_cash=1000, commission_rate=.01, minimum_commission=2, sell_tax_rate=.03)
        result = bt.run_backtest(prepare(fixture(opens=[101,100,99,110,99])), config=config)
        # 9 shares: entry 900 + 9; exit 990 - 9.90 - 29.70 = 950.40.
        trade = result.trades[0]
        self.assertEqual(trade['shares'], 9)
        self.assertEqual(trade['entry_commission'], 9.)
        self.assertEqual(trade['exit_commission'], 9.9)
        self.assertEqual(trade['transaction_tax'], 29.7)
        self.assertEqual(trade['net_profit'], 41.4)
        self.assertAlmostEqual(trade['return_percent'], 41.4/909*100)
        self.assertEqual(result.summary['ending_cash'], 1041.4)
        self.assertEqual(result.summary['win_rate_percent'], 100.)

    def test_minimum_fee_is_included_in_entry_share_sizing(self):
        config = bt.BacktestConfig(initial_cash=1000, commission_rate=.001425, minimum_commission=20)
        self.assertEqual(config.affordable_shares(Decimal('1000'), 100), 9)
        self.assertEqual(config.affordable_shares(Decimal('19'), 1), 0)

    def test_share_unit_and_rounding(self):
        config = bt.BacktestConfig(initial_cash=1_000_000, share_unit=1000)
        self.assertEqual(config.affordable_shares(Decimal('1000000'), 100) % 1000, 0)
        self.assertEqual(config.amounts(1, 1)[1], Decimal('20.00'))
        self.assertEqual(bt.BacktestConfig(minimum_commission=0, commission_rate=.005).amounts(1, 1)[1], Decimal('0.01'))

    def test_config_rejects_invalid_financial_inputs(self):
        for options in [dict(initial_cash=0), dict(initial_cash=float('nan')), dict(commission_rate=-1),
                dict(commission_rate=1), dict(sell_tax_rate=float('inf')), dict(minimum_commission=-1),
                dict(share_unit=0), dict(share_unit=True), dict(initial_cash=True), dict(initial_cash='bad')]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                bt.BacktestConfig(**options)


class BacktestDataTests(unittest.TestCase):
    def test_missing_60m_warmup_or_any_requested_bar_fails(self):
        for index in (0, 59, 60, 62, -1):
            parts = list(fixture())
            parts[0] = parts[0].drop(parts[0].index[index])
            with self.subTest(index=index), self.assertRaisesRegex(bt.BacktestDataError, 'INCOMPLETE_60M'):
                prepare(parts)

    def test_60m_bad_ohlcv_or_duplicate_never_gets_skipped(self):
        for kind in ('invalid', 'duplicate', 'naive'):
            parts = list(fixture())
            if kind == 'invalid':
                parts[0].iloc[62, parts[0].columns.get_loc('Open')] = -1
            elif kind == 'duplicate':
                parts[0] = pd.concat([parts[0], parts[0].iloc[[62]]])
            else:
                parts[0].index = parts[0].index.tz_localize(None)
            with self.subTest(kind=kind), self.assertRaises(bt.BacktestDataError):
                prepare(parts)

    def test_unknown_daily_gap_and_short_warmup_fail_explicitly(self):
        for index in (5, 124, 125):
            parts = list(fixture())
            parts[1] = parts[1].drop(parts[1].index[index])
            with self.subTest(index=index), self.assertRaisesRegex(bt.BacktestDataError, 'INCOMPLETE_DAILY'):
                prepare(parts)
        parts = list(fixture())
        parts[2] = package.SessionCalendar(parts[2].sessions[10:], parts[2].sessions[10], parts[2].through_date)
        with self.assertRaisesRegex(bt.BacktestDataError, 'WARMUP_CALENDAR_INSUFFICIENT'):
            prepare(parts)

    def test_closed_zero_daily_artifact_does_not_add_bar_or_trade(self):
        parts = list(fixture([100]*4+[101]+[102]*5, omitted_sessions=(126,)))
        closed = daily_fixture()[1].sessions[126]
        stamp = pd.Timestamp(datetime.combine(closed, time(), TAIPEI))
        parts[1].loc[stamp] = [999999., 0.]
        data = prepare(parts)
        self.assertNotIn(stamp, data.daily.bars.index)
        result = bt.run_backtest(data, config=ZERO_COST)
        self.assertEqual(len(result.fills), 1)

    def test_invalid_dates_future_end_empty_sessions_and_calendar_coverage(self):
        parts = fixture()
        with self.assertRaisesRegex(bt.BacktestDataError, 'INVALID_RANGE'):
            bt.prepare_data(parts[0], parts[1], symbol='2330.TW', start=parts[4]+timedelta(days=1),
                end=parts[3], calendar=parts[2], now=NOW)
        with self.assertRaisesRegex(bt.BacktestDataError, 'UNFINISHED_RANGE'):
            bt.prepare_data(parts[0], parts[1], symbol='2330.TW', start=parts[3], end=parts[4],
                calendar=parts[2], now=pd.Timestamp(datetime.combine(parts[4], time(13, 29), TAIPEI)))
        with self.assertRaisesRegex(bt.BacktestDataError, 'NO_TRADING_SESSIONS'):
            data_module.data_window(parts[3]-timedelta(days=2), parts[3]-timedelta(days=1), calendar=parts[2])

    def test_live_730_day_guard_happens_before_network_or_unknown_calendar(self):
        class NoIO:
            def history(self, *args, **kwargs):
                raise AssertionError('Network should not be called')
        with self.assertRaisesRegex(bt.BacktestDataError, 'YAHOO_60M_RANGE'):
            bt.fetch_backtest_data('2330', '2023-09-01', '2023-10-01', provider=NoIO(), now=NOW)

    def test_live_730_day_guard_includes_warmup(self):
        parts = fixture()
        now = pd.Timestamp(datetime.combine(parts[3]+timedelta(days=725), time(14), TAIPEI))
        class NoIO:
            def history(self, *args, **kwargs):
                raise AssertionError('Network should not be called')
        with self.assertRaisesRegex(bt.BacktestDataError, 'YAHOO_60M_RANGE'):
            bt.fetch_backtest_data('2330', parts[3], parts[4], provider=NoIO(), calendar=parts[2], now=now)

    def test_actual_truncated_response_fails_without_shortening_or_market_fallback(self):
        parts = fixture()
        calls = []
        class Truncated:
            def history(self, symbol, *, interval, **kwargs):
                calls.append((symbol, interval))
                return parts[0].iloc[20:] if interval == '60m' else parts[1]
        with self.assertRaisesRegex(bt.BacktestDataError, 'INCOMPLETE_60M'):
            bt.fetch_backtest_data('2330', parts[3], parts[4], provider=Truncated(), calendar=parts[2], now=NOW)
        self.assertEqual(calls, [('2330.TW','60m'), ('2330.TW','1d')])

    def test_empty_source_and_api_failure_are_clear_errors(self):
        parts = fixture()
        class Empty:
            def history(self, *args, **kwargs):
                return pd.DataFrame()
        with self.assertRaisesRegex(bt.BacktestDataError, 'YAHOO_60M_UNAVAILABLE'):
            bt.fetch_backtest_data('2330', parts[3], parts[4], provider=Empty(), calendar=parts[2], now=NOW)
        class Broken:
            def history(self, *args, **kwargs):
                raise OSError('unavailable')
        with self.assertRaisesRegex(bt.BacktestDataError, 'YAHOO_SOURCE_ERROR'):
            bt.fetch_backtest_data('2330.TW', parts[3], parts[4], provider=Broken(), calendar=parts[2], now=NOW)

    def test_bare_symbol_resolves_twowith_exact_requested_range(self):
        parts = fixture()
        calls = []
        class Provider:
            def history(self, symbol, *, interval, **kwargs):
                calls.append((symbol, interval, kwargs))
                if symbol.endswith('.TW'):
                    return pd.DataFrame()
                return parts[0] if interval == '60m' else parts[1]
        data = bt.fetch_backtest_data('6488', parts[3], parts[4], provider=Provider(), calendar=parts[2], now=NOW)
        self.assertEqual(data.symbol, '6488.TWO')
        self.assertEqual(calls[1][2]['start'], data.window.minute_start)
        self.assertEqual(calls[-1][2]['start'], data.window.daily_start)


class BacktestCliTests(unittest.TestCase):
    def test_json_csv_outputs_include_open_position_and_empty_trade_headers(self):
        result = bt.run_backtest(prepare(fixture([101,102,103,104,105])), config=ZERO_COST)
        with TemporaryDirectory(prefix='666-backtest-') as temporary:
            paths = bt.export_backtest(result, Path(temporary)/'result.json')
            saved = json.loads(Path(paths['json']).read_text(encoding='utf-8'))
            self.assertEqual(saved['open_position']['shares'], 98)
            self.assertIn('net_profit', Path(paths['trades']).read_text(encoding='utf-8-sig'))
            self.assertIn('BUY', Path(paths['fills']).read_text(encoding='utf-8-sig'))

    def test_command_accepts_original_symbol_start_end_options(self):
        data = prepare(fixture())
        with TemporaryDirectory(prefix='666-backtest-') as temporary:
            output = Path(temporary)/'result.csv'
            with patch.object(cli, 'fetch_backtest_data', return_value=data), \
                 patch.object(cli, 'SnapshotProvider'), redirect_stdout(StringIO()):
                code = cli.main(['--symbol','2330','--start',data.start.isoformat(),'--end',data.end.isoformat(),
                    '--source-snapshot','test.json','--output',str(output)])
            self.assertEqual(code, 0)
            self.assertTrue(output.is_file())
            self.assertTrue(output.with_suffix('.json').is_file())

    def test_parent_cli_routes_backtest_without_starting_original_app(self):
        with patch.object(cli, 'main', return_value=0) as entry:
            self.assertEqual(main_cli.main(['backtest','--symbol','2330']), 0)
        entry.assert_called_once_with(['--symbol','2330'])
        self.assertNotIn('app.main', sys.modules)

    def test_cli_accepts_explicit_verified_calendar_for_unbundled_years(self):
        data = prepare(fixture())
        calendar = data.window.calendar
        with TemporaryDirectory(prefix='666-calendar-') as temporary:
            path = Path(temporary)/'calendar.json'
            path.write_text(json.dumps(dict(sessions=[day.isoformat() for day in calendar.sessions],
                start_date=calendar.start_date.isoformat(), through_date=calendar.through_date.isoformat(),
                source='test verified calendar')), encoding='utf-8')
            with patch.object(cli, 'fetch_backtest_data', return_value=data) as fetch, \
                 patch.object(cli, 'SnapshotProvider'), redirect_stdout(StringIO()):
                code = cli.main(['--symbol','2330','--start',data.start.isoformat(),'--end',data.end.isoformat(),
                    '--source-snapshot','test.json','--calendar',str(path),'--output',str(Path(temporary)/'out.json')])
            self.assertEqual(code, 0)
            self.assertEqual(fetch.call_args.kwargs['calendar'].sessions, calendar.sessions)

    def test_snapshot_cutoff_is_checked_before_replay(self):
        with TemporaryDirectory(prefix='666-source-') as temporary:
            path = Path(temporary)/'snapshot.json'
            path.write_text(json.dumps(dict(as_of='2026-10-28T13:30:00+08:00',source_snapshots=[])), encoding='utf-8')
            provider = bt.SnapshotProvider(path)
            with self.assertRaisesRegex(bt.BacktestDataError, 'SNAPSHOT_AS_OF'):
                bt.fetch_backtest_data('2330','2026-09-17','2026-10-29',provider=provider,now=NOW)

    def test_failure_does_not_write_partial_backtest(self):
        with TemporaryDirectory(prefix='666-backtest-') as temporary:
            output = Path(temporary)/'failed.json'
            with patch.object(cli, 'fetch_backtest_data', side_effect=bt.BacktestDataError('INCOMPLETE_60M','missing')), \
                 patch.object(cli, 'SnapshotProvider'), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    cli.main(['--symbol','2330','--start','2026-09-17','--end','2026-10-29',
                        '--source-snapshot','test.json','--output',str(output)])
            self.assertEqual(raised.exception.code, 1)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()

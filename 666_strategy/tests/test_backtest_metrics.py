"""Descriptive metrics and calendar extension without changing the strategy."""
from datetime import date
from decimal import Decimal
import importlib
from pathlib import Path
import unittest

import pandas as pd

from test_backtest import bt, fixture, prepare, ZERO_COST

metrics = importlib.import_module('666_strategy.backtest.metrics')
calendar_module = importlib.import_module('666_strategy.calendar')


class PerformanceMetricTests(unittest.TestCase):
    def test_peak_to_trough_drawdown_has_known_positive_percent(self):
        curve=[dict(at=str(i),equity=value) for i,value in enumerate([1000,1200,900,1100,800,1000])]
        result=metrics.performance_metrics(curve,[],1000)
        self.assertAlmostEqual(result['max_drawdown_percent'],100/3)
        self.assertEqual(result['drawdown_peak_at'],'1')
        self.assertEqual(result['drawdown_trough_at'],'4')

    def test_initial_capital_captures_entry_fee_drawdown(self):
        result=metrics.performance_metrics([dict(at='entry',equity=980)],[],1000)
        self.assertEqual(result['max_drawdown_percent'],2.)
        self.assertIsNone(result['drawdown_peak_at'])

    def test_rising_equity_and_no_trades(self):
        result=metrics.performance_metrics([dict(at='a',equity=1100),dict(at='b',equity=1200)],[],1000)
        self.assertEqual(result['max_drawdown_percent'],0.)
        self.assertIsNone(result['average_holding_days'])

    def test_elapsed_holding_days_include_weekends_and_fractional_days(self):
        trades=[dict(entry_time='2026-09-24T13:00:00+08:00',exit_time='2026-09-29T10:00:00+08:00'),
                dict(entry_time='2026-10-14T11:00:00+08:00',exit_time='2026-10-14T13:00:00+08:00')]
        result=metrics.performance_metrics([],trades,1000)
        self.assertAlmostEqual(result['average_holding_days'],((5-3/24)+(2/24))/2)

    def test_buy_hold_includes_entry_fee_and_same_share_sizing_without_fake_exit(self):
        data=prepare(fixture(opens=[100,100,100,100,100]))
        bars=data.history.bars.tail(5).copy()
        bars.iloc[-1,bars.columns.get_loc('Close')]=110
        config=bt.BacktestConfig(initial_cash=1000,commission_rate=.01,minimum_commission=0,sell_tax_rate=.5)
        result=metrics.buy_and_hold(bars,config)
        self.assertEqual(result['shares'],9)
        self.assertEqual(result['entry_commission'],9.)
        self.assertEqual(result['ending_equity'],1081.)
        self.assertAlmostEqual(result['return_percent'],8.1)
        self.assertEqual(result['price_return_percent'],10.)
        self.assertFalse(result['end_liquidated'])

    def test_buy_hold_no_affordable_unit_is_cash_not_a_fake_position(self):
        data=prepare(fixture())
        result=metrics.buy_and_hold(data.history.bars.tail(5),bt.BacktestConfig(initial_cash=10,share_unit=1000))
        self.assertEqual(result['shares'],0)
        self.assertEqual(result['return_percent'],0.)
        self.assertIsNone(result['entry_time'])

    def test_equity_curve_final_mark_matches_summary_and_keeps_trade_dates(self):
        result=bt.run_backtest(prepare(fixture(opens=[101,125,99,95,99])),config=ZERO_COST)
        self.assertEqual(len(result.equity_curve),10)
        self.assertEqual(result.equity_curve[-1]['equity'],result.summary['ending_equity'])
        self.assertEqual([f['price'] for f in result.fills],[125,95])
        self.assertEqual(result.summary['total_net_profit'],-2400.)
        self.assertEqual(result.trades[0]['holding_days'],2/24)
        self.assertEqual(result.summary['average_holding_days'],2/24)
        self.assertGreater(result.summary['max_drawdown_percent'],0)

    def test_open_duration_does_not_enter_closed_average_or_force_sale(self):
        result=bt.run_backtest(prepare(fixture([101,102,103,104,105])),config=ZERO_COST)
        self.assertIsNone(result.summary['average_holding_days'])
        self.assertEqual(result.open_position['holding_days'],3.5/24)
        self.assertEqual(len(result.fills),1)
        self.assertEqual(result.trades,[])

    def test_open_equity_mark_uses_open_not_future_close(self):
        result=bt.run_backtest(prepare(fixture([101,102,103,104,105],opens=[100,125,130,140,150])),config=ZERO_COST)
        point=next(p for p in result.equity_curve if p['phase']=='OPEN' and p['shares']>0)
        self.assertEqual(point['mark_price'],125.)
        self.assertEqual(point['equity'],10000.)
        self.assertEqual(result.benchmark['entry_price'],100.)

    def test_2025_revised_calendar_and_stock_specific_lunar_new_year_days(self):
        calendar,_=calendar_module.bundled_calendar(date(2025,1,1),date(2025,12,31))
        for day in ('2025-01-23','2025-01-24','2025-02-08','2025-09-29','2025-10-06','2025-10-24','2025-12-25'):
            self.assertNotIn(date.fromisoformat(day),calendar.sessions)
        for day in ('2025-01-22','2025-02-03','2025-10-30'):
            self.assertIn(date.fromisoformat(day),calendar.sessions)

    def test_annual_calendar_warmup_dates_and_period_are_not_shortened(self):
        module=importlib.import_module('666_strategy.backtest.data')
        window=module.data_window(date(2025,10,30),date(2026,10,29))
        self.assertEqual(window.sessions[0],date(2025,10,30))
        self.assertEqual(window.sessions[-1],date(2026,10,29))
        self.assertEqual(len(window.sessions),241)
        self.assertEqual(window.minute_start.isoformat(),'2025-10-13T09:00:00+08:00')
        self.assertEqual(window.daily_start.isoformat(),'2025-05-08T00:00:00+08:00')


class AnnualSourceContractTests(unittest.TestCase):
    def setUp(self):
        path=Path(__file__).resolve().parents[1]/'verification/annual_source_20251030_20261029.json'
        self.provider=bt.SnapshotProvider(path)
        self.module=importlib.import_module('666_strategy.backtest.data')
        self.window=self.module.data_window(date(2025,10,30),date(2026,10,29))

    def test_annual_source_has_internal_invalid_bars_even_though_latest_status_is_ok(self):
        history_module=importlib.import_module('666_strategy.history')
        for symbol in ('2330.TW','2408.TW'):
            result=history_module.analyze_history(self.provider.frames[symbol,'60m'],symbol=symbol,
                calendar=self.window.calendar,request_start=self.window.minute_start,as_of=self.window.cutoff)
            self.assertEqual(result.status,'OK')  # latest window alone is insufficient proof
            self.assertEqual(len(result.bars),1265)
            bad=result.bars.loc[~result.bars.is_valid]
            self.assertEqual(len(bad),10)
            self.assertEqual(set(bad.index.date),{date(2026,2,2),date(2026,4,20)})

    def test_annual_backtest_entrypoint_rejects_full_range_without_shortening(self):
        for symbol in ('2330.TW','2408.TW'):
            with self.assertRaisesRegex(bt.BacktestDataError,'INCOMPLETE_60M') as raised:
                bt.fetch_backtest_data(symbol,'2025-10-30','2026-10-29',provider=self.provider)
            self.assertEqual(raised.exception.details['expected_bars'],1265)
            self.assertEqual(raised.exception.details['required_start'],'2025-10-13T09:00:00+08:00')

    def test_daily_warmup_gap_is_unknown_not_inferred_as_holiday(self):
        daily_module=importlib.import_module('666_strategy.daily')
        self.assertIn(date(2025,8,1),self.window.calendar.sessions)
        for symbol in ('2330.TW','2408.TW'):
            result=daily_module.analyze_daily_trend(self.provider.frames[symbol,'1d'],symbol=symbol,
                calendar=self.window.calendar,request_start=self.window.daily_start,as_of=self.window.cutoff)
            self.assertEqual(result.snapshot_at('2025-10-30T09:00:00+08:00')['status'],'UNKNOWN')
            self.assertTrue(any(issue['code']=='MISSING_OR_INVALID_DAILY_CLOSE' and
                issue['time'].startswith('2025-08-01') for issue in result.issues))


if __name__ == '__main__':
    unittest.main()

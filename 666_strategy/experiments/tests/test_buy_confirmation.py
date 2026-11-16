"""Behavioral research contracts, independent of real quote acquisitions."""
import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tests'))
from test_backtest import fixture, prepare, ZERO_COST, bt

experiment = importlib.import_module('666_strategy.experiments.buy_confirmation')


class ConfirmationTests(unittest.TestCase):
    def run_b(self, closes, opens=None, **kwargs):
        return experiment.run_confirmed_backtest(prepare(fixture(closes, opens=opens, **kwargs)), config=ZERO_COST)

    def test_confirmation_uses_next_close_and_following_open(self):
        result = self.run_b([101,102,103,99,99], [101,120,130,90,85])
        self.assertEqual([f['action'] for f in result.fills], ['BUY','SELL'])
        buy, sell = result.fills
        self.assertIn('T10:00:', buy['signal_time'])
        self.assertIn('T11:00:', buy['confirmation_time'])
        self.assertIn('T11:00:', buy['execution_time'])
        self.assertEqual(buy['price'], 130)
        self.assertEqual(sell['price'], 85)
        self.assertEqual(buy['shares'], 76)
        self.assertEqual(result.summary['total_net_profit'], -3420)

    def test_failed_confirmation_does_not_revive(self):
        result = self.run_b([101,99,98,98,98])
        self.assertEqual(result.fills, [])
        self.assertEqual(result.candidates[0]['status'], 'CANCELLED_CLOSE_NOT_ABOVE_MA60')

    def test_equal_close_fails_strict_comparison(self):
        equal = (5800+101)/59
        data = prepare(fixture([101,equal,99,99,99]))
        # Make equality exact; this specifically tests the strict comparison gate.
        data.history.bars.loc[data.history.bars.index[61], 'MA60'] = equal
        result = experiment.run_confirmed_backtest(data, config=ZERO_COST)
        self.assertEqual(result.fills, [])
        self.assertEqual(result.candidates[0]['status'], 'CANCELLED_CLOSE_NOT_ABOVE_MA60')

    def test_fresh_crossing_can_start_new_candidate_after_cancellation(self):
        result = self.run_b([101,99,102,103,104])
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(result.candidates[0]['status'], 'CANCELLED_CLOSE_NOT_ABOVE_MA60')
        self.assertEqual(result.candidates[1]['status'], 'FILLED')
        self.assertIn('T13:00:', result.fills[0]['execution_time'])

    def test_original_daily_buy_block_still_applies(self):
        data = prepare(fixture([101,102,103,104,105]))
        original = data.daily.snapshot_at
        def nonbull(at):
            return dict(original(at), status='NON_BULL')
        with patch.object(data.daily, 'snapshot_at', side_effect=nonbull):
            result = experiment.run_confirmed_backtest(data, config=ZERO_COST)
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.fills, [])

    def test_daily_filter_rechecked_at_confirmation(self):
        result = self.run_b([100,100,100,101,102]+[103]*5, daily_failure=(0,1.))
        self.assertEqual(result.fills, [])
        self.assertEqual(result.candidates[0]['status'], 'CANCELLED_CONFIRMATION_TREND')

    def test_approved_buy_rechecks_trend_before_execution(self):
        data = prepare(fixture([100,100,100,101,102]+[103]*5))
        original = data.daily.snapshot_at
        first_day = data.start
        def changed(at):
            item = original(at)
            at = pd.Timestamp(at)
            return dict(item, status='NON_BULL') if at.date()>first_day and at.hour==9 else item
        with patch.object(data.daily, 'snapshot_at', side_effect=changed):
            result = experiment.run_confirmed_backtest(data, config=ZERO_COST)
        self.assertEqual(result.fills, [])
        self.assertEqual(result.candidates[0]['status'], 'CANCELLED_AT_EXECUTION')

    def test_1300_signal_confirms_next_session_and_executes_next_native_bar(self):
        result = self.run_b([100]*4+[101]+[102,103,104,105,106],
                            [100]*4+[101]+[150,160,170,180,190], omitted_sessions=(126,))
        buy = result.fills[0]
        self.assertIn('T13:30:', buy['signal_time'])
        self.assertIn('T10:00:', buy['confirmation_time'])
        self.assertIn('T10:00:', buy['execution_time'])
        self.assertEqual(buy['price'],160)
        self.assertGreater(pd.Timestamp(buy['execution_time']).date(),pd.Timestamp(buy['signal_time']).date())

    def test_confirmation_1300_waits_until_next_session_open(self):
        result = self.run_b([100,100,100,101,102]+[103]*5)
        self.assertIn('T13:30:',result.fills[0]['confirmation_time'])
        self.assertIn('T09:00:',result.fills[0]['execution_time'])

    def test_exit_precedence_over_sell_is_unchanged(self):
        data = prepare(fixture([101,102,103,104,90]+[90]*5,daily_failure=(0,1.)))
        result = experiment.run_confirmed_backtest(data,config=ZERO_COST)
        self.assertEqual([f['action'] for f in result.fills],['BUY','EXIT'])
        self.assertIn('T09:00:',result.fills[-1]['execution_time'])
        self.assertEqual(result.fills[-1]['execution_time'],bt.run_backtest(data,config=ZERO_COST).fills[-1]['execution_time'])

    def test_last_signal_and_confirmation_remain_pending_without_future_fill(self):
        waiting = self.run_b([100,100,100,100,101])
        approved = self.run_b([100,100,100,101,102])
        self.assertEqual(waiting.fills,[])
        self.assertIsNotNone(waiting.pending_candidate)
        self.assertIsNone(waiting.pending_decision)
        self.assertEqual(approved.fills,[])
        self.assertIsNone(approved.pending_candidate)
        self.assertIsNotNone(approved.pending_decision)

    def test_future_execution_bar_close_cannot_change_its_open_buy(self):
        left = self.run_b([101,102,110,100,100],[101,102,130,99,99])
        right = self.run_b([101,102,90,100,100],[101,102,130,99,99])
        self.assertEqual(left.fills[0],right.fills[0])


if __name__ == '__main__':
    unittest.main()

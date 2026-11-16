"""Verified 2024 stock-market closures, independent of price availability."""
from datetime import date
import importlib
import unittest

calendar=importlib.import_module('666_strategy.calendar')


class Calendar2024Tests(unittest.TestCase):
    def test_settlement_only_days_and_makeup_saturday_are_not_sessions(self):
        sessions,_=calendar.bundled_calendar(date(2024,1,1),date(2024,12,31))
        self.assertEqual(len(sessions.sessions),242)
        for day in ('2024-02-06','2024-02-07','2024-02-08','2024-02-09','2024-02-14','2024-02-17'):
            self.assertNotIn(date.fromisoformat(day),sessions.sessions)
        for day in ('2024-02-05','2024-02-15','2024-04-03'):
            self.assertIn(date.fromisoformat(day),sessions.sessions)

    def test_five_explicit_typhoon_closures_preserve_adjacent_open_days(self):
        sessions,_=calendar.bundled_calendar(date(2024,1,1),date(2024,12,31))
        for day in ('2024-07-24','2024-07-25','2024-10-02','2024-10-03','2024-10-31'):
            self.assertNotIn(date.fromisoformat(day),sessions.sessions)
        for day in ('2024-07-23','2024-07-26','2024-10-01','2024-10-04','2024-10-30','2024-11-01'):
            self.assertIn(date.fromisoformat(day),sessions.sessions)


if __name__=='__main__':
    unittest.main()

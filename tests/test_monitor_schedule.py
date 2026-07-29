from datetime import datetime
from zoneinfo import ZoneInfo

from app.scheduler import should_run_monitoring


def test_should_run_monitoring_during_trading_hours():
    now = datetime(2026, 7, 28, 10, 30, tzinfo=ZoneInfo("Asia/Taipei"))

    assert should_run_monitoring(now=now)


def test_should_skip_monitoring_outside_trading_hours():
    now = datetime(2026, 7, 28, 8, 30, tzinfo=ZoneInfo("Asia/Taipei"))

    assert not should_run_monitoring(now=now)


def test_force_mode_allows_manual_test_run_outside_trading_hours():
    now = datetime(2026, 7, 28, 8, 30, tzinfo=ZoneInfo("Asia/Taipei"))

    assert should_run_monitoring(now=now, force=True)

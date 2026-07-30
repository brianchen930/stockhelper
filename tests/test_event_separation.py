import pandas as pd
import app.database as database
import app.stock as stock_module

from app.analysis_engine import generate_analysis
from app.analysis.common import split_directional_factors
from app.data_quality import assess_analysis_quality
from app.rules import evaluate_notification
from app.scheduler import build_quality_display_lines, should_include_timeframe_analysis


def _notification(
    current_signal="觀望",
    current_trend="均線糾結",
    previous_signal="觀望",
    previous_trend="均線糾結",
    analysis_is_valid=True,
    issues=None,
):
    return evaluate_notification(
        current_signal=current_signal,
        current_trend=current_trend,
        previous_signal=previous_signal,
        previous_trend=previous_trend,
        reasons=[],
        analysis_is_valid=analysis_is_valid,
        data_quality_issues=issues or [],
    )


def _analysis_data(short_label="中性", medium_label="中性", **overrides):
    data = {
        "close": 100.0,
        "realtime_price": 100.0,
        "price_change_percent": 1.0,
        "analysis": {"signal": "觀望", "trend": "均線糾結"},
        "timeframe_analysis": {
            "short_term": {"label": short_label},
            "medium_term": {"label": medium_label},
        },
    }
    data.update(overrides)
    return data


def test_invalid_analysis_skips_timeframe_summary():
    assert should_include_timeframe_analysis(False, {"short_term": {"label": "偏空"}}) is False
    assert should_include_timeframe_analysis(True, {"short_term": {"label": "偏空"}}) is True


def test_valid_analysis_does_not_emit_quality_warning_text():
    result = build_quality_display_lines(True, {"issues": []}, "2408", "南亞科")

    assert result == []


def test_bearish_macd_reason_is_classified_as_bearish():
    bullish, bearish = split_directional_factors(["MACD 空方動能增強"])

    assert bullish == []
    assert bearish == ["MACD 空方動能增強"]


def test_get_realtime_price_returns_date_field(monkeypatch):
    class FakeTicker:
        def __init__(self, *_args, **_kwargs):
            self.fast_info = {
                "last_price": 100.0,
                "previous_close": 99.0,
                "last_volume": 1000,
            }

        def history(self, **_kwargs):
            return pd.DataFrame(
                {"Close": [100.0], "Volume": [1000]},
                index=[pd.Timestamp("2024-01-03")],
            )

    monkeypatch.setattr(stock_module.yf, "Ticker", lambda *_args, **_kwargs: FakeTicker())

    result = stock_module.get_realtime_price("2408")

    assert result["date"] == "2024-01-03"


def test_invalid_analysis_emits_warning_text():
    result = build_quality_display_lines(False, {"issues": ["latest_close_missing"]}, "2408", "南亞科")

    assert result[0] == "【資料品質警告】"
    assert "略過完整技術分析" in result[1]


def test_normal_signal_change_is_a_scored_market_event():
    result = _notification(current_signal="偏多")

    assert result["score"] == 4
    assert result["should_notify"] is True
    assert result["market_events"]
    assert result["data_quality_events"] == []


def test_normal_trend_change_is_a_scored_market_event():
    result = _notification(current_trend="多頭排列")

    assert result["score"] == 2
    assert result["should_notify"] is True


def test_missing_close_is_data_quality_and_never_scores_or_notifies():
    quality = assess_analysis_quality(_analysis_data(
        close=None,
        realtime_price=None,
        price_change_percent=None,
        analysis={"signal": "無法判斷", "trend": "資料不足"},
    ))
    result = _notification(
        current_signal="無法判斷",
        current_trend="資料不足",
        analysis_is_valid=quality["is_valid"],
        issues=quality["issues"],
    )

    assert result["score"] == 0
    assert result["level"] == "不通知"
    assert result["should_notify"] is False
    assert all(event["score"] == 0 for event in result["data_quality_events"])


def test_latest_source_close_missing_is_invalid_even_with_older_fallback_close():
    data = _analysis_data()
    data["close"] = None
    data["realtime_price"] = None
    data["data_quality"] = {"latest_missing_fields": ["Close"]}

    quality = assess_analysis_quality(data)

    assert quality["is_valid"] is False
    assert quality["issues"] == ["latest_close_missing"]


def test_realtime_price_can_resolve_latest_close_missing():
    data = _analysis_data()
    data["close"] = None
    data["realtime_price"] = 392.5
    data["price_change_percent"] = -9.98
    data["data_quality"] = {"latest_missing_fields": ["Close"]}

    quality = assess_analysis_quality(data)

    assert quality["is_valid"] is True
    assert "latest_close_missing" not in quality["issues"]


def test_one_timeframe_can_be_insufficient_when_the_other_is_valid():
    quality = assess_analysis_quality(_analysis_data(short_label="資料不足"))

    assert quality["is_valid"] is True


def test_both_timeframes_insufficient_disable_market_analysis():
    quality = assess_analysis_quality(
        _analysis_data(short_label="資料不足", medium_label="資料不足")
    )

    assert quality["is_valid"] is False
    assert "insufficient_timeframe_data" in quality["issues"]


def test_repeated_data_issue_never_becomes_a_market_event():
    for _ in range(3):
        result = _notification(
            current_signal="資料不足",
            current_trend="資料不足",
            previous_signal="偏多",
            previous_trend="多頭排列",
            analysis_is_valid=False,
            issues=["latest_close_missing"],
        )
        assert result["score"] == 0
        assert result["market_events"] == []
        assert result["data_alert_notify"] is False


def test_invalid_state_cannot_be_a_signal_or_trend_transition():
    result = _notification(
        current_signal="無法判斷",
        current_trend="資料不足",
        previous_signal="偏多",
        previous_trend="多頭排列",
    )

    assert result["score"] == 0
    assert result["should_notify"] is False


def test_data_issue_does_not_overwrite_last_valid_market_state(tmp_path, monkeypatch):
    db_path = tmp_path / "stocks.db"
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)
    database.create_tables()
    database.add_stock("2408", "南亞科")
    database.update_stock_state("2408", "偏多", "多頭排列")

    database.save_data_quality_issue("2408", "2408:latest_close_missing")
    state = database.get_all_stocks()[0]

    assert state["last_signal"] == "偏多"
    assert state["last_trend"] == "多頭排列"
    assert state["last_data_issue_key"] == "2408:latest_close_missing"


def test_recovery_to_same_valid_state_is_not_a_reversal():
    result = _notification(
        current_signal="偏多",
        current_trend="多頭排列",
        previous_signal="偏多",
        previous_trend="多頭排列",
    )

    assert result["score"] == 0
    assert result["should_notify"] is False


def test_base_strength_ignores_event_score_and_reports_invalid_analysis():
    valid = generate_analysis(
        trend="多頭排列",
        signal="偏多",
        score=99,
        matched_rules=[],
    )
    invalid = generate_analysis(
        trend="資料不足",
        signal="無法判斷",
        score=99,
        matched_rules=["【資料品質警告】"],
        analysis_is_valid=False,
    )

    assert valid["strength"] == "弱"
    assert invalid["strength"] == "資料不足"

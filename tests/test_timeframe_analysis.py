import numpy as np
import pandas as pd

from app.analysis.medium_term import analyze_medium_term
from app.analysis.short_term import analyze_short_term
from app.analysis.common import TimeframeResult, split_directional_factors
from app.analysis.timeframe_summary import (
    analyze_timeframes,
    build_operation_reference,
    format_timeframe_discord,
    summarize_timeframes,
)
from app.indicators import calculate_kd, calculate_macd, calculate_moving_averages, calculate_rsi


def _market_data(direction: int = 1, periods: int = 80) -> pd.DataFrame:
    close = pd.Series(np.linspace(100, 140 if direction > 0 else 60, periods))
    data = pd.DataFrame({
        "Close": close,
        "High": close + 1,
        "Low": close - 1,
        "Volume": np.linspace(1000, 1600, periods),
    })
    data = calculate_moving_averages(data)
    data["RSI14"] = calculate_rsi(data["Close"])
    macd = calculate_macd(data["Close"])
    data[["MACD", "MACD_SIGNAL", "MACD_HIST"]] = macd
    kd = calculate_kd(data["High"], data["Low"], data["Close"])
    data[["KD_K", "KD_D", "KD_J"]] = kd
    return data


def test_bullish_data_produces_independent_positive_views():
    result = analyze_timeframes(_market_data())

    assert result["short_term"]["score"] >= 2
    assert result["medium_term"]["score"] >= 2
    assert result["short_term"]["view"] in {"bullish", "slightly_bullish"}
    assert result["medium_term"]["view"] in {"bullish", "slightly_bullish"}
    assert "方向一致偏多" in result["overall_summary"]
    assert result["short_term"]["bullish_factors"]
    assert "避免追價" in result["operation_reference"]["for_non_holder"]
    assert (result["short_term"]["score_min"], result["short_term"]["score_max"]) == (-8, 8)
    assert (result["medium_term"]["score_min"], result["medium_term"]["score_max"]) == (-9, 9)
    assert not any("量價背離" in warning for warning in result["medium_term"]["warnings"])


def test_bearish_data_does_not_treat_oversold_as_buy_signal():
    data = _market_data(direction=-1)
    short = analyze_short_term(data)
    medium = analyze_medium_term(data)

    assert short["score"] < 0
    assert medium["score"] < 0
    assert any("不代表股價已完成止跌" in warning for warning in short["warnings"])
    assert short["bearish_factors"]
    operation = build_operation_reference(short, medium)
    assert "不再破低" in operation["for_non_holder"]
    assert "風險將進一步升高" in operation["for_holder"]


def test_insufficient_medium_history_is_safe():
    result = analyze_timeframes(_market_data(periods=30))

    assert result["medium_term"]["label"] == "資料不足"
    assert result["medium_term"]["score"] == 0
    assert result["medium_term"]["score_min"] is None
    assert result["medium_term"]["score_max"] is None
    assert "資料不足" in result["overall_summary"]
    assert "資料不足" in result["operation_reference"]["for_non_holder"]
    output = "\n".join(format_timeframe_discord(result))
    assert "None" not in output
    assert "nan" not in output.lower()
    assert "偏多因素：\n\n" not in output


def _result(score: int, warnings: list[str] | None = None) -> TimeframeResult:
    return {
        "view": "bullish" if score >= 2 else "bearish" if score <= -2 else "neutral",
        "label": "偏多" if score >= 2 else "偏空" if score <= -2 else "中性",
        "score": score,
        "score_min": -8,
        "score_max": 8,
        "reasons": [],
        "bullish_factors": [],
        "bearish_factors": [],
        "warnings": warnings or [],
        "summary": "",
    }


def test_short_bullish_medium_bearish_is_treated_as_rebound():
    short, medium = _result(4), _result(-4)
    summary, _ = summarize_timeframes(short, medium)
    operation = build_operation_reference(short, medium)

    assert "技術性反彈" in summary
    assert "站回月線或季線" in operation["for_non_holder"]
    assert "修復失敗" in operation["for_holder"]


def test_short_bearish_medium_bullish_is_treated_as_pullback():
    short, medium = _result(-4), _result(4)
    summary, _ = summarize_timeframes(short, medium)
    operation = build_operation_reference(short, medium)

    assert "波段上升中的整理" in summary
    assert "中期架構尚可" in operation["for_non_holder"]
    assert "支撐未破壞" in operation["for_holder"]


def test_bullish_overheated_case_warns_against_chasing():
    short = _result(4, ["股價接近近期高點，需留意追高風險"])
    medium = _result(4)
    operation = build_operation_reference(short, medium)

    assert "暫緩追價" in operation["for_non_holder"]
    forbidden = ("立即買進", "一定會", "必定", "買點")
    assert not any(text in operation["for_non_holder"] for text in forbidden)


def test_medium_support_and_bearish_factors_are_separated():
    bullish, bearish = split_directional_factors([
        "股價仍位於季線下方",
        "季線方向向上",
    ])

    assert bullish == ["季線方向向上"]
    assert bearish == ["股價仍位於季線下方"]


def test_market_structure_describes_fixed_range_comparison_accurately():
    result = analyze_medium_term(_market_data())
    structure = result["market_structure"]

    assert structure["method"] == "rolling_30d_range_comparison"
    assert "30 日區間" in structure["description"]
    assert "形成較高高點與較高低點" not in structure["description"]


def test_formatter_shows_distinct_score_ranges_and_limits_observations():
    result = analyze_timeframes(_market_data(direction=-1))
    output = "\n".join(format_timeframe_discord(result))

    assert "範圍：-8～+8" in output
    assert "範圍：-9～+9" in output
    assert len(result["operation_reference"]["observation_conditions"]) <= 2
    assert "短期反彈" not in result["medium_term"]["summary"]

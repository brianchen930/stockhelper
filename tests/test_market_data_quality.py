import math

import pandas as pd

from app.analysis.timeframe_summary import analyze_timeframes, format_timeframe_discord
from app.indicators import calculate_kd, calculate_macd, calculate_moving_averages, calculate_rsi
from app.market_data import is_finite_number, normalize_history
from app.stock import classify_market_relative_performance, get_stock_analysis


def _raw_history(periods: int = 118) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=periods, freq="B")
    close = pd.Series([100 + index * 0.2 for index in range(periods)], index=index)
    return pd.DataFrame({
        "Open": close - 0.2,
        "High": close + 1,
        "Low": close - 1,
        "Close": close,
        "Volume": 1000.0,
    }, index=index)


def _enrich(data: pd.DataFrame) -> pd.DataFrame:
    data = calculate_moving_averages(data)
    data["RSI14"] = calculate_rsi(data["Close"])
    macd = calculate_macd(data["Close"])
    data[["MACD", "MACD_SIGNAL", "MACD_HIST"]] = macd
    kd = calculate_kd(data["High"], data["Low"], data["Close"])
    data[["KD_K", "KD_D", "KD_J"]] = kd
    return data


def test_latest_nan_close_uses_last_two_valid_closes():
    raw = _raw_history()
    expected_latest = float(raw["Close"].iloc[-2])
    expected_previous = float(raw["Close"].iloc[-3])
    raw.loc[raw.index[-1], "Close"] = float("nan")

    cleaned, quality = normalize_history(raw)

    assert len(cleaned) == 117
    assert quality["latest_valid_close"] == expected_latest
    assert quality["previous_valid_close"] == expected_previous
    assert "Close" in quality["latest_missing_fields"]
    assert is_finite_number(cleaned["Close"].iloc[-1])


def test_last_row_entirely_nan_is_removed_but_previous_row_remains():
    raw = _raw_history()
    raw.loc[raw.index[-1], :] = float("nan")
    cleaned, quality = normalize_history(raw)

    assert len(cleaned) == 117
    assert quality["latest_valid_date"] == raw.index[-2].strftime("%Y-%m-%d")


def test_nan_volume_does_not_remove_valid_price_row():
    raw = _raw_history()
    raw.loc[raw.index[-1], "Volume"] = float("nan")
    cleaned, quality = normalize_history(raw)

    assert len(cleaned) == 118
    assert "Volume" in quality["missing_fields"]
    assert math.isnan(float(cleaned["Volume"].iloc[-1]))


def test_unaligned_stock_and_benchmark_dates_do_not_create_fake_flat_result():
    result = classify_market_relative_performance(float("nan"), 1.2)

    assert result["label"] == "資料不足"
    assert result["difference"] is None
    assert result["stock_change_percent"] is None


def test_118_valid_rows_are_enough_for_both_timeframes():
    cleaned, _ = normalize_history(_raw_history())
    result = analyze_timeframes(_enrich(cleaned))

    assert result["short_term"]["label"] != "資料不足"
    assert result["medium_term"]["label"] != "資料不足"


def test_118_raw_rows_with_only_17_valid_closes_report_true_counts():
    raw = _raw_history()
    raw.loc[raw.index[:-17], "Close"] = float("nan")
    cleaned, quality = normalize_history(raw)
    result = analyze_timeframes(_enrich(cleaned))
    output = "\n".join(format_timeframe_discord(result))

    assert quality["raw_count"] == 118
    assert quality["valid_count"] == 17
    assert "原始資料 118 筆、有效資料 17 筆、最低需求 20 筆" in output
    assert "nan" not in output.lower()


def test_short_term_can_run_when_medium_history_is_insufficient():
    cleaned, _ = normalize_history(_raw_history(40))
    result = analyze_timeframes(_enrich(cleaned))

    assert result["short_term"]["label"] != "資料不足"
    assert result["medium_term"]["label"] == "資料不足"


def test_missing_kd_only_skips_kd_scoring():
    cleaned, _ = normalize_history(_raw_history())
    data = _enrich(cleaned)
    data[["KD_K", "KD_D", "KD_J"]] = float("nan")
    result = analyze_timeframes(data)

    assert result["short_term"]["label"] != "資料不足"
    assert result["medium_term"]["label"] != "資料不足"
    assert any("KD 資料不足" in warning for warning in result["short_term"]["warnings"])


def test_multiindex_yfinance_columns_are_flattened():
    raw = _raw_history()
    raw.columns = pd.MultiIndex.from_tuples((column, "2408.TW") for column in raw.columns)
    cleaned, quality = normalize_history(raw)

    assert list(cleaned.columns[:5]) == ["Open", "High", "Low", "Close", "Volume"]
    assert quality["valid_count"] == 118


def test_empty_dataframe_is_safe():
    cleaned, quality = normalize_history(pd.DataFrame())
    result = analyze_timeframes(cleaned)

    assert cleaned.empty
    assert quality["valid_count"] == 0
    assert result["short_term"]["label"] == "資料不足"
    assert result["medium_term"]["label"] == "資料不足"


def test_empty_download_returns_displayable_data_quality_result(monkeypatch):
    class EmptyTicker:
        def history(self, **kwargs):
            return pd.DataFrame()

    monkeypatch.setattr("app.stock.resolve_yahoo_symbol", lambda stock_code: f"{stock_code}.TW")
    monkeypatch.setattr("app.stock.yf.Ticker", lambda symbol: EmptyTicker())
    result = get_stock_analysis("2408")

    assert result["close"] is None
    assert result["change_percent"] is None
    assert result["market_relative_performance"]["label"] == "資料不足"
    assert result["data_quality"]["raw_count"] == 0
    assert result["timeframe_analysis"]["short_term"]["label"] == "資料不足"


def test_infinity_is_not_a_valid_number():
    assert not is_finite_number(float("inf"))
    assert not is_finite_number(float("-inf"))
    assert not is_finite_number(float("nan"))

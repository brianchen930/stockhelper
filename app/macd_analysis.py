from typing import Any

from app.market_data import is_finite_number


def analyze_macd(
    macd_line: Any,
    signal_line: Any,
    histogram: Any,
    histogram_previous: Any,
    previous_macd_line: Any = None,
    previous_signal_line: Any = None,
) -> dict[str, Any]:
    values = (macd_line, signal_line, histogram, histogram_previous)
    if not all(is_finite_number(value) for value in values):
        return {
            "macd_line": None,
            "signal_line": None,
            "histogram": None,
            "histogram_previous": None,
            "position": "unknown",
            "zero_axis": "unknown",
            "momentum": "data_insufficient",
            "cross": None,
            "label": "資料不足",
            "description": "MACD 資料不足，無法判讀動能。",
        }

    line = float(macd_line)
    signal = float(signal_line)
    hist = float(histogram)
    previous_hist = float(histogram_previous)
    cross = None
    if is_finite_number(previous_macd_line) and is_finite_number(previous_signal_line):
        previous_line = float(previous_macd_line)
        previous_signal = float(previous_signal_line)
        if previous_line <= previous_signal and line > signal:
            cross = "golden_cross"
        elif previous_line >= previous_signal and line < signal:
            cross = "death_cross"

    if hist > 0 and hist > previous_hist:
        momentum = "bullish_strengthening"
        label = "多方動能增強"
        description = "柱狀體位於零軸上且持續擴大，多方動能增強。"
    elif hist > 0 and hist < previous_hist:
        momentum = "bullish_weakening"
        label = "多方動能減弱"
        description = "柱狀體仍在零軸上，但正在縮小，多方動能減弱。"
    elif hist < 0 and hist < previous_hist:
        momentum = "bearish_strengthening"
        label = "空方動能增強"
        description = "柱狀體位於零軸下且持續擴大，空方動能增強。"
    elif hist < 0 and hist > previous_hist:
        momentum = "bearish_weakening"
        label = "空方動能減弱"
        description = "柱狀體仍在零軸下，但正在縮小，空方動能減弱，但尚未完成多方翻轉。"
    else:
        momentum = "neutral"
        label = "動能持平"
        description = "MACD 柱狀體與前一期接近，動能方向尚未明朗。"

    return {
        "macd_line": line,
        "signal_line": signal,
        "histogram": hist,
        "histogram_previous": previous_hist,
        "position": "above_signal" if line > signal else "below_signal" if line < signal else "at_signal",
        "zero_axis": "above" if line > 0 else "below" if line < 0 else "at_zero",
        "momentum": momentum,
        "cross": cross,
        "label": label,
        "description": description,
    }


def format_macd_summary(result: dict[str, Any]) -> str:
    if result["momentum"] == "data_insufficient":
        return "MACD 資料不足"
    return (
        f"MACD：線 {result['macd_line']:.2f}｜"
        f"訊號線 {result['signal_line']:.2f}｜"
        f"柱狀體 {result['histogram']:.2f}（{result['label']}）"
    )

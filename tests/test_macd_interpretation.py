import pytest

from app.analysis_engine import build_technical_summary
from app.macd_analysis import analyze_macd
from app.rules.macd_rule import MACDRule


@pytest.mark.parametrize(
    ("histogram", "previous", "momentum", "label"),
    [
        (-1.2, -1.8, "bearish_weakening", "空方動能減弱"),
        (-1.8, -1.2, "bearish_strengthening", "空方動能增強"),
        (1.8, 1.2, "bullish_strengthening", "多方動能增強"),
        (1.2, 1.8, "bullish_weakening", "多方動能減弱"),
    ],
)
def test_macd_histogram_momentum_states(histogram, previous, momentum, label):
    result = analyze_macd(2.98, 4.20, histogram, previous)

    assert result["position"] == "below_signal"
    assert result["zero_axis"] == "above"
    assert result["momentum"] == momentum
    assert result["label"] == label


def test_positive_macd_line_below_signal_is_not_described_as_bullish():
    result = analyze_macd(2.98, 4.20, -1.22, -1.80)

    assert result["label"] == "空方動能減弱"
    assert "尚未完成多方翻轉" in result["description"]


def test_macd_insufficient_data():
    result = analyze_macd(None, 4.20, None, -1.80)

    assert result["momentum"] == "data_insufficient"
    assert result["label"] == "資料不足"


def test_technical_summary_and_rule_share_macd_interpretation():
    macd = analyze_macd(2.98, 4.20, -1.22, -1.80, 3.0, 4.0)
    summary = build_technical_summary({
        "macd_analysis": macd,
        "rsi": None,
        "kd_j": None,
        "ma5": None,
        "ma20": None,
        "ma60": None,
    })
    rule = MACDRule().evaluate({
        "macd": 2.98,
        "macd_signal": 4.20,
        "macd_histogram": -1.22,
        "previous_macd": 3.0,
        "previous_macd_signal": 4.0,
        "previous_macd_histogram": -1.80,
        "macd_analysis": macd,
    })

    assert "空方動能減弱" in summary
    assert any("空方動能減弱" in message for message in rule["messages"])
    assert "MACD：線 2.98｜訊號線 4.20｜柱狀體 -1.22" in summary

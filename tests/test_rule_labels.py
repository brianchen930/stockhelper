from app.rules.kd_rule import KDRule
from app.rules.macd_rule import MACDRule
from app.rules.rsi_rule import RSIRule


def test_bearish_macd_message_has_no_positive_directional_score():
    result = MACDRule().evaluate({
        "macd": -2.0,
        "macd_signal": -1.0,
        "macd_histogram": -2.0,
        "previous_macd": -1.5,
        "previous_macd_signal": -1.0,
        "previous_macd_histogram": -1.0,
    })

    assert any("空方規則命中" in message for message in result["messages"])
    assert not any("【MACD +" in message for message in result["messages"])


def test_kd_oversold_is_risk_not_buy_signal():
    result = KDRule().evaluate({
        "kd_k": 10.0,
        "kd_d": 12.0,
        "kd_j": 6.0,
        "previous_kd_k": 11.0,
        "previous_kd_d": 13.0,
    })

    assert any("尚未確認止跌" in message for message in result["messages"])
    assert not any("買點" in message for message in result["messages"])


def test_neutral_rsi_label_is_explicit():
    result = RSIRule().evaluate({"rsi": 45.0, "current_signal": "觀望"})

    assert result["messages"] == ["【RSI｜中性 0】RSI 為 45.00，位於正常區間。"]

from app.stock import classify_market_relative_performance


def test_classify_market_relative_performance_labels():
    assert classify_market_relative_performance(3.2, 0.8)["label"] == "優於大盤"
    assert classify_market_relative_performance(1.8, 0.8)["label"] == "略優於大盤"
    assert classify_market_relative_performance(0.2, 0.3)["label"] == "持平大盤"
    assert classify_market_relative_performance(-0.8, 0.2)["label"] == "略弱於大盤"
    assert classify_market_relative_performance(-3.1, 0.2)["label"] == "弱於大盤"


def test_classify_market_relative_performance_returns_difference():
    result = classify_market_relative_performance(1.5, 0.5)

    assert result["difference"] == 1.0
    assert result["label"] == "略優於大盤"

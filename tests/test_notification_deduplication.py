from app.scheduler import build_notification_signature


def test_signature_is_stable_for_same_signal_state():
    first = build_notification_signature(
        current_signal="偏多",
        current_trend="多頭排列",
        score=5,
        matched_rules=["【MACD +2】形成黃金交叉"],
        summary="多頭趨勢偏強",
    )

    second = build_notification_signature(
        current_signal="偏多",
        current_trend="多頭排列",
        score=5,
        matched_rules=["【MACD +2】形成黃金交叉"],
        summary="多頭趨勢偏強",
    )

    assert first == second


def test_signature_changes_when_state_changes():
    first = build_notification_signature(
        current_signal="偏多",
        current_trend="多頭排列",
        score=5,
        matched_rules=["【MACD +2】形成黃金交叉"],
        summary="多頭趨勢偏強",
    )

    second = build_notification_signature(
        current_signal="偏空",
        current_trend="空頭排列",
        score=5,
        matched_rules=["【MACD +2】形成黃金交叉"],
        summary="多頭趨勢偏強",
    )

    assert first != second

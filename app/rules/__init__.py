from app.rules.engine import RuleEngine


rule_engine = RuleEngine()


def evaluate_notification(
    current_signal,
    current_trend,
    previous_signal,
    previous_trend,
    reasons,
    rsi=None,
    macd=None,
    macd_signal=None,
    macd_histogram=None,
    previous_macd=None,
    previous_macd_signal=None,
    previous_macd_histogram=None,
    kd_k=None,
    kd_d=None,
    kd_j=None,
    previous_kd_k=None,
    previous_kd_d=None,
    previous_kd_j=None,
):
    context = {
        "current_signal": current_signal,
        "current_trend": current_trend,
        "previous_signal": previous_signal,
        "previous_trend": previous_trend,
        "reasons": reasons,
        "rsi": rsi,
        "macd": macd,
        "macd_signal": macd_signal,
        "macd_histogram": macd_histogram,
        "previous_macd": previous_macd,
        "previous_macd_signal": previous_macd_signal,
        "previous_macd_histogram": previous_macd_histogram,
        "kd_k": kd_k,
        "kd_d": kd_d,
        "kd_j": kd_j,
        "previous_kd_k": previous_kd_k,
        "previous_kd_d": previous_kd_d,
        "previous_kd_j": previous_kd_j,
    }

    return rule_engine.evaluate(context)
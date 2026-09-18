from typing import Any

from app.market_data import is_finite_number
from app.macd_analysis import analyze_macd, format_macd_summary


def build_technical_summary(data: dict) -> str:
    """將技術指標整理成適合終端機與通知顯示的簡短摘要。"""

    rsi = data.get("rsi")
    if not is_finite_number(rsi):
        rsi_text = "RSI 資料不足"
    elif rsi >= 70:
        rsi_text = f"RSI {rsi:.2f}（超買）"
    elif rsi <= 30:
        rsi_text = f"RSI {rsi:.2f}（超賣）"
    else:
        rsi_text = f"RSI {rsi:.2f}（中性）"

    macd_result = data.get("macd_analysis") or analyze_macd(
        data.get("macd"),
        data.get("macd_signal"),
        data.get("macd_histogram"),
        data.get("previous_macd_histogram"),
        data.get("previous_macd"),
        data.get("previous_macd_signal"),
    )
    macd_text = format_macd_summary(macd_result)

    kd_j = data.get("kd_j")
    if not is_finite_number(kd_j):
        kd_text = "KD J 資料不足"
    elif kd_j < 0:
        kd_text = f"KD J {kd_j:.2f}（弱）"
    elif kd_j <= 20:
        kd_text = f"KD J {kd_j:.2f}（低）"
    elif kd_j >= 80:
        kd_text = f"KD J {kd_j:.2f}（高）"
    else:
        kd_text = f"KD J {kd_j:.2f}（中性）"

    # The strategy includes Close vs MA5 as well as MA ordering. Reuse its
    # same-bar state, as RuleEngine/base summary do; do not classify it again.
    ma_text = (data.get("analysis") or {}).get("trend") or "資料不足"

    summary = f"{rsi_text} / {macd_text} / {kd_text} / 日線趨勢：{ma_text}"
    if "atr" in data:
        atr, percent = data.get("atr"), data.get("atr_percent")
        if is_finite_number(atr) and is_finite_number(percent):
            summary += f" / ATR14：{atr:.2f}（ATR% {percent:.2f}%｜{data.get('volatility_level', '資料不足')}）"
        else:
            summary += " / ATR14：資料不足"
    return summary


def generate_analysis(
    trend: str,
    signal: str,
    score: int,
    matched_rules: list[Any],
    analysis_is_valid: bool = True,
    *,
    rule_evidence: list[dict] | None = None,
    timeframe_analysis: dict | None = None,
) -> dict[str, Any]:
    """Build display layers without feeding back into strategy or notification.

    Legacy arguments remain accepted. Notification score and free-form messages
    are never interpreted as directional evidence. Structured callers should
    provide RuleEngine.evidence; absent evidence is reported explicitly.
    """
    from app.analysis.signal_layers import build_signal_layers
    items = rule_evidence if rule_evidence is not None else [
        item for item in matched_rules if isinstance(item, dict)
        and 'category' in item and 'directional_score' in item and 'reason' in item]
    return build_signal_layers(trend, items, timeframe_analysis, analysis_is_valid)


def attach_signal_summary(data, *, analysis_is_valid=True):
    """Read-only analysis API uses the same rules, without evaluating events."""
    from app.rules.rsi_rule import RSIRule
    from app.rules.macd_rule import MACDRule
    from app.rules.kd_rule import KDRule
    strategy = data.get('analysis') or {}
    context = dict(data, current_signal=strategy.get('signal'), current_trend=strategy.get('trend'))
    items = []
    if analysis_is_valid:
        for rule in (RSIRule(), MACDRule(), KDRule()):
            items.extend(rule.evaluate(context).get('evidence', []))
    data['signal_summary'] = generate_analysis(
        strategy.get('trend'), strategy.get('signal'), 0, [], analysis_is_valid,
        rule_evidence=items, timeframe_analysis=data.get('timeframe_analysis'))

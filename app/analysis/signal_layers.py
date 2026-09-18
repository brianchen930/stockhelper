"""Explain existing momentum and trend separately; never changes trading policy."""
from copy import deepcopy

from app.analysis.common import score_to_view, unique_sentences
from app.market_data import is_finite_number
from app.rules.base import RuleCategory as C, evidence


ALIGNMENT_LABELS = {
    'ALIGNED_BULLISH': '趨勢與動能同向偏多',
    'ALIGNED_BEARISH': '趨勢與動能同向偏空',
    'DIVERGENT': '趨勢與動能分歧',
    'NEUTRAL': '趨勢與動能尚未形成同向共識',
    'INSUFFICIENT_DATA': '資料不足',
}


def _sign(value):
    return (value > 0) - (value < 0)


def _momentum(items):
    directional = [e for e in items if e['category'] == C.MOMENTUM]
    bullish = [e for e in directional if e['directional_score'] > 0]
    bearish = [e for e in directional if e['directional_score'] < 0]
    bull = sum(e['directional_score'] for e in bullish)
    bear = -sum(e['directional_score'] for e in bearish)
    score = bull - bear
    # Reuse rule weights (cross=2, histogram=1) and previous summary's 3-point
    # strong-direction boundary. Notification weights never enter this sum.
    direction = ('偏多' if score >= 3 else '中性偏多' if score > 0 else
                 '偏空' if score <= -3 else '中性偏空' if score < 0 else '中性')
    count = len(bullish) + len(bearish)
    strength = '強' if count >= 3 else '中' if count >= 2 else '弱'
    if bullish and bearish:
        strength = '弱'
    reasons = [e['reason'] for e in directional if e['directional_score']]
    reasons += [e['reason'] for e in items if e['category'] == C.CONFIRMATION]
    reasons += [e['reason'] for e in directional if not e['directional_score']]
    return dict(direction=direction, sign=_sign(score), strength=strength, score=score,
                bullish_score=bull, bearish_score=bear, bullish_rules=bullish,
                bearish_rules=bearish, bullish_count=len(bullish), bearish_count=len(bearish),
                reasons=unique_sentences(reasons), available=bool(directional))


def _trend(ma_trend, timeframe):
    short = timeframe.get('short_term') or {}
    medium = timeframe.get('medium_term') or {}
    valid_medium = is_finite_number(medium.get('score')) and medium.get('label') != '資料不足'
    ma_direction = {'多頭排列': 1, '空頭排列': -1, '均線糾結': 0}.get(ma_trend)
    if valid_medium:
        score = medium['score']
        _, direction = score_to_view(score)
        sign = 1 if score >= 2 else -1 if score <= -2 else 0
        strength = '強' if abs(score) >= 4 else '中' if abs(score) >= 2 else '弱'
        source = 'medium_term'
        # Existing scores contain some momentum. Preserve them and expose source;
        # do not claim this is a new, purely structural model.
        reasons = ([f'日線均線呈{ma_trend}'] if ma_direction is not None else [])
        reasons += [r for r in medium.get('reasons', []) if 'MACD' not in r]
        if not reasons:
            reasons = [f'沿用中期評分 {score:+g}']
    elif ma_direction is not None:
        score, sign = None, ma_direction
        direction = '偏多' if sign > 0 else '偏空' if sign < 0 else '盤整'
        strength, source = ('中' if sign else '弱'), 'ma_strategy'
        reasons = [f'日線均線呈{ma_trend}']
    else:
        score, sign, direction, strength, source = None, 0, '資料不足', '資料不足', 'unavailable'
        reasons = []
    return dict(direction=direction, sign=sign, strength=strength, score=score,
                reasons=unique_sentences(reasons), source=source,
                short_term_score=short.get('score'), medium_term_score=medium.get('score'),
                ma_trend=ma_trend, available=source != 'unavailable',
                evidence=[evidence(C.TREND, 'EXISTING_TREND', r) for r in unique_sentences(reasons)])


def _combine(momentum, trend):
    m, t = momentum['sign'], trend['sign']
    if not trend['available']:
        return dict(direction='資料不足', strength='資料不足', alignment='INSUFFICIENT_DATA')
    divergent = m * t < 0
    aligned = m * t > 0
    alignment = ('DIVERGENT' if divergent else 'ALIGNED_BULLISH' if aligned and t > 0 else
                 'ALIGNED_BEARISH' if aligned else 'NEUTRAL')
    if aligned:
        direction = '偏多' if t > 0 else '偏空'
        order = ['弱', '中', '強']
        strength = min((momentum['strength'], trend['strength']), key=order.index)
    else:
        sign = t or m
        direction = '中性偏多' if sign > 0 else '中性偏空' if sign < 0 else '中性'
        strength = '弱'
    return dict(direction=direction, strength=strength, alignment=alignment)


def build_signal_layers(ma_trend, items, timeframe=None, valid=True):
    items = deepcopy(items)
    if not valid:
        items, timeframe = [], {}
    momentum = _momentum(items)
    trend = _trend(ma_trend, timeframe or {})
    combined = _combine(momentum, trend)
    risks = [e for e in items if e['category'] == C.RISK]
    for period in ('short_term', 'medium_term'):
        risks += [evidence(C.RISK, period.upper() + '_WARNING', r)
                  for r in ((timeframe or {}).get(period) or {}).get('warnings', [])]
    if not valid:
        for layer in (momentum, trend):
            layer.update(direction='資料不足', strength='資料不足', score=None,
                         sign=0, reasons=[], available=False)
        trend['evidence'] = []
        combined = dict(direction='資料不足', strength='資料不足', alignment='INSUFFICIENT_DATA')
    alignment = combined['alignment']
    if not valid or alignment == 'INSUFFICIENT_DATA':
        summary = '本次資料不足，不產生綜合方向，請等待有效行情資料'
    elif alignment == 'DIVERGENT':
        summary = ('整體趨勢仍偏多，但短期動能尚未完全跟上' if trend['sign'] > 0 else
                   '短期反彈動能出現，但整體趨勢尚未翻多')
    elif alignment == 'ALIGNED_BULLISH':
        summary = '趨勢與目前動能同向偏多'
    elif alignment == 'ALIGNED_BEARISH':
        summary = '趨勢與目前動能同向偏空'
    elif not momentum['available']:
        summary = '目前缺少可分類的動能證據；' + '、'.join(trend['reasons'][:1])
    elif trend['sign'] == 0:
        summary = '趨勢仍在盤整或均線糾結，動能尚未形成趨勢共識'
    else:
        summary = '沿用現有趨勢方向，但動能尚未提供同向確認'
    return dict(momentum=momentum, trend=trend, combined=combined,
                risk=dict(evidence=risks, reasons=unique_sentences([e['reason'] for e in risks])),
                market_bias=combined['direction'], strength=combined['strength'],
                strength_label='綜合強度', summary=summary + '。', suggestion='')


def format_signal_summary(result):
    """Presentation only: all directions, strength and explanations are inputs."""
    lines = ['【訊號摘要】']
    for key, title in (('momentum', '動能'), ('trend', '趨勢')):
        layer = result[key]
        lines.append(f"{title}：{layer['direction']}｜{layer['strength']}")
        lines.extend('・' + reason for reason in layer['reasons'][:2])
    combined = result['combined']
    lines += [f"綜合方向：{combined['direction']}｜{combined['strength']}",
              '狀態：' + ALIGNMENT_LABELS[combined['alignment']], result['summary']]
    if result['risk']['reasons']:
        lines.append('風險提醒：' + '；'.join(result['risk']['reasons'][:2]))
    return lines

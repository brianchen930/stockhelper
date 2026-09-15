"""Pure, chronological daily features and explainable institutional scores."""
from datetime import datetime
from zoneinfo import ZoneInfo
from .config import DEFAULT_CONFIG

TAIPEI = ZoneInfo('Asia/Taipei')


def taipei_time(value):
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    return value.replace(tzinfo=TAIPEI) if value.tzinfo is None else value.astimezone(TAIPEI)


def unknown(reason='法人資料不足，沿用原始支撐機率'):
    return dict(institutional_score=None, institutional_level='UNKNOWN', features={},
                confidence=0., reasons=[reason], latest_available_institutional_date=None)


def flow_trend(values):
    if len(values) < 3:
        return 'NEUTRAL'
    a, b, c = values[-3:]
    if a < 0 and b < 0 and c < 0:
        return 'SELLING_WEAKENING' if a < b < c else 'SELLING_ACCELERATING' if a > b > c else 'NEUTRAL'
    if a > 0 and b > 0 and c > 0:
        return 'BUYING_ACCELERATING' if a < b < c else 'BUYING_WEAKENING' if a > b > c else 'NEUTRAL'
    return 'NEUTRAL'


def streak(values):
    direction = (values[-1] > 0) - (values[-1] < 0)
    count = 0
    for value in reversed(values):
        if not direction or (value > 0) - (value < 0) != direction:
            break
        count += direction
    return count


def build_context(rows, as_of, config=DEFAULT_CONFIG):
    cutoff = taipei_time(as_of)
    rows = sorted((r for r in rows if r['date'] < cutoff.date().isoformat()
                   and taipei_time(r['available_at']) <= cutoff), key=lambda r: r['date'])
    if not rows:
        return unknown()
    if len({r['date'] for r in rows}) != len(rows) or len({r['symbol'] for r in rows}) != 1:
        raise ValueError('Context requires unique dates for one symbol')
    if (cutoff.date() - datetime.fromisoformat(rows[-1]['date']).date()).days > config.max_age_days:
        return unknown('法人資料過舊，沿用原始支撐機率')
    # Do not label five nonconsecutive observations as a five-session streak.
    start = 0
    for i in range(1, len(rows)):
        previous = rows[i].get('previous_trading_date')
        if previous is not None and previous != rows[i-1]['date']:
            start = i
    rows = rows[start:]
    features, scores, directions, reasons = {}, {}, {}, []
    for prefix, column, label, weight in (
            ('foreign', 'foreign_net', '外資', config.foreign_weight),
            ('trust', 'investment_trust_net', '投信', config.trust_weight),
            ('dealer', 'dealer_net', '自營商', 0)):
        values = [r[column] for r in rows]
        for n in (1, 3, 5, 10):
            features[f'{prefix}_net_{n}d'] = sum(values[-n:]) if len(rows) >= n else None
            if n != 10:
                volume = sum(r['volume'] for r in rows[-n:])
                features[f'{prefix}_net_ratio_{n}d'] = sum(values[-n:]) / volume if len(rows) >= n and volume > 0 else None
        if prefix == 'dealer':
            continue
        run, trend = streak(values), flow_trend(values)
        features[prefix + '_streak'] = run
        features[prefix + '_flow_trend'] = trend
        sign = lambda v: (v > 0) - (v < 0)
        daily = features[prefix + '_net_ratio_1d']
        cumulative = features[prefix + '_net_ratio_5d']
        if cumulative is None:
            cumulative = features[prefix + '_net_ratio_3d']
        points = sign(run) if abs(run) >= config.streak_days else 0
        points += sign(cumulative) if cumulative is not None and abs(cumulative) >= config.cumulative_ratio else 0
        points += sign(daily) if daily is not None and abs(daily) >= config.daily_ratio else 0
        points += 1 if trend == 'SELLING_WEAKENING' else -1 if trend == 'SELLING_ACCELERATING' else 0
        scores[prefix] = points * weight
        directions[prefix] = sign(values[-1])
        if trend == 'SELLING_WEAKENING':
            reasons.append(label + '仍賣超，但近 3 日賣壓逐步減弱')
        elif trend == 'SELLING_ACCELERATING':
            reasons.append(label + '近 3 日賣壓持續加速')
        elif abs(run) >= config.streak_days:
            reasons.append(f'{label}連續 {abs(run)} 日' + ('買超' if run > 0 else '賣超'))
        elif daily:
            reasons.append(label + ('偏買超' if daily > 0 else '偏賣超'))
    mixed = directions['foreign'] * directions['trust'] < 0
    score = sum(scores.values())
    consensus = directions['foreign'] == directions['trust'] and directions['foreign'] != 0
    if consensus and all(abs(features[p + '_streak']) >= config.streak_days for p in ('foreign', 'trust')):
        score += directions['foreign'] * config.consensus_bonus
    score = max(-config.score_limit, min(config.score_limit, score))
    level = ('STRONG_SUPPORT' if score >= config.strong_threshold else 'BULLISH' if score >= config.bullish_threshold
             else 'STRONG_PRESSURE' if score <= -config.strong_threshold else 'BEARISH' if score <= -config.bullish_threshold else 'NEUTRAL')
    if mixed:
        level = 'MIXED'
        reasons.insert(0, '外資與投信買賣方向分歧，降低法人訊號信心')
    elif consensus:
        reasons.insert(0, '外資與投信同步' + ('買超，有利於支撐守住' if directions['foreign'] > 0 else '賣超，增加支撐失效風險'))
    return dict(institutional_score=score, institutional_level=level,
                confidence=config.mixed_confidence if mixed else 1., features=features,
                component_scores=scores, reasons=(reasons or ['法人買賣力量暫無明顯方向'])[:3],
                latest_available_institutional_date=rows[-1]['date'])

"""Adapt existing daily analysis without recalculating technical indicators."""
from datetime import datetime
from zoneinfo import ZoneInfo
from app.market_data import is_finite_number
from app.decision_engine import DecisionConfig, DecisionContext


def number(value):
    return float(value) if is_finite_number(value) else None


def classify_support(price, previous_price, low, high, atr, volume_ratio=None, config=None):
    c = config or DecisionConfig()
    if any(number(v) is None for v in (price, low, high, atr)) or atr <= 0:
        return 'UNKNOWN', None
    distance = (low - price) / atr
    if price < low:
        persistent = previous_price is not None and previous_price < low
        volume = volume_ratio is not None and volume_ratio >= c.volume_confirmation_ratio
        confirmed = distance >= c.confirmed_break_atr or (distance >= c.persistent_break_atr and persistent and volume)
        return ('CONFIRMED_BREAK' if confirmed else 'MINOR_BREAK'), distance
    if previous_price is not None and previous_price < low:
        return 'RECLAIMED', distance
    if low <= price <= high:
        return 'TESTING', distance
    if previous_price is not None and low <= previous_price <= high:
        return 'HOLDING', distance
    return ('APPROACHING' if (price - high) / atr <= c.near_zone_atr else 'DISTANT'), distance


def build_decision_context(result, data=None, *, previous_zones=None, config=None, now=None):
    c = config or DecisionConfig()
    sr = result.get('support_resistance') or {}
    tf = result.get('timeframe_analysis') or {}
    short, medium = tf.get('short_term') or {}, tf.get('medium_term') or {}
    score = lambda x: number(x.get('score')) or 0
    direction = lambda x: 1 if score(x) >= 2 else -1 if score(x) <= -2 else 0
    price = number(sr.get('current_price'))
    if price is None:
        price = number(result.get('close'))
    atr = number(result.get('atr'))
    previous_price = None
    latest_high = None
    volume_ratio = None
    if data is not None and len(data) >= 2:
        previous_price = number(data.Close.iloc[-2])
        latest_high = number(data.High.iloc[-1]) if 'High' in data else None
        if 'Volume' in data and len(data) >= 20:
            volumes = data.Volume.iloc[-20:-1]
            baseline = number(volumes.mean()) if volumes.map(is_finite_number).all() else None
            volume = number(data.Volume.iloc[-1])
            if baseline is not None and baseline > 0 and volume is not None and volume >= 0:
                volume_ratio = volume / baseline
    flow = sr.get('institutional_context') or {}
    candidate = sr.get('bayesian_support_selected') or {}
    display = candidate.get('display') or {}
    from app.decision_zones import displayed_zones, reference, overlap
    selected = displayed_zones(sr, str(result.get('stock_code', '')))
    support, resistance = selected['support'], selected['resistance']
    prior = previous_zones or {}
    old_support = reference(prior.get('nearest_support'), 'support', str(result.get('stock_code', '')),
                            prior.get('previous_support_as_of', prior.get('as_of')), 'previous_detector')
    old_resistance = reference(prior.get('nearest_resistance'), 'resistance', str(result.get('stock_code', '')),
                               prior.get('previous_resistance_as_of', prior.get('as_of')), 'previous_detector')
    # Only an active zone with known support provenance can be a defense zone.
    if ('zone_lifecycle' not in sr and selected['active'] and old_support
            and overlap(selected['active'], old_support) >= c.probability_zone_overlap):
        support = selected['active']
    def status(zone, known):
        if not zone or price is None or atr is None or atr <= 0:
            return 'UNKNOWN', None
        state, distance = classify_support(price, previous_price if known else None,
            zone['low'], zone['high'], atr, volume_ratio, c)
        if not known and state in ('CONFIRMED_BREAK', 'MINOR_BREAK'):
            return 'UNKNOWN', distance
        if (state == 'CONFIRMED_BREAK' and previous_price is not None
                and previous_price < zone['low'] and latest_high is not None and latest_high >= zone['low']):
            state = 'FLIPPED_TO_RESISTANCE'
        return state, distance
    support_status, break_distance = status(support, overlap(support, old_support) >= c.probability_zone_overlap)
    previous_support_status, previous_break_distance = status(old_support, bool(old_support))
    ds = max(support['low'] - price, price - support['high'], 0) / atr if support and price is not None and atr and atr > 0 else None
    dr = max(resistance['low'] - price, price - resistance['high'], 0) / atr if resistance and price is not None and atr and atr > 0 else None
    def resistance_state(zone, known):
        if not zone or price is None or atr is None or atr <= 0:
            return 'UNKNOWN'
        low, high = zone['low'], zone['high']
        if known and price > high and (price - high) / atr >= c.breakout_atr and volume_ratio is not None and volume_ratio >= c.volume_confirmation_ratio:
            return 'CONFIRMED_BREAKOUT'
        if known and previous_price is not None and previous_price >= low and price < low:
            return 'REJECTED'
        if low <= price <= high:
            return 'TESTING'
        if price < low and (low - price) / atr <= c.near_zone_atr:
            return 'APPROACHING'
        return 'UNKNOWN'
    resistance_status = resistance_state(resistance, overlap(resistance, old_resistance) >= c.probability_zone_overlap)
    previous_resistance_status = resistance_state(old_resistance, bool(old_resistance))
    if 'zone_lifecycle' in sr:
        # Lifecycle owns roles; this adapter must not infer a flip from a single bar.
        statuses = {'RECLAIMED_SUPPORT': 'RECLAIMED',
                    'RESISTANCE_TO_SUPPORT': 'HOLDING', 'MINOR_BREAK': 'MINOR_BREAK'}
        support_status = statuses.get((support or {}).get('status'), support_status if support else 'UNKNOWN')
        if support and price is not None and price < support['low']:
            support_status = 'MINOR_BREAK'
        resistance_status = 'TESTING' if resistance and resistance['low'] <= price <= resistance['high'] else 'APPROACHING' if dr is not None and dr <= c.near_zone_atr else 'UNKNOWN'
        historical = sr['zone_lifecycle'].get('historical_zones', [])
        broken = [z for z in historical if z.get('status') == 'BROKEN_SUPPORT']
        old_support = min(broken, key=lambda z: abs(z.get('distance_pct', 0)), default=None)
        previous_support_status = 'CONFIRMED_BREAK' if old_support else 'UNKNOWN'
        previous_break_distance = (old_support['low'] - price) / atr if old_support and atr and atr > 0 else None
        broken_resistance = [z for z in historical if z.get('status') == 'BROKEN_RESISTANCE']
        old_resistance = min(broken_resistance, key=lambda z: abs(z.get('distance_pct', 0)), default=None)
        previous_resistance_status = 'CONFIRMED_BREAKOUT' if old_resistance else 'UNKNOWN'
    # Bayesian prediction applies only to its own tested zone, never another one.
    rating = 'UNKNOWN'
    if support and display.get('model_status') == 'ready' and (candidate.get('zone_lifecycle') or {}).get('active_support', True):
        lo, hi = number(candidate.get('support_low')), number(candidate.get('support_high'))
        if lo is not None and hi is not None:
            overlap = max(0, min(hi, support['high']) - max(lo, support['low']))
            union = max(hi, support['high']) - min(lo, support['low'])
            if union > 0 and overlap / union >= c.probability_zone_overlap:
                rating = display.get('rating', 'UNKNOWN')
    now = now or datetime.now(ZoneInfo('Asia/Taipei'))
    stamp = result.get('history_date') or result.get('date')
    complete = False
    try:
        day = datetime.fromisoformat(stamp).date()
        local = now.astimezone(ZoneInfo('Asia/Taipei'))
        complete = day < local.date() or (day == local.date() and (local.hour, local.minute) >= (13, 30))
    except (TypeError, ValueError):
        pass
    from app.support_resistance_analysis.interaction import classify_interaction
    from app.decision_zones import overlap as zone_overlap
    interactions = []
    if 'zone_lifecycle' in sr:
        for key in ('support_zones', 'resistance_zones', 'active_zones', 'historical_zones'):
            interactions.extend(z['interaction'] for z in sr['zone_lifecycle'].get(key, []) if z.get('interaction'))
    else:
        # Compatibility path consumes this adapter's existing confirmation
        # results. It does not introduce alternative thresholds or persistence.
        for role, zone, old, state in (
            ('support', support, old_support, support_status),
            ('resistance', resistance, old_resistance, resistance_status)):
            if zone:
                prior_interaction = (old or {}).get('interaction') if zone_overlap(zone, old) >= c.probability_zone_overlap else None
                confirmed = complete and state in ('CONFIRMED_BREAK', 'FLIPPED_TO_RESISTANCE', 'CONFIRMED_BREAKOUT')
                if confirmed or prior_interaction or not zone.get('interaction'):
                    zone['interaction'] = classify_interaction(price, zone['low'], zone['high'], role,
                        previous=prior_interaction, confirmed=confirmed)
                if zone['interaction']:
                    interactions.append(zone['interaction'])
    above = lambda key: None if price is None or number(result.get(key)) is None else price > result[key]
    def bias(key):
        ma = number(result.get(key))
        return (price / ma - 1) * 100 if price is not None and ma is not None and ma > 0 else 0
    rsi, k, d = map(number, (result.get('rsi'), result.get('kd_k'), result.get('kd_d')))
    macd = result.get('macd_analysis') or {}
    hist, old_hist = number(result.get('macd_histogram')), number(result.get('previous_macd_histogram'))
    from app.support_resistance_analysis.selection import recent_breakout_reference
    return DecisionContext(
        breakout_reference_zone=recent_breakout_reference(sr, price, atr, stamp, near_atr=c.near_zone_atr)
            if resistance is None else None,
        level_interactions=interactions,
        zone_lifecycle_statuses={z['stable_zone_id']: {'status': z.get('status'), 'current_role': z.get('current_role')}
            for key in ('support_zones', 'resistance_zones', 'active_zones', 'historical_zones')
            for z in (sr.get('zone_lifecycle') or {}).get(key, []) if z.get('stable_zone_id')},
        symbol=str(result.get('stock_code', '')), observation_time=stamp, observation_complete=complete,
        current_price=price, active_support_zone=support, active_resistance_zone=resistance,
        previous_support_zone=old_support, previous_resistance_zone=old_resistance,
        current_active_support_status=support_status, previous_support_status=previous_support_status,
        previous_resistance_status=previous_resistance_status, previous_break_distance_atr=previous_break_distance,
        short_term_direction=direction(short), short_term_score=score(short),
        medium_term_direction=direction(medium), medium_term_score=score(medium),
        support_probability=rating, base_support_probability=number(candidate.get('base_support_probability', (candidate.get('result') or {}).get('posterior_success_probability'))),
        adjusted_support_probability=number(candidate.get('adjusted_support_probability')),
        adjusted_support_level=candidate.get('adjusted_support_level'),
        support_strength=number((support or {}).get('strength_score')),
        resistance_strength=number((resistance or {}).get('strength_score')),
        distance_to_support=ds, distance_to_resistance=dr,
        institutional_level=flow.get('institutional_level', 'UNKNOWN'), institutional_score=number(flow.get('institutional_score')),
        institutional_confidence=number(flow.get('confidence')) or 0,
        institutional_selling_weakened='SELLING_WEAKENING' in (flow.get('features') or {}).values(),
        institutional_as_of=flow.get('latest_available_institutional_date'),
        atr=atr, atr_percent=number(result.get('atr_percent')), volatility_level=result.get('volatility_level', '資料不足'),
        macd_state=macd.get('position', 'unknown'), macd_momentum=macd.get('momentum', 'data_insufficient'),
        macd_histogram_change_atr=(hist - old_hist) / atr if hist is not None and old_hist is not None and atr is not None and atr > 0 else None,
        rsi_state='UNKNOWN' if rsi is None else 'OVERBOUGHT' if rsi >= 70 else 'OVERSOLD' if rsi <= 30 else 'NEUTRAL',
        kd_state='UNKNOWN' if k is None or d is None else 'BULLISH' if k > d else 'BEARISH' if k < d else 'NEUTRAL',
        relative_market_strength=number((result.get('market_relative_performance') or {}).get('difference')),
        price_above_ma5=above('ma5'), price_above_ma20=above('ma20'), price_above_ma60=above('ma60'),
        volume_state='UNKNOWN' if volume_ratio is None else 'EXPANDING' if volume_ratio >= c.volume_confirmation_ratio else 'NORMAL',
        volume_ratio=volume_ratio, support_status=support_status, resistance_status=resistance_status,
        break_distance_atr=break_distance, overextended=bias('ma5') >= c.overextended_ma5_pct or bias('ma20') >= c.overextended_ma20_pct,
        data_valid=bool(short and medium and price is not None and price > 0 and short.get('label') != '資料不足' and medium.get('label') != '資料不足'))


def attach_decision(result, data=None, *, previous_zones=None, now=None):
    from dataclasses import asdict
    from app.decision_engine import DecisionEngine
    from app.decision_state import stabilize
    from app.decision_formatter import format_operation_reference
    context = build_decision_context(result, data, previous_zones=previous_zones, now=now)
    decision, _ = stabilize(DecisionEngine().evaluate(context), context, {})
    result['decision_context'] = asdict(context)
    result['trading_decision'] = asdict(decision)
    result['timeframe_analysis']['trading_decision'] = asdict(decision)
    result['timeframe_analysis']['decision_context'] = asdict(context)
    result['timeframe_analysis']['operation_reference'] = format_operation_reference(decision)

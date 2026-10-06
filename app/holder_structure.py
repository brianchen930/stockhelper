"""Causal breakout events and durable holder defense, separate from entry zones."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
import json

from app.decision_zones import valid_zone
from app.entry_paths import observation_day
from app.market_data import is_finite_number


BROKEN = ('CONFIRMED_BREAK', 'FLIPPED_TO_RESISTANCE')


def recent(stamp, now, days):
    then, today = observation_day(str(stamp)), observation_day(str(now))
    return bool(then and today and 0 <= (date.fromisoformat(today) - date.fromisoformat(then)).days <= days)


def valid_event(event, stamp, config):
    return (isinstance(event, dict) and event.get('source') == 'VOLUME_PLATFORM_BREAKOUT'
            and recent(event.get('breakout_date'), stamp, config.structural_breakout_max_days)
            and all(is_finite_number(event.get(k)) and event[k] > 0 for k in
                    ('breakout_level', 'breakout_candle_open', 'breakout_candle_low'))
            and event['breakout_candle_low'] <= event['breakout_candle_open']
            and is_finite_number(event.get('volume_ratio'))
            and event['volume_ratio'] >= config.volume_confirmation_ratio)


def event_zone(event):
    # The platform edge starts the retest; losing the candle's lower boundary
    # invalidates that breakout. A retest inside this band is not a thesis break.
    return dict(low=min(event['breakout_level'], event['breakout_candle_low']),
                high=event['breakout_level'], source='BREAKOUT_STRUCTURE',
                structural_basis='BREAKOUT_PLATFORM_AND_CANDLE',
                zone_id='breakout:' + event['breakout_date'],
                breakout_candle_open=event['breakout_candle_open'],
                breakout_candle_low=event['breakout_candle_low'])


def classify_defense(price, previous_price, zone, atr, volume_ratio, completed, config, old_status='UNKNOWN'):
    if not valid_zone(zone) or not is_finite_number(price) or not is_finite_number(atr) or atr <= 0:
        return 'UNKNOWN'
    if not completed:
        if old_status in BROKEN:
            return old_status  # Intraday recovery cannot erase a confirmed failure.
        return 'MINOR_BREAK' if price < zone['low'] else 'RETESTING' if price < zone['high'] else 'HOLDING'
    from app.decision_context import classify_support
    status, _ = classify_support(price, previous_price, zone['low'], zone['high'], atr, volume_ratio, config)
    if old_status in BROKEN and price < zone['high']:
        return 'CONFIRMED_BREAK'
    if status in BROKEN or status == 'MINOR_BREAK':
        return status
    if status == 'TESTING' and old_status not in (*BROKEN, 'HOLDING', 'RECLAIMED', 'RETESTING'):
        return 'TESTING'
    return 'RECLAIMED' if old_status in BROKEN else 'RETESTING' if price < zone['high'] else 'HOLDING'


def completed_bars(data, stamp, completed):
    if data is None or not len(data) or not observation_day(stamp):
        return None
    day = observation_day(stamp)
    # Cut off future and forming bars before any rolling statistic or pivot.
    mask = [bool(observation_day(str(i)) and (observation_day(str(i)) < day or
            completed and observation_day(str(i)) == day)) for i in data.index]
    return data.loc[mask].sort_index()


def detect_breakout(data, stamp, config):
    if data is None or not {'Open', 'High', 'Low', 'Close', 'Volume'} <= set(data.columns):
        return None
    n = config.breakout_platform_bars
    from app.volatility import calculate_atr
    atr_series = calculate_atr(data)
    event = None
    for i in range(max(n, 19), len(data)):
        day = observation_day(str(data.index[i]))
        if not recent(day, stamp, config.structural_breakout_max_days):
            continue
        window = data.iloc[i-n:i]
        row = data.iloc[i]
        sample = data.iloc[max(0, i-19):i+1]
        if not all(is_finite_number(v) and v > 0 for v in sample[['Open', 'High', 'Low', 'Close']].to_numpy().flat):
            continue
        if any(sample.Low > sample[['Open', 'Close']].min(axis=1)) or any(sample.High < sample[['Open', 'Close']].max(axis=1)):
            continue
        if not all(is_finite_number(v) and v >= 0 for v in sample.Volume):
            continue
        atr = float(atr_series.iloc[i-1])
        baseline = float(data.Volume.iloc[i-19:i].mean())
        if not is_finite_number(atr) or atr <= 0 or baseline <= 0:
            continue
        level = float(window.High.max())
        width = level - float(window.Low.min())
        # A platform needs repeated tests across time and bounded consolidation;
        # a lone swing high or a trending range is not a platform breakout.
        touches = [j for j, value in enumerate(window.High) if level - value <= .25 * atr]
        if (len(touches) < 2 or touches[-1] - touches[0] < 3
                or width > config.breakout_platform_width_atr * atr):
            continue
        ratio = float(row.Volume / baseline)
        if (row.Close <= row.Open or row.Close < level + config.volume_breakout_atr * atr
                or data.Close.iloc[i-1] > level or ratio < config.volume_confirmation_ratio):
            continue
        event = dict(source='VOLUME_PLATFORM_BREAKOUT', breakout_level=level,
            breakout_candle_open=float(row.Open), breakout_candle_low=float(row.Low),
            breakout_candle_close=float(row.Close),
            breakout_date=day, volume_ratio=ratio, atr_at_breakout=atr,
            platform_start_date=observation_day(str(window.index[0])),
            platform_end_date=observation_day(str(window.index[-1])))
    return event


def structure_from_history(data, sr, stamp, complete, config):
    bars = completed_bars(data, stamp, complete)
    event = detect_breakout(bars, stamp, config)
    zone, status = (event_zone(event), 'HOLDING') if event else (None, 'UNKNOWN')
    if event:
        from app.volatility import calculate_atr
        atr_series = calculate_atr(bars)
        # Replay only bars after the event; a past confirmed failure remains
        # failed even if today's distance is smaller than the ATR threshold.
        for i in range(1, len(bars)):
            if observation_day(str(bars.index[i])) <= event['breakout_date']:
                continue
            ratio = bars.Volume.iloc[i] / bars.Volume.iloc[max(0, i-19):i].mean()
            status = classify_defense(bars.Close.iloc[i], bars.Close.iloc[i-1], zone,
                atr_series.iloc[i], ratio, True, config, status)
    else:
        candidates = []
        for view in (sr, sr.get('zone_lifecycle') or {}):
            for key in ('support_zones', 'active_zones', 'historical_zones'):
                candidates.extend(view.get(key) or [])
            if view.get('nearest_support'):
                candidates.append(view['nearest_support'])
        zone = select_defense(candidates, config)
        if bars is not None and len(bars) and {'High', 'Low', 'Close'} <= set(bars.columns):
            from app.support_resistance_analysis.swing import detect_swings
            from app.support_resistance_analysis.config import SupportResistanceConfig
            swing_config = SupportResistanceConfig(swing_window=config.structural_swing_window,
                                                    swing_atr_factor=config.structural_swing_atr_factor)
            swings = [s for s in detect_swings(bars, swing_config) if s.method == 'swing_low'
                      and recent(observation_day(str(bars.index[s.position])), stamp, config.structural_breakout_max_days)]
            if swings:
                s = swings[-1]
                zone = dict(low=s.price, high=s.price, structural_basis='CONFIRMED_SWING_LOW',
                    zone_id='swing:' + observation_day(str(bars.index[s.position])),
                    confirmed_at=observation_day(str(bars.index[s.confirmed_position])))
    return dict(breakout_event=event, structural_support_zone=zone, structural_support_status=status)


def select_defense(zones, config):
    candidates = []
    for z in zones:
        if not valid_zone(z):
            continue
        methods = z.get('methods') or []
        strength = z.get('strength_score')
        role = str(z.get('current_role') or z.get('role') or z.get('type') or '').upper()
        if ((role == 'RESISTANCE' or z.get('status') == 'BROKEN_RESISTANCE')
                and z.get('previous_role') != 'SUPPORT'):
            continue
        swing = 'swing_low' in methods and is_finite_number(strength) and strength >= config.min_support_strength
        strong = is_finite_number(strength) and strength >= config.structural_support_min_strength
        if swing or strong:
            candidates.append(dict(deepcopy(z), structural_basis='CONFIRMED_SWING_LOW' if swing else 'STRONG_SUPPORT'))
    return max(candidates, key=lambda z: ('swing_low' in (z.get('methods') or []), z.get('strength_score') or 0), default=None)


def resolve_structure(c, previous, config):
    """Use the last committed event across truncated histories and restarts."""
    assessment = dict(date=c.observation_time, price=c.current_price, complete=c.observation_complete,
                      atr=c.atr, volume_ratio=c.volume_ratio, previous_close=c.previous_close,
                      policy=[config.confirmed_break_atr, config.persistent_break_atr,
                              config.structural_support_min_strength, config.volume_confirmation_ratio,
                              config.structural_breakout_max_days])
    if c.structural_assessment == assessment and not (previous or {}).get('holder_structure_memory'):
        return c
    try:
        saved = json.loads((previous or {}).get('holder_structure_memory') or '{}')
    except (ValueError, TypeError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    event = deepcopy(c.breakout_event) if valid_event(c.breakout_event, c.observation_time, config) else None
    prior_event = saved.get('breakout_event')
    from_saved = valid_event(prior_event, c.observation_time, config) and (
        not event or prior_event['breakout_date'] >= event['breakout_date'])
    if from_saved:
        event = deepcopy(prior_event)
    zone = event_zone(event) if event else deepcopy(c.structural_support_zone)
    if not event and zone and zone.get('source') == 'BREAKOUT_STRUCTURE':
        zone = None
    status = saved.get('structural_support_status', 'UNKNOWN') if from_saved else c.structural_support_status
    classified = False
    if not valid_zone(zone):
        current = dict(c.active_support_zone or {})
        if c.support_strength is not None:
            current['strength_score'] = c.support_strength
        zone = select_defense([current, c.previous_support_zone], config)
        if zone:
            status = c.support_status if zone.get('low') == current.get('low') and zone.get('high') == current.get('high') else c.previous_support_status
            classified = True  # Reuse adapter confirmation; do not invent a new break.
    # Preserve a known defense when it vanishes from the nearest-zone display.
    prior_zone = saved.get('structural_support_zone')
    if (not event and valid_zone(prior_zone) and prior_zone.get('source') != 'BREAKOUT_STRUCTURE'
            and recent(saved.get('last_observation_time'), c.observation_time, config.structural_breakout_max_days)):
        zone = deepcopy(prior_zone)
        status = saved.get('structural_support_status', 'UNKNOWN')
        classified = False
    if zone and not classified:
        prior_price = saved.get('last_close', c.previous_close)
        status = classify_defense(c.current_price, prior_price, zone, c.atr, c.volume_ratio,
                                  c.observation_complete, config, status)
    if classified and status in BROKEN and (not c.observation_complete or c.current_price is None or c.current_price >= zone['low']):
        status = 'MINOR_BREAK' if c.current_price is not None and c.current_price < zone['low'] else 'UNKNOWN'
    if (classified and is_finite_number(c.current_price) and c.current_price >= zone['high']
            and status in ('UNKNOWN', 'DISTANT', 'APPROACHING')):
        status = 'HOLDING'
    if zone:
        zone.pop('interaction', None)  # Short-zone interaction metadata has a different role/time.
    return replace(c, breakout_event=event, structural_support_zone=zone, structural_support_status=status,
                   structural_assessment=assessment)


def structure_memory(c, previous):
    if (not c.data_valid or not observation_day(c.observation_time)
            or c.observation_status in ('MISSING', 'INVALID', 'FUTURE')
            or not all(is_finite_number(v) and v > 0 for v in (c.current_price, c.atr))):
        return (previous or {}).get('holder_structure_memory')
    if not c.observation_complete:
        old = (previous or {}).get('holder_structure_memory')
        # A first intraday scan can discover an already completed historical
        # breakout. Save that event, never the forming bar's price/status.
        try:
            saved = json.loads(old or '{}')
        except (TypeError, ValueError):
            saved = {}
        event = c.breakout_event
        if (event and event['breakout_date'] < (observation_day(c.observation_time) or '')
                and (not isinstance(saved, dict) or saved.get('breakout_event') != event)):
            return json.dumps(dict(version=1, breakout_event=event, structural_support_zone=event_zone(event),
                structural_support_status='CONFIRMED_BREAK' if c.structural_support_status in BROKEN else 'HOLDING',
                last_observation_time=event['breakout_date'], last_close=event.get('breakout_candle_close')), ensure_ascii=False)
        return old
    return json.dumps(dict(version=1, breakout_event=c.breakout_event,
        structural_support_zone=c.structural_support_zone, structural_support_status=c.structural_support_status,
        last_observation_time=c.observation_time, last_close=c.current_price), ensure_ascii=False)


def structural_break(c):
    return valid_zone(c.structural_support_zone) and c.structural_support_status in BROKEN


def short_support(c):
    candidates = [(c.active_support_zone, c.support_status)] if valid_zone(c.active_support_zone) else []
    if c.previous_support_status in BROKEN and valid_zone(c.previous_support_zone):
        candidates.append((c.previous_support_zone, c.previous_support_status))
    states = {'SUPPORT_BREAKDOWN_PENDING': 'MINOR_BREAK', 'SUPPORT_BREAKDOWN_CONFIRMED': 'CONFIRMED_BREAK',
              'SUPPORT_BREAKDOWN_FAILED': 'RECLAIMED', 'TESTING_SUPPORT': 'TESTING'}
    for interaction in c.level_interactions:
        if interaction.get('role') != 'support' or interaction.get('state') not in states:
            continue
        zone = dict(low=interaction.get('zone_low'), high=interaction.get('zone_high'), interaction=interaction)
        if valid_zone(zone):
            candidates.append((zone, states[interaction['state']]))
    if not candidates:
        return None, 'UNKNOWN'
    from app.support_resistance_analysis.selection import boundary_distance
    return min(candidates, key=lambda item: boundary_distance(item[0], c.current_price))

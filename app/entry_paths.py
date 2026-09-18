"""Independent entry candidates using existing trend, zone and risk evidence."""
from enum import StrEnum


class EntryPath(StrEnum):
    BREAKOUT_ENTRY = 'BREAKOUT_ENTRY'
    PULLBACK_ENTRY = 'PULLBACK_ENTRY'
    NO_ENTRY = 'NO_ENTRY'


class PathStatus(StrEnum):
    INACTIVE = 'INACTIVE'
    WATCHING = 'WATCHING'
    WAITING_CONFIRMATION = 'WAITING_CONFIRMATION'
    READY = 'READY'
    BLOCKED_BY_RISK = 'BLOCKED_BY_RISK'
    INVALIDATED = 'INVALIDATED'


def summarize(paths):
    ready = [key for key in ('breakout', 'pullback') if paths[key]['ready']]
    paths['entry_ready'] = bool(ready)
    paths['active_path'] = paths[ready[0]]['path'] if ready else EntryPath.NO_ENTRY
    # Presentation priority only; both paths may be ready.
    relevant = [key for key in ('breakout', 'pullback')
                if paths[key]['status'] in (PathStatus.WAITING_CONFIRMATION, PathStatus.BLOCKED_BY_RISK)]
    paths['primary_path'] = (ready or relevant or ['breakout'])[0]
    return paths


def evaluate_entry_paths(c, gates, config):
    S = PathStatus
    blockers = list(map(str, gates))
    if c.overextended:
        blockers.append('OVEREXTENDED')
    if not c.data_valid or not c.atr or c.atr <= 0 or c.volatility_level == '資料不足':
        blockers.append('DATA_INSUFFICIENT')
    if c.institutional_level in ('UNKNOWN', 'MIXED'):
        blockers.append('INSTITUTIONAL_DATA_UNCERTAIN')
    if (c.active_support_zone or {}).get('interaction', {}).get('state') == 'SUPPORT_BREAKDOWN_CONFIRMED':
        blockers.append('SUPPORT_BREAK')

    def make(path, zone, state, trend, core, relevant, invalid, reasons, missing, extra=()):
        risk = list(dict.fromkeys(blockers + list(extra)))
        eligible = bool(trend and core and not risk and c.observation_complete)
        status = (S.INVALIDATED if invalid else S.INACTIVE if not relevant else
                  S.WATCHING if not trend else S.BLOCKED_BY_RISK if core and risk else
                  S.READY if eligible else S.WAITING_CONFIRMATION)
        # A support touch requires consecutive completed observations first.
        if path == EntryPath.PULLBACK_ENTRY and state == 'TESTING_SUPPORT' and status == S.READY:
            status = S.WAITING_CONFIRMATION
            missing.append('支撐尚待連續有效收盤確認守穩')
        if not trend:
            missing.append('趨勢尚未符合此路徑條件')
        if not c.observation_complete:
            missing.append('等待有效收盤資料')
        return dict(path=path, status=status, ready=status == S.READY,
                    eligible=eligible and not invalid, zone=zone, interaction_state=state,
                    reasons=reasons, missing=missing, risk_blockers=risk,
                    confirmation_count=0, confirmation_required=config.confirmation_required)

    # Current actionable resistance always wins over historical crossings.
    from app.support_resistance_analysis.selection import actionable, recent_nearby
    rz = c.active_resistance_zone
    # A current crossing can still be evaluated for confirmation, without being
    # relabelled as an active defense zone.
    if rz and rz.get('status') and not actionable(rz, 'resistance', c.current_price):
        rz = None
    rs = (rz or {}).get('interaction', {}).get('state')
    old = c.breakout_reference_zone or c.previous_resistance_zone
    old_state = (old or {}).get('interaction', {}).get('state')
    if (not rz and old and recent_nearby(old, c.current_price, c.atr,
                                         c.observation_time or old.get('reference_as_of'),
                                         near_atr=config.near_zone_atr)):
        rz, rs = old, old_state
        legacy = c.previous_resistance_status
    else:
        legacy_only = not any((c.active_resistance_zone, c.previous_resistance_zone,
                               c.breakout_reference_zone, c.zone_lifecycle_statuses))
        legacy = c.resistance_status if rz or legacy_only else 'UNKNOWN'
    rs = rs or {'CONFIRMED_BREAKOUT': 'RESISTANCE_BREAKOUT_CONFIRMED',
               'TESTING': 'TESTING_RESISTANCE', 'APPROACHING': 'BELOW_RESISTANCE',
               'REJECTED': 'RESISTANCE_BREAKOUT_FAILED'}.get(legacy, legacy)
    confirmed = rs == 'RESISTANCE_BREAKOUT_CONFIRMED'
    # Historical lifecycle records cannot override a current failed crossing.
    if confirmed and rz and c.current_price is not None and c.current_price <= rz['high']:
        rs, confirmed = 'RESISTANCE_BREAKOUT_FAILED', False
    btrend = c.short_term_direction >= 0 and c.medium_term_direction > 0
    breakout = make(EntryPath.BREAKOUT_ENTRY, rz, rs, btrend, confirmed,
        rs not in ('UNKNOWN', 'DISTANT'), rs == 'RESISTANCE_BREAKOUT_FAILED',
        (['短中期趨勢允許突破型進場'] if btrend else []) +
        (['壓力區已完成突破確認'] if confirmed else []),
        [] if confirmed else ['尚未完成有效突破確認'])
    if rs == 'BELOW_RESISTANCE' and breakout['status'] == S.WAITING_CONFIRMATION:
        breakout['status'] = S.WATCHING

    sz = c.active_support_zone
    if sz and sz.get('status') and not actionable(sz, 'support', c.current_price):
        sz = None
    ss = (sz or {}).get('interaction', {}).get('state')
    ss = ss or {'TESTING': 'TESTING_SUPPORT', 'MINOR_BREAK': 'SUPPORT_BREAKDOWN_PENDING',
               'CONFIRMED_BREAK': 'SUPPORT_BREAKDOWN_CONFIRMED',
               'FLIPPED_TO_RESISTANCE': 'SUPPORT_BREAKDOWN_CONFIRMED'}.get(c.support_status, c.support_status)
    if not sz and c.previous_support_status in ('CONFIRMED_BREAK', 'FLIPPED_TO_RESISTANCE'):
        sz, ss = c.previous_support_zone, 'SUPPORT_BREAKDOWN_CONFIRMED'
    held = c.support_status in ('HOLDING', 'RECLAIMED')
    # A role flip alone is not a pullback setup; require current proximity.
    if sz and c.distance_to_support is not None and c.distance_to_support > config.near_zone_atr:
        held = False
    invalid = ss == 'SUPPORT_BREAKDOWN_CONFIRMED' or c.medium_term_direction < 0
    pending = ss == 'SUPPORT_BREAKDOWN_PENDING'
    touching = ss == 'TESTING_SUPPORT'
    if c.active_support_zone and sz is None:
        held = touching = False
    momentum = (c.macd_momentum in ('bullish_strengthening', 'bearish_weakening')
                and c.price_above_ma5 is True)
    ptrend = c.medium_term_direction > 0
    strength = c.support_strength is not None and c.support_strength >= config.min_support_strength
    core = (held or touching) and momentum and not pending and strength
    missing = ([] if held else ['等待支撐確認守穩']) + ([] if momentum else ['等待短期動能改善'])
    if not strength:
        missing.append('支撐強度尚未符合既有門檻')
    pullback = make(EntryPath.PULLBACK_ENTRY, sz, ss, ptrend, core,
        held or touching or pending or invalid, invalid,
        (['中期趨勢仍偏多'] if ptrend else []) + (['支撐已重新站穩'] if held else []), missing,
        ['HIGH_VOLATILITY'] if c.volatility_level in ('高波動', 'HIGH') else [])
    if invalid:
        pullback['missing'] = ['原支撐已確認失守，需重新尋找下方有效支撐'] if ss == 'SUPPORT_BREAKDOWN_CONFIRMED' else ['中期趨勢已轉弱，回檔型條件失效']
    elif not held and not touching and not pending:
        pullback['missing'] = ['目前尚未形成回檔支撐測試']
    return summarize(dict(breakout=breakout, pullback=pullback))


def entry_action(paths, fallback, volatility):
    from app.decision_engine import ActionState as A
    if paths['breakout']['ready']:
        return A.ALLOW_PROBE_ENTRY if volatility in ('高波動', 'HIGH') else A.ENTRY_CONDITION_MET
    if paths['pullback']['ready']:
        return A.ALLOW_PROBE_ENTRY
    return A.WATCH_FOR_CONFIRMATION if fallback in (A.ALLOW_PROBE_ENTRY, A.ENTRY_CONDITION_MET) else fallback


def stabilize_paths(paths, context, previous, config):
    """Reuse daily debounce policy independently for each path and zone identity."""
    import json
    from copy import deepcopy
    paths = deepcopy(paths)
    try:
        memory = json.loads(previous.get('entry_path_memory') or '{}')
    except (ValueError, TypeError):
        memory = {}
    updated = {}
    for key in ('breakout', 'pullback'):
        p = paths[key]
        z = p['zone'] or {}
        identity = z.get('stable_zone_id') or z.get('zone_id') or [z.get('low'), z.get('high')]
        old = memory.get(key, {})
        stamp = old.get('last_observation_time') or previous.get('last_observation_time')
        new = bool(context.observation_time and (not stamp or context.observation_time > stamp)
                   and context.observation_complete and context.data_valid)
        count = old.get('count', 0) if old.get('identity') == identity and p['eligible'] else 0
        if new and p['eligible']:
            count = min(count + 1, config.confirmation_required)
        p['confirmation_count'] = count
        if p['eligible']:
            p['ready'] = count >= config.confirmation_required
            p['status'] = PathStatus.READY if p['ready'] else PathStatus.WAITING_CONFIRMATION
            p['missing'] = [] if p['ready'] else ['等待連續有效收盤確認']
        updated[key] = dict(identity=identity, count=count,
                            last_observation_time=context.observation_time if new else stamp)
    return summarize(paths), json.dumps(updated, ensure_ascii=False)


def suspend_paths(paths):
    from copy import deepcopy
    paths = deepcopy(paths)
    for key in ('breakout', 'pullback'):
        paths[key].update(ready=False, eligible=False, status=PathStatus.WAITING_CONFIRMATION,
                          missing=['資料早於最近觀察，不作為目前進場依據'])
    return summarize(paths)

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
    ready.sort(key=lambda key: paths[key].get('target_action') != 'ENTRY_CONDITION_MET')
    paths['entry_ready'] = bool(ready)
    paths['active_path'] = paths[ready[0]]['path'] if ready else EntryPath.NO_ENTRY
    # Presentation priority only; both paths may be ready.
    relevant = [key for key in ('breakout', 'pullback')
                if paths[key]['status'] in (PathStatus.WAITING_CONFIRMATION, PathStatus.BLOCKED_BY_RISK)]
    relevant.sort(key=lambda key: (not paths[key].get('core_met'),
                                  bool(paths[key].get('risk_blockers'))))
    paths['primary_path'] = (ready or relevant or ['breakout'])[0]
    return paths


def breakout_strength(price, high, atr, volume_ratio, config):
    """Entry confirmation is independent of lifecycle role-flip confirmation."""
    from app.market_data import is_finite_number
    if not all(is_finite_number(v) and v > 0 for v in (price, high, atr)):
        return None
    distance = (price - high) / atr
    if distance >= config.breakout_atr - 1e-9:
        return 'STRONG'
    if (distance >= config.volume_breakout_atr - 1e-9
            and is_finite_number(volume_ratio) and volume_ratio >= config.volume_confirmation_ratio):
        return 'VOLUME_CONFIRMED'
    return None


def evaluate_entry_paths(c, gates, config, base_score=0.):
    from app.market_data import is_finite_number
    from app.decision_zones import valid_zone
    S = PathStatus
    blockers = list(map(str, gates))
    modifiers = []
    if (c.volatility_level in ('極高波動', 'EXTREME')
            or (is_finite_number(c.atr_percent) and c.atr_percent >= config.trade_extreme_atr_pct)):
        modifiers.append('EXTREME_VOLATILITY')
    elif c.volatility_level in ('高波動', 'HIGH'):
        modifiers.append('HIGH_VOLATILITY')
    if c.overextended:
        modifiers.append('OVEREXTENDED')
    if (not c.data_valid or not all(is_finite_number(v) and v > 0 for v in (c.atr, c.current_price))
            or c.volatility_level in (None, '', 'UNKNOWN', '資料不足')):
        blockers.append('DATA_INSUFFICIENT')
    if (c.active_support_zone or {}).get('interaction', {}).get('state') == 'SUPPORT_BREAKDOWN_CONFIRMED':
        blockers.append('SUPPORT_BREAK')

    def make(path, zone, state, trend, core, relevant, invalid, reasons, missing, strong=False):
        risk = list(dict.fromkeys(blockers))
        components = dict(market=base_score, setup=2. if core else 0.,
            ma5=.5 if c.price_above_ma5 is True else 0.,
            volume=1. if is_finite_number(c.volume_ratio) and c.volume_ratio >= config.volume_confirmation_ratio else 0.,
            support_strength=1. if path == EntryPath.PULLBACK_ENTRY and core and
                is_finite_number(c.support_strength) and c.support_strength >= config.min_support_strength else 0.)
        score = round(sum(components.values()), 2)
        confirmation_reasons = []
        if not strong:
            confirmation_reasons.append('突破幅度較小' if path == EntryPath.BREAKOUT_ENTRY else '支撐尚在測試')
        if score < config.entry_score_min:
            confirmation_reasons.append('進場信心尚未達正常進場門檻')
        confirmation_reasons.extend({'EXTREME_VOLATILITY': 'ATR 波動度極高',
                                     'OVEREXTENDED': '價格正乖離過大'}[code]
                                    for code in modifiers if code != 'HIGH_VOLATILITY')
        required = config.confirmation_required if confirmation_reasons else 1
        eligible = bool(trend and core and score >= config.probe_score_min and not risk
                        and c.observation_complete and observation_day(c.observation_time))
        status = (S.INVALIDATED if invalid else S.INACTIVE if not relevant else
                  S.WATCHING if not trend else S.BLOCKED_BY_RISK if core and risk else
                  S.READY if eligible and required == 1 else S.WAITING_CONFIRMATION)
        if core and score < config.probe_score_min:
            missing.append(f'進場分數 {score:g} 尚未達試單門檻 {config.probe_score_min:g}')
        elif eligible and required > 1:
            missing.append(f'等待 {required} 根有效收盤確認')
        if not trend:
            missing.append('趨勢尚未符合此路徑條件')
        if not c.observation_complete or not observation_day(c.observation_time):
            missing.append('等待有效收盤資料')
        from app.decision_engine import ActionState as A
        target = A.ENTRY_CONDITION_MET if score >= config.entry_score_min else A.ALLOW_PROBE_ENTRY
        if modifiers:
            target = A.ALLOW_PROBE_ENTRY
        return dict(path=path, status=status, ready=status == S.READY,
                    eligible=eligible and not invalid, zone=zone, interaction_state=state,
                    reasons=reasons, missing=missing, risk_blockers=risk,
                    risk_modifiers=list(modifiers), confirmation_reasons=confirmation_reasons,
                    core_met=bool(core and trend and not invalid), entry_score=score,
                    score_components=components, target_action=str(target),
                    score_thresholds=dict(probe=config.probe_score_min, entry=config.entry_score_min),
                    confirmation_count=1 if eligible else 0, confirmation_required=required)

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
    quality = breakout_strength(c.current_price, rz['high'], c.atr, c.volume_ratio, config) if valid_zone(rz) else None
    core = bool(quality and rs != 'RESISTANCE_BREAKOUT_FAILED')
    btrend = c.medium_term_direction > 0
    breakout = make(EntryPath.BREAKOUT_ENTRY, rz, rs, btrend, core,
        bool(rz) or rs not in ('UNKNOWN', 'DISTANT'), rs == 'RESISTANCE_BREAKOUT_FAILED',
        (['中期趨勢允許突破型進場'] if btrend else []) +
        (['突破幅度／量能已符合進場條件'] if core else []),
        [] if core else [f'等待突破至少 {config.breakout_atr:g} ATR，或 {config.volume_breakout_atr:g} ATR 搭配量比 {config.volume_confirmation_ratio:g}'],
        strong=quality == 'STRONG')
    breakout['breakout_strength'] = quality
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
    held = c.support_status in ('HOLDING', 'RECLAIMED') or ss == 'SUPPORT_BREAKDOWN_FAILED'
    # A role flip alone is not a pullback setup; require current proximity.
    near = (valid_zone(sz) and is_finite_number(c.current_price) and is_finite_number(c.atr) and c.atr > 0
            and c.current_price >= sz['low']
            and max(c.current_price - sz['high'], 0) / c.atr <= config.near_zone_atr)
    invalid = ss == 'SUPPORT_BREAKDOWN_CONFIRMED' or c.medium_term_direction < 0
    pending = ss == 'SUPPORT_BREAKDOWN_PENDING'
    touching = ss == 'TESTING_SUPPORT'
    if c.active_support_zone and sz is None:
        held = touching = False
    ptrend = c.medium_term_direction > 0
    core = near and (held or touching) and not pending
    missing = [] if held and near else ['等待支撐確認守穩']
    pullback = make(EntryPath.PULLBACK_ENTRY, sz, ss, ptrend, core,
        (near and (held or touching)) or pending or invalid, invalid,
        (['中期趨勢仍偏多'] if ptrend else []) + (['支撐已重新站穩'] if held else []), missing,
        strong=held)
    if invalid:
        pullback['missing'] = ['原支撐已確認失守，需重新尋找下方有效支撐'] if ss == 'SUPPORT_BREAKDOWN_CONFIRMED' else ['中期趨勢已轉弱，回檔型條件失效']
    elif not held and not touching and not pending:
        pullback['missing'] = ['目前尚未形成回檔支撐測試']
    return summarize(dict(breakout=breakout, pullback=pullback))


def entry_action(paths, fallback, volatility):
    from app.decision_engine import ActionState as A
    ready = [paths[k] for k in ('breakout', 'pullback') if paths[k]['ready']]
    if ready:
        return (A.ENTRY_CONDITION_MET if volatility not in ('高波動', 'HIGH') and
                any(p['target_action'] == A.ENTRY_CONDITION_MET for p in ready) else A.ALLOW_PROBE_ENTRY)
    waiting = any(paths[k].get('eligible') for k in ('breakout', 'pullback'))
    return A.WATCH_FOR_CONFIRMATION if waiting or fallback in (A.ALLOW_PROBE_ENTRY, A.ENTRY_CONDITION_MET) else fallback


def observation_day(stamp):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        value = datetime.fromisoformat(stamp)
        if value.tzinfo:
            value = value.astimezone(ZoneInfo('Asia/Taipei'))
        return value.date().isoformat()
    except (TypeError, ValueError):
        return None


def confirmation_count(eligible, identity, context, old, stamp, config):
    """Shared confirmation policy for entry paths and position trade actions."""
    day, prior_day = observation_day(context.observation_time), observation_day(stamp)
    new = bool(day and (not prior_day or day > prior_day)
               and context.observation_complete and context.data_valid)
    count = old.get('count', 0) if old.get('identity') == identity and eligible else 0
    if new and eligible:
        count = min(count + 1, config.confirmation_required)
    return count, context.observation_time if new else stamp


def stabilize_paths(paths, context, previous, config):
    """Reuse daily debounce policy independently for each path and zone identity."""
    import json
    from copy import deepcopy
    paths = deepcopy(paths)
    try:
        memory = json.loads(previous.get('entry_path_memory') or '{}')
    except (ValueError, TypeError):
        memory = {}
    policy = dict(version=3, thresholds=config.__dict__)
    same_policy = isinstance(memory, dict) and memory.get('policy') == policy
    if not same_policy:
        memory = {}
    updated = dict(policy=policy)
    for key in ('breakout', 'pullback'):
        p = paths[key]
        z = p['zone'] or {}
        identity = z.get('stable_zone_id') or z.get('zone_id') or [z.get('low'), z.get('high')]
        old = memory.get(key, {})
        stamp = (old.get('last_observation_time') or previous.get('last_observation_time')) if same_policy else None
        if not context.observation_complete:
            # Intraday fluctuations neither add nor erase completed-bar evidence.
            # The current setup is still fully evaluated and cannot be READY.
            p['confirmation_count'] = min(old.get('count', 0), p['confirmation_required']) if old.get('identity') == identity else 0
            if old:
                updated[key] = old
            continue
        count, stamp = confirmation_count(p['eligible'], identity, context, old, stamp, config)
        p['confirmation_count'] = min(count, p['confirmation_required'])
        if p['eligible']:
            p['ready'] = count >= p['confirmation_required']
            p['status'] = PathStatus.READY if p['ready'] else PathStatus.WAITING_CONFIRMATION
            p['missing'] = [] if p['ready'] else ['等待連續有效收盤確認']
        updated[key] = dict(identity=identity, count=count,
                            last_observation_time=stamp)
    return summarize(paths), json.dumps(updated, ensure_ascii=False)


def suspend_paths(paths):
    from copy import deepcopy
    paths = deepcopy(paths)
    for key in ('breakout', 'pullback'):
        paths[key].update(ready=False, eligible=False, status=PathStatus.WAITING_CONFIRMATION,
                          missing=['資料早於最近觀察，不作為目前進場依據'])
    return summarize(paths)

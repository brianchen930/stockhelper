"""Roles for existing detected zones. No indicator or zone detection calculations."""
from copy import deepcopy
from enum import StrEnum
import hashlib
import json
from app.decision_zones import overlap, valid_zone
from app.market_data import is_finite_number
from .config import ZoneLifecycleConfig
from .interaction import record_interaction


class ZoneStatus(StrEnum):
    ACTIVE_SUPPORT = 'ACTIVE_SUPPORT'
    ACTIVE_RESISTANCE = 'ACTIVE_RESISTANCE'
    BROKEN_SUPPORT = 'BROKEN_SUPPORT'
    BROKEN_RESISTANCE = 'BROKEN_RESISTANCE'
    SUPPORT_TO_RESISTANCE = 'SUPPORT_TO_RESISTANCE'
    RESISTANCE_TO_SUPPORT = 'RESISTANCE_TO_SUPPORT'
    INVALIDATED = 'INVALIDATED'
    ROLE_TRANSITION = 'ROLE_TRANSITION'
    MINOR_BREAK = 'MINOR_BREAK'
    RECLAIMED_SUPPORT = 'RECLAIMED_SUPPORT'


def compatible(a, b, atr, config=None):
    c = config or ZoneLifecycleConfig()
    if not valid_zone(a) or not valid_zone(b) or overlap(a, b) < c.overlap_min:
        return False
    # Explicit distinct swing identities must never be collapsed.
    if a.get('origin_id') and b.get('origin_id') and a['origin_id'] != b['origin_id']:
        return False
    wa, wb = a['high'] - a['low'], b['high'] - b['low']
    center_gap = abs((a['high'] + a['low'] - b['high'] - b['low']) / 2)
    if min(wa, wb) <= 0:
        return a['low'] == b['low'] and a['high'] == b['high']
    if center_gap > c.center_distance_width * min(wa, wb) or abs(wa - wb) > c.width_difference_ratio * min(wa, wb):
        return False
    if is_finite_number(atr) and atr > 0:
        return center_gap <= c.center_distance_atr * atr and abs(wa - wb) <= c.width_difference_atr * atr
    return center_gap == 0 and wa == wb


def zone_key(z):
    return (z['low'], z['high'], z.get('type', ''), tuple(sorted(z.get('methods', []))), z.get('origin_id', ''))


def match_zones(current, previous, atr, config=None):
    """Deterministic greedy one-to-one matching, against frozen identity anchors."""
    edges = []
    for i, z in enumerate(current):
        for j, old in enumerate(previous):
            anchor = dict(low=old.get('anchor_low', old['zone_low']), high=old.get('anchor_high', old['zone_high']),
                          origin_id=old.get('origin_id'))
            if compatible(z, anchor, atr, config):
                edges.append((-overlap(z, anchor), abs(z['low'] + z['high'] - anchor['low'] - anchor['high']),
                              old['stable_zone_id'], zone_key(z), i, j))
    used_old, used_new, matches = set(), set(), {}
    for *_, i, j in sorted(edges):
        if i not in used_new and j not in used_old:
            matches[i] = j
            used_new.add(i)
            used_old.add(j)
    return matches


def detected_zones(sr, atr, config=None):
    raw = []
    for role in ('support', 'resistance', 'active'):
        zones = sr.get(role + '_zones', [])
        if role + '_zones' not in sr and sr.get('nearest_' + role):
            zones = [sr['nearest_' + role]]
        raw.extend(dict(z, type=role) for z in zones if valid_zone(z))
    # Exact duplicates are one detector observation, not competing identities.
    raw = list({zone_key(z): z for z in sorted(raw, key=lambda z: z.get('strength_score', 0))}.values())
    groups = []
    for z in sorted(raw, key=zone_key):
        # Complete-link grouping avoids A~B~C chain merging. Only duplicate
        # roles are resolved here; distinct same-role detector zones stay separate.
        group = next((g for g in groups if z['type'] not in {v['type'] for v in g}
                      and all(compatible(z, v, atr, config) for v in g)), None)
        if group is None:
            groups.append([z])
        else:
            group.append(z)
    return [dict(g[0], detected_roles=sorted({v['type'] for v in g})) for g in groups]


def advance_lifecycle(sr, previous, *, symbol, timeframe='1d', observation_time,
                      close, high=None, low=None, atr=None, volume_ratio=None,
                      completed=True, config=None):
    c, S = config or ZoneLifecycleConfig(), ZoneStatus
    old = deepcopy(previous)
    current = detected_zones(sr, atr, c)
    matches = match_zones(current, old, atr, c)
    records = deepcopy(old)
    for i, zone in enumerate(current):
        if i in matches:
            record = records[matches[i]]
            if record.get('last_observation_at') and observation_time <= record['last_observation_at']:
                continue
            record.update(zone_low=zone['low'], zone_high=zone['high'], last_seen_at=observation_time,
                          strength_score=zone.get('strength_score', 0), strength_label=zone.get('strength_label', 'unknown'))
        else:
            role = zone['type'].upper()
            status = S.ACTIVE_SUPPORT if role == 'SUPPORT' else S.ACTIVE_RESISTANCE if role == 'RESISTANCE' else S.ROLE_TRANSITION
            if len(zone['detected_roles']) > 1:
                role, status = 'NONE', S.ROLE_TRANSITION
            identity = [symbol, timeframe, observation_time, zone_key(zone)]
            record = dict(stable_zone_id=hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24],
                symbol=symbol, timeframe=timeframe, zone_low=zone['low'], zone_high=zone['high'],
                anchor_low=zone['low'], anchor_high=zone['high'], previous_role=None, current_role=role,
                status=status, break_confirmed_at=None, flip_confirmed_at=None,
                last_seen_at=observation_time, last_observation_at=None, last_price=None,
                methods=zone.get('methods', []), origin_id=zone.get('origin_id'),
                strength_score=zone.get('strength_score', 0), strength_label=zone.get('strength_label', 'unknown'))
            records.append(record)
    for r in records:
        stamp = r.get('last_observation_at')
        if not completed or (stamp and observation_time <= stamp) or not is_finite_number(close):
            continue
        zl, zh = r['anchor_low'], r['anchor_high']
        prior_price = r.get('last_price')
        normalized = is_finite_number(atr) and atr > 0
        old_status = r['status']
        if normalized:
            downside, upside = (zl - close) / atr, (close - zh) / atr
            volume = is_finite_number(volume_ratio) and volume_ratio >= c.volume_confirmation_ratio
            down = downside >= c.confirmed_break_atr or (downside >= c.persistent_break_atr and prior_price is not None and prior_price < zl and volume)
            up = upside >= c.confirmed_break_atr or (upside >= c.persistent_break_atr and prior_price is not None and prior_price > zh and volume)
            if old_status in (S.ACTIVE_SUPPORT, S.MINOR_BREAK, S.RECLAIMED_SUPPORT, S.RESISTANCE_TO_SUPPORT):
                if down:
                    r.update(status=S.BROKEN_SUPPORT, previous_role='SUPPORT', current_role='NONE',
                             break_confirmed_at=observation_time, flip_confirmed_at=None)
                elif close < zl:
                    r['status'] = S.MINOR_BREAK
                elif old_status == S.MINOR_BREAK:
                    r['status'] = S.RECLAIMED_SUPPORT
            elif old_status in (S.ACTIVE_RESISTANCE, S.SUPPORT_TO_RESISTANCE):
                if up:
                    r.update(status=S.BROKEN_RESISTANCE, previous_role='RESISTANCE', current_role='NONE',
                             break_confirmed_at=observation_time, flip_confirmed_at=None)
            elif old_status == S.BROKEN_SUPPORT and observation_time > r['break_confirmed_at']:
                if close > zh:
                    r.update(status=S.RECLAIMED_SUPPORT, current_role='SUPPORT')
                elif (prior_price is not None and prior_price < zl and is_finite_number(high)
                      and high >= zl and close < zl - c.rejection_atr * atr):
                    r.update(status=S.SUPPORT_TO_RESISTANCE, current_role='RESISTANCE', flip_confirmed_at=observation_time)
            elif old_status == S.BROKEN_RESISTANCE and observation_time > r['break_confirmed_at']:
                if close < zl:
                    r.update(status=S.ACTIVE_RESISTANCE, current_role='RESISTANCE')
                elif (prior_price is not None and prior_price > zh and is_finite_number(low)
                      and low <= zh and close > zh + c.rejection_atr * atr):
                    r.update(status=S.RESISTANCE_TO_SUPPORT, current_role='SUPPORT', flip_confirmed_at=observation_time)
        r['interaction'] = record_interaction(r, close, previous=r.get('interaction'))
        r.update(last_observation_at=observation_time, last_price=close)
    return records


def lifecycle_view(records, close, observation_time, config=None):
    """Only current confirmed roles enter active view; broken zones remain history."""
    S = ZoneStatus
    support, resistance, testing, historical = [], [], [], []
    for r in sorted(records, key=lambda v: v['stable_zone_id']):
        z = dict(r, low=r['zone_low'], high=r['zone_high'], zone_id=r['stable_zone_id'],
                 role=r['current_role'].lower(), source='zone_lifecycle', as_of=r['last_observation_at'],
                 distance_pct=((r['zone_low'] + r['zone_high']) / 2 / close - 1) * 100)
        saved = r.get('interaction')
        # Same completed observation is idempotent; forming-bar projections are
        # never persisted and can only use already established confirmation.
        z['interaction'] = (deepcopy(saved) if saved and saved['price'] == close
                            and saved['zone_low'] == r['zone_low'] and saved['zone_high'] == r['zone_high']
                            else record_interaction(r, close, previous=saved))
        if r['status'] in (S.BROKEN_SUPPORT, S.BROKEN_RESISTANCE, S.INVALIDATED, S.ROLE_TRANSITION):
            historical.append(z)
        elif r['status'] == S.MINOR_BREAK:
            testing.append(z)
        elif r['last_seen_at'] == observation_time or r['status'] in (S.SUPPORT_TO_RESISTANCE, S.RESISTANCE_TO_SUPPORT):
            if ((r['current_role'] == 'SUPPORT' and close < z['low'])
                    or (r['current_role'] == 'RESISTANCE' and close > z['high'])):
                # An unconfirmed intraday crossing is not an active role or a
                # confirmed break. Keep it explicitly transitional in the view.
                historical.append(dict(z, status=S.ROLE_TRANSITION, current_role='NONE', role='none'))
            else:
                target = support if r['current_role'] == 'SUPPORT' else resistance
                target.append(z)
    # Contradictory overlapping records are not independently presented as valid
    # opposing roles. Confirmed flips win; otherwise mark the view as transition.
    for s in list(support):
        for r in list(resistance):
            if s not in support or r not in resistance:
                continue
            if overlap(s, r) >= (config or ZoneLifecycleConfig()).overlap_min:
                if r['status'] == S.SUPPORT_TO_RESISTANCE:
                    support.remove(s)
                elif s['status'] == S.RESISTANCE_TO_SUPPORT:
                    resistance.remove(r)
                else:
                    support.remove(s)
                    resistance.remove(r)
                    historical.append(dict(s, status=S.ROLE_TRANSITION, current_role='NONE', role='none'))
    return dict(support_zones=support, resistance_zones=resistance, active_zones=testing,
                historical_zones=historical)

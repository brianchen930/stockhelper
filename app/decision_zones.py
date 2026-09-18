"""One immutable zone reference for report, narrative and decision consumers."""
from copy import deepcopy
import hashlib
import json
from app.market_data import is_finite_number


def valid_zone(zone):
    return (isinstance(zone, dict) and all(is_finite_number(zone.get(k)) for k in ('low', 'high'))
            and 0 < zone['low'] <= zone['high'])


def overlap(a, b):
    if not valid_zone(a) or not valid_zone(b):
        return 0.
    union = max(a['high'], b['high']) - min(a['low'], b['low'])
    return max(0., min(a['high'], b['high']) - max(a['low'], b['low'])) / union if union else float(a['low'] == b['low'])


def reference(zone, role, symbol='', as_of=None, source='current_detector'):
    if not valid_zone(zone):
        return None
    result = deepcopy(zone)
    # Stable for identical boundaries/methods across runs; a changed boundary is
    # a new snapshot, not an assertion of persistent market-zone identity.
    identity = [symbol, role, float(zone['low']), float(zone['high']), sorted(zone.get('methods') or [])]
    result.update(zone_id=hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:20],
                  role=role, source=source, as_of=as_of)
    if zone.get('stable_zone_id'):
        result['zone_id'] = zone['stable_zone_id']
    return result


def displayed_zones(sr, symbol=''):
    sr = sr or {}
    from app.support_resistance_analysis.selection import actionable, boundary_distance
    price = sr.get('current_price')
    if 'zone_lifecycle' in sr:
        view = sr['zone_lifecycle']
        return {role: deepcopy(min((z for z in view.get(role + '_zones', [])
                    if valid_zone(z) and (role == 'active' or actionable(z, role, price))),
                key=lambda z: (boundary_distance(z, price), z.get('stable_zone_id', '')), default=None))
                for role in ('support', 'resistance', 'active')}
    selected = {}
    for role in ('support', 'resistance', 'active'):
        zones = [z for z in sr.get(role + '_zones', []) if valid_zone(z)
                 and (role == 'active' or actionable(z, role, price))]
        zone = min(zones, key=lambda z: (boundary_distance(z, price),
                   -z.get('strength_score', 0) if role == 'active' else 0), default=None)
        # Compatibility for structured consumers with only nearest_* populated.
        if role + '_zones' not in sr and role != 'active':
            zone = sr.get('nearest_' + role)
            if zone and (not valid_zone(zone) or not actionable(zone, role, price)):
                zone = None
        selected[role] = reference(zone, role, symbol, sr.get('as_of'))
    candidate = sr.get('bayesian_support_selected') or {}
    model = dict(low=candidate.get('support_low'), high=candidate.get('support_high'))
    from app.bayesian_support.rating import ZONE_OVERLAP_THRESHOLD
    matches = [(role, selected[role]) for role in ('active', 'support')
               if selected[role] and overlap(selected[role], model) >= ZONE_OVERLAP_THRESHOLD]
    if matches:
        role, zone = max(matches, key=lambda pair: overlap(pair[1], model))
        union = dict(zone, low=min(zone['low'], model['low']), high=max(zone['high'], model['high']))
        selected[role] = reference(union, role, symbol, sr.get('as_of'), 'display_union')
    from app.support_resistance_analysis.interaction import classify_interaction
    for role, zone in selected.items():
        if zone is not None:
            saved = zone.get('interaction')
            same_zone = bool(saved and saved.get('zone_low') == zone['low']
                             and saved.get('zone_high') == zone['high'] and saved.get('role') == role)
            if not (same_zone and saved.get('price') == sr.get('current_price')):
                zone['interaction'] = classify_interaction(sr.get('current_price'), zone['low'], zone['high'], role,
                    previous=saved if same_zone else None)
    return selected

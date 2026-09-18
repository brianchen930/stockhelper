"""Read-only role eligibility and display selection; never advances zone state."""
from copy import deepcopy
from datetime import datetime

from app.market_data import is_finite_number
from app.volatility import calculate_atr_distance


SUPPORT_STATES = {'ACTIVE_SUPPORT', 'RECLAIMED_SUPPORT', 'RESISTANCE_TO_SUPPORT'}
RESISTANCE_STATES = {'ACTIVE_RESISTANCE', 'SUPPORT_TO_RESISTANCE'}
TRANSITION_STATES = {'BROKEN_SUPPORT', 'BROKEN_RESISTANCE', 'ROLE_TRANSITION', 'MINOR_BREAK'}


def actionable(zone, role, price=None):
    if not zone:
        return False
    status = zone.get('status')
    allowed = SUPPORT_STATES if role == 'support' else RESISTANCE_STATES
    if status and status not in allowed:
        return False
    current = zone.get('current_role') or zone.get('role') or zone.get('type')
    if current and current.lower() != role:
        return False
    interaction = (zone.get('interaction') or {}).get('state', '')
    if interaction in ('SUPPORT_BREAKDOWN_PENDING', 'SUPPORT_BREAKDOWN_CONFIRMED',
                       'RESISTANCE_BREAKOUT_PENDING', 'RESISTANCE_BREAKOUT_CONFIRMED'):
        return False
    if is_finite_number(price):
        if role == 'support' and price < zone['low'] or role == 'resistance' and price > zone['high']:
            return False
    return True


def boundary_distance(zone, price):
    if is_finite_number(price):
        return max(zone['low'] - price, price - zone['high'], 0)
    return abs(zone.get('distance_pct') or 0)


def _day(value):
    try:
        return datetime.fromisoformat(str(value)).date()
    except (TypeError, ValueError):
        return None


def recent_nearby(zone, price, atr, observation_time, *, near_atr=None, recent_days=None):
    """Fresh crossing only; a newly refreshed historical record is not a new break."""
    from app.decision_engine import DecisionConfig
    from app.decision_zones import valid_zone
    from .config import ZoneDisplayConfig
    near_atr = DecisionConfig().near_zone_atr if near_atr is None else near_atr
    recent_days = ZoneDisplayConfig().recent_transition_days if recent_days is None else recent_days
    if not valid_zone(zone) or not is_finite_number(price) or not is_finite_number(atr) or atr <= 0:
        return False
    boundary = min(max(price, zone['low']), zone['high'])
    distance = calculate_atr_distance(price, boundary, atr)
    if distance is None or distance > near_atr:
        return False
    state = (zone.get('interaction') or {}).get('state')
    role = zone.get('previous_role') or (zone.get('interaction') or {}).get('role')
    if role and role.lower() == 'resistance' and price <= zone['high']:
        return False
    if role and role.lower() == 'support' and price >= zone['low']:
        return False
    confirmed = state in ('RESISTANCE_BREAKOUT_CONFIRMED', 'SUPPORT_BREAKDOWN_CONFIRMED')
    stamp = zone.get('break_confirmed_at') if confirmed or zone.get('status') in ('BROKEN_SUPPORT', 'BROKEN_RESISTANCE') else zone.get('as_of', zone.get('last_observation_at'))
    now, then = _day(observation_time), _day(stamp)
    return bool(now and then and 0 <= (now - then).days <= recent_days)


def select_zone_display(result, *, config=None):
    from app.decision_zones import displayed_zones
    from .config import ZoneDisplayConfig
    config = config or ZoneDisplayConfig()
    selected = displayed_zones(result)
    view = result.get('zone_lifecycle') or {}
    price, atr = result.get('current_price'), result.get('atr')
    stamp = view.get('observation_time') or result.get('as_of')
    candidates = []
    for zone in view.get('historical_zones', []) + view.get('active_zones', []):
        state = (zone.get('interaction') or {}).get('state')
        if (zone.get('status') in TRANSITION_STATES and state in (
                'RESISTANCE_BREAKOUT_PENDING', 'RESISTANCE_BREAKOUT_CONFIRMED',
                'SUPPORT_BREAKDOWN_PENDING', 'SUPPORT_BREAKDOWN_CONFIRMED')
                and recent_nearby(zone, price, atr, stamp, recent_days=config.recent_transition_days)):
            candidates.append(zone)
    secondary = min(candidates, key=lambda z: (boundary_distance(z, price), z.get('stable_zone_id', '')), default=None)
    def item(zone, role, priority):
        return dict(zone=deepcopy(zone), display_role=role, is_actionable=priority == 1,
                    display_priority=priority) if zone else None
    return dict(support=item(selected['support'], 'ACTIVE_SUPPORT', 1),
                resistance=item(selected['resistance'], 'ACTIVE_RESISTANCE', 1),
                secondary=item(secondary, 'NEARBY_ROLE_REVERSAL_CANDIDATE', 2))


def recent_breakout_reference(result, price, atr, observation_time, *, near_atr=None):
    """A fallback for a fresh crossing only when no active resistance remains."""
    view = result.get('zone_lifecycle') or {}
    stamp = observation_time or view.get('observation_time') or result.get('as_of')
    candidates = [z for z in view.get('historical_zones', [])
                  if (z.get('interaction') or {}).get('state') in (
                      'RESISTANCE_BREAKOUT_PENDING', 'RESISTANCE_BREAKOUT_CONFIRMED')
                  and recent_nearby(z, price, atr, stamp, near_atr=near_atr)]
    zone = min(candidates, key=lambda z: (boundary_distance(z, price), z.get('stable_zone_id', '')), default=None)
    return dict(deepcopy(zone), reference_as_of=stamp) if zone else None

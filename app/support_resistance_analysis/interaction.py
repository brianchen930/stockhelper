"""Price/zone interaction metadata; confirmation is supplied by existing policy."""
from enum import StrEnum

from app.market_data import is_finite_number


class LevelInteractionState(StrEnum):
    ABOVE_SUPPORT = 'ABOVE_SUPPORT'
    TESTING_SUPPORT = 'TESTING_SUPPORT'
    SUPPORT_BREAKDOWN_PENDING = 'SUPPORT_BREAKDOWN_PENDING'
    SUPPORT_BREAKDOWN_CONFIRMED = 'SUPPORT_BREAKDOWN_CONFIRMED'
    SUPPORT_BREAKDOWN_FAILED = 'SUPPORT_BREAKDOWN_FAILED'
    BELOW_RESISTANCE = 'BELOW_RESISTANCE'
    TESTING_RESISTANCE = 'TESTING_RESISTANCE'
    RESISTANCE_BREAKOUT_PENDING = 'RESISTANCE_BREAKOUT_PENDING'
    RESISTANCE_BREAKOUT_CONFIRMED = 'RESISTANCE_BREAKOUT_CONFIRMED'
    RESISTANCE_BREAKOUT_FAILED = 'RESISTANCE_BREAKOUT_FAILED'


def classify_interaction(price, low, high, role, *, previous=None, confirmed=False):
    """Classify one known role. Unknown provenance is deliberately not guessed.

    Callers match zone identities before passing previous. Distance to an
    untouched zone is boundary/price-1; crossed distance is price/boundary-1.
    Legacy center distances and ranking are left untouched.
    """
    if (role not in ('support', 'resistance') or
            not all(is_finite_number(v) for v in (price, low, high)) or
            price <= 0 or low <= 0 or high < low):
        return None
    S = LevelInteractionState
    previous = previous or {}
    prior = previous.get('state') if previous.get('role') == role else None
    support = role == 'support'
    pending = S.SUPPORT_BREAKDOWN_PENDING if support else S.RESISTANCE_BREAKOUT_PENDING
    failed = S.SUPPORT_BREAKDOWN_FAILED if support else S.RESISTANCE_BREAKOUT_FAILED
    location = 'below' if price < low else 'above' if price > high else 'inside'
    crossed = price < low if support else price > high
    ratio = (0.5 if high == low else (price - low) / (high - low)) if location == 'inside' else None
    position = None if ratio is None else 'lower' if ratio < .33 else 'upper' if ratio > .67 else 'middle'
    if crossed:
        state = (S.SUPPORT_BREAKDOWN_CONFIRMED if support else S.RESISTANCE_BREAKOUT_CONFIRMED) if confirmed else pending
        boundary = low if support else high
        distance = (price / boundary - 1) * 100
    else:
        state = failed if prior == pending else (
            (S.TESTING_SUPPORT if support else S.TESTING_RESISTANCE) if location == 'inside'
            else (S.ABOVE_SUPPORT if support else S.BELOW_RESISTANCE))
        boundary = high if support else low
        distance = None if location == 'inside' else (boundary / price - 1) * 100
    return dict(zone_low=low, zone_high=high, role=role, state=state,
                previous_state=prior, price=price, location=location,
                position_ratio=ratio, position_in_zone=position, distance_pct=distance)


def record_interaction(record, price, *, previous=None):
    """Use lifecycle confirmation/role, never infer confirmation from price."""
    status = record.get('status')
    role = record.get('current_role', '').lower()
    if status in ('BROKEN_SUPPORT', 'BROKEN_RESISTANCE'):
        role = record['previous_role'].lower()
    return classify_interaction(price, record['zone_low'], record['zone_high'], role,
        previous=previous, confirmed=status in ('BROKEN_SUPPORT', 'BROKEN_RESISTANCE'))

"""Price events against previously observed zones; never changes technical scores."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
from uuid import uuid4

from app.market_data import is_finite_number
from app.rules.base import BaseRule, RuleCategory
from app.support_resistance_analysis.formatting import format_zone_strength, select_active_zone

EVENTS = {
    'NEAR_SUPPORT': ('接近強支撐', '一般通知'),
    'NEAR_RESISTANCE': ('接近強壓力', '一般通知'),
    'ENTER_SUPPORT_ZONE': ('進入支撐區', '注意通知'),
    'ENTER_RESISTANCE_ZONE': ('進入壓力區', '注意通知'),
    'SUPPORT_BOUNCE': ('支撐反彈', '重要通知'),
    'SUPPORT_BREAKDOWN': ('跌破重要支撐', '重要通知'),
    'RESISTANCE_BREAKOUT': ('突破重要壓力', '重要通知'),
    'RESISTANCE_REJECTION': ('壓力受阻', '重要通知'),
}


@dataclass(frozen=True)
class SupportResistanceNotificationConfig:
    min_strength: float = 6.0
    near_pct: float = 0.015
    break_pct: float = 0.005
    reaction_pct: float = 0.01
    zone_match_pct: float = 0.005
    cooldown_hours: float = 72
    retention_days: int = 7
    enabled_events: tuple = tuple(EVENTS)

    def __post_init__(self):
        for name in ('min_strength', 'near_pct', 'break_pct', 'reaction_pct',
                     'zone_match_pct', 'cooldown_hours', 'retention_days'):
            value = getattr(self, name)
            if not is_finite_number(value) or value < 0:
                raise ValueError(f'{name} must be finite and nonnegative')
        if not set(self.enabled_events) <= set(EVENTS):
            raise ValueError('Unknown support/resistance event')


def decode_state(value):
    try:
        state = json.loads(value) if isinstance(value, str) else deepcopy(value or {})
        return state if isinstance(state, dict) else {}
    except (ValueError, TypeError):
        return {}


def acknowledge_events(state, events, now):
    """Called only after notifier confirms delivery; failures retain pending events."""
    state = deepcopy(state)
    ids = {event['id'] for event in events}
    for event in events:
        for zone in state.get('zones', []):
            if zone['id'] == event['zone_id']:
                zone['last_sent'] = now.isoformat()
    state['pending'] = [e for e in state.get('pending', []) if e['id'] not in ids]
    return state


class SupportResistanceRule(BaseRule):
    name = '支撐／壓力事件規則'
    rule_category = RuleCategory.EVENT

    def __init__(self, config=None):
        self.config = config or SupportResistanceNotificationConfig()

    def evaluate(self, context):
        previous = decode_state(context.get('support_resistance_state'))
        empty = dict(events=[], notifications=[], state=previous, should_notify=False)
        try:
            return self._evaluate(context, previous, empty)
        except Exception:
            logging.getLogger(__name__).exception('Support/resistance event analysis unavailable')
            return empty

    def _evaluate(self, context, previous, empty):
        sr = context.get('support_resistance') or {}
        price = sr.get('current_price')
        if (not context.get('analysis_is_valid', True) or sr.get('error') or
                not is_finite_number(price) or price <= 0 or not sr.get('as_of')):
            return empty
        # Daily Close from yfinance may still be a forming bar during the session.
        # Do not persist it as a confirmed previous close or emit close events.
        if not context.get('support_resistance_bar_closed', False):
            return empty
        c = self.config
        now = context.get('now') or datetime.now(timezone.utc)
        stamp = now.isoformat()
        cutoff = (now-timedelta(days=c.retention_days)).isoformat()
        as_of = sr['as_of']
        if previous.get('as_of') and as_of < previous['as_of']:
            return empty
        state = deepcopy(previous)
        pending = [e for e in state.get('pending', []) if e['observed_at'] >= cutoff]
        old = [z for z in state.get('zones', []) if z['seen_at'] >= cutoff]
        current = [z for key in ('support_zones', 'resistance_zones', 'active_zones') for z in sr.get(key, [])]
        tracked = []
        used = set()
        for zone in current:
            if not all(is_finite_number(zone.get(k)) for k in ('low', 'high', 'center', 'strength_score')):
                continue
            if zone['low'] <= 0 or zone['high'] < zone['low']:
                continue
            matches = [(max(abs(zone['low']-z['zone']['low']), abs(zone['high']-z['zone']['high']))/zone['center'], i, z)
                       for i, z in enumerate(old) if i not in used]
            match = min(matches, default=None, key=lambda item: item[0])
            if match and match[0] <= c.zone_match_pct:
                _, i, record = match
                used.add(i)
                # Freeze original boundaries/role during a test; today's detector
                # can flip a broken support to resistance. Do not lose that event.
                record['seen_at'] = stamp
                if (record['phase'] == 'broken' and as_of != previous.get('as_of')
                        and zone['type'] in ('support', 'resistance') and zone['type'] != record['role']):
                    record.update(zone=deepcopy(zone), role=zone['type'], phase='outside', active_event=None)
            elif zone['type'] in ('support', 'resistance'):
                record = dict(id=uuid4().hex, zone=deepcopy(zone), role=zone['type'],
                              seen_at=stamp, phase='outside', active_event=None, last_sent=None)
            else:
                # A first-ever active zone has no known approach direction.
                continue
            tracked.append(record)
        # Retain recently known zones even when they fall out of today's top 3.
        tracked.extend(z for i, z in enumerate(old) if i not in used)
        previous_price = previous.get('price')
        new_bar = as_of != previous.get('as_of')
        events = []
        for record in tracked:
            zone, role = record['zone'], record['role']
            low, high = zone['low'], zone['high']
            event = None
            terminal = record['phase'] == 'broken'
            if new_bar and previous_price is not None and not terminal:
                if role == 'support':
                    if previous_price >= low*(1-c.break_pct) and price < low*(1-c.break_pct):
                        event = 'SUPPORT_BREAKDOWN'
                        record['phase'] = 'broken'
                    elif record['phase'] == 'inside' and price > high*(1+c.reaction_pct):
                        event = 'SUPPORT_BOUNCE'
                        record['phase'] = 'outside'
                    elif previous_price > high and low <= price <= high:
                        event = 'ENTER_SUPPORT_ZONE'
                        record['phase'] = 'inside'
                else:
                    if previous_price <= high*(1+c.break_pct) and price > high*(1+c.break_pct):
                        event = 'RESISTANCE_BREAKOUT'
                        record['phase'] = 'broken'
                    elif record['phase'] == 'inside' and price < low*(1-c.reaction_pct):
                        event = 'RESISTANCE_REJECTION'
                        record['phase'] = 'outside'
                    elif previous_price < low and low <= price <= high:
                        event = 'ENTER_RESISTANCE_ZONE'
                        record['phase'] = 'inside'
            # Never infer a transition against a zone first discovered today.
            known_ids = {z['id'] for z in old}
            if record['id'] not in known_ids and event:
                event = None
                record['phase'] = 'outside'
            gap = (price-high)/high if role == 'support' else (low-price)/low
            near = 0 < gap <= c.near_pct and record['phase'] == 'outside'
            if event is None and near and (new_bar or (record.get('active_event') or '').startswith('NEAR_')):
                event = 'NEAR_' + role.upper()
            if event is None:
                if new_bar and not (low <= price <= high):
                    record['active_event'] = None
                continue
            strong = zone['strength_score'] >= c.min_strength
            last_sent = record.get('last_sent')
            cooled = not last_sent or now-datetime.fromisoformat(last_sent) >= timedelta(hours=c.cooldown_hours)
            changed = record.get('active_event') != event
            notify = strong and event in c.enabled_events and (changed or cooled)
            record['active_event'] = event
            boundary = high if event in ('RESISTANCE_BREAKOUT', 'SUPPORT_BOUNCE') else low if event in ('SUPPORT_BREAKDOWN', 'RESISTANCE_REJECTION') else high if role == 'support' else low
            item = dict(id=uuid4().hex, zone_id=record['id'], event_type='support_resistance', category=event,
                        message=EVENTS[event][0], level=EVENTS[event][1], score=0,
                        notify=notify, zone=deepcopy(zone), role=role, price=price,
                        distance_pct=(price/boundary-1)*100, as_of=as_of, observed_at=stamp)
            events.append(item)
            if notify and not any(e['zone_id'] == item['zone_id'] and e['category'] == event for e in pending):
                pending.append(item)
        state.update(zones=tracked, pending=pending, price=price, as_of=as_of)
        return dict(events=events, notifications=pending, state=state, should_notify=bool(pending))


DISPLAY_EVENT_PRIORITY = {
    'RESISTANCE_BREAKOUT': 0, 'SUPPORT_BREAKDOWN': 0,
    'ENTER_SUPPORT_ZONE': 1, 'ENTER_RESISTANCE_ZONE': 1, 'CURRENTLY_TESTING_ZONE': 1,
    'SUPPORT_BOUNCE': 2, 'RESISTANCE_REJECTION': 2,
    'NEAR_SUPPORT': 3, 'NEAR_RESISTANCE': 3,
}


def format_support_resistance_events(events, support_resistance=None):
    # Display selection only: do not mutate events, notification flags, or state.
    active = select_active_zone(support_resistance)
    # The SR section already displays active. Filter only a duplicate display
    # event, preserving the original event list and all notification state.
    choices = [event for event in events if not (
        event['category'] == 'CURRENTLY_TESTING_ZONE' and active
        and event['zone']['low'] == active['low'] and event['zone']['high'] == active['high'])]
    if not choices:
        return []
    event = min(choices, key=lambda e: (DISPLAY_EVENT_PRIORITY.get(e['category'], 4),
                                       abs(e.get('distance_pct', 0)), -e['zone'].get('strength_score', 0)))
    zone = event['zone']
    lines = ['【支撐 / 壓力事件】', f"・{event['message']}：{zone['low']:.2f}～{zone['high']:.2f}"]
    if event['category'] != 'CURRENTLY_TESTING_ZONE':
        lines.append(f"距離：{event['distance_pct']:+.2f}%")
    lines.append(format_zone_strength(zone))
    return lines

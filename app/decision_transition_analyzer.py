"""Explain already-stabilized decisions; never evaluate or change an action.

Reasons are observed evidence, not counterfactual proof of causality.
Only compact snapshots and actual transition events are persisted.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json

from app.decision_engine import DecisionConfig

SNAPSHOT_VERSION = 1


class TransitionReasonCode(StrEnum):
    RISK_GATE_RELEASED = 'RISK_GATE_RELEASED'
    RISK_GATE_PARTIALLY_RELEASED = 'RISK_GATE_PARTIALLY_RELEASED'
    RISK_GATE_ACTIVATED = 'RISK_GATE_ACTIVATED'
    CONFIRMED_SUPPORT_BREAK = 'CONFIRMED_SUPPORT_BREAK'
    SUPPORT_HOLD_CONFIRMED = 'SUPPORT_HOLD_CONFIRMED'
    SUPPORT_RECLAIMED = 'SUPPORT_RECLAIMED'
    RESISTANCE_BREAKOUT_CONFIRMED = 'RESISTANCE_BREAKOUT_CONFIRMED'
    RESISTANCE_REJECTION_CONFIRMED = 'RESISTANCE_REJECTION_CONFIRMED'
    BEARISH_MOMENTUM_WEAKENING = 'BEARISH_MOMENTUM_WEAKENING'
    BEARISH_MOMENTUM_ACCELERATING = 'BEARISH_MOMENTUM_ACCELERATING'
    BULLISH_MOMENTUM_STRENGTHENING = 'BULLISH_MOMENTUM_STRENGTHENING'
    BULLISH_MOMENTUM_WEAKENING = 'BULLISH_MOMENTUM_WEAKENING'
    INSTITUTIONAL_PRESSURE_EASING = 'INSTITUTIONAL_PRESSURE_EASING'
    INSTITUTIONAL_PRESSURE_INCREASING = 'INSTITUTIONAL_PRESSURE_INCREASING'
    VOLATILITY_RISK_EASING = 'VOLATILITY_RISK_EASING'
    VOLATILITY_RISK_INCREASING = 'VOLATILITY_RISK_INCREASING'
    SHORT_TERM_TREND_IMPROVED = 'SHORT_TERM_TREND_IMPROVED'
    SHORT_TERM_TREND_DETERIORATED = 'SHORT_TERM_TREND_DETERIORATED'
    MEDIUM_TERM_TREND_IMPROVED = 'MEDIUM_TERM_TREND_IMPROVED'
    MEDIUM_TERM_TREND_DETERIORATED = 'MEDIUM_TERM_TREND_DETERIORATED'
    RELATIVE_STRENGTH_IMPROVED = 'RELATIVE_STRENGTH_IMPROVED'
    RELATIVE_STRENGTH_DETERIORATED = 'RELATIVE_STRENGTH_DETERIORATED'
    OVEREXTENSION_CLEARED = 'OVEREXTENSION_CLEARED'
    OVEREXTENSION_TRIGGERED = 'OVEREXTENSION_TRIGGERED'
    KEY_LEVEL_RECLAIMED = 'KEY_LEVEL_RECLAIMED'
    KEY_LEVEL_LOST = 'KEY_LEVEL_LOST'
    CONFIRMATION_COUNT_REACHED = 'CONFIRMATION_COUNT_REACHED'
    CONFIRMATION_COUNT_RESET = 'CONFIRMATION_COUNT_RESET'
    ACTIVE_SUPPORT_REPOSITIONED = 'ACTIVE_SUPPORT_REPOSITIONED'
    NEW_ACTIVE_SUPPORT_AVAILABLE = 'NEW_ACTIVE_SUPPORT_AVAILABLE'
    ENTRY_THRESHOLD_CROSSED = 'ENTRY_THRESHOLD_CROSSED'
    HOLDER_RISK_THRESHOLD_CROSSED = 'HOLDER_RISK_THRESHOLD_CROSSED'
    SUPPORT_PROBABILITY_IMPROVED = 'SUPPORT_PROBABILITY_IMPROVED'
    SUPPORT_PROBABILITY_DETERIORATED = 'SUPPORT_PROBABILITY_DETERIORATED'
    INSUFFICIENT_TRANSITION_EVIDENCE = 'INSUFFICIENT_TRANSITION_EVIDENCE'


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def compact_context(context):
    values = asdict(context)
    for key, zone in list(values.items()):
        if key.endswith('_zone') and zone:
            values[key] = {k: zone[k] for k in (
                'stable_zone_id', 'zone_id', 'low', 'high', 'status', 'current_role', 'role'
            ) if k in zone}
    return values


def context_fingerprint(context):
    # Input only: output scores/counts never create a new observation identity.
    def normalize(value):
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [normalize(v) for v in value]
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value
    return hashlib.sha256(canonical({'version': SNAPSHOT_VERSION,
                                    'context': normalize(compact_context(context))}).encode('utf-8')).hexdigest()


def context_summary(context, decision, state, config=None):
    return dict(version=SNAPSHOT_VERSION, context=compact_context(context),
                fingerprint=context_fingerprint(context),
                decision=dict(entry_state=str(decision.entry_action), holder_state=str(decision.holder_action),
                              entry_score=decision.entry_score, risk_score=decision.risk_score,
                              risk_gate=sorted(decision.risk_gate)),
                confirmation={k: state.get(k) for k in (
                    'candidate_entry_state', 'candidate_holder_state',
                    'confirmation_count', 'holder_confirmation_count')},
                thresholds=asdict(config or DecisionConfig()))


@dataclass
class DecisionContextDelta:
    changed: dict = field(default_factory=dict)
    unchanged: dict = field(default_factory=dict)
    gates_added: list = field(default_factory=list)
    gates_removed: list = field(default_factory=list)

    @classmethod
    def between(cls, previous, current):
        delta = cls()
        for section in ('context', 'decision', 'confirmation'):
            old, new = previous.get(section, {}), current.get(section, {})
            for key in sorted(set(old) | set(new)):
                target = delta.unchanged if old.get(key) == new.get(key) else delta.changed
                target[f'{section}.{key}'] = {'previous': old.get(key), 'current': new.get(key)}
        before = set(previous['decision']['risk_gate'])
        after = set(current['decision']['risk_gate'])
        delta.gates_added = sorted(after - before)
        delta.gates_removed = sorted(before - after)
        return delta


@dataclass
class TransitionEvent:
    symbol: str
    role: str
    previous_state: str
    current_state: str
    transition_type: str
    primary_transition_reason: dict
    supporting_transition_reasons: list
    transition_reasons: list
    previous_context_snapshot: dict
    current_context_snapshot: dict
    timestamp: str
    data_timestamp: str | None
    observation_fingerprint: str


def zone_id(zone):
    return (zone or {}).get('stable_zone_id') or (zone or {}).get('zone_id')


def same_zone(a, b):
    if zone_id(a) or zone_id(b):
        return bool(zone_id(a) and zone_id(a) == zone_id(b))
    return a == b  # Older contexts can lack IDs; never match different geometry.


def zone_label(zone):
    if zone and zone.get('low') is not None and zone.get('high') is not None:
        return f"{zone['low']:g}～{zone['high']:g}"
    return '原支撐／壓力區'


def transition_type(role, before, after):
    order = (['AVOID', 'WAIT', 'WATCH_FOR_CONFIRMATION', 'ALLOW_PROBE_ENTRY', 'ENTRY_CONDITION_MET']
             if role == 'ENTRY' else
             ['EXIT_CONDITION_APPROACHING', 'REDUCE_EXPOSURE', 'TIGHTEN_RISK', 'HOLD_WITH_CAUTION', 'HOLD'])
    if before not in order or after not in order:
        return 'NEUTRAL_RECLASSIFICATION'
    return 'IMPROVEMENT' if order.index(after) > order.index(before) else 'RISK_WORSENING'


def evidence(previous, current, delta):
    """Return observed differences with direction, priority and explicit evidence."""
    R = TransitionReasonCode
    old, new = previous['context'], current['context']
    reasons = []

    def add(code, text, direction, priority=50, **details):
        reasons.append(dict(code=str(code), text=text, direction=direction,
                            priority=priority, evidence=details))

    gate_labels = {'SUPPORT_BREAK': '目前支撐失守', 'PREVIOUS_SUPPORT_BREAK': '前一支撐失守',
                   'LOW_SUPPORT_PROBABILITY': '支撐成功率過低', 'STRONG_INSTITUTIONAL_PRESSURE': '法人強力壓力',
                   'BEARISH_MACD_ACCELERATION': '空方動能增強', 'EXTREME_VOLATILITY': '極高波動',
                   'BOTH_TRENDS_BEARISH': '短中期皆偏空', 'DATA_INSUFFICIENT': '資料不足'}
    if delta.gates_added:
        add(R.RISK_GATE_ACTIVATED, '新增風險限制：' + '、'.join(gate_labels.get(k, k) for k in delta.gates_added), -1, 100,
            added=delta.gates_added)
    if delta.gates_removed:
        partial = bool(current['decision']['risk_gate'])
        released = '、'.join(gate_labels.get(k, k) for k in delta.gates_removed)
        add(R.RISK_GATE_PARTIALLY_RELEASED if partial else R.RISK_GATE_RELEASED,
            released + ('限制解除，仍有其他風險限制' if partial else '限制解除，原有風險限制已全部解除'), 1, 100,
            removed=delta.gates_removed, remaining=current['decision']['risk_gate'])

    # Track the old active zone even when lifecycle has moved it into previous_support.
    a, b = old.get('active_support_zone'), new.get('active_support_zone')
    old_status = old.get('current_active_support_status')
    if old_status in (None, 'UNKNOWN'):
        old_status = old.get('support_status')
    status = new.get('current_active_support_status')
    if status in (None, 'UNKNOWN'):
        status = new.get('support_status')
    comparable = same_zone(a, b)
    target = b
    if a and not comparable and same_zone(a, new.get('previous_support_zone')):
        status = new.get('previous_support_status')
        target = new['previous_support_zone']
        comparable = True
    broken = ('CONFIRMED_BREAK', 'BROKEN_SUPPORT', 'FLIPPED_TO_RESISTANCE', 'SUPPORT_TO_RESISTANCE')
    if comparable:
        if status in broken and old_status not in broken:
            add(R.CONFIRMED_SUPPORT_BREAK, zone_label(target) + ' 防守區確認失守', -1, 110, zone=target)
        elif status in ('HOLDING', 'HOLD_CONFIRMED') and old_status not in ('HOLDING', 'HOLD_CONFIRMED'):
            add(R.SUPPORT_HOLD_CONFIRMED, zone_label(target) + ' 支撐由測試轉為確認守穩', 1, 85, zone=target)
        elif status == 'RECLAIMED' and old_status != 'RECLAIMED':
            add(R.SUPPORT_RECLAIMED, zone_label(target) + ' 支撐重新站回', 1, 105, zone=target)
    if b and not same_zone(a, b):
        add(R.ACTIVE_SUPPORT_REPOSITIONED if a else R.NEW_ACTIVE_SUPPORT_AVAILABLE,
            '目前支撐改為 ' + zone_label(b) + '，不代表舊支撐風險解除', 0, 45, previous_zone=a, zone=b)

    probability = {'VERY_LOW': 0, '極低': 0, 'LOW': 1, '低': 1, 'MEDIUM': 2, '中': 2,
                   'HIGH': 3, '高': 3, 'VERY_HIGH': 4, '極高': 4}
    x, y = old.get('support_probability'), new.get('support_probability')
    if x in probability and y in probability and probability[x] != probability[y]:
        improve = probability[y] > probability[x]
        add(R.SUPPORT_PROBABILITY_IMPROVED if improve else R.SUPPORT_PROBABILITY_DETERIORATED,
            f'當前支撐成功率等級 {x} → {y}', 1 if improve else -1, 70, previous=x, current=y)

    a, b = old.get('active_resistance_zone'), new.get('active_resistance_zone')
    before, after = old.get('resistance_status'), new.get('resistance_status')
    if a and not same_zone(a, b) and same_zone(a, new.get('previous_resistance_zone')):
        b, after = new['previous_resistance_zone'], new.get('previous_resistance_status')
    if same_zone(a, b) and before != after:
        if after == 'CONFIRMED_BREAKOUT':
            add(R.RESISTANCE_BREAKOUT_CONFIRMED, zone_label(b) + ' 壓力區確認突破', 1, 105, zone=b)
        elif after == 'REJECTED':
            add(R.RESISTANCE_REJECTION_CONFIRMED, zone_label(b) + ' 回測確認遇阻', -1, 85, zone=b)

    momentum = {
        'bearish_weakening': (R.BEARISH_MOMENTUM_WEAKENING, 'MACD 空方動能由增強轉為減弱', 1),
        'bearish_strengthening': (R.BEARISH_MOMENTUM_ACCELERATING, 'MACD 空方動能轉為增強', -1),
        'bullish_strengthening': (R.BULLISH_MOMENTUM_STRENGTHENING, 'MACD 多方動能轉強', 1),
        'bullish_weakening': (R.BULLISH_MOMENTUM_WEAKENING, 'MACD 多方動能轉弱', -1),
    }
    before, after = old.get('macd_momentum'), new.get('macd_momentum')
    if before in momentum and after in momentum and before != after:
        code, text, direction = momentum[after]
        if after == 'bearish_weakening' and before != 'bearish_strengthening':
            text = 'MACD 轉為空方動能減弱狀態'
        add(code, text, direction, previous=before, current=after)

    for prefix, label in (('short_term', '短期'), ('medium_term', '中期')):
        x, y = old.get(prefix + '_score'), new.get(prefix + '_score')
        dx, dy = old.get(prefix + '_direction'), new.get(prefix + '_direction')
        if x is not None and y is not None and (x != y or dx != dy):
            direction = 1 if (dy, y) > (dx, x) else -1
            code = R[f'{prefix.upper()}_TREND_' + ('IMPROVED' if direction > 0 else 'DETERIORATED')]
            crossing = dx != dy or (x <= 0 < y) or (y < 0 <= x)
            add(code, f'{label}趨勢分數 {x:g} → {y:g}', direction,
                95 if prefix == 'medium_term' and direction < 0 and crossing else 60 if crossing else 10,
                previous_direction=dx, current_direction=dy, previous=x, current=y)

    levels = {'STRONG_PRESSURE': 0, 'BEARISH': 1, 'NEUTRAL': 2, 'BULLISH': 3, 'STRONG_SUPPORT': 4}
    x, y = old.get('institutional_level'), new.get('institutional_level')
    if x in levels and y in levels and x != y:
        improve = levels[y] > levels[x]
        add(R.INSTITUTIONAL_PRESSURE_EASING if improve else R.INSTITUTIONAL_PRESSURE_INCREASING,
            f'法人評級 {x} → {y}', 1 if improve else -1, previous=x, current=y)
    elif x == y and y in ('STRONG_PRESSURE', 'BEARISH') and not old.get('institutional_selling_weakened') and new.get('institutional_selling_weakened'):
        add(R.INSTITUTIONAL_PRESSURE_EASING, '法人仍偏空，但賣壓邊際減弱', 1)

    volatility = {'LOW': 0, '低波動': 0, 'NORMAL': 1, '中等波動': 1, 'HIGH': 2, '高波動': 2,
                  'EXTREME': 3, '極高波動': 3}
    x, y = old.get('volatility_level'), new.get('volatility_level')
    if x in volatility and y in volatility and volatility[x] != volatility[y]:
        improve = volatility[y] < volatility[x]
        add(R.VOLATILITY_RISK_EASING if improve else R.VOLATILITY_RISK_INCREASING,
            f'波動風險等級 {x} → {y}', 1 if improve else -1)
    x, y = old.get('relative_market_strength'), new.get('relative_market_strength')
    if x is not None and y is not None and x != y:
        threshold = current['thresholds']['relative_strength_threshold']
        bucket = lambda v: -1 if v <= -threshold else 1 if v >= threshold else 0
        if bucket(x) != bucket(y):
            add(R.RELATIVE_STRENGTH_IMPROVED if y > x else R.RELATIVE_STRENGTH_DETERIORATED,
                f'相對大盤強弱 {x:g} → {y:g}，跨越既有判斷門檻', 1 if y > x else -1)
    if old.get('overextended') != new.get('overextended'):
        raised = new.get('overextended')
        add(R.OVEREXTENSION_TRIGGERED if raised else R.OVEREXTENSION_CLEARED,
            '價格進入過熱條件' if raised else '價格脫離過熱條件', -1 if raised else 1, 95)
    for key, label in (('price_above_ma5', '5 日均線'), ('price_above_ma20', '20 日均線'), ('price_above_ma60', '60 日均線')):
        x, y = old.get(key), new.get(key)
        if isinstance(x, bool) and isinstance(y, bool) and x != y:
            add(R.KEY_LEVEL_RECLAIMED if y else R.KEY_LEVEL_LOST,
                ('價格重新站上 ' if y else '價格跌至下方：') + label, 1 if y else -1, 55,
                field=key, previous=x, current=y)
    return reasons


def analyze_transition(previous, current, symbol, timestamp=None):
    """Pure analysis of supplied snapshots. Does not mutate either snapshot."""
    if not previous or previous.get('version') != current.get('version'):
        return [], {'status': 'BASELINE_UNAVAILABLE', 'message': '缺少可比較的前次快照，無法可靠歸因'}
    delta = DecisionContextDelta.between(previous, current)
    candidates = evidence(previous, current, delta)
    debug = dict(status='ANALYZED', context_delta=asdict(delta), roles={})
    events = []
    for role, key, count_key, candidate_key in (
        ('ENTRY', 'entry_state', 'confirmation_count', 'candidate_entry_state'),
        ('HOLDER', 'holder_state', 'holder_confirmation_count', 'candidate_holder_state')):
        before, after = previous['decision'][key], current['decision'][key]
        kind = transition_type(role, before, after) if before != after else 'UNCHANGED'
        reasons = [dict(r) for r in candidates]
        p, c = previous['confirmation'], current['confirmation']
        x, y = p.get(count_key) or 0, c.get(count_key) or 0
        required = current['thresholds']['confirmation_required']
        same_candidate = p.get(candidate_key) == c.get(candidate_key) and c.get(candidate_key) == after
        aggressive = role == 'HOLDER' or after in ('ALLOW_PROBE_ENTRY', 'ENTRY_CONDITION_MET')
        if same_candidate and aggressive and x < required <= y and kind == 'IMPROVEMENT':
            reasons.append(dict(code='CONFIRMATION_COUNT_REACHED', text=f'連續有效新觀察確認 {x} → {y}，達到 {required} 次門檻',
                                direction=1, priority=120, evidence={'previous': x, 'current': y, 'required': required}))
        elif x > y:
            reasons.append(dict(code='CONFIRMATION_COUNT_RESET', text=f'候選條件確認次數 {x} → {y}',
                                direction=-1, priority=40, evidence={'previous': x, 'current': y}))
        score_key = 'entry_score' if role == 'ENTRY' else 'risk_score'
        thresholds = (('probe_score_min', 'entry_score_min') if role == 'ENTRY' else ('tighten_risk_score',))
        for name in thresholds:
            threshold = current['thresholds'][name]
            x, y = previous['decision'][score_key], current['decision'][score_key]
            if (x < threshold <= y) or (y < threshold <= x):
                reasons.append(dict(code='ENTRY_THRESHOLD_CROSSED' if role == 'ENTRY' else 'HOLDER_RISK_THRESHOLD_CROSSED',
                    text=f'{"進場分數" if role == "ENTRY" else "持有風險分數"} {x:g} → {y:g}，跨越 {threshold:g} 門檻',
                    direction=(1 if y > x else -1) * (1 if role == 'ENTRY' else -1), priority=90,
                    evidence={'threshold': name, 'value': threshold, 'previous': x, 'current': y}))
        # Entry gates do not directly control Holder; keep them as lower priority evidence there.
        if role == 'HOLDER':
            reasons = [dict(r, priority=35) if r['code'].startswith('RISK_GATE_') else r for r in reasons]
        direction = 1 if kind == 'IMPROVEMENT' else -1 if kind == 'RISK_WORSENING' else 0
        selected = sorted([r for r in reasons if r['direction'] in (0, direction) or direction == 0],
                          key=lambda r: (-r['priority'], r['code']))
        debug['roles'][role] = dict(previous=before, current=after, transition_type=kind,
                                    all_reasons=reasons, selected_reasons=selected,
                                    ignored_unchanged_conditions=delta.unchanged)
        if before == after:
            continue
        if not selected:
            selected = [dict(code='INSUFFICIENT_TRANSITION_EVIDENCE', text='狀態已變更；現有差異不足以可靠判定主要依據',
                             direction=0, priority=0, evidence={})]
        supporting = selected[1:]
        # Keep full codes/debug, but do not repeat the same break as a second sentence about its gate.
        if selected[0]['code'] == 'CONFIRMED_SUPPORT_BREAK':
            supporting = [r for r in supporting if not (
                r['code'] == 'RISK_GATE_ACTIVATED' and
                set(r['evidence'].get('added', [])) <= {'SUPPORT_BREAK', 'PREVIOUS_SUPPORT_BREAK'})]
        events.append(TransitionEvent(symbol, role, before, after, kind, selected[0], supporting,
            [r['code'] for r in selected], previous, current,
            timestamp or datetime.now(timezone.utc).isoformat(), current['context'].get('observation_time'),
            current['fingerprint']))
    return events, debug

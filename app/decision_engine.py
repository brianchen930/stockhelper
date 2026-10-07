"""Deterministic market decisions. No IO; scores are not probabilities."""
from dataclasses import dataclass, field
from enum import StrEnum
from app.position_status import PositionStatus


class ActionState(StrEnum):
    AVOID = 'AVOID'
    WAIT = 'WAIT'
    WATCH_FOR_CONFIRMATION = 'WATCH_FOR_CONFIRMATION'
    ALLOW_PROBE_ENTRY = 'ALLOW_PROBE_ENTRY'
    ENTRY_CONDITION_MET = 'ENTRY_CONDITION_MET'
    DO_NOT_CHASE = 'DO_NOT_CHASE'


class HolderActionState(StrEnum):
    HOLD = 'HOLD'
    HOLD_WITH_CAUTION = 'HOLD_WITH_CAUTION'
    TIGHTEN_RISK = 'TIGHTEN_RISK'
    REDUCE_EXPOSURE = 'REDUCE_EXPOSURE'
    EXIT_CONDITION_APPROACHING = 'EXIT_CONDITION_APPROACHING'


class ReasonCode(StrEnum):
    LOW_SUPPORT_PROBABILITY = 'LOW_SUPPORT_PROBABILITY'
    HIGH_SUPPORT_PROBABILITY = 'HIGH_SUPPORT_PROBABILITY'
    STRONG_INSTITUTIONAL_PRESSURE = 'STRONG_INSTITUTIONAL_PRESSURE'
    POSITIVE_INSTITUTIONAL_FLOW = 'POSITIVE_INSTITUTIONAL_FLOW'
    BEARISH_SHORT_TREND = 'BEARISH_SHORT_TREND'
    BULLISH_SHORT_TREND = 'BULLISH_SHORT_TREND'
    BEARISH_MACD_ACCELERATION = 'BEARISH_MACD_ACCELERATION'
    BULLISH_MACD_ACCELERATION = 'BULLISH_MACD_ACCELERATION'
    SELLING_WEAKENING = 'SELLING_WEAKENING'
    EXTREME_VOLATILITY = 'EXTREME_VOLATILITY'
    HIGH_VOLATILITY = 'HIGH_VOLATILITY'
    NEAR_SUPPORT = 'NEAR_SUPPORT'
    NEAR_RESISTANCE = 'NEAR_RESISTANCE'
    SUPPORT_BREAK = 'SUPPORT_BREAK'
    PREVIOUS_SUPPORT_BREAK = 'PREVIOUS_SUPPORT_BREAK'
    MINOR_SUPPORT_BREAK = 'MINOR_SUPPORT_BREAK'
    SUPPORT_HOLD = 'SUPPORT_HOLD'
    SUPPORT_RECLAIMED = 'SUPPORT_RECLAIMED'
    RESISTANCE_BREAKOUT = 'RESISTANCE_BREAKOUT'
    RESISTANCE_REJECTION = 'RESISTANCE_REJECTION'
    OVEREXTENDED = 'OVEREXTENDED'
    RELATIVE_WEAKNESS = 'RELATIVE_WEAKNESS'
    RELATIVE_STRENGTH = 'RELATIVE_STRENGTH'
    BOTH_TRENDS_BEARISH = 'BOTH_TRENDS_BEARISH'
    BEARISH_MEDIUM_TREND = 'BEARISH_MEDIUM_TREND'
    DATA_INSUFFICIENT = 'DATA_INSUFFICIENT'
    CONFIRMATION_PENDING = 'CONFIRMATION_PENDING'


@dataclass(frozen=True)
class DecisionConfig:
    confirmation_required: int = 2
    confirmed_break_atr: float = .75
    persistent_break_atr: float = .35
    breakout_atr: float = .5
    volume_breakout_atr: float = .25
    near_zone_atr: float = 1.
    volume_confirmation_ratio: float = 1.5
    overextended_ma5_pct: float = 6.
    overextended_ma20_pct: float = 12.
    entry_score_min: float = 6.
    probe_score_min: float = 3.
    min_support_strength: float = 4.
    probability_zone_overlap: float = .7
    relative_strength_threshold: float = 2.
    tighten_risk_score: float = 4.
    bearish_acceleration_atr: float = .02
    retained_evidence_max_days: int = 10
    trade_min_upside_pct: float = 2.
    trade_add_upside_pct: float = 3.
    trade_extreme_atr_pct: float = 8.
    holder_large_profit_pct: float = 10.
    holder_near_cost_pct: float = 3.
    trade_observe_score: float = 1.
    trade_consider_reduce_score: float = 3.
    trade_reduce_score: float = 6.
    trade_exit_score: float = 9.
    trade_medium_weight: float = 3.
    trade_support_weight: float = 4.
    trade_macd_weight: float = 2.5
    trade_institutional_weight: float = 3.
    trade_relative_weight: float = 2.
    trade_short_weight: float = .75
    trade_oscillator_weight: float = .25
    trade_volatility_multiplier: float = .15
    signal_bullish_exit_pct: float = .2
    signal_bearish_exit_pct: float = .2
    signal_confirmation_required: int = 2
    critical_min_risk_dimensions: int = 3
    structural_support_min_strength: float = 6.
    structural_breakout_max_days: int = 90
    breakout_platform_bars: int = 20
    breakout_platform_width_atr: float = 4.
    structural_swing_window: int = 5
    structural_swing_atr_factor: float = 1.

    def __post_init__(self):
        import math
        for key, value in self.__dict__.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{key} must be positive and finite')
        if not isinstance(self.confirmation_required, int) or self.confirmation_required < 2:
            raise ValueError('confirmation_required must be an integer >= 2')
        if not isinstance(self.signal_confirmation_required, int):
            raise ValueError('signal_confirmation_required must be an integer >= 1')
        if not isinstance(self.breakout_platform_bars, int) or self.breakout_platform_bars < 5:
            raise ValueError('breakout_platform_bars must be an integer >= 5')
        if not isinstance(self.structural_swing_window, int) or self.structural_swing_window < 3:
            raise ValueError('structural_swing_window must be an integer >= 3')
        if not isinstance(self.critical_min_risk_dimensions, int) or self.critical_min_risk_dimensions < 3:
            raise ValueError('critical_min_risk_dimensions must be an integer >= 3')
        if self.persistent_break_atr > self.confirmed_break_atr or self.probability_zone_overlap > 1:
            raise ValueError('Invalid break/overlap thresholds')
        if self.volume_breakout_atr >= self.breakout_atr or self.probe_score_min >= self.entry_score_min:
            raise ValueError('Entry thresholds must be strictly increasing')
        if self.holder_near_cost_pct >= self.holder_large_profit_pct:
            raise ValueError('Near-cost threshold must be below large-profit threshold')
        if not (self.trade_observe_score < self.trade_consider_reduce_score < self.trade_reduce_score < self.trade_exit_score):
            raise ValueError('Trade action thresholds must be strictly increasing')


@dataclass(frozen=True)
class DecisionContext:
    symbol: str = ''
    observation_time: str | None = None
    observation_complete: bool = False
    short_term_direction: int = 0
    short_term_score: float = 0
    medium_term_direction: int = 0
    medium_term_score: float = 0
    support_probability: str = 'UNKNOWN'
    base_support_probability: float | None = None
    adjusted_support_probability: float | None = None
    adjusted_support_level: str | None = None
    support_strength: float | None = None
    resistance_strength: float | None = None
    distance_to_support: float | None = None
    distance_to_resistance: float | None = None
    institutional_level: str = 'UNKNOWN'
    institutional_score: float | None = None
    institutional_confidence: float = 0.
    institutional_selling_weakened: bool = False
    institutional_as_of: str | None = None
    institutional_freshness: str = 'FRESH'
    atr: float | None = None
    atr_percent: float | None = None
    volatility_level: str = '資料不足'
    macd_state: str = 'unknown'
    macd_momentum: str = 'data_insufficient'
    macd_histogram_change_atr: float | None = None
    rsi_state: str = 'UNKNOWN'
    kd_state: str = 'UNKNOWN'
    relative_market_strength: float | None = None
    price_above_ma5: bool | None = None
    price_above_ma20: bool | None = None
    price_above_ma60: bool | None = None
    volume_state: str = 'UNKNOWN'
    volume_ratio: float | None = None
    support_status: str = 'UNKNOWN'
    resistance_status: str = 'UNKNOWN'
    break_distance_atr: float | None = None
    overextended: bool = False
    data_valid: bool = True
    data_issues: list[str] = field(default_factory=list)
    observation_status: str | None = None
    current_price: float | None = None
    active_support_zone: dict | None = None
    active_resistance_zone: dict | None = None
    previous_support_zone: dict | None = None
    previous_resistance_zone: dict | None = None
    previous_support_status: str = 'UNKNOWN'
    previous_resistance_status: str = 'UNKNOWN'
    current_active_support_status: str = 'UNKNOWN'
    previous_break_distance_atr: float | None = None
    zone_lifecycle_statuses: dict = field(default_factory=dict)
    level_interactions: list[dict] = field(default_factory=list)
    breakout_reference_zone: dict | None = None
    position_status: PositionStatus = PositionStatus.WATCHING
    average_cost: float | None = None
    shares: int | None = None
    entry_date: str | None = None
    unrealized_return: float | None = None
    volume_deteriorating: bool = False
    raw_signal_state: str = '無法判斷'
    signal_ma_values: dict = field(default_factory=dict)
    previous_close: float | None = None
    breakout_event: dict | None = None
    structural_support_zone: dict | None = None
    structural_support_status: str = 'UNKNOWN'
    structural_assessment: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.institutional_freshness != 'FRESH':
            for key, value in dict(institutional_level='UNKNOWN', institutional_score=None,
                                   institutional_confidence=0., institutional_selling_weakened=False).items():
                object.__setattr__(self, key, value)


@dataclass
class TradingDecision:
    entry_action: ActionState
    holder_action: HolderActionState
    entry_reasons: list[ReasonCode] = field(default_factory=list)
    holder_reasons: list[ReasonCode] = field(default_factory=list)
    entry_score: float = 0
    risk_score: float = 0
    risk_gate: list[ReasonCode] = field(default_factory=list)
    previous_action_state: dict = field(default_factory=dict)
    confirmation_count: int = 0
    entry_upgrade_triggers: list[dict] = field(default_factory=list)
    entry_downgrade_triggers: list[dict] = field(default_factory=list)
    holder_improve_triggers: list[dict] = field(default_factory=list)
    holder_worsen_triggers: list[dict] = field(default_factory=list)
    price_context: dict = field(default_factory=dict)
    transition_events: list[dict] = field(default_factory=list)
    transition_debug: dict = field(default_factory=dict)
    current_trigger_evidence: dict = field(default_factory=dict)
    retained_state_evidence: dict = field(default_factory=dict)
    state_basis: str = 'INSUFFICIENT_EVIDENCE'
    consistency_warnings: list[str] = field(default_factory=list)
    entry_paths: dict = field(default_factory=dict)
    decision: str = 'OBSERVE'
    position_status: str = 'WATCHING'
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    follow_up: list[str] = field(default_factory=list)
    trade_evidence: dict = field(default_factory=dict)
    trade_confirmation_count: int = 0
    raw_action_state: str = '無法判斷'
    final_action_state: str = '無法判斷'
    hysteresis_held: bool = False
    signal_hysteresis: dict = field(default_factory=dict)
    hysteresis_candidate_state: str = '無法判斷'
    pending_candidate_state: str | None = None
    signal_confirmation_count: int = 0
    required_confirmations: int = 2
    persistence_held: bool = False
    signal_persistence: dict = field(default_factory=dict)
    critical_event: bool = False
    bypass_persistence: bool = False
    bypass_hysteresis: bool = False
    critical_event_rules: list[str] = field(default_factory=list)
    pre_bypass_candidate_state: str = '無法判斷'
    persistence_cleared: bool = False
    critical_event_details: dict = field(default_factory=dict)


class DecisionEngine:
    def __init__(self, config=None):
        self.config = config or DecisionConfig()

    def evaluate(self, c: DecisionContext, previous_action_state=None) -> TradingDecision:
        from app.holder_structure import resolve_structure, structural_break
        c = resolve_structure(c, previous_action_state, self.config)
        A, H, R = ActionState, HolderActionState, ReasonCode
        reasons, gates, risk_contributions = [], [], []
        entry = risk = 0.
        def add(reason, points=0, danger=0, gate=False):
            nonlocal entry, risk
            reasons.append(reason)
            entry += points
            risk += danger
            if danger:
                risk_contributions.append({'code': str(reason), 'points': danger})
            if gate:
                gates.append(reason)
        low = c.support_probability in ('低', '極低', 'LOW', 'VERY_LOW')
        high = c.support_probability in ('高', '極高', 'HIGH', 'VERY_HIGH')
        pressure = c.institutional_level == 'STRONG_PRESSURE'
        positive = c.institutional_level in ('BULLISH', 'STRONG_SUPPORT')
        broken = c.support_status in ('CONFIRMED_BREAK', 'FLIPPED_TO_RESISTANCE')
        if c.active_support_zone and c.current_price is not None:
            broken = broken and c.current_price < c.active_support_zone['low']
        previous_broken = c.previous_support_status in ('CONFIRMED_BREAK', 'FLIPPED_TO_RESISTANCE')
        bearish_macd = (c.macd_momentum == 'bearish_strengthening'
                       and (c.macd_histogram_change_atr is None
                            or c.macd_histogram_change_atr <= -self.config.bearish_acceleration_atr))
        if not c.data_valid:
            return self._with_triggers(TradingDecision(A.WAIT, H.HOLD_WITH_CAUTION, [R.DATA_INSUFFICIENT],
                                   [R.DATA_INSUFFICIENT], risk_gate=[R.DATA_INSUFFICIENT]), c, previous_action_state)
        if broken:
            add(R.SUPPORT_BREAK, -3, 3, True)
        elif previous_broken:
            add(R.PREVIOUS_SUPPORT_BREAK, -3, 3, True)
        broken = broken or previous_broken  # Risk survives, but never relabels the current zone.
        if low:
            add(R.LOW_SUPPORT_PROBABILITY, -2, 1, c.support_probability in ('極低', 'VERY_LOW'))
        if pressure:
            add(R.STRONG_INSTITUTIONAL_PRESSURE, -2, 2, True)
        elif positive:
            add(R.POSITIVE_INSTITUTIONAL_FLOW, 2)
        elif c.institutional_level == 'BEARISH':
            entry -= 1
            risk += 1
            risk_contributions.append({'code': 'INSTITUTIONAL_BEARISH', 'points': 1})
        # Base rating and institutional category each contribute exactly once.
        # Adjusted probability and raw institutional score remain audit inputs only.
        if bearish_macd:
            add(R.BEARISH_MACD_ACCELERATION, -1, 1)
        elif c.macd_momentum == 'bullish_strengthening':
            add(R.BULLISH_MACD_ACCELERATION, 1)
        elif c.macd_momentum == 'bearish_weakening':
            add(R.SELLING_WEAKENING, 1)
        elif c.institutional_selling_weakened:
            add(R.SELLING_WEAKENING)  # Already incorporated in institutional level.
        if (c.volatility_level in ('極高波動', 'EXTREME')
                or (c.atr_percent is not None and c.atr_percent >= self.config.trade_extreme_atr_pct)):
            add(R.EXTREME_VOLATILITY, -1, 1)
        elif c.volatility_level in ('高波動', 'HIGH'):
            add(R.HIGH_VOLATILITY, -1, 1)
        if c.short_term_direction < 0:
            add(R.BEARISH_SHORT_TREND, -min(abs(c.short_term_score) / 2, 2), 1)
        elif c.short_term_direction > 0:
            add(R.BULLISH_SHORT_TREND, min(abs(c.short_term_score) / 2, 2))
        entry += max(-2, min(2, c.medium_term_score / 3))
        if c.medium_term_direction < 0:
            add(R.BEARISH_MEDIUM_TREND, gate=True)
        if c.short_term_direction < 0 and c.medium_term_direction < 0:
            add(R.BOTH_TRENDS_BEARISH, -1, 2, True)
        if high:
            add(R.HIGH_SUPPORT_PROBABILITY, 2)
        if c.overextended:
            add(R.OVEREXTENDED, -1, 1)
        if c.distance_to_resistance is not None and c.distance_to_resistance <= self.config.near_zone_atr:
            add(R.NEAR_RESISTANCE)
        if c.distance_to_support is not None and c.distance_to_support <= self.config.near_zone_atr:
            add(R.NEAR_SUPPORT)
        if c.support_status == 'MINOR_BREAK':
            add(R.MINOR_SUPPORT_BREAK, -1, 1)
        if c.support_status in ('HOLDING', 'RECLAIMED'):
            add(R.SUPPORT_HOLD if c.support_status == 'HOLDING' else R.SUPPORT_RECLAIMED)
        if 'CONFIRMED_BREAKOUT' in (c.resistance_status, c.previous_resistance_status):
            add(R.RESISTANCE_BREAKOUT)
        if c.resistance_status == 'REJECTED':
            add(R.RESISTANCE_REJECTION, -2, 1)
        if c.relative_market_strength is not None and abs(c.relative_market_strength) >= self.config.relative_strength_threshold:
            strong = c.relative_market_strength > 0
            add(R.RELATIVE_STRENGTH if strong else R.RELATIVE_WEAKNESS, 1 if strong else -1, 0 if strong else 1)
        # Conservative fallback; independent paths own positive entry decisions.
        if (low and pressure) or broken or R.BOTH_TRENDS_BEARISH in gates:
            action = A.AVOID
        elif c.overextended:
            action = A.DO_NOT_CHASE
        elif c.resistance_status == 'REJECTED' and (bearish_macd or c.short_term_direction < 0):
            action = A.WAIT
        elif (high and c.institutional_level in ('NEUTRAL', 'BULLISH', 'STRONG_SUPPORT')
              and (c.macd_momentum == 'bearish_weakening' or c.institutional_selling_weakened)) or c.support_status == 'RECLAIMED':
            action = A.WATCH_FOR_CONFIRMATION
        elif c.resistance_status in ('APPROACHING', 'TESTING', 'CONFIRMED_BREAKOUT') and positive and c.volume_state == 'EXPANDING':
            action = A.WATCH_FOR_CONFIRMATION
        else:
            action = A.WAIT
        # Entry risk uses nearby zones; holder selling requires thesis defense.
        holder_broken = structural_break(c)
        holder_reasons = [r for r in reasons if r not in (R.SUPPORT_BREAK, R.PREVIOUS_SUPPORT_BREAK)]
        if holder_broken:
            holder_reasons.append(R.SUPPORT_BREAK)
        if holder_broken and c.medium_term_direction < 0 and bearish_macd and pressure:
            holder = H.EXIT_CONDITION_APPROACHING
            rule = 'BROKEN_SUPPORT_MEDIUM_BEARISH_MACD_PRESSURE'
        elif holder_broken and c.medium_term_direction < 0:
            holder = H.REDUCE_EXPOSURE
            rule = 'BROKEN_SUPPORT_WITH_CONFIRMING_RISK'
        elif holder_broken or (low and pressure) or risk >= self.config.tighten_risk_score:
            holder = H.TIGHTEN_RISK
            rule = 'SUPPORT_BREAK' if holder_broken else 'LOW_SUPPORT_WITH_PRESSURE' if low and pressure else 'RISK_SCORE_THRESHOLD'
        elif (risk or c.short_term_direction <= 0 or c.medium_term_direction <= 0
              or c.atr is None or c.institutional_level in ('UNKNOWN', 'MIXED')
              or c.macd_momentum == 'data_insufficient'):
            holder = H.HOLD_WITH_CAUTION
            rule = 'CAUTION'
        else:
            holder = H.HOLD
            rule = 'HOLD'
        decision = TradingDecision(action, holder, list(dict.fromkeys(reasons)),
                                   list(dict.fromkeys(holder_reasons)), round(entry, 2), risk, gates)
        from app.decision_evidence import record_evidence
        record_evidence(decision, c, rule, risk_contributions, bearish_macd, self.config)
        return self._with_triggers(decision, c, previous_action_state)

    def apply_signal_hysteresis(self, d, c, previous_action_state=None):
        """Stabilize the existing MA strategy signal; never use entry/risk scores.

        Entry remains the strategy's strict Close > MA5 > MA20 > MA60
        (or the inverse). Exit tolerates a small reversal of every adjacent
        pair, measured as a percentage of the slower MA. Equality retains.
        """
        import math
        previous = dict(previous_action_state or {})
        old = previous.get('signal_state')
        raw = c.raw_signal_state
        d.previous_action_state = previous
        d.raw_action_state = d.final_action_state = raw
        d.hysteresis_held = False
        values = [c.signal_ma_values.get(k) for k in ('close', 'ma5', 'ma20', 'ma60')]
        valid = (c.data_valid and raw in ('偏多', '觀望', '偏空')
                 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                         and math.isfinite(v) and v > 0 for v in values))
        gaps = [(a - b) / b * 100 for a, b in zip(values, values[1:])] if valid else []
        if d.state_basis == 'STALE_DATA':
            d.final_action_state = old or raw
        elif valid:
            bull_exit = -self.config.signal_bullish_exit_pct
            bear_exit = self.config.signal_bearish_exit_pct
            if old == '偏多' and all(g >= bull_exit or math.isclose(g, bull_exit) for g in gaps):
                d.final_action_state = old
            elif old == '偏空' and all(g <= bear_exit or math.isclose(g, bear_exit) for g in gaps):
                d.final_action_state = old
            d.hysteresis_held = d.final_action_state == old and raw != old
        d.signal_hysteresis = dict(
            raw_action_state=raw, previous_action_state=old,
            final_action_state=d.final_action_state, hysteresis_held=d.hysteresis_held,
            valid=valid, adjacent_gap_pct=gaps,
            thresholds=dict(bullish_exit_pct=self.config.signal_bullish_exit_pct,
                            bearish_exit_pct=self.config.signal_bearish_exit_pct))
        return d

    def apply_signal_persistence(self, d, c, previous=None, *, observation_time=None, read_only=False):
        """Confirm the hysteresis candidate against the last committed state.

        This is a pure transition: callers persist the returned memory. A
        monitoring timestamp identifies a round, independently of daily bars.
        Reapplying a round against the same previous snapshot is idempotent.
        """
        from datetime import datetime, timezone
        old = previous or {}
        current = old.get('signal_state')
        pending = old.get('signal_pending_candidate_state')
        count = old.get('signal_confirmation_count') or 0
        last_time = old.get('signal_last_observation_time')
        previous_state = old.get('signal_previous_action_state')
        candidate = d.signal_hysteresis['final_action_state']
        from app.critical_events import evaluate_critical_event
        critical = evaluate_critical_event(d, c, self.config)
        d.critical_event = critical['critical_event']
        d.critical_event_rules = critical['rules']
        d.pre_bypass_candidate_state = candidate
        d.bypass_persistence = d.bypass_hysteresis = d.persistence_cleared = False
        required = self.config.signal_confirmation_required
        stamp = observation_time or c.observation_time
        if stamp:
            try:
                value = datetime.fromisoformat(stamp)
                stamp = value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError):
                stamp = None
        status = 'INVALID'
        confirmed_count = 0
        final = current or d.final_action_state
        if d.state_basis == 'STALE_DATA' or (stamp and last_time and stamp < last_time):
            status = 'STALE'
        elif read_only:
            # Queries expose committed memory; they are not monitoring rounds.
            status = 'READ_ONLY'
        elif stamp and last_time and stamp == last_time:
            status = 'REPLAY'
        elif d.signal_hysteresis['valid'] and stamp:
            previous_state = current
            last_time = stamp
            if d.critical_event:
                target = critical['target_state']
                # Critical events only escalate to risk (or retain that risk).
                # They never authorize a bullish candidate or fast recovery.
                d.bypass_persistence = current != target
                d.bypass_hysteresis = d.bypass_persistence and candidate != target
                d.persistence_cleared = pending is not None or count > 0
                final, pending, count = target, None, 0
                status = 'CRITICAL_BYPASS' if d.bypass_persistence else 'CRITICAL_RISK_RETAINED'
            elif current not in ('偏多', '觀望', '偏空'):
                # No state transition exists on the first valid observation.
                final, pending, count, status = candidate, None, 0, 'INITIALIZED'
            elif candidate == current:
                pending, count, status = None, 0, 'UNCHANGED'
            else:
                count = count + 1 if candidate == pending else 1
                pending = candidate
                status = 'PENDING'
                if count >= required:
                    confirmed_count = count
                    final, pending, count, status = candidate, None, 0, 'CONFIRMED'
        d.hysteresis_candidate_state = candidate
        d.pending_candidate_state = pending
        d.signal_confirmation_count = count
        d.required_confirmations = required
        d.persistence_held = (status == 'PENDING'
            or (status == 'REPLAY' and pending is not None and candidate != final)
            or (status == 'READ_ONLY' and current is not None and candidate != final))
        d.final_action_state = final
        d.signal_persistence = dict(
            hysteresis_candidate_state=candidate, previous_action_state=current,
            pending_candidate_state=pending, confirmation_count=count,
            required_confirmations=required, persistence_held=d.persistence_held,
            final_action_state=final, last_observation_time=last_time,
            status=status, confirmed_count=confirmed_count)
        d.critical_event_details = dict(critical, bypass_persistence=d.bypass_persistence,
            bypass_hysteresis=d.bypass_hysteresis, pre_bypass_candidate_state=candidate,
            final_action_state=final, persistence_cleared=d.persistence_cleared)
        return dict(signal_state=final if status not in ('INVALID', 'STALE', 'REPLAY', 'READ_ONLY') else current,
                    signal_previous_action_state=previous_state,
                    signal_pending_candidate_state=pending, signal_confirmation_count=count,
                    signal_last_observation_time=last_time)

    def _with_triggers(self, decision, context, previous_action_state=None):
        from app.entry_paths import evaluate_entry_paths, entry_action
        decision.entry_paths = evaluate_entry_paths(context, decision.risk_gate, self.config, decision.entry_score)
        decision.risk_gate = list(dict.fromkeys(ReasonCode(code)
            for key in ('breakout', 'pullback') for code in decision.entry_paths[key]['risk_blockers']))
        if (ReasonCode.RESISTANCE_BREAKOUT in decision.entry_reasons
                and not decision.entry_paths['breakout']['core_met']):
            decision.entry_reasons.remove(ReasonCode.RESISTANCE_BREAKOUT)
        if decision.entry_paths['breakout']['core_met'] and ReasonCode.RESISTANCE_BREAKOUT not in decision.entry_reasons:
            decision.entry_reasons.append(ReasonCode.RESISTANCE_BREAKOUT)
        decision.entry_score = max(p['entry_score'] for k, p in decision.entry_paths.items()
                                   if k in ('breakout', 'pullback'))
        decision.entry_action = entry_action(decision.entry_paths, decision.entry_action, context.volatility_level)
        from app.decision_transitions import attach_triggers
        decision = self.evaluate_trade(attach_triggers(decision, context, self.config), context)
        self.apply_signal_hysteresis(decision, context, previous_action_state)
        self.apply_signal_persistence(decision, context, previous_action_state)
        return decision

    def evaluate_trade(self, d, c):
        """Weight existing analysis states; never recalculate indicators.

        Positive points mean risk, negative points mean protection. Raw market
        scores/gates belong to the existing entry/holder analysis and are not
        summed again. Cost cannot change these weights or the action band.
        """
        from app.position_status import read_position_status
        cfg = self.config
        from app.holder_structure import resolve_structure, structural_break, short_support, BROKEN
        c = resolve_structure(c, None, cfg)
        holding = read_position_status(c.position_status) == PositionStatus.HOLDING
        d.position_status = str(read_position_status(c.position_status))
        d.decision = 'OBSERVE'
        d.reasons, d.warnings, d.follow_up = [], [], []
        d.trade_confirmation_count = 0
        from app.market_data import is_finite_number
        from app.entry_paths import observation_day
        checks = dict(analysis_valid=c.data_valid,
            price_valid=is_finite_number(c.current_price) and c.current_price > 0,
            observation_complete=c.observation_complete,
            observation_valid=bool(observation_day(c.observation_time))
                and c.observation_status not in ('MISSING', 'INVALID', 'FUTURE'),
            atr_valid=is_finite_number(c.atr) and c.atr > 0,
            volatility_valid=c.volatility_level not in (None, '', '資料不足', 'UNKNOWN'),
            chronological=d.state_basis != 'STALE_DATA')
        blockers = []
        if not checks['analysis_valid']:
            blockers.extend(c.data_issues or ['短中期分析未通過有效性檢查'])
        if not checks['price_valid']:
            blockers.append('日線價格缺失或無效')
        if not checks['observation_valid']:
            blockers.append({'MISSING': '缺少日線資料日期，無法確認收盤',
                'INVALID': '日線資料日期格式無效，無法確認收盤',
                'FUTURE': f'日線資料日期 {c.observation_time} 晚於本輪日期',
            }.get(c.observation_status, '日線資料日期缺失或格式無效'))
        if not checks['atr_valid']:
            blockers.append('ATR 缺失或無效，無法完成風險評估')
        if not checks['volatility_valid']:
            blockers.append('波動度分類缺失，無法完成風險評估')
        if not checks['chronological']:
            blockers.append(f'本輪日線 {c.observation_time} 早於已採用的日線，未更新交易動作')
        # An unfinished daily bar is usable market evidence. Only events that
        # require a close (entry confirmation and risk recovery) wait for it.
        valid = checks['valid'] = all(value for key, value in checks.items()
                                     if key != 'observation_complete')
        checks['blocking_reasons'] = list(dict.fromkeys(blockers))
        checks['observation_time'] = c.observation_time
        contributions = []
        def add(code, dimension, points, text):
            contributions.append(dict(code=code, dimension=dimension, points=round(points, 4), text=text))

        broken = structural_break(c)
        accelerating = ReasonCode.BEARISH_MACD_ACCELERATION in d.entry_reasons
        sz = c.structural_support_zone
        strength = (sz or {}).get('strength_score')
        important = bool(c.breakout_event) or (strength is not None and strength >= cfg.min_support_strength)
        short_zone, short_status = short_support(c)
        short_broken = bool(short_zone and c.current_price is not None and c.current_price < short_zone['low']
                            and short_status in (*BROKEN, 'MINOR_BREAK'))
        sell_eligible = bool(broken and c.medium_term_direction < 0)
        if broken:
            weight = cfg.trade_support_weight * (1.25 if important else 1.)
            add('SUPPORT_BREAK', 'structure', weight, '結構防守已確認失守')
        elif c.structural_support_status == 'MINOR_BREAK':
            add('MINOR_SUPPORT_BREAK', 'structure', cfg.trade_support_weight * .375, '結構防守初步跌破，尚未確認失守')
        elif (sz and c.current_price is not None and c.current_price >= sz['low']
              and c.structural_support_status in ('HOLDING', 'RECLAIMED', 'RETESTING')):
            add('SUPPORT_INTACT', 'structure', -cfg.trade_support_weight * (.75 if important else .5),
                f"結構防守 {sz['low']:g}～{sz['high']:g}" + ('已收復' if c.structural_support_status == 'RECLAIMED' else '仍守穩'))
        if short_broken and (not sz or (short_zone['low'], short_zone['high']) != (sz['low'], sz['high'])):
            add('SHORT_TERM_SUPPORT_BREAK', 'short_term', .75, '短線支撐失守，短線轉弱／觀察是否站回')
        # Probability only adjusts an intact/unconfirmed zone, never cancels a break.
        if not broken and c.support_status != 'MINOR_BREAK' and c.active_support_zone:
            if c.support_probability in ('低', '極低', 'LOW', 'VERY_LOW'):
                add('LOW_SUPPORT_PROBABILITY', 'structure', .5, '支撐成功率評等偏低')
        if c.resistance_status == 'REJECTED':
            add('RESISTANCE_REJECTION', 'structure', 1., '上方壓力測試受阻')
        elif ReasonCode.RESISTANCE_BREAKOUT in d.entry_reasons:
            add('RESISTANCE_BREAKOUT', 'structure', -1.,
                '突破幅度／量能條件已具備' if c.observation_complete
                else '盤中突破幅度／量能條件已具備，待收盤確認')
        elif c.distance_to_resistance is not None and 0 <= c.distance_to_resistance <= cfg.near_zone_atr:
            add('NEAR_RESISTANCE', 'structure', .5, '現價接近上方壓力，操作空間受限')
        if c.medium_term_direction:
            add('MEDIUM_TREND', 'trend', -cfg.trade_medium_weight * c.medium_term_direction,
                '中期趨勢偏多' if c.medium_term_direction > 0 else '中期趨勢偏空')
        if c.short_term_direction:
            add('SHORT_TREND', 'trend', -cfg.trade_short_weight * c.short_term_direction,
                '短期趨勢偏多' if c.short_term_direction > 0 else '短期趨勢偏空')
        if accelerating:
            add('MACD_ACCELERATION', 'momentum', cfg.trade_macd_weight, 'MACD 空方動能加速')
        elif c.macd_momentum in ('bearish_strengthening', 'bullish_weakening'):
            add('MACD_WEAKENING', 'momentum', cfg.trade_macd_weight * .5, 'MACD 動能轉弱')
        elif c.macd_momentum in ('bearish_weakening', 'bullish_strengthening'):
            add('MACD_IMPROVING', 'momentum', -cfg.trade_macd_weight * .3,
                'MACD 空方動能減弱' if c.macd_momentum == 'bearish_weakening' else 'MACD 多方動能增強')
        if c.kd_state in ('BULLISH', 'BEARISH'):
            add('KD', 'momentum', cfg.trade_oscillator_weight * (-1 if c.kd_state == 'BULLISH' else 1),
                'KD 尚未明顯轉空' if c.kd_state == 'BULLISH' else 'KD 偏空')
        if c.rsi_state in ('OVERBOUGHT', 'OVERSOLD'):
            add('RSI', 'momentum', cfg.trade_oscillator_weight,
                'RSI 超買，短線回檔風險增加' if c.rsi_state == 'OVERBOUGHT' else 'RSI 超賣，弱勢風險仍在')
        if c.overextended:
            add('OVEREXTENDED', 'momentum', .5, '價格相對均線過度延伸')
        # The category already aggregates institutions; do not add raw score or
        # adjusted support probability again, or claim unobserved synchronous sales.
        flow_weights = {'STRONG_PRESSURE': (1., '法人賣壓明顯'), 'BEARISH': (2/3, '法人籌碼偏空'),
                        'BULLISH': (-.5, '法人籌碼偏多'), 'STRONG_SUPPORT': (-1., '法人買盤強勁')}
        if c.institutional_level in flow_weights:
            weight, label = flow_weights[c.institutional_level]
            add('INSTITUTIONAL_FLOW', 'institutional', cfg.trade_institutional_weight * weight, label)
        if c.relative_market_strength is not None and abs(c.relative_market_strength) >= cfg.relative_strength_threshold:
            weak = c.relative_market_strength < 0
            add('RELATIVE_MARKET', 'relative_market', cfg.trade_relative_weight * (1 if weak else -1),
                f"相對大盤{'落後' if weak else '領先'} {abs(c.relative_market_strength):g} 個百分點")
        if c.volume_deteriorating:
            add('VOLUME_DETERIORATING', 'volume', 1., '下跌伴隨放量，量價結構轉弱')

        extreme = c.volatility_level in ('極高波動', 'EXTREME') or (c.atr_percent is not None and c.atr_percent >= cfg.trade_extreme_atr_pct)
        high_volatility = extreme or c.volatility_level in ('高波動', 'HIGH')
        # Count NET adverse dimensions; correlated RSI/KD/MACD and short/medium
        # trends cannot masquerade as multiple independent confirmations.
        dimensions = {}
        for item in contributions:
            dimensions[item['dimension']] = dimensions.get(item['dimension'], 0.) + item['points']
        adverse = [key for key, points in dimensions.items() if points > 0]
        raw_score = sum(item['points'] for item in contributions)
        if high_volatility:
            bonus = max(0., raw_score) * cfg.trade_volatility_multiplier * (2 if extreme else 1)
            if bonus:
                add('ATR_RISK_AMPLIFIER', 'volatility', bonus, 'ATR 波動度極高，放大既有風險' if extreme else 'ATR 波動度偏高，放大既有風險')
        score = round(sum(item['points'] for item in contributions), 4)
        def action_for(value):
            if sell_eligible and value >= cfg.trade_exit_score and len(adverse) >= 3:
                return 'EXIT'
            if sell_eligible and value >= cfg.trade_reduce_score and len(adverse) >= 2:
                return 'REDUCE'
            if sell_eligible and value >= cfg.trade_consider_reduce_score and len(adverse) >= 2:
                return 'CONSIDER_REDUCE'
            if short_broken or value >= cfg.trade_observe_score or not any(item['points'] < 0 for item in contributions):
                return 'OBSERVE'
            return 'HOLD'
        market_action = action_for(score)
        # Missing analysis is not bullish evidence. It prevents an unqualified
        # HOLD, but does not suppress established multi-signal market risk.
        missing = []
        if not sz and not broken:
            missing.append('缺少有效結構防守參考')
        if c.institutional_level == 'UNKNOWN':
            missing.append('缺少有效法人資料')
        if c.macd_momentum in ('unknown', 'data_insufficient'):
            missing.append('MACD 資料不足')
        constraints = missing if market_action == 'HOLD' else []
        if constraints:
            market_action = 'OBSERVE'
        without_atr = action_for(round(raw_score, 4))
        if missing and without_atr == 'HOLD':
            without_atr = 'OBSERVE'
        atr_affects_action = valid and holding and without_atr != market_action
        if valid:
            if holding:
                d.decision = market_action
            else:
                # EntryEngine owns eligibility and close confirmation. The
                # weighted holder assessment must not veto a confirmed entry.
                # ENTRY_CONDITION_MET is this engine's normal ALLOW_ENTRY state.
                d.decision = ('ENTER' if d.entry_action in (
                    ActionState.ENTRY_CONDITION_MET, ActionState.ALLOW_PROBE_ENTRY,
                    'ALLOW_ENTRY') else 'OBSERVE')
        cost_context = self._holding_cost_context(c) if holding else None
        d.trade_evidence = dict(version=4, checks=checks, contributions=contributions,
            support_policy='STRUCTURAL_V1', structural_support_zone=sz,
            structural_support_status=c.structural_support_status, breakout_event=c.breakout_event,
            short_term_support_zone=short_zone, short_term_support_broken=short_broken,
            reduction_eligible=sell_eligible,
            action_constraints=constraints,
            volatility_effect=dict(affects_action=atr_affects_action,
                action_without_volatility=without_atr, action_with_volatility=market_action),
            dimension_scores={key: round(value, 4) for key, value in dimensions.items()},
            weighted_score=score, raw_weighted_score=round(raw_score, 4), risk_dimensions=adverse,
            market_action=market_action if valid else 'OBSERVE',
            reduce_signal=market_action in ('CONSIDER_REDUCE', 'REDUCE', 'EXIT') and valid,
            risk_evidence=[x['text'] for x in contributions if x['points'] > 0],
            positive_evidence=[x['text'] for x in contributions if x['points'] < 0],
            average_cost=c.average_cost if holding else None, cost_context=cost_context,
            unrealized_return=cost_context['return_percent'] if cost_context else None,
            thresholds=dict(observe=cfg.trade_observe_score, consider_reduce=cfg.trade_consider_reduce_score,
                            reduce=cfg.trade_reduce_score, exit=cfg.trade_exit_score))
        # Show at most four influential observations, with a meaningful opposing
        # signal when present. All contributions remain available for auditing.
        ranked = sorted((x for x in contributions if x['dimension'] != 'volatility' or atr_affects_action),
                        key=lambda x: abs(x['points']), reverse=True)
        primary = [x for x in ranked if (x['points'] > 0) == (market_action != 'HOLD')]
        opposing = [x for x in ranked if x not in primary]
        selected = primary[:3] + opposing[:1] if primary else opposing[:4]
        if atr_affects_action:
            amplifier = next(x for x in ranked if x['code'] == 'ATR_RISK_AMPLIFIER')
            if amplifier not in selected:
                selected = selected[:3] + [amplifier]
        selected = sorted(selected, key=lambda x: abs(x['points']), reverse=True)
        if not valid:
            d.reasons = list(checks['blocking_reasons'])
            selected = []
        else:
            d.reasons = [x['text'] for x in selected]
            if constraints:
                selected = selected[:3]
                d.reasons = constraints + d.reasons[:3]
            if not d.reasons:
                d.reasons = ['本輪沒有足夠方向性證據，維持觀察']
        d.trade_evidence['key_signal_codes'] = [x['code'] for x in selected]
        d.follow_up = ([f"觀察支撐 {sz['low']:g}～{sz['high']:g} 的守穩／失守與中期趨勢是否同步變化"]
                       if sz else ['觀察中期趨勢變化，待有效支撐價位形成後重新評估'])
        if not valid:
            d.follow_up = []
            if not checks['chronological']:
                d.follow_up.append('取得不早於已採用日期的日線後重新評估')
            if not checks['observation_valid']:
                d.follow_up.append('取得日期有效的日線後重新評估')
            if not checks['analysis_valid'] or not checks['price_valid']:
                d.follow_up.extend('補齊並驗證' + reason.replace('缺失或無效', '').replace('未通過有效性檢查', '')
                    for reason in dict.fromkeys((c.data_issues or (['短中期分析'] if not c.data_valid else []))
                        + ([] if checks['price_valid'] else ['日線價格'])))
            if not checks['atr_valid'] or not checks['volatility_valid']:
                d.follow_up.append('取得有效 ATR 與波動度分類後重新評估風險')
        elif constraints:
            d.follow_up.extend(reason + '，補齊後重新評估' for reason in constraints)
        if d.decision == 'ENTER':
            path = d.entry_paths.get(d.entry_paths.get('primary_path'), {})
            kind = '試單進場' if d.entry_action == ActionState.ALLOW_PROBE_ENTRY else '正常進場'
            d.reasons = [kind + '條件已完成確認'] + list(path.get('reasons', []))
            # Keep weighted evidence (including missing institutional data) for
            # audit; it describes holder risk, not the WATCHING entry authority.
            d.trade_evidence['decision_basis'] = 'ENTRY_ACTION'
            d.trade_evidence['entry_action'] = str(d.entry_action)
        return d

    def _holding_cost_context(self, c):
        """Return percentages use percentage points, matching the context adapter."""
        import math
        def finite(value):
            return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        if not all(finite(v) and v > 0 for v in (c.average_cost, c.current_price)):
            return None
        calculated = (c.current_price / c.average_cost - 1) * 100
        # Prefer the supplied P&L when consistent (including display rounding).
        rate = (c.unrealized_return if finite(c.unrealized_return)
                and abs(c.unrealized_return - calculated) <= .01 else calculated)
        if abs(rate) <= self.config.holder_near_cost_pct + 1e-8:
            state = 'NEAR_COST'
        elif rate >= self.config.holder_large_profit_pct - 1e-8:
            state = 'LARGE_PROFIT'
        else:
            state = 'LOSS' if rate < 0 else 'PROFIT'
        sz = c.active_support_zone
        distance = max(sz['low'] - c.average_cost, c.average_cost - sz['high'], 0) if sz else None
        return dict(state=state, return_percent=rate,
                    support_near_cost=distance is not None and distance / c.average_cost * 100 <= self.config.holder_near_cost_pct)

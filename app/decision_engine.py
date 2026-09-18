"""Deterministic market decisions. No IO; scores are not probabilities."""
from dataclasses import dataclass, field
from enum import StrEnum


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
    DATA_INSUFFICIENT = 'DATA_INSUFFICIENT'
    CONFIRMATION_PENDING = 'CONFIRMATION_PENDING'


@dataclass(frozen=True)
class DecisionConfig:
    confirmation_required: int = 2
    confirmed_break_atr: float = .75
    persistent_break_atr: float = .35
    breakout_atr: float = .5
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

    def __post_init__(self):
        import math
        for key, value in self.__dict__.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{key} must be positive and finite')
        if not isinstance(self.confirmation_required, int) or self.confirmation_required < 2:
            raise ValueError('confirmation_required must be an integer >= 2')
        if self.persistent_break_atr > self.confirmed_break_atr or self.probability_zone_overlap > 1:
            raise ValueError('Invalid break/overlap thresholds')


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


class DecisionEngine:
    def __init__(self, config=None):
        self.config = config or DecisionConfig()

    def evaluate(self, c: DecisionContext) -> TradingDecision:
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
                                   [R.DATA_INSUFFICIENT], risk_gate=[R.DATA_INSUFFICIENT]), c)
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
            add(R.BEARISH_MACD_ACCELERATION, -1, 1, True)
        elif c.macd_momentum == 'bullish_strengthening':
            add(R.BULLISH_MACD_ACCELERATION, 1)
        elif c.macd_momentum == 'bearish_weakening':
            add(R.SELLING_WEAKENING, 1)
        elif c.institutional_selling_weakened:
            add(R.SELLING_WEAKENING)  # Already incorporated in institutional level.
        if c.volatility_level in ('極高波動', 'EXTREME'):
            add(R.EXTREME_VOLATILITY, -1, 1, True)
        elif c.volatility_level in ('高波動', 'HIGH'):
            add(R.HIGH_VOLATILITY, -1, 1)
        if c.short_term_direction < 0:
            add(R.BEARISH_SHORT_TREND, -min(abs(c.short_term_score) / 2, 2), 1)
        elif c.short_term_direction > 0:
            add(R.BULLISH_SHORT_TREND, min(abs(c.short_term_score) / 2, 2))
        entry += max(-2, min(2, c.medium_term_score / 3))
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
            add(R.SUPPORT_HOLD if c.support_status == 'HOLDING' else R.SUPPORT_RECLAIMED, 1)
        if 'CONFIRMED_BREAKOUT' in (c.resistance_status, c.previous_resistance_status):
            add(R.RESISTANCE_BREAKOUT, 2)
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
        if broken and c.medium_term_direction < 0 and bearish_macd and pressure:
            holder = H.EXIT_CONDITION_APPROACHING
            rule = 'BROKEN_SUPPORT_MEDIUM_BEARISH_MACD_PRESSURE'
        elif broken and (c.institutional_level in ('BEARISH', 'STRONG_PRESSURE') or bearish_macd):
            holder = H.REDUCE_EXPOSURE
            rule = 'BROKEN_SUPPORT_WITH_CONFIRMING_RISK'
        elif broken or (low and pressure) or risk >= self.config.tighten_risk_score:
            holder = H.TIGHTEN_RISK
            rule = 'SUPPORT_BREAK' if broken else 'LOW_SUPPORT_WITH_PRESSURE' if low and pressure else 'RISK_SCORE_THRESHOLD'
        elif (risk or c.short_term_direction <= 0 or c.medium_term_direction <= 0
              or c.atr is None or c.institutional_level in ('UNKNOWN', 'MIXED')
              or c.macd_momentum == 'data_insufficient'):
            holder = H.HOLD_WITH_CAUTION
            rule = 'CAUTION'
        else:
            holder = H.HOLD
            rule = 'HOLD'
        decision = TradingDecision(action, holder, list(dict.fromkeys(reasons)),
                                   list(dict.fromkeys(reasons)), round(entry, 2), risk, gates)
        from app.decision_evidence import record_evidence
        record_evidence(decision, c, rule, risk_contributions, bearish_macd, self.config)
        return self._with_triggers(decision, c)

    def _with_triggers(self, decision, context):
        from app.entry_paths import evaluate_entry_paths, entry_action
        decision.entry_paths = evaluate_entry_paths(context, decision.risk_gate, self.config)
        if (ReasonCode.RESISTANCE_BREAKOUT in decision.entry_reasons
                and decision.entry_paths['breakout']['interaction_state'] != 'RESISTANCE_BREAKOUT_CONFIRMED'):
            decision.entry_reasons.remove(ReasonCode.RESISTANCE_BREAKOUT)
            decision.entry_score = round(decision.entry_score - 2, 2)
        decision.entry_action = entry_action(decision.entry_paths, decision.entry_action, context.volatility_level)
        from app.decision_transitions import attach_triggers
        return attach_triggers(decision, context, self.config)

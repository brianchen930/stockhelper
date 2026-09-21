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
    trade_min_upside_pct: float = 2.
    trade_add_upside_pct: float = 3.
    trade_extreme_atr_pct: float = 8.

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
    decision: str = 'WAIT'
    position_status: str = 'WATCHING'
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    follow_up: list[str] = field(default_factory=list)
    trade_evidence: dict = field(default_factory=dict)
    trade_confirmation_count: int = 0


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
        return self.evaluate_trade(attach_triggers(decision, context, self.config), context)

    def evaluate_trade(self, d, c):
        """Position-aware action, after market risk evaluation or stabilization.

        Cost never votes on market direction. All positive actions require a
        support setup, momentum, room, volume and risk evidence together.
        """
        from app.position_status import read_position_status
        holding = read_position_status(c.position_status) == PositionStatus.HOLDING
        d.position_status = str(read_position_status(c.position_status))
        d.decision = 'HOLD' if holding else 'WAIT'
        d.reasons, d.warnings = [], []
        d.follow_up = ['後續須支撐守穩、動能改善且上方空間足夠，再重新評估']
        broken = ReasonCode.SUPPORT_BREAK in d.risk_gate or ReasonCode.PREVIOUS_SUPPORT_BREAK in d.risk_gate
        accelerating = ReasonCode.BEARISH_MACD_ACCELERATION in d.risk_gate
        bearish = c.medium_term_direction < 0
        weak_volume = c.volume_deteriorating
        risk_evidence = ([('主要支撐已確認失守' if ReasonCode.SUPPORT_BREAK in d.risk_gate else '前支撐已確認失守')] if broken else [])
        risk_evidence += (['中期趨勢偏空'] if bearish else [])
        risk_evidence += (['MACD 空方動能加速'] if accelerating else [])
        risk_evidence += (['下跌伴隨放量，量價結構轉弱'] if weak_volume else [])
        if not broken and c.support_status == 'MINOR_BREAK':
            risk_evidence.append('主要支撐出現初步跌破，尚未確認失守')
        if not accelerating and c.macd_momentum in ('bearish_strengthening', 'bullish_weakening'):
            risk_evidence.append('MACD 動能轉弱')
        if c.institutional_level == 'STRONG_PRESSURE':
            risk_evidence.append('法人賣壓明顯')
        extreme = c.volatility_level in ('極高波動', 'EXTREME') or (c.atr_percent is not None and c.atr_percent >= self.config.trade_extreme_atr_pct)
        if extreme or c.volatility_level in ('高波動', 'HIGH'):
            d.warnings.append('波動偏高，新增部位須控制曝險')
        if holding and c.unrealized_return is not None and c.unrealized_return < 0:
            d.warnings.append('目前帳面虧損；成本不構成加碼或延後退出的理由')
        rz, sz = c.active_resistance_zone, c.active_support_zone
        upside = round((rz['low'] / c.current_price - 1) * 100, 8) if rz and c.current_price and c.current_price > 0 else None
        high = c.support_probability in ('高', '極高', 'HIGH', 'VERY_HIGH')
        very_high = c.support_probability in ('極高', 'VERY_HIGH')
        improving = c.macd_momentum in ('bearish_weakening', 'bullish_strengthening')
        near = bool(sz and c.distance_to_support is not None and c.distance_to_support <= self.config.near_zone_atr
                    and c.current_price is not None and c.current_price >= sz['low']
                    and c.support_status in ('TESTING', 'HOLDING', 'RECLAIMED', 'APPROACHING'))
        gates = [g for g in d.risk_gate if g != ReasonCode.EXTREME_VOLATILITY]
        severe_risk = d.holder_action in (HolderActionState.TIGHTEN_RISK, HolderActionState.REDUCE_EXPOSURE, HolderActionState.EXIT_CONDITION_APPROACHING)
        checks = {
            'valid': bool(c.data_valid and c.observation_complete and c.atr and c.atr > 0 and c.volatility_level != '資料不足' and d.state_basis != 'STALE_DATA'),
            'trend': c.medium_term_direction >= 0 and c.price_above_ma20 is not None and c.price_above_ma60 is not None
                and not (c.price_above_ma20 is False and c.price_above_ma60 is False),
            'support': near and high,
            'momentum': improving and c.price_above_ma5 is True,
            'room': upside is not None and upside > self.config.trade_min_upside_pct,
            'volume': c.volume_ratio is not None and c.volume_ratio > 0 and not weak_volume,
            'risk': not gates and not severe_risk and not c.overextended and not accelerating,
            'volatility': not extreme or (very_high and c.macd_momentum == 'bullish_strengthening' and c.kd_state == 'BULLISH' and c.price_above_ma20 is True),
        }
        add_checks = (c.medium_term_direction > 0 and c.short_term_direction >= 0
                      and c.price_above_ma20 is True and c.kd_state == 'BULLISH'
                      and upside is not None and upside > self.config.trade_add_upside_pct)
        eligible = all(checks.values()) and (not holding or add_checks)
        d.trade_evidence = dict(checks=checks, eligible=eligible, upside_percent=upside,
                               risk_evidence=risk_evidence, average_cost=c.average_cost,
                               unrealized_return=c.unrealized_return, version=1)
        if not checks['valid']:
            d.reasons = ['目前缺少有效收盤或必要風險資料', '暫不新增部位', '待有效資料補齊再評估市場結構']
            d.warnings.append('目前決策資料不足，不能解讀為風險已解除')
        elif holding and broken and bearish and (accelerating or weak_volume):
            d.decision, d.reasons = 'EXIT', risk_evidence + ['主要交易假設已失效']
        elif holding and (len(risk_evidence) >= 2 or (
                severe_risk and d.state_basis == 'RETAINED_PENDING_CONFIRMATION')):
            d.decision = 'REDUCE'
            d.reasons = risk_evidence + ['風險已提高，尚未達完整退出條件']
            if d.state_basis == 'RETAINED_PENDING_CONFIRMATION':
                d.reasons.append('原風險改善尚待連續新日線確認')
        elif eligible:
            d.decision = 'ADD' if holding else 'ENTER'
            d.reasons = ['目前回測有效支撐區', '支撐成功率評等：' + c.support_probability,
                         '中期結構與均線允許承接', 'MACD 動能改善', '距離上方壓力仍有合理空間']
            if holding:
                d.warnings.append('目前已有持倉，新增部位會提高曝險')
        elif holding:
            d.reasons = risk_evidence + ['目前沒有足夠加碼證據', '目前未達減碼或退出的交叉確認條件', '維持現有部位並觀察結構變化']
        else:
            missing = {'trend': '中期趨勢或均線結構偏弱', 'support': '尚未靠近高可信度有效支撐',
                       'momentum': '動能與短期均線尚未共同改善', 'room': '上方壓力空間不足或缺少有效壓力參考',
                       'volume': '量價惡化或量能資料不足', 'risk': '既有風險限制尚未解除',
                       'volatility': '極高波動所需的加強確認尚未成立'}
            d.reasons = risk_evidence + [v for k, v in missing.items() if not checks[k]]
        if sz:
            d.follow_up = [f"後續觀察 {sz['low']:g}～{sz['high']:g} 支撐；守穩或失守後須合併趨勢與動能重新評估"]
        return d

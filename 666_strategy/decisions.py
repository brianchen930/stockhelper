"""Pure position lifecycle, separate from raw crossings and any broker/backtest."""
from dataclasses import asdict, dataclass, replace
from datetime import datetime, time

from .daily import DailyTrendResult, session_close
from .history import SessionCalendar, TAIPEI, local_time
from .signals import SignalResult


@dataclass(frozen=True)
class TradeDecision:
    decision_id: str
    symbol: str
    action: str
    reason: str
    created_at: str
    execute_after: str | None
    signal_time: str | None = None
    trend_close_time: str | None = None


@dataclass(frozen=True)
class PositionState:
    symbol: str
    holding: bool = False
    pending: TradeDecision | None = None
    last_signal_time: str | None = None
    last_evaluated_at: str | None = None
    daily_checked_through: str | None = None

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        """Restore caller-owned state without loading a database or global state."""
        fields = dict(value)
        if fields.get('pending') is not None:
            fields['pending'] = TradeDecision(**fields['pending'])
        return cls(**fields)


@dataclass(frozen=True)
class DecisionResult:
    state: PositionState
    decision: TradeDecision | None
    trend: dict
    reason: str

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class ExecutionResult:
    state: PositionState
    executed: bool
    action: str | None
    reason: str


def executable_time(at, calendar):
    """First regular-session instant >= at; None means calendar not yet known."""
    at = local_time(at)
    if not calendar.start_date <= at.date() <= calendar.through_date:
        return None
    for day in calendar.sessions:
        opening = local_time(datetime.combine(day, time(9), TAIPEI))
        if at <= opening:
            return opening.isoformat()
        if day == at.date() and at < session_close(day):
            return at.isoformat()
    return None


def _decision(state, action, at, calendar, *, reason, signal=None, trend=None):
    trigger = trend['daily_close_time'] if action == 'EXIT' else signal['signal_time']
    return TradeDecision(f'{state.symbol}:{action}:{trigger}', state.symbol, action, reason,
        at.isoformat(), executable_time(local_time(trigger), calendar),
        signal_time=None if signal is None else signal['signal_time'],
        trend_close_time=None if trend is None else trend['daily_close_time'])


def _snapshots_since(daily, since, at):
    """Check every intervening completed session, including missing-data slots."""
    return [daily.snapshot_at(session_close(day)) for day in daily.calendar.sessions
            if (since is None or session_close(day) > local_time(since)) and session_close(day) <= at]


def evaluate_decision(state: PositionState, *, at, daily: DailyTrendResult,
                      calendar: SessionCalendar, signal=None) -> DecisionResult:
    """Evaluate an open/clock/bar-close event without assuming an execution.

    Call at each daily close and next session open even without a raw signal.
    A persisted pending EXIT survives UNKNOWN and subsequent trend recovery.
    BUY events are consumed once; blocked/cancelled ones never become latent buys.
    """
    at = local_time(at)
    if state.symbol != daily.symbol:
        raise ValueError('Position and daily trend symbols differ')
    if state.last_evaluated_at and at < local_time(state.last_evaluated_at):
        raise ValueError('Position events must be chronological')
    trend = daily.snapshot_at(at)
    changes = _snapshots_since(daily, state.daily_checked_through, at)
    # For a newly injected holding, use current trend; do not invent pre-entry exits.
    if state.daily_checked_through is None:
        changes = [trend]
    invalid = next((item for item in changes if item['status'] == 'NON_BULL'), None)
    pending = state.pending
    if pending and pending.symbol != state.symbol:
        raise ValueError('Pending decision belongs to a different symbol')
    reason = 'NO_NEW_SIGNAL'
    if state.holding:
        if pending and pending.action == 'BUY':
            raise ValueError('A holding cannot have a pending BUY')
        if invalid is not None and (pending is None or pending.action != 'EXIT'):
            pending = _decision(state, 'EXIT', at, calendar, reason='DAILY_TREND_INVALIDATED', trend=invalid)
        # An unknown future open can be resolved after extending the calendar.
        if pending and pending.execute_after is None:
            trigger = pending.trend_close_time or pending.signal_time
            pending = replace(pending, execute_after=executable_time(trigger, calendar))
    elif pending and pending.action != 'BUY':
        raise ValueError('A flat position cannot have a pending SELL/EXIT')
    elif pending:
        intervening = _snapshots_since(daily, pending.signal_time, at)
        if trend['status'] != 'BULL' or any(item['status'] != 'BULL' for item in intervening):
            pending, reason = None, 'BUY_CANCELLED_TREND_RECHECK'
        elif pending.execute_after is None:
            pending = replace(pending, execute_after=executable_time(pending.signal_time, calendar))
    last_signal = state.last_signal_time
    if signal is not None:
        if signal.get('symbol') != state.symbol or signal.get('signal_type') not in ('BUY', 'SELL'):
            raise ValueError('Expected an original BUY/SELL event for this symbol')
        signal_at = local_time(signal['signal_time'])
        if signal_at != at or local_time(signal['bar_end']) != signal_at:
            raise ValueError('New signals are accepted only at their confirmed bar_end')
        if last_signal is None or signal_at > local_time(last_signal):
            last_signal = signal_at.isoformat()
            if signal['signal_type'] == 'SELL':
                if state.holding and (pending is None or pending.action != 'EXIT'):
                    pending = _decision(state, 'SELL', at, calendar, reason='RAW_SELL', signal=signal)
                elif not state.holding:
                    pending, reason = None, 'SELL_WHILE_FLAT'
            elif not state.holding:
                if trend['status'] == 'BULL':
                    pending = _decision(state, 'BUY', at, calendar, reason='RAW_BUY_AND_DAILY_BULL',
                                        signal=signal, trend=trend)
                else:
                    pending, reason = None, 'BUY_BLOCKED_' + trend['status']
            else:
                reason = 'BUY_WHILE_HOLDING'
        else:
            reason = 'SIGNAL_ALREADY_CONSUMED'
    checked = state.daily_checked_through
    if trend['daily_close_time'] is not None:
        checked = trend['daily_close_time']
    updated = replace(state, pending=pending, last_signal_time=last_signal,
                      last_evaluated_at=at.isoformat(), daily_checked_through=checked)
    return DecisionResult(updated, pending, trend, pending.reason if pending else reason)


def confirm_execution(state: PositionState, *, decision_id, at, daily, calendar) -> ExecutionResult:
    """Recheck BEFORE accepting a position transition. No price, order IO or P&L.

    The caller retains returned state, including cancellations on rejection.
    A repeated execution using that state is rejected, preventing SELL/EXIT double
    execution. Durable storage/broker acknowledgement is a future integration.
    """
    result = evaluate_decision(state, at=at, daily=daily, calendar=calendar)
    pending, state = result.decision, result.state
    if pending is None or pending.decision_id != decision_id:
        return ExecutionResult(state, False, None, 'DECISION_NO_LONGER_PENDING')
    at = local_time(at)
    if pending.execute_after is None or at < local_time(pending.execute_after):
        return ExecutionResult(state, False, None, 'BEFORE_ELIGIBLE_EXECUTION_TIME')
    if executable_time(at, calendar) != at.isoformat():
        return ExecutionResult(state, False, None, 'OUTSIDE_REGULAR_SESSION')
    if pending.action == 'BUY' and result.trend['status'] != 'BULL':
        return ExecutionResult(replace(state, pending=None), False, None, 'BUY_TREND_RECHECK_FAILED')
    return ExecutionResult(replace(state, holding=pending.action == 'BUY', pending=None),
                           True, pending.action, 'POSITION_TRANSITION_CONFIRMED')


def evaluate_strategy(signals: SignalResult, daily: DailyTrendResult, state: PositionState,
                      *, at, calendar: SessionCalendar):
    """Adapt the second-stage output without changing its bars or raw events.

    Only a new event exactly at `at` is forwarded; an old latest_signal never
    becomes a fresh buy at the next opening or after trend recovery.
    """
    if signals.symbol != state.symbol:
        raise ValueError('Signals and position symbols differ')
    latest = signals.latest_signal
    signal = latest if latest and local_time(latest['signal_time']) == local_time(at) else None
    return evaluate_decision(state, at=at, daily=daily, calendar=calendar, signal=signal)

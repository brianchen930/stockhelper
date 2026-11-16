"""Isolated B experiment: one completed bar confirms original eligible BUY.

Accounting loop is intentionally isolated from the unchanged production runner.
SELL/EXIT reuse the existing decision functions. No live provider or broker IO.
"""
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal

from ..decisions import PositionState, confirm_execution, evaluate_decision
from ..signals import generate_signals
from ..backtest.config import BacktestConfig, decimal, money
from ..backtest.data import BacktestData
from ..backtest.metrics import buy_and_hold, holding_days, performance_metrics
from ..backtest.runner import BacktestResult


@dataclass
class ConfirmationResult(BacktestResult):
    candidates: list = field(default_factory=list)
    pending_candidate: dict | None = None


def run_confirmed_backtest(data: BacktestData, *, config=None):
    """Original eligible crossing at i, confirm i+1 Close, buy i+2 Open.

    A candidate never enters the original pending state before confirmation.
    No execution bar Close/High/Low is consulted at its Open. Failed candidates
    are consumed; only a fresh original crossing can start another candidate.
    """
    config = config or BacktestConfig()
    signals = generate_signals(data.history)
    raw_events = {item['signal_time']: item for item in signals.signals}
    state = PositionState(data.symbol)
    cash = money(config.initial_cash)
    fills, trades, events, in_range_signals = [], [], [], []
    equity_curve = []
    position = None
    candidate = None
    candidates, candidate_by_id = [], {}
    bars = signals.bars.loc[(signals.bars.index.date >= data.start) & (signals.bars.index.date <= data.end)]
    for bar_number, (stamp, row) in enumerate(bars.iterrows()):
        # Never inspect this bar's Close/High/Low to size or execute at its Open.
        pending_before = state.pending
        review = evaluate_decision(state, at=stamp, daily=data.daily, calendar=data.window.calendar)
        state = review.state
        if pending_before and state.pending is None:
            if pending_before.action == 'BUY':
                candidate_by_id[pending_before.decision_id].update(
                    status='CANCELLED_AT_EXECUTION', cancelled_at=stamp.isoformat(), cancel_reason=review.reason)
            events.append(dict(at=stamp.isoformat(), phase='OPEN', reason=review.reason,
                               decision_id=pending_before.decision_id))
        if state.pending is not None:
            pending = state.pending
            shares = config.affordable_shares(cash, row.Open) if pending.action == 'BUY' else position['shares']
            if pending.action == 'BUY' and shares == 0:
                candidate_by_id[pending.decision_id].update(status='INSUFFICIENT_CASH', cancelled_at=stamp.isoformat())
                events.append(dict(at=stamp.isoformat(), phase='OPEN', reason='INSUFFICIENT_CASH',
                                   decision_id=pending.decision_id, cash=float(cash), price=float(row.Open)))
                state = replace(state, pending=None)
            else:
                transition = confirm_execution(state, decision_id=pending.decision_id,
                    at=stamp, daily=data.daily, calendar=data.window.calendar)
                state = transition.state
                if transition.executed:
                    selling = transition.action != 'BUY'
                    gross, fee, tax = config.amounts(row.Open, shares, sell=selling)
                    cash += gross-fee-tax if selling else -(gross+fee)
                    trade_id = len(trades)+1
                    fill = dict(trade_id=trade_id, symbol=data.symbol, action=transition.action,
                        signal_time=pending.signal_time, trend_close_time=pending.trend_close_time,
                        decision_id=pending.decision_id, execution_time=stamp.isoformat(),
                        confirmation_time=pending.created_at,
                        execution_bar_start=stamp.isoformat(), price=float(row.Open), shares=shares,
                        gross_amount=float(gross), commission=float(fee), transaction_tax=float(tax),
                        cash_after=float(cash), net_profit=None, return_percent=None)
                    if not selling:
                        candidate_by_id[pending.decision_id].update(status='FILLED',
                            execution_time=stamp.isoformat(), execution_price=float(row.Open), shares=shares)
                        position = dict(shares=shares, entry_fill=fill, entry_cost=gross+fee)
                    else:
                        entry = position['entry_fill']
                        profit = money(gross-fee-tax-position['entry_cost'])
                        percent = float(profit / position['entry_cost'] * 100)
                        fill.update(net_profit=float(profit), return_percent=percent)
                        trades.append(dict(trade_id=trade_id, symbol=data.symbol,
                            entry_signal_time=entry['signal_time'], entry_time=entry['execution_time'],
                            entry_confirmation_time=entry['confirmation_time'],
                            exit_signal_time=pending.signal_time, trend_close_time=pending.trend_close_time,
                            exit_time=stamp.isoformat(), exit_action=transition.action,
                            entry_price=entry['price'], exit_price=fill['price'], shares=shares,
                            entry_commission=entry['commission'], exit_commission=float(fee),
                            transaction_tax=float(tax), entry_cost=float(position['entry_cost']),
                            exit_net_proceeds=float(gross-fee-tax), net_profit=float(profit), return_percent=percent,
                            holding_days=holding_days(entry['execution_time'], stamp)))
                        position = None
                    fills.append(fill)
                    events.append(dict(at=stamp.isoformat(), phase='OPEN', reason='FILLED_'+transition.action,
                                       decision_id=pending.decision_id, price=float(row.Open)))
        equity_curve.append(dict(at=stamp.isoformat(), phase='OPEN', cash=float(cash),
            shares=0 if position is None else position['shares'], mark_price=float(row.Open),
            equity=float(cash+(money(decimal(row.Open)*position['shares']) if position else Decimal('0.00')))))
        at = row.bar_end
        event = raw_events.get(at.isoformat())
        if event:
            in_range_signals.append(event)
        previous = state.pending
        review = evaluate_decision(state, at=at, daily=data.daily, calendar=data.window.calendar, signal=event)
        state = review.state
        reason = review.reason
        if candidate is not None:
            origin, record = candidate
            assert bar_number == record['signal_bar_number']+1 and not state.holding
            record.update(confirmation_bar_number=bar_number, confirmation_bar_start=stamp.isoformat(),
                confirmation_time=at.isoformat(), confirmation_close=float(row.Close),
                confirmation_ma60=float(row.MA60), confirmation_daily_status=review.trend['status'],
                confirmation_daily_close_time=review.trend['daily_close_time'])
            # Strict >: equality fails. Evaluate only this just-completed bar.
            if not row.Close > row.MA60:
                reason = 'CANCELLED_CLOSE_NOT_ABOVE_MA60'
            elif review.trend['status'] != 'BULL':
                reason = 'CANCELLED_CONFIRMATION_TREND'
            else:
                confirmed = replace(origin, created_at=at.isoformat(), execute_after=at.isoformat(),
                    reason='BUY_ONE_BAR_CONFIRMED', trend_close_time=review.trend['daily_close_time'])
                # Preserve the original intervening-daily-trend cancellation policy.
                recheck = evaluate_decision(replace(state, pending=confirmed), at=at,
                    daily=data.daily, calendar=data.window.calendar)
                state = recheck.state
                reason = 'CONFIRMED' if state.pending is not None else 'CANCELLED_INTERVENING_TREND'
            record['status'] = reason
            if reason != 'CONFIRMED':
                record.update(cancelled_at=at.isoformat(), cancel_reason=reason)
            candidate = None
        elif state.pending is not None and state.pending.action == 'BUY':
            origin = state.pending
            assert origin.created_at == at.isoformat()
            record = dict(decision_id=origin.decision_id, signal_time=origin.signal_time,
                signal_bar_number=bar_number, signal_bar_start=stamp.isoformat(), signal_close=float(row.Close),
                signal_ma60=float(row.MA60), signal_daily_status=review.trend['status'],
                signal_daily_close_time=review.trend['daily_close_time'], status='WAITING')
            candidates.append(record)
            candidate_by_id[origin.decision_id] = record
            candidate = origin, record
            state = replace(state, pending=None)
            reason = 'WAIT_ONE_COMPLETED_BAR'
        # Include every close, including daily invalidations without a crossing.
        events.append(dict(at=at.isoformat(), phase='CLOSE', bar_start=stamp.isoformat(),
            raw_signal=None if event is None else event['signal_type'], reason=reason,
            trend=review.trend, holding=state.holding,
            pending_decision=None if state.pending is None else asdict(state.pending),
            candidate_signal_time=None if candidate is None else candidate[0].signal_time,
            pending_changed=previous != state.pending))
        equity_curve.append(dict(at=at.isoformat(), phase='CLOSE', cash=float(cash),
            shares=0 if position is None else position['shares'], mark_price=float(row.Close),
            equity=float(cash+(money(decimal(row.Close)*position['shares']) if position else Decimal('0.00')))))
    last = bars.iloc[-1]
    market_value = Decimal('0.00')
    open_position = None
    if position:
        market_value = money(decimal(last.Close) * position['shares'])
        unrealized = money(market_value-position['entry_cost'])
        open_position = dict(symbol=data.symbol, shares=position['shares'],
            entry_time=position['entry_fill']['execution_time'], entry_price=position['entry_fill']['price'],
            entry_commission=position['entry_fill']['commission'], entry_cost=float(position['entry_cost']),
            mark_time=last.bar_end.isoformat(), mark_price=float(last.Close), market_value=float(market_value),
            unrealized_net_profit=float(unrealized),
            unrealized_return_percent=float(unrealized/position['entry_cost']*100),
            holding_days=holding_days(position['entry_fill']['execution_time'], last.bar_end),
            note='Marked at final 60m Close, entry fee included; no hypothetical exit fee/tax or forced sale')
    ending_equity = cash+market_value
    realized = sum((decimal(trade['net_profit']) for trade in trades), Decimal('0.00'))
    total = money(ending_equity-money(config.initial_cash))
    summary = dict(initial_cash=float(money(config.initial_cash)), ending_cash=float(cash),
        ending_equity=float(ending_equity), realized_net_profit=float(realized),
        unrealized_net_profit=0. if open_position is None else open_position['unrealized_net_profit'],
        total_net_profit=float(total), total_return_percent=float(total/money(config.initial_cash)*100),
        completed_trades=len(trades), winning_trades=sum(t['net_profit'] > 0 for t in trades),
        win_rate_percent=None if not trades else sum(t['net_profit'] > 0 for t in trades)/len(trades)*100,
        commissions=float(sum((decimal(fill['commission']) for fill in fills), Decimal('0.00'))),
        transaction_taxes=float(sum((decimal(fill['transaction_tax']) for fill in fills), Decimal('0.00'))),
        insufficient_cash_buys=sum(event['reason'] == 'INSUFFICIENT_CASH' for event in events),
        period_end_liquidated=False)
    benchmark = buy_and_hold(bars, config)
    summary.update(performance_metrics(equity_curve, trades, config.initial_cash),
                   buy_and_hold_return_percent=benchmark['return_percent'],
                   buy_and_hold_price_return_percent=benchmark['price_return_percent'])
    metadata = dict(data.metadata, fill_rule='next canonical 60m bar Open after signal confirmation',
        same_timestamp_order='previous bar close -> next bar open', currency='TWD',
        sizing='all available cash, whole share_unit quantities, no leverage',
        cost_basis='configurable simulation defaults; original app only replays decisions',
        slippage=0, dividends_included=False, strategy_imports='existing generate_signals/evaluate_decision/confirm_execution',
        drawdown_basis='Initial capital, then each 60m Open after fills and completed Close; no intrabar High/Low path',
        holding_days_basis='Elapsed calendar days for closed trades; open duration listed separately',
        experiment='B: exactly one next completed native bar Close > its MA60; daily BULL; following Open',
        original_buy_daily_filter_preserved=True, optimized_parameters=False)
    return ConfirmationResult(data.symbol, data.start.isoformat(), data.end.isoformat(), config.to_dict(),
        summary, fills, trades, open_position, None if state.pending is None else asdict(state.pending),
        events, in_range_signals, metadata, equity_curve, benchmark,
        candidates=candidates, pending_candidate=None if candidate is None else asdict(candidate[0]))

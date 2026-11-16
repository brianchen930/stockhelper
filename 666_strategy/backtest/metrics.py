"""Descriptive performance only; no signal, trend or execution rule changes."""
import pandas as pd

from .config import decimal, money


def holding_days(entry_time, exit_time):
    """Elapsed calendar days (including nights/holidays), not number of bars."""
    return (pd.Timestamp(exit_time)-pd.Timestamp(entry_time)).total_seconds()/86400


def performance_metrics(equity_curve, trades, initial_cash):
    """Positive peak-to-trough loss percentage, using observed Open/Close marks.

    Initial capital is the first high-water mark. Never infer High/Low path or
    force liquidations just to calculate drawdown. Closed trades only in mean.
    """
    peak = decimal(initial_cash)
    peak_at = None
    largest = decimal(0)
    peak_when, trough_when = None, None
    for point in equity_curve:
        equity = decimal(point['equity'])
        if equity > peak:
            peak, peak_at = equity, point['at']
        drawdown = (peak-equity)/peak*100
        if drawdown > largest:
            largest, peak_when, trough_when = drawdown, peak_at, point['at']
    durations = [holding_days(t['entry_time'], t['exit_time']) for t in trades]
    return dict(max_drawdown_percent=float(largest), drawdown_peak_at=peak_when,
        drawdown_trough_at=trough_when,
        average_holding_days=None if not durations else sum(durations)/len(durations))


def buy_and_hold(bars, config):
    """Same capital/share units/entry fee, first run Open to final Close mark.

    No hypothetical final sale, exit fee or tax, matching open strategy holdings.
    Gross price return is also supplied so the convention is unambiguous.
    """
    first, last = bars.iloc[0], bars.iloc[-1]
    initial = money(config.initial_cash)
    shares = config.affordable_shares(initial, first.Open)
    gross, fee, _ = config.amounts(first.Open, shares) if shares else (money(0), money(0), money(0))
    cash = initial-gross-fee
    value = money(decimal(last.Close)*shares)
    equity = cash+value
    return dict(shares=shares, entry_time=None if not shares else bars.index[0].isoformat(),
        entry_price=float(first.Open), entry_commission=float(fee),
        mark_time=last.bar_end.isoformat(), mark_price=float(last.Close),
        ending_cash=float(cash), market_value=float(value), ending_equity=float(equity),
        net_profit=float(equity-initial), return_percent=float((equity-initial)/initial*100),
        price_return_percent=float((decimal(last.Close)/decimal(first.Open)-1)*100),
        end_liquidated=False, note='First in-range 60m Open to final 60m Close; entry fee only, no dividends or hypothetical exit')

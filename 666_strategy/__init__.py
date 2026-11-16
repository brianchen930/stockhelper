"""Independent 60m crossings, stock daily trend and pure position decisions.

The numeric package name is loaded with importlib.import_module('666_strategy')
or executed with python -m 666_strategy.
"""
from .history import HistoryResult, SessionCalendar, analyze_history
from .provider import YFinanceProvider, fetch_history
from .fugle_provider import FugleProvider, FugleSnapshotProvider
from .signals import SignalResult, generate_signals
from .daily import DailyTrendResult, analyze_daily_trend, fetch_daily_trend
from .decisions import (PositionState, TradeDecision, DecisionResult, ExecutionResult,
                        evaluate_decision, evaluate_strategy, confirm_execution)

__all__ = ['HistoryResult', 'SessionCalendar', 'YFinanceProvider', 'FugleProvider', 'FugleSnapshotProvider', 'analyze_history',
           'fetch_history', 'SignalResult', 'generate_signals', 'DailyTrendResult',
           'analyze_daily_trend', 'fetch_daily_trend', 'PositionState', 'TradeDecision',
           'DecisionResult', 'ExecutionResult', 'evaluate_decision', 'evaluate_strategy',
           'confirm_execution']

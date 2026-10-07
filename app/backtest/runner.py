"""Daily decision replay using the production analysis, rules and state machine.

No fills, position transitions, sizing or returns are simulated. Each run starts
fresh; price warm-up precedes start, decision memory starts on the first output day.
"""
from contextlib import closing
from datetime import date, datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo
import csv
import json
import sqlite3

import pandas as pd
import yfinance as yf

from app.data_quality import assess_analysis_quality
from app.decision_state import create_table, update_monitor_decision
from app.institutional_flow.service import InstitutionalFlowService
from app.institutional_flow.storage import ReadOnlyFlowStore
from app.market_data import normalize_history
from app.position_status import PositionStatus
from app.rules import evaluate_notification
from app.stock import analyze_stock_history
from app.support_resistance_analysis.lifecycle_storage import LifecycleStore

TAIPEI = ZoneInfo('Asia/Taipei')
ACTION_LABELS = {'OBSERVE': '觀察', 'WAIT': '觀察', 'ENTER': '建倉', 'ADD': '加碼',
                 'HOLD': '持有', 'CONSIDER_REDUCE': '考慮減碼', 'REDUCE': '減碼', 'EXIT': '出清'}


def daily_frame(frame):
    """Align daily bars by Taipei session date; do not fill missing sessions."""
    if frame is None or frame.empty:
        return pd.DataFrame(index=pd.DatetimeIndex([]))
    frame = frame.copy()
    index = pd.DatetimeIndex(pd.to_datetime(frame.index))
    if index.tz is not None:
        index = index.tz_convert(TAIPEI).tz_localize(None)
    frame.index = index.normalize()
    if frame.index.isna().any():
        raise ValueError('歷史資料包含無效日期')
    return frame.sort_index().loc[lambda df: ~df.index.duplicated(keep='last')]


def download_history(symbol, start, end):
    """Resolve TW/TWO against the requested historical interval, never today."""
    symbol = str(symbol).strip().upper()
    if not symbol:
        raise ValueError('股票代號不可為空')
    candidates = [symbol] if symbol.startswith('^') or symbol.endswith(('.TW', '.TWO')) else [symbol + '.TW', symbol + '.TWO']
    for candidate in candidates:
        frame = yf.Ticker(candidate).history(start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(), interval='1d', auto_adjust=False)
        if frame is not None and not frame.empty:
            return candidate, frame
    raise ValueError(f'{symbol} 在指定期間沒有可用歷史資料')


def benchmark_change(frame, day):
    prefix = frame.loc[frame.index <= day]
    if prefix.empty or prefix.index[-1] != day:
        return None  # Never substitute a different session's market return.
    data, _ = normalize_history(prefix)
    if len(data) < 2 or data.index[-1] != day or data.Close.iloc[-2] <= 0:
        return None
    return round((float(data.Close.iloc[-1]) / float(data.Close.iloc[-2]) - 1) * 100, 2)


def load_state(connection, symbol):
    cursor = connection.execute('SELECT * FROM decision_state WHERE symbol=?', (symbol,))
    row = cursor.fetchone()
    return dict(zip([column[0] for column in cursor.description], row)) if row else {}


def action_change(previous, current):
    """Describe recorded decision differences without adding trading rules."""
    if previous is None:
        return False, '首個回放交易日，建立決策基準'
    if previous['decision'] == current['decision']:
        return False, '動作未改變'
    old = {v['code']: v for v in previous.get('trade_evidence', {}).get('contributions', [])}
    new = {v['code']: v for v in current.get('trade_evidence', {}).get('contributions', [])}
    changes = []
    for code in sorted(old.keys() | new.keys()):
        a, b = old.get(code), new.get(code)
        if a != b:
            changes.append(f"{code}：{a['points'] if a else 0:g} → {b['points'] if b else 0:g}；"
                           + (b['text'] if b else '原有依據解除：' + a['text']))
    for key in ('action_constraints', 'recovery'):
        before = previous.get('trade_evidence', {}).get(key)
        after = current.get('trade_evidence', {}).get(key)
        if before != after:
            changes.append(f'{key}：{json.dumps(before, ensure_ascii=False)} → {json.dumps(after, ensure_ascii=False)}')
    # Engine reasons are evidence, not a counterfactual claim of causality.
    changes.extend(current.get('reasons', []))
    return True, (f"{ACTION_LABELS[previous['decision']]} → {ACTION_LABELS[current['decision']]}："
                  + '；'.join(dict.fromkeys(changes or ['現有差異不足以可靠歸因，請檢查決策快照'])))


class BacktestRunner:
    def __init__(self, *, history_loader=download_history, institutional_db=None):
        self.history_loader = history_loader
        if institutional_db is None:
            from app.database import DATABASE_PATH
            institutional_db = DATABASE_PATH
        self.institutional_db = Path(institutional_db)
        self.states = {}

    def run(self, symbol, start, end, *, history=None, benchmark_history=None,
            position_status='WATCHING'):
        start, end = date.fromisoformat(str(start)), date.fromisoformat(str(end))
        if start > end:
            raise ValueError('開始日期不可晚於結束日期')
        now = datetime.now(TAIPEI)
        if end > now.date() or (end == now.date() and now.time() < time(13, 30)):
            raise ValueError('結束日期必須是已收盤的歷史日期')
        status = PositionStatus(position_status).value
        symbol = str(symbol).strip().upper()
        if not symbol:
            raise ValueError('股票代號不可為空')
        # Match the live default six-month rolling input, including EMA warm-up.
        warmup = (pd.Timestamp(start) - pd.DateOffset(months=6)).date()
        resolved = symbol if symbol.endswith(('.TW', '.TWO')) else symbol + '.TW'
        if history is None:
            resolved, history = self.history_loader(symbol, warmup, end)
        stock = daily_frame(history)
        if benchmark_history is None:
            try:
                _, benchmark_history = self.history_loader('^TWII', warmup, end)
            except Exception:
                benchmark_history = pd.DataFrame()
        benchmark = daily_frame(benchmark_history)
        days = stock.index[(stock.index.date >= start) & (stock.index.date <= end)]
        if not len(days):
            raise ValueError('指定期間沒有可回放的交易日')
        code = resolved.split('.')[0]
        self.states[code] = {}
        rows, previous_decision, previous_rules = [], None, {}
        service = InstitutionalFlowService(store=ReadOnlyFlowStore(self.institutional_db))
        # LifecycleStore opens/closes connections itself, so give it a private
        # temporary file. Decision memory lives in a separate in-memory database.
        with TemporaryDirectory(prefix='decision-backtest-') as directory, closing(sqlite3.connect(':memory:')) as connection:
            lifecycle_path = Path(directory) / 'lifecycle.db'
            lifecycle = LifecycleStore(lambda: sqlite3.connect(lifecycle_path))
            create_table(connection)
            connection.commit()
            for day in days:
                cutoff = datetime.combine(day.date(), time(13, 30), TAIPEI)
                begin = day - pd.DateOffset(months=6)
                prefix = stock.loc[(stock.index >= begin) & (stock.index <= day)].copy()
                # Reject an unreadable bar instead of silently replaying yesterday.
                normalized, _ = normalize_history(prefix)
                if normalized.empty or normalized.index[-1] != day or normalized.Close.iloc[-1] <= 0:
                    raise ValueError(f'{day.date()} 收盤價無效，無法回放')
                previous_state = load_state(connection, code)
                data = analyze_stock_history(code, prefix, resolved_symbol=resolved,
                    position_status=status, replay=True, now=cutoff,
                    benchmark_change_percent=benchmark_change(benchmark, day),
                    previous_state=previous_state, lifecycle_store=lifecycle,
                    institutional_service=service)
                quality = assess_analysis_quality(data)
                if quality['is_valid'] and not quality['issues'] and data.get('decision_context'):
                    update_monitor_decision(data, connection=connection,
                                            signal_observation_time=cutoff.isoformat())
                decision = data['trading_decision']
                signal = decision['final_action_state'] if quality['is_valid'] else '無法判斷'
                strategy = data.get('analysis') or {}
                fields = ('rsi', 'macd', 'macd_signal', 'macd_histogram', 'previous_macd',
                          'previous_macd_signal', 'previous_macd_histogram', 'kd_k', 'kd_d',
                          'kd_j', 'previous_kd_k', 'previous_kd_d', 'previous_kd_j',
                          'macd_analysis', 'support_resistance')
                rules = evaluate_notification(current_signal=signal, final_action_state=signal,
                    current_trend=strategy.get('trend'), previous_signal=previous_rules.get('signal'),
                    previous_trend=previous_rules.get('trend'), reasons=strategy.get('reasons', []),
                    **{key: data.get(key) for key in fields}, analysis_is_valid=quality['is_valid'],
                    data_quality_issues=quality['issues'], now=cutoff,
                    support_resistance_bar_closed=True,
                    support_resistance_state=previous_rules.get('support_resistance_state'))
                if quality['is_valid']:
                    previous_rules = dict(signal=signal, trend=strategy.get('trend'),
                        support_resistance_state=rules.get('support_resistance_state'))
                state = load_state(connection, code)
                self.states[code] = state
                context = data['decision_context']
                changed, change_reason = action_change(previous_decision, decision)
                sr = data.get('support_resistance') or {}
                tf = data.get('timeframe_analysis') or {}
                rows.append(dict(date=day.date().isoformat(), close=data.get('close'), signal=signal,
                    action=ACTION_LABELS[decision['decision']], action_code=decision['decision'],
                    position_status=status, short_trend=(tf.get('short_term') or {}).get('label'),
                    mid_trend=(tf.get('medium_term') or {}).get('label'), rsi=data.get('rsi'),
                    macd_status=data.get('macd_analysis'), kd_status=context.get('kd_state'),
                    kd_k=data.get('kd_k'), kd_d=data.get('kd_d'), kd_j=data.get('kd_j'),
                    support_status=context.get('support_status'), resistance_status=context.get('resistance_status'),
                    structural_support_status=context.get('structural_support_status'),
                    relative_market=data.get('market_relative_performance'),
                    institutional_flow=sr.get('institutional_context'),
                    matched_rules=rules.get('matched_rules', []),
                    decision_rule_codes=[v['code'] for v in decision.get('trade_evidence', {}).get('contributions', [])],
                    reason='；'.join(decision.get('reasons', [])),
                    action_changed=changed, action_change_reason=change_reason,
                    entry_action=decision.get('entry_action'), holder_action=decision.get('holder_action'),
                    previous_action_state=decision.get('previous_action_state'),
                    confirmation_count=decision.get('confirmation_count'),
                    signal_confirmation_count=decision.get('signal_confirmation_count'),
                    signal_hysteresis=decision.get('signal_hysteresis'),
                    signal_persistence=decision.get('signal_persistence'),
                    state_before=previous_state, state_after=state,
                    decision_snapshot=decision, data_quality=quality,
                    bayesian_status=sr.get('bayesian_support_status'),
                    as_of=cutoff.isoformat(), symbol=code))
                previous_decision = decision
        # Normalize tuples/enums from existing dataclass snapshots for callers
        # and identical CSV/JSON representations; reject non-finite JSON here.
        return json.loads(json.dumps(rows, ensure_ascii=False, allow_nan=False))


def export_rows(rows, path):
    path = Path(path)
    if path.suffix.lower() not in ('.csv', '.json'):
        raise ValueError('輸出檔案必須是 .csv 或 .json')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == '.json':
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    else:
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ['date', 'close', 'signal', 'action', 'short_trend', 'mid_trend', 'reason'])
            writer.writeheader()
            for index, row in enumerate(rows):
                # Filter only CSV output; replay still processes every trading day.
                if index > 0 and row['action_code'] == rows[index - 1]['action_code']:
                    continue
                writer.writerow({key: json.dumps(value, ensure_ascii=False, allow_nan=False)
                    if isinstance(value, (dict, list)) else value for key, value in row.items()})
    return path

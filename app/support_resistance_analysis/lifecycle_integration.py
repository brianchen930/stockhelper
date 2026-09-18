"""Attach canonical roles after analysis without modifying raw notification inputs."""
from datetime import datetime, time
from zoneinfo import ZoneInfo
import hashlib
import json
import logging
import pandas as pd
from app.market_data import is_finite_number
from .lifecycle import advance_lifecycle, lifecycle_view, compatible
from .lifecycle_storage import LifecycleStore, create_tables

logger = logging.getLogger(__name__)


def attach_zone_lifecycle(result, data, previous_zones=None, *, store=None, now=None):
    sr = result.get('support_resistance') or {}
    if sr.get('error') or data is None or len(data) < 2:
        return
    now = now or datetime.now(ZoneInfo('Asia/Taipei'))
    timestamp = pd.Timestamp(data.index[-1])
    # Use comparable, timezone-aware closing instants for chronology and outcomes.
    stamp = datetime.combine(timestamp.date(), time(13, 30), ZoneInfo('Asia/Taipei')).isoformat()
    complete = now >= datetime.fromisoformat(stamp)
    close, high, low = (float(data.iloc[-1][k]) for k in ('Close', 'High', 'Low'))
    atr = result.get('atr', sr.get('atr'))
    if not all(is_finite_number(v) for v in (close, high, low)) or close <= 0:
        return
    ratio = None
    if 'Volume' in data and len(data) >= 20:
        past, current = data.Volume.iloc[-20:-1], data.Volume.iloc[-1]
        if past.map(is_finite_number).all() and past.mean() > 0 and is_finite_number(current):
            ratio = float(current / past.mean())
    symbol = str(result.get('stock_code', ''))
    storage = store or LifecycleStore()
    connection = storage.connect()
    try:
        create_tables(connection)
        connection.commit()
        connection.execute('BEGIN IMMEDIATE')
        previous = storage.load(connection, symbol, '1d')
        newest = max((r['last_observation_at'] or '' for r in previous), default='')
        replay = bool(newest and stamp < newest)
        if replay:
            previous = []  # Never apply future live state to a historical request.
        if not previous and previous_zones:
            old_stamp = datetime.combine(pd.Timestamp(data.index[-2]).date(), time(13, 30), ZoneInfo('Asia/Taipei')).isoformat()
            previous = advance_lifecycle(previous_zones, [], symbol=symbol, observation_time=old_stamp,
                close=float(data.Close.iloc[-2]), high=float(data.High.iloc[-2]), low=float(data.Low.iloc[-2]),
                atr=previous_zones.get('atr', atr), completed=True)
        records = advance_lifecycle(sr, previous, symbol=symbol, observation_time=stamp,
            close=close, high=high, low=low, atr=atr, volume_ratio=ratio, completed=complete)
        # Intraday views use established roles; they cannot confirm a break/flip.
        view = lifecycle_view(records, close, stamp)
        candidates = sr.get('bayesian_support', []) or []
        selected = sr.get('bayesian_support_selected')
        if selected and not any(v is selected for v in candidates):
            candidates = [*candidates, selected]
        for candidate in candidates:
            model_zone = dict(low=candidate['support_low'], high=candidate['support_high'])
            matches = [r for r in records if compatible(model_zone, dict(low=r['anchor_low'], high=r['anchor_high']), atr)]
            record = min(matches, key=lambda r: (abs(r['zone_low'] - model_zone['low']), r['stable_zone_id']), default=None)
            active_ids = {z['stable_zone_id'] for z in view['support_zones']}
            active = bool(record and record['stable_zone_id'] in active_ids and record['current_role'] == 'SUPPORT')
            candidate['zone_lifecycle'] = dict(stable_zone_id=record['stable_zone_id'] if record else None,
                status=record['status'] if record else 'UNLINKED_HISTORICAL', active_support=active,
                break_confirmed_at=record['break_confirmed_at'] if record else None)
            if complete and not replay and active and (candidate.get('result') or {}).get('model_status') == 'ready':
                observed = candidate.get('prediction_time') or now.isoformat()
                model_ref = candidate.get('model_reference', 'unknown')
                key = [record['stable_zone_id'], stamp, model_ref]
                connection.execute('''INSERT OR IGNORE INTO zone_bayesian_events
                    (prediction_id,stable_zone_id,prediction_time,observation_time,support_probability,predicted_level,model_reference)
                    VALUES (?,?,?,?,?,?,?)''', (hashlib.sha256(json.dumps(key).encode()).hexdigest(), record['stable_zone_id'],
                    observed, stamp, (candidate.get('result') or {}).get('posterior_success_probability'),
                    (candidate.get('display') or {}).get('rating'), model_ref))
        if complete and not replay:
            storage.save(connection, records)
            storage.resolve_predictions(connection, records)
        events = storage.events(connection, symbol, '1d') if not replay else []
        view.update(records=records, bayesian_events=events, observation_time=stamp, persisted=complete and not replay)
        sr['zone_lifecycle'] = view
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

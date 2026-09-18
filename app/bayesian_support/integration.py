"""Display-only inference from cached JSON. No fit, download or historical loop."""
from functools import lru_cache
from pathlib import Path
from datetime import datetime, time
from zoneinfo import ZoneInfo
import json
import pandas as pd

from .features import finite
from .model import BayesianSupportModel
from .rating import rate_support_probability, validate_thresholds
from .presentation import append_probability_output

MODEL_DIRECTORY = Path(__file__).resolve().parents[2] / 'data' / 'models' / 'bayesian_support'


@lru_cache(maxsize=16)
def _load_model(path, modified_ns, size):
    return BayesianSupportModel.load(path)


@lru_cache(maxsize=16)
def _load_rating(path, modified_ns, size):
    reference = json.loads(Path(path).read_text(encoding='utf-8'))
    if reference.get('version') != 'support_rating_v1':
        raise ValueError('Unknown rating reference version')
    validate_thresholds(reference['thresholds'])
    if reference.get('rating_strategy') not in ('distribution', 'fixed', 'fixed_fallback'):
        raise ValueError('Invalid rating strategy')
    if reference['rating_strategy'] == 'distribution' and reference['distribution']['count'] < reference['minimum_samples']:
        raise ValueError('Insufficient distribution samples')
    return reference


def attach_bayesian_support(result, data, *, research_mode=False):
    """Attach estimates only for actual daily support tests, matching training.

    Touch history is not available in the live pipeline: skip it, never replace
    it with the legacy detector count. Reconstruct just the previous day's
    known zones (one detection, NOT a walk-forward) to match the training cutoff.
    """
    symbol = str(result.get('stock_code', ''))
    if not symbol.isalnum():
        return
    path = MODEL_DIRECTORY / (symbol + '.json')
    if not path.is_file() or data is None or len(data) < 2:
        return
    now = datetime.now(ZoneInfo('Asia/Taipei'))
    if data.index[-1].date() >= now.date() and now.time() < time(13, 30):
        return  # The training reference is a completed daily Close.
    sr = result.get('support_resistance')
    if not isinstance(sr, dict) or sr.get('error'):
        return
    try:
        stat = path.stat()
        model = _load_model(str(path), stat.st_mtime_ns, stat.st_size)
        if symbol not in model.metadata['symbols']:
            raise ValueError('Model symbol scope mismatch')
        from app.support_resistance_analysis import SupportResistanceEngine
        previous = SupportResistanceEngine().detect(data.iloc[:-1])
        if previous.get('error'):
            return
        definition = model.metadata.get('event_definition') or {}
        event_config = definition.get('event_config') or {}
        evidence_config = event_config.get('evidence') or {}
        if not event_config or not evidence_config:
            raise ValueError('Model lacks event/volume definitions')
        row = data.iloc[-1]
        atr = finite(result.get('atr'))
        low, high, close = map(finite, (row.Low, row.High, row.Close))
        if atr is None or atr <= 0 or None in (low, high, close) or not 0 < low <= close <= high:
            return
        period = evidence_config['volume_ma_period']
        volume = finite(row.get('Volume'))
        past = data.Volume.iloc[max(0, len(data) - 1 - period):-1].map(finite)
        baseline = float(past.mean()) if len(past) == period and past.notna().all() and past.ge(0).all() else None
        ratio = volume / baseline if volume is not None and volume >= 0 and baseline is not None and baseline > 0 else None
        level = None
        if ratio is not None:
            level = 'spike'
            for setting, label in (('volume_ratio_low', 'low'), ('volume_ratio_high', 'normal'), ('volume_ratio_spike', 'elevated')):
                if ratio < evidence_config[setting]:
                    level = label
                    break
        predictions = []
        for zone in previous.get('support_zones', []):
            zl, zh = zone['low'], zone['high']
            buffer = event_config['touch_atr_threshold'] * atr
            if low > zh + buffer or high < zl - buffer:
                continue
            event = dict(event_date=data.index[-1].isoformat(), touch_volume_level=level,
                         volatility_level=result.get('volatility_level'),
                         support_source_count=len(set(zone.get('methods') or [])),
                         distance_to_support_atr=max(zl - close, close - zh, 0) / atr,
                         zone_width_atr=(zh - zl) / atr)
            prediction = model.predict(event).to_dict()
            predictions.append(dict(support_low=zl, support_high=zh, result=prediction,
                prediction_time=now.isoformat(), model_reference=f'{path.name}:{stat.st_mtime_ns}:{stat.st_size}'))
        sr['bayesian_support'] = predictions
        if predictions:
            reference = None
            rating_path = MODEL_DIRECTORY / (symbol + '.rating.json')
            try:
                if rating_path.is_file():
                    stat = rating_path.stat()
                    saved = _load_rating(str(rating_path), stat.st_mtime_ns, stat.st_size)
                    # Historical display must not use a future distribution.
                    if symbol in saved['symbols'] and pd.Timestamp(saved['reference_end']) < pd.to_datetime(data.index[-1], utc=True):
                        reference = saved
            except (ValueError, TypeError, KeyError, OSError):
                pass  # Valid posterior remains available with fixed fallback.
            for candidate in predictions:
                candidate['display'] = rate_support_probability(candidate['result'], reference).to_dict()
            nearest = min(predictions, key=lambda p: max(p['support_low'] - close, close - p['support_high'], 0))
            sr['bayesian_support_selected'] = nearest
            result['support_resistance_text'] = append_probability_output(result['support_resistance_text'], sr, research_mode=research_mode)
    except (ValueError, TypeError, KeyError, OSError) as error:
        # Optional inference cannot discard normal indicators or trigger trades.
        sr['bayesian_support_status'] = 'unavailable'
        sr['bayesian_support_error'] = str(error)
        result['support_resistance_text'] += '\n支撐成功機率：資料不足'

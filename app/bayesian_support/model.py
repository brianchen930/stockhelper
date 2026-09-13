"""Auditable categorical Naive Bayes with log-odds evidence contributions."""
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

import pandas as pd

from .config import BayesConfig, FEATURE_SPECS, MODEL_VERSION
from .features import bucket_value, clean_scalar, validate_events


@dataclass
class BayesianSupportResult:
    prior_success_probability: float | None
    posterior_success_probability: float | None
    posterior_failure_probability: float | None
    confidence_percent: float | None
    support_confidence_level: str
    model_status: str
    used_evidence_count: int
    skipped_evidence_count: int
    evidence_details: list
    skipped_evidence: list
    training_sample_count: int
    training_success_count: int
    training_failure_count: int
    model_version: str = MODEL_VERSION

    def to_dict(self):
        return asdict(self)


def stable_logistic(log_odds):
    if log_odds >= 0:
        return 1 / (1 + math.exp(-log_odds))
    exp_value = math.exp(log_odds)
    return exp_value / (1 + exp_value)


class BayesianSupportModel:
    def __init__(self, config=None):
        self.config = config or BayesConfig()
        self.tables, self.metadata = {}, {}
        self.prior = None

    def fit(self, training_events, *, as_of=None, event_definition=None):
        data = validate_events(training_events)
        if as_of is not None:
            cutoff = pd.to_datetime(as_of, utc=True)
            if pd.isna(cutoff):
                raise ValueError('Invalid training cutoff')
            data = data.loc[(data.event_date < cutoff) & (data.label_available_date < cutoff)]
        resolved = data.loc[data.label.isin(['success', 'failure'])]
        successes = int(resolved.label.eq('success').sum())
        failures = int(resolved.label.eq('failure').sum())
        alpha = self.config.alpha
        self.prior = (successes + alpha) / (len(resolved) + 2 * alpha) if len(resolved) else None
        self.metadata = dict(model_version=MODEL_VERSION, training_event_count=len(data),
            training_sample_count=len(resolved), training_success_count=successes,
            training_failure_count=failures, neutral_count=int(data.label.eq('neutral').sum()),
            excluded_label_count=int((~data.label.isin(['success', 'failure', 'neutral'])).sum()),
            training_start=None if resolved.empty else resolved.event_date.min().isoformat(),
            training_end=None if resolved.empty else resolved.event_date.max().isoformat(),
            training_available_until=None if resolved.empty else resolved.label_available_date.max().isoformat(),
            fit_as_of=None if as_of is None else pd.to_datetime(as_of, utc=True).isoformat(),
            symbols=sorted(resolved.symbol.astype(str).unique().tolist()),
            feature_list=list(self.config.active_features), event_definition=event_definition,
            duplicate_count=training_events.attrs.get('duplicate_count', data.attrs.get('duplicate_count', 0)),
            probability_scope='P(success | evidence, resolved within configured horizon)')
        self.tables = {}
        for feature in self.config.active_features:
            spec = FEATURE_SPECS[feature]
            values = resolved.get(spec['raw'], pd.Series(index=resolved.index, dtype='object')).map(lambda value: bucket_value(feature, value))
            known = values.notna()
            totals = {label: int((known & resolved.label.eq(label)).sum()) for label in ('success', 'failure')}
            table = {}
            for category in spec['categories']:
                counts = {label: int((values.eq(category) & resolved.label.eq(label)).sum()) for label in totals}
                # Missing is skipped rather than treated as a category or failure.
                ps = (counts['success'] + alpha) / (totals['success'] + alpha * len(spec['categories']))
                pf = (counts['failure'] + alpha) / (totals['failure'] + alpha * len(spec['categories']))
                table[category] = dict(success_count=counts['success'], failure_count=counts['failure'],
                    bucket_count=sum(counts.values()), observed_success_count=totals['success'],
                    observed_failure_count=totals['failure'], p_given_success=ps, p_given_failure=pf,
                    likelihood_ratio=ps / pf, log_likelihood_ratio=math.log(ps) - math.log(pf),
                    low_sample_warning=sum(counts.values()) < self.config.min_bucket_events)
            self.tables[feature] = table
        return self

    @property
    def status(self):
        if not self.metadata:
            return 'not_fitted'
        if (self.metadata['training_sample_count'] < self.config.min_training_events
                or min(self.metadata['training_success_count'], self.metadata['training_failure_count']) == 0):
            return 'insufficient_training_data'
        return 'ready'

    def predict(self, event):
        if not self.metadata:
            raise ValueError('Model must be fitted or loaded before predict')
        if self.metadata['training_available_until'] is not None:
            if event.get('event_date') is None:
                raise ValueError('Prediction requires event_date to enforce training maturity')
            date = pd.to_datetime(event['event_date'], utc=True)
            if pd.isna(date):
                raise ValueError('Invalid prediction event_date')
            if date <= pd.Timestamp(self.metadata['training_available_until']):
                raise ValueError('Look-ahead bias: model contains labels unavailable before event_date')
        details, skipped = [], []
        for feature in self.config.active_features:
            raw = event.get(FEATURE_SPECS[feature]['raw'])
            bucket = bucket_value(feature, raw)
            reason = None
            entry = self.tables[feature].get(bucket) if bucket is not None else None
            if entry is None:
                reason = 'missing_or_unknown_evidence'
            elif entry['observed_success_count'] + entry['observed_failure_count'] == 0:
                reason = 'no_training_observations'
            if reason:
                skipped.append(dict(feature=feature, raw_value=clean_scalar(raw), reason=reason))
                continue
            lr = entry['likelihood_ratio']
            direction = 'positive' if lr >= self.config.positive_lr else 'negative' if lr <= self.config.negative_lr else 'neutral'
            details.append(dict(feature=feature, raw_value=clean_scalar(raw), bucket=bucket,
                                **entry, direction=direction))
        posterior = None
        if self.prior is not None:
            log_odds = math.log(self.prior) - math.log1p(-self.prior)
            log_odds += math.fsum(e['log_likelihood_ratio'] for e in details)
            posterior = stable_logistic(log_odds)
        level = 'insufficient_data'
        if posterior is not None and self.status == 'ready':
            level = self.config.confidence_labels[-1]
            for edge, label in zip(self.config.confidence_edges, self.config.confidence_labels):
                if posterior < edge:
                    level = label
                    break
        return BayesianSupportResult(self.prior, posterior, None if posterior is None else 1 - posterior,
            posterior * 100 if self.status == 'ready' and posterior is not None else None,
            level, self.status, len(details), len(skipped), details, skipped,
            self.metadata['training_sample_count'], self.metadata['training_success_count'], self.metadata['training_failure_count'])

    def likelihood_frame(self):
        return pd.DataFrame([dict(feature=feature, bucket=bucket, **entry)
                             for feature, table in self.tables.items() for bucket, entry in table.items()])

    def save(self, path):
        if not self.metadata:
            raise ValueError('Cannot save an unfitted model')
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as file:
            json.dump(dict(model_version=MODEL_VERSION, config=asdict(self.config), bins=FEATURE_SPECS,
                           metadata=self.metadata, prior=self.prior, likelihoods=self.tables),
                      file, ensure_ascii=False, indent=2, allow_nan=False)

    @classmethod
    def load(cls, path):
        with Path(path).open(encoding='utf-8') as file:
            stored = json.load(file)
        if stored['model_version'] != MODEL_VERSION or stored['bins'] != FEATURE_SPECS:
            raise ValueError('Model version/bin definitions differ; rebuild explicitly')
        config = stored['config']
        for name in ('active_features', 'confidence_edges', 'confidence_labels', 'calibration_edges'):
            config[name] = tuple(config[name])
        model = cls(BayesConfig(**config))
        model.metadata, model.tables, model.prior = stored['metadata'], stored['likelihoods'], stored['prior']
        count = model.metadata['training_sample_count']
        if any(type(model.metadata[name]) is not int or model.metadata[name] < 0 for name in
               ('training_sample_count', 'training_success_count', 'training_failure_count')):
            raise ValueError('Training counts must be nonnegative integers')
        if count != model.metadata['training_success_count'] + model.metadata['training_failure_count']:
            raise ValueError('Invalid training counts')
        expected_prior = ((model.metadata['training_success_count'] + model.config.alpha)
                          / (count + 2 * model.config.alpha)) if count else None
        if model.prior != expected_prior or set(model.tables) != set(model.config.active_features):
            raise ValueError('Invalid saved prior/features')
        for feature, table in model.tables.items():
            if set(table) != set(FEATURE_SPECS[feature]['categories']):
                raise ValueError('Invalid likelihood categories')
            for entry in table.values():
                for label in ('success', 'failure'):
                    n, total = entry[label + '_count'], entry['observed_' + label + '_count']
                    expected = (n + model.config.alpha) / (total + model.config.alpha * len(table))
                    if not 0 <= n <= total or entry['p_given_' + label] != expected:
                        raise ValueError('Corrupt likelihood counts/probabilities')
                ps, pf = entry['p_given_success'], entry['p_given_failure']
                if not 0 < ps <= 1 or not 0 < pf <= 1 or not math.isclose(entry['likelihood_ratio'], ps / pf):
                    raise ValueError('Invalid likelihood ratio')
                if not math.isclose(entry['log_likelihood_ratio'], math.log(ps) - math.log(pf), abs_tol=1e-12):
                    raise ValueError('Invalid log likelihood ratio')
        return model


def explain_prediction(result, min_training_events=100):
    result = result.to_dict() if isinstance(result, BayesianSupportResult) else result
    count = result['training_sample_count']
    if result['model_status'] != 'ready':
        return f'Bayesian 支撐可信度：資料不足\n歷史有效事件：{count} / 最低需求 {min_training_events}'
    lines = [f"Bayesian 支撐可信度：{result['confidence_percent']:.0f}%｜{result['support_confidence_level']}",
             '定義：已 resolved 的支撐測試中，達成既定反彈條件的估計機率。',
             '研究模型，尚未校準；此數值不是股票上漲機率。',
             f"Prior：{result['prior_success_probability']:.0%}"]
    for direction, title in (('positive', '主要正向證據'), ('negative', '主要負向證據')):
        entries = sorted((e for e in result['evidence_details'] if e['direction'] == direction),
                         key=lambda e: abs(e['log_likelihood_ratio']), reverse=True)[:3]
        if entries:
            lines.append(title + '：')
            for entry in entries:
                warning = '；小樣本' if entry['low_sample_warning'] else ''
                lines.append(f"・{entry['feature']}={entry['bucket']}（LR {entry['likelihood_ratio']:.2f}{warning}）")
    lines.append(f"樣本：{count} 筆 resolved events；使用 {result['used_evidence_count']} 項、跳過 {result['skipped_evidence_count']} 項證據。")
    return '\n'.join(lines)

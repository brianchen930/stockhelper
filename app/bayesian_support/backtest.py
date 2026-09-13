"""Expanding-window evaluation. Never imported or executed by the scheduler."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pandas as pd

from .config import BayesConfig
from .features import attach_label_availability, validate_events
from .model import BayesianSupportModel, explain_prediction
from .evaluation import (evaluate_predictions, calibration_summary, yearly_summary,
                          source_statistics, dependence_summary)


def backtest_bayesian_support(events_df, config=None):
    config = config or BayesConfig()
    data = validate_events(events_df)
    records = []
    for date, current in data.groupby('event_date', sort=True):
        # An earlier event is NOT eligible until its whole outcome window ended.
        # Same-date events always share the same training set; no row-order leak.
        training = data.loc[(data.event_date < date) & (data.label_available_date < date)]
        model = BayesianSupportModel(config).fit(training, as_of=date)
        for event in current.to_dict('records'):
            result = model.predict(event) if model.status == 'ready' else None
            details = [] if result is None else result.evidence_details
            top = lambda direction: max((e for e in details if e['direction'] == direction),
                                        key=lambda e: abs(e['log_likelihood_ratio']), default=None)
            records.append(dict(symbol=str(event['symbol']), timeframe=event['timeframe'],
                event_date=date.isoformat(), support_low=event['support_low'], support_high=event['support_high'],
                label_available_date=event['label_available_date'].isoformat(),
                prior_success_probability=model.prior, predicted_probability=None if result is None else result.posterior_success_probability,
                baseline_probability=None if result is None else model.prior, actual_label=event['label'],
                model_status='warmup' if result is None else result.model_status,
                training_sample_count=model.metadata['training_sample_count'],
                training_available_until=model.metadata['training_available_until'],
                used_evidence_count=0 if result is None else result.used_evidence_count,
                skipped_evidence_count=len(config.active_features) if result is None else result.skipped_evidence_count,
                evidence_details=details, top_positive_evidence=top('positive'), top_negative_evidence=top('negative')))
    columns = ['symbol', 'timeframe', 'event_date', 'support_low', 'support_high', 'label_available_date',
               'prior_success_probability', 'predicted_probability', 'baseline_probability', 'actual_label',
               'model_status', 'training_sample_count', 'training_available_until', 'used_evidence_count',
               'skipped_evidence_count', 'evidence_details', 'top_positive_evidence', 'top_negative_evidence']
    result = pd.DataFrame(records, columns=columns)
    result.attrs.update(duplicate_count=data.attrs.get('duplicate_count', 0),
                        resolved_count=int(data.label.isin(['success', 'failure']).sum()),
                        neutral_count=int(data.label.eq('neutral').sum()), total_events=len(data))
    return result


def write_research_report(events, predictions, model, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    model.save(directory / 'model.json')
    output = predictions.copy()
    for name in ('evidence_details', 'top_positive_evidence', 'top_negative_evidence'):
        output[name] = output[name].map(lambda value: json.dumps(value, ensure_ascii=False, allow_nan=False))
    output.to_csv(directory / 'predictions.csv', encoding='utf-8-sig', index=False)
    ready = output.loc[output.model_status.eq('ready')]
    ready.tail(10).to_csv(directory / 'recent_10_predictions.csv', encoding='utf-8-sig', index=False)
    summary = dict(dataset=predictions.attrs, training=model.metadata,
                   prior=model.prior, evaluation=evaluate_predictions(predictions, model.config))
    with (directory / 'summary.json').open('x', encoding='utf-8') as file:
        json.dump(summary, file, ensure_ascii=False, indent=2, allow_nan=False)
    for name, table in (('likelihoods', model.likelihood_frame()), ('calibration', calibration_summary(predictions, model.config)),
                        ('yearly', yearly_summary(predictions, model.config)), ('source_statistics', source_statistics(events)),
                        ('conditional_dependence', dependence_summary(events, model.config))):
        table.to_csv(directory / (name + '.csv'), encoding='utf-8-sig', index=False)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events', type=Path)
    parser.add_argument('--output', type=Path, default=Path('data/models/bayesian_support'))
    parser.add_argument('--activate', action='store_true', help='Install the evaluated model for local display; never overwrite an existing model')
    args = parser.parse_args(argv)
    metadata = json.loads((args.events.parent / 'report.json').read_text(encoding='utf-8'))['metadata']
    data = pd.read_csv(args.events, dtype={'symbol': str})
    symbols = data.symbol.unique().tolist()
    if len(symbols) != 1:
        parser.error('CLI expects one symbol and its sibling history.csv; use the API for multiple calendars')
    history = pd.read_csv(args.events.parent / 'history.csv', index_col=0, parse_dates=True)
    if 'label_available_date' not in data:
        data = attach_label_availability(data, {symbols[0]: history}, metadata['config']['lookahead_bars'])
    data = validate_events(data)
    predictions = backtest_bayesian_support(data)
    definition = dict(event_config=metadata['config'], detector_config=metadata.get('detector_config'),
                      source_schema_version=metadata['schema_version'],
                      source_sha256=hashlib.sha256(args.events.read_bytes()).hexdigest())
    model = BayesianSupportModel().fit(data, as_of=history.index[-1], event_definition=definition)
    directory = args.output / (symbols[0] + '_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid4().hex[:8])
    summary = write_research_report(data, predictions, model, directory)
    if args.activate:
        model.save(args.output / (symbols[0] + '.json'))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print('Calibration:\n' + calibration_summary(predictions).to_string(index=False))
    print('Recent predictions:\n' + predictions.loc[predictions.model_status.eq('ready')].tail(10)[
        ['event_date', 'support_low', 'support_high', 'prior_success_probability', 'predicted_probability', 'actual_label']].to_string(index=False))
    print('Output:', directory)


if __name__ == '__main__':
    main()

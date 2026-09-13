"""Manual smoke test: real watchlist -> real quotes -> scheduler -> captured notifier.

Run from 台股: .venv/Scripts/python.exe tests/verify_support_resistance_runtime.py
Uses a SQLite backup and a recording sender; never sends a Discord webhook.
"""
from contextlib import redirect_stdout
from functools import partial
import io
import json
import os
from pathlib import Path
import sqlite3
import ssl
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app.database as database
import app.scheduler as scheduler
from app.notifier import send_stock_notifications


def main():
    output = Path('runtime_verification')
    output.mkdir(exist_ok=True)
    if '--windows-ca' in sys.argv:
        # Use the OS trust store for this process only; TLS verification stays on.
        bundle = output/'windows-trusted-ca.pem'
        bundle.write_text(''.join(ssl.DER_cert_to_PEM_cert(cert)
                          for cert in ssl.create_default_context().get_ca_certs(binary_form=True)), encoding='ascii')
        os.environ['REQUESTS_CA_BUNDLE'] = str(bundle.resolve())
    snapshot = output/'watchlist_snapshot.db'
    with sqlite3.connect(database.DATABASE_PATH.as_uri()+'?mode=ro', uri=True) as source:
        with sqlite3.connect(snapshot) as target:
            source.backup(target)
    captured, rule_results = [], []
    original_evaluate = scheduler.evaluate_notification

    def evaluate(**context):
        result = original_evaluate(**context)
        rule_results.append(dict(has_sr=bool(context.get('support_resistance')),
                                 events=result.get('support_resistance_events', []),
                                 should_notify=result.get('support_resistance_notify', False)))
        return result

    def sender(message):
        captured.append(message)
        return {'success': True, 'status_code': 200, 'error': None}

    log = io.StringIO()
    with patch.object(database, 'DATABASE_PATH', snapshot.resolve()):
        database.create_tables()
        stocks = database.get_all_stocks()
        if not stocks:
            raise RuntimeError('A real watchlist stock is required')
        selected = stocks[:1]
        with patch.object(scheduler, 'get_all_stocks', return_value=selected), \
             patch.object(scheduler, 'evaluate_notification', side_effect=evaluate), \
             patch.object(scheduler, 'send_stock_notifications', partial(send_stock_notifications, sender=sender)), \
             redirect_stdout(log):
            scheduler.run_monitor_job()
    (output/'terminal.txt').write_text(log.getvalue(), encoding='utf-8')
    (output/'notifications.txt').write_text('\n\n'.join(captured), encoding='utf-8')
    (output/'rules.json').write_text(json.dumps(rule_results, ensure_ascii=False, indent=2), encoding='utf-8')
    if not rule_results or not rule_results[0]['has_sr']:
        raise RuntimeError('Real runtime did not reach SR notification evaluation; inspect terminal.txt')
    print('Runtime stock:', selected[0]['stock_code'])
    print('SR reached RuleEngine:', rule_results[0]['has_sr'])
    print('Captured notifications:', len(captured))
    print('Artifacts:', output.resolve())


if __name__ == '__main__':
    main()

"""Opt-in live official/FinMind verification using an isolated SQLite database.

Run from the project directory; does not send notifications or change stocks.db.
"""
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.institutional_flow.features import TAIPEI
from app.institutional_flow.service import InstitutionalFlowService
from app.institutional_flow.storage import FlowStore
from app.institutional_flow.presentation import format_context


def main():
    directory = Path(__file__).parent / 'institutional_freshness'
    directory.mkdir(exist_ok=True)
    report = dict(verified_at=datetime.now(TAIPEI).isoformat(), results=[])
    with tempfile.TemporaryDirectory(dir=directory) as temp:
        store = FlowStore(lambda: sqlite3.connect(Path(temp) / 'verification.db'))
        service = InstitutionalFlowService(store)
        for symbol in ('2408.TW', '3211.TWO'):
            context = service.context(symbol)
            report['results'].append(dict(symbol=symbol, context=context, text=format_context(context)))
            print(symbol, context.get('source'), context['data_date'], context['freshness'])
            print(format_context(context))
    path = directory / 'live_report.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    assert all(r['context']['freshness'] == 'FRESH' and r['context']['source'] in ('TWSE', 'TPEx')
               for r in report['results']), 'Live official freshness verification failed; inspect live_report.json'


if __name__ == '__main__':
    main()

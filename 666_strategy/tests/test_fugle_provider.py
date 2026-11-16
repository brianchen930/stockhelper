"""Synthetic provider contracts; actual market replay is audited separately."""
from contextlib import redirect_stdout, redirect_stderr
import copy
import importlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from test_backtest import fixture, prepare, NOW, ZERO_COST

f = importlib.import_module('666_strategy.fugle_provider')
bt = importlib.import_module('666_strategy.backtest')
cli = importlib.import_module('666_strategy.backtest.__main__')


def snapshot_document(parts):
    minute, daily, calendar, start, end = parts
    sources = []
    for interval, frame in [('60m', minute), ('1d', daily.loc[daily.index.date <= end])]:
        rows = []
        for stamp, row in frame.iterrows():
            rows.append(dict(bar_start=stamp.isoformat(),
                source_timestamp=stamp.isoformat() if interval == '60m' else str(stamp.date()),
                **{name: float(row.get(name, row.Close)) for name in f.OHLCV},
                source_volume=float(row.Volume)/(1000 if interval == '60m' else 1),
                source_volume_unit='lots' if interval == '60m' else 'shares'))
        sources.append(dict(symbol='2330.TW', interval=interval, rows=rows))
    return dict(schema=f.SCHEMA, policy=f.POLICY, as_of=str(end)+'T13:30:00+08:00',
        calendar=dict(sessions=[str(day) for day in calendar.sessions],
            start_date=str(calendar.start_date), through_date=str(calendar.through_date), source=calendar.source),
        provenance=dict(source_sha256='synthetic-test-only'), source_snapshots=sources)


class FugleProviderTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)/'snapshot.json'
        self.parts = fixture()

    def save(self, document=None):
        document = copy.deepcopy(snapshot_document(self.parts) if document is None else document)
        document.pop('payload_sha256', None)
        document['payload_sha256'] = f.canonical_digest(document)
        self.path.write_text(json.dumps(document), encoding='utf-8')
        return f.FugleSnapshotProvider(self.path)

    def test_offline_replay_never_reads_credentials_or_downloads(self):
        with patch.object(f, 'FugleClient', side_effect=AssertionError('No live client')):
            provider = self.save()
            data = bt.fetch_backtest_data('2330', self.parts[3], self.parts[4], provider=provider, now=NOW)
            result = bt.run_backtest(data, config=ZERO_COST)
        expected = bt.run_backtest(prepare(self.parts), config=ZERO_COST)
        self.assertEqual(result.fills, expected.fills)
        self.assertEqual(result.summary, expected.summary)
        self.assertEqual(result.metadata['source'], 'Fugle')
        self.assertNotIn('yahoo_60m_policy', result.metadata)
        self.assertEqual(data.history.metadata['interval'], '60m')

    def test_tampered_snapshot_is_refused_before_replay(self):
        self.save()
        document = json.loads(self.path.read_text())
        document['source_snapshots'][0]['rows'][0]['Close'] += 1
        self.path.write_text(json.dumps(document))
        with self.assertRaisesRegex(f.ProbeError, '^FUGLE_SNAPSHOT_DIGEST_MISMATCH$'):
            f.FugleSnapshotProvider(self.path)

    def test_incorrect_volume_conversion_is_refused_even_if_resealed(self):
        document = snapshot_document(self.parts)
        document['source_snapshots'][0]['rows'][0]['Volume'] *= 1000
        with self.assertRaisesRegex(f.ProbeError, '^FUGLE_SNAPSHOT_UNIT_OR_VALUE_MISMATCH$'):
            self.save(document)

    def test_naive_timestamp_is_refused_even_if_resealed(self):
        document = snapshot_document(self.parts)
        document['source_snapshots'][0]['rows'][0]['source_timestamp'] = '2026-01-01T09:00:00'
        with self.assertRaises(f.ProbeError):
            self.save(document)

    def test_missing_bar_fails_existing_integrity_check_without_shortening(self):
        document = snapshot_document(self.parts)
        document['source_snapshots'][0]['rows'].pop(61)
        provider = self.save(document)
        with self.assertRaisesRegex(bt.BacktestDataError, 'INCOMPLETE_60M'):
            bt.fetch_backtest_data('2330', self.parts[3], self.parts[4], provider=provider, now=NOW)

    def test_missing_daily_warmup_fails_existing_integrity_check(self):
        document = snapshot_document(self.parts)
        document['source_snapshots'][1]['rows'].pop(-2)
        provider = self.save(document)
        with self.assertRaisesRegex(bt.BacktestDataError, 'INCOMPLETE_DAILY'):
            bt.fetch_backtest_data('2330', self.parts[3], self.parts[4], provider=provider, now=NOW)

    def test_snapshot_as_of_cannot_be_extended(self):
        document = snapshot_document(self.parts)
        document['as_of'] = str(self.parts[3])+'T13:29:00+08:00'
        provider = self.save(document)
        with self.assertRaisesRegex(bt.BacktestDataError, 'SNAPSHOT_AS_OF'):
            bt.fetch_backtest_data('2330', self.parts[3], self.parts[4], provider=provider, now=NOW)

    def test_snapshot_returns_independent_end_exclusive_copy(self):
        provider = self.save()
        raw = self.parts[0]
        start, end = raw.index[-5], raw.index[-3]
        frame = provider.history('2330.TW', start=start, end=end, interval='60m')
        self.assertEqual(list(frame.index), [start, raw.index[-4]])
        frame.loc[start, 'Close'] = -1
        self.assertGreater(provider.history('2330.TW', start=start, end=end, interval='60m').Close.iloc[0], 0)

    def test_live_requests_split_and_keep_native_prices_and_auction_volume(self):
        calls = []
        class Client:
            def candles(self, spec):
                calls.append(spec)
                stamp = spec['start']+'T13:00:00+08:00'
                return [dict(bar_start=stamp, Open=100, High=105, Low=99, Close=104,
                    Volume=3000, source_volume=3, source_volume_unit='lots', source_timestamp=stamp)]
        provider = f.FugleProvider(client=Client())
        frame = provider.history('2330.TW', start=pd.Timestamp('2025-01-01T00:00:00+08:00'),
            end=pd.Timestamp('2026-01-01T00:00:00+08:00'), interval='60m')
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0]['start'], '2025-01-01')
        self.assertEqual(calls[-1]['end'], '2025-12-31')
        for previous, current in zip(calls, calls[1:]):
            self.assertEqual(pd.Timestamp(previous['end'])+pd.Timedelta(days=1), pd.Timestamp(current['start']))
        self.assertTrue(frame.Close.eq(104).all())
        self.assertTrue(frame.Volume.eq(3000).all())

    def test_extra_1330_bar_is_rejected_instead_of_silently_dropped_or_merged(self):
        with self.assertRaisesRegex(f.ProbeError, '^FUGLE_NON_NATIVE_60M_GRID$'):
            f.native_frame([dict(bar_start='2026-01-05T13:30:00+08:00', Close=100)], '60m')

    def test_duplicate_bars_are_rejected(self):
        row = dict(bar_start='2026-01-05T13:00:00+08:00', Close=100)
        with self.assertRaisesRegex(f.ProbeError, '^DUPLICATE_TIMESTAMP$'):
            f.native_frame([row, row], '60m')

    def test_fugle_does_not_use_yahoo_730_day_limit_or_index_reference(self):
        parts, calls = self.parts, []
        class Provider:
            source_id = 'fugle'
            metadata = dict(source='Fugle', source_mode='synthetic_test')
            def history(self, symbol, *, interval, **kwargs):
                calls.append((symbol, interval))
                return parts[0] if interval == '60m' else parts[1]
        data = bt.fetch_backtest_data('2330', parts[3], parts[4], provider=Provider(),
            calendar=parts[2], now=NOW+pd.Timedelta(days=1000))
        self.assertEqual(calls, [('2330.TW', '60m'), ('2330.TW', '1d')])
        self.assertEqual(data.metadata['source'], 'Fugle')

    def test_arbitrary_provider_exception_is_not_exposed(self):
        class Provider:
            source_id = 'fugle'
            def history(self, *args, **kwargs):
                raise RuntimeError('synthetic-secret')
        with self.assertRaises(bt.BacktestDataError) as caught:
            bt.fetch_backtest_data('2330', self.parts[3], self.parts[4], provider=Provider(),
                                  calendar=self.parts[2], now=NOW)
        self.assertNotIn('synthetic-secret', str(caught.exception))

    def test_unsupported_markets_and_intervals_fail_explicitly(self):
        provider = self.save()
        for symbol, interval in [('6488.TWO', '60m'), ('2330.TW', '1h')]:
            with self.assertRaises(f.ProbeError):
                provider.history(symbol, start=NOW-pd.Timedelta(days=1), end=NOW, interval=interval)

    def test_cli_selects_fugle_and_exports_full_results(self):
        self.save()
        output = self.path.parent/'result.json'
        with redirect_stdout(io.StringIO()):
            code = cli.main(['--source', 'fugle', '--source-snapshot', str(self.path),
                '--symbol', '2330', '--start', str(self.parts[3]), '--end', str(self.parts[4]), '--output', str(output)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['metadata']['source'], 'Fugle')
        self.assertTrue(output.with_suffix('.fills.csv').is_file())

    def test_yahoo_snapshot_cannot_be_mistaken_for_fugle(self):
        self.path.write_text(json.dumps(dict(as_of=NOW.isoformat(), source_snapshots=[])))
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(['--source', 'fugle', '--source-snapshot', str(self.path),
                      '--symbol', '2330', '--start', str(self.parts[3]), '--end', str(self.parts[4])])


if __name__ == '__main__':
    unittest.main()

"""Market identity, native units, sealed TPEx data and legacy TWSE contracts."""
import copy
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from test_backtest import fixture, NOW
from test_fugle_provider import snapshot_document
from test_fugle_probe import Opener, payload, spec

f = importlib.import_module('666_strategy.fugle_provider')
c = importlib.import_module('666_strategy.fugle_client')
data = importlib.import_module('666_strategy.backtest.data')


def tpex_document():
    document = copy.deepcopy(snapshot_document(fixture()))
    document.update(schema=f.SCHEMA_V2, policy=f.POLICY_V2,
        research_sample={'origin':'user_designated','symbols':['3211.TWO']},
        closing_time_validation={'status':'SYNTHETIC_TEST_ONLY'})
    for series in document['source_snapshots']:
        series.update(symbol='3211.TWO',exchange='TPEx',market='OTC')
        for row in series['rows']:
            row.update(source_exchange='TPEx',source_market='OTC')
    return document


class FugleTPExTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/'snapshot.json'

    def save(self,document):
        doc = copy.deepcopy(document)
        doc.pop('payload_sha256',None)
        doc['payload_sha256'] = f.canonical_digest(doc)
        self.path.write_text(json.dumps(doc),encoding='utf-8')
        return f.FugleSnapshotProvider(self.path)

    def test_suffix_resolution_is_explicit_and_legacy_helper_stays_strict(self):
        self.assertEqual(f.fugle_symbol('3105.TWO'),('3105','TPEx','OTC'))
        self.assertEqual(f.fugle_symbol('2330.TW'),('2330','TWSE','TSE'))
        for bad in ('3105','3105.OTC','3105.TWOO','3105.t wo','3105.US'):
            with self.assertRaises(c.ProbeError):f.fugle_symbol(bad)
        with self.assertRaises(c.ProbeError):f.twse_symbol('3105.TWO')

    def test_real_transport_path_accepts_tpex_only_when_explicit(self):
        raw = payload();raw.update(exchange='TPEx',market='OTC')
        request = dict(spec(),exchange='TPEx',market='OTC')
        client = c.FugleClient(environ={'FUGLE_API_KEY':'synthetic-test'},opener=Opener(body=raw))
        row = client.candles(request)[0]
        self.assertEqual(row['source_exchange'],'TPEx')
        self.assertEqual(row['source_market'],'OTC')
        with self.assertRaisesRegex(c.ProbeError,'METADATA_MISMATCH'):
            c.normalize(raw,spec())

    def test_tpex_daily_and_intraday_units_preserve_native_values(self):
        for tf,volume,unit in [('60',12000,'lots'),('D',12,'shares')]:
            raw = payload(tf);raw.update(exchange='TPEx',market='OTC')
            row = c.normalize(raw,dict(spec(tf),exchange='TPEx',market='OTC'))[0]
            self.assertEqual(row['Volume'],volume)
            self.assertEqual(row['source_volume_unit'],unit)
            self.assertEqual(row['source_volume'],12)

    def test_market_crossing_and_esb_are_not_accepted(self):
        for exchange,market in [('TWSE','TSE'),('TPEx','ESB'),('OTC','OTC'),('TPEx',None)]:
            raw = payload();raw.update(exchange=exchange,market=market)
            with self.assertRaisesRegex(c.ProbeError,'METADATA_MISMATCH'):
                c.normalize(raw,dict(spec(),exchange='TPEx',market='OTC'))
        with self.assertRaisesRegex(c.ProbeError,'UNSUPPORTED_MARKET'):
            c.normalize(payload(),dict(spec(),exchange='TPEx',market='ESB'))

    def test_tpex_utc_becomes_taipei_without_losing_source_time(self):
        raw = payload(stamps=['2026-02-03T05:00:00Z']);raw.update(exchange='TPEx',market='OTC')
        row = c.normalize(raw,dict(spec(),exchange='TPEx',market='OTC'))[0]
        self.assertEqual(row['bar_start'],'2026-02-03T13:00:00+08:00')
        self.assertEqual(row['source_timestamp'],'2026-02-03T05:00:00+00:00')

    def test_live_provider_passes_expected_market_and_rejects_forged_client_rows(self):
        calls = []
        class Client:
            def candles(self,request):
                calls.append(request)
                raw = payload();raw.update(symbol='3105',exchange='TPEx',market='OTC')
                return c.normalize(raw,request)
        p = f.FugleProvider(client=Client())
        frame = p.history('3105.TWO',start=pd.Timestamp('2026-02-03T00:00+08:00'),
            end=pd.Timestamp('2026-02-04T00:00+08:00'),interval='60m')
        self.assertEqual(calls[0]['symbol'],'3105')
        self.assertEqual((calls[0]['exchange'],calls[0]['market']),('TPEx','OTC'))
        self.assertEqual(p.snapshots[0]['exchange'],'TPEx')
        self.assertEqual(frame.Volume.iloc[0],12000)
        class Wrong:
            def candles(self,request):return c.normalize(payload(),spec())
        with self.assertRaisesRegex(c.ProbeError,'FUGLE_SNAPSHOT_MARKET_MISMATCH'):
            f.FugleProvider(client=Wrong()).history('3105.TWO',start=frame.index[0],
                end=frame.index[0]+pd.Timedelta(days=1),interval='60m')

    def test_v2_offline_source_preparation_never_downloads_or_runs_strategy(self):
        provider = self.save(tpex_document())
        parts = fixture()
        with patch.object(f,'FugleClient',side_effect=AssertionError('Network forbidden')):
            prepared = data.fetch_backtest_data('3211.TWO',parts[3],parts[4],provider=provider,now=NOW)
        self.assertEqual(prepared.symbol,'3211.TWO')
        self.assertEqual(prepared.metadata['exchange'],'TPEx')
        self.assertEqual(prepared.metadata['market'],'OTC')
        self.assertEqual(prepared.metadata['research_sample']['origin'],'user_designated')

    def test_v2_resealed_wrong_series_identity_rejected(self):
        for field,value in [('exchange','TWSE'),('market','ESB'),('symbol','3211.TW')]:
            doc = tpex_document();doc['source_snapshots'][0][field]=value
            with self.assertRaisesRegex(c.ProbeError,'FUGLE_SNAPSHOT_MARKET_MISMATCH'):self.save(doc)

    def test_v2_resealed_wrong_row_identity_or_units_rejected(self):
        for field,value in [('source_exchange','TWSE'),('source_market','ESB'),('Volume',999999),('source_volume_unit','shares')]:
            doc = tpex_document();doc['source_snapshots'][0]['rows'][0][field]=value
            with self.assertRaises(c.ProbeError):self.save(doc)

    def test_v1_cannot_be_relabelled_as_tpex_even_with_digest_recomputed(self):
        doc = tpex_document();doc.update(schema=f.SCHEMA,policy=f.POLICY)
        with self.assertRaisesRegex(c.ProbeError,'FUGLE_INVALID_SNAPSHOT_SCHEMA'):self.save(doc)

    def test_v2_missing_rows_keep_existing_fail_closed_data_gate(self):
        for series,code in [(0,'INCOMPLETE_60M'),(1,'INCOMPLETE_DAILY')]:
            doc = tpex_document();doc['source_snapshots'][series]['rows'].pop(61)
            provider = self.save(doc);parts = fixture()
            with self.assertRaisesRegex(data.BacktestDataError,code):
                data.fetch_backtest_data('3211.TWO',parts[3],parts[4],provider=provider,now=NOW)

    def test_legacy_v1_rows_and_metadata_are_unmodified(self):
        doc = snapshot_document(fixture())
        provider = self.save(doc)
        self.assertEqual(provider.snapshots,doc['source_snapshots'])
        self.assertEqual({k:v for k,v in provider.metadata.items() if k in f.POLICY},f.POLICY)
        self.assertNotIn('exchange',provider.metadata)
        self.assertNotIn('research_sample',provider.metadata)


if __name__=='__main__':unittest.main()

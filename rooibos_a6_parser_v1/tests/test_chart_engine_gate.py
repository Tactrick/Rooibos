import json
import sqlite3
import unittest

from rooibos_a13.models import EvidenceRecord, canonical_hash
from rooibos_a13.read_gate import snapshot_content_hash, SnapshotAccessDenied
from rooibos_chart_engine.a13_reader import ChartSnapshotReader
from rooibos_a13.lifecycle import apply_migration_008, register_snapshot_for_records


class ChartEngineGateTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('''CREATE TABLE a13_snapshot_decisions (
            snapshot_id TEXT PRIMARY KEY, dataset_type TEXT, record_ids_json TEXT,
            dataset_status TEXT, assessments_hash TEXT, findings_json TEXT,
            decided_at TEXT, policy_version TEXT, snapshot_content_hash TEXT)''')
        self.records = []
        for i in range(3):
            payload = {"open_time_ms": 1_800_000_000_000 + i * 900_000,
                       "open": 10.0+i, "high": 12.0+i, "low": 9.0+i,
                       "close": 11.0+i, "volume": 100.0,
                       "interval_ms": 900_000, "interval": "15m",
                       "candle_status": "closed", "volume_unit": "token",
                       "market_id": "pool-1"}
            self.records.append(EvidenceRecord(
                record_id=f'a6:{i}', dataset_type='a6_ohlcv', chain='solana',
                mint='ExactMintCaseSensitive', source_id='provider-a',
                endpoint_id='ohlcv', run_id='run-1', raw_response_id=f'raw-{i}',
                observed_at='2026-11-09T10:00:00+00:00', normalization_version='a6/1',
                content_hash=canonical_hash(payload), payload=payload, source_record_index=i))
        self.assessments_hash = 'assessments-abc'
        self.snapshot_hash = snapshot_content_hash(self.records)
        self.conn.execute('INSERT INTO a13_snapshot_decisions VALUES (?,?,?,?,?,?,?,?,?)', (
            'snapshot-1', 'a6_ohlcv', json.dumps([r.record_id for r in self.records]),
            'approved_for_research', self.assessments_hash, '[]',
            '2026-11-09T10:01:00+00:00', 'a13-governance/0.1.0', self.snapshot_hash))
        self.conn.commit()
        self.conn.execute('''CREATE TABLE collection_runs (
            run_id TEXT PRIMARY KEY, source_id TEXT, endpoint_id TEXT)''')
        self.conn.execute(
            "INSERT INTO collection_runs VALUES (?,?,?)",
            ('run-1', 'provider-a', 'ohlcv'),
        )
        self.conn.execute('''CREATE TABLE raw_responses (
            raw_response_id TEXT PRIMARY KEY, run_id TEXT, source_id TEXT, endpoint_id TEXT)''')
        for i in range(3):
            self.conn.execute(
                'INSERT INTO raw_responses VALUES (?,?,?,?)',
                (f'raw-{i}', 'run-1', 'provider-a', 'ohlcv'),
            )
        apply_migration_008(self.conn)
        self.conn.execute("""CREATE TABLE a13_snapshot_evidence_links (
            snapshot_id TEXT NOT NULL, record_id TEXT NOT NULL, raw_response_id TEXT NOT NULL,
            source_record_index INTEGER NOT NULL, linked_at TEXT NOT NULL,
            PRIMARY KEY(snapshot_id,record_id))""")
        register_snapshot_for_records(self.conn, snapshot_id='snapshot-1', event_id='snapshot-1:registered',
            effective_at='2026-11-09T10:02:00Z', actor_id='test-governance', reason='publish chart fixture',
            records=self.records)
        self.reader = ChartSnapshotReader(self.conn, allowed_policy_versions={'a13-governance/0.1.0'})

    def test_approved_snapshot_exposes_sorted_candles(self):
        result = self.reader.load(snapshot_id='snapshot-1', records=self.records,
            expected_snapshot_hash=self.snapshot_hash, expected_assessments_hash=self.assessments_hash,
            chain='solana', mint='ExactMintCaseSensitive', interval='15m', expected_candles=3)
        self.assertEqual(len(result.candles), 3)
        self.assertEqual(result.candles[0].open_time_ms, 1_800_000_000_000)
        self.assertEqual(result.candles[0].raw_response_id, 'raw-0')

    def test_no_decision_denied(self):
        with self.assertRaises(SnapshotAccessDenied):
            self.reader.load(snapshot_id='missing', records=self.records,
                expected_snapshot_hash=self.snapshot_hash, expected_assessments_hash=self.assessments_hash,
                chain='solana', mint='ExactMintCaseSensitive', interval='15m')

    def test_hash_tampering_denied(self):
        altered = list(self.records)
        r = altered[0]
        p = dict(r.payload); p['close'] = 10.5
        altered[0] = EvidenceRecord(**{**r.__dict__, 'payload': p})
        with self.assertRaises(SnapshotAccessDenied):
            self.reader.load(snapshot_id='snapshot-1', records=altered,
                expected_snapshot_hash=self.snapshot_hash, expected_assessments_hash=self.assessments_hash,
                chain='solana', mint='ExactMintCaseSensitive', interval='15m')

    def test_exact_mint_case_is_preserved(self):
        with self.assertRaises(SnapshotAccessDenied):
            self.reader.load(snapshot_id='snapshot-1', records=self.records,
                expected_snapshot_hash=self.snapshot_hash, expected_assessments_hash=self.assessments_hash,
                chain='solana', mint='exactmintcasesensitive', interval='15m')

    def test_forming_candle_denied(self):
        changed = list(self.records)
        r = changed[0]; p = dict(r.payload); p['candle_status'] = 'forming'
        changed[0] = EvidenceRecord(**{**r.__dict__, 'payload': p,
                                       'content_hash': canonical_hash(p)})
        h = snapshot_content_hash(changed)
        self.conn.execute('UPDATE a13_snapshot_decisions SET snapshot_content_hash=? WHERE snapshot_id=?', (h, 'snapshot-1'))
        self.conn.commit()
        with self.assertRaises(SnapshotAccessDenied):
            self.reader.load(snapshot_id='snapshot-1', records=changed,
                expected_snapshot_hash=h, expected_assessments_hash=self.assessments_hash,
                chain='solana', mint='ExactMintCaseSensitive', interval='15m')

    def test_gaps_denied(self):
        changed = list(self.records)
        r = changed[1]; p = dict(r.payload); p['open_time_ms'] += 900_000
        changed[1] = EvidenceRecord(**{**r.__dict__, 'payload': p,
                                       'content_hash': canonical_hash(p)})
        h = snapshot_content_hash(changed)
        self.conn.execute('UPDATE a13_snapshot_decisions SET snapshot_content_hash=? WHERE snapshot_id=?', (h, 'snapshot-1'))
        self.conn.commit()
        with self.assertRaises(SnapshotAccessDenied):
            self.reader.load(snapshot_id='snapshot-1', records=changed,
                expected_snapshot_hash=h, expected_assessments_hash=self.assessments_hash,
                chain='solana', mint='ExactMintCaseSensitive', interval='15m')


if __name__ == '__main__':
    unittest.main()

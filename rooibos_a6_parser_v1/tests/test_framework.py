import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rooibos_db.database import initialize_database, check_integrity
from rooibos_collectors import (
    CollectorFramework, CollectorPolicyError, EndpointPolicy, InvalidRunTransition,
)
from rooibos_a6.parser import ParseContext, normalise_records
from rooibos_a6.storage import persist_parse_result
from rooibos_collectors.a6_pipeline import process_a6_raw_response
from rooibos_a13.models import SourcePolicy


class CollectorFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'moby_market.db'
        initialize_database(self.db)
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO sources(source_id,source_name,source_type) VALUES ('moby','Moby','api')")
        c.execute("INSERT INTO source_endpoints(endpoint_id,source_id,endpoint_name,method,path) VALUES ('ohlcv','moby','get_token_ohlcv','GET','/token/ohlcv')")
        c.execute("INSERT INTO collectors(collector_id,collector_name,module_code,version) VALUES ('A6','A6 OHLCV','A6','1.0.0')")
        c.execute("INSERT INTO assets(asset_id,chain,token_address,symbol) VALUES ('asset1','solana','MintABC','TST')")
        c.commit(); c.close()
        self.policy = EndpointPolicy(
            source_id='moby',endpoint_id='ohlcv',endpoint_name='get_token_ohlcv',method='GET',
            path='/token/ohlcv',allowed_parameter_keys=frozenset({'mint','interval','limit'}),
            validated=True,max_limit=100,
        )
        self.framework = CollectorFramework(self.db,[self.policy])

    def tearDown(self):
        self.tmp.cleanup()

    def create_run(self):
        self.framework.create_run(
            run_id='run1',collector_id='A6',source_id='moby',endpoint_id='ohlcv',
            collector_version='1.0.0',requested_scope={'mint':'MintABC','interval':'15m','limit':2},
            started_at='2026-11-09T10:00:00Z',
        )

    def test_create_run_and_request_ticket_capture_raw_before_parse(self):
        self.create_run()
        self.framework.transition('run1','configured')
        ticket = self.framework.authorise_request('run1',{'mint':'MintABC','interval':'15m','limit':2})
        self.assertEqual(ticket.request_sequence,0)
        raw_hash = self.framework.capture_response(
            ticket,raw_response_id='raw1',payload={'synthetic':True},http_status=200,
            retrieved_at='2026-11-09T10:00:01Z',page_sequence=0,
        )
        self.assertTrue(raw_hash)
        c = sqlite3.connect(self.db)
        raw = c.execute('SELECT request_id,request_params_json FROM raw_responses WHERE raw_response_id=?',('raw1',)).fetchone()
        run = c.execute('SELECT status,request_count,response_count FROM collection_runs WHERE run_id=?',('run1',)).fetchone()
        c.close()
        self.assertEqual(raw[0],ticket.request_id)
        self.assertEqual(run,('raw_captured',1,1))
        self.framework.transition('run1','parsed')
        context = ParseContext(
            chain='solana',asset_id='asset1',mint_address='MintABC',interval='15m',source_id='moby',
            endpoint_id='ohlcv',collection_run_id='run1',raw_response_ref='raw1',
        )
        records = [{'timestamp_ms':1794225600000,'open':1,'high':1.2,'low':.9,'close':1.1,'volume':10,'candle_status':'closed'}]
        result = normalise_records(records,context)
        persisted = persist_parse_result(self.db,result=result,raw_response_id='raw1')
        self.assertEqual(persisted['inserted_count'],1)
        c = sqlite3.connect(self.db)
        self.assertEqual(c.execute('SELECT status FROM collection_runs WHERE run_id=?',('run1',)).fetchone()[0],'normalised')
        c.close()
        self.assertTrue(check_integrity(self.db)['ok'])


    def test_a6_raw_capture_to_a13_publication_end_to_end(self):
        self.create_run()
        self.framework.transition('run1', 'configured')
        ticket = self.framework.authorise_request(
            'run1', {'mint':'MintABC','interval':'15m','limit':2}
        )
        self.framework.capture_response(
            ticket, raw_response_id='raw-pipeline',
            payload={'provider_fixture':'synthetic', 'identity':{'mint':'MintABC'}, 'records':[
                {'timestamp_ms':1794225600000,'open':1,'high':1.2,'low':.9,'close':1.1,'volume':10},
                {'timestamp_ms':1794226500000,'open':1.1,'high':1.3,'low':1,'close':1.2,'volume':12},
            ]},
            http_status=200, retrieved_at='2026-11-09T10:00:01Z', page_sequence=0,
        )
        context = ParseContext(
            chain='solana', asset_id='asset1', mint_address='MintABC',
            interval='15m', source_id='moby', endpoint_id='ohlcv',
            collection_run_id='run1', raw_response_ref='raw-pipeline',
            volume_unit='base_token',
        )
        outcome = process_a6_raw_response(
            self.framework, run_id='run1', raw_response_id='raw-pipeline',
            parse_context=context,
            request_identity_fields={'mint_address':'mint','interval':'interval'},
            response_identity_fields={'mint_address':('identity','mint')},
            decode_payload=lambda payload: [
                dict(row, candle_status='closed', source_record_index=index,
                     source_record_count=len(payload['records']))
                for index, row in enumerate(payload['records'])
            ],
            snapshot_id='a6-e2e-snapshot',
            source_policies={'moby': SourcePolicy('moby', ('ohlcv',), True)},
            assessed_at='2026-11-09T10:02:00Z',
            decided_at='2026-11-09T10:03:00Z',
            effective_at='2026-11-09T10:04:00Z',
            actor_id='test-governance', reason='verify complete A6 publication path',
        )
        self.assertTrue(outcome.published)
        self.assertEqual(len(outcome.candle_ids), 2)
        self.assertEqual(outcome.decision.dataset_status, 'approved_for_research')
        c = sqlite3.connect(self.db)
        self.assertEqual(
            c.execute("SELECT status FROM collection_runs WHERE run_id='run1'").fetchone()[0],
            'completed',
        )
        self.assertEqual(
            c.execute("SELECT COUNT(*) FROM a13_snapshot_raw_links WHERE snapshot_id='a6-e2e-snapshot'").fetchone()[0],
            1,
        )
        c.close()


    def test_a6_pipeline_quarantines_empty_decode_without_a13_publication(self):
        self.create_run()
        self.framework.transition('run1', 'configured')
        ticket = self.framework.authorise_request(
            'run1', {'mint':'MintABC','interval':'15m','limit':2}
        )
        self.framework.capture_response(
            ticket, raw_response_id='raw-empty',
            payload={'provider_fixture':'synthetic', 'identity':{'mint':'MintABC'}, 'records':[]},
            http_status=200, retrieved_at='2026-11-09T10:00:01Z', page_sequence=0,
        )
        context = ParseContext(
            chain='solana', asset_id='asset1', mint_address='MintABC',
            interval='15m', source_id='moby', endpoint_id='ohlcv',
            collection_run_id='run1', raw_response_ref='raw-empty',
            volume_unit='base_token',
        )
        outcome = process_a6_raw_response(
            self.framework, run_id='run1', raw_response_id='raw-empty',
            parse_context=context, request_identity_fields={'mint_address':'mint','interval':'interval'},
            response_identity_fields={'mint_address':('identity','mint')},
            decode_payload=lambda payload: payload['records'],
            snapshot_id='a6-empty-snapshot',
            source_policies={'moby': SourcePolicy('moby', ('ohlcv',), True)},
            assessed_at='2026-11-09T10:02:00Z',
            decided_at='2026-11-09T10:03:00Z',
            effective_at='2026-11-09T10:04:00Z',
            actor_id='test-governance', reason='verify empty response quarantine',
        )
        self.assertFalse(outcome.published)
        self.assertEqual(outcome.candle_ids, ())
        c = sqlite3.connect(self.db)
        self.assertEqual(
            c.execute("SELECT status FROM collection_runs WHERE run_id='run1'").fetchone()[0],
            'quarantined',
        )
        self.assertEqual(c.execute("SELECT COUNT(*) FROM a13_snapshot_decisions").fetchone()[0], 0)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM ohlcv_candles").fetchone()[0], 0)
        c.close()


    def _assert_a6_request_identity_mismatch_is_rejected(self, *, mismatch):
        self.create_run()
        self.framework.transition('run1', 'configured')
        ticket = self.framework.authorise_request(
            'run1', {'mint':'MintABC','interval':'15m','limit':2}
        )
        raw_id = 'raw-mismatch-' + mismatch
        self.framework.capture_response(
            ticket, raw_response_id=raw_id, payload={'identity':{'mint':'MintABC'}, 'records':[]},
            http_status=200, retrieved_at='2026-11-09T10:00:01Z', page_sequence=0,
        )
        context = ParseContext(
            chain='solana', asset_id='asset1',
            mint_address='WrongMint' if mismatch == 'mint_address' else 'MintABC',
            interval='1h' if mismatch == 'interval' else '15m',
            source_id='moby', endpoint_id='ohlcv',
            collection_run_id='run1', raw_response_ref=raw_id,
        )
        with self.assertRaisesRegex(ValueError, 'does not match captured request'):
            process_a6_raw_response(
                self.framework, run_id='run1', raw_response_id=raw_id,
                parse_context=context,
                request_identity_fields={'mint_address':'mint','interval':'interval'},
                response_identity_fields={'mint_address':('identity','mint')},
                decode_payload=lambda payload: payload['records'],
                snapshot_id='mismatch-' + mismatch,
                source_policies={'moby': SourcePolicy('moby', ('ohlcv',), True)},
                assessed_at='2026-11-09T10:02:00Z',
                decided_at='2026-11-09T10:03:00Z',
                effective_at='2026-11-09T10:04:00Z',
                actor_id='test-governance', reason='verify request identity binding',
            )
        c = sqlite3.connect(self.db)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM ohlcv_candles").fetchone()[0], 0)
        self.assertEqual(c.execute(
            "SELECT status FROM collection_runs WHERE run_id='run1'"
        ).fetchone()[0], 'raw_captured')
        c.close()

    def test_a6_pipeline_rejects_mismatched_requested_mint(self):
        self._assert_a6_request_identity_mismatch_is_rejected(mismatch='mint_address')

    def test_a6_pipeline_rejects_mismatched_requested_interval(self):
        self._assert_a6_request_identity_mismatch_is_rejected(mismatch='interval')

    def _assert_a6_response_identity_rejected(self, *, payload, expected_message):
        self.create_run()
        self.framework.transition('run1', 'configured')
        ticket = self.framework.authorise_request('run1', {'mint':'MintABC','interval':'15m','limit':2})
        self.framework.capture_response(ticket, raw_response_id='raw-response-identity', payload=payload, http_status=200, retrieved_at='2026-11-09T10:00:01Z', page_sequence=0)
        context = ParseContext(chain='solana', asset_id='asset1', mint_address='MintABC', interval='15m', source_id='moby', endpoint_id='ohlcv', collection_run_id='run1', raw_response_ref='raw-response-identity')
        with self.assertRaisesRegex(ValueError, expected_message):
            process_a6_raw_response(self.framework, run_id='run1', raw_response_id='raw-response-identity', parse_context=context, request_identity_fields={'mint_address':'mint','interval':'interval'}, response_identity_fields={'mint_address':('identity','mint')}, decode_payload=lambda value: value['records'], snapshot_id='response-identity-rejected', source_policies={'moby': SourcePolicy('moby', ('ohlcv',), True)}, assessed_at='2026-11-09T10:02:00Z', decided_at='2026-11-09T10:03:00Z', effective_at='2026-11-09T10:04:00Z', actor_id='test-governance', reason='verify response market identity')
        c = sqlite3.connect(self.db)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM ohlcv_candles').fetchone()[0], 0)
        self.assertEqual(c.execute("SELECT status FROM collection_runs WHERE run_id='run1'").fetchone()[0], 'raw_captured')
        c.close()

    def test_a6_pipeline_rejects_response_for_different_mint(self):
        self._assert_a6_response_identity_rejected(payload={'identity':{'mint':'OtherMint'}, 'records':[]}, expected_message='response mint identity does not match')

    def test_a6_pipeline_rejects_response_without_market_identity(self):
        self._assert_a6_response_identity_rejected(payload={'records':[]}, expected_message='missing mapped market identity path')

    def test_unknown_endpoint_is_default_denied(self):
        framework = CollectorFramework(self.db,[])
        with self.assertRaises(CollectorPolicyError):
            framework.create_run(
                run_id='run1',collector_id='A6',source_id='moby',endpoint_id='ohlcv',
                collector_version='1',requested_scope={},
            )

    def test_unvalidated_candidate_is_denied(self):
        candidate = EndpointPolicy(
            source_id='moby',endpoint_id='ohlcv',endpoint_name='get_token_ohlcv',method='GET',
            path='/token/ohlcv',allowed_parameter_keys=frozenset({'mint'}),validated=False,
        )
        framework = CollectorFramework(self.db,[candidate])
        with self.assertRaises(CollectorPolicyError):
            framework.create_run(
                run_id='run1',collector_id='A6',source_id='moby',endpoint_id='ohlcv',
                collector_version='1',requested_scope={},
            )

    def test_trading_endpoint_rejected_even_if_marked_validated(self):
        policy = EndpointPolicy(
            source_id='moby',endpoint_id='ohlcv',endpoint_name='quick_buy',method='GET',
            path='/buy',allowed_parameter_keys=frozenset(),validated=True,
        )
        framework = CollectorFramework(self.db,[policy])
        with self.assertRaises(CollectorPolicyError):
            framework.create_run(
                run_id='run1',collector_id='A6',source_id='moby',endpoint_id='ohlcv',
                collector_version='1',requested_scope={},
            )

    def test_endpoint_database_mismatch_is_denied(self):
        wrong = EndpointPolicy(
            source_id='moby',endpoint_id='ohlcv',endpoint_name='other_endpoint',method='GET',
            path='/other',allowed_parameter_keys=frozenset(),validated=True,
        )
        framework = CollectorFramework(self.db,[wrong])
        with self.assertRaises(CollectorPolicyError):
            framework.create_run(
                run_id='run1',collector_id='A6',source_id='moby',endpoint_id='ohlcv',
                collector_version='1',requested_scope={},
            )

    def test_request_parameter_allowlist_and_limit_are_enforced(self):
        self.create_run(); self.framework.transition('run1','configured')
        with self.assertRaises(CollectorPolicyError):
            self.framework.authorise_request('run1',{'mint':'MintABC','secret':'x'})
        with self.assertRaises(CollectorPolicyError):
            self.framework.authorise_request('run1',{'mint':'MintABC','limit':101})
        with self.assertRaises(CollectorPolicyError):
            self.framework.authorise_request('run1',{'mint':'MintABC','limit':True})

    def test_invalid_lifecycle_transition_is_rejected(self):
        self.create_run()
        with self.assertRaises(InvalidRunTransition):
            self.framework.transition('run1','completed')
        self.framework.transition('run1','configured')
        self.framework.transition('run1','collecting')
        self.framework.transition('run1','failed')
        with self.assertRaises(InvalidRunTransition):
            self.framework.transition('run1','collecting')

    def test_collector_cannot_complete_without_a_verified_a13_publication(self):
        self.create_run()
        self.framework.transition('run1', 'configured')
        self.framework.transition('run1', 'collecting')
        self.framework.transition('run1', 'raw_captured')
        self.framework.transition('run1', 'parsed')
        self.framework.transition('run1', 'normalised')
        self.framework.transition('run1', 'validating')

        with self.assertRaisesRegex(InvalidRunTransition, 'complete_after_a13_publication'):
            self.framework.transition('run1', 'completed')

        with self.assertRaisesRegex(InvalidRunTransition, 'GateGrant'):
            self.framework.complete_after_a13_publication(
                'run1', grant=object(), records=[object()],
            )

        c = sqlite3.connect(self.db)
        self.assertEqual(
            c.execute("SELECT status FROM collection_runs WHERE run_id='run1'").fetchone()[0],
            'validating',
        )
        c.close()

    def test_request_cannot_be_issued_before_run_is_configured(self):
        self.create_run()
        with self.assertRaises(InvalidRunTransition):
            self.framework.authorise_request('run1',{'mint':'MintABC'})


if __name__ == '__main__':
    unittest.main()

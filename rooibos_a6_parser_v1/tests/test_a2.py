import sqlite3, sys, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from rooibos_db.database import initialize_database, check_integrity
from rooibos_a6.storage import store_raw_response
from rooibos_a2 import (MarketMetricInput, MarketObservationInput, normalize_observation,
                        compare_observations, persist_a2_result, persist_comparison)

class A2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.db=Path(self.tmp.name)/'moby_market.db'
        initialize_database(self.db)
        c=sqlite3.connect(self.db)
        c.execute("INSERT INTO sources(source_id,source_name,source_type) VALUES ('src1','Source 1','api')")
        c.execute("INSERT INTO sources(source_id,source_name,source_type) VALUES ('src2','Source 2','api')")
        c.execute("INSERT INTO source_endpoints(endpoint_id,source_id,endpoint_name,path) VALUES ('ep1','src1','market_data','/market')")
        c.execute("INSERT INTO source_endpoints(endpoint_id,source_id,endpoint_name,path) VALUES ('ep2','src2','market_data','/market')")
        c.execute("INSERT INTO collectors(collector_id,collector_name,module_code,version) VALUES ('A2','A2','A2','1.0.0')")
        c.execute("INSERT INTO collection_runs(run_id,collector_id,source_id,endpoint_id,collector_version,requested_scope_json,started_at,status) VALUES ('r1','A2','src1','ep1','1.0.0','{}','2026-11-09T10:00:00Z','raw_captured')")
        c.execute("INSERT INTO collection_runs(run_id,collector_id,source_id,endpoint_id,collector_version,requested_scope_json,started_at,status) VALUES ('r2','A2','src2','ep2','1.0.0','{}','2026-11-09T10:00:00Z','raw_captured')")
        c.commit(); c.close()
        store_raw_response(self.db,raw_response_id='raw1',run_id='r1',source_id='src1',endpoint_id='ep1',payload={'raw':1},request_params={},retrieved_at='2026-11-09T10:00:01Z')
        store_raw_response(self.db,raw_response_id='raw2',run_id='r2',source_id='src2',endpoint_id='ep2',payload={'raw':2},request_params={},retrieved_at='2026-11-09T10:00:02Z')

    def tearDown(self): self.tmp.cleanup()

    def item(self, source='src1', endpoint='ep1', run='r1', raw='raw1', metrics=None, **kw):
        return MarketObservationInput('solana','MintABC','2026-11-09T10:00:00Z',source,endpoint,run,raw,0,tuple(metrics or [MarketMetricInput('liquidity',100000,unit='USD')]),**kw)

    def test_zero_is_present_and_distinct_from_missing(self):
        result=normalize_observation(self.item(metrics=[MarketMetricInput('buy_count',0),MarketMetricInput('sell_count',None,'missing')]))
        self.assertEqual(len(result.observations),2)
        self.assertEqual(result.observations[0].numeric_value,0.0)
        self.assertEqual(result.observations[0].value_status,'present')
        self.assertIsNone(result.observations[1].numeric_value)
        self.assertEqual(result.observations[1].value_status,'missing')

    def test_negative_nan_and_boolean_rejected(self):
        for value in (-1,float('nan'),True):
            r=normalize_observation(self.item(metrics=[MarketMetricInput('liquidity',value)]))
            self.assertEqual(len(r.observations),0)
            self.assertEqual(r.rejected_metric_count,1)

    def test_volume_requires_window_warning(self):
        r=normalize_observation(self.item(metrics=[MarketMetricInput('volume',100)]))
        self.assertTrue(any(f.code=='VOLUME_WINDOW_UNKNOWN' for f in r.findings))

    def test_unknown_metric_rejected(self):
        r=normalize_observation(self.item(metrics=[MarketMetricInput('magic_metric',5)]))
        self.assertEqual(r.rejected_metric_count,1)

    def test_pool_specific_requires_identity_warning(self):
        r=normalize_observation(self.item(metrics=[MarketMetricInput('liquidity',10)],market_scope='pool_specific'))
        self.assertTrue(any(f.code=='POOL_IDENTITY_MISSING' for f in r.findings))

    def test_deterministic_ids_and_manifest(self):
        a=normalize_observation(self.item()); b=normalize_observation(self.item())
        self.assertEqual(a.observations[0].observation_id,b.observations[0].observation_id)
        self.assertEqual(a.manifest()['manifest_sha256'],b.manifest()['manifest_sha256'])

    def test_metric_metadata_is_part_of_observation_identity(self):
        a=normalize_observation(self.item(metrics=[MarketMetricInput('liquidity',100,unit='USD',metadata={'provider_field':'liqA'})])).observations[0]
        b=normalize_observation(self.item(metrics=[MarketMetricInput('liquidity',100,unit='USD',metadata={'provider_field':'liqB'})])).observations[0]
        self.assertNotEqual(a.observation_id,b.observation_id)
        self.assertNotEqual(a.content_sha256,b.content_sha256)
    def test_cross_source_like_for_like_comparison(self):
        a=normalize_observation(self.item()).observations[0]
        b=normalize_observation(self.item(source='src2',endpoint='ep2',run='r2',raw='raw2',metrics=[MarketMetricInput('liquidity',104000,unit='USD')])).observations[0]
        c=compare_observations([a,b],tolerance_pct=5)
        self.assertEqual(c.outcome,'within_tolerance')
        self.assertAlmostEqual(c.spread_pct,3.9215686,places=5)

    def test_material_disagreement_is_flagged_not_resolved(self):
        a=normalize_observation(self.item()).observations[0]
        b=normalize_observation(self.item(source='src2',endpoint='ep2',run='r2',raw='raw2',metrics=[MarketMetricInput('liquidity',200000,unit='USD')])).observations[0]
        c=compare_observations([a,b],tolerance_pct=5)
        self.assertEqual(c.outcome,'disagreement')
        self.assertIn(a.observation_id,c.observation_ids); self.assertIn(b.observation_id,c.observation_ids)

    def test_different_windows_or_market_scope_not_comparable(self):
        a=normalize_observation(self.item(metrics=[MarketMetricInput('volume',100,unit='USD',window_seconds=300)])).observations[0]
        b=normalize_observation(self.item(source='src2',endpoint='ep2',run='r2',raw='raw2',metrics=[MarketMetricInput('volume',100,unit='USD',window_seconds=3600)])).observations[0]
        self.assertEqual(compare_observations([a,b]).outcome,'not_comparable')

    def test_same_source_is_not_independent_confirmation(self):
        a=normalize_observation(self.item()).observations[0]
        b=normalize_observation(self.item(endpoint='ep1',run='r1',raw='raw1',metrics=[MarketMetricInput('liquidity',105000,unit='USD')])).observations[0]
        self.assertEqual(compare_observations([a,b]).outcome,'not_comparable')

    def test_observation_time_mismatch_is_not_comparable(self):
        a=normalize_observation(self.item()).observations[0]
        later=MarketObservationInput('solana','MintABC','2026-11-09T10:05:00Z','src2','ep2','r2','raw2',0,(MarketMetricInput('liquidity',105000,unit='USD'),))
        b=normalize_observation(later).observations[0]
        self.assertEqual(compare_observations([a,b]).outcome,'not_comparable')

    def test_estimated_and_observed_values_are_not_compared(self):
        a=normalize_observation(self.item()).observations[0]
        b=normalize_observation(self.item(source='src2',endpoint='ep2',run='r2',raw='raw2',metrics=[MarketMetricInput('liquidity',105000,unit='USD',measurement_kind='estimated')])).observations[0]
        self.assertEqual(compare_observations([a,b]).outcome,'not_comparable')

    def test_different_pool_identity_not_comparable(self):
        a=normalize_observation(self.item(metrics=[MarketMetricInput('liquidity',100,unit='USD')],market_scope='pool_specific',pool_address='Pool1')).observations[0]
        b=normalize_observation(self.item(source='src2',endpoint='ep2',run='r2',raw='raw2',metrics=[MarketMetricInput('liquidity',100,unit='USD')],market_scope='pool_specific',pool_address='Pool2')).observations[0]
        self.assertEqual(compare_observations([a,b]).outcome,'not_comparable')

    def test_persist_and_replay_is_idempotent(self):
        r=normalize_observation(self.item(metrics=[MarketMetricInput('liquidity',0,unit='USD'),MarketMetricInput('volume',None,'unavailable',unit='USD',window_seconds=3600)]))
        a=persist_a2_result(self.db,result=r); b=persist_a2_result(self.db,result=r)
        self.assertEqual(a['inserted_count'],2); self.assertEqual(b['idempotent_replay_count'],2)
        c=sqlite3.connect(self.db); self.assertEqual(c.execute('SELECT COUNT(*) FROM market_observations').fetchone()[0],2); c.close()

    def test_raw_provenance_mismatch_rolls_back(self):
        r=normalize_observation(self.item(source='src2',endpoint='ep2',run='r2',raw='raw1'))
        with self.assertRaises(ValueError): persist_a2_result(self.db,result=r)
        c=sqlite3.connect(self.db); self.assertEqual(c.execute('SELECT COUNT(*) FROM market_observations').fetchone()[0],0); c.close()

    def test_replay_rejects_same_hash_with_changed_provenance(self):
        from dataclasses import replace
        original = normalize_observation(self.item())
        persist_a2_result(self.db, result=original)
        forged = replace(
            original.observations[0],
            source_id='src2', endpoint_id='ep2', run_id='r2', raw_response_id='raw2',
        )
        altered = replace(original, observations=[forged])
        with self.assertRaisesRegex(ValueError, 'content or provenance'):
            persist_a2_result(self.db, result=altered)
        c=sqlite3.connect(self.db)
        row=c.execute(
            'SELECT source_id,endpoint_id,run_id,raw_response_id FROM market_observations'
        ).fetchone()
        c.close()
        self.assertEqual(row, ('src1','ep1','r1','raw1'))

    def test_append_only_trigger(self):
        r=normalize_observation(self.item()); persist_a2_result(self.db,result=r)
        c=sqlite3.connect(self.db)
        with self.assertRaises(sqlite3.IntegrityError): c.execute("UPDATE market_observations SET numeric_value=3")
        with self.assertRaises(sqlite3.IntegrityError): c.execute("DELETE FROM market_observations")
        c.close()

    def test_comparison_persisted(self):
        a=normalize_observation(self.item()).observations[0]
        b=normalize_observation(self.item(source='src2',endpoint='ep2',run='r2',raw='raw2')).observations[0]
        persist_a2_result(self.db,result=normalize_observation(self.item()))
        persist_a2_result(self.db,result=normalize_observation(self.item(source='src2',endpoint='ep2',run='r2',raw='raw2')))
        result=compare_observations([a,b])
        cid=persist_comparison(self.db,comparison=result,observations=[a,b],tolerance_pct=5)
        self.assertTrue(cid)
        c=sqlite3.connect(self.db); self.assertEqual(c.execute('SELECT outcome FROM market_observation_comparisons').fetchone()[0],'within_tolerance'); c.close()

    def test_database_integrity(self):
        persist_a2_result(self.db,result=normalize_observation(self.item()))
        self.assertTrue(check_integrity(self.db)['ok'])

if __name__=='__main__': unittest.main(verbosity=2)

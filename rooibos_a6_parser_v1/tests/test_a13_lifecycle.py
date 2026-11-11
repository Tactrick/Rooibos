import json, sqlite3, tempfile, unittest
from pathlib import Path
from rooibos_db.database import initialize_database, _connect
from rooibos_a13.models import EvidenceRecord
from rooibos_a13.read_gate import authorize_snapshot, snapshot_content_hash, SnapshotAccessDenied
from rooibos_a13.lifecycle import (LifecycleError, register_snapshot, supersede_snapshot,
    withdraw_snapshot, set_legal_hold, lifecycle_state, add_retention_policy, register_snapshot_for_records,
    assign_retention_policy, assess_retention)

class A13LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'moby_market.db'
        initialize_database(self.path); self.conn=_connect(self.path)
        self.record=EvidenceRecord('rec-1','a1_discovery','solana','MintCaseSensitive','fixture','ep','run','raw',
            '2026-11-09T10:00:00Z','norm-v1','payloadhash',{'symbol':'X'},0)
        self.hash=snapshot_content_hash([self.record])
        self._decision('snap-1','approved_for_research')
        self._decision('snap-2','approved_for_research')
        self.conn.execute("INSERT INTO sources(source_id,source_name,source_type) VALUES ('fixture','Fixture','api')")
        self.conn.execute("INSERT INTO collectors(collector_id,collector_name,module_code,version) VALUES ('collector','Fixture','A1','1')")
        self.conn.execute("INSERT INTO collection_runs(run_id,collector_id,source_id,collector_version,requested_scope_json,started_at,status) VALUES ('run','collector','fixture','1','{}','2026-11-09T09:00:00Z','completed')")
        self.conn.execute("INSERT INTO raw_responses(raw_response_id,run_id,source_id,retrieved_at,request_params_json,payload_json,payload_sha256) VALUES ('raw','run','fixture','2026-11-09T10:00:00Z','{}','{}','hash')")
        self.conn.execute("INSERT INTO raw_responses(raw_response_id,run_id,source_id,retrieved_at,request_params_json,payload_json,payload_sha256) VALUES ('raw-other','run','fixture','2026-11-09T10:00:00Z','{}','{\"other\":true}','other-hash')")

    def tearDown(self): self.conn.close(); self.tmp.cleanup()

    def _decision(self,snapshot_id,status):
        self.conn.execute('''INSERT INTO a13_snapshot_decisions
          (snapshot_id,dataset_type,record_ids_json,dataset_status,assessments_hash,findings_json,decided_at,policy_version,snapshot_content_hash)
          VALUES (?,?,?,?,?,?,?,?,?)''',(snapshot_id,'a1_discovery',json.dumps(['rec-1']),status,'assesshash','[]',
          '2026-11-09T10:01:00Z','a13-governance/0.1.0',self.hash))

    def _register(self, *, snapshot_id, event_id, effective_at, actor_id, reason, raw_response_ids):
        if list(raw_response_ids) == ['raw']:
            return register_snapshot_for_records(self.conn, snapshot_id=snapshot_id,
                event_id=event_id, effective_at=effective_at, actor_id=actor_id,
                reason=reason, records=[self.record])
        register_snapshot(self.conn, snapshot_id=snapshot_id, event_id=event_id,
            effective_at=effective_at, actor_id=actor_id, reason=reason,
            raw_response_ids=raw_response_ids)
        self.conn.execute(
            "INSERT INTO a13_snapshot_evidence_links "
            "(snapshot_id,record_id,raw_response_id,source_record_index,linked_at) VALUES (?,?,?,?,?)",
            (snapshot_id, self.record.record_id, self.record.raw_response_id,
             self.record.source_record_index, effective_at))

    def _gate(self,snapshot='snap-1',now='2026-11-09T10:02:00Z',**kwargs):
        return authorize_snapshot(self.conn,snapshot_id=snapshot,records=[self.record],expected_snapshot_hash=self.hash,
          expected_assessments_hash='assesshash',allowed_policy_versions={'a13-governance/0.1.0'},now=now,**kwargs)

    def test_registered_snapshot_passes_required_lifecycle_gate(self):
        self._register(snapshot_id='snap-1',event_id='evt-1',effective_at='2026-11-09T10:01:30Z',
          actor_id='governance',reason='approved and published',raw_response_ids=['raw'])
        self.assertEqual(self._gate(require_lifecycle=True).snapshot_id,'snap-1')
        self.assertEqual(lifecycle_state(self.conn,'snap-1').state,'registered')


    def test_publication_helper_binds_exact_record_membership_and_raw_evidence(self):
        register_snapshot_for_records(self.conn,snapshot_id='snap-1',event_id='evt-1',
          effective_at='2026-11-09T10:01:30Z',actor_id='g',reason='publish',records=[self.record])
        linked=tuple(r[0] for r in self.conn.execute("SELECT raw_response_id FROM a13_snapshot_raw_links WHERE snapshot_id='snap-1'"))
        self.assertEqual(linked,('raw',))
        from dataclasses import replace
        with self.assertRaises(LifecycleError):
            register_snapshot_for_records(self.conn,snapshot_id='snap-2',event_id='evt-x',
              effective_at='2026-11-09T10:01:30Z',actor_id='g',reason='wrong membership',records=[replace(self.record,record_id='other')])

    def test_publication_rejects_record_with_changed_raw_response_reference(self):
        from dataclasses import replace
        altered = replace(self.record, raw_response_id='raw-other')
        with self.assertRaises(LifecycleError):
            register_snapshot_for_records(self.conn, snapshot_id='snap-1', event_id='evt-bad',
                effective_at='2026-11-09T10:01:30Z', actor_id='g', reason='altered provenance',
                records=[altered])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM a13_snapshot_lifecycle_events WHERE snapshot_id='snap-1'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM a13_snapshot_raw_links WHERE snapshot_id='snap-1'").fetchone()[0], 0)

    def test_publication_rejects_changed_record_content_hash(self):
        from dataclasses import replace
        altered = replace(self.record, content_hash='f' * 64)
        with self.assertRaises(LifecycleError):
            register_snapshot_for_records(self.conn, snapshot_id='snap-1', event_id='evt-bad',
                effective_at='2026-11-09T10:01:30Z', actor_id='g', reason='altered content',
                records=[altered])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM a13_snapshot_lifecycle_events WHERE snapshot_id='snap-1'").fetchone()[0], 0)

    def test_read_gate_rejects_registered_link_to_unapproved_raw_response(self):
        register_snapshot(self.conn, snapshot_id='snap-1', event_id='evt-1',
            effective_at='2026-11-09T10:01:30Z', actor_id='g', reason='bad raw link',
            raw_response_ids=['raw-other'])
        with self.assertRaises(SnapshotAccessDenied):
            self._gate(require_lifecycle=True)

    def test_read_gate_rejects_extra_persisted_raw_response_link(self):
        register_snapshot(self.conn, snapshot_id='snap-1', event_id='evt-1',
            effective_at='2026-11-09T10:01:30Z', actor_id='g', reason='publish',
            raw_response_ids=['raw'])
        self.conn.execute('DROP TRIGGER trg_a13_raw_links_no_update')
        self.conn.execute('DROP TRIGGER trg_a13_raw_links_no_delete')
        self.conn.execute("INSERT INTO a13_snapshot_raw_links VALUES ('snap-1','raw-other','2026-11-09T10:01:31Z')")
        with self.assertRaises(SnapshotAccessDenied):
            self._gate(require_lifecycle=True)

    def test_unregistered_snapshot_fails_required_lifecycle_gate(self):
        with self.assertRaises(SnapshotAccessDenied): self._gate(require_lifecycle=True)

    def test_withdrawal_denies_gate_and_is_append_only(self):
        self._register(snapshot_id='snap-1',event_id='evt-1',effective_at='2026-11-09T10:01:30Z',actor_id='g',reason='publish',raw_response_ids=['raw'])
        withdraw_snapshot(self.conn,snapshot_id='snap-1',event_id='evt-2',effective_at='2026-11-09T10:03:00Z',actor_id='g',reason='source evidence invalidated')
        with self.assertRaises(SnapshotAccessDenied): self._gate(require_lifecycle=True)
        with self.assertRaises(sqlite3.IntegrityError): self.conn.execute("DELETE FROM a13_snapshot_lifecycle_events WHERE event_id='evt-2'")

    def test_supersession_preserves_old_and_registers_replacement(self):
        register_snapshot(self.conn,snapshot_id='snap-1',event_id='evt-1',effective_at='2026-11-09T10:01:30Z',actor_id='g',reason='publish',raw_response_ids=['raw'])
        supersede_snapshot(self.conn,old_snapshot_id='snap-1',new_snapshot_id='snap-2',old_event_id='evt-2',new_event_id='evt-3',
          effective_at='2026-11-09T10:04:00Z',actor_id='g',reason='corrected evidence',new_raw_response_ids=['raw'])
        self.assertEqual(lifecycle_state(self.conn,'snap-1').state,'superseded')
        self.assertEqual(lifecycle_state(self.conn,'snap-1').superseded_by,'snap-2')
        self.assertEqual(lifecycle_state(self.conn,'snap-2').state,'registered')
        with self.assertRaises(SnapshotAccessDenied): self._gate(snapshot='snap-1',require_lifecycle=True)

    def test_retention_expiry_denies_use_but_does_not_delete(self):
        register_snapshot(self.conn,snapshot_id='snap-1',event_id='evt-1',effective_at='2026-11-09T10:01:30Z',actor_id='g',reason='publish',raw_response_ids=['raw'])
        add_retention_policy(self.conn,policy_id='research-default',version=1,dataset_type='a1_discovery',retention_days=1,
          effective_at='2026-11-09T00:00:00Z',approval_reference='POL-1',created_by='governance')
        assign_retention_policy(self.conn,snapshot_id='snap-1',policy_id='research-default',version=1,
          assigned_at='2026-11-09T10:02:00Z',assigned_by='governance')
        result=assess_retention(self.conn,snapshot_id='snap-1',now='2026-11-11T00:00:00Z')
        self.assertTrue(result.expired); self.assertFalse(result.deletion_permitted); self.assertEqual(result.raw_response_ids,('raw',))
        with self.assertRaises(SnapshotAccessDenied): self._gate(now='2026-11-11T00:00:00Z',require_lifecycle=True,enforce_retention=True)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM a13_snapshot_decisions WHERE snapshot_id="snap-1"').fetchone()[0],1)

    def test_legal_hold_is_audited_and_does_not_reactivate_withdrawn_snapshot(self):
        register_snapshot(self.conn,snapshot_id='snap-1',event_id='evt-1',effective_at='2026-11-09T10:01:30Z',actor_id='g',reason='publish',raw_response_ids=['raw'])
        set_legal_hold(self.conn,snapshot_id='snap-1',event_id='evt-2',effective_at='2026-11-09T10:02:00Z',actor_id='legal',reason='preserve for review',held=True)
        self.assertTrue(lifecycle_state(self.conn,'snap-1').legal_hold)
        self.assertEqual(lifecycle_state(self.conn,'snap-1').state,'registered')
        self._gate(require_lifecycle=True)  # preservation hold does not itself withdraw research approval
        withdraw_snapshot(self.conn,snapshot_id='snap-1',event_id='evt-3',effective_at='2026-11-09T10:03:00Z',actor_id='g',reason='withdrawn')
        self.assertTrue(lifecycle_state(self.conn,'snap-1').legal_hold)
        with self.assertRaises(SnapshotAccessDenied): self._gate(require_lifecycle=True)

    def test_lifecycle_time_cannot_move_backwards(self):
        register_snapshot(self.conn,snapshot_id='snap-1',event_id='evt-1',effective_at='2026-11-09T10:01:30Z',actor_id='g',reason='publish',raw_response_ids=['raw'])
        with self.assertRaises(LifecycleError): withdraw_snapshot(self.conn,snapshot_id='snap-1',event_id='evt-2',
          effective_at='2026-11-09T10:00:00Z',actor_id='g',reason='bad time')


    def test_retention_enforcement_denies_missing_policy(self):
        register_snapshot(self.conn,snapshot_id='snap-1',event_id='evt-1',effective_at='2026-11-09T10:01:30Z',actor_id='g',reason='publish',raw_response_ids=['raw'])
        with self.assertRaises(SnapshotAccessDenied):
            self._gate(require_lifecycle=True,enforce_retention=True)

    def test_lifecycle_hash_chain_tampering_is_detected(self):
        register_snapshot(self.conn,snapshot_id='snap-1',event_id='evt-1',effective_at='2026-11-09T10:01:30Z',actor_id='g',reason='publish',raw_response_ids=['raw'])
        self.conn.execute('DROP TRIGGER trg_a13_lifecycle_no_update')
        self.conn.execute("UPDATE a13_snapshot_lifecycle_events SET reason='tampered' WHERE event_id='evt-1'")
        with self.assertRaises(SnapshotAccessDenied): self._gate(require_lifecycle=True)

    def test_retention_policy_is_immutable(self):
        add_retention_policy(self.conn,policy_id='p',version=1,dataset_type='a1_discovery',retention_days=30,
          effective_at='2026-11-09T00:00:00Z',approval_reference='POL',created_by='g')
        with self.assertRaises(sqlite3.IntegrityError): self.conn.execute("UPDATE a13_retention_policies SET retention_days=2 WHERE policy_id='p'")

if __name__=='__main__': unittest.main(verbosity=2)

import unittest
from unittest.mock import patch

from support import (TEST_ENGINE, audit_actions, drain_queue, insert_reviewed_evidence,
                     reset_database, seed_case, table_rows)

from fastapi import HTTPException
from sqlalchemy import select

from src.constants.workflow import Status
from src.models.tables import audit, cases, evidence, runs
from src.services import workflow
from src.services.closure import scrub_payload
from src.services.domain import SPEC, solve


class ClosureServiceTests(unittest.TestCase):
    def setUp(self):
        reset_database()
        self.case = seed_case(SPEC['sample'])
        self.case_id = self.case['id']

    def test_close_requires_reviewed_evidence(self):
        with self.assertRaises(HTTPException) as caught:
            workflow.close_case(self.case_id, 'operator')
        self.assertEqual(caught.exception.status_code, 409)
        with TEST_ENGINE.connect() as conn:
            row = conn.execute(select(cases).where(cases.c.id == self.case_id)).mappings().one()
            self.assertIsNone(row['closed_at'])
            self.assertEqual(row['payload'], SPEC['sample'])
        self.assertNotIn('close_case', audit_actions(self.case_id))

    def test_close_scrubs_case_but_keeps_evidence_and_review(self):
        run_id, evidence_id = insert_reviewed_evidence(self.case_id)
        before_evidence = next(row for row in table_rows(evidence) if row['id'] == evidence_id)

        closed = workflow.close_case(self.case_id, 'operator')
        self.assertTrue(closed['closed_at'])
        self.assertEqual(closed['closed_by'], 'operator')

        stored = next(row for row in table_rows(cases) if row['id'] == self.case_id)
        self.assertEqual(stored['payload'], scrub_payload(SPEC['sample']))
        for asset in stored['payload']['assets']:
            self.assertIsNone(asset['object_key'])

        # 旧证据、校验码与签署照常可查，内容不被结档改写。
        after_evidence = next(row for row in table_rows(evidence) if row['id'] == evidence_id)
        self.assertEqual(after_evidence['report'], before_evidence['report'])
        self.assertEqual(after_evidence['digest'], before_evidence['digest'])
        self.assertEqual(after_evidence['reviewer'], 'reviewer')
        self.assertIsNotNone(after_evidence['reviewed_at'])
        self.assertEqual(after_evidence['review_note'], before_evidence['review_note'])
        self.assertEqual(audit_actions(self.case_id).count('close_case'), 1)

    def test_new_run_rejected_after_closure_but_accepted_before(self):
        insert_reviewed_evidence(self.case_id)
        accepted = workflow.enqueue(self.case_id, 'operator')
        self.assertEqual(accepted['status'], Status.QUEUED)

        workflow.close_case(self.case_id, 'operator')
        with self.assertRaises(HTTPException) as caught:
            workflow.enqueue(self.case_id, 'operator')
        self.assertEqual(caught.exception.status_code, 409)

        # 结档前受理的任务仍在队列中，且没有多余任务登记。
        queued = [row for row in table_rows(runs) if row['case_id'] == self.case_id]
        self.assertEqual([row['status'] for row in queued].count(Status.QUEUED), 1)

    def test_queued_run_completes_after_closure_with_original_material(self):
        insert_reviewed_evidence(self.case_id)
        queued = workflow.enqueue(self.case_id, 'operator')
        workflow.close_case(self.case_id, 'operator')

        # 档案已脱敏，但排队任务保留的快照仍是原资料。
        queued_row = next(row for row in table_rows(runs) if row['id'] == queued['id'])
        self.assertEqual(queued_row['payload_snapshot'], SPEC['sample'])

        finished_ids = drain_queue()
        self.assertEqual(finished_ids, [queued['id']])
        report = next(row for row in table_rows(evidence) if row['run_id'] == queued['id'])['report']
        baseline = solve(SPEC['sample'], TEST_ENGINE)
        self.assertEqual(report, baseline)
        self.assertEqual(report['metrics'], SPEC['expect'])

        done_row = next(row for row in table_rows(runs) if row['id'] == queued['id'])
        self.assertEqual(done_row['status'], Status.COMPLETE)
        self.assertIsNone(done_row['payload_snapshot'])

    def test_running_run_keeps_snapshot_and_finishes_normally(self):
        insert_reviewed_evidence(self.case_id)
        running = workflow.enqueue(self.case_id, 'operator')
        job = workflow.claim()
        self.assertEqual(job[0], running['id'])
        run_id, token, snapshot = job
        self.assertEqual(snapshot, SPEC['sample'])

        workflow.close_case(self.case_id, 'operator')
        result = solve(snapshot, TEST_ENGINE)
        workflow.finish(run_id, token, result=result)

        row = next(row for row in table_rows(runs) if row['id'] == run_id)
        self.assertEqual(row['status'], Status.COMPLETE)
        self.assertIsNone(row['payload_snapshot'])
        evidence_row = next(row for row in table_rows(evidence) if row['run_id'] == run_id)
        self.assertEqual(evidence_row['report']['metrics'], SPEC['expect'])

    def test_close_is_idempotent_on_retry(self):
        insert_reviewed_evidence(self.case_id)
        first = workflow.close_case(self.case_id, 'operator')
        second = workflow.close_case(self.case_id, 'operator')
        self.assertEqual(first['closed_at'], second['closed_at'])
        self.assertEqual(audit_actions(self.case_id).count('close_case'), 1)

    def test_interrupted_closure_rolls_back_and_retry_succeeds_once(self):
        insert_reviewed_evidence(self.case_id)
        with patch('src.services.workflow.scrub_payload', side_effect=RuntimeError('中断')):
            with self.assertRaises(RuntimeError):
                workflow.close_case(self.case_id, 'operator')

        row = next(row for row in table_rows(cases) if row['id'] == self.case_id)
        self.assertIsNone(row['closed_at'])
        self.assertEqual(row['payload'], SPEC['sample'])
        self.assertNotIn('close_case', audit_actions(self.case_id))

        closed = workflow.close_case(self.case_id, 'operator')
        self.assertTrue(closed['closed_at'])
        self.assertEqual(audit_actions(self.case_id).count('close_case'), 1)

    def test_finished_run_snapshots_are_erased_at_closure(self):
        run_id, _ = insert_reviewed_evidence(self.case_id)
        finished_row = next(row for row in table_rows(runs) if row['id'] == run_id)
        self.assertIsNone(finished_row['payload_snapshot'])

        later = workflow.enqueue(self.case_id, 'operator')
        workflow.close_case(self.case_id, 'operator')
        queued_row = next(row for row in table_rows(runs) if row['id'] == later['id'])
        self.assertEqual(queued_row['payload_snapshot'], SPEC['sample'])

        drain_queue()
        self.assertTrue(all(row['payload_snapshot'] is None for row in table_rows(runs)))

    def test_closure_audited_with_actor(self):
        insert_reviewed_evidence(self.case_id)
        workflow.close_case(self.case_id, 'operator')
        with TEST_ENGINE.connect() as conn:
            event = conn.execute(select(audit).where(audit.c.action == 'close_case')).mappings().one()
            self.assertEqual(event['actor'], 'operator')
            self.assertEqual(event['reference'], str(self.case_id))


if __name__ == '__main__':
    unittest.main()

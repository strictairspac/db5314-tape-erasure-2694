import threading
import unittest

from support import (auth, drain_queue, insert_reviewed_evidence, make_client,
                     reset_database, seed_case, seed_user, table_rows, TEST_ENGINE)

from fastapi import HTTPException
from sqlalchemy import select

from src.models.tables import cases, runs
from src.services import workflow
from src.services.domain import SPEC


class ClosureRouteTests(unittest.TestCase):
    def setUp(self):
        reset_database()
        for username in ('operator', 'reviewer', 'viewer'):
            seed_user(username)
        self.client = make_client()
        self.case_id = seed_case(SPEC['sample'])['id']

    def test_reviewer_and_viewer_cannot_close(self):
        insert_reviewed_evidence(self.case_id)
        for username in ('reviewer', 'viewer'):
            response = self.client.post(f'/cases/{self.case_id}/close', headers=auth(self.client, username), json={})
            self.assertEqual(response.status_code, 403, response.text)
        with TEST_ENGINE.connect() as conn:
            self.assertIsNone(conn.execute(select(cases.c.closed_at).where(
                cases.c.id == self.case_id)).scalar_one())

    def test_operator_close_lifecycle_over_http(self):
        insert_reviewed_evidence(self.case_id)
        response = self.client.post(f'/cases/{self.case_id}/close',
                                    headers=auth(self.client, 'operator'), json={})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body['closed_at'])
        self.assertEqual(body['closed_by'], 'operator')
        for asset in body['payload']['assets']:
            self.assertIsNone(asset['object_key'])
            if asset['kind'] == 'tape':
                self.assertIsNone(asset['performer_id'])

    def test_case_list_exposes_closure_status(self):
        insert_reviewed_evidence(self.case_id)
        headers = auth(self.client, 'viewer')
        before = self.client.get('/cases', headers=headers).json()
        self.assertIsNone(before[0]['closed_at'])
        self.client.post(f'/cases/{self.case_id}/close', headers=auth(self.client, 'operator'), json={})
        after = self.client.get('/cases', headers=headers).json()
        self.assertTrue(after[0]['closed_at'])
        self.assertEqual(after[0]['closed_by'], 'operator')

    def test_run_request_after_closure_is_rejected_over_http(self):
        insert_reviewed_evidence(self.case_id)
        self.client.post(f'/cases/{self.case_id}/close', headers=auth(self.client, 'operator'), json={})
        response = self.client.post(f'/cases/{self.case_id}/runs', headers=auth(self.client, 'operator'))
        self.assertEqual(response.status_code, 409)
        self.assertIn('结档', response.json()['detail'])

    def test_acceptance_order_concurrent_close_and_enqueue(self):
        insert_reviewed_evidence(self.case_id)
        outcomes = []

        def enqueue_then_close_barrier(barrier):
            barrier.wait()
            try:
                outcomes.append(('enqueue', workflow.enqueue(self.case_id, 'operator')['status']))
            except HTTPException as exc:
                outcomes.append(('enqueue', exc.status_code))

        def close_at_barrier(barrier):
            barrier.wait()
            try:
                outcomes.append(('close', workflow.close_case(self.case_id, 'operator')['closed_at']))
            except HTTPException as exc:
                outcomes.append(('close', exc.status_code))

        barrier = threading.Barrier(2)
        threads = [threading.Thread(target=enqueue_then_close_barrier, args=(barrier,)),
                   threading.Thread(target=close_at_barrier, args=(barrier,))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        result = dict(outcomes)
        self.assertIn(result['enqueue'], ('queued', 409))
        if result['enqueue'] == 'queued':
            # 受理先于结档：任务保留并按原资料完成。
            self.assertNotEqual(result['close'], 409)
            finished = drain_queue()
            self.assertEqual(len(finished), 1)
            run = table_rows(runs)[-1]
            self.assertEqual(run['status'], 'complete')
            self.assertIsNone(run['payload_snapshot'])
        else:
            # 结档先于受理：新请求退回，队列保持为空。
            queued = [row for row in table_rows(runs)
                      if row['case_id'] == self.case_id and row['status'] == 'queued']
            self.assertEqual(queued, [])

    def test_close_unknown_case_returns_404(self):
        response = self.client.post('/cases/9999/close', headers=auth(self.client, 'operator'), json={})
        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()

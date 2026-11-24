import unittest

from sqlalchemy import select

from src.models.tables import cases, runs
from src.services.domain import SPEC

from support import (auth, drain_queue, make_client, reset_database,
                     seed_user, table_rows, TEST_ENGINE)


class EndToEndClosureFlowTests(unittest.TestCase):
    def setUp(self):
        reset_database()
        for username in ('operator', 'reviewer', 'viewer'):
            seed_user(username)
        self.client = make_client()

    def test_full_flow_review_then_close_then_reject(self):
        client = self.client
        operator = auth(client, 'operator')
        reviewer = auth(client, 'reviewer')
        viewer = auth(client, 'viewer')

        created = client.post('/cases', headers=operator,
                              json={'title': '端到端结档样例', 'payload': SPEC['sample']})
        self.assertEqual(created.status_code, 201, created.text)
        case_id = created.json()['id']

        # 无签署证据时结档被拒。
        self.assertEqual(client.post(f'/cases/{case_id}/close', headers=operator, json={}).status_code, 409)

        run = client.post(f'/cases/{case_id}/runs', headers=operator)
        self.assertEqual(run.status_code, 202, run.text)
        run_id = run.json()['id']

        drain_queue()

        evidence_list = client.get('/evidence', headers=viewer).json()
        evidence_id = next(row['id'] for row in evidence_list if row['run_id'] == run_id)

        # 复核员签署，经办员不能代替签署。
        self.assertEqual(client.post(f'/evidence/{evidence_id}/review', headers=operator,
                                     json={'note': '越权'}).status_code, 403)
        review = client.post(f'/evidence/{evidence_id}/review', headers=reviewer,
                             json={'note': '核对通过，同意结档'})
        self.assertEqual(review.status_code, 200, review.text)
        self.assertEqual(review.json()['reviewer'], 'reviewer')

        # 结档前再受理一个排队任务，模拟“还有任务在排队”。
        queued = client.post(f'/cases/{case_id}/runs', headers=operator)
        self.assertEqual(queued.status_code, 202, queued.text)
        queued_id = queued.json()['id']

        closed = client.post(f'/cases/{case_id}/close', headers=operator, json={})
        self.assertEqual(closed.status_code, 200, closed.text)
        self.assertTrue(closed.json()['closed_at'])

        # 库中不再有源路径和演员编号。
        case_row = conn_case(case_id)
        for asset in case_row['payload']['assets']:
            self.assertIsNone(asset['object_key'])
            if asset['kind'] == 'tape':
                self.assertIsNone(asset['performer_id'])

        # 关档后新请求退回。
        rejected = client.post(f'/cases/{case_id}/runs', headers=operator)
        self.assertEqual(rejected.status_code, 409)

        # 排队任务仍可完成，且完成后快照被擦除。
        drain_queue()
        queued_row = next(row for row in table_rows(runs) if row['id'] == queued_id)
        self.assertEqual(queued_row['status'], 'complete')
        self.assertIsNone(queued_row['payload_snapshot'])

        # 观察员仍可下载旧证据并看到签署。
        download = client.get(f'/evidence/{evidence_id}/download', headers=viewer)
        self.assertEqual(download.status_code, 200)
        self.assertIn('attachment', download.headers['content-disposition'])
        evidence_after = next(row for row in client.get('/evidence', headers=viewer).json()
                              if row['id'] == evidence_id)
        self.assertEqual(evidence_after['reviewer'], 'reviewer')
        self.assertIsNotNone(evidence_after['reviewed_at'])

        # 重复结档幂等，不新增审计。
        again = client.post(f'/cases/{case_id}/close', headers=operator, json={})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()['closed_at'], closed.json()['closed_at'])


def conn_case(case_id):
    with TEST_ENGINE.connect() as conn:
        return dict(conn.execute(select(cases).where(cases.c.id == case_id)).mappings().one())


if __name__ == '__main__':
    unittest.main()

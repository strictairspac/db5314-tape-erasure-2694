import unittest

from src.services import domain
from src.services.closure import scrub_payload
from src.services.domain import SPEC, solve
from src.services.tape_erasure import apply_erasure, catalog_copy

from support import TEST_ENGINE, reset_database


class SnapshotEquivalenceTests(unittest.TestCase):
    def setUp(self):
        reset_database()
        self.payload = SPEC['sample']
        self.cutoff = self.payload['cutoff']

    def _run_copy(self, payload):
        _, catalog = domain._parse(payload)
        with catalog_copy(catalog) as db:
            return apply_erasure(db, payload['cutoff'])

    def test_snapshot_and_original_follow_same_order_and_archive(self):
        original = self._run_copy(self.payload)
        snapshot = self._run_copy(self.payload)
        self.assertEqual(original[2].deletion_order, snapshot[2].deletion_order)
        self.assertEqual(original[3], snapshot[3])
        self.assertEqual(original[1].assets, snapshot[1].assets)

    def test_solve_report_is_identical_for_snapshot(self):
        first = solve(self.payload, TEST_ENGINE)
        second = solve(dict(self.payload), TEST_ENGINE)
        self.assertEqual(first, second)
        self.assertEqual(first['metrics'], SPEC['expect'])

    def test_scrubbed_payload_keeps_graph_and_removes_identity(self):
        scrubbed = scrub_payload(self.payload)
        self.assertEqual(scrubbed['references'], self.payload['references'])
        self.assertEqual(scrubbed['holds'], self.payload['holds'])
        self.assertEqual([row['asset_id'] for row in scrubbed['assets']],
                         [row['asset_id'] for row in self.payload['assets']])
        serialized = str(scrubbed)
        for asset in self.payload['assets']:
            self.assertNotIn(asset['object_key'], serialized)
        for asset in self.payload['assets']:
            if asset['kind'] == 'tape':
                self.assertNotIn(asset['performer_id'], serialized)

    def test_erasure_report_contains_no_source_paths_or_performer_ids(self):
        report = solve(self.payload, TEST_ENGINE)
        serialized = str(report)
        for asset in self.payload['assets']:
            self.assertNotIn(asset['object_key'], serialized)
            if asset['kind'] == 'tape':
                self.assertNotIn(asset['performer_id'], serialized)
        # 节点号取代源编号，证据仍可逐条核对。
        execution = next(section['rows'] for section in report['sections']
                         if section['title'] == '已执行的擦除顺序')
        self.assertTrue(all(row['节点'].startswith('N') for row in execution[1:]))


if __name__ == '__main__':
    unittest.main()

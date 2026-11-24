import unittest

from src.services.closure import SCRUBBED_ASSET_FIELDS, is_closed, scrub_asset, scrub_payload
from src.services.domain import SPEC


class ScrubPayloadTests(unittest.TestCase):
    def setUp(self):
        self.payload = SPEC['sample']

    def test_removes_object_key_and_performer_id_only(self):
        clean = scrub_payload(self.payload)
        self.assertEqual(len(clean['assets']), len(self.payload['assets']))
        for row in clean['assets']:
            self.assertIsNone(row['object_key'])
            if row['kind'] == 'tape':
                self.assertIsNone(row['performer_id'])
            else:
                self.assertNotIn('performer_id', row)
        # 脱敏字段集合明确限定为源路径和演员编号。
        self.assertEqual(SCRUBBED_ASSET_FIELDS, ('object_key', 'performer_id'))

    def test_keeps_retention_review_fields(self):
        clean = scrub_payload(self.payload)
        self.assertEqual(clean['cutoff'], self.payload['cutoff'])
        self.assertEqual(clean['references'], self.payload['references'])
        self.assertEqual(clean['holds'], self.payload['holds'])
        for before, after in zip(self.payload['assets'], clean['assets']):
            self.assertEqual(before['asset_id'], after['asset_id'])
            self.assertEqual(before['kind'], after['kind'])
            if before['kind'] == 'tape':
                self.assertEqual(before['expires_on'], after['expires_on'])
                self.assertEqual(before['track'], after['track'])
                self.assertEqual(before['duration_seconds'], after['duration_seconds'])

    def test_does_not_mutate_input(self):
        scrub_payload(self.payload)
        tape = next(row for row in self.payload['assets'] if row['kind'] == 'tape')
        self.assertTrue(tape['object_key'])
        self.assertTrue(tape['performer_id'])

    def test_scrub_asset_is_idempotent(self):
        tape = next(row for row in self.payload['assets'] if row['kind'] == 'tape')
        once = scrub_asset(tape)
        twice = scrub_asset(once)
        self.assertEqual(once, twice)

    def test_is_closed_predicate(self):
        self.assertFalse(is_closed({}))
        self.assertFalse(is_closed({'closed_at': None}))
        self.assertTrue(is_closed({'closed_at': '2026-11-24T10:00:00+00:00'}))
        self.assertFalse(is_closed(None))


if __name__ == '__main__':
    unittest.main()

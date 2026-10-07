"""Derived share resolution only; no browser, business database, or live state."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pdd_monitor import capture_control
from pdd_monitor import competitor_registry as registry


SHARE = 'https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC_SHARE'
SOURCE = 'https://mobile.yangkeduo.com/mall_page.html?mall_sn=SYNTHETIC_STORE'
NAME = 'SYNTHETIC VERIFIED SHOP'


class IntakeProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pdd_intake_projection_')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pending = registry.register_target(self.root, 'USER ENTERED NAME', SHARE)['target']
        self.stable = registry.register_target(self.root, None, SOURCE)['target']

    def receipt(self, *, target=None, original=SHARE, name=NAME, stamp='2026-01-02T01:00:00Z',
                evidence_changes=None, receipt_changes=None):
        target = target or self.stable
        evidence = {'userShareUrl': original, 'shopName': name, 'sourceUrl': target['source_url'],
                    'header': '搜索店铺商品\n' + name + '\n全部商品', 'observedAt': stamp,
                    'selectedMarkup': '<li class="current_abc">上新</li>'}
        evidence.update(evidence_changes or {})
        raw = json.dumps(evidence, ensure_ascii=False).encode('utf-8')
        digest = hashlib.sha256(raw).hexdigest()
        folder = self.root / 'state/competitors/intakes'
        evidence_path = folder / 'evidence' / (digest + '.json')
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_bytes(raw)
        value = {'schema_version': 1, 'original_input_url': original,
                 'resolved_storefront_url': evidence['sourceUrl'], 'observed_shop_name': name,
                 'observed_at': stamp, 'identity_kind': target['identity_kind'],
                 'stable_identifier': target['stable_identifier'], 'shop_id': target['shop_id'],
                 'stable_target_id': target['target_id'],
                 'verification_method': 'authorized_browser_visible_storefront', 'selected_sort': '上新',
                 'host_evidence': {'file': 'evidence/' + digest + '.json', 'sha256': digest, 'bytes': len(raw)}}
        value.update(receipt_changes or {})
        canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        receipt_path = folder / (hashlib.sha256(canonical).hexdigest() + '.json')
        receipt_path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return receipt_path, evidence_path

    def project(self, runs=None):
        before = {str(path): path.read_bytes() for path in self.root.rglob('*.json')}
        rows, digests = registry.build_targets(self.root, runs or [])
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.root.rglob('*.json')})
        self.assertFalse((self.root / 'data').exists())
        return next(row for row in rows if row['target_id'] == self.pending['target_id']), rows, digests

    def run_row(self, source=SOURCE):
        return {'shop_id': self.stable['shop_id'], 'shop_name': NAME, 'source_url': source,
                'run_id': 'run_SYNTHETIC', 'snapshot_metadata': {'sourceUrl': source},
                'observed_to_epoch': 1, 'status': 'complete', 'end_boundary_observed': 1, 'card_count': 20}

    def test_valid_intake_links_pending_projection_without_changing_original_url_or_registration(self):
        receipt, host = self.receipt()
        row, _, digests = self.project()
        self.assertEqual(row['source_url'], SHARE)
        self.assertEqual(row['target_id'], self.pending['target_id'])
        self.assertEqual(row['display_name'], NAME)
        self.assertEqual(row['registered_display_name'], 'USER ENTERED NAME')
        self.assertEqual(row['observed_shop_name'], NAME)
        self.assertEqual(row['shop_id'], self.stable['shop_id'])
        self.assertEqual(row['resolved_source_url'], SOURCE)
        self.assertEqual(row['resolved_target_id'], self.stable['target_id'])
        self.assertEqual(row['identity_status'], 'stable_url')
        self.assertEqual(row['status'], 'pending_capture')
        self.assertEqual(row['run_count'], 0)
        self.assertTrue(row['can_configure_tracking'])
        self.assertEqual(row['verification_status'], 'verified')
        self.assertEqual(row['verification_receipt_sha256'], [receipt.stem])
        self.assertEqual(row['verification_host_sha256'], [host.stem])
        for path in (receipt, host):
            self.assertEqual(digests[str(path)], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_completed_capture_marks_both_views_observed_without_synthetic_extra_shop(self):
        self.receipt()
        row, rows, _ = self.project([self.run_row()])
        self.assertEqual(row['status'], 'observed')
        self.assertEqual(row['run_count'], 1)
        self.assertEqual(row['latest_run_id'], 'run_SYNTHETIC')
        self.assertEqual(row['observed_shop_name'], NAME)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(item['shop_id'] == self.stable['shop_id'] for item in rows))

    def test_absent_evidence_and_same_named_unrelated_share_do_not_resolve(self):
        row, _, _ = self.project([self.run_row()])
        self.assertIsNone(row['shop_id'])
        self.assertEqual(row['status'], 'needs_identity')
        self.receipt(original=SHARE + 'OTHER')
        row, _, _ = self.project([self.run_row()])
        self.assertIsNone(row['shop_id'])
        self.assertNotIn('resolved_target_id', row)

    def test_multiple_intakes_for_one_typed_store_choose_latest_provenance_and_keep_all_digests(self):
        older = self.receipt(name='OLD NAME', stamp='2026-01-01T01:00:00Z')
        newer = self.receipt()
        row, _, digests = self.project()
        self.assertEqual(row['display_name'], NAME)
        self.assertEqual(row['verification_observed_at'], '2026-01-02T01:00:00Z')
        self.assertEqual(len(row['verification_receipt_sha256']), 2)
        self.assertTrue(all(str(path) in digests for path in (*older, *newer)))

    def test_same_original_share_with_two_typed_resolutions_stays_unbound(self):
        self.receipt()
        other = registry.register_target(self.root, NAME, SOURCE.replace('SYNTHETIC_STORE', 'OTHER'))['target']
        self.receipt(target=other)
        row, _, _ = self.project([self.run_row()])
        self.assertIsNone(row['shop_id'])
        self.assertTrue(row['identity_conflict'])
        self.assertFalse(row['can_configure_tracking'])
        self.assertNotIn('resolved_target_id', row)

    def test_missing_stable_registration_cannot_be_invented_from_receipt(self):
        unregistered = registry._target(NAME, SOURCE.replace('SYNTHETIC_STORE', 'UNREGISTERED'))
        self.receipt(target=unregistered)
        row, _, _ = self.project()
        self.assertIsNone(row['shop_id'])
        self.assertEqual(row['verification_status'], 'needs_review')

    def test_tampered_receipt_or_host_bytes_do_not_create_a_binding(self):
        for which in (0, 1):
            with self.subTest(which=which):
                paths = self.receipt()
                saved = paths[which].read_bytes()
                if which == 0:
                    value = json.loads(saved)
                    value['observed_shop_name'] = 'ALTERED'
                    paths[which].write_text(json.dumps(value), encoding='utf-8')
                else:
                    paths[which].write_bytes(saved + b' ')
                row, _, _ = self.project()
                self.assertIsNone(row['shop_id'])
                self.assertEqual(row['verification_status'], 'needs_review')
                paths[which].write_bytes(saved)

    def test_rehashed_bad_name_sort_time_schema_host_or_original_evidence_is_still_rejected(self):
        cases = [({'header': NAME + ' PLUS'}, {}),
                 ({'selectedMarkup': '<li class="current_x">销量</li>'}, {}),
                 ({'userShareUrl': SHARE + 'OTHER'}, {}),
                 ({'sourceUrl': SOURCE.replace('mobile.', 'www.')}, {}),
                 ({}, {'schema_version': True}),
                 ({}, {'verification_method': 'unverified_input'}),
                 ({}, {'host_evidence': {'file': '../elsewhere.json', 'sha256': 'a' * 64, 'bytes': 1}})]
        for index, (evidence, receipt) in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory(prefix='pdd_invalid_projection_') as temporary:
                original = self.root
                try:
                    self.root = Path(temporary)
                    registry.register_target(self.root, 'USER ENTERED NAME', SHARE)
                    registry.register_target(self.root, None, SOURCE)
                    self.receipt(evidence_changes=evidence, receipt_changes=receipt)
                    row, _, _ = self.project()
                    self.assertIsNone(row['shop_id'])
                    self.assertFalse(row['can_configure_tracking'])
                finally:
                    self.root = original
        self.receipt(stamp='2999-01-01T01:00:00Z')
        self.assertIsNone(self.project()[0]['shop_id'])

    def test_existing_typed_history_conflict_clears_any_derived_resolution(self):
        self.receipt()
        row, _, _ = self.project([self.run_row(SOURCE.replace('mall_sn=SYNTHETIC_STORE', 'mall_id=101'))])
        self.assertIsNone(row['shop_id'])
        self.assertTrue(row['identity_conflict'])
        self.assertNotIn('resolved_target_id', row)
        self.assertNotIn('resolved_source_url', row)
        self.assertEqual(row['verification_status'], 'needs_review')

    def test_evidence_change_during_verification_invalidates_export_instead_of_using_stale_hash(self):
        _, host = self.receipt()
        validate = capture_control._verified_intake_name
        def changed(*args):
            result = validate(*args)
            host.write_bytes(host.read_bytes() + b' ')
            return result
        with patch.object(capture_control, '_verified_intake_name', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'changed during target export'):
                registry.build_targets(self.root, [])


if __name__ == '__main__':
    unittest.main()

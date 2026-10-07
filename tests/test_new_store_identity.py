"""New-store identity boundaries in temporary state, without browser or business writes."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pdd_monitor import capture_control
from pdd_monitor import competitor_registry as registry
from pdd_monitor.shop_tracking import save_tracking

URL = 'https://mobile.yangkeduo.com/mall_page.html?mall_id=101'
SHARE = 'https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC_SHARE'


class NewStoreIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pdd_new_store_SYNTHETIC_')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def register(self, name=None, url=URL):
        return registry.register_target(self.root, name, url)

    def resolve(self, target):
        with patch.object(capture_control, '_known_shop', side_effect=ValueError('Unknown shop: SYNTHETIC no history')):
            result = capture_control._resolve_shop(self.root, target['shop_id'])
        self.assertFalse((self.root / 'data').exists())
        return result

    def receipt(self, target, *, name='SYNTHETIC VERIFIED SHOP', stamp='2026-01-01T01:00:00Z', evidence_changes=None, receipt_changes=None):
        evidence = {'shopName': name, 'header': '搜索店铺商品\n' + name + '\n全部商品', 'sourceUrl': target['source_url'],
                    'userShareUrl': SHARE, 'observedAt': stamp, 'sortElements': [{'html': '<li class="current_abc">上新</li>'}]}
        evidence.update(evidence_changes or {})
        raw = json.dumps(evidence, ensure_ascii=False).encode('utf-8')
        digest = hashlib.sha256(raw).hexdigest()
        root = self.root / 'state/competitors/intakes'
        evidence_path = root / 'evidence' / (digest + '.json')
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_bytes(raw)
        receipt = {'schema_version': 1, 'original_input_url': SHARE, 'resolved_storefront_url': evidence['sourceUrl'],
                   'observed_shop_name': name, 'observed_at': stamp, 'identity_kind': target['identity_kind'],
                   'stable_identifier': target['stable_identifier'], 'shop_id': target['shop_id'], 'stable_target_id': target['target_id'],
                   'verification_method': 'authorized_browser_visible_storefront', 'selected_sort': '上新',
                   'host_evidence': {'file': 'evidence/' + digest + '.json', 'sha256': digest, 'bytes': len(raw)},
                   'website_collection_performed': False, 'business_databases_written': False, 'pending_target_rewritten': False, 'tracking_changed': False}
        receipt.update(receipt_changes or {})
        canonical = json.dumps(receipt, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        path = root / (hashlib.sha256(canonical).hexdigest() + '.json')
        path.write_text(json.dumps(receipt, ensure_ascii=False), encoding='utf-8')
        return path, evidence_path

    def test_url_only_then_verified_intake_resolves_name_without_rewriting_registration_or_source(self):
        registered = self.register(url=URL + '&refer_share_channel=OLD')
        target = registered['target']
        self.assertEqual(target['display_name'], '待核实店名')
        before = Path(registered['path']).read_bytes()
        self.receipt(target, evidence_changes={'sourceUrl': URL + '&refer_share_channel=NEW'})
        state_before = {str(p): p.read_bytes() for p in (self.root / 'state').rglob('*.json')}
        resolved = self.resolve(target)
        self.assertEqual(resolved['shop_name'], 'SYNTHETIC VERIFIED SHOP')
        self.assertEqual(resolved['source_url'], target['source_url'])
        self.assertEqual(resolved['identity_basis'], 'verified_intake')
        self.assertEqual(Path(registered['path']).read_bytes(), before)
        self.assertEqual(state_before, {str(p): p.read_bytes() for p in (self.root / 'state').rglob('*.json')})

    def test_placeholder_without_verified_intake_stops_before_browser_or_capture_attempt(self):
        target = self.register()['target']
        with self.assertRaisesRegex(ValueError, 'visible storefront name'):
            self.resolve(target)
        self.assertFalse((self.root / 'state/capture_jobs').exists())
        self.assertFalse((self.root / 'state/collection_attempts').exists())

    def test_explicit_registration_name_remains_a_preflight_name_not_verified_intake(self):
        registered = self.register(name='SYNTHETIC USER NAME')
        resolved = self.resolve(registered['target'])
        self.assertEqual(resolved['shop_name'], 'SYNTHETIC USER NAME')
        self.assertEqual(resolved['identity_basis'], 'registered_stable_url')
        repeated = self.register(name='UNVERIFIED CHANGED NAME')
        self.assertEqual(repeated['status'], 'unchanged')
        self.assertEqual(self.resolve(registered['target'])['shop_name'], 'SYNTHETIC USER NAME')

    def test_receipt_or_host_content_tamper_cannot_authorize_a_name(self):
        target = self.register()['target']
        receipt, evidence = self.receipt(target)
        original = evidence.read_bytes()
        evidence.write_bytes(original + b' ')
        with self.assertRaisesRegex(ValueError, 'host evidence hash'):
            self.resolve(target)
        evidence.write_bytes(original)
        value = json.loads(receipt.read_text(encoding='utf-8'))
        value['observed_shop_name'] = 'CHANGED'
        receipt.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'receipt hash'):
            self.resolve(target)

    def test_rehashed_but_invalid_visible_name_sort_time_or_source_is_rejected(self):
        cases = [({'header': 'SYNTHETIC VERIFIED SHOP PLUS'}, {}), ({'shopName': 'DIFFERENT'}, {}),
                 ({'sortElements': [{'html': '<li class="current_x">销量</li>'}]}, {}),
                 ({'sourceUrl': URL.replace('101', '202')}, {}),
                 ({'sourceUrl': URL.replace('mobile.yangkeduo.com', 'www.yangkeduo.com')}, {}),
                 ({'userShareUrl': SHARE + 'OTHER'}, {}), ({}, {'identity_kind': 'mall_sn'}),
                 ({}, {'verification_method': 'unverified_user_input'}), ({}, {'selected_sort': '销量'}), ({}, {'schema_version': True})]
        for index, (evidence_changes, receipt_changes) in enumerate(cases):
            with self.subTest(index=index):
                previous = self.root
                self.root = previous / str(index)
                try:
                    target = self.register()['target']
                    self.receipt(target, evidence_changes=evidence_changes, receipt_changes=receipt_changes)
                    with self.assertRaises(ValueError):
                        self.resolve(target)
                finally:
                    self.root = previous
        for stamp in ['2999-01-01T00:00:00Z', '2026-01-01T00:00:00']:
            with self.subTest(stamp=stamp):
                previous = self.root
                self.root = previous / ('future' if '2999' in stamp else 'naive')
                try:
                    target = self.register()['target']
                    self.receipt(target, stamp=stamp)
                    with self.assertRaisesRegex(ValueError, 'observation time'):
                        self.resolve(target)
                finally:
                    self.root = previous

    def test_host_evidence_path_cannot_escape_or_substitute_an_unbound_file(self):
        target = self.register()['target']
        self.receipt(target, receipt_changes={'host_evidence': {'file': '../outside.json', 'sha256': 'a' * 64, 'bytes': 10}})
        with self.assertRaisesRegex(ValueError, 'reference invalid'):
            self.resolve(target)

    def test_newest_verified_name_is_selected_but_simultaneous_conflicting_names_stop(self):
        target = self.register()['target']
        self.receipt(target, name='SYNTHETIC OLD NAME')
        self.receipt(target, name='SYNTHETIC NEW NAME', stamp='2026-01-02T01:00:00Z')
        self.assertEqual(self.resolve(target)['shop_name'], 'SYNTHETIC NEW NAME')
        self.receipt(target, name='SYNTHETIC CONFLICT', stamp='2026-01-02T01:00:00Z')
        with self.assertRaisesRegex(ValueError, 'names conflict'):
            self.resolve(target)

    def test_foreign_intake_cannot_supply_a_same_named_shop_identity(self):
        target = self.register()['target']
        foreign = registry._target('SYNTHETIC VERIFIED SHOP', URL.replace('101', '202'))
        self.receipt(foreign)
        with self.assertRaisesRegex(ValueError, 'visible storefront name'):
            self.resolve(target)

    def test_observed_history_remains_authoritative(self):
        target = self.register()['target']
        self.receipt(target, name='NEW INTAKE NAME')
        latest = {'shop_name': 'HISTORICAL SHOP', 'source_url': URL, 'snapshot_json': json.dumps({'sourceUrl': URL})}
        with patch.object(capture_control, '_known_shop', return_value=(latest, latest)):
            resolved = capture_control._resolve_shop(self.root, target['shop_id'])
        self.assertEqual(resolved['shop_name'], 'HISTORICAL SHOP')
        self.assertEqual(resolved['identity_basis'], 'observed_snapshot')

    def test_same_numeric_value_different_typed_identity_cannot_be_registered_in_either_order(self):
        for first, second in [(URL, URL.replace('mall_id', 'mall_sn')), (URL.replace('mall_id', 'mall_sn'), URL)]:
            with self.subTest(first=first), tempfile.TemporaryDirectory(prefix='pdd_typed_SYNTHETIC_') as root:
                registered = registry.register_target(root, 'SAME DISPLAY NAME', first)
                before = Path(registered['path']).read_bytes()
                with self.assertRaisesRegex(ValueError, 'conflicting typed'):
                    registry.register_target(root, 'SAME DISPLAY NAME', second)
                self.assertEqual(Path(registered['path']).read_bytes(), before)
                self.assertEqual(len(list((Path(root) / 'state/competitors/targets').glob('*.json'))), 1)

    def test_legacy_typed_collision_disables_both_tracking_projections_and_preserves_old_settings(self):
        first = self.register(name='SYNTHETIC')['target']
        saved = save_tracking(self.root, first['shop_id'], 'enabled', 0)
        setting_path = Path(saved['path'])
        before = setting_path.read_bytes()
        second = registry._target('SYNTHETIC', URL.replace('mall_id', 'mall_sn'))
        second['registered_at'] = first['registered_at']
        registry.write_new_json(self.root / 'state/competitors/targets' / (second['target_id'] + '.json'), second)
        for runs in [[], [{'shop_id': first['shop_id'], 'shop_name': 'SYNTHETIC', 'source_url': URL,
                           'snapshot_metadata': {'sourceUrl': URL}, 'observed_to_epoch': 1}]]:
            rows, _ = registry.build_targets(self.root, runs)
            self.assertTrue(rows)
            for row in rows:
                self.assertTrue(row['identity_conflict'])
                self.assertIsNone(row['shop_id'])
                self.assertFalse(row['can_configure_tracking'])
                self.assertNotEqual(row['tracking_status'], 'enabled')
                with self.assertRaisesRegex(ValueError, '身份尚未核实'):
                    registry.set_target_tracking(self.root, runs, row['target_id'], 'enabled', 1)
        self.assertEqual(setting_path.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'conflicting mall_id/mall_sn'):
            self.resolve(first)


if __name__ == '__main__':
    unittest.main()

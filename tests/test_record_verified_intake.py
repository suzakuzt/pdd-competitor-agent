"""Host-evidence intake tests. All target and receipt writes are temporary."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE_PROJECT = Path(os.environ.get('PDD_TEST_PROJECT', Path(__file__).resolve().parents[1]))
SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/record_verified_intake.py'


def shop_id(number):
    return 'shop_' + hashlib.sha256(('id:' + str(number)).encode()).hexdigest()[:24]


class VerifiedIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pdd_intake_SYNTHETIC_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        shutil.copytree(SOURCE_PROJECT / 'pdd_monitor', self.project / 'pdd_monitor',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copyfile(SOURCE_PROJECT / 'AGENTS.md', self.project / 'AGENTS.md')
        self.evidence = self.root / 'host.json'
        self.share = 'https://p.pinduoduo.com/SYNTHETIC-share-a'
        self.write_evidence()

    def write_evidence(self, number=1001, share=None, **changes):
        self.document = {'userShareUrl': share or self.share,
            'sourceUrl': f'https://mobile.yangkeduo.com/mall_page.html?mall_id={number}',
            'shopName': '合成同名店铺', 'header': '搜索店铺商品\n合成同名店铺\n全部商品',
            'observedAt': '2026-01-01T01:00:00Z',
            'sortElements': [{'html': '<li class="optItem current_abc">上新</li>'}],
            'synthetic': True}
        self.document.update(changes)
        self.evidence.write_text(json.dumps(self.document, ensure_ascii=False), encoding='utf-8')

    def call(self, number=1001, share=None, success=True):
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(SCRIPT),
            '--project', str(self.project), '--evidence', str(self.evidence),
            '--original-url', share or self.share, '--expected-shop-id', shop_id(number)],
            capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0 if success else 1, result.stderr + result.stdout)
        self.assertFalse((self.project / 'data').exists(), 'Intake must not create business databases')
        return json.loads(result.stdout)

    def register(self, url, name='合成同名店铺'):
        code = ('import sys,json;sys.path.insert(0,sys.argv[1]);'
                'from pdd_monitor.competitor_registry import register_target;'
                'print(json.dumps(register_target(sys.argv[1],sys.argv[2],sys.argv[3])))')
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', code,
            str(self.project), name, url], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def targets(self):
        return list((self.project / 'state/competitors/targets').glob('*.json'))

    def test_normal_evidence_hash_and_idempotent_registration(self):
        original = self.evidence.read_bytes()
        result = self.call()
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['registration_status'], 'registered')
        receipt_path = Path(result['receipt_path'])
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        canonical = json.dumps(receipt, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), receipt_path.stem)
        self.assertEqual(receipt['host_evidence']['sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual((receipt_path.parent / receipt['host_evidence']['file']).read_bytes(), original)
        self.assertEqual(receipt['verification_method'], 'authorized_browser_visible_storefront')
        before = {str(p): p.read_bytes() for p in (self.project / 'state').rglob('*.json')}
        repeated = self.call()
        self.assertEqual(repeated['status'], 'unchanged')
        self.assertEqual(repeated['registration_status'], 'unchanged')
        self.assertEqual(before, {str(p): p.read_bytes() for p in (self.project / 'state').rglob('*.json')})
        self.assertEqual(len(self.targets()), 1)

    def test_same_share_conflicting_resolved_identity_is_rejected(self):
        self.call()
        original_targets = {p.name: p.read_bytes() for p in self.targets()}
        self.write_evidence(number=1002)
        result = self.call(number=1002, success=False)
        self.assertIn('conflicting verified resolution', result['message'])
        self.assertEqual(original_targets, {p.name: p.read_bytes() for p in self.targets()})

    def test_share_query_identity_never_substitutes_for_resolved_storefront(self):
        share = 'https://p.pinduoduo.com/SYNTHETIC-share?mall_id=9999'
        self.write_evidence(share=share)
        result = self.call(share=share)
        self.assertEqual(result['shop_id'], shop_id(1001))
        self.write_evidence(share=share, sourceUrl=share)
        self.call(number=9999, share=share, success=False)
        self.assertEqual(len(self.targets()), 1)

    def test_same_name_different_shops_remain_separate(self):
        first = self.call()
        share2 = 'https://p.pinduoduo.com/SYNTHETIC-share-b'
        self.write_evidence(number=1002, share=share2)
        second = self.call(number=1002, share=share2)
        self.assertNotEqual(first['target_id'], second['target_id'])
        self.assertNotEqual(first['shop_id'], second['shop_id'])
        self.assertEqual(len(self.targets()), 2)

    def test_existing_pending_short_target_is_preserved_and_disclosed(self):
        pending = self.register(self.share)
        old = Path(pending['path']).read_bytes()
        result = self.call()
        self.assertEqual(Path(pending['path']).read_bytes(), old)
        self.assertEqual(result['existing_pending_target_ids'], [pending['target']['target_id']])
        self.assertIn('仍保持待核验', result['pending_note'])
        self.assertEqual(len(self.targets()), 2)
        self.assertFalse(json.loads(Path(result['receipt_path']).read_text(encoding='utf-8'))['pending_target_rewritten'])

    def test_typed_stable_identifier_namespace_collision_is_rejected(self):
        self.register('https://mobile.yangkeduo.com/mall_page.html?mall_sn=1001')
        before = {p.name: p.read_bytes() for p in self.targets()}
        result = self.call(success=False)
        self.assertIn('typed identity conflicts', result['message'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.targets()})

    def test_wrong_expected_identity_or_non_matching_host_evidence_rejected(self):
        for changes, number, share in [({}, 1002, None),
                ({'header': '另一家店'}, 1001, None),
                ({'sortElements': [{'html': '<li>上新</li>'}]}, 1001, None),
                ({'selectedMarkup': '<li aria-selected="true">销量</li>'}, 1001, None),
                ({'observedAt': '2026-01-01T01:00:00'}, 1001, None),
                ({'userShareUrl': 'https://p.pinduoduo.com/other'}, 1001, None)]:
            with self.subTest(changes=changes, number=number):
                self.write_evidence(**changes)
                self.call(number=number, share=share, success=False)
                self.assertEqual(self.targets(), [])

    def test_original_stable_url_cannot_resolve_to_different_identity(self):
        original = 'https://mobile.yangkeduo.com/mall_page.html?mall_id=1002'
        self.write_evidence(share=original)
        self.call(share=original, success=False)
        self.assertEqual(self.targets(), [])

    def test_private_host_field_refused_and_existing_receipt_tampering_rejected(self):
        self.write_evidence(nested={'owner_token': 'SYNTHETIC-NOT-REAL'})
        self.call(success=False)
        self.assertEqual(self.targets(), [])
        self.write_evidence()
        result = self.call()
        path = Path(result['receipt_path'])
        damaged = json.loads(path.read_text(encoding='utf-8'))
        damaged['selected_sort'] = '销量'
        path.write_text(json.dumps(damaged), encoding='utf-8')
        repeated = self.call(success=False)
        self.assertIn('content hash', repeated['message'])


if __name__ == '__main__':
    unittest.main()

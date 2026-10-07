"""New-shop lifecycle regression: only synthetic temporary projects and local files."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pdd_monitor import capture_control as control
from pdd_monitor import collection_log as journal
from pdd_monitor.competitor_registry import register_target
from pdd_monitor.shop_tracking import read_tracking
from pdd_monitor.store import _connect_readonly, import_snapshot, initialize, shop_identity_evidence


URL = 'https://mobile.yangkeduo.com/mall_page.html?mall_id=9000000009'
OTHER_URL = 'https://mobile.yangkeduo.com/mall_page.html?mall_id=9000000008'
NAME = 'SYNTHETIC NEW SHOP'
SHOP = shop_identity_evidence({'sourceUrl': URL})['shop_id']
OTHER_SHOP = shop_identity_evidence({'sourceUrl': OTHER_URL})['shop_id']


class NewShopCaptureControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pdd_new_shop_SYNTHETIC_')
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        (self.project / 'AGENTS.md').write_text('SYNTHETIC PROJECT', encoding='utf-8')
        initialize(self.project / 'data')

    def register(self, url=URL, name=NAME):
        return register_target(self.project, name=name, source_url=url)['target']

    def hashes(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (self.project / 'data').glob('*.sqlite3')}

    def run_count(self):
        with closing(_connect_readonly(self.project / 'data')) as connection:
            return connection.execute('SELECT COUNT(*) FROM runs').fetchone()[0]

    def assert_unconfigured(self):
        self.assertEqual(read_tracking(self.project, SHOP)[0]['status'], 'unconfigured')
        self.assertFalse((self.project / 'state/new_arrivals').exists())

    def sealed_partial(self, job):
        directory = Path(job['session_directory'])
        directory.mkdir(parents=True)
        (directory / 'session.json').write_text(json.dumps({
            'attemptId': job['attempt_id'], 'phase': 'partial', 'operation': None,
            'sessionId': 'capture_synthetic', 'progress': {'batches': 1, 'cards': 2},
            'retry': {'allowed': True},
        }), encoding='utf-8')
        (directory / 'preserved-batch.txt').write_text('SYNTHETIC UNCHANGED PREFIX', encoding='utf-8')
        return directory

    def observed(self, url=OTHER_URL, name='SYNTHETIC OLD SHOP'):
        snapshot = {'shopName': name, 'sourceUrl': url, 'sort': '上新',
                    'observedFrom': '2026-10-04T01:00:00Z', 'observedTo': '2026-10-04T01:00:00Z',
                    'status': 'complete', 'endBoundaryObserved': True,
                    'rows': [{'viewOrder': 1, 'title': 'SYNTHETIC RAW CARD', 'salesRaw': None, 'imageUrl': None}]}
        path = self.project / 'synthetic_snapshot.json'
        path.write_text(json.dumps(snapshot), encoding='utf-8')
        return import_snapshot(self.project / 'data', path)

    def test_registered_zero_run_plan_is_pending_and_read_only(self):
        self.register(); before = self.hashes()
        result = control.plan(self.project, SHOP)
        self.assertEqual(result['status'], 'plan_only')
        self.assertEqual(result['target_status'], 'pending_capture')
        self.assertEqual(result['identity_basis'], 'registered_stable_url')
        self.assertEqual((result['shop_name'], result['source_url']), (NAME, URL))
        self.assertIsNone(result['latest_run_id']); self.assertIsNone(result['latest_card_count'])
        self.assertIsNone(result['latest_status']); self.assertIsNone(result['reference_run_id'])
        self.assertFalse(result['website_collection_performed']); self.assertEqual(result['jobs'], [])
        self.assertEqual(before, self.hashes()); self.assertEqual(self.run_count(), 0)
        self.assert_unconfigured()

    def test_registered_zero_run_begin_failed_and_sop_retry(self):
        self.register(); before = self.hashes()
        first = control.begin(self.project, shop_id=SHOP)
        self.assertTrue(first['acquired'])
        directory = self.sealed_partial(first)
        control.abandon(self.project, first['job_id'], 'SYNTHETIC interrupted and sealed')
        child = control.begin(self.project, shop_id=SHOP, retry_job=first['job_id'])
        self.assertTrue(child['acquired']); self.assertEqual(child['fresh_retry_index'], 1)
        self.assertEqual(child['parent_job_id'], first['job_id'])
        self.assertEqual((child['shop_id'], child['source_url'], child['shop_name']), (SHOP, URL, NAME))
        self.assertNotEqual(child['attempt_id'], first['attempt_id'])
        self.assertNotEqual(child['session_directory'], first['session_directory'])
        self.assertEqual(child['session_options']['parentSession']['directory'], str(directory))
        self.assertEqual((directory / 'preserved-batch.txt').read_text(), 'SYNTHETIC UNCHANGED PREFIX')
        self.assertEqual(self.run_count(), 0); self.assertEqual(before, self.hashes()); self.assert_unconfigured()

    def test_retry_only_parent_works_without_registration_or_run(self):
        first = control.begin(self.project, source_url=URL, shop_name=NAME)
        control.abandon(self.project, first['job_id'], 'SYNTHETIC zero card failure')
        child = control.begin(self.project, retry_job=first['job_id'])
        self.assertEqual((child['shop_id'], child['source_url'], child['shop_name']), (SHOP, URL, NAME))
        self.assertEqual(self.run_count(), 0)

    def test_retry_rejects_each_explicit_target_conflict_before_claim(self):
        first = control.begin(self.project, source_url=URL, shop_name=NAME)
        control.abandon(self.project, first['job_id'], 'SYNTHETIC zero card failure')
        for arguments in ({'shop_id': OTHER_SHOP}, {'source_url': OTHER_URL}, {'shop_name': 'DIFFERENT SHOP'}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                control.begin(self.project, retry_job=first['job_id'], **arguments)
        self.assertFalse((control._job_dir(self.project, first['job_id']) / 'retry_claim.json').exists())
        self.assertIsNone(journal.get_status(self.project / 'state/collection_attempts')['active_lock'])
        child = control.begin(self.project, retry_job=first['job_id'], shop_id=SHOP, source_url=URL, shop_name=NAME)
        self.assertTrue(child['acquired'])

    def test_pending_plan_preserves_only_own_jobs_not_observed_other_shop(self):
        self.observed(); self.register()
        other = control.begin(self.project, shop_id=OTHER_SHOP)
        control.abandon(self.project, other['job_id'], 'SYNTHETIC other-shop failure')
        first = control.begin(self.project, shop_id=SHOP)
        control.abandon(self.project, first['job_id'], 'SYNTHETIC own failure')
        child = control.begin(self.project, shop_id=SHOP, retry_job=first['job_id'])
        result = control.plan(self.project, SHOP)
        self.assertEqual(result['target_status'], 'pending_capture')
        self.assertIsNone(result['latest_run_id']); self.assertIsNone(result['reference_run_id'])
        self.assertEqual({job['job_id'] for job in result['jobs']}, {first['job_id'], child['job_id']})
        self.assertEqual(result['source_url'], URL); self.assertEqual(result['shop_name'], NAME)

    def test_same_hash_different_typed_registered_identities_are_rejected(self):
        self.register()
        opaque_url = URL.replace('mall_id=', 'mall_sn=')
        target = self.register(opaque_url, 'SYNTHETIC DIFFERENT TYPED ID')
        self.assertEqual(target['shop_id'], SHOP)
        before = self.hashes()
        with self.assertRaisesRegex(ValueError, 'conflicting mall_id/mall_sn'):
            control.plan(self.project, SHOP)
        with self.assertRaisesRegex(ValueError, 'conflicting mall_id/mall_sn'):
            control.begin(self.project, shop_id=SHOP)
        self.assertEqual(before, self.hashes())
        self.assertFalse((self.project / 'state/collection_attempts').exists())

    def test_registered_type_cannot_override_colliding_observed_identity(self):
        self.observed(URL, NAME)
        self.register(URL.replace('mall_id=', 'mall_sn='), 'SYNTHETIC OTHER TYPE')
        with self.assertRaisesRegex(ValueError, 'conflicts with observed'):
            control.plan(self.project, SHOP)
        with self.assertRaisesRegex(ValueError, 'conflicts with observed'):
            control.begin(self.project, shop_id=SHOP)

    def test_plan_does_not_attribute_same_hash_other_typed_job(self):
        first = control.begin(self.project, source_url=URL.replace('mall_id=', 'mall_sn='), shop_name='SYNTHETIC OTHER TYPE')
        control.abandon(self.project, first['job_id'], 'SYNTHETIC other typed identity failure')
        self.register()
        with self.assertRaisesRegex(ValueError, 'typed identity conflicts'):
            control.plan(self.project, SHOP)

    def test_unresolved_same_name_never_falls_back_to_other_shop(self):
        self.observed()
        target = self.register('https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC', 'SYNTHETIC OLD SHOP')
        self.assertIsNone(target['shop_id'])
        before = self.hashes()
        with self.assertRaisesRegex(ValueError, 'Unknown shop'):
            control.plan(self.project, SHOP)
        with self.assertRaisesRegex(ValueError, 'Unknown shop'):
            control.begin(self.project, shop_id=SHOP)
        self.assertEqual(before, self.hashes())

    def test_pending_registry_begin_preserves_explicit_verified_current_name(self):
        self.register()
        job = control.begin(self.project, shop_id=SHOP, shop_name='SYNTHETIC VERIFIED CURRENT NAME')
        self.assertEqual(job['session_options']['shopName'], 'SYNTHETIC VERIFIED CURRENT NAME')
        self.assertEqual(job['source_url'], URL); self.assert_unconfigured()

    def test_zero_run_retry_still_rejects_siblings_and_exhausted_child(self):
        self.register()
        first = control.begin(self.project, shop_id=SHOP)
        control.abandon(self.project, first['job_id'], 'SYNTHETIC first failure')
        child = control.begin(self.project, shop_id=SHOP, retry_job=first['job_id'])
        control.abandon(self.project, child['job_id'], 'SYNTHETIC retry failure')
        for parent in (first, child):
            with self.assertRaises(ValueError):
                control.begin(self.project, shop_id=SHOP, retry_job=parent['job_id'])
        self.assertEqual(self.run_count(), 0); self.assert_unconfigured()


if __name__ == '__main__':
    unittest.main()

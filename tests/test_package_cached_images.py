"""Synthetic, temporary-only cache packaging contract tests; no live capture."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
import socket
import sys
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import package_cached_images as reuse
import pdd_monitor.store as original_store
from image_cache_review import validate_restriction_review, navigation_only_reason

SOURCE_PROJECT = Path(original_store.__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
URL = "https://images.example.invalid/original.png?size=exact"
BLOCKED = "https://images.example.invalid/blocked.png"
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic image fixture, not website bytes"


class ReuseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="reuse_test_")
        self.root = Path(self.temporary.name)
        self.project = self.root / "synthetic_project"
        self.project.mkdir(parents=True)
        shutil.copytree(SOURCE_PROJECT / "pdd_monitor", self.project / "pdd_monitor", ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copy2(SOURCE_PROJECT / "schema.sql", self.project / "schema.sql")
        self.store = reuse.load_store(self.project)
        self.store.initialize(self.project / "data")
        old = self.snapshot([URL, BLOCKED], minute=0)
        old_file = self.root / "synthetic_old.json"
        old_file.write_bytes(reuse.json_bytes(old))
        archive = self.root / "synthetic_old.zip"
        self.archive(archive, PNG)
        queue = self.root / "synthetic_old_queue.json"
        queue.write_bytes(reuse.json_bytes({"blockedPreviousAttempts": [{
            "viewOrder": 2, "imageUrl": BLOCKED, "previousAttemptReason": "Synthetic tool restriction: do not retry"}]}))
        self.store.import_snapshot(self.project / "data", old_file, archive, queue)
        self.new = self.root / "synthetic_new.json"
        self.new.write_bytes(reuse.json_bytes(self.snapshot([URL, URL, BLOCKED, URL + "&different=true", None], minute=2)))
        self.output = self.root / "output"
        self.network = patch.object(socket, 'create_connection', side_effect=AssertionError('Network forbidden'))
        self.network.start(); self.addCleanup(self.network.stop)

    def tearDown(self):
        self.temporary.cleanup()

    def snapshot(self, urls, minute):
        return {"shopId": "987654321", "shopName": "QA synthetic shop", "sourceUrl": "https://mobile.yangkeduo.com/mall_page.html?mall_id=987654321",
                "status": "partial", "endBoundaryObserved": False,
                "observedFrom": f"2026-10-04T01:{minute:02d}:00Z", "observedTo": f"2026-10-04T01:{minute:02d}:30Z",
                "rows": [{"viewOrder": index, "title": "QA fixture independent card", "imageUrl": url, "salesRaw": None}
                         for index, url in enumerate(urls, 1)]}

    def archive(self, path, data):
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("images/fixture.png", data)
            archive.writestr("manifest.json", reuse.json_bytes({"items": [{"viewOrder": 1, "originalImageUrl": URL,
                "archivePath": "images/fixture.png", "sha256": hashlib.sha256(data).hexdigest()}]}))

    def run_package(self):
        return reuse.package_cached_images(self.project, self.new, self.output, sealed=True)

    def test_exact_url_card_grain_blocked_and_missing_import_roundtrip(self):
        before = {name: reuse.sha_file(self.project / "data" / name) for name in ("monitor.sqlite3", "images.sqlite3")}
        result = self.run_package()
        self.assertEqual(result["card_counts"], {"reused_verified_cache": 2, "blocked_previous_attempt": 1,
                         "unknown_source_conflict": 0, "pending_image_stage": 1, "missing_source_url": 1})
        self.assertEqual(result["reused_unique_blobs"], 1)
        self.assertEqual(before, result["database_sha256_after"])
        imported = self.store.import_snapshot(self.project / "data", self.new,
            self.output / "images.zip", self.output / "image_queue.json")
        with closing(sqlite3.connect(self.project / "data" / "monitor.sqlite3")) as connection:
            rows = connection.execute("SELECT status,previous_attempt_blocked,previous_attempt_reason,automatic_retry_allowed FROM image_tasks WHERE run_id=? ORDER BY observation_id", (imported["run_id"],)).fetchall()
        self.assertEqual([row[0] for row in rows], ["saved", "saved", "blocked_previous_attempt", "pending_image_stage", "missing_source_url"])
        self.assertEqual(rows[2][1], 1)
        self.assertIn("do not retry", rows[2][2])
        self.assertTrue(all(row[3] == 0 for row in rows))
        with zipfile.ZipFile(self.output / 'images.zip') as archive:
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual([row['viewOrder'] for row in manifest['cardDecisions']], [1, 2, 3, 4, 5])
            self.assertEqual(len(manifest['items']), 2)
            self.assertEqual(manifest['items'][0]['archivePath'], manifest['items'][1]['archivePath'])
        missing = json.loads((self.output / 'missing_urls.json').read_text(encoding='utf-8'))
        self.assertEqual(missing['cardsWithoutUrl'], [5])
        self.assertEqual(len(missing['items']), 2)
        blocked = next(item for item in missing['items'] if item['imageUrl'] == BLOCKED)
        self.assertEqual(blocked['actionRequired'], 'requires_review')
        self.assertFalse(blocked['eligibleForAuthorizedAcquisition'])

    def test_existing_outputs_not_overwritten(self):
        self.run_package()
        digest = reuse.sha_file(self.output / "cache_acceptance.json")
        with self.assertRaises(FileExistsError):
            self.run_package()
        self.assertEqual(digest, reuse.sha_file(self.output / "cache_acceptance.json"))

    def test_batch_and_unsealed_inputs_refused(self):
        with self.assertRaisesRegex(ValueError, "separately sealed"):
            reuse.package_cached_images(self.project, self.new, self.output)
        self.new.write_bytes(reuse.json_bytes({"batch": 1, "cards": []}))
        with self.assertRaisesRegex(ValueError, "batch"):
            self.run_package()
        self.assertFalse(self.output.exists())

    def test_corrupted_cached_blob_refused(self):
        with closing(sqlite3.connect(self.project / "data" / "images.sqlite3")) as connection:
            connection.execute("UPDATE assets SET data=?", (PNG[:-1] + b"!",))
            connection.commit()
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.run_package()
        self.assertFalse((self.output / "cache_acceptance.json").exists())

    def test_multiple_historical_hashes_are_not_guessed(self):
        another = self.root / "synthetic_second_old.json"
        another.write_bytes(reuse.json_bytes(self.snapshot([URL], minute=1)))
        archive = self.root / "synthetic_second_old.zip"
        self.archive(archive, PNG + b"second contents")
        self.store.import_snapshot(self.project / "data", another, archive)
        result = self.run_package()
        self.assertEqual(result["card_counts"]["unknown_source_conflict"], 2)
        self.assertEqual(result["card_counts"]["reused_verified_cache"], 0)
        missing = json.loads((self.output / 'missing_urls.json').read_text(encoding='utf-8'))
        conflict = next(item for item in missing['items'] if item['imageUrl'] == URL)
        self.assertEqual(conflict['actionRequired'], 'requires_review')
        self.assertFalse(conflict['eligibleForAuthorizedAcquisition'])

    def test_url_normalization_is_not_a_reuse_match(self):
        self.new.write_bytes(reuse.json_bytes(self.snapshot([" " + URL], minute=2)))
        with self.assertRaisesRegex(ValueError, "normalization"):
            self.run_package()
        self.assertFalse(self.output.exists())

    def test_explicit_paired_data_directory_is_read_only(self):
        data = self.root / 'matched_copy'
        shutil.copytree(self.project / 'data', data)
        before = {name: reuse.sha_file(data / name) for name in ('monitor.sqlite3', 'images.sqlite3')}
        result = reuse.package_cached_images(self.project, self.new, self.output, data_dir=data, sealed=True)
        self.assertEqual(result['database_sha256_before'], before)
        self.assertEqual(result['database_sha256_after'], before)
        self.assertEqual(result['data_dir'], str(data.resolve()))

    def test_blocked_url_wins_even_if_another_run_has_cached_bytes(self):
        blocked = self.root / 'synthetic_restricted.json'
        blocked.write_bytes(reuse.json_bytes(self.snapshot([URL], minute=1)))
        queue = self.root / 'synthetic_restricted_queue.json'
        queue.write_bytes(reuse.json_bytes({'blockedPreviousAttempts': [{'viewOrder': 1, 'imageUrl': URL,
            'previousAttemptReason': 'SYNTHETIC restriction, do not retry'}]}))
        self.store.import_snapshot(self.project / 'data', blocked, image_queue_path=queue)
        result = self.run_package()
        self.assertEqual(result['card_counts']['reused_verified_cache'], 0)
        self.assertEqual(result['card_counts']['blocked_previous_attempt'], 3)

    def reviewed_fixture(self):
        url = 'https://img.pddpic.com/SYNTHETIC_REVIEWED.png'
        snapshot = self.snapshot([url], minute=3)
        snapshot_file = self.root / 'review_source.json'
        snapshot_file.write_bytes(reuse.json_bytes(snapshot))
        queue = self.root / 'review_source_queue.json'
        queue.write_bytes(reuse.json_bytes({'blockedPreviousAttempts': [{'viewOrder': 1, 'imageUrl': url,
            'previousAttemptReason': 'safety_rejection_of_chrome_error_protocol_after_prior_failure'}]}))
        imported = self.store.import_snapshot(self.project / 'data', snapshot_file, image_queue_path=queue)
        _, blocked, _, _ = reuse.read_cache(self.project / 'data', self.store)
        review = {
            'schema_version': 1, 'decision': 'verified_local_cache_reuse_only', 'scope': 'chrome_error_navigation_only',
            'original_image_url': url, 'loaded_image_url': url, 'asset_sha256': hashlib.sha256(PNG).hexdigest(),
            'snapshot_sha256': reuse.sha_file(snapshot_file), 'view_order': 1, 'title': snapshot['rows'][0]['title'],
            'shop_source_url': snapshot['sourceUrl'], 'observed_page_url': snapshot['sourceUrl'] + '&mall_tab_key=mall_goods',
            'reviewed_at': '2026-10-07T01:00:00Z', 'authorization_reference': 'Synthetic explicit user request',
            'inventory_sha256': 'a' * 64, 'review_sha256': 'b' * 64, 'receipt_sha256': 'c' * 64,
            'image_loaded': True, 'matched_card_count': 1, 'forbidden_protocol_accessed': False, 'independent_download': False,
            'historical_block_refs': [{key: value for key, value in ref.items() if key != 'status'} for ref in blocked[url]],
        }
        item = {'viewOrder': 1, 'originalImageUrl': url, 'title': snapshot['rows'][0]['title'],
                'archivePath': 'images/reviewed.png', 'sha256': hashlib.sha256(PNG).hexdigest(),
                'acquisitionMethod': 'browser_loaded_image_body', 'historical_restriction_review': review}
        archive = self.root / 'reviewed.zip'
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as target:
            target.writestr('images/reviewed.png', PNG)
            target.writestr('manifest.json', reuse.json_bytes({'snapshotSha256': review['snapshot_sha256'], 'items': [item]}))
        self.new.write_bytes(reuse.json_bytes(self.snapshot([url], minute=4)))
        return url, imported['run_id'], archive, item

    def test_reviewed_navigation_failure_reuses_only_local_verified_bytes_and_preserves_old_failure(self):
        url, run_id, archive, _ = self.reviewed_fixture()
        before = reuse.read_cache(self.project / 'data', self.store)[1][url]
        self.store.attach_images(self.project / 'data', run_id, archive)
        candidates, blocked, _, _ = reuse.read_cache(self.project / 'data', self.store)
        self.assertEqual(blocked[url][0]['previous_attempt_reason'], before[0]['previous_attempt_reason'])
        self.assertEqual(blocked[url][0]['status'], 'saved')
        policy = reuse.cache_reuse_policy(candidates, blocked)
        self.assertIn(url, policy['skip_image_urls'])
        self.assertNotIn(url, policy['blocked_image_urls'])
        self.assertIn(BLOCKED, policy['blocked_image_urls'])
        result = self.run_package()
        self.assertEqual(result['card_counts']['reused_verified_cache'], 1)
        queue = json.loads((self.output / 'image_queue.json').read_text(encoding='utf-8'))
        self.assertFalse(queue['automaticRetryAllowed'])
        self.assertFalse(queue['items'][0]['automaticRetryAllowed'])
        with zipfile.ZipFile(self.output / 'images.zip') as target:
            manifest = json.loads(target.read('manifest.json'))
            self.assertEqual(len(manifest['items'][0]['reviewedRestrictionEvidence']), 1)
        # A future imported cache copy retains the original reviewed source link.
        self.store.import_snapshot(self.project / 'data', self.new, self.output / 'images.zip', self.output / 'image_queue.json')
        candidates, blocked, _, _ = reuse.read_cache(self.project / 'data', self.store)
        self.assertIn(url, reuse.cache_reuse_policy(candidates, blocked)['skip_image_urls'])
        with closing(sqlite3.connect(self.project / 'data/monitor.sqlite3')) as conn:
            saved = conn.execute('SELECT previous_attempt_blocked,previous_attempt_reason,automatic_retry_allowed FROM image_tasks WHERE run_id=?', (run_id,)).fetchone()
        self.assertEqual(saved, (1, before[0]['previous_attempt_reason'], 0))

    def test_review_does_not_cover_later_real_or_unreviewed_navigation_restrictions(self):
        url, run_id, archive, _ = self.reviewed_fixture()
        self.store.attach_images(self.project / 'data', run_id, archive)
        for minute, reason in [(5, 'download_timeout_followed_by_chrome_error_page'), (6, 'HTTPS image safety rejection')]:
            source = self.root / f'new_restriction_{minute}.json'
            source.write_bytes(reuse.json_bytes(self.snapshot([url], minute=minute)))
            queue = self.root / f'new_restriction_{minute}_queue.json'
            queue.write_bytes(reuse.json_bytes({'blockedPreviousAttempts': [{'viewOrder': 1, 'imageUrl': url, 'previousAttemptReason': reason}]}))
            self.store.import_snapshot(self.project / 'data', source, image_queue_path=queue)
            candidates, blocked, _, _ = reuse.read_cache(self.project / 'data', self.store)
            policy = reuse.cache_reuse_policy(candidates, blocked)
            self.assertNotIn(url, policy['skip_image_urls'])
            self.assertIn(url, policy['blocked_image_urls'])
        self.assertEqual(self.run_package()['card_counts']['blocked_previous_attempt'], 1)

    def test_review_requires_bound_original_card_asset_snapshot_and_loaded_normal_shop_evidence(self):
        url, _, _, item = self.reviewed_fixture()
        review = item['historical_restriction_review']
        kwargs = dict(original_url=url, title=review['title'], snapshot_sha=review['snapshot_sha256'], view_order=1, shop_source_url=review['shop_source_url'])
        self.assertEqual(validate_restriction_review(item, **kwargs), review)
        for field, value in [('original_image_url', url+'?different=1'), ('loaded_image_url', url+'?variant=1'),
                             ('asset_sha256', 'd'*64), ('snapshot_sha256', 'd'*64), ('view_order', 2), ('title', 'wrong card'),
                             ('observed_page_url', 'chrome-error://chromewebdata/'),
                             ('observed_page_url', review['shop_source_url'].replace('987654321','111')),
                             ('image_loaded', False), ('matched_card_count', 2), ('independent_download', True),
                             ('forbidden_protocol_accessed', True), ('authorization_reference', ''), ('receipt_sha256', None)]:
            changed = json.loads(json.dumps(item));changed['historical_restriction_review'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_restriction_review(changed, **kwargs)
        for reason in ['Actual HTTPS safety rejection', 'unknown', 'prefix download_timeout_followed_by_chrome_error_page']:
            changed = json.loads(json.dumps(item));changed['historical_restriction_review']['historical_block_refs'][0]['previous_attempt_reason'] = reason
            with self.assertRaises(ValueError):
                validate_restriction_review(changed, **kwargs)

    def test_reviewed_cache_with_multiple_hashes_remains_blocked(self):
        url, run_id, archive, _ = self.reviewed_fixture()
        self.store.attach_images(self.project / 'data', run_id, archive)
        source = self.root / 'conflicting_reviewed_source.json';source.write_bytes(reuse.json_bytes(self.snapshot([url], minute=5)))
        second = self.root / 'conflicting_reviewed.zip';content = PNG + b'changed'
        with zipfile.ZipFile(second, 'w') as target:
            target.writestr('images/conflict.png', content)
            target.writestr('manifest.json', reuse.json_bytes({'items': [{'viewOrder': 1, 'originalImageUrl': url, 'archivePath': 'images/conflict.png', 'sha256': hashlib.sha256(content).hexdigest()}]}))
        self.store.import_snapshot(self.project / 'data', source, second)
        candidates, blocked, _, _ = reuse.read_cache(self.project / 'data', self.store)
        self.assertNotIn(url, reuse.cache_reuse_policy(candidates, blocked)['skip_image_urls'])
        self.assertEqual(self.run_package()['card_counts']['blocked_previous_attempt'], 1)

    def test_inheritance_wrapper_is_narrow_and_not_an_arbitrary_safety_exception(self):
        reason = 'download_timeout_followed_by_chrome_error_page'
        wrapper = 'Inherited exact-URL restriction; no retry: '
        self.assertTrue(navigation_only_reason(wrapper + wrapper + reason + '; ' + wrapper + reason))
        for value in [reason + '; HTTPS image safety rejection', 'Safety rejection ' + reason, None, '']:
            self.assertFalse(navigation_only_reason(value))

    def test_mime_size_and_sidecar_consistency_are_checked(self):
        for query, parameters in [('UPDATE assets SET mime=?', ('image/jpeg',)),
                                  ('UPDATE assets SET mime=?,byte_count=?', ('image/png', len(PNG) + 1))]:
            with closing(sqlite3.connect(self.project / 'data/images.sqlite3')) as connection:
                connection.execute('PRAGMA ignore_check_constraints=ON')  # Corruption fixture, temp DB only.
                connection.execute(query, parameters); connection.commit()
            with self.assertRaisesRegex(ValueError, 'size, SHA256 or MIME'):
                self.run_package()
        with closing(sqlite3.connect(self.project / 'data/images.sqlite3')) as connection:
            connection.execute('UPDATE assets SET mime=?,byte_count=?', ('image/png', len(PNG))); connection.commit()
        (self.project / 'data/images.sqlite3-wal').write_bytes(b'SYNTHETIC sidecar')
        with self.assertRaisesRegex(ValueError, 'sidecar'):
            self.run_package()
        self.assertFalse((self.output / 'cache_acceptance.json').exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)

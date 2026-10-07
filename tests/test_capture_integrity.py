"""Capture gate tests. Every writable fixture/database lives in TemporaryDirectory.

If run from a three-file staging tree, set PDD_TEST_PROJECT to the read-only
project containing schema.sql. Production checkout needs no environment setting.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from pdd_monitor import store
from pdd_monitor.capture_integrity import requires_capture_integrity, verify_capture


PROJECT = Path(__file__).resolve().parents[1]
SCHEMA_PROJECT = Path(os.environ.get('PDD_TEST_PROJECT', PROJECT))
URL = 'https://mobile.yangkeduo.com/mall_page.html?mall_id=900000001'
TIMES = ['2026-10-05T01:00:00.000Z', '2026-10-05T01:01:00.000Z']


def identity():
    result = store.shop_identity_evidence({'sourceUrl': URL})
    return {k: result[k] for k in ('identity_kind', 'stable_identifier', 'source_mall_id', 'source_mall_sn')} | {'origin': 'https://mobile.yangkeduo.com', 'source_url': URL}


def card(index, top, scroll, ready=True):
    title = f'SYNTHETIC TEST ONLY card {index}' if ready else '加载中'
    image = f'https://example.invalid/{index}.png' if ready else None
    return {'slot': f'0:{index}', 'domColumn': 0, 'domRow': index, 'title': title,
            'identityTitle': title if ready else None, 'imageUrl': image, 'rawImageUrl': image,
            'goodsId': None, 'goodsUrl': None, 'goodsIdEvidence': 'synthetic no ID',
            'salesRaw': '已拼11件' if index == 0 else None, 'priceRaw': '¥4.50', 'rawText': title,
            'position': {'top': top, 'left': 10, 'bottom': top + 150,
                         'viewportTop': top - scroll, 'viewportBottom': top + 150 - scroll},
            'inViewport': top + 150 - scroll > 0 and top - scroll < 400,
            'ready': ready, 'pendingReasons': [] if ready else ['title_not_ready', 'original_image_url_not_ready']}


def batch(index, *, pending=False):
    scroll = 0 if index == 1 else 450
    cards = [card(0, 100, scroll), card(1, 300, scroll, ready=not pending)]
    if index == 2:
        cards.append(card(2, 600, scroll))
    complete = index == 2 and not pending
    manifest = [{k: copy.deepcopy(c[k]) for k in ('slot', 'domColumn', 'domRow', 'position', 'pendingReasons')} |
                {'state': 'ready' if c['ready'] else 'pending'} for c in cards]
    return {'batch': index, 'driverVersion': 5, 'at': TIMES[index-1], 'pageUrl': URL,
            'shopVerified': True, 'shopIdentityVerified': True, 'listPresent': True, 'columnLayoutVerified': True,
            'observedShopIdentity': identity(), 'expectedShopIdentity': identity(),
            'scrollTop': scroll, 'viewportHeight': 400, 'viewportWidth': 1280, 'scrollHeight': 1500,
            'columnSlotCounts': [len(cards)], 'loadedCardCount': len(cards), 'slotManifest': manifest,
            'cards': cards, 'readCoverageBottom': max(c['position']['bottom'] for c in cards),
            'boundaries': [] if index == 1 else [{'top': 350, 'bottom': 380, 'documentTop': 800, 'rendered': True, 'visible': True}],
            'recommendations': [], 'endBoundaryObserved': index == 2,
            'scrollEvidence': None if index == 1 else {'kind': 'normal_scroll', 'from': 0, 'readCoverageBottom': 450,
                'maxAllowedScrollTop': 450, 'requestedPixels': 450, 'requestedViewportUnits': 1.125},
            'added': len(cards) - int(pending) if index == 1 else 1, 'seen': len(cards) - int(pending),
            'pending': int(pending), 'stop': None, 'complete': complete, 'noProgressReads': 0,
            'mode': 'rendered_nonhidden_dom_including_offviewport'}


def snapshot(batches, *, reason=None):
    accepted = [b for b in batches if not b['stop']]
    rows, pending, first_rendered = {}, [], {}
    for b in accepted:
        pending = []
        for c in b['cards']:
            first_rendered.setdefault(c['slot'], b['at'])
            if not c['ready']:
                entry = next(e for e in b['slotManifest'] if e['slot'] == c['slot'])
                pending.append({**entry, 'identity': {k: c.get(k) for k in ('identityTitle', 'imageUrl', 'goodsUrl')}, 'firstRenderedAt': first_rendered[c['slot']]})
                continue
            first_seen = rows.get(c['slot'], {}).get('firstObservedAt', b['at'])
            rows[c['slot']] = {**copy.deepcopy(c), 'recordKey': f'position_{c["position"]["top"]}_{c["position"]["left"]}',
                'firstRenderedAt': first_rendered[c['slot']], 'firstObservedAt': first_seen, 'observedAt': b['at'],
                'observedAtPrecision': 'batch_read', 'firstObservedAtPrecision': 'batch_read',
                'observationTimeMeaning': 'latest actual rendered DOM batch read; not listing time', 'latestBatch': b['batch']}
    complete = accepted[-1]['complete'] and not reason and not batches[-1]['stop']
    return {'synthetic': True, 'shopName': 'SYNTHETIC TEST ONLY', 'sourceUrl': URL, 'shopIdentityEvidence': identity(),
            'observedFrom': accepted[0]['at'], 'observedTo': accepted[-1]['at'], 'status': 'complete' if complete else 'partial',
            'endBoundaryObserved': accepted[-1]['endBoundaryObserved'], 'sort': '上新',
            'collectionEvidence': {'driverVersion': 5, 'mode': 'all rendered nonhidden including offviewport',
                'batchCount': len(batches), 'verifiedBatchCount': len(accepted), 'pendingSlotCount': len(pending),
                'pendingSlots': pending, 'stopReason': reason or batches[-1]['stop'], 'maxScrollViewports': 3,
                'checkpointDirectory': 'sources/SYNTHETIC/batches', 'lastAttemptAt': batches[-1]['at'],
                'shopIdentityChecks': [{'at': b['at'], 'pageUrl': b['pageUrl'], 'verified': b['shopIdentityVerified'], 'stop': b['stop']} for b in batches]},
            'scrolls': [{'at': b['at'], 'count': b['seen'], 'pending': b['pending'], 'scrollTop': b['scrollTop'], 'end': b['endBoundaryObserved']} for b in accepted],
            'rows': [dict(r, viewOrder=i) for i, r in enumerate(sorted(rows.values(), key=lambda c: (c['position']['top'], c['position']['left'])), 1)]}


class CaptureIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pdd_capture_integrity_synthetic_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'capture'
        self.directory.mkdir()
        self.batches = [batch(1), batch(2)]
        self.snap = snapshot(self.batches)
        self.path = self.directory / 'snapshot.json'

    def save(self, *, same_directory=False):
        folder = self.directory if same_directory else self.directory / 'batches'
        folder.mkdir(exist_ok=True)
        self.path.write_text(json.dumps(self.snap, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        for b in self.batches:
            (folder / f'batch_{b["batch"]:04d}.json').write_text(json.dumps(b, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        return self.path

    def failure(self, code):
        result = verify_capture(self.save())
        self.assertFalse(result['valid'], result)
        self.assertEqual(code, result['errors'][0]['code'])
        return result

    def initialize_target(self):
        project = self.root / 'test_project'
        project.mkdir()
        shutil.copyfile(SCHEMA_PROJECT / 'schema.sql', project / 'schema.sql')
        self.file_patch = patch.object(store, '__file__', str(project / 'pdd_monitor' / 'store.py'))
        self.file_patch.start()
        self.addCleanup(self.file_patch.stop)
        return project / 'data'

    def hashes(self, directory):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.glob('*.sqlite3')}

    def test_complete_replays_and_hashes_every_source(self):
        result = verify_capture(self.save(), identity_shop())
        self.assertTrue(result['valid'], result)
        self.assertEqual(('complete', 2, 3, 3), (result['verified_status'], result['batch_count'], result['card_count'], len(result['source_hashes'])))
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), result['snapshot_sha256'])

    def test_one_pixel_rounding_jitter_is_accepted_without_rewriting_positions(self):
        for field in ('top', 'left'):
            with self.subTest(field=field):
                self.batches = [batch(1), batch(2)]
                target = self.batches[1]['cards'][0]
                target['position'][field] += 1
                if field == 'top':
                    target['position']['viewportTop'] += 1
                self.batches[1]['slotManifest'][0]['position'] = copy.deepcopy(target['position'])
                self.snap = snapshot(self.batches)
                result = verify_capture(self.save())
                self.assertTrue(result['valid'], result)
                self.assertEqual(target['position'][field], self.snap['rows'][0]['position'][field])

    def test_layout_jitter_cannot_accumulate_from_adjacent_batches(self):
        for field in ('top', 'left'):
            with self.subTest(field=field):
                self.batches = []
                for index, delta in enumerate((0, 1, 2), 1):
                    current = batch(1)
                    current.update(batch=index, at=f'2026-10-05T01:00:0{index}.000Z', added=2 if index == 1 else 0)
                    current['cards'][0]['position'][field] += delta
                    if field == 'top':
                        current['cards'][0]['position']['viewportTop'] += delta
                    current['slotManifest'][0]['position'] = copy.deepcopy(current['cards'][0]['position'])
                    self.batches.append(current)
                self.snap = snapshot(self.batches, reason='synthetic_operator_stop')
                self.failure('layout_changed')

    def test_more_than_one_pixel_is_rejected_even_when_under_two(self):
        target = self.batches[1]['cards'][0]
        target['position']['top'] += 1.01
        target['position']['viewportTop'] += 1.01
        self.batches[1]['slotManifest'][0]['position'] = copy.deepcopy(target['position'])
        self.snap = snapshot(self.batches)
        self.failure('layout_changed')

    def test_pending_slot_uses_first_rendered_anchor_after_becoming_ready(self):
        self.batches = [batch(1, pending=True), batch(1), batch(1)]
        for index, current in enumerate(self.batches, 1):
            current.update(batch=index, at=f'2026-10-05T01:00:0{index}.000Z', added=1 if index <= 2 else 0)
            current['cards'][1]['position']['top'] += index - 1
            current['cards'][1]['position']['viewportTop'] += index - 1
            current['slotManifest'][1]['position'] = copy.deepcopy(current['cards'][1]['position'])
        self.snap = snapshot(self.batches, reason='synthetic_operator_stop')
        self.failure('layout_changed')

    def test_end_sized_step_still_cannot_pass_previously_read_coverage(self):
        first = batch(1)
        first['cards'] = [card(index, 100 + 200 * index, 0) for index in range(40)]
        first['slotManifest'] = [{key: copy.deepcopy(c[key]) for key in ('slot', 'domColumn', 'domRow', 'position', 'pendingReasons')} |
                                 {'state': 'ready'} for c in first['cards']]
        limit = first['cards'][-1]['position']['bottom']
        first.update(loadedCardCount=40, columnSlotCounts=[40], added=40, seen=40, readCoverageBottom=limit, scrollHeight=10000)
        second = copy.deepcopy(first)
        second.update(batch=2, at=TIMES[1], scrollTop=limit, added=0, complete=True, endBoundaryObserved=True, noProgressReads=1,
                      boundaries=[{'top': 350, 'bottom': 380, 'documentTop': limit + 350, 'rendered': True, 'visible': True}],
                      scrollEvidence={'kind': 'normal_scroll', 'from': 0, 'readCoverageBottom': limit, 'maxAllowedScrollTop': limit,
                                      'requestedPixels': limit, 'requestedViewportUnits': limit / 400})
        for c, entry in zip(second['cards'], second['slotManifest']):
            c['position']['viewportTop'] -= limit
            c['position']['viewportBottom'] -= limit
            entry['position'] = copy.deepcopy(c['position'])
        self.batches = [first, second]
        self.snap = snapshot(self.batches)
        self.snap['collectionEvidence']['maxScrollViewports'] = 100
        result = verify_capture(self.save())
        self.assertTrue(result['valid'], result)
        self.assertGreater(second['scrollEvidence']['requestedViewportUnits'], 6)
        self.snap['collectionEvidence']['maxScrollViewports'] = 100.01
        self.failure('invalid_scroll_limit')
        self.snap['collectionEvidence']['maxScrollViewports'] = 100
        second['scrollTop'] += 3
        self.failure('scroll_coverage_gap')

    def test_relocated_project_reference_uses_only_adjacent_batches(self):
        self.snap['collectionEvidence']['checkpointDirectory'] = 'sources/old_computer_location/batches'
        self.assertTrue(verify_capture(self.save())['valid'])

    def test_same_directory_layout(self):
        result = verify_capture(self.save(same_directory=True))
        self.assertTrue(result['valid'], result)
        self.assertEqual('same_directory', result['checkpoint_layout'])

    def test_partial_does_not_become_complete(self):
        self.batches = [batch(1)]
        self.snap = snapshot(self.batches, reason='operator_interrupted_for_fresh_retry')
        result = verify_capture(self.save())
        self.assertTrue(result['valid'], result)
        self.assertEqual('partial', result['verified_status'])
        self.assertEqual('operator_interrupted_for_fresh_retry', result['stop_reason'])

    def test_partial_pending_keeps_unknown_slot(self):
        self.batches = [batch(1, pending=True)]
        self.snap = snapshot(self.batches, reason='rendering_not_ready')
        result = verify_capture(self.save())
        self.assertTrue(result['valid'], result)
        self.assertEqual((1, 1), (result['card_count'], result['pending_slot_count']))

    def test_first_ready_is_not_first_rendered_time(self):
        self.batches[0] = batch(1, pending=True)
        self.batches[1]['added'] = 2
        self.snap = snapshot(self.batches)
        result = verify_capture(self.save())
        self.assertTrue(result['valid'], result)
        self.assertEqual(TIMES[1], self.snap['rows'][1]['firstObservedAt'])
        self.snap['rows'][1]['firstObservedAt'] = TIMES[0]
        self.failure('snapshot_rows_mismatch')

    def test_complete_without_end_is_rejected_even_if_snapshot_claims_end(self):
        self.batches = [batch(1)]
        self.snap = snapshot(self.batches, reason='interrupted')
        self.snap.update(status='complete', endBoundaryObserved=True)
        self.snap['collectionEvidence']['stopReason'] = None
        self.failure('last_batch_mismatch')

    def test_offviewport_end_cannot_be_declared_visible(self):
        self.batches[1]['boundaries'][0].update(top=450, bottom=480, documentTop=900)
        self.failure('boundary_not_visible')

    def test_complete_pending_is_rejected(self):
        self.batches = [batch(1, pending=True)]
        self.snap = snapshot(self.batches, reason='not_ready')
        self.snap.update(status='complete')
        self.snap['collectionEvidence']['stopReason'] = None
        self.failure('false_complete')

    def test_missing_partial_reason_is_rejected(self):
        self.batches = [batch(1)]
        self.snap = snapshot(self.batches)
        self.failure('missing_partial_reason')

    def test_removed_or_downgraded_version_does_not_bypass(self):
        for version in (None, 4, '5', True):
            with self.subTest(version=version):
                self.snap['collectionEvidence']['driverVersion'] = version
                self.assertTrue(requires_capture_integrity(self.snap))
                self.failure('unsupported_driver')

    def test_rows_detect_v5_when_collection_evidence_removed(self):
        self.snap.pop('collectionEvidence')
        self.assertTrue(requires_capture_integrity(self.snap))
        self.failure('unsupported_driver')

    def test_path_traversal_or_absolute_never_read(self):
        for reference in ('../batches', 'sources/../../batches', 'D:/secret/batches', '\\\\host\\share', '/tmp/batches', 'batches\x00'):
            with self.subTest(reference=reference):
                self.snap['collectionEvidence']['checkpointDirectory'] = reference
                self.failure('unsafe_checkpoint_path')

    def test_ambiguous_local_evidence_rejected(self):
        self.save()
        shutil.copyfile(self.directory / 'batches' / 'batch_0001.json', self.directory / 'batch_0001.json')
        self.assertEqual('ambiguous_or_missing_batches', verify_capture(self.path)['errors'][0]['code'])

    def test_missing_and_discontinuous_files(self):
        self.save()
        old = self.directory / 'batches' / 'batch_0002.json'
        old.rename(old.with_name('batch_0003.json'))
        self.assertEqual('batch_sequence_gap', verify_capture(self.path)['errors'][0]['code'])
        old.with_name('batch_0003.json').unlink()
        self.assertEqual('batch_count_mismatch', verify_capture(self.path)['errors'][0]['code'])

    def test_duplicate_batch_number_and_slot_rejected(self):
        self.batches[1]['batch'] = 1
        self.failure('batch_count_mismatch')
        self.batches = [batch(1), batch(2)]
        self.batches[1]['slotManifest'][1] = copy.deepcopy(self.batches[1]['slotManifest'][0])
        self.failure('duplicate_or_missing_slot')

    def test_cross_shop_batch_and_expected_shop(self):
        self.save()
        self.assertEqual('wrong_shop', verify_capture(self.path, 'shop_' + '0'*24)['errors'][0]['code'])
        self.batches[1]['pageUrl'] = URL.replace('900000001', '900000002')
        self.failure('cross_shop_batch')

    def test_scroll_jump_cannot_cross_unread_gap(self):
        self.batches[1]['scrollTop'] = 1000
        self.failure('scroll_coverage_gap')

    def test_not_started_at_top(self):
        self.batches[0]['scrollTop'] = 200
        self.failure('not_started_at_top')

    def test_content_drift_with_consistently_changed_snapshot_is_rejected(self):
        self.batches[1]['cards'][0]['title'] = 'SYNTHETIC changed'
        self.batches[1]['cards'][0]['identityTitle'] = 'SYNTHETIC changed'
        self.snap = snapshot(self.batches)
        self.failure('slot_content_conflict')

    def test_row_omission_or_time_rewrite_is_rejected(self):
        self.snap['rows'].pop()
        self.failure('snapshot_rows_mismatch')
        self.snap = snapshot(self.batches)
        self.snap['rows'][0]['observedAt'] = TIMES[0]
        self.failure('snapshot_rows_mismatch')

    def test_out_of_order_or_terminal_summary_mismatch(self):
        self.batches[1]['at'] = '2026-10-04T01:00:00Z'
        self.failure('unordered_time')
        self.batches = [batch(1), batch(2)]
        self.snap['collectionEvidence']['lastAttemptAt'] = TIMES[0]
        self.failure('attempt_manifest_mismatch')

    def test_invalid_json_nan_duplicate_keys_and_boolean_counts(self):
        self.save()
        file = self.directory / 'batches' / 'batch_0002.json'
        for content, code in (('{bad', 'invalid_json'), ('{"at":NaN}', 'invalid_json'), ('{"batch":2,"batch":2}', 'duplicate_json_key')):
            file.write_text(content, encoding='utf-8')
            self.assertEqual(code, verify_capture(self.path)['errors'][0]['code'])
        self.snap['collectionEvidence']['batchCount'] = True
        self.failure('batch_count_mismatch')

    def test_terminal_failed_attempt_keeps_verified_prefix_only(self):
        self.batches[1].update(stop='three_frontier_reads_without_ready_progress', complete=False, added=0, seen=2)
        self.snap = snapshot(self.batches)
        result = verify_capture(self.save())
        self.assertTrue(result['valid'], result)
        self.assertEqual((2, 1, 2, 'partial'), (result['batch_count'], result['verified_batch_count'], result['card_count'], result['verified_status']))

    def test_gate_rejects_before_creating_database(self):
        data = self.root / 'must_not_exist'
        self.snap['rows'][0]['salesRaw'] = '已拼999件'
        self.save()
        with self.assertRaises(store.EvidenceValidationError):
            store.import_snapshot(data, self.path)
        self.assertFalse(data.exists())

    def test_gate_rejection_and_fresh_retry_preserve_old_history(self):
        data = self.initialize_target()
        self.batches = [batch(1)]
        self.snap = snapshot(self.batches, reason='interrupted')
        old = store.import_snapshot(data, self.save())
        baseline = self.hashes(data)
        self.snap['rows'][0]['salesRaw'] = '已拼999件'
        with self.assertRaises(store.EvidenceValidationError):
            store.import_snapshot(data, self.save())
        self.assertEqual(baseline, self.hashes(data))
        self.directory = self.root / 'fresh_retry'
        self.directory.mkdir()
        self.path = self.directory / 'snapshot.json'
        self.batches = [batch(1), batch(2)]
        # New attempt has its own actual observation window; old partial is immutable.
        for b in self.batches:
            b['at'] = b['at'].replace('01:', '02:', 1)
        self.snap = snapshot(self.batches)
        new = store.import_snapshot(data, self.save())
        self.assertNotEqual(old['run_id'], new['run_id'])
        self.assertEqual('partial', store.get_summary(data, old['run_id'])['status'])
        self.assertEqual('complete', store.get_summary(data, new['run_id'])['status'])
        after = self.hashes(data)
        self.assertEqual('duplicate', store.import_snapshot(data, self.path)['status'])
        self.assertEqual(after, self.hashes(data))

    def test_legacy_remains_compatible_but_not_verified_complete(self):
        self.snap.pop('collectionEvidence')
        self.snap['rows'] = [{'viewOrder': 1, 'title': 'SYNTHETIC LEGACY', 'salesRaw': '已拼11件', 'observedAt': TIMES[0]}]
        self.path.write_text(json.dumps(self.snap), encoding='utf-8')
        result = verify_capture(self.path)
        self.assertTrue(result['valid'])
        self.assertFalse(result['gate_required'])
        self.assertIsNone(result['verified_status'])
        data = self.initialize_target()
        self.assertEqual('imported', store.import_snapshot(data, self.path)['status'])

    def test_existing_v5_standalone_replay_is_readonly_noop_but_new_import_requires_batches(self):
        data = self.initialize_target()
        original = store.import_snapshot(data, self.save())
        before = self.hashes(data)
        standalone = self.root / 'standalone_snapshot.json'
        shutil.copyfile(self.path, standalone)
        replay = store.import_snapshot(data, standalone)
        self.assertEqual(('duplicate', original['run_id']), (replay['status'], replay['run_id']))
        self.assertFalse(replay['capture_integrity']['revalidated'])
        self.assertEqual(before, self.hashes(data))
        fresh = self.root / 'new_database'
        with self.assertRaises(store.EvidenceValidationError):
            store.import_snapshot(fresh, standalone)
        self.assertFalse(fresh.exists())

    def test_sealed_checkpoint_hash_manifest_is_enforced(self):
        self.save()
        self.snap['collectionEvidence']['checkpointManifest'] = [
            {'file': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size, 'verified': True}
            for p in sorted((self.directory / 'batches').glob('batch_*.json'))]
        self.assertTrue(verify_capture(self.save())['valid'])
        for field, value in (('sha256', '0'*64), ('sha256', 'bad'), ('bytes', True), ('bytes', 1), ('file', 'batch_0002.json'), ('verified', False), ('parseError', True)):
            with self.subTest(field=field, value=value):
                original = copy.deepcopy(self.snap['collectionEvidence']['checkpointManifest'][0])
                self.snap['collectionEvidence']['checkpointManifest'][0][field] = value
                self.failure('checkpoint_manifest_mismatch')
                self.snap['collectionEvidence']['checkpointManifest'][0] = original

    def test_capture_session_is_fresh_window_without_hidden_auto_retries(self):
        session = {'schemaVersion': 1, 'sessionId': 'synthetic_session_only', 'parentSessionId': None,
                   'freshRetryIndex': 0, 'recoveredFromDurableCheckpoints': False, 'freshWindowOnly': True, 'automaticRetriesPerformed': 0}
        self.snap['collectionEvidence']['captureSession'] = session
        self.snap['collectionEvidence'].update(importReady=True, checkpointIntegrityStatus='verified')
        self.save()
        self.snap['collectionEvidence']['checkpointManifest'] = [
            {'file': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size, 'verified': True}
            for p in sorted((self.directory / 'batches').glob('batch_*.json'))]
        self.assertTrue(verify_capture(self.save())['valid'])
        session['freshWindowOnly'] = False
        self.failure('invalid_capture_session')

    def test_explicit_shop_identity_conflict(self):
        self.snap['mallId'] = '900000002'
        self.failure('invalid_shop_identity')

    def test_erased_three_reads_stop_cannot_continue(self):
        self.batches = [batch(1)]
        for index in (2, 3, 4):
            b = batch(1)
            b.update(batch=index, at=f'2026-10-05T01:0{index}:00.000Z', scrollTop=450,
                     added=0, noProgressReads=index-1)
            for c in b['cards']:
                c['position']['viewportTop'] -= 450
                c['position']['viewportBottom'] -= 450
            for entry, c in zip(b['slotManifest'], b['cards']):
                entry['position'] = copy.deepcopy(c['position'])
            b['scrollEvidence'] = {'kind': 'normal_scroll' if index == 2 else 'stationary_read',
                'from': 0 if index == 2 else 450, 'readCoverageBottom': 450, 'maxAllowedScrollTop': 450,
                'requestedPixels': 450 if index == 2 else 0, 'requestedViewportUnits': 1.125 if index == 2 else 0}
            self.batches.append(b)
        self.snap = snapshot(self.batches, reason='operator_stop')
        self.failure('unreported_stop')

    def test_duplicate_auxiliary_conflicts_are_readonly_failures(self):
        data = self.initialize_target()
        self.save()
        queue = self.root / 'queue.json'
        queue.write_text(json.dumps({'items': []}), encoding='utf-8')
        store.import_snapshot(data, self.path, image_queue_path=queue)
        before = self.hashes(data)
        self.assertEqual('duplicate', store.import_snapshot(data, self.path, image_queue_path=queue)['status'])
        queue.write_text(json.dumps({'items': [], 'source': 'different immutable evidence'}), encoding='utf-8')
        with self.assertRaises(store.EvidenceValidationError):
            store.import_snapshot(data, self.path, image_queue_path=queue)
        archive = self.root / 'conflicting.zip'
        with zipfile.ZipFile(archive, 'w') as handle:
            handle.writestr('manifest.json', json.dumps({'items': [{'viewOrder': 1, 'originalImageUrl': 'https://example.invalid/wrong'}]}))
        with self.assertRaises(store.EvidenceValidationError):
            store.import_snapshot(data, self.path, image_archive=archive)
        self.assertEqual(before, self.hashes(data))

    def test_boolean_cannot_impersonate_snapshot_view_order(self):
        self.snap['rows'][0]['viewOrder'] = True
        self.failure('snapshot_rows_mismatch')

    def test_session_manual_review_and_missing_seal_are_not_import_ready(self):
        for field, value in (('importReady', False), ('importReady', 'true'), ('checkpointIntegrityStatus', 'manual_review_required')):
            with self.subTest(field=field):
                self.snap = snapshot(self.batches)
                self.snap['collectionEvidence'][field] = value
                self.failure('manual_review_required')
        self.snap = snapshot(self.batches)
        self.snap['collectionEvidence'].update(captureSession={}, importReady=True, checkpointIntegrityStatus='verified')
        self.failure('session_not_sealed')


def identity_shop():
    return store.shop_identity_evidence({'sourceUrl': URL})['shop_id']


if __name__ == '__main__':
    unittest.main()

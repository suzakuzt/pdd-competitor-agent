import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request

from pdd_monitor.artist_heat import (SOURCE_URL, MAX_BYTES, TIMEOUT, _SameHostRedirect,
    parse_iqiyi_html, save_html_snapshot, fetch_iqiyi_snapshot, build_heat_queries)


def fixture(updated='10-04 16:55', values=('100', '90'), *, names=('周也 许凯', '演员甲 演员乙'),
            types=('电视剧', '电影'), titles=('作品甲', '作品乙')):
    cards = []
    for i, value in enumerate(values):
        kind = types[i % len(types)]
        cards.append(f'''<a class="rvi__box rvi__box3" data-block-v2="detailrank.0"
           data-rseat-v2="{i+1}" href="//www.iqiyi.com/v_work{i}.html">
           <div class="rvi__tit1" title="{titles[i % len(titles)]}"><span>{i+1}</span>ignored</div>
           <div class="rvi__type1" title="{kind} / 2026 / 剧情 古装 / {names[i % len(names)]}"></div>
           <span class="rvi__index__num">{value}</span><p class="rvi__index__txt">实时热度</p></a>''')
    return ('<a class="rtab__slider__link selected" href="/ranks1/-1/0">总榜</a>'
            '<a class="rtab__sub__link selected" href="/ranks1/-1/0">热播榜</a>'
            '<span class="erji__meta__txt">按实时热度排行最近更新' + updated +
            '</span>' + ''.join(cards) + '<script>window.__NUXT__={invisible:999999};throw new Error()</script>').encode()


def hashes(project):
    return {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in project.rglob('*') if p.is_file()}


class ArtistHeatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, html=None, captured='2026-10-04T08:55:53Z'):
        return save_html_snapshot(self.project, html or fixture(), captured)

    def query(self, catalog=None):
        return build_heat_queries(self.project, catalog)

    def test_readonly_unconfigured_does_not_create_state(self):
        before = hashes(self.project)
        result = self.query()
        self.assertEqual(result['artist_heat_summary']['rows'][0]['status'], 'not_configured')
        self.assertEqual(result['artist_heat_people']['rows'], [])
        self.assertEqual(before, hashes(self.project))

    def test_public_ssr_only_and_source_metadata_people(self):
        parsed = parse_iqiyi_html(fixture(), '2026-10-04T08:55:53Z')
        self.assertEqual([w['value'] for w in parsed['works']], [100, 90])
        self.assertEqual(parsed['source_timestamp'], '2026-10-04T08:55:00Z')
        self.assertEqual(parsed['works'][0]['cast_names'], ['周也', '许凯'])
        self.assertFalse(parsed['full_board_complete'])
        self.assertNotIn('invisible', json.dumps(parsed))
        self.save()
        q = self.query({'entities': [{'name': '周也', 'entity_kind': 'artist', 'entity_id': 'artist_zhouye'},
                                    {'name': '许凯', 'entity_kind': 'esports', 'entity_id': 'esports_x'}]})
        rows = {r['name']: r for r in q['artist_heat_people']['rows']}
        self.assertTrue(rows['周也']['in_artist_directory'])
        self.assertFalse(rows['许凯']['in_artist_directory'])
        self.assertEqual(rows['周也']['directory_entity_id'], 'artist_zhouye')
        self.assertIsNone(rows['周也']['personal_heat'])
        self.assertNotIn('shop_id', rows['周也'])
        self.assertEqual(rows['周也']['associated_works'][0]['change_status'], 'first_observation')

    def test_same_measurements_replay_different_html_noise_not_new_period(self):
        first = self.save()
        before = hashes(self.project)
        second = self.save(fixture() + b'<!-- newly generated unrelated markup -->', '2026-10-04T08:56:30Z')
        self.assertEqual(second['status'], 'duplicate')
        self.assertEqual(first['snapshot_id'], second['snapshot_id'])
        self.assertEqual(before, hashes(self.project))
        self.assertEqual(self.query()['artist_heat_summary']['rows'][0]['snapshot_count'], 1)

    def test_same_source_time_conflict_preserves_all_existing_bytes(self):
        self.save()
        before = hashes(self.project)
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            self.save(fixture(values=('101', '90')))
        self.assertEqual(before, hashes(self.project))

    def test_three_comparable_periods_up_down_flat_work_only(self):
        self.save(fixture(values=('100', '90', '80')))
        self.save(fixture('10-04 17:00', ('105', '85', '80')), '2026-10-04T09:00:01Z')
        q = self.query()
        history = q['artist_heat_history']['rows']
        self.assertEqual([r['change_status'] for r in history[:3]], ['first_observation'] * 3)
        self.assertEqual([r['change_status'] for r in history[3:]], ['up', 'down', 'flat'])
        self.assertEqual([r['delta'] for r in history[3:]], [5, -5, 0])
        self.assertEqual(q['artist_heat_summary']['rows'][0]['comparison_available_count'], 3)
        self.assertTrue(all(p['personal_heat'] is None for p in q['artist_heat_people']['rows']))

    def test_missing_and_approximate_value_remain_unknown_not_zero(self):
        self.save(fixture(values=('--', '100+')))
        self.save(fixture('10-04 17:00', ('105', '85')), '2026-10-04T09:00:01Z')
        rows = self.query()['artist_heat_history']['rows']
        self.assertTrue(all(r['delta'] is None for r in rows))
        self.assertTrue(all(r['change_status'] == 'unknown' for r in rows))
        self.assertEqual([r['value'] for r in rows[:2]], [None, None])

    def test_absent_work_not_zero_or_off_board(self):
        self.save()
        self.save(fixture('10-04 17:00', ('105',)), '2026-10-04T09:00:01Z')
        q = self.query()
        self.assertEqual(len(q['artist_heat_history']['rows']), 3)
        self.assertEqual(q['artist_heat_summary']['rows'][0]['work_count'], 1)
        self.assertNotIn('演员甲', [p['name'] for p in q['artist_heat_people']['rows']])
        self.assertEqual(q['artist_heat_history']['rows'][1]['value'], 90)

    def test_source_time_cache_regression_not_growth(self):
        self.save(fixture('10-04 17:00'), '2026-10-04T09:00:01Z')
        self.save(fixture('10-04 16:55', ('500', '500')), '2026-10-04T09:01:01Z')
        rows = self.query()['artist_heat_history']['rows'][-2:]
        self.assertTrue(all(r['comparison_reason'] == 'source_time_not_strictly_increasing' for r in rows))
        self.assertTrue(all(r['delta'] is None for r in rows))

    def test_time_year_rollover_stale_future_invalid_boundaries(self):
        p = parse_iqiyi_html(fixture('12-31 23:59'), '2027-01-01T00:01:00+08:00')
        self.assertEqual(p['source_timestamp'], '2026-12-31T15:59:00Z')
        self.assertEqual(p['time_status'], 'trusted_with_timezone_assumption')
        self.assertEqual(parse_iqiyi_html(fixture('10-01 16:55'), '2026-10-04T08:55:53Z')['time_status'], 'older_than_48_hours')
        self.assertEqual(parse_iqiyi_html(fixture('10-04 17:01'), '2026-10-04T08:55:00Z')['time_status'], 'future_more_than_5_minutes')
        self.assertEqual(parse_iqiyi_html(fixture('02-30 12:00'), '2026-10-04T08:55:00Z')['time_status'], 'invalid_date')
        self.assertEqual(parse_iqiyi_html(fixture('10-04 17:00'), '2026-10-04T08:55:00Z')['time_status'], 'trusted_with_timezone_assumption')
        with self.assertRaises(ValueError):
            parse_iqiyi_html(fixture(), '2026-10-04T08:55:00')

    def test_untrusted_time_preserved_but_not_compared(self):
        self.save(fixture('10-01 16:55'))
        self.save(fixture('10-04 17:00', ('110', '110')), '2026-10-04T09:00:01Z')
        rows = self.query()['artist_heat_history']['rows']
        self.assertTrue(all(r['change_status'] == 'unknown' for r in rows))
        self.assertTrue(all(r['delta'] is None for r in rows))

    def test_child_and_animation_genres_not_people(self):
        self.save(fixture(types=('动漫', '少儿'), names=('热血 冒险', '团队协作 探索冒险')))
        self.assertEqual(self.query()['artist_heat_people']['rows'], [])
        self.assertEqual(self.query()['artist_heat_summary']['rows'][0]['work_count'], 2)

    def test_reject_wrong_scope_metric_unsafe_url_and_duplicate_rank(self):
        for invalid in (
            fixture().replace('热播榜'.encode(), '飙升榜'.encode()),
            fixture().replace('实时热度</p>'.encode(), '播放量</p>'.encode()),
            fixture().replace(b'//www.iqiyi.com/v_work0.html', b'https://evil.example/v_work0.html'),
            fixture().replace(b'data-rseat-v2="2"', b'data-rseat-v2="1"'),
            b'<script>' + fixture() + b'</script>',
            b'x' * (MAX_BYTES + 1),
        ):
            with self.subTest(size=len(invalid)), self.assertRaises(ValueError):
                parse_iqiyi_html(invalid, '2026-10-04T08:55:53Z')

    def test_failed_fetch_records_one_attempt_and_preserves_history(self):
        self.save()
        before = {p: h for p, h in hashes(self.project).items() if '/attempts/' not in p.replace('\\', '/')}
        calls = []
        def fetcher(url, **options):
            calls.append((url, options))
            raise TimeoutError('timeout fixture')
        receipt = fetch_iqiyi_snapshot(self.project, fetcher)
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], (SOURCE_URL, {'timeout': TIMEOUT, 'max_bytes': MAX_BYTES}))
        after = {p: h for p, h in hashes(self.project).items() if '/attempts/' not in p.replace('\\', '/')}
        self.assertEqual(before, after)
        summary = self.query()['artist_heat_summary']['rows'][0]
        self.assertEqual(summary['last_attempt_status'], 'failed')
        self.assertEqual(summary['latest_captured_at'], '2026-10-04T08:55:53Z')

    def test_fetch_rejects_external_redirect_status_oversize_without_retry(self):
        for result in (
            {'status': 200, 'url': 'https://evil.example/', 'body': fixture()},
            {'status': 403, 'url': SOURCE_URL, 'body': fixture()},
            b'x' * (MAX_BYTES + 1),
        ):
            calls = []
            def fetcher(*args, **kwargs):
                calls.append(1)
                return result
            self.assertEqual(fetch_iqiyi_snapshot(self.project, fetcher)['status'], 'failed')
            self.assertEqual(len(calls), 1)
        self.assertEqual(self.query()['artist_heat_history']['rows'], [])
        for url in ('https://evil.example/', 'http://www.iqiyi.com/x', 'https://user:pass@www.iqiyi.com/x'):
            with self.assertRaises(ValueError):
                _SameHostRedirect().redirect_request(Request(SOURCE_URL), None, 302, '', {}, url)

    def test_profiles_exact_join_and_readonly_evidence_hashes(self):
        self.save()
        path = self.project / 'state/artist_heat/profiles.json'
        path.write_text(json.dumps({'schema_version': 1, 'profiles': [
            {'name': '周也', 'identity_short': '演员', 'other_work_examples': '山河令',
             'official_platform_profile_url': 'https://www.iqiyi.com/star/123',
             'identity_source_status': 'opened_primary_platform_profile', 'checked_date': '2026-10-04'},
            {'name': '许凯别名', 'identity_short': '不应匹配'}]}, ensure_ascii=False), encoding='utf-8')
        before = hashes(self.project)
        q = self.query()
        rows = {r['name']: r for r in q['artist_heat_people']['rows']}
        self.assertEqual(rows['周也']['identity_brief'], '演员；可从《山河令》认识')
        self.assertIsNone(rows['许凯']['person_url'])
        self.assertEqual(q['artist_heat_people']['source']['files'][str(path.resolve())], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(before, hashes(self.project))

    def test_corrupt_evidence_fails_read_without_repair_or_writes(self):
        self.save()
        source = next((self.project / 'sources/artist_heat').glob('*.html'))
        source.write_bytes(source.read_bytes() + b'corruption')
        before = hashes(self.project)
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.query()
        self.assertEqual(before, hashes(self.project))

    def test_existing_lock_not_automatically_recovered(self):
        folder = self.project / 'state/artist_heat'
        folder.mkdir(parents=True)
        lock = folder / '.import.lock'
        lock.write_text('{"pid":999999,"started_at":"2001-01-01T00:00:00Z"}')
        before = hashes(self.project)
        with self.assertRaisesRegex(ValueError, 'locked'):
            self.save()
        self.assertEqual(before, hashes(self.project))

    def test_successful_fetch_and_duplicate_fetch_are_not_two_periods(self):
        with patch('pdd_monitor.artist_heat._now', return_value='2026-10-04T08:55:53Z'):
            first = fetch_iqiyi_snapshot(self.project, lambda *a, **kw: fixture())
            second = fetch_iqiyi_snapshot(self.project, lambda *a, **kw: fixture())
        self.assertEqual(first['status'], 'saved')
        self.assertEqual(second['status'], 'duplicate')
        self.assertEqual(self.query()['artist_heat_summary']['rows'][0]['snapshot_count'], 1)
        self.assertEqual(len(list((self.project / 'state/artist_heat/attempts').glob('*.json'))), 2)

    def test_freshness_reminder_24h_independent_of_source_time_trust(self):
        self.save(fixture('10-01 16:55'))
        before = hashes(self.project)
        recent = build_heat_queries(self.project, now='2026-10-05T08:55:53Z')['artist_heat_summary']['rows'][0]
        old = build_heat_queries(self.project, now='2026-10-05T08:55:54Z')['artist_heat_summary']['rows'][0]
        self.assertEqual(recent['freshness_status'], 'recent')
        self.assertEqual(recent['time_status'], 'older_than_48_hours')
        self.assertEqual(old['freshness_status'], 'stale')
        self.assertIn('本地提醒阈值', old['freshness_note'])
        self.assertEqual(before, hashes(self.project))

    def test_catalog_exact_names_and_profiles_unsafe_url_rejected(self):
        self.save()
        folder = self.project / 'state/artist_research'
        folder.mkdir(parents=True)
        (folder / 'catalog.json').write_text(json.dumps({'entities': [
            {'name': '周也', 'entity_kind': 'artist', 'entity_id': 'zhouye'},
            {'name': '演员甲甲', 'entity_kind': 'artist', 'entity_id': 'unmatched', 'match_terms': ['演员甲']}]}), encoding='utf-8')
        q = self.query()
        rows = {r['name']: r for r in q['artist_heat_people']['rows']}
        self.assertTrue(rows['周也']['in_artist_directory'])
        self.assertFalse(rows['演员甲']['in_artist_directory'])
        (self.project / 'state/artist_heat/profiles.json').write_text(json.dumps({'schema_version': 1,
            'profiles': [{'name': '周也', 'official_platform_profile_url': 'javascript:alert(1)'}]}), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.query()


if __name__ == '__main__':
    unittest.main()

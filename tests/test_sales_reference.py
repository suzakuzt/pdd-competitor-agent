"""Synthetic sales-reference acceptance; all writes stay in temporary fixtures."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from pdd_monitor.sales_reference import create_sales_reference
from pdd_monitor.store import attach_images, connect, import_snapshot


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jh3sAAAAASUVORK5CYII=')


class SalesReferenceAcceptance(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='pdd_sales_reference_synthetic_')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / 'data'
        self.output = self.root / 'reference.html'

    def import_rows(self, sales, hour=1, titles=None, status='complete'):
        rows = []
        for view, raw in enumerate(sales, 1):
            rows.append({'viewOrder': view, 'recordKey': f'synthetic-{view}',
                         'title': titles[view - 1] if titles else f'SYNTHETIC product {view}',
                         'salesRaw': raw, 'priceRaw': '券后¥4.5',
                         'goodsId': None, 'goodsUrl': None,
                         'imageUrl': f'https://example.invalid/{view}.png',
                         'observedAt': f'2026-10-04T{hour:02d}:01:00Z',
                         'observedAtPrecision': 'batch_read'})
        source = self.root / f'synthetic-{hour}.json'
        source.write_text(json.dumps({'synthetic': True, 'shopName': 'SYNTHETIC TEST ONLY',
            'sourceUrl': 'https://example.invalid/shop?mall_id=9000000001', 'sort': '上新',
            'observedFrom': f'2026-10-04T{hour:02d}:00:00Z',
            'observedTo': f'2026-10-04T{hour:02d}:10:00Z', 'status': status,
            'endBoundaryObserved': status == 'complete', 'rows': rows}), encoding='utf-8')
        return import_snapshot(self.data, source)['run_id'], rows

    def report(self, run_id=None):
        result = create_sales_reference(self.data, self.output, run_id)
        payload = json.loads(self.output.with_suffix('.json').read_text(encoding='utf-8'))
        html = self.output.read_text(encoding='utf-8')
        return result, payload, html

    def test_labels_thresholds_exclusions_and_observation_precision(self):
        run_id, _ = self.import_rows(['已拼11件', '已拼10件', '已拼9件', '已拼1件',
                                     '已抢15件', '总售20件', '已拼12人', '已售4单', '售出5件', '已拼0件',
                                     '已拼10+件', None, '收藏20'])
        result, payload, html = self.report(run_id)
        run = payload['runs'][0]
        rows = run['rows']
        self.assertEqual([row['view_order'] for row in rows], list(range(1, 10)))
        self.assertEqual([row['reference_group'] for row in rows],
                         ['yipin_gt10', 'yipin_eq10', 'yipin_1to9', 'yipin_1to9',
                          'yipin_gt10', 'other_positive', 'other_positive',
                          'other_positive', 'other_positive'])
        self.assertEqual([row['eligible_gt10'] for row in rows], [True, False, False, False, True, False, False, False, False])
        self.assertEqual([(row['sales_label'], row['sales_value'], row['sales_unit']) for row in rows[4:]],
                         [('已抢', 15, '件'), ('总售', 20, '件'), ('已拼', 12, '人'),
                          ('已售', 4, '单'), ('售出', 5, '件')])
        self.assertEqual(run['counts'], {'positive': 9, 'missing': 1, 'non_exact_or_unparsed': 2,
                                        'zero': 1, 'other_excluded': 0, 'total': 13, 'excluded': 4})
        self.assertEqual(len(run['label_unit_counts']), 6)
        self.assertEqual(rows[0]['observed_at_precision'], 'batch_read')
        self.assertEqual(rows[0]['observed_at'], '2026-10-04T01:01:00Z')
        self.assertIn('已拼 =10 件', html)
        self.assertIn('观察时刻（北京时间）：2026-10-04 09:01:00', html)
        self.assertIn('时间精度：批次读取时刻', html)
        self.assertIn('商品 ID 待补', html)
        self.assertIn('<details class="evidence"><summary>来源详情</summary>', html)
        self.assertIn('<summary>轮次来源</summary>', html)
        self.assertEqual(html.count('<th>'), 6)
        self.assertEqual(result['positive_observation_count'], 9)

    def test_identical_titles_and_separate_rounds_keep_individual_cards(self):
        old_id, _ = self.import_rows(['已拼11件', '已拼5件'], titles=['同名', '同名'])
        new_id, _ = self.import_rows(['已拼13件', '已拼2件'], hour=2,
                                    titles=['同名', '同名'], status='partial')
        result, payload, html = self.report()
        self.assertEqual(result['run_ids'], [new_id, old_id])
        self.assertEqual([[row['sales_value'] for row in run['rows']] for run in payload['runs']],
                         [[13, 2], [11, 5]])
        self.assertEqual(len({row['observation_id'] for run in payload['runs'] for row in run['rows']}), 4)
        self.assertEqual(html.count('<td class="title">同名</td>'), 4)
        self.assertIn('本轮覆盖不完整', html)
        _, one_run, _ = self.report(old_id)
        self.assertEqual([run['run_id'] for run in one_run['runs']], [old_id])
        with self.assertRaisesRegex(ValueError, 'No matching snapshot'):
            self.report('missing-run')

    def test_readonly_html_escaping_local_image_and_no_network_image_loading(self):
        hostile_title = '<script>alert("x")</script><img src=x onerror="evil()">& full title'
        run_id, rows = self.import_rows(['已拼11件', '已抢2件', '总售3件'],
                                        titles=[hostile_title, 'blocked', 'missing'])
        archive = self.root / 'images.zip'
        sha = hashlib.sha256(PNG).hexdigest()
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('images/one.png', PNG)
            bundle.writestr('manifest.json', json.dumps({'synthetic': True, 'items': [{
                'viewOrder': 1, 'title': hostile_title, 'originalImageUrl': rows[0]['imageUrl'],
                'archivePath': 'images/one.png', 'sha256': sha}]}))
        attach_images(self.data, run_id, archive)
        connection = connect(self.data)
        try:
            connection.execute('UPDATE observations SET goods_url=? WHERE run_id=? AND view_order=1',
                               ('javascript:alert(1)', run_id))
            connection.execute('UPDATE image_tasks SET status=?,previous_attempt_blocked=1,'
                               'previous_attempt_reason=? WHERE run_id=? AND observation_id IN '
                               '(SELECT observation_id FROM observations WHERE run_id=? AND view_order=2)',
                               ('blocked_previous_attempt', '<script>bad()</script>', run_id, run_id))
        finally:
            connection.close()
        paths = [self.data / 'monitor.sqlite3', self.data / 'images.sqlite3']
        before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        result, payload, html = self.report()
        self.assertEqual(before, [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths])
        self.assertFalse(result['database_writes_performed'])
        self.assertEqual(payload['runs'][0]['rows'][0]['title'], hostile_title)
        self.assertNotIn(hostile_title, html)
        self.assertIn('&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;', html)
        self.assertNotIn('href="javascript:', html)
        self.assertNotIn('<img src="http', html)
        self.assertIn('data:image/png;base64,', html)
        self.assertIn('手动查看原图网址', html)
        self.assertIn('不自动重试', html)
        self.assertIn('主图待补', html)
        self.assertEqual(result['embedded_unique_image_count'], 1)
        self.assertEqual(payload['runs'][0]['rows'][0]['image_content_status'], 'verified_local')
        self.assertEqual(payload['runs'][0]['rows'][1]['image_status'], 'blocked_previous_attempt')
        with self.assertRaisesRegex(ValueError, '.html'):
            create_sales_reference(self.data, self.data / 'monitor.sqlite3')


if __name__ == '__main__':
    unittest.main()

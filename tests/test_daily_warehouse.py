from copy import deepcopy
import unittest
from pdd_monitor.daily_warehouse import build_daily_warehouse


def run(name, day, hour=1, complete=True, shop='A'):
    return {'run_id': name, 'shop_id': shop, 'shop_name': shop, 'status': 'complete' if complete else 'partial',
            'end_boundary_observed': complete, 'observed_from': f'2026-10-{day:02d}T{hour:02d}:00:00Z',
            'observed_to': f'2026-10-{day:02d}T{hour:02d}:10:00Z', 'snapshot_sha256': name}


def card(number, source, sales=20, goods='123', **extra):
    value={'observation_id':number,'run_id':source['run_id'],'view_order':number,'title':'SYNTHETIC ONLY',
           'goods_id':goods,'identity_status':'unique_goods_id' if goods else 'unknown',
           'sales_raw':f'已拼{sales}件','sales_value':sales,'sales_label':'已拼','sales_unit':'件','sales_precision':'exact_display',
           'observed_at':source['observed_to'],'observed_at_precision':'card_read','price_raw':'券后¥3.50'}
    value.update(extra);return value


class WarehouseTests(unittest.TestCase):
    def test_same_day_latest_complete_replaces_view_preserves_source(self):
        old,new,partial=run('old',4),run('new',4,2),run('partial',4,3,False)
        runs=[partial,old,new];rows=[card(1,old),card(2,new,25),card(3,partial,30)]
        before=deepcopy((runs,rows));data=build_daily_warehouse(runs,rows)
        self.assertEqual(data['warehouse_days'][0]['run_id'],'new')
        self.assertEqual(data['warehouse_days'][0]['latest_observed_run_id'],'partial')
        self.assertEqual(data['warehouse_days'][0]['version_count'],3)
        self.assertEqual([r['observation_id'] for r in data['warehouse_records']],[2])
        self.assertEqual((runs,rows),before)

    def test_next_day_retains_yesterday_and_computes_exact_delta(self):
        a,b=run('a',4),run('b',5)
        data=build_daily_warehouse([b,a],[card(1,a,0),card(2,b,15)])
        self.assertEqual(len(data['warehouse_days']),2)
        self.assertEqual(len(data['warehouse_tracks']),1)
        self.assertEqual(data['warehouse_points'][1]['daily_delta'],15)

    def test_beijing_midnight_and_partial_fallback(self):
        a=run('a',4,16,False)
        data=build_daily_warehouse([a],[card(1,a)])
        self.assertEqual(data['warehouse_days'][0]['date'],'2026-10-05')
        self.assertEqual(data['warehouse_days'][0]['selection'],'partial_fallback')

    def test_missing_id_or_conflict_never_merge_by_title_image(self):
        a,b=run('a',4),run('b',5)
        rows=[card(1,a,goods=None,image_url='same'),card(2,b,goods=None,image_url='same'),
              card(3,a,goods='456'),card(4,a,goods='456')]
        data=build_daily_warehouse([a,b],rows)
        self.assertEqual(len(data['warehouse_tracks']),4)
        self.assertTrue(all(p['delta_status']=='identity_unverified' for p in data['warehouse_points']))
        self.assertTrue(all(p['daily_delta'] is None for p in data['warehouse_points']))

    def test_cross_shop_same_id_is_independent(self):
        a,b=run('a',4),run('b',5,shop='B')
        data=build_daily_warehouse([a,b],[card(1,a),card(2,b)])
        self.assertEqual(len(data['warehouse_tracks']),2)
        self.assertTrue(all(p['daily_delta'] is None for p in data['warehouse_points']))

    def test_missing_day_labels_and_ambiguous_sales_stay_unknown(self):
        for other in ({'sales_label':'总售'},{'sales_precision':'approximate'},{'sales_value':None}):
            a,b=run('a',4),run('b',5)
            data=build_daily_warehouse([a,b],[card(1,a),card(2,b,**other)])
            self.assertIsNone(data['warehouse_points'][1]['cumulative_yipin'])
            self.assertIsNone(data['warehouse_points'][1]['daily_delta'])
        a,b=run('a',4),run('b',6)
        data=build_daily_warehouse([a,b],[card(1,a),card(2,b,30)])
        self.assertEqual(data['warehouse_points'][1]['delta_status'],'missing_previous_day')
        self.assertIsNone(data['warehouse_points'][1]['daily_delta'])

    def test_negative_delta_and_invalid_card_timestamp(self):
        a,b=run('a',4),run('b',5)
        data=build_daily_warehouse([a,b],[card(1,a,30),card(2,b,20)])
        self.assertEqual(data['warehouse_points'][1]['delta_status'],'negative_anomaly')
        self.assertIsNone(data['warehouse_points'][1]['daily_delta'])
        data=build_daily_warehouse([a,b],[card(1,a),card(2,b,observed_at=a['observed_to'])])
        self.assertEqual(data['warehouse_points'][1]['delta_status'],'time_unverified')
        self.assertIsNone(data['warehouse_points'][1]['cumulative_yipin'])

    def test_price_conditions_and_ranges(self):
        a=run('a',4)
        data=build_daily_warehouse([a],[card(1,a,goods=None),card(2,a,goods=None,price_raw='¥5'),card(3,a,goods=None,price_raw='¥3-5')])
        p=data['warehouse_points']
        self.assertEqual(p[0]['display_price_yuan'],3.5)
        self.assertEqual(p[0]['price_condition'],'coupon_after_display')
        self.assertEqual(p[1]['price_condition'],'display_unspecified')
        self.assertIsNone(p[2]['display_price_yuan'])

    def test_missing_source_run_rejected(self):
        a=run('a',4)
        with self.assertRaises(ValueError):build_daily_warehouse([], [card(1,a)])

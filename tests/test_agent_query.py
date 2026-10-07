"""Independent read-only tool tests; synthetic rows exist only in memory/C temp."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

try:
    from pdd_monitor.agent_query import AgentQuery
except ModuleNotFoundError:
    from agent_query import AgentQuery

HERE = Path(__file__).resolve().parent
REAL_SNAPSHOT = Path(os.environ['PDD_AGENT_QUERY_REAL_SNAPSHOT']) if os.environ.get('PDD_AGENT_QUERY_REAL_SNAPSHOT') else None


def fixture():
    def run(rid, shop, hour, complete):
        return {'run_id':rid,'shop_id':shop,'shop_name':'测试店'+shop,
            'observed_from':f'2026-10-04T{hour:02d}:00:00Z','observed_to':f'2026-10-04T{hour:02d}:10:00Z',
            'status':'complete' if complete else 'partial','end_boundary_observed':complete,
            'snapshot_sha256':rid+'-sha','snapshot_metadata':{'large':'not exported'}}
    def card(oid, rid='a-ref', value=None, label=None, unit=None, precision='missing', title='测试立牌'):
        return {'observation_id':oid,'run_id':rid,'view_order':oid,'title':title,'goods_id':None,'goods_url':None,
            'sales_raw':None if value is None else f'{label}{value}{unit}','sales_value':value,
            'sales_label':label,'sales_unit':unit,'sales_precision':precision,'price_raw':'券后¥4.5',
            'identity_status':'unknown_no_goods_id','observed_at':None,'observed_at_precision':'run_window',
            'image_status':'pending','image_content_status':'pending','image_url':'https://example.test/original.jpg',
            'asset_sha256':None,'row_json':'RAW_JSON_MUST_NOT_LEAK','data_url':'data:image/png;base64,MUST_NOT_LEAK',
            'original':{'rawText':'原卡原文，无商品ID'}}
    rows=[card(1,value=11,label='已拼',unit='件',precision='exact_display'),
        card(2,value=10,label='已拼',unit='件',precision='exact_display'),
        card(3,value=0,label='已拼',unit='件',precision='exact_display'),card(4),
        card(5,value=999,label='已拼',unit='件',precision='non_exact_or_unparsed'),
        card(6,value=400,label='已抢',unit='件',precision='exact_display'),
        card(7,value=100,label='已拼',unit='单',precision='exact_display'),
        card(8,value=11,label='已拼',unit='件',precision='exact_display'),
        card(9,value=12,label='已拼',unit='件',precision='exact_display',title='别的挂件'),
        card(10,'a-new',title='测试立牌'),card(11,'a-new',title='后续卡'),
        card(12,'b-only',9999,'已拼','件','exact_display')]
    rows[0].update(image_content_status='verified_local',image_status='saved',asset_sha256='a'*64)
    rows[1].update(image_content_status='unavailable_or_invalid',image_status='blocked',previous_attempt_blocked=True)
    data={'id':'test-app','generatedAt':'2026-10-04T13:00:00Z','queries':{
        'runs':{'rows':[run('a-ref','a',10,True),run('a-new','a',12,False),run('b-only','b',11,True)]},
        'observations':{'rows':rows},'image_assets':{'rows':[{'sha256':'a'*64,'integrity_status':'verified_local','mime':'image/png','byte_count':10,'data_url':'data:image/png;base64,MUST_NOT_LEAK'}]},
        'artist_watchlist':{'rows':[{'artist_id':'artist-a','artist_name':'测试演员','entity_kind':'artist','shop_id':'a','run_id':'a-ref','observation_ids':[1,4],'latest_run_id':'a-new','latest_observation_ids':[10],
            'identity_note':'目录线索','sources':[{'label':'官方入口','url':'https://example.test/artist','verification':'测试来源','basis':'目录关联','date_checked':'2026-10-04','data_url':'MUST_NOT_LEAK'}]},
            {'artist_id':'esports-a','artist_name':'测试选手','entity_kind':'esports','shop_id':'a','run_id':'a-ref','observation_ids':[2],'latest_run_id':'a-new','latest_observation_ids':[],'sources':[]}]},
        'new_arrival_summary':{'rows':[{'shop_id':'a','tracking_id':'tracking-a','state':'candidates_available','baseline_run_ids':['a-ref'],'baseline_observation_count':9,'post_baseline_run_ids':['a-new'],'post_baseline_run_count':1,'post_baseline_observation_count':2,'item_count':1}]},
        'new_arrival_items':{'rows':[{'arrival_item_id':'arrival-a','tracking_id':'tracking-a','shop_id':'a','run_id':'a-new','observation_id':10,'title':'测试立牌','newness':'unknown','discovery_kind':'new_card_clue','discovery_label':'新卡片线索（身份待核验）','first_observed_at':None,'first_observed_at_precision':'unknown','first_observation_window':{'from':'2026-10-04T12:00:00Z','to':'2026-10-04T12:10:00Z'},'possible_since':None,'possible_until':'2026-10-04T12:10:00Z','latest_run_id':'a-new','latest_observation_ids':[10,11],'first_observation_id_candidates':[10],'historical_references':[{'run_id':'a-ref','observation_id':1,'match_basis':'clue_only'}],'subsequent_references':[{'run_id':'a-new','observation_id':11,'match_basis':'clue_only','temporal_order':'same_run_sibling'}]}]}}}
    return data


class AgentQueryTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = fixture()
        self.tools = AgentQuery(self.snapshot)

    def test_context_reference_latest_and_history_are_separate(self):
        shops=self.tools.context()['shops'];self.assertEqual(len(shops),2)
        a=self.tools.context('a')['shops'][0]
        self.assertEqual(a['reference']['run_id'],'a-ref');self.assertEqual(a['reference']['card_count'],9)
        self.assertEqual(a['latest']['run_id'],'a-new');self.assertEqual(a['latest']['card_count'],2)
        self.assertEqual(a['historical_observation_count'],11)
        self.assertEqual(self.tools.search('a',scope='all')['total'],11)
        self.assertEqual(self.tools.search('a',scope='latest')['total'],2)

    def test_sales_boundaries_use_exact_yipin_pieces_only_and_keep_zero(self):
        self.assertEqual([r['observation_id'] for r in self.tools.search('a',sales_min=11)['rows']],[6,9,1,8])
        self.assertEqual([r['observation_id'] for r in self.tools.search('a',sales_min=10,sales_max=10)['rows']],[2])
        self.assertEqual([r['observation_id'] for r in self.tools.search('a',sales_min=0,sales_max=0)['rows']],[3])
        self.assertIsNone(self.tools.card('a',4)['card']['sales_value'])
        self.assertFalse(self.tools.card('a',5)['card']['eligible_gt10'])

    def test_sales_sort_unknown_tail_ties_position_and_no_title_dedup(self):
        rows=self.tools.search('a')['rows']
        self.assertEqual([r['observation_id'] for r in rows[:6]],[6,9,1,8,2,3])
        self.assertEqual([r['observation_id'] for r in rows[6:]],[4,5,7])
        self.assertEqual([r['observation_id'] for r in self.tools.search('a',sort='position')['rows']],list(range(1,10)))
        self.assertEqual(self.tools.search('a',terms=['测试立牌'])['total'],8)
        self.assertEqual(self.tools.search('a',terms=['测试立牌'],scope='all')['total'],9)

    def test_terms_are_literal_case_insensitive_any_contains(self):
        self.assertEqual(self.tools.search('a',terms=['不存在','挂件'])['total'],1)
        self.snapshot['queries']['observations']['rows'][0]['title']='ABC Test'
        tools=AgentQuery(self.snapshot)
        self.assertEqual(tools.search('a',terms=['abc'])['total'],1)
        self.assertEqual(tools.search('a',terms=["' OR 1=1"])['total'],0)

    def test_pagination_is_bounded_and_complete_without_repeated_rows(self):
        one=self.tools.search('a',limit=2);two=self.tools.search('a',limit=2,offset=one['next_offset'])
        self.assertEqual(one['total'],9);self.assertEqual(one['next_offset'],2)
        self.assertFalse({r['observation_id'] for r in one['rows']} & {r['observation_id'] for r in two['rows']})
        end=self.tools.search('a',offset=100);self.assertEqual(end['rows'],[]);self.assertFalse(end['has_more']);self.assertIsNone(end['next_offset'])

    def test_query_input_rejects_bool_nan_wrong_types_and_excess_bounds(self):
        for field,values in {'limit':[True,False,0,21,1.5,float('nan'),'2',None],
            'offset':[True,-1,1.5,float('nan'),'2',1_000_001],
            'sales_min':[True,-1,1.5,float('nan'),float('inf'),'11'],
            'sales_max':[False,-1,1.5,float('nan'),'10'],
            'artists_only':[0,1,'true',None,[],{}],
            'scope':[True,'other',[],None],'sort':[True,'other',[],None],'image_status':['other',True,[],None]}.items():
            for value in values:
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):self.tools.search('a',**{field:value})
        with self.assertRaises(ValueError):self.tools.search('a',sales_min=11,sales_max=10)
        for terms in ('hello',[True],[''],[' '],['x'*121],['x']*9,{},1):
            with self.subTest(terms=terms),self.assertRaises(ValueError):self.tools.search('a',terms=terms)
        for method in (self.tools.search,self.tools.new_arrivals):
            with self.assertRaises(ValueError):method('a',limit=True)
            with self.assertRaises(ValueError):method('a',offset=float('nan'))

    def test_cross_shop_reads_are_rejected_without_fallback(self):
        for method in (self.tools.context,self.tools.search,self.tools.new_arrivals):
            with self.assertRaises(ValueError):method('not-a-shop')
        for oid in (12,True,'1',0,-1,None,float('nan')):
            with self.subTest(oid=oid),self.assertRaises(ValueError):self.tools.card('a',oid)
        self.assertEqual(self.tools.card('b',12)['card']['shop_id'],'b')

    def test_artist_filter_requires_reviewed_artist_association_not_title_or_esports(self):
        result=self.tools.search('a',artists_only=True,sort='position')
        self.assertEqual([r['observation_id'] for r in result['rows']],[1,4])
        self.assertTrue(result['filters']['artists_only']);self.assertIn('不代表完整追星圈',result['artist_filter_basis'])
        self.assertEqual(self.tools.search('a',scope='latest',artists_only=True)['total'],1)
        self.assertEqual(self.tools.card('a',1)['card']['artist_sources'][0]['sources'][0]['url'],'https://example.test/artist')

    def test_images_are_actual_metadata_refs_and_never_base64(self):
        self.assertEqual(self.tools.search('a',image_status='cached')['total'],1)
        self.assertEqual(self.tools.search('a',image_status='failed_or_blocked')['total'],1)
        self.assertEqual(self.tools.search('a',image_status='pending')['total'],7)
        card=self.tools.card('a',1)['card'];self.assertEqual(card['asset_sha256'],'a'*64)
        self.assertEqual(card['thumbnail_ref'],{'query_id':'image_assets','asset_sha256':'a'*64,'observation_id':1})
        self.assertEqual(card['image_url'],'https://example.test/original.jpg')
        combined=json.dumps([self.tools.context(),self.tools.search('a'),self.tools.card('a',1),self.tools.new_arrivals('a')],ensure_ascii=False)
        for unwanted in ('MUST_NOT_LEAK','row_json','data_url','base64','snapshot_metadata'):self.assertNotIn(unwanted,combined)

    def test_arrivals_use_existing_rows_no_inferred_newness_or_sales(self):
        result=self.tools.new_arrivals('a');self.assertEqual(result['total'],1)
        row=result['rows'][0];self.assertEqual(row['newness'],'unknown');self.assertIsNone(row['possible_since'])
        self.assertEqual(row['latest_observation_ids'],[10,11]);self.assertEqual(len(row['historical_references']),1)
        self.assertIsNone(row['anchor_card']['sales_value']);self.assertIn('不是已确认新品',result['interpretation'])
        self.assertEqual(self.tools.new_arrivals('b')['summary']['state'],'not_configured')
        for state,expected in [('no_post_baseline_run','等待下一轮'),('no_first_candidates','不代表已确认没有新品')]:
            snapshot=fixture();snapshot['queries']['new_arrival_items']['rows']=[];snapshot['queries']['new_arrival_summary']['rows'][0]['state']=state
            self.assertIn(expected,AgentQuery(snapshot).new_arrivals('a')['interpretation'])

    def test_return_values_and_original_snapshot_do_not_mutate_indexes(self):
        before=deepcopy(self.snapshot)
        self.tools.search('a')['rows'][0]['title']='output mutation'
        self.tools.card('a',1)['card']['artist_sources'][0]['sources'][0]['label']='output mutation'
        self.tools.new_arrivals('a')['rows'][0]['latest_observation_ids'].append(12)
        self.assertEqual(self.snapshot,before)
        self.snapshot['queries']['observations']['rows'][0]['title']='input mutation'
        self.assertEqual(self.tools.card('a',1)['card']['title'],'测试立牌')
        self.assertEqual(self.tools.card('a',1)['card']['artist_sources'][0]['sources'][0]['label'],'官方入口')
        self.assertEqual(self.tools.new_arrivals('a')['rows'][0]['latest_observation_ids'],[10,11])

    def test_malformed_cross_shop_references_and_nan_snapshot_fail_closed(self):
        mutations=[lambda d:d['queries']['observations']['rows'][0].update(shop_id='b'),
            lambda d:d['queries']['artist_watchlist']['rows'][0].update(observation_ids=[12]),
            lambda d:d['queries']['new_arrival_items']['rows'][0].update(latest_observation_ids=[12]),
            lambda d:d['queries']['new_arrival_items']['rows'][0].update(latest_run_id='b-only'),
            lambda d:d['queries']['new_arrival_summary']['rows'][0].update(baseline_run_ids=['b-only']),
            lambda d:d['queries']['observations']['rows'][0].update(sales_value=float('nan'))]
        for mutate in mutations:
            data=fixture();mutate(data)
            with self.subTest(mutation=mutate),self.assertRaises(ValueError):AgentQuery(data)
        with tempfile.TemporaryDirectory(dir=HERE) as temp:
            path=Path(temp)/'bad.json';path.write_text('{"queries":{},"invalid":NaN}',encoding='utf-8')
            with self.assertRaises(ValueError):AgentQuery.from_path(path)



if __name__ == "__main__": unittest.main(verbosity=2)

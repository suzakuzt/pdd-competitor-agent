from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

MODULE=Path(__file__).resolve().parents[1]/'pdd_monitor/opportunities.py'
spec=importlib.util.spec_from_file_location('opportunities',MODULE)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def run(key,shop='s1',hour=10,complete=True):
    return {'run_id':key,'shop_id':shop,'observed_from':f'2026-10-04T{hour:02}:00:00Z',
            'observed_to':f'2026-10-04T{hour:02}:10:00Z','status':'complete' if complete else 'partial',
            'end_boundary_observed':complete,'source_url':'https://example.test/shop/'+shop}

def card(oid,rid,value=11,goods=None,label='已拼',precision='exact_display'):
    return {'observation_id':oid,'run_id':rid,'view_order':oid,'title':'相同标题','goods_id':goods,
            'identity_status':'unique_goods_id' if goods else 'unknown_no_goods_id','goods_url':None,
            'sales_raw':None if value is None else f'{label}{value}件','sales_label':label,'sales_unit':'件',
            'sales_value':value,'sales_precision':precision,'observed_at':None,'observed_at_precision':'run_window',
            'asset_sha256':'a'*64,'image_content_status':'verified_local','image_status':'saved',
            'price_raw':'券后¥4.50','image_url':'https://example.test/image'}

def pack(runs,obs,refs=None):
    refs=refs or {s:next(r['run_id'] for r in runs if r['shop_id']==s) for s in {r['shop_id'] for r in runs}}
    profiles=[];products=[]
    for shop,ref in refs.items():
        latest=max((r for r in runs if r['shop_id']==shop),key=lambda r:r['observed_to'])
        profiles.append({'shop_id':shop,'shop_name':shop,'reference_run_id':ref,'latest_run_id':latest['run_id'],
                         'latest_is_complete':latest['status']=='complete'})
        products.extend(dict(o,shop_id=shop) for o in obs if o['run_id']==ref)
    return {key:{'rows':value,'source':{'files':['fixture.json'],'tables':['observations']}} for key,value in
        {'runs':runs,'observations':obs,'competitor_shops':profiles,'competitor_products':products,
         'new_arrival_items':[],'new_arrival_summary':[],'comparison_items':[]}.items()}

def output(q): return m.build_opportunity_queries(q)['profit_opportunities']['rows']
def summary(q):return m.build_opportunity_queries(q)['profit_opportunity_summary']['rows']
def pair(old=1,new=2,delta=4):
    return {'comparison_item_id':f'pair-{old}-{new}','comparison_id':'r2:previous:r1',
            'baseline_run_id':'r1','target_run_id':'r2','old_observation_id':old,'new_observation_id':new,
            'status':'comparable','match_basis':'same_unique_goods_id','display_delta':delta,
            'elapsed_hours_min':50/60,'elapsed_hours_max':70/60}
def arrival(oid=2,rid='r2',kind='new_card_clue',refs=None):
    return {'arrival_item_id':'arrival-'+str(oid),'tracking_id':'t1','shop_id':'s1','observation_id':oid,
            'run_id':rid,'discovery_kind':kind,'latest_observation_ids':refs or [oid]}
def track(q):
    q['new_arrival_summary']['rows']=[{'shop_id':'s1','tracking_id':'t1','started_at':'2026-10-04T10:30:00Z',
        'baseline_run_ids':['r1'],'state':'candidates_available'}]

class OpportunityTests(unittest.TestCase):
    def test_expanded_partial_origin_keeps_arrival_evidence_but_uses_sales_tier(self):
        q=pack([run('r1',complete=False),run('r2',hour=11)],[card(1,'r1'),card(2,'r2')])
        track(q); q['new_arrival_summary']['rows'][0].update(baseline_coverage_status='partial_only',baseline_coverage_note='起点部分，补扫可能为旧存量')
        q['new_arrival_items']['rows']=[dict(arrival(),coverage_expansion_possible=True)]
        row=next(r for r in output(q) if r['observation_id']==2)
        self.assertEqual(row['priority_key'],'eligible_reference')
        self.assertTrue(row['is_new_candidate']);self.assertTrue(row['coverage_expansion_possible'])
        self.assertFalse(row['new_candidate_priority_eligible']);self.assertEqual(row['new_arrival_item_ids'],['arrival-2'])
        self.assertTrue(any('此前未采到的旧存量' in text for text in row['blockers']))
        self.assertIn('起点部分，补扫可能为旧存量',summary(q)[0]['coverage_gaps'])

    def test_coverage_expansion_unknown_low_other_label_and_fuzzy_stay_watch(self):
        observed=[card(1,'r1'),card(2,'r2',11),card(3,'r2',10),card(4,'r2',None,precision='missing'),
            card(5,'r2',99,label='已抢'),card(6,'r2',11,precision='non_exact_or_unparsed')]
        q=pack([run('r1',complete=False),run('r2',hour=11)],observed);track(q)
        q['new_arrival_items']['rows']=[dict(arrival(oid),coverage_expansion_possible=True) for oid in range(2,7)]
        original=deepcopy(q);rows=output(q);s=summary(q)[0]
        self.assertEqual(q,original)
        self.assertEqual({r['observation_id'] for r in rows if r['priority_key']=='eligible_reference'},{1,2,5})
        self.assertEqual({r['observation_id'] for r in rows if r['priority_key']=='watch'},{3,4,6})
        self.assertEqual((s['new_candidate_count'],s['arrival_evidence_count'],s['coverage_expansion_evidence_count']),(0,5,5))
        self.assertEqual(sum(s[key+'_count'] for key in m.TIERS),s['opportunity_count'])
        self.assertEqual(s['opportunity_count'],6)
        self.assertTrue(all(r['new_arrival_item_ids']==['arrival-'+str(r['observation_id'])] for r in rows if r['is_new_candidate']))

    def test_weak_low_first_observation_is_watch_reliable_first_id_keeps_candidate(self):
        q=pack([run('r1'),run('r2',hour=11)],[card(1,'r1'),card(2,'r2',2),card(3,'r2',2,'103')]);track(q)
        q['new_arrival_items']['rows']=[arrival(),arrival(3,kind='first_observed_id_candidate')]
        rows={r['observation_id']:r for r in output(q)}
        self.assertEqual(rows[2]['priority_key'],'watch');self.assertTrue(rows[2]['is_new_candidate'])
        self.assertEqual(rows[3]['priority_key'],'new_candidate');self.assertTrue(rows[3]['new_candidate_priority_eligible'])

    def test_partial_origin_growth_still_wins_over_sales_without_new_priority(self):
        q=pack([run('r1',complete=False),run('r2',hour=11)],[card(1,'r1',11,'101'),card(2,'r2',15,'101')]);track(q)
        q['new_arrival_items']['rows']=[dict(arrival(),coverage_expansion_possible=True)]
        q['comparison_items']['rows']=[pair()]
        row=next(r for r in output(q) if r['observation_id']==2)
        self.assertEqual(row['priority_key'],'comparable_growth');self.assertTrue(row['is_comparable_growth'])
        self.assertEqual(row['new_arrival_item_ids'],['arrival-2']);self.assertEqual(row['comparison_item_ids'],['pair-1-2'])

    def test_eligible_bucket_descending_same_label_and_ties_original_position(self):
        q=pack([run('r1')],[card(1,'r1',11),card(2,'r1',90),card(3,'r1',90),card(4,'r1',500,label='已抢'),card(5,'r1',10)])
        rows=output(q)
        self.assertEqual([r['observation_id'] for r in rows if r['priority_key']=='eligible_reference'],[4,2,3,1])
        self.assertEqual([r['priority_key'] for r in rows],['eligible_reference']*4+['watch'])

    def test_real_card_grain_reference_and_latest_no_merge_or_mutation(self):
        q=pack([run('r1'),run('r2',hour=11,complete=False)],[card(1,'r1'),card(2,'r1'),card(3,'r2',None,precision='missing')])
        before=deepcopy(q);rows=output(q)
        self.assertEqual(q,before);self.assertEqual({r['observation_id'] for r in rows},{1,2,3})
        self.assertEqual([r['priority_order'] for r in rows],[3,3,4])
        self.assertEqual(rows[-1]['sales_display'],None)
        s=summary(q)[0];self.assertEqual((s['opportunity_count'],s['eligible_reference_count'],s['watch_count']),(3,2,1))
        self.assertEqual((s['reference_card_count'],s['latest_card_count']),(2,1))

    def test_same_reference_latest_deduplicates_only_observation_id(self):
        q=pack([run('r1')],[card(1,'r1'),card(2,'r1')]);q['competitor_products']['rows']*=2
        rows=output(q);self.assertEqual(len(rows),2);self.assertTrue(all(r['source_role']=='reference_and_latest' for r in rows))

    def test_exact_threshold_label_unit_null_boolean_and_fuzzy(self):
        rows=[card(1,'r1',10),card(2,'r1',11),card(3,'r1',50,label='已抢'),card(4,'r1',None,precision='missing'),
              card(5,'r1',11,precision='non_exact_or_unparsed'),card(6,'r1',True)]
        rows[4]['sales_raw']='已拼10+件';rows.append(dict(card(7,'r1',99),sales_unit='单'))
        result=output(pack([run('r1')],rows))
        self.assertEqual([r['observation_id'] for r in result if r['eligible_gt10']],[3,2])
        self.assertEqual(next(r for r in result if r['observation_id']==5)['sales_display'],'已拼10+件')

    def test_arrival_then_growth_then_gt10_priority_are_exclusive(self):
        q=pack([run('r1'),run('r2',hour=11)],[card(1,'r1',11,'101'),card(2,'r2',15,'101')])
        track(q);q['new_arrival_items']['rows']=[arrival()];q['comparison_items']['rows']=[pair()]
        r=next(x for x in output(q) if x['observation_id']==2)
        self.assertEqual(r['priority_key'],'new_candidate');self.assertTrue(r['is_comparable_growth']);self.assertTrue(r['eligible_gt10'])
        self.assertEqual(r['evidence_observation_ids'],[1,2]);self.assertEqual(summary(q)[0]['new_candidate_count'],1)
        self.assertEqual(summary(q)[0]['comparable_growth_count'],0)

    def test_persistent_arrival_latest_references_keep_independent_cards(self):
        runs=[run('r1'),run('r2',hour=11),run('r3',hour=12)]
        q=pack(runs,[card(1,'r1'),card(2,'r2',1),card(3,'r3',3),card(4,'r3',4)])
        track(q);q['new_arrival_items']['rows']=[arrival(refs=[3,4])]
        rows=output(q)
        self.assertEqual({r['observation_id'] for r in rows},{1,3,4})
        self.assertEqual({r['observation_id'] for r in rows if r['is_new_candidate']},{3,4})
        self.assertTrue(all(2 in r['evidence_observation_ids'] for r in rows if r['is_new_candidate']))

    def test_baseline_and_prestart_cards_cannot_be_new(self):
        q=pack([run('r1'),run('r2',hour=11)],[card(1,'r1'),card(2,'r2')]);track(q)
        q['new_arrival_items']['rows']=[arrival(1,'r1'),arrival(refs=[1,2])]
        rows=output(q);self.assertFalse(next(r for r in rows if r['observation_id']==1)['is_new_candidate'])
        self.assertTrue(next(r for r in rows if r['observation_id']==2)['is_new_candidate'])
        self.assertGreater(summary(q)[0]['rejected_arrival_evidence_count'],0)

    def test_growth_rejects_missing_duplicate_conflicting_identity_and_wrong_delta(self):
        for variant in ('missing','duplicate','url_conflict','wrong_delta','negative','no_existing_pair'):
            with self.subTest(variant=variant):
                obs=[card(1,'r1',1,'101'),card(2,'r2',5,'101')]
                if variant=='missing':obs[1]['goods_id']=None
                if variant=='duplicate':obs.append(card(3,'r2',5,'101'))
                if variant=='url_conflict':obs[1]['goods_url']='https://example.test/goods?goods_id=999'
                q=pack([run('r1'),run('r2',hour=11)],obs)
                q['comparison_items']['rows']=[] if variant=='no_existing_pair' else [pair(delta=8 if variant=='wrong_delta' else -4 if variant=='negative' else 4)]
                self.assertFalse(any(r['is_comparable_growth'] for r in output(q)))

    def test_growth_label_retained_and_does_not_imply_yipin_threshold(self):
        q=pack([run('r1'),run('r2',hour=11)],[card(1,'r1',1,'101',label='已抢'),card(2,'r2',5,'101',label='已抢')])
        q['comparison_items']['rows']=[pair()]
        r=next(x for x in output(q) if x['observation_id']==2)
        self.assertEqual(r['priority_key'],'comparable_growth');self.assertFalse(r['eligible_gt10'])
        self.assertIn('已抢',''.join(r['reasons']));self.assertFalse(any('不是已拼件' in s for s in r['blockers']))

    def test_cross_shop_same_id_title_does_not_join(self):
        q=pack([run('r1','s1'),run('r2','s2',11)],[card(1,'r1',1,'101'),card(2,'r2',5,'101')])
        q['comparison_items']['rows']=[pair()]
        self.assertEqual(len(output(q)),2);self.assertFalse(any(r['is_comparable_growth'] for r in output(q)))
        self.assertEqual(len(summary(q)),2)
        q['competitor_shops']['rows'][0]['latest_run_id']='r2'
        # Whichever profile appears first, ensure a mismatched shop reference exists.
        q['competitor_shops']['rows'][0]['latest_run_id']='r2' if q['competitor_shops']['rows'][0]['shop_id']=='s1' else 'r1'
        with self.assertRaises(ValueError):output(q)

    def test_window_growth_valid_but_overlap_bad_order_or_invented_elapsed_rejected(self):
        for variant in ('valid','overlap','bad_elapsed','invalid_card_time'):
            with self.subTest(variant=variant):
                runs=[run('r1'),run('r2',hour=11)]
                obs=[card(1,'r1',1,'101'),card(2,'r2',5,'101')]
                if variant=='overlap':runs[1]['observed_from']='2026-10-04T10:05:00Z'
                if variant=='invalid_card_time':obs[1]['observed_at']='2026-10-03T11:00:00Z'
                q=pack(runs,obs);item=pair()
                if variant=='bad_elapsed':item['elapsed_hours_min']=3
                q['comparison_items']['rows']=[item]
                self.assertEqual(any(r['is_comparable_growth'] for r in output(q)),variant=='valid')

    def test_unknown_config_waiting_and_image_gaps_no_profit(self):
        q=pack([run('r1')],[dict(card(1,'r1',None,precision='missing'),asset_sha256=None,image_content_status='unavailable_or_invalid')])
        q['new_arrival_summary']['rows']=[{'shop_id':'s1','tracking_id':'t1','state':'no_post_baseline_run'}]
        s=summary(q)[0];self.assertEqual((s['missing_image_count'],s['missing_identity_count'],s['unknown_sales_count']),(1,1,1))
        self.assertTrue(any('等待' in x for x in s['coverage_gaps']))
        r=output(q)[0];self.assertEqual(r['profit_status'],'unknown_no_own_sales_or_costs')
        self.assertNotIn('profit',r);self.assertNotIn('roi',r)

    def test_conflicting_reference_original_or_missing_cards_rejected(self):
        q=pack([run('r1')],[card(1,'r1'),card(2,'r1')]);q['competitor_products']['rows'][0]['title']='changed'
        with self.assertRaises(ValueError):output(q)
        q=pack([run('r1')],[card(1,'r1'),card(2,'r1')]);q['competitor_products']['rows'].pop()
        with self.assertRaises(ValueError):output(q)

    def test_read_only_project_and_source_lineage(self):
        q=pack([run('r1')],[card(1,'r1')])
        with tempfile.TemporaryDirectory() as tmp:
            result=m.build_opportunity_queries(q,Path(tmp));self.assertEqual(list(Path(tmp).iterdir()),[])
        self.assertEqual(result['profit_opportunities']['source']['sourceQueryRowIds']['observations'],[1])
        self.assertEqual(set(result),set(m.QUERY_IDS))

    def test_reviewed_artist_links_respect_scope_and_never_change_priority(self):
        q=pack([run('r1'),run('r2',hour=11)],[card(1,'r1'),card(2,'r2',1)])
        before={row['observation_id']:row['priority_key'] for row in output(q)}
        artist={'artist_id':'s1:person1','artist_name':'合成艺人','entity_kind':'artist','shop_id':'s1',
            'run_id':'r1','observation_ids':[1],'latest_run_id':'r2','latest_observation_ids':[2],
            'sources':[{'url':'https://example.test/artist','label':'公开入口','verified_entry':False,'verification':'未核实'}]}
        q['artist_watchlist']={'rows':[artist,dict(artist,artist_id='unresolved',entity_kind='unresolved',artist_name='未明称呼')]}
        rows=output(q)
        self.assertEqual({row['observation_id']:row['priority_key'] for row in rows},before)
        self.assertTrue(all(row['artist_names']==['合成艺人'] for row in rows))
        self.assertFalse(rows[0]['artist_public_links'][0]['verified_entry'])
        self.assertIn('商品化许可',rows[0]['artist_topic_note'])
        artist['shop_id']='other-shop'
        with self.assertRaises(ValueError):output(q)

if __name__=='__main__':unittest.main()

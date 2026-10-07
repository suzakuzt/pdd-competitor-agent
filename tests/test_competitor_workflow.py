"""Temporary multi-shop workflow tests. No fabricated rows in production."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from pdd_monitor.store import import_snapshot
from pdd_monitor.competitor_registry import register_target, build_targets
from pdd_monitor.portfolio_tracking import start_shop_tracking, build_portfolio_arrivals
from pdd_monitor.dashboard_data import build_dashboard_snapshot
from pdd_monitor.competitor_export import export_competitor


class CompetitorWorkflowAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pdd_portfolio_synthetic_')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root/'data'

    def run_snapshot(self, mall, hour, sales='已拼11件', status='complete'):
        value = {'synthetic':True,'shopName':'SYNTHETIC SAME SHOP NAME',
                 'sourceUrl':f'https://mobile.yangkeduo.com/mall_page.html?mall_id={mall}',
                 'observedFrom':f'2026-10-04T{hour:02d}:00:00Z', 'observedTo':f'2026-10-04T{hour:02d}:05:00Z',
                 'status':status,'endBoundaryObserved':status=='complete', 'rows':[
                     {'viewOrder':1,'title':'SYNTHETIC SAME PRODUCT 立牌','goodsId':'999',
                      'goodsUrl':'https://mobile.yangkeduo.com/goods.html?goods_id=999','salesRaw':sales,
                      'priceRaw':'券后¥4.5','imageUrl':'https://example.invalid/shared.png',
                      'observedAt':f'2026-10-04T{hour:02d}:02:00Z','observedAtPrecision':'batch_read'}]}
        path=self.root/f'{mall}-{hour}.json';path.write_text(json.dumps(value),encoding='utf-8')
        return import_snapshot(self.data,path)

    def snapshot(self):
        return build_dashboard_snapshot(self.data,self.root/'dashboard/data.json')

    def test_name_only_does_not_create_data_or_claim_collection(self):
        result=register_target(self.root,'SYNTHETIC name')
        self.assertEqual(result['target']['status'],'needs_identity')
        self.assertIsNone(result['target']['shop_id'])
        self.assertFalse(self.data.exists())
        self.assertFalse(result['website_collection_performed'])

    def test_same_shop_tracking_parameters_are_idempotent(self):
        first=register_target(self.root,'SYNTHETIC', 'https://mobile.yangkeduo.com/mall_page.html?mall_id=101&track=a')
        before=Path(first['path']).read_bytes()
        second=register_target(self.root,'new display request', 'https://mobile.yangkeduo.com/mall_page.html?track=b&mall_id=101')
        self.assertEqual(second['status'],'unchanged')
        self.assertEqual(before,Path(second['path']).read_bytes())
        self.assertEqual(len(list((self.root/'state/competitors/targets').glob('*.json'))),1)

    def test_short_link_remains_unresolved_and_does_not_fetch(self):
        result=register_target(self.root,source_url='https://p.pinduoduo.com/synthetic-share')
        self.assertEqual(result['target']['identity_status'],'unresolved_share_link')
        self.assertIsNone(result['target']['shop_id'])

    def test_foreign_and_product_links_rejected(self):
        for value in ('https://example.invalid/?mall_id=1','https://mobile.yangkeduo.com/goods.html?goods_id=1',
                      'https://mobile.yangkeduo.com.evil.invalid/?mall_id=1','https://u:p@mobile.yangkeduo.com/?mall_id=1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                register_target(self.root,source_url=value)

    def test_multishop_reference_and_history_export_isolated(self):
        self.run_snapshot(101,1)
        self.run_snapshot(202,2,'已抢999件')
        self.run_snapshot(101,3,'已拼1件',status='partial')
        snapshot=self.snapshot();q=snapshot['queries']
        shops=q['competitor_shops']['rows'];self.assertEqual(len(shops),2)
        for shop in shops:
            shop_id=shop['shop_id'];run_ids={r['run_id'] for r in q['runs']['rows'] if r['shop_id']==shop_id}
            products=[p for p in q['competitor_products']['rows'] if p['shop_id']==shop_id]
            self.assertEqual(len(products),1)
            self.assertIn(products[0]['run_id'],run_ids)
            result=export_competitor(snapshot,shop_id,self.root/(shop_id+'.json'))
            package=json.loads(Path(result['path']).read_text(encoding='utf-8'))
            self.assertTrue(all(r['run_id'] in run_ids for r in package['queries']['observations']['rows']))
            self.assertTrue(all(r['shop_id']==shop_id for r in package['queries']['competitor_products']['rows']))
            self.assertEqual(len(package['queries']['runs']['rows']),len(run_ids))
            for entry in package['queries'].values():
                self.assertTrue(all(w['run_id'] in run_ids for w in entry['source'].get('observationWindows',[])))
            with self.assertRaises(FileExistsError):
                export_competitor(snapshot,shop_id,Path(result['path']))

    def test_each_shop_origin_is_independent_and_never_rebased(self):
        self.run_snapshot(101,1);self.run_snapshot(202,2)
        s=self.snapshot();runs=s['queries']['runs']['rows'];obs=s['queries']['observations']['rows']
        first,second=sorted({r['shop_id'] for r in runs})
        configured=start_shop_tracking(self.root,runs,obs,first,'2026-10-04T04:00:00Z')
        path=Path(configured['config_path']);before=path.read_bytes()
        result,meta=build_portfolio_arrivals(self.root,runs,obs)
        summaries={r['shop_id']:r for r in result['new_arrival_summary']}
        self.assertEqual(summaries[first]['state'],'no_post_baseline_run')
        self.assertEqual(summaries[second]['state'],'not_configured')
        repeated=start_shop_tracking(self.root,runs,obs,first,'2026-10-05T04:00:00Z')
        self.assertEqual(repeated['status'],'unchanged');self.assertEqual(before,path.read_bytes())
        start_shop_tracking(self.root,runs,obs,second,'2026-10-04T05:00:00Z')
        result,meta=build_portfolio_arrivals(self.root,runs,obs)
        self.assertEqual(len(meta['per_shop']),2)
        self.assertEqual({r['started_at'] for r in result['new_arrival_summary']},{'2026-10-04T04:00:00Z','2026-10-04T05:00:00Z'})

    def test_artist_export_isolates_shops_and_keeps_channels_public(self):
        self.run_snapshot(101,1)
        self.run_snapshot(202,2,'已抢999件')
        self.run_snapshot(101,3,'已拼1件',status='partial')
        catalog = {'schema_version':1, 'version':'synthetic-export-test', 'checked_date':'2026-10-04',
                   'entities':[{'entity_id':'synthetic_person', 'name':'SYNTHETIC PERSON',
                                'entity_kind':'artist', 'match_terms':['SYNTHETIC SAME PRODUCT']}],
                   'event_rules':[], 'sources':[],
                   'channels':[{'channel_id':'studio', 'label':'SYNTHETIC public studio',
                                'url':'https://example.invalid/studio'},
                               {'channel_id':'organizer', 'label':'SYNTHETIC public organizer',
                                'url':'https://example.invalid/organizer'}]}
        path=self.root/'state/artist_research/catalog.json';path.parent.mkdir(parents=True)
        path.write_text(json.dumps(catalog),encoding='utf-8')
        snapshot=self.snapshot();queries=snapshot['queries']
        original=json.dumps(snapshot,sort_keys=True)
        for shop in queries['competitor_shops']['rows']:
            shop_id=shop['shop_id']
            own_runs={r['run_id'] for r in queries['runs']['rows'] if r['shop_id']==shop_id}
            own_obs={r['observation_id'] for r in queries['observations']['rows'] if r['run_id'] in own_runs}
            result=export_competitor(snapshot,shop_id,self.root/(shop_id+'-artists.json'))
            package=json.loads(Path(result['path']).read_text(encoding='utf-8'))
            for key in ('artist_watchlist','artist_research_summary'):
                exported=package['queries'][key]
                self.assertEqual(exported['rows'],[r for r in queries[key]['rows'] if r['shop_id']==shop_id])
                self.assertTrue(exported['rows'])
                self.assertEqual(exported['export_filter'],{'shop_id':shop_id,'run_ids':sorted(own_runs)})
                self.assertTrue(all(w['run_id'] in own_runs for w in exported['source'].get('observationWindows',[])))
            for item in package['queries']['artist_watchlist']['rows']:
                self.assertLessEqual(set(item['observation_ids']+item['latest_observation_ids']),own_obs)
            public=package['queries']['artist_source_channels']
            self.assertEqual(public['rows'],catalog['channels'])
            self.assertEqual(public['export_filter'],{'scope':'public_directory','shop_independent':True})
            self.assertNotIn('observationWindows',public['source'])
            self.assertIn('不按店铺或观察轮次过滤',''.join(public['source']['filters']))
            self.assertTrue(all('shop_id' not in row and 'run_id' not in row for row in public['rows']))
        self.assertEqual(json.dumps(snapshot,sort_keys=True),original)

    def test_legacy_export_without_research_queries_keeps_old_contents(self):
        self.run_snapshot(101,1)
        snapshot=self.snapshot()
        research_keys=('artist_watchlist','artist_research_summary','artist_source_channels',
                       'artist_heat_people','artist_heat_history','artist_heat_summary','artist_heat_sources',
                       'profit_opportunities','profit_opportunity_summary','profit_trials','profit_trial_summary')
        for key in research_keys:
            snapshot['queries'].pop(key,None)
        shop_id=snapshot['queries']['competitor_shops']['rows'][0]['shop_id']
        result=export_competitor(snapshot,shop_id,self.root/'legacy-profile.json')
        package=json.loads(Path(result['path']).read_text(encoding='utf-8'))
        self.assertTrue(all(key not in package['queries'] for key in research_keys))
        self.assertEqual(package['queries']['observations']['rows'],snapshot['queries']['observations']['rows'])
        self.assertEqual(len(package['queries']),14)  # Original queries plus own tracking/SOP state.

    def test_public_work_history_is_complete_for_each_shop_export(self):
        from pdd_monitor.artist_heat import save_html_snapshot
        from tests.test_artist_heat import fixture
        self.run_snapshot(101,1);self.run_snapshot(202,2)
        save_html_snapshot(self.root,fixture(),'2026-10-04T08:55:53Z')
        save_html_snapshot(self.root,fixture(updated='10-04 17:55',values=('110','85')),'2026-10-04T09:55:53Z')
        snapshot=self.snapshot()
        for shop in snapshot['queries']['competitor_shops']['rows']:
            result=export_competitor(snapshot,shop['shop_id'],self.root/(shop['shop_id']+'-heat.json'))
            package=json.loads(Path(result['path']).read_text(encoding='utf-8'))
            for key in ('artist_heat_people','artist_heat_history','artist_heat_summary','artist_heat_sources'):
                self.assertEqual(package['queries'][key]['rows'],snapshot['queries'][key]['rows'])
                self.assertTrue(package['queries'][key]['export_filter']['shop_independent'])
                self.assertNotIn('observationWindows',package['queries'][key]['source'])
            self.assertEqual(len(package['queries']['artist_heat_history']['rows']),4)

    def test_optional_empty_research_query_remains_explicit(self):
        self.run_snapshot(101,1)
        snapshot=self.snapshot()
        snapshot['queries'].pop('artist_research_summary')
        snapshot['queries'].pop('artist_source_channels')
        self.assertEqual(snapshot['queries']['artist_watchlist']['rows'],[])
        shop_id=snapshot['queries']['competitor_shops']['rows'][0]['shop_id']
        result=export_competitor(snapshot,shop_id,self.root/'empty-research-profile.json')
        package=json.loads(Path(result['path']).read_text(encoding='utf-8'))
        self.assertEqual(package['queries']['artist_watchlist']['rows'],[])
        self.assertNotIn('artist_research_summary',package['queries'])
        self.assertNotIn('artist_source_channels',package['queries'])

    def test_registered_target_binding_uses_stable_identity_not_name(self):
        self.run_snapshot(101,1)
        register_target(self.root,'SYNTHETIC SAME SHOP NAME')
        stable=register_target(self.root,'SYNTHETIC SAME SHOP NAME','https://mobile.yangkeduo.com/mall_page.html?mall_id=101')
        rows=self.snapshot()['queries']['competitor_targets']['rows']
        self.assertEqual(len(rows),2)
        self.assertEqual(sum(r['status']=='observed' for r in rows),1)
        self.assertEqual(sum(r['status']=='needs_identity' and r['shop_id'] is None for r in rows),1)

    def test_legacy_namespace_collision_is_not_a_binding(self):
        self.run_snapshot(101,1)
        register_target(self.root,'opaque target','https://mobile.yangkeduo.com/mall_page.html?mall_sn=101')
        rows=self.snapshot()['queries']['competitor_targets']['rows']
        conflict=next(r for r in rows if r.get('identity_conflict'))
        self.assertEqual(conflict['status'],'needs_identity');self.assertIsNone(conflict['shop_id'])

    def test_export_and_registry_reads_do_not_change_business_databases(self):
        self.run_snapshot(101,1)
        hashes=lambda:{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.data.glob('*.sqlite3')}
        before=hashes();s=self.snapshot()
        register_target(self.root,'SYNTHETIC','https://mobile.yangkeduo.com/mall_page.html?mall_id=101')
        self.snapshot();export_competitor(s,s['queries']['runs']['rows'][0]['shop_id'],self.root/'profile.json')
        self.assertEqual(before,hashes())

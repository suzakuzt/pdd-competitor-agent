import json
from pathlib import Path
import tempfile
import unittest
from pdd_monitor.artist_research import title_entities,event_clues,build_artist_research,load_catalog,_safe_url

def catalog():
    return {'schema_version':1,'version':'test','checked_date':'2026-10-04','entities':[
      {'entity_id':'yu','name':'刘宇','entity_kind':'artist','match_terms':['刘宇','Liu Yu']},
      {'entity_id':'yuning','name':'刘宇宁','entity_kind':'artist','match_terms':['刘宇宁']},
      {'entity_id':'yunqi','name':'云旗','entity_kind':'artist','match_terms':['云旗']},
      {'entity_id':'jiuwei','name':'KPL·九尾','entity_kind':'esports','match_terms':['九尾'],'required_context':'KPL'}],
      'sources':[{'artist':'刘宇','label':'test public source','url':'https://example.com/artist','verified_entry':True}],
      'channels':[], 'event_rules':[{'clue_id':'birthday','label':'生日词','terms':['生日']}],
      'excluded_generic_event_phrases':['生日礼物']}

def obs(ident,run,title,value=12,label='已拼',precision='exact_display'):
    return {'observation_id':ident,'run_id':run,'title':title,'sales_value':value,'sales_label':label,
            'sales_unit':'件','sales_precision':precision,'sales_raw':None if value is None else label+str(value)+'件'}

class ArtistResearchTests(unittest.TestCase):
    def test_longest_actual_match_with_unmatched_long_alias(self):
        self.assertEqual(title_entities('刘宇宁立牌',catalog()),['yuning'])

    def test_multi_person_cards_not_merged(self):
        self.assertEqual(set(title_entities('云旗刘宇刘宇',catalog())),{'yu','yunqi'})

    def test_esport_needs_context(self):
        self.assertEqual(title_entities('王者荣耀妲己九尾',catalog()),[])
        self.assertEqual(title_entities('KPL九尾',catalog()),['jiuwei'])

    def test_gift_not_birthday_event(self):
        self.assertFalse(event_clues('生日礼物立牌',catalog()))
        self.assertTrue(event_clues('刘宇生日立牌',catalog()))

    def fixture(self):
        runs=[{'run_id':r,'shop_id':s,'status':st,'end_boundary_observed':st=='complete'} for r,s,st in [('A1','A','complete'),('A2','A','partial'),('B1','B','complete')]]
        shops=[{'shop_id':'A','reference_run_id':'A1','latest_run_id':'A2'},{'shop_id':'B','reference_run_id':'B1','latest_run_id':'B1'}]
        rows=[obs(1,'A1','刘宇云旗生日'),obs(2,'A1','刘宇',10),obs(3,'A1','游戏卡',None),obs(4,'A2','刘宇宁'),obs(5,'B1','刘宇',22,'已抢')]
        return shops,runs,rows

    def test_shop_reference_and_latest_only(self):
        shops,runs,rows=self.fixture(); output=build_artist_research(catalog(),shops,runs,rows)
        a=[r for r in output['artist_watchlist'] if r['shop_id']=='A']
        y=next(r for r in a if r['artist_name']=='刘宇宁')
        self.assertEqual(y['reference_card_count'],0); self.assertEqual(y['latest_observation_ids'],[4]);self.assertTrue(y['latest_is_partial'])
        b=next(r for r in output['artist_watchlist'] if r['shop_id']=='B')
        self.assertEqual(b['observation_ids'],[5]);self.assertEqual(b['eligible_card_count'],1)
        self.assertIn('刘宇宁',y['title_search_text'])
        self.assertNotIn('云旗',y['title_search_text'])

    def test_summary_distinct_cards_not_sum_of_people(self):
        shops,runs,rows=self.fixture(); output=build_artist_research(catalog(),shops,runs,rows)
        a=output['artist_research_summary'][0]
        self.assertEqual((a['artist_count'],a['reference_artist_count'],a['artist_card_count'],a['artist_eligible_card_count']),(3,2,2,1))
        self.assertEqual(a['event_clues'][0]['count'],1)

    def test_missing_sales_unknown_and_exact_threshold(self):
        shops,runs,rows=self.fixture(); rows += [obs(6,'A1','云旗',None),obs(7,'A1','云旗',20,precision='lower_bound')]
        y=next(r for r in build_artist_research(catalog(),shops,runs,rows)['artist_watchlist'] if r['shop_id']=='A' and r['artist_name']=='云旗')
        self.assertEqual((y['reference_card_count'],y['eligible_card_count']),(3,1))

    def test_invalid_shop_boundary(self):
        shops,runs,rows=self.fixture(); shops[0]['latest_run_id']='B1'
        with self.assertRaises(ValueError):build_artist_research(catalog(),shops,runs,rows)

    def test_source_directory_does_not_mutate_input(self):
        shops,runs,rows=self.fixture(); cat=catalog();before=json.dumps([cat,shops,runs,rows],sort_keys=True)
        output=build_artist_research(cat,shops,runs,rows);output['artist_watchlist'][0]['sources'][0]['label']='changed'
        self.assertEqual(before,json.dumps([cat,shops,runs,rows],sort_keys=True))

    def test_unsafe_source_urls_rejected(self):
        for url in ['javascript:alert(1)','https://u:p@example.com','https://example.com\n.evil.test/',' https://example.com']:
            with self.subTest(url=url), self.assertRaises(ValueError):_safe_url(url)

    def test_missing_and_invalid_catalog_no_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cat,hashes=load_catalog(root)
            self.assertEqual(cat['version'],'unconfigured');self.assertEqual(hashes,{})
            file=root/'state/artist_research/catalog.json';file.parent.mkdir(parents=True)
            bad=catalog();bad['entities'][0]['match_terms']=[''];file.write_text(json.dumps(bad),encoding='utf-8')
            before=file.read_bytes()
            with self.assertRaises(ValueError):load_catalog(root)
            self.assertEqual(before,file.read_bytes())

    def test_candidate_source_not_verified(self):
        shops,runs,rows=self.fixture();cat=catalog();cat['sources'][0]['verified_entry']=False
        y=next(r for r in build_artist_research(cat,shops,runs,rows)['artist_watchlist'] if r['artist_name']=='刘宇')
        self.assertEqual(y['source_status'],'入口待核验')

if __name__=='__main__':unittest.main()

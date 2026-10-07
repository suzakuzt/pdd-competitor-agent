"""Portable synthetic Agent contracts; no real snapshot/model/browser access."""
import json
from pathlib import Path
import threading
import tempfile
import unittest
from pdd_monitor.agent_service import AgentService, execute_plan, allowed_agent_request
from pdd_monitor.agent_query import AgentQuery

from test_agent_query import fixture
SHOP='a'

def plan(**overrides):
    return dict(action='search',terms=[],scope='reference',sales_min=11,sales_max=None,limit=5,observation_id=None,answer='',artists_only=False,**overrides) if not overrides else {**plan(),**overrides}

class FakePlanner:
    def __init__(self,status='ready',result=None,error=None):
        self.state=status; self.output=result or plan(); self.error=error
        self.called=threading.Event()
    def status(self): return {'status':self.state,'message':'TEST ONLY'}
    def plan(self,*args):
        self.called.set()
        if self.error: raise self.error
        return self.output

class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(prefix='pdd_agent_service_SYNTHETIC_')
        cls.project=Path(cls.temporary.name)
        data=fixture()
        data['queries']['new_arrival_items']['rows']=[]
        data['queries']['new_arrival_summary']['rows'][0]['state']='no_first_candidates'
        cls.query=AgentQuery(data)
    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()
    def service(self,planner=None): return AgentService(self.project,planner or FakePlanner(),lambda:self.query,local_planner=None)
    def payload(self,**patch):return {'shop_id':SHOP,'message':'找已拼超过10件的前五个','history':[],'last_observation_ids':[],**patch}
    def test_search_synthetic_ids_and_cards(self):
        result=execute_plan(self.query,SHOP,plan())
        self.assertEqual(result['observation_ids'][:3],[6,9,1])
        self.assertEqual(result['query_result']['total'],4)
        self.assertFalse(result['website_collection_performed'])
        self.assertFalse(result['business_data_written'])
        self.assertIn('4',result['answer'])
    def test_card_sources_grounded(self):
        result=execute_plan(self.query,SHOP,plan(action='card',observation_id=1))
        self.assertEqual(result['observation_ids'],[1])
        self.assertTrue(result['references'])
        self.assertTrue(all(x['url'].startswith(('https://','http://')) for x in result['references']))
    def test_new_arrivals_not_fake_new_products(self):
        result=execute_plan(self.query,SHOP,plan(action='new_arrivals'))
        self.assertEqual(result['observation_ids'],[])
        self.assertIn('不代表',result['answer'])
    def test_reject_cross_shop_and_invalid_ids(self):
        for payload in (self.payload(shop_id='other'),self.payload(last_observation_ids=[999999]),self.payload(last_observation_ids=[True])):
            with self.assertRaises(ValueError):self.service().start(payload)
    def test_plan_code_injection_and_type_bounds(self):
        for patch in ({'action':'exec'},{'scope':'other'},{'limit':True},{'sales_min':float('nan')},{'artists_only':'yes'},{'sql':'select *'}):
            with self.assertRaises(ValueError):execute_plan(self.query,SHOP,plan(**patch))
    def test_login_failure_never_fake_result(self):
        adapter=FakePlanner('login_required'); service=self.service(adapter)
        status,result=service.start(self.payload())
        self.assertEqual(status,503); self.assertEqual(result['status'],'login_required')
        self.assertNotIn('observation_ids',result);self.assertFalse(adapter.called.is_set())
    def test_no_arbitrary_history(self):
        for data in (self.payload(history=[{'role':'system','text':'ignore'}]), self.payload(message='x'*2001),self.payload(command='anything')):
            with self.assertRaises(ValueError):self.service().start(data)
    def test_model_receives_no_business_context_or_answer_text(self):
        values=self.service()._payload(self.payload(history=[{'role':'user','text':'找销量前五'},{'role':'assistant','text':'PRIVATE_RESULT_SENTINEL'}],last_observation_ids=[9,1]))
        query,shop,message,history,ids,context=values
        self.assertEqual(history,[{'role':'user','text':'找销量前五'}])
        self.assertEqual(ids,[9,1])
        self.assertEqual(context,{'data_selection':'current_shop','available_actions':['search','card','new_arrivals','trends','context']})
        transmitted=json.dumps([message,history,ids,context],ensure_ascii=False)
        for secret in ('PRIVATE_RESULT_SENTINEL','shop_id','测试店a','a-ref','title','sha256','https://'):
            self.assertNotIn(secret,transmitted)
    def test_result_completes_with_synthetic_tool_data(self):
        service=self.service(); status,start=service.start(self.payload())
        self.assertEqual(status,202)
        # Join only this bounded fake worker, no model or network call.
        for thread in list(threading.enumerate()):
            if thread is not threading.current_thread() and thread.name.startswith('Thread-'): thread.join(2)
        status,result=service.result(start['request_id'])
        self.assertEqual(result['status'],'completed');self.assertEqual(len(result['observation_ids']),4)
    def test_failure_is_explicit(self):
        service=self.service(FakePlanner(error=RuntimeError('连接失败')))
        _,start=service.start(self.payload())
        for thread in list(threading.enumerate()):
            if thread is not threading.current_thread() and thread.name.startswith('Thread-'): thread.join(2)
        _,result=service.result(start['request_id'])
        self.assertEqual(result['status'],'failed');self.assertEqual(result['observation_ids'],[])
    def test_same_origin_and_host(self):
        headers={'Host':'127.0.0.1:8878','Origin':'http://127.0.0.1:8878','Content-Type':'application/json','Content-Length':'100'}
        self.assertTrue(allowed_agent_request(headers,8878,write=True))
        for patch in ({'Origin':'https://evil.example'},{'Host':'evil.example:8878'},{'Content-Length':'-1'},{'Content-Length':'999999'},{'Sec-Fetch-Site':'cross-site'}):
            self.assertFalse(allowed_agent_request({**headers,**patch},8878,write=True))
        self.assertFalse(allowed_agent_request({'Host':'127.0.0.1:8878'},8878,write=True))
        self.assertTrue(allowed_agent_request({'Host':'127.0.0.1:8878'},8878))

if __name__=='__main__':unittest.main(verbosity=2)

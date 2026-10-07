import threading
import unittest
from pdd_monitor.agent_service import AgentService
from pdd_monitor.agent_query import AgentQuery
from test_agent_query import fixture

class NoModel:
    def __init__(self): self.calls=0
    def status(self):
        self.calls+=1
        return {'status':'configuration_required','message':'Synthetic missing key'}
    def plan(self,*args):
        self.calls+=1
        raise AssertionError('No model should be called')

class LocalServiceTests(unittest.TestCase):
    def setUp(self):
        self.query=AgentQuery(fixture())
        self.model=NoModel()
        self.service=AgentService('.',planner=self.model,query_factory=lambda:self.query)
    def run_query(self,message,**patch):
        code,start=self.service.start({'shop_id':'a','message':message,**patch})
        self.assertEqual(code,202)
        for thread in list(threading.enumerate()):
            if thread is not threading.current_thread() and thread.name.startswith('Thread-'):thread.join(2)
        return self.service.result(start['request_id'])[1]
    def test_complete_new_list_does_not_use_latest_partial_or_first_seen(self):
        for message in ('新品里面热销前5','帮我查下这家店铺新品排名前5','上新中销量最高的5个商品'):
            result=self.run_query(message)
            self.assertEqual(result['status'],'completed')
            self.assertEqual(result['execution_mode'],'local')
            self.assertEqual(result['tool_calls'][0]['tool'],'search')
            self.assertIsNone(result['tool_calls'][0]['criteria']['sales_min'])
            self.assertEqual(result['query_result']['population_count'],9)
            self.assertEqual(result['observation_ids'][:3],[6,9,1])
            self.assertEqual(result['scope']['run_ids'],['a-ref'])
        self.assertEqual(self.model.calls,0)
    def test_new_records_remain_separate_from_new_list(self):
        result=self.run_query('新增记录')
        self.assertEqual(result['tool_calls'][0]['tool'],'new_arrivals')
        self.assertEqual(self.model.calls,0)
    def test_previous_answer_order_controls_card_followup(self):
        result=self.run_query('第二个商品的参考来源',last_observation_ids=[9,1,8])
        self.assertEqual(result['observation_ids'],[1])
        self.assertEqual(result['execution_mode'],'local')
    def test_cross_shop_reference_rejected_before_routing(self):
        with self.assertRaises(ValueError):self.run_query('第二个商品详情',last_observation_ids=[12])
        self.assertEqual(self.model.calls,0)
    def test_unknown_request_retains_model_status_failure(self):
        code,result=self.service.start({'shop_id':'a','message':'仅今天上架的王一博图片并分析活动'})
        self.assertEqual(code,503)
        self.assertEqual(result['status'],'configuration_required')
        self.assertEqual(self.model.calls,1)
    def test_explicit_threshold_preserves_strict_sales_gate(self):
        result=self.run_query('已拼大于10件的前5个商品')
        self.assertEqual(result['query_result']['total'],4)
        self.assertEqual(result['tool_calls'][0]['criteria']['sales_min'],11)

if __name__=='__main__':unittest.main()

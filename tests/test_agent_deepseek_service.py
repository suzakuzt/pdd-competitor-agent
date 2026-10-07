"""Synthetic DeepSeek -> validated plan -> shop-local evidence integration."""
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError

from pdd_monitor.agent_deepseek import API_ENDPOINT, DeepSeekPlanner
from pdd_monitor.agent_query import AgentQuery
from pdd_monitor.agent_service import AgentService
from test_agent_query import fixture
from test_agent_service import plan


class Response(io.BytesIO):
    headers = {'Content-Type': 'application/json'}
    def geturl(self): return API_ENDPOINT
    def getcode(self): return 200


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.query = AgentQuery(fixture())
        self.sent = []

    def service(self, output=None, http_error=None):
        def send(request, timeout):
            self.sent.append(json.loads(request.data))
            if http_error:
                raise HTTPError(API_ENDPOINT, http_error, 'unsafe-key-body', {}, io.BytesIO(b'private-remote-error'))
            return Response(json.dumps({'object':'chat.completion','choices':[{'index':0,'finish_reason':'stop',
                'message':{'role':'assistant','content':json.dumps(output or plan())}}]}).encode())
        planner = DeepSeekPlanner(lambda:'sk-synthetic-do-not-use', opener=send)
        return AgentService(Path(self.temp.name), planner=planner, query_factory=lambda:self.query, local_planner=None)

    def run_query(self, service, shop='a', ids=None):
        # Execute only the bounded worker synchronously; no real thread/network.
        request = {'shop_id':shop,'message':'找已拼大于10件的前5个','history':[], 'last_observation_ids':ids or []}
        with unittest.mock.patch('pdd_monitor.agent_service.threading.Thread') as worker:
            def start():
                args = worker.call_args.kwargs
                args['target'](*args['args'])
            worker.return_value.start.side_effect = start
            code, started = service.start(request)
        self.assertEqual(code,202)
        return service.result(started['request_id'])[1]

    def test_query_different_shops_keeps_independent_scope(self):
        service = self.service()
        a = self.run_query(service)
        b = self.run_query(service,'b')
        self.assertEqual(a['observation_ids'],[6,9,1,8])
        self.assertEqual(b['observation_ids'],[12])
        self.assertEqual(a['scope']['shop_id'],'a')
        self.assertEqual(b['scope']['shop_id'],'b')
        self.assertEqual(len(self.sent),2)
        transmitted = json.dumps(self.sent,ensure_ascii=False)
        for private in ('测试立牌','b-only','a-ref','RAW_JSON_MUST_NOT_LEAK','data:image','shop_id'):
            self.assertNotIn(private,transmitted)

    def test_cross_shop_followup_id_rejected_before_api(self):
        service = self.service()
        with self.assertRaises(ValueError):
            service.start({'shop_id':'a','message':'看这个','last_observation_ids':[12]})
        self.assertEqual(self.sent,[])

    def test_model_cannot_bind_a_foreign_card(self):
        result = self.run_query(self.service(plan(action='card',observation_id=12)))
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['observation_ids'],[])

    def test_http_failure_has_no_fallback_or_secret(self):
        service = self.service(http_error=402)
        result = self.run_query(service)
        self.assertEqual(result['status'],'failed')
        self.assertIn('余额不足', result['message'])
        self.assertEqual(result['observation_ids'],[])
        self.assertEqual(len(self.sent),1)
        self.assertFalse(service.busy)
        for private in ('unsafe-key-body','private-remote-error','sk-synthetic'):
            self.assertNotIn(private,str(result))


if __name__=='__main__': unittest.main()

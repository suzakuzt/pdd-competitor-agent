import threading
import time
import unittest
from pdd_monitor.profit_service import ProfitService, allowed_profit_request


class ProfitServiceTests(unittest.TestCase):
    def service(self, publisher=None, **kwargs):
        return ProfitService('temp-test',publisher or (lambda root:{'status':'built'}),
            validator=kwargs.pop('validator',lambda root,trial:None),
            saver=kwargs.pop('saver',lambda root,trial,revision:{'status':'saved','revision':revision+1}),
            evaluator=kwargs.pop('evaluator',lambda trial:{'result':None}),**kwargs)

    def done(self, service):
        for _ in range(200):
            state=service.status()
            if state['status']!='running':return state
            time.sleep(.005)
        self.fail('Service did not complete')

    def test_same_origin_and_bounded_json_required(self):
        headers={'Host':'127.0.0.1:8878','Origin':'http://127.0.0.1:8878','Content-Type':'application/json','Content-Length':'128'}
        self.assertTrue(allowed_profit_request(headers,8878))
        for key,value in [('Host','evil.test'),('Origin','null'),('Origin','https://evil.test'),('Content-Length','65537'),('Content-Length','0'),('Content-Length','oops'),('Content-Type','text/plain')]:
            with self.subTest(key=key,value=value):self.assertFalse(allowed_profit_request({**headers,key:value},8878))

    def test_preview_does_not_save_or_publish(self):
        calls=[]
        service=self.service(publisher=lambda root:calls.append('publish'),saver=lambda *args:calls.append('save'))
        self.assertFalse(service.preview({'trial':{}})['saved']);self.assertEqual(calls,[])

    def test_reference_mismatch_rejected_before_mutation(self):
        def fail(root,trial):raise ValueError('wrong shop')
        calls=[];service=self.service(validator=fail,saver=lambda *args:calls.append('save'))
        with self.assertRaises(ValueError):service.preview({'trial':{}})
        with self.assertRaises(ValueError):service.start({'trial':{},'expected_revision':0})
        self.assertEqual(calls,[])

    def test_save_serialized_and_success_requires_published_snapshot(self):
        release=threading.Event();calls=[]
        def publish(root):calls.append(root);release.wait(1);return {'status':'built'}
        service=self.service(publisher=publish)
        self.assertEqual(service.start({'trial':{},'expected_revision':0})[0],202)
        self.assertEqual(service.start({'trial':{},'expected_revision':0})[0],409)
        release.set();state=self.done(service)
        self.assertEqual(state['status'],'succeeded');self.assertTrue(state['receipt']['dashboard_built']);self.assertEqual(len(calls),1)

    def test_failed_build_keeps_saved_receipt_and_never_reports_success(self):
        service=self.service(publisher=lambda root:{'status':'failed'})
        service.start({'trial':{},'expected_revision':0});state=self.done(service)
        self.assertEqual(state['status'],'failed');self.assertEqual(state['receipt']['revision'],1)
        self.assertNotIn('dashboard_built',state['receipt'])

    def test_unknown_parameters_and_boolean_revision_rejected(self):
        service=self.service()
        for payload in [{'trial':{},'url':'https://invalid'},{'trial':{},'expected_revision':True},{'trial':{},'expected_revision':-1}]:
            with self.assertRaises(ValueError):service.start(payload)
        with self.assertRaises(ValueError):service.preview({'trial':{},'path':'elsewhere'})

if __name__=='__main__':unittest.main()

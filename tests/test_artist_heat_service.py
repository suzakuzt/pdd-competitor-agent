import threading
import time
import unittest
from unittest.mock import patch
from pdd_monitor.artist_heat_service import ArtistHeatService, allowed_refresh_request


class HeatServiceTests(unittest.TestCase):
    def headers(self):
        return {'Host':'127.0.0.1:8878','Origin':'http://127.0.0.1:8878','Content-Type':'application/json','Content-Length':'2'}

    def test_rejects_foreign_or_missing_origin_and_unbounded_input(self):
        self.assertTrue(allowed_refresh_request(self.headers(),8878))
        for field, value in [('Host','evil.example'),('Origin','https://evil.example'),('Origin','null'),('Origin',''),
                             ('Content-Type','text/plain'),('Content-Length','-1'),('Content-Length','65'),('Content-Length','oops')]:
            h=self.headers();h[field]=value
            with self.subTest(field=field,value=value):self.assertFalse(allowed_refresh_request(h,8878))

    def wait_finished(self, service):
        for _ in range(100):
            if service.status()['status']!='running':return service.status()
            time.sleep(.005)
        self.fail('Job did not finish')

    def test_single_job_and_cooldown_do_not_repeat_collection(self):
        release=threading.Event();calls=[];clock=[10.0]
        def collect(project):
            calls.append(project);release.wait(1);return {'status':'saved','snapshot_id':'test'}
        service=ArtistHeatService('test-root',collector=collect,clock=lambda:clock[0])
        self.assertEqual(service.start()[0],202)
        self.assertEqual(service.start()[0],409)
        release.set();self.assertEqual(self.wait_finished(service)['status'],'succeeded')
        self.assertEqual(service.start()[0],429);self.assertEqual(len(calls),1)
        clock[0]=71;self.assertEqual(service.start()[0],202)
        self.wait_finished(service);self.assertEqual(len(calls),2)

    def test_failure_has_no_fake_success_no_retry_or_leaked_error(self):
        calls=[]
        def fail(project):calls.append(project);raise ValueError('secret-token-upstream-html')
        service=ArtistHeatService('test-root',collector=fail)
        self.assertEqual(service.start()[0],202)
        result=self.wait_finished(service)
        self.assertEqual(result['status'],'failed');self.assertNotIn('secret-token',str(result));self.assertEqual(calls,['test-root'])

    def test_failed_receipt_is_not_a_success(self):
        for receipt in [None, {}, {'status':'failed'}, {'status':'unrecognized'}]:
            with self.subTest(receipt=receipt):
                service=ArtistHeatService('test-root',collector=lambda project:receipt)
                service.start();self.assertEqual(self.wait_finished(service)['status'],'failed')

    def test_thread_start_failure_does_not_leave_running_job(self):
        service=ArtistHeatService('test-root',collector=lambda project:None)
        with patch('pdd_monitor.artist_heat_service.threading.Thread.start',side_effect=RuntimeError('no thread')):
            code,status=service.start()
        self.assertEqual(code,503);self.assertEqual(status['status'],'failed')

    def test_success_requires_reviewed_dashboard_publication_when_configured(self):
        calls=[]
        service=ArtistHeatService('root',collector=lambda project:{'status':'saved'},
            publisher=lambda project:(calls.append(project) or {'status':'built'}))
        service.start();result=self.wait_finished(service)
        self.assertEqual(calls,['root']);self.assertTrue(result['receipt']['dashboard_built'])
        for publication in [None,{}, {'status':'failed'}]:
            with self.subTest(publication=publication):
                service=ArtistHeatService('root',collector=lambda project:{'status':'saved'},publisher=lambda project:publication)
                service.start();self.assertEqual(self.wait_finished(service)['status'],'failed')

if __name__=='__main__':unittest.main()

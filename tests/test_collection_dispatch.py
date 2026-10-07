"""Queue handoff tests with synthetic workers and temporary service state."""

from pathlib import Path
import tempfile
import threading
import unittest

from pdd_monitor.collection_service import CollectionService


A = 'shop_' + 'a' * 24
B = 'shop_' + 'b' * 24


class StopLoop(BaseException):
    pass


class ObservedWake:
    """Keep real Event semantics; expose when the scheduler starts waiting."""

    def __init__(self):
        self.event = threading.Event()
        self.condition = threading.Condition()
        self.wait_count = 0
        self.stopped = False

    def set(self):
        self.event.set()

    def clear(self):
        self.event.clear()

    def wait(self, timeout=None):
        with self.condition:
            self.wait_count += 1
            self.condition.notify_all()
        result = self.event.wait(timeout)
        if self.stopped:
            raise StopLoop()
        return result

    def waiting_at_least(self, count):
        with self.condition:
            return self.condition.wait_for(lambda: self.wait_count >= count, timeout=2)

    def stop(self):
        self.stopped = True
        self.event.set()


class Service(CollectionService):
    def _shop(self, shop_id):
        if shop_id not in (A, B):
            raise ValueError('Unknown synthetic shop')
        return {'shop_name': shop_id}


class CollectionDispatchTests(unittest.TestCase):
    def test_finished_worker_wakes_queued_shop_without_poll_delay(self):
        for first_fails in (False, True):
            with self.subTest(first_fails=first_fails), tempfile.TemporaryDirectory() as temporary:
                first_started = threading.Event()
                release_first = threading.Event()
                second_started = threading.Event()
                second_saved = threading.Event()
                calls = []

                def worker(job):
                    calls.append(job['shop_id'])
                    if job['shop_id'] == A:
                        first_started.set()
                        if not release_first.wait(5):
                            raise AssertionError('Synthetic first worker was not released')
                        if first_fails:
                            raise RuntimeError('Synthetic worker failure')
                    else:
                        second_started.set()
                    return {'status': 'complete', 'message': 'Synthetic result'}

                service = Service(Path(temporary), runner=worker, autostart=False)
                wake = ObservedWake()
                service.wake = wake
                original_save = service._save

                def save():
                    original_save()
                    if calls == [A, B] and service.active is None:
                        second_saved.set()

                service._save = save

                def loop():
                    try:
                        service._loop()
                    except StopLoop:
                        pass

                first = service.start({'shop_id': A, 'kind': 'shop'})[1]['job']
                scheduler = threading.Thread(target=loop, daemon=True)
                scheduler.start()
                try:
                    self.assertTrue(first_started.wait(2))
                    # Consume the first request's wake while its worker is active.
                    self.assertTrue(wake.waiting_at_least(2))
                    second = service.start({'shop_id': B, 'kind': 'shop'})[1]['job']
                    # Consume B's request wake too; A still owns the worker slot.
                    self.assertTrue(wake.waiting_at_least(3))
                    self.assertFalse(second_started.is_set())
                    self.assertEqual(service.active, first['id'])
                    release_first.set()
                    self.assertTrue(second_started.wait(2),
                                    'Queued shop still waits for the 15-second polling tick')
                    self.assertTrue(second_saved.wait(2))
                    with service.lock:
                        jobs = {job['id']: job for job in service.state['jobs']}
                        self.assertEqual(jobs[first['id']]['status'], 'failed' if first_fails else 'complete')
                        self.assertEqual(jobs[second['id']]['status'], 'complete')
                    self.assertEqual(calls, [A, B])
                finally:
                    release_first.set()
                    wake.stop()
                    scheduler.join(2)
                    self.assertFalse(scheduler.is_alive())


if __name__ == '__main__':
    unittest.main()

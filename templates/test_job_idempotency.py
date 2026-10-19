"""Background job safety: idempotent enqueue, bounded retries, dead letters, concurrent workers, single scheduling
(POST-070, POST-071, POST-072 and neighbours).

Needs `adapter.py` exposing JobAdapter (see adapter_stub.py). Use a disposable queue and a fake clock. Run:
    python3 -m unittest -v test_job_idempotency
Save the output to a file and record it with `ledger.py verify --artifact <that file>`.
A pass shows these behaviors with the workers and scheduler the adapter runs, locally. It does not prove the
production broker's delivery guarantees, alerting on dead letters, or behavior across real separate machines.
"""
import threading
import unittest

from adapter import JobAdapter

WORKERS = 8


class JobIdempotency(unittest.TestCase):
    def setUp(self):
        self.app = JobAdapter()
        self.app.start()
        self.addCleanup(self.app.stop)

    def in_parallel(self, fn, count=WORKERS, timeout=30):
        barrier, problems = threading.Barrier(count), []

        def run():
            try:
                barrier.wait(timeout=10)
                fn()
            except Exception as error:  # a crashed worker must fail the test, not vanish with its thread
                problems.append(repr(error))

        threads = [threading.Thread(target=run, daemon=True) for _ in range(count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout)
        self.assertFalse(any(t.is_alive() for t in threads), 'a worker never went idle: retries look unbounded')
        self.assertEqual(problems, [], 'every worker must finish without raising')

    def drain(self):
        self.in_parallel(self.app.run_worker_until_idle, count=1)

    def test_a_job_runs_once(self):
        self.app.enqueue('order-1')
        self.drain()
        self.assertEqual(self.app.applied_count('order-1'), 1, 'sanity check: an enqueued job must run, or the rest proves nothing')

    def test_same_job_enqueued_twice_applies_once(self):
        self.app.enqueue('order-1')
        self.app.enqueue('order-1')  # a double click, a retried request, a redelivered message
        self.drain()
        self.assertEqual(self.app.applied_count('order-1'), 1)

    def test_transient_failure_is_retried_then_applies_once(self):
        self.app.enqueue_failing('flaky', failures=1)
        self.drain()
        self.assertEqual(self.app.applied_count('flaky'), 1, 'one failure must be retried, not lose the work')
        self.assertEqual(self.app.attempts('flaky'), 2)
        self.assertNotIn('flaky', self.app.dead_letters())

    def test_permanent_failure_is_bounded_then_dead_lettered(self):
        self.app.enqueue_failing('doomed', failures=10**6)
        self.drain()
        limit = self.app.max_attempts()
        self.assertGreater(limit, 1, 'a job that is never retried loses work on the first transient error')
        self.assertEqual(self.app.attempts('doomed'), limit, 'retries must stop at the configured bound')
        self.assertIn('doomed', self.app.dead_letters(), 'an exhausted job must be visible in the dead-letter place')
        self.assertEqual(self.app.applied_count('doomed'), 0)

    def test_concurrent_workers_do_not_double_apply(self):
        keys = [f'order-{n}' for n in range(5)]
        for key in keys:
            self.app.enqueue(key)
        self.in_parallel(self.app.run_worker_until_idle)
        self.assertEqual({k: self.app.applied_count(k) for k in keys}, dict.fromkeys(keys, 1),
                         'each job must apply exactly once across parallel workers')

    def test_scheduled_job_is_not_double_scheduled(self):
        self.app.schedule('nightly-report', 3600)
        self.app.schedule('nightly-report', 3600)  # a second server, or a restart, registers it again
        self.app.advance_clock(3600)
        self.in_parallel(self.app.run_scheduler, count=2)
        self.drain()
        self.assertEqual(self.app.applied_count('nightly-report'), 1, 'one period must produce one run')
        self.app.advance_clock(3600)
        self.app.run_scheduler()
        self.drain()
        self.assertEqual(self.app.applied_count('nightly-report'), 2, 'sanity check: the schedule must keep firing')


if __name__ == '__main__':
    unittest.main(verbosity=2)

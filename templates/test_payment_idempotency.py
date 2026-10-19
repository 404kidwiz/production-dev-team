"""Payment webhook safety (signature, replay, concurrent duplicates, tampering).

Needs `adapter.py` exposing PaymentAdapter (see adapter_stub.py). Use a disposable instance and a TEST webhook
secret only. Run:
    python3 -m unittest -v test_payment_idempotency
Save the output to a file and record it with `ledger.py verify --artifact <that file>`.
A pass shows these behaviors against the local instance. It does not prove the provider's real signing scheme,
reconciliation, refunds or out-of-order delivery; add tests for those that your provider can produce.
"""
import threading
import unittest

from adapter import PaymentAdapter

REJECTED = {400, 401, 403}
CENTS = 1250


class PaymentWebhooks(unittest.TestCase):
    def setUp(self):
        self.app = PaymentAdapter()
        self.app.start()
        self.addCleanup(self.app.stop)
        self.user = self.app.create_user('payer')
        self.payable = self.app.create_payable(self.user, CENTS)

    def applied(self):
        return self.app.applied_cents(self.user, self.payable)

    def event(self, event_id='evt_1', cents=CENTS):
        return self.app.make_event(event_id, self.payable, cents)

    def test_signed_event_is_applied_once(self):
        body = self.event()
        status = self.app.post_webhook(body, self.app.sign(body))
        self.assertEqual(status, 200, 'sanity check: a correctly signed event must be accepted')
        self.assertEqual(self.applied(), CENTS)

    def test_missing_signature_is_rejected_and_has_no_effect(self):
        status = self.app.post_webhook(self.event(), None)
        self.assertIn(status, REJECTED)
        self.assertEqual(self.applied(), 0)

    def test_wrong_signature_is_rejected_and_has_no_effect(self):
        status = self.app.post_webhook(self.event(), 'deadbeef' * 8)
        self.assertIn(status, REJECTED)
        self.assertEqual(self.applied(), 0)

    def test_tampered_body_with_a_valid_old_signature_is_rejected(self):
        original = self.event('evt_t', 100)
        signature = self.app.sign(original)
        tampered = self.event('evt_t', 999999)
        self.assertIn(self.app.post_webhook(tampered, signature), REJECTED)
        self.assertEqual(self.applied(), 0)

    def test_replayed_event_is_applied_only_once(self):
        body = self.event()
        signature = self.app.sign(body)
        for _ in range(3):
            self.app.post_webhook(body, signature)
        self.assertEqual(self.applied(), CENTS, 'replaying the same event id must not apply it again')

    def test_concurrent_duplicates_are_applied_only_once(self):
        body = self.event('evt_race')
        signature = self.app.sign(body)
        barrier = threading.Barrier(8)
        problems = []

        def fire():
            try:
                barrier.wait(timeout=10)
                self.app.post_webhook(body, signature)
            except Exception as error:  # a crashed delivery must fail the test, not vanish with its thread
                problems.append(repr(error))

        threads = [threading.Thread(target=fire) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(problems, [], 'every concurrent delivery must complete without raising')
        self.assertEqual(self.applied(), CENTS, 'parallel deliveries of one event must be applied once')

    def test_distinct_events_both_apply(self):
        a, b = self.event('evt_a', 400), self.event('evt_b', 300)
        self.app.post_webhook(a, self.app.sign(a))
        self.app.post_webhook(b, self.app.sign(b))
        self.assertEqual(self.applied(), 700, 'idempotency must key on the event id, not swallow different events')


if __name__ == '__main__':
    unittest.main(verbosity=2)

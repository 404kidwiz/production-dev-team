"""Two-user object-level isolation (controls POST-010 and neighbours).

Needs `adapter.py` exposing IsolationAdapter (see adapter_stub.py). Run:
    python3 -m unittest -v test_two_user_isolation
Save the output to a file and record it with `ledger.py verify --artifact <that file>`.
A pass proves isolation for the object type and operations the adapter exercises, in a local run, nothing more.
"""
import unittest

from adapter import IsolationAdapter

DENIED = {401, 403, 404}


class TwoUserIsolation(unittest.TestCase):
    def setUp(self):
        self.app = IsolationAdapter()
        self.app.start()
        self.addCleanup(self.app.stop)
        self.alice = self.app.create_user('alice')
        self.bob = self.app.create_user('bob')
        self.alice_object = self.app.create_object(self.alice, 'alice-private')
        self.bob_object = self.app.create_object(self.bob, 'bob-private')

    def assertDenied(self, status, action):
        self.assertIn(status, DENIED, f'{action}: expected a denial, got {status}')

    def test_owner_can_use_their_own_object(self):
        status, _ = self.app.read(self.alice, self.alice_object)
        self.assertEqual(status, 200, 'sanity check: owner must be able to read, or the denials below prove nothing')
        self.assertEqual(self.app.update(self.alice, self.alice_object, 'renamed'), 200)
        self.assertEqual(self.app.label_of(self.alice, self.alice_object), 'renamed')

    def test_other_user_cannot_read(self):
        status, body = self.app.read(self.bob, self.alice_object)
        self.assertDenied(status, 'bob reads alice object')
        self.assertNotIn('alice-private', str(body), 'denied response must not leak the data')

    def test_other_user_cannot_update(self):
        self.assertDenied(self.app.update(self.bob, self.alice_object, 'hacked'), 'bob updates alice object')
        self.assertEqual(self.app.label_of(self.alice, self.alice_object), 'alice-private', 'the write must not have happened')

    def test_other_user_cannot_delete(self):
        self.assertDenied(self.app.delete(self.bob, self.alice_object), 'bob deletes alice object')
        self.assertEqual(self.app.label_of(self.alice, self.alice_object), 'alice-private', 'the object must still exist')

    def test_listing_excludes_other_users_objects(self):
        self.assertNotIn(self.alice_object, self.app.list_ids(self.bob))
        self.assertIn(self.bob_object, self.app.list_ids(self.bob))

    def test_unauthenticated_requests_are_denied(self):
        status, _ = self.app.read(None, self.alice_object)
        self.assertDenied(status, 'anonymous read')
        self.assertDenied(self.app.update(None, self.alice_object, 'x'), 'anonymous update')
        self.assertDenied(self.app.delete(None, self.alice_object), 'anonymous delete')
        self.assertEqual(self.app.label_of(self.alice, self.alice_object), 'alice-private',
                         'a denied anonymous write or delete must not have happened')

    def test_guessing_neighbouring_ids_never_succeeds(self):
        if not all(isinstance(i, int) for i in (self.alice_object, self.bob_object)):
            self.skipTest('ids are not integers; add an enumeration probe for your id scheme')
        for guess in range(1, max(self.alice_object, self.bob_object) + 5):
            if guess == self.bob_object:
                continue  # bob's own object is allowed
            status, _ = self.app.read(self.bob, guess)
            self.assertDenied(status, f'bob probes id {guess}')


if __name__ == '__main__':
    unittest.main(verbosity=2)

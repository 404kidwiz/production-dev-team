"""Upload safety: authorization, size and type limits, path traversal, expiring links (POST-042 and neighbours).

Needs `adapter.py` exposing UploadAdapter (see adapter_stub.py). Use disposable storage only. Run:
    python3 -m unittest -v test_upload_safety
Save the output to a file and record it with `ledger.py verify --artifact <that file>`.
A pass shows these behaviors for the upload and download paths the adapter exercises, locally. It does not prove
malware scanning, image re-encoding, storage bucket permissions or CDN caching; test those separately.
"""
import re
import unittest

from adapter import UploadAdapter

DENIED = {401, 403, 404}
REJECTED = {400, 403, 413, 415, 422}
HOSTILE = b'<html><script>alert(document.cookie)</script></html>'
TRAVERSAL_NAMES = ('../../escape.png', '..\\..\\escape.png', '/tmp/escape.png', 'C:\\escape.png')


def contained(path):
    p = str(path).replace('\\', '/')
    return not p.startswith('/') and not re.match(r'^[A-Za-z]:', p) and '..' not in p.split('/')


class UploadSafety(unittest.TestCase):
    def setUp(self):
        self.app = UploadAdapter()
        self.app.start()
        self.addCleanup(self.app.stop)
        self.alice = self.app.create_user('alice')
        self.bob = self.app.create_user('bob')
        self.name, self.ctype, self.data = self.app.allowed_file()
        _, self.alice_file = self.app.upload(self.alice, self.name, self.ctype, self.data)

    def assertRefusedAndNotStored(self, status, action, before):
        self.assertIn(status, REJECTED | DENIED, f'{action}: expected a refusal, got {status}')
        self.assertEqual(sorted(self.app.stored_paths()), before, f'{action}: a refused upload must not be stored')

    def test_owner_can_upload_and_download(self):
        self.assertIsNotNone(self.alice_file, 'sanity check: the allowed sample must upload, or the refusals below prove nothing')
        self.assertTrue(self.app.stored_paths(), 'sanity check: stored_paths() must list the accepted upload, '
                        'or every "not stored" assertion below passes vacuously')
        status, body = self.app.download(self.alice, self.alice_file)
        self.assertEqual((status, body), (200, self.data))

    def test_anonymous_upload_is_refused(self):
        before = sorted(self.app.stored_paths())
        status, _ = self.app.upload(None, self.name, self.ctype, self.data)
        self.assertIn(status, DENIED, f'anonymous upload: expected a denial, got {status}')
        self.assertEqual(sorted(self.app.stored_paths()), before, 'a denied anonymous upload must not be stored')

    def test_anonymous_download_is_refused(self):
        status, body = self.app.download(None, self.alice_file)
        self.assertIn(status, DENIED, f'anonymous download: expected a denial, got {status}')
        self.assertNotEqual(body, self.data, 'denied response must not contain the file')

    def test_other_user_cannot_download(self):
        status, body = self.app.download(self.bob, self.alice_file)
        self.assertIn(status, DENIED, f'bob downloads alice file: expected a denial, got {status}')
        self.assertNotEqual(body, self.data, 'denied response must not contain the file')

    def test_oversize_file_is_refused(self):
        before = sorted(self.app.stored_paths())
        big = self.data + b'\0' * (self.app.max_upload_bytes() - len(self.data) + 1)
        status, _ = self.app.upload(self.alice, self.name, self.ctype, big)
        self.assertRefusedAndNotStored(status, 'upload one byte over the limit', before)

    def test_disallowed_content_type_is_refused(self):
        before = sorted(self.app.stored_paths())
        for name, ctype, data in (('page.html', 'text/html', HOSTILE),
                                  ('run.exe', 'application/x-msdownload', b'MZ\x90\x00' + b'\0' * 60)):
            status, _ = self.app.upload(self.alice, name, ctype, data)
            self.assertRefusedAndNotStored(status, f'upload {name} as {ctype}', before)

    def test_mismatched_magic_bytes_are_refused(self):
        before = sorted(self.app.stored_paths())
        status, _ = self.app.upload(self.alice, self.name, self.ctype, HOSTILE)
        self.assertRefusedAndNotStored(status, f'HTML bytes declared as {self.ctype}', before)

    def test_stored_name_cannot_traverse_paths(self):
        for name in TRAVERSAL_NAMES:
            before = len(self.app.stored_paths())
            status, _ = self.app.upload(self.alice, name, self.ctype, self.data)  # refusing or renaming are both fine
            if status not in REJECTED | DENIED:
                self.assertGreater(len(self.app.stored_paths()), before,
                                   f'{name!r} was accepted but stored_paths() shows nothing new, so containment cannot be checked')
        escaped = [p for p in self.app.stored_paths() if not contained(p)]
        self.assertEqual(escaped, [], 'a client-supplied file name must never choose the storage path')

    def requireLinks(self):
        if not self.app.supports_links():
            self.skipTest('download links not supported by this adapter: expiry and scope were NOT tested')

    def test_download_link_expires(self):
        self.requireLinks()
        status, link = self.app.create_link(self.alice, self.alice_file)
        self.assertEqual(self.app.fetch_link(link), (200, self.data), f'sanity check: a fresh link must work (create: {status})')
        self.app.advance_past_link_expiry()
        status, body = self.app.fetch_link(link)
        self.assertIn(status, DENIED, f'expired link: expected a denial, got {status}')
        self.assertNotEqual(body, self.data)

    def test_other_user_cannot_create_a_link_to_my_file(self):
        self.requireLinks()
        status, link = self.app.create_link(self.bob, self.alice_file)
        self.assertIn(status, DENIED, f'bob creates a link to alice file: expected a denial, got {status}')
        if link is not None:
            self.assertNotEqual(self.app.fetch_link(link)[1], self.data, 'a denied link must not work anyway')


if __name__ == '__main__':
    unittest.main(verbosity=2)

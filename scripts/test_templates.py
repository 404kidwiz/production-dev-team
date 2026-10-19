"""The shipped test templates must fail on an insecure app, pass on a secure one, and error when unconfigured."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

TEMPLATES = Path(__file__).resolve().parents[1] / 'templates'

FAKE_ADAPTER = textwrap.dedent('''
    import hashlib, hmac, itertools, threading
    SECURE = {secure}
    ANON_WRITES = False
    RACE_RAISES = False
    LEAK_BODY = False
    DENIED_BUT_WRITES = False
    ALWAYS_DENY = False
    PAY_DENY_BUT_APPLY = False
    DEDUPE_BY_PAYABLE = False
    LEAK_PROBE = False
    SECRET = b"test-secret"

    class _Base:
        def start(self):
            self.ids, self.objects, self.applied, self.seen = itertools.count(1), {{}}, {{}}, set()
            self.lock = threading.Lock()
        def stop(self): pass
        def create_user(self, name): return name

    class IsolationAdapter(_Base):
        def create_object(self, session, label):
            i = next(self.ids); self.objects[i] = [session, label]; self.last = i; return i
        def _visible(self, session, i):
            o = self.objects.get(i)
            if ALWAYS_DENY: return None, 404
            if session is None: return None, 401
            if o is None: return None, 404
            if SECURE and o[0] != session: return None, 404
            return o, 200
        def read(self, session, i):
            if LEAK_PROBE and session is not None and i == self.last + 4: return 200, {{}}
            o, status = self._visible(session, i)
            body = {{"label": o[1]}} if o else {{}}
            if LEAK_BODY and status == 404 and i in self.objects: body = {{"label": self.objects[i][1]}}
            return status, body
        def update(self, session, i, label):
            o, status = self._visible(session, i)
            if ANON_WRITES and session is None: o = self.objects.get(i)
            if DENIED_BUT_WRITES and status == 404 and i in self.objects: o = self.objects[i]
            if o: o[1] = label
            return status
        def delete(self, session, i):
            o, status = self._visible(session, i)
            if ANON_WRITES and session is None: o = self.objects.get(i)
            if DENIED_BUT_WRITES and status == 404 and i in self.objects: o = self.objects[i]
            if o: del self.objects[i]
            return status
        def list_ids(self, session):
            return [i for i, o in self.objects.items() if (not SECURE) or o[0] == session]
        def label_of(self, session, i):
            o = self.objects.get(i); return o[1] if o else None

    class PaymentAdapter(_Base):
        def create_payable(self, session, cents):
            i = next(self.ids); self.applied[i] = 0; return i
        def make_event(self, event_id, payable_id, cents):
            return ("%s|%s|%s" % (event_id, payable_id, cents)).encode()
        def sign(self, body): return hmac.new(SECRET, body, hashlib.sha256).hexdigest()
        def post_webhook(self, body, signature):
            if ALWAYS_DENY: return 401
            bad = SECURE and (not signature or not hmac.compare_digest(signature, self.sign(body)))
            if bad and not PAY_DENY_BUT_APPLY: return 401
            event_id, payable, cents = body.decode().split("|")
            key = payable if DEDUPE_BY_PAYABLE else event_id
            with self.lock:
                if SECURE and key in self.seen: return 200
                self.seen.add(key)
                self.applied[int(payable)] += int(cents)
            if bad: return 401
            if RACE_RAISES and threading.current_thread() is not threading.main_thread():
                raise RuntimeError("delivery crashed after applying")
            return 200
        def applied_cents(self, session, payable_id): return self.applied[payable_id]
''')

UPLOAD_FAKE = textwrap.dedent('''
    import itertools
    ANON_UPLOAD = False
    NO_OWNER_CHECK = False
    NO_SIZE_LIMIT = False
    TRUST_DECLARED_TYPE = False
    ALLOW_ANY_TYPE = False
    RAW_NAME = False
    LINKS = True
    LINKS_NEVER_EXPIRE = False
    ANYONE_CAN_LINK = False
    REJECT_ALL = False
    HIDE_PATHS = False
    ANON_DOWNLOAD = False
    LIMIT = 1024
    PNG = b"\\x89PNG\\r\\n\\x1a\\n"

    class UploadAdapter:
        def start(self): self.ids, self.files, self.paths, self.links, self.now = itertools.count(1), {}, [], {}, 0
        def stop(self): pass
        def create_user(self, name): return name
        def allowed_file(self): return "photo.png", "image/png", PNG + b"pixels" * 4
        def max_upload_bytes(self): return LIMIT
        def upload(self, session, filename, content_type, data):
            if REJECT_ALL: return 400, None
            if session is None and not ANON_UPLOAD: return 401, None
            if len(data) > LIMIT and not NO_SIZE_LIMIT: return 413, None
            if not ALLOW_ANY_TYPE and content_type != "image/png": return 415, None
            if not ALLOW_ANY_TYPE and not TRUST_DECLARED_TYPE and not data.startswith(PNG): return 415, None
            i = next(self.ids)
            self.files[i] = (session, data)
            self.paths.append(filename if RAW_NAME else "u/%d" % i)
            return 201, i
        def download(self, session, i):
            f = self.files.get(i)
            if session is None and not ANON_DOWNLOAD: return 401, b""
            if f is None or (f[0] != session and not NO_OWNER_CHECK and not (session is None and ANON_DOWNLOAD)): return 404, b""
            return 200, f[1]
        def stored_paths(self): return [] if HIDE_PATHS else list(self.paths)
        def supports_links(self): return LINKS
        def create_link(self, session, i):
            f = self.files.get(i)
            if f is None or (f[0] != session and not ANYONE_CAN_LINK): return 404, None
            token = "link-%d" % len(self.links)
            self.links[token] = (i, self.now + 300)
            return 201, token
        def fetch_link(self, link):
            entry = self.links.get(link)
            if entry is None or (self.now > entry[1] and not LINKS_NEVER_EXPIRE): return 403, b""
            return 200, self.files[entry[0]][1]
        def advance_past_link_expiry(self): self.now += 301
''')

AI_FAKE = textwrap.dedent('''
    import itertools
    NO_OWNER_CHECK = False
    NO_APPROVAL_GATE = False
    APPROVAL_ANY_ARGS = False
    APPROVAL_FROM_TEXT = False
    INJECTION_WIDENS = False
    NO_BUDGET = False
    SHARED_BUDGET = False
    ALWAYS_DENY = False

    class AIToolAdapter:
        def start(self):
            self.ids, self.records, self.approvals, self.budgets, self.used = itertools.count(1), {}, set(), {}, {}
        def stop(self): pass
        def create_user(self, name): return name
        def create_record(self, session, label):
            i = next(self.ids); self.records[i] = (session, label); return i
        def record_exists(self, session, i): return i in self.records
        def offered_tools(self, session, user_content):
            tools = {"read_record", "delete_record"}
            if INJECTION_WIDENS and "administrator" in user_content: tools.add("export_all_records")
            return tools
        def _approval(self, session, tool, args):
            return (session, tool) if APPROVAL_ANY_ARGS else (session, tool, args.get("id"))
        def record_human_approval(self, session, tool, args): self.approvals.add(self._approval(session, tool, args))
        def set_budget(self, session, calls): self.budgets["all" if SHARED_BUDGET else session] = calls
        def propose_tool_call(self, session, tool, args, user_content=""):
            if ALWAYS_DENY or session is None: return 403, None
            if tool not in self.offered_tools(session, user_content): return 403, None
            if tool == "export_all_records": return 200, [r[1] for r in self.records.values()]
            record = self.records.get(args.get("id"))
            if record is None or (record[0] != session and not NO_OWNER_CHECK): return 404, None
            if tool == "delete_record" and not NO_APPROVAL_GATE:
                approved = self._approval(session, tool, args) in self.approvals
                if not approved and not (APPROVAL_FROM_TEXT and "approved" in user_content): return 202, "pending approval"
            key = "all" if SHARED_BUDGET else session
            if not NO_BUDGET and key in self.budgets and self.used.get(key, 0) >= self.budgets[key]: return 429, None
            self.used[key] = self.used.get(key, 0) + 1
            if tool == "read_record": return 200, {"label": record[1]}
            del self.records[args["id"]]
            self.approvals.discard(self._approval(session, tool, args))
            return 200, {"deleted": args["id"]}
''')

JOBS_FAKE = textwrap.dedent('''
    import threading, time
    NO_DEDUPE = False
    NO_RETRY = False
    UNBOUNDED_RETRIES = False
    NO_DEAD_LETTER = False
    RACY_CLAIM = False
    DOUBLE_SCHEDULE = False
    NEVER_RUN = False
    MAX_ATTEMPTS = 3

    class JobAdapter:
        def start(self):
            self.queue, self.keys, self.applied, self.tries, self.dead = [], set(), {}, {}, []
            self.failures, self.schedules, self.now, self.lock = {}, {}, 0, threading.Lock()
        def stop(self): pass
        def enqueue(self, key):
            with self.lock:
                if key in self.keys and not NO_DEDUPE: return
                self.keys.add(key); self.queue.append(key)
        def enqueue_failing(self, key, failures): self.failures[key] = failures; self.enqueue(key)
        def run_worker_until_idle(self):
            while not NEVER_RUN:
                if RACY_CLAIM:
                    if not self.queue: return
                    key = self.queue[0]; time.sleep(0.01); self._run(key)
                    try: self.queue.remove(key)
                    except ValueError: pass
                    continue
                with self.lock:
                    if not self.queue: return
                    key = self.queue.pop(0)
                self._run(key)
        def _run(self, key):
            n = self.tries[key] = self.tries.get(key, 0) + 1
            if n <= self.failures.get(key, 0):
                limit = 1 if NO_RETRY else 50 if UNBOUNDED_RETRIES else MAX_ATTEMPTS
                if n < limit: self.queue.append(key)
                elif not NO_DEAD_LETTER: self.dead.append(key)
                return
            self.applied[key] = self.applied.get(key, 0) + 1
        def applied_count(self, key): return self.applied.get(key, 0)
        def attempts(self, key): return self.tries.get(key, 0)
        def max_attempts(self): return MAX_ATTEMPTS
        def dead_letters(self): return list(self.dead)
        def schedule(self, name, every_seconds):
            with self.lock:
                if name in self.schedules and not DOUBLE_SCHEDULE: return
                self.schedules.setdefault(name, []).append([every_seconds, self.now + every_seconds])
        def advance_clock(self, seconds): self.now += seconds
        def run_scheduler(self):
            with self.lock:
                for name, entries in self.schedules.items():
                    for entry in entries:
                        while entry[1] <= self.now:
                            entry[1] += entry[0]
                            self.applied[name] = self.applied.get(name, 0) + 1
''')


def flip(source, flag):
    assert f'{flag} = False' in source or f'{flag} = True' in source, flag
    return source.replace(f'{flag} = False', f'{flag} = True') if f'{flag} = False' in source \
        else source.replace(f'{flag} = True', f'{flag} = False')


def run_template(module, adapter_source):
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / 'adapter.py').write_text(adapter_source)
        proc = subprocess.run([sys.executable, '-m', 'unittest', '-v', module], cwd=str(TEMPLATES),
                              env={'PYTHONPATH': tmp, 'PYTHONDONTWRITEBYTECODE': '1', 'PATH': '/usr/bin:/bin'},
                              capture_output=True, text=True)
    failed = set(re.findall(r'^(?:FAIL|ERROR): (\w+)', proc.stderr, re.M))
    return proc.returncode, failed, proc.stderr


class TemplateTests(unittest.TestCase):
    def test_isolation_template_passes_secure_and_catches_each_defect(self):
        code, failed, err = run_template('test_two_user_isolation', FAKE_ADAPTER.format(secure=True))
        self.assertEqual((code, failed), (0, set()), err)
        code, failed, _ = run_template('test_two_user_isolation', FAKE_ADAPTER.format(secure=False))
        self.assertNotEqual(code, 0)
        self.assertEqual(failed, {'test_other_user_cannot_read', 'test_other_user_cannot_update', 'test_other_user_cannot_delete',
                                  'test_listing_excludes_other_users_objects', 'test_guessing_neighbouring_ids_never_succeeds'})

    def test_payment_template_passes_secure_and_catches_each_defect(self):
        code, failed, err = run_template('test_payment_idempotency', FAKE_ADAPTER.format(secure=True))
        self.assertEqual((code, failed), (0, set()), err)
        code, failed, _ = run_template('test_payment_idempotency', FAKE_ADAPTER.format(secure=False))
        self.assertNotEqual(code, 0)
        self.assertEqual(failed, {'test_missing_signature_is_rejected_and_has_no_effect',
                                  'test_wrong_signature_is_rejected_and_has_no_effect',
                                  'test_tampered_body_with_a_valid_old_signature_is_rejected',
                                  'test_replayed_event_is_applied_only_once',
                                  'test_concurrent_duplicates_are_applied_only_once'})

    def test_denied_anonymous_writes_that_still_happen_are_caught(self):
        source = FAKE_ADAPTER.format(secure=True).replace('ANON_WRITES = False', 'ANON_WRITES = True')
        code, failed, _ = run_template('test_two_user_isolation', source)
        self.assertNotEqual(code, 0)
        self.assertEqual(failed, {'test_unauthenticated_requests_are_denied'})

    def test_crashing_concurrent_deliveries_fail_the_test(self):
        source = FAKE_ADAPTER.format(secure=True).replace('RACE_RAISES = False', 'RACE_RAISES = True')
        code, failed, _ = run_template('test_payment_idempotency', source)
        self.assertNotEqual(code, 0)
        self.assertEqual(failed, {'test_concurrent_duplicates_are_applied_only_once'})

    def variant(self, flag):
        return FAKE_ADAPTER.format(secure=True).replace(f'{flag} = False', f'{flag} = True')

    def test_denied_but_still_applied_and_leaky_adapters_are_caught(self):
        cases = [
            ('test_two_user_isolation', 'LEAK_BODY', {'test_other_user_cannot_read'}),
            ('test_two_user_isolation', 'DENIED_BUT_WRITES', {'test_other_user_cannot_update', 'test_other_user_cannot_delete'}),
            ('test_two_user_isolation', 'LEAK_PROBE', {'test_guessing_neighbouring_ids_never_succeeds'}),
            ('test_payment_idempotency', 'PAY_DENY_BUT_APPLY', {'test_missing_signature_is_rejected_and_has_no_effect',
                                                                'test_wrong_signature_is_rejected_and_has_no_effect',
                                                                'test_tampered_body_with_a_valid_old_signature_is_rejected'}),
            ('test_payment_idempotency', 'DEDUPE_BY_PAYABLE', {'test_distinct_events_both_apply'}),
        ]
        for module, flag, expected in cases:
            code, failed, err = run_template(module, self.variant(flag))
            self.assertNotEqual(code, 0, flag)
            self.assertEqual(failed, expected, f'{flag}: {err[-600:]}')

    def test_an_adapter_that_denies_everything_fails_the_sanity_checks(self):
        code, failed, _ = run_template('test_two_user_isolation', self.variant('ALWAYS_DENY'))
        self.assertIn('test_owner_can_use_their_own_object', failed)
        code, failed, _ = run_template('test_payment_idempotency', self.variant('ALWAYS_DENY'))
        self.assertIn('test_signed_event_is_applied_once', failed)

    def assertCatches(self, module, source, cases, sanity):
        code, failed, err = run_template(module, source)
        self.assertEqual((code, failed), (0, set()), err)
        for flag, expected in cases:
            code, failed, err = run_template(module, flip(source, flag))
            self.assertNotEqual(code, 0, flag)
            self.assertEqual(failed, expected, f'{flag}: {err[-600:]}')
        code, failed, _ = run_template(module, flip(source, sanity[0]))
        self.assertIn(sanity[1], failed, f'{sanity[0]} must fail the sanity check')

    def test_upload_template_passes_secure_and_catches_each_defect(self):
        self.assertCatches('test_upload_safety', UPLOAD_FAKE, [
            ('ANON_UPLOAD', {'test_anonymous_upload_is_refused'}),
            ('NO_OWNER_CHECK', {'test_other_user_cannot_download'}),
            ('NO_SIZE_LIMIT', {'test_oversize_file_is_refused'}),
            ('TRUST_DECLARED_TYPE', {'test_mismatched_magic_bytes_are_refused'}),
            ('ALLOW_ANY_TYPE', {'test_disallowed_content_type_is_refused', 'test_mismatched_magic_bytes_are_refused'}),
            ('RAW_NAME', {'test_stored_name_cannot_traverse_paths'}),
            ('LINKS_NEVER_EXPIRE', {'test_download_link_expires'}),
            ('ANYONE_CAN_LINK', {'test_other_user_cannot_create_a_link_to_my_file'}),
            ('ANON_DOWNLOAD', {'test_anonymous_download_is_refused'}),
            ('HIDE_PATHS', {'test_owner_can_upload_and_download', 'test_stored_name_cannot_traverse_paths'}),
        ], ('REJECT_ALL', 'test_owner_can_upload_and_download'))

    def test_unsupported_links_skip_loudly_instead_of_passing(self):
        code, failed, err = run_template('test_upload_safety', flip(UPLOAD_FAKE, 'LINKS'))
        self.assertEqual((code, failed), (0, set()), err)
        self.assertEqual(err.count("skipped 'download links not supported"), 2, err)
        self.assertIn('skipped=2', err.splitlines()[-1])

    def test_ai_template_passes_secure_and_catches_each_defect(self):
        self.assertCatches('test_ai_tool_authorization', AI_FAKE, [
            ('NO_OWNER_CHECK', {'test_tool_call_naming_another_users_record_is_refused'}),
            ('NO_APPROVAL_GATE', {'test_irreversible_tool_needs_recorded_human_approval',
                                  'test_injected_text_cannot_widen_the_tool_set'}),
            ('APPROVAL_ANY_ARGS', {'test_irreversible_tool_needs_recorded_human_approval'}),
            ('APPROVAL_FROM_TEXT', {'test_injected_text_cannot_widen_the_tool_set'}),
            ('INJECTION_WIDENS', {'test_injected_text_cannot_widen_the_tool_set'}),
            ('NO_BUDGET', {'test_exhausted_budget_refuses_further_calls'}),
            ('SHARED_BUDGET', {'test_exhausted_budget_refuses_further_calls'}),
        ], ('ALWAYS_DENY', 'test_owner_tool_call_runs'))

    def test_jobs_template_passes_secure_and_catches_each_defect(self):
        self.assertCatches('test_job_idempotency', JOBS_FAKE, [
            ('NO_DEDUPE', {'test_same_job_enqueued_twice_applies_once'}),
            ('NO_RETRY', {'test_transient_failure_is_retried_then_applies_once',
                          'test_permanent_failure_is_bounded_then_dead_lettered'}),
            ('UNBOUNDED_RETRIES', {'test_permanent_failure_is_bounded_then_dead_lettered'}),
            ('NO_DEAD_LETTER', {'test_permanent_failure_is_bounded_then_dead_lettered'}),
            ('RACY_CLAIM', {'test_concurrent_workers_do_not_double_apply'}),
            ('DOUBLE_SCHEDULE', {'test_scheduled_job_is_not_double_scheduled'}),
        ], ('NEVER_RUN', 'test_a_job_runs_once'))

    def test_unconfigured_stub_errors_instead_of_passing(self):
        stub = (TEMPLATES / 'adapter_stub.py').read_text()
        for module in ('test_two_user_isolation', 'test_payment_idempotency', 'test_upload_safety',
                       'test_ai_tool_authorization', 'test_job_idempotency'):
            code, failed, err = run_template(module, stub)
            self.assertNotEqual(code, 0, module)
            self.assertIn('NotImplementedError', err)
            self.assertNotIn('skipped', err.splitlines()[-1].lower())

    def test_missing_adapter_is_an_import_error(self):
        proc = subprocess.run([sys.executable, '-m', 'unittest', 'test_two_user_isolation'], cwd=str(TEMPLATES),
                              env={'PYTHONDONTWRITEBYTECODE': '1', 'PATH': '/usr/bin:/bin'}, capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('adapter', proc.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)

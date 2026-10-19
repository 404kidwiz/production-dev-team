# Test templates

Runnable stdlib `unittest` templates for the failures that cost non-engineers the most: one user reading another's data, a payment event applied twice, an unsafe upload, a model tool call nobody authorized, and a background job applied twice or lost. They live in `templates/`. Use them for `multitenant`, `accounts`, `payments`, `uploads`, `ai` and `jobs` controls instead of writing the first version of these tests from scratch.

| File | Covers | Typical controls |
|---|---|---|
| `templates/test_two_user_isolation.py` | Owner works; a second real user cannot read, update, delete or list the first user's object; anonymous is denied; neighbouring ids are denied | `POST-010` and the isolation family |
| `templates/test_payment_idempotency.py` | Signed event applies once; missing, wrong or tampered signature is rejected with no effect; replay and 8 concurrent duplicates apply once; distinct events both apply | the payments family |
| `templates/test_upload_safety.py` | Owner uploads and downloads; anonymous upload, a second user's download, an oversize file, a disallowed type and mismatched magic bytes are refused and nothing is stored; a client file name such as `../../x` never picks the storage path; download links expire and only the owner can create one | `POST-042` and the uploads family |
| `templates/test_ai_tool_authorization.py` | The app, not the model, authorizes tool calls: a call naming another user's record is refused (even with that user's approval forged by the caller); an irreversible tool waits for a recorded human approval of that exact call; prompt-injected text neither widens the offered tool set nor counts as approval; an exhausted per-user budget refuses further calls | `POST-114`, `POST-116`, `POST-121` |
| `templates/test_job_idempotency.py` | A job enqueued twice applies once; a transient failure is retried; a permanent failure stops at the configured bound and lands in the dead-letter place; 8 parallel workers apply each job once; a schedule registered twice fires once per period | `POST-070`, `POST-071`, `POST-072` |
| `templates/adapter_stub.py` | The interface you implement (one class per template; implement only the ones you use) | n/a |

## Use

1. Copy the templates you need and `adapter_stub.py` into one test directory of the project. Rename the stub to `adapter.py` in that same directory and implement every method for this app. Run the tests from that directory (`cd tests && python3 -m unittest -v test_two_user_isolation`) so `from adapter import ...` resolves. Keep the adapter thin: translate calls, add no logic.
   Not an HTTP app? The adapter is just Python. Call your functions directly (see the worked example below).
2. The adapter must start a disposable instance: temp database, fake provider keys, localhost, a known TEST webhook secret. Never aim these at production, shared staging or a live payment account.
3. Run `python3 -m unittest -v test_two_user_isolation test_payment_idempotency` (plus `test_upload_safety`, `test_ai_tool_authorization`, `test_job_idempotency` if you copied them) and save the output to a file.
4. Open the file, confirm every test ran and passed (a skip is not a pass), then record it with `ledger.py verify --artifact <file> --watch <auth, webhook, upload, tool-dispatch or worker source files>`.

## Worked adapter for a plain-Python app (no HTTP)

```python
# adapter.py (next to the tests)
from myapp import create_app          # returns an app object with a fresh in-memory database

class IsolationAdapter:
    def start(self):                  self.app = create_app(db=":memory:")
    def stop(self):                   self.app.close()
    def create_user(self, name):      return self.app.users.create(name).session
    def create_object(self, session, label):
        return self.app.invoices.create(session, label=label).id
    def read(self, session, object_id):
        result = self.app.invoices.get(session, object_id)      # returns (status, body)
        return result.status, result.body
    def update(self, session, object_id, label):  return self.app.invoices.update(session, object_id, label=label).status
    def delete(self, session, object_id):         return self.app.invoices.delete(session, object_id).status
    def list_ids(self, session):      return [i.id for i in self.app.invoices.list(session)]
    def label_of(self, session, object_id):
        found = self.app.invoices.get(session, object_id)
        return found.body["label"] if found.status == 200 else None
```

Use real sessions (the objects your auth layer issues), never a mock that skips authorization. `session=None` must reach the same code path an anonymous caller would.

## Adding what your feature needs (for example a CSV export)

The templates cover single-object access. A bulk or export path is a separate route to the same data and needs its own test. Add an adapter method and a test in the same style:

```python
# adapter.py
def export_csv(self, session):
    """Return the exported text for everything this session may export."""
    return self.app.invoices.export_csv(session)

# test_export_isolation.py
def test_export_contains_only_my_rows(self):
    mine = self.app.export_csv(self.alice)
    self.assertIn("alice-private", mine)
    self.assertNotIn("bob-private", mine)

def test_anonymous_export_is_denied(self):
    with self.assertRaises(PermissionError):      # or assert the status your app returns
        self.app.export_csv(None)
```

For "same tenant, different user" rules (members who may not see each other's rows), create two users in one tenant in `setUp` and repeat the denial tests between them; the stub's `create_user` may need a `tenant` argument.

## Uploads, AI tools and jobs

Same rules as above: disposable instance, thin adapter, real sessions. Each adapter class in `templates/adapter_stub.py` documents every method; these are the parts people get wrong.

**`test_upload_safety.py`** proves, for the upload and download paths the adapter calls: anonymous upload is refused; another user cannot download; a file one byte over the limit, an HTML or EXE upload, and HTML bytes declared as your allowed type are all refused **and leave storage unchanged**; no stored path escapes the upload root whatever file name the client sends; a download link stops working after its lifetime and another user cannot mint one for your file. It does not prove malware scanning, image re-encoding, bucket permissions, CDN caching, or that the app never serves an upload inline from your own domain.
- `allowed_file` returns a file your app really accepts (a real 1x1 PNG, a tiny PDF). The oversize case is that file padded with zero bytes; if your validator rejects padded files for being malformed, the oversize test can pass for the wrong reason, so make sure your validator really reached the size check: the test accepts any refusal status (400, 403, 413, 415 or 422) and does not print which one, so confirm the 413 path once by hand or in your own test.
- `max_upload_bytes` must read the app's configured limit, not restate a number.
- `stored_paths` must list what is actually on disk or in the bucket, relative to the upload root (`os.path.relpath` of the resolved path for disk), not what the database thinks was stored. Otherwise a traversal write is invisible.
- No download links? Return False from `supports_links`. The two link tests then **skip** with the reason "download links not supported by this adapter: expiry and scope were NOT tested". Report that as not tested, never as passed.
- `advance_past_link_expiry` moves the clock the signer reads (inject a clock, or sign with a short TTL and sleep). It must not revoke links, or the expiry test proves nothing.

**`test_ai_tool_authorization.py`** plays the model. It never calls a model: it hands the app a proposed tool call, the way a malicious or confused model could, and checks the app's own gate. It proves that gate refuses cross-user records, holds irreversible calls until a recorded human approval of that exact call, ignores injected instructions and approval claims in user content, and enforces a per-user budget. It does not prove the model resists injection, answer quality, what private data reaches the prompt, or provider-outage behavior (`POST-117`, `POST-118`, `POST-120` need their own checks).
- `propose_tool_call` must enter the same function that receives tool calls from the provider SDK response, so authorization, approval and budget checks run. Calling the tool implementation directly makes every refusal test meaningless.
- The test uses generic names `read_record` (safe) and `delete_record` (irreversible: map it to your delete, send, refund or pay tool). `export_all_records` is a made-up tool the model was never offered; pass unknown names through unchanged so the app has to refuse them.
- `record_human_approval` goes through the real approval endpoint or UI action, scoped to the one call. `set_budget` converts "N more calls" into your app's unit (tokens, cents).
- A pending-approval answer such as 202 is fine for an unapproved irreversible call; the test only requires that the call did not run.

**`test_job_idempotency.py`** proves, for the worker and scheduler the adapter runs: duplicate enqueues apply once, one failure is retried, permanent failure stops at `max_attempts` and is visible in `dead_letters`, 8 threads running `run_worker_until_idle` at once apply each job once, and a schedule registered by two servers fires once per period even when two scheduler passes run together. It does not prove the production broker's delivery guarantees, alerting on dead letters, or behavior across real separate machines and clocks. Known limit: an adapter that serializes workers behind its own lock passes the concurrency test while the real claim is still racy, so keep the adapter a thin pass-through to the real worker code.
- `run_worker_until_idle` processes everything due, advancing a fake clock through retry backoff, and returns. If retries never end the test fails after 30 seconds instead of hanging.
- `applied_count` counts the real side effect (rows, emails, charges), not handler starts; `attempts` counts handler starts. `enqueue_failing` makes your real handler fail, for example by patching the downstream client it calls.
- Each thread calling `run_worker_until_idle` or `run_scheduler` must act like a separate worker or server (its own connection or session), or the concurrency tests can pass on a lock that production does not have.

## What a result means

- A template with an unimplemented adapter method **errors**; it never skips or passes. If you see `NotImplementedError`, the template is not configured and proves nothing.
- Each denial test has a sanity test (the owner can use the object; a correctly signed event is accepted) so that "denied" cannot pass just because the whole endpoint is broken.
- A pass proves those behaviors for the object type and operations the adapter exercises, locally. It does not prove other endpoints, other object types, the provider's real signing scheme, refunds, reconciliation or out-of-order delivery. Extend the adapter and tests for what your app and provider can actually produce, and state the scope in the ledger `check`.
- The integer-id probe in the isolation template is skipped for non-integer ids, and the upload link tests skip when the adapter declares no links. A skip is not a pass: add a probe for your id scheme or say it was not tested.

## Evidence that the templates work

The bundle test `scripts/test_templates.py` runs every template against tiny in-memory fakes. Each secure fake passes every test. Each deliberately broken variant (no owner check, no size limit, trusting the declared content type, raw file names, links that never expire, no approval gate, approval read from user text, injection that adds a tool, shared budgets, no idempotency key, unbounded retries, no dead letter, racy job claims, double scheduling and more) must fail exactly the tests named in that file and no others. A fake that refuses everything fails the sanity test, a fake without links skips the two link tests with the stated reason, and the unconfigured stub errors for every template.

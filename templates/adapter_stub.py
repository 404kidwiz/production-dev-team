"""Adapter stub. Copy to `adapter.py` next to the test templates and fill in the methods.

Rules for the adapter:
- It must start a DISPOSABLE instance of the app (temp database, fake provider keys, localhost).
  Never point these tests at production, staging with real data, or a live payment account.
- Each method is a thin translation to your app's real interface (HTTP, test client, function call).
  Keep logic out of the adapter: a clever adapter can hide the bug the test is looking for.
- Until every method is implemented the tests ERROR on purpose. An unconfigured template must never
  look like a pass.
"""


def todo(name):
    raise NotImplementedError(f'adapter.{name} is not implemented: fill it in before running the template')


class IsolationAdapter:
    """Used by test_two_user_isolation.py."""

    def start(self):
        """Boot a fresh disposable app instance."""
        todo('IsolationAdapter.start')

    def stop(self):
        todo('IsolationAdapter.stop')

    def create_user(self, name):
        """Create a real user and return an opaque session (token, cookie jar, client...)."""
        todo('IsolationAdapter.create_user')

    def create_object(self, session, label):
        """Create one owned object (invoice, document, project...). Return its id."""
        todo('IsolationAdapter.create_object')

    def read(self, session, object_id):
        """Return (http_status_or_equivalent, body). `session=None` means unauthenticated."""
        todo('IsolationAdapter.read')

    def update(self, session, object_id, label):
        """Return a status. Must attempt a real write."""
        todo('IsolationAdapter.update')

    def delete(self, session, object_id):
        """Return a status. Must attempt a real delete."""
        todo('IsolationAdapter.delete')

    def list_ids(self, session):
        """Return the ids this session can list."""
        todo('IsolationAdapter.list_ids')

    def label_of(self, session, object_id):
        """As the OWNER, return the object's current label, or None if it no longer exists."""
        todo('IsolationAdapter.label_of')


class PaymentAdapter:
    """Used by test_payment_idempotency.py. The disposable instance must use a known TEST webhook secret."""

    def start(self):
        todo('PaymentAdapter.start')

    def stop(self):
        todo('PaymentAdapter.stop')

    def create_user(self, name):
        todo('PaymentAdapter.create_user')

    def create_payable(self, session, cents):
        """Create something that can be paid (an invoice, order...). Return its id."""
        todo('PaymentAdapter.create_payable')

    def make_event(self, event_id, payable_id, cents):
        """Return the exact raw request body (bytes) the payment provider would send."""
        todo('PaymentAdapter.make_event')

    def sign(self, body):
        """Return the valid signature header value for `body` using the test secret."""
        todo('PaymentAdapter.sign')

    def post_webhook(self, body, signature):
        """POST `body` with `signature` (None = header omitted). Return the status."""
        todo('PaymentAdapter.post_webhook')

    def applied_cents(self, session, payable_id):
        """As the owner, return the total cents recorded as paid, as an integer."""
        todo('PaymentAdapter.applied_cents')


class UploadAdapter:
    """Used by test_upload_safety.py. Storage must be a disposable temp directory or bucket."""

    def start(self):
        todo('UploadAdapter.start')

    def stop(self):
        todo('UploadAdapter.stop')

    def create_user(self, name):
        """Create a real user and return an opaque session."""
        todo('UploadAdapter.create_user')

    def allowed_file(self):
        """Return (filename, content_type, data) for a small file the app accepts, for example a real 1x1 PNG."""
        todo('UploadAdapter.allowed_file')

    def max_upload_bytes(self):
        """Return the app's configured upload size limit in bytes (read it from the app's config, do not invent it)."""
        todo('UploadAdapter.max_upload_bytes')

    def upload(self, session, filename, content_type, data):
        """Upload through the real endpoint. Return (status, file_id or None). `session=None` means anonymous."""
        todo('UploadAdapter.upload')

    def download(self, session, file_id):
        """Return (status, body_bytes) from the real download path."""
        todo('UploadAdapter.download')

    def stored_paths(self):
        """Return every storage key or path the app has written, RELATIVE to its upload root.

        For a disk store, resolve symlinks and return os.path.relpath(real_path, root), so a file written outside
        the root shows up as '../...'. For an object store, return the object keys. Include every file, not only
        the ones the app's database knows about.
        """
        todo('UploadAdapter.stored_paths')

    def supports_links(self):
        """Return True if the app issues download links (signed URLs, share tokens). False skips the link tests."""
        todo('UploadAdapter.supports_links')

    def create_link(self, session, file_id):
        """Ask the app for a download link as `session`. Return (status, link or None)."""
        todo('UploadAdapter.create_link')

    def fetch_link(self, link):
        """Fetch `link` with no session, as anyone holding the link would. Return (status, body_bytes)."""
        todo('UploadAdapter.fetch_link')

    def advance_past_link_expiry(self):
        """Move the app's clock (or the signer's) past the link lifetime. Do not delete or revoke links here."""
        todo('UploadAdapter.advance_past_link_expiry')


class AIToolAdapter:
    """Used by test_ai_tool_authorization.py. The test plays the model: it chooses the tool call the model proposes.

    Route every proposed call through the SAME authorization layer that real model output goes through (the code
    that receives a tool call from the provider SDK). Never call the tool function directly; never call a model.
    Generic tool names used by the test: 'read_record' (safe), 'delete_record' (irreversible, needs approval).
    Map them to your app's names here; pass any other name through unchanged.
    """

    def start(self):
        todo('AIToolAdapter.start')

    def stop(self):
        todo('AIToolAdapter.stop')

    def create_user(self, name):
        """Create a real user and return an opaque session."""
        todo('AIToolAdapter.create_user')

    def create_record(self, session, label):
        """Create one record owned by `session` that the tools can act on. Return its id."""
        todo('AIToolAdapter.create_record')

    def record_exists(self, session, record_id):
        """As the OWNER, return True if the record still exists."""
        todo('AIToolAdapter.record_exists')

    def offered_tools(self, session, user_content):
        """Return the set of tool names the app would offer the model for a turn whose user content is `user_content`."""
        todo('AIToolAdapter.offered_tools')

    def propose_tool_call(self, session, tool, args, user_content=''):
        """Feed the app a model response proposing `tool(**args)` during a turn containing `user_content`.

        Return (status, result). 200 means the tool executed; a pending-approval answer (such as 202) or a refusal
        means it did not.
        """
        todo('AIToolAdapter.propose_tool_call')

    def record_human_approval(self, session, tool, args):
        """Record approval exactly as the human approval UI or endpoint does, for this one call."""
        todo('AIToolAdapter.record_human_approval')

    def set_budget(self, session, calls):
        """Set this user's model or tool budget so that exactly `calls` more successful calls fit."""
        todo('AIToolAdapter.set_budget')


class JobAdapter:
    """Used by test_job_idempotency.py. Use a disposable queue (temp database, in-memory broker) and a fake clock."""

    def start(self):
        todo('JobAdapter.start')

    def stop(self):
        todo('JobAdapter.stop')

    def enqueue(self, key):
        """Enqueue the app's real job with idempotency key `key` (an order id, an event id...)."""
        todo('JobAdapter.enqueue')

    def enqueue_failing(self, key, failures):
        """Enqueue a job whose handler raises on its first `failures` attempts, then succeeds."""
        todo('JobAdapter.enqueue_failing')

    def run_worker_until_idle(self):
        """Run one worker in the calling thread until nothing is runnable, advancing the fake clock through retry
        backoff. Several threads may call this at once; each must behave like a separate worker process."""
        todo('JobAdapter.run_worker_until_idle')

    def applied_count(self, key):
        """Return how many times the side effect of job or schedule `key` happened (rows written, emails sent...)."""
        todo('JobAdapter.applied_count')

    def attempts(self, key):
        """Return how many times the handler for `key` was started."""
        todo('JobAdapter.attempts')

    def max_attempts(self):
        """Return the app's configured total attempts per job, including the first."""
        todo('JobAdapter.max_attempts')

    def dead_letters(self):
        """Return the keys of jobs in the dead-letter queue or table, as an operator would see them."""
        todo('JobAdapter.dead_letters')

    def schedule(self, name, every_seconds):
        """Register the recurring job `name` the way each server does at boot."""
        todo('JobAdapter.schedule')

    def advance_clock(self, seconds):
        todo('JobAdapter.advance_clock')

    def run_scheduler(self):
        """Run one scheduler pass, as one server would. Several threads may call this at once (several servers)."""
        todo('JobAdapter.run_scheduler')

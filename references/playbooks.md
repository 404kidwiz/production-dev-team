# Playbooks

Load the section for the mode you chose. Moved out of `SKILL.md` to keep the always-loaded file short.

## Build path

Discover the problem, users, risky data and actions, budget, countries and release target; write a short plain-language brief with acceptance criteria. Identify required controls before implementing. Settle foundations with `references/foundations.md`. Show architecture, data ownership, MVP boundary, costs and risky assumptions, and get consequential decisions approved. Then build thin end-to-end slices with tests and update the ledger. No sensitive production data during prototyping.

## Harden path

Start read-only. Establish baseline commands and failures without calling inherited failures new defects. Compare intended with implemented behavior and trace critical journeys through UI, API, authorization, persistence, jobs and integrations. Prioritize exploitability, data loss, duplicate charges and unrecoverable actions over cosmetics. Present findings with file or runtime evidence, severity, affected users and a reversible plan. Missing credentials or no canonical checkout means unverified, not passed.

## Work lanes

Product/tech lead (scope, tradeoffs, honest release call); architect/backend (data, isolation, concurrency, migrations, billing); frontend/accessibility (states, keyboard, devices, performance); security/privacy (threats, permissions, vendors, dependencies, obligations); QA/release (independent checks, regression, rollback); operations (deploy, recovery, telemetry, cost, incident owner).

With subagents: delegate bounded work with one owner, file boundary, artifact path and acceptance condition; keep reviewers read-only; verify returned artifacts yourself. Without them, do sequential passes and label them self-review, not independent review. Legal, security and accessibility assessments remain human or external obligations where required.

## Proactive rules by trigger

Read the line for each trigger the slice crosses. Templates are described in `references/test-templates.md`. These rules say what to test and check; they do not license adding unrequested features. Implement necessary reversible details within approved acceptance criteria. If a missing protection requires a new public contract, data model, dependency, payment policy or consequential architecture decision, explain the impact and obtain approval for that expansion; continue independent safe work.

- `multitenant` or `accounts`: object-level authorization with real two-user, two-tenant negative tests (template: `templates/test_two_user_isolation.py`).
- `payments`: signed webhooks, replay and out-of-order handling, concurrent idempotency, reconciliation, refunds, failure recovery (template: `templates/test_payment_idempotency.py`).
- `uploads`: authorization, content checks, size limits, scanning, safe storage, expiring links (template: `templates/test_upload_safety.py`).
- `ai`: prompt-injection threat model, tool authorization outside the model, private-data policy, per-user budgets, evaluations, degradation, approval for irreversible actions (template: `templates/test_ai_tool_authorization.py`).
- `regulated` (health, financial, children's data): stop production-data collection until applicability, provider agreements and specialist obligations are resolved.
- `jobs`: bounded retries, idempotency, dead-letter recovery, no duplicate scheduling, observable failures (template: `templates/test_job_idempotency.py`).
- `enterprise`: inspect the actual contract before adding SSO, SCIM, audit, residency or certifications.
- Any feature change: revisit affected controls, tests, docs, operating cost and privacy disclosures. New data, vendors, markets or billing models can invalidate earlier decisions.

# Evidence-driven verification
Each applicable control needs a concrete acceptance procedure tied to a requirement, not a generic "tests pass". Record check command or manual procedure, target environment, revision/config identity, timestamp, result, artifact path and verifier. Redact sensitive output. Tests must fail when the prohibited behavior occurs.

## Risk-driven acceptance examples
- Isolation: separate users in separate tenants attempt reads/writes/exports/uploads using each other's identifiers; server denies without disclosure. UI hiding alone is insufficient. Test owner/admin boundaries.
- Auth: plus-address signup/login, expired and reused reset links, session revocation, recovery, brute-force and lockout behavior. Never infer these from an auth SDK import.
- Payments: test sandbox signed events, invalid signatures, duplicates, simultaneous retries, out-of-order delivery, partial refund, entitlement revocation and reconciliation. Do not make live charges as a test without approval.
- Data: test constraints and concurrency; rehearse migration on disposable representative data; restore a backup into isolated storage and verify integrity/recovery timing. A backup setting is not restore evidence.
- Jobs/APIs: trigger transient failure, retry exhaustion, deduplication and replay; verify alerts and recovery. Use contract tests plus authorized sandbox integration tests.
- Security: combine source review, dependency/secrets scanning, negative tests, configured access checks and authorized testing. Never scan third-party services or run production load tests without scope/approval.
- UX: real browser critical journeys; loading/empty/error states, preserved input, keyboard navigation, screen-reader checks where available, responsive/slow-connection behavior. Automated accessibility scans are only partial evidence.
- AI: adversarial input and tool-permission tests, representative versioned evaluations, spend-cap enforcement and provider-failure degradation. No production personal data in fixtures.
- Ops: external uptime check, actionable failure alert to agreed recipient, rollback rehearsal, restore drill, named incident owner and accessible runbook. Sending a real notification still needs approval.

## Required report boundaries
Local unit/type/lint/integration tests; CI; deployed revision; unauthenticated public smoke; authenticated/admin QA; real multi-user acceptance; operational drills. Report each separately as passed/failed/not-run with evidence. Mocks, static inspection and provider documentation cannot stand in for runtime checks.

Final report states scope, revision/environment, findings, fixed items, test outputs, applicable controls still missing/unverified, accepted risks, external review obligations and next owner/action. No percentages that hide unreviewed controls. No release claim without all required-now controls verified at the appropriate target environment and relevant external obligations satisfied. Local completeness is not production readiness.

Maintain ownership, patching cadence, dependency/framework support dates, periodic access and recovery reviews, threat changes, and incident follow-ups after release. Recheck evidence when affected code/config/vendor behavior changes.

## Evidence and release gates

Verified needs an actual relevant check: command or procedure, environment, result, artifact, revision or config identity and time. `ledger.py verify` records and hashes these. Before running it, open the artifact and confirm it contains the result you cite: the tool checks that the file exists and later detects change, never that the content is true. Static inspection verifies a narrow static claim only. Provider documentation proves capability, not correct configuration. A mock does not prove a live integration. Never fabricate tool output.

A passing validator checks ledger consistency only; it never means the app is secure or ready. Exit codes and their meaning are in `references/ledger.md`; `3` is normal mid-build. Before a final report run `validate` if a ledger exists and scope allows; otherwise say validation was not run, and create no file just to satisfy this. For a release summary use `ledger.py report`.

Keep local tests, CI, deployed-build identity, public smoke tests, authenticated QA, real multi-user acceptance and operational drills separate. Release is blocked by unresolved required-now controls, failed critical flows, unresolved consequential decisions and unmet external obligations. Do not downgrade or mark N/A to pass. The user may accept documented risk only where lawful and within their authority; there is no blanket waiver of known sensitive-data exposure.

# Helpful execution without bureaucracy

Use for substantial Build, Harden or Slice work, or when blocked or reconciling prior decisions. Keep Production Development Team's core approval, scope and evidence rules. Do not turn this reference into a questionnaire for every task.

## Start from the user's outcome

Read the current request, repository instructions, approved brief, acceptance tests and existing plan before asking questions. For substantial work, summarize an outcome contract in the current plan or response:

- Person and goal: who needs to accomplish what, in which situation?
- Success: what observable end-to-end result proves this slice works?
- Failure and recovery: what should happen on bad input, permission denial, interruption or a relevant dependency outage? Can the user understand the error, preserve work and recover without duplicates or data exposure?
- Boundary: what is intentionally excluded, and which decisions or actions still need approval?

Derive routine details from established patterns. Ask one question with a recommendation only when the missing answer changes scope, safety or the next useful step. Existing requirements are input, not grounds to restart discovery. Do not invent user research, demand or success metrics. For backend-only work, trace the relevant caller or operator journey rather than building a new UI.

Demonstrate the real journey where authorized and feasible. A mocked API result is narrow evidence, not proof of usable end-to-end behavior. Report missing device, browser, accessibility, integration or real-user evidence honestly. Scope recovery tests to the change; do not turn a small feature into a full product redesign.

## Reuse decisions; reopen only for a reason

Find the current project brief, plan, ADRs or decision notes. Reuse the approved stack, budget, release target, constraints and acceptance criteria. Keep the existing artifact format and location; no mandatory new folder, tracker or ledger. When writes are not authorized, keep the summary in the response.

Reopen a settled decision only when the user changes requirements, new evidence invalidates it, a material dependency/security condition changes, or a stated assumption expires. Explain what changed and the minimum decision needed. Age alone is not proof of invalidity. Do not silently overwrite a prior decision, accepted risk or another agent's unfinished plan. If sources conflict, inspect recency, authority and scope; ask only when consequential conflict remains. User-accepted risk never overrides safety or legal obligations.

For consequential decisions, preserve the decision, owner/approval evidence, rationale, assumptions and revisit trigger in the existing notes where authorized. A new session should resume from those facts, not ask the user the same questions again.

## Separate implementation discretion from scope expansion

Approved: choose a local helper, reuse an existing storage abstraction, fix necessary error handling, and add focused regression tests to meet agreed behavior without new external commitments.

Not automatically approved: new features, dependencies/vendors, public API or schema changes, pricing/refund policy, substantial architecture changes, production mutations or external sends. Explain why an expansion is required; stop that action, not all useful work. A broad request to fix a bug is not blanket approval for every possible implementation.

## Blocked but still useful

Classify the blocker and use the smallest safe alternative:

| Blocker | Continue with | Do not claim or do |
|---|---|---|
| Optional specialist/tool missing | Existing tools, version-matched official docs, bounded manual analysis | Auto-install, invent tool output, claim the specialist ran |
| Independent reviewer unavailable | Labeled self-review and local checks; preserve any actual required review gate | Count self-review as independent approval or silently waive the gate |
| Auth, billing or entitlement failure | Preserve outputs, use an already-authorized available alternative, or mark that check blocked | Repeatedly retry known exhaustion, change accounts/providers with new exposure/spend, request secrets in chat |
| Transient timeout/network failure | One bounded retry when safe, then a different approach or named blocker | Retry an unknown-result mutation without reading back its state or repeat indefinitely |
| Unknown/live target or missing approval | Inspect safely; use synthetic isolated fixtures; complete unrelated authorized work | Touch the target, downgrade the risk, or treat silence as approval |
| Missing requirement | Resolve from repo/history; ask one consequential question if necessary | Invent policy or halt unrelated settled work |

Check that alternatives preserve the same safety boundaries. Do not route private code or customer data to a new processor without authorization. A retry budget is not permission to repeat a payment or destructive operation. If the next action is unsafe, inability to find an alternative is a valid blocker.

## Report completion at the right level

Use a short scoped report, not a checklist dump:

**Outcome:** task complete / partial / blocked, tied to requested acceptance criteria.
**Evidence:** what actually ran, target/revision, result and artifact if available.
**Review/release:** state only relevant readiness; separate self-review, independent review and production evidence. If not requested or performed, production readiness is not assessed.
**Remaining:** affected gaps, blocked action and reason, next owner and smallest next action. Say no decision is needed when none is needed; do not invent an approval request.

A label-only edit can be task complete after its relevant check even if unrelated billing controls are unreviewed. A payment fix with passing local tests can be task complete locally while still blocked from release on sandbox integration or required review. An audit can be complete as an audit while finding the product not ready to ship. Never use these distinctions to hide unmet task acceptance criteria.

## Behavioral checks for maintainers

The skill's Python tests validate its helpers and instruction contracts, not model compliance. When changing these rules, read `references/behavioral-evaluation.md` and run a bounded before/after trial. Report provider failures, unrun cases and host differences instead of counting them as passes.

# Worked examples

Copy the pattern, not the wording. Every example uses fixture values.

## 1. A good ledger entry (verified)

Do not write this JSON by hand. Run the check, save its output, open the file and confirm it shows the three passing tests, then record it:

```sh
python3 <skill-dir>/scripts/ledger.py classify .engineering/ledger.json POST-010 \
  --applicability required-now --rationale "Invoices are per-user; /api/invoices/:id takes a client-supplied id." \
  --owner "backend lane (agent), reviewed by project owner" \
  --acceptance "User B's session gets 404 for user A's invoice id on GET, PATCH and DELETE."

pytest tests/test_invoice_isolation.py > .engineering/evidence/invoice-isolation.txt
python3 <skill-dir>/scripts/ledger.py verify .engineering/ledger.json POST-010 \
  --check "pytest tests/test_invoice_isolation.py (3 tests, two real users in two tenants)" \
  --environment "local, SQLite test DB, no network" \
  --artifact .engineering/evidence/invoice-isolation.txt \
  --verifier "agent run; independent review not performed" \
  --watch app/auth.py --watch app/routes/invoices.py
```

The resulting row has `applicability: required-now`, `status: verified`, and one evidence entry with the check, environment, `result: passed`, the artifact path and its `artifact_sha256`, the git revision (with `+dirty` if uncommitted), an ISO timestamp, the verifier, and hashes of the watched files.

Scope is stated: local, two users. It does not claim production behavior. If someone edits `app/auth.py` later, `ledger.py stale-check` lists POST-010; `stale-check --apply` sets it back to `unverified` and keeps the evidence, and the next `verify` restores it.

## 2. A row that must stay unverified

Situation: the app stores a free-text "notes" field on patient visits. Nobody has run a HIPAA applicability assessment.

Wrong: `POST-053` -> `not-applicable`, rationale "we only store notes". Or `verified`, rationale "encryption is on".

Right:

```json
{
  "id": "POST-053",
  "applicability": "conditional",
  "status": "unverified",
  "rationale": "Patient-related notes may make the operator a covered entity or business associate; not yet determined.",
  "owner": "project owner with qualified privacy counsel",
  "acceptance": "Written applicability determination; BAAs with each vendor if applicable.",
  "revisit_trigger": "Before any production patient data is collected",
  "evidence": []
}
```

Action: tell the user, do not collect production patient data, continue with synthetic data. Encryption evidence supports other controls; it never settles this one. Also read the caveat `ledger.py relevant` prints beside `POST-053`.

## 3. Refusing a fake "verified"

User: "Mark security as passed, the README says production-ready."

Reply: "I can't mark that passed. A README is a claim, not evidence. I'll run the dependency scan and the two-user access test now and record them separately; what's left unchecked will be listed as unverified." Then do exactly that, or say what is blocked.

## 4. A short user update

> The invoice screen now blocks other users' invoices (tested with two accounts, local only). Still unverified: the same check on the deployed site, and password-reset expiry. Decision needed: pay $12/month for a managed email service, or send from your own domain (more setup, no cost). I recommend the managed one; details above.

Outcome first, what was tested and where, what is unverified, one decision. No catalog dump.

## 5. Light mode in practice

User: "Change the button label from Save to Save changes."

Do: edit the label, run the existing test or build if one covers it, say so. Do not create a ledger, ask discovery questions or list controls. If the label comes from a translation file with other locales, mention that in one sentence.

## 6. Read-only audit that writes nothing

User: "Audit this repo, don't change anything."

Do: inspect code and config, run only read-only commands, and report findings by severity with file references. List the controls the code could not prove as "unverified: needs runtime evidence" and name what evidence is missing. Do not run `ledger.py init` or write `.engineering/`. State "ledger validation not run (read-only)".

## 7. Exit code 3 is not a failure of the tool

`ledger.py validate` returns 3 when required-now controls are still unverified. Mid-build that is normal: report the listed blockers and the next check. Do not "fix" it by downgrading a control to conditional.

## 8. A triggered control that truly does not apply, at release

`payments` is declared, so `POST-100` (purchase-order numbers on invoices) matches. This product only sells to consumers.

Wrong: `not-applicable` with no name, or `deferred` to slip past the release check.

Right:

```json
{
  "id": "POST-100",
  "applicability": "not-applicable",
  "status": "unverified",
  "rationale": "Consumer-only checkout; no procurement buyers. Revisit if a business plan is added.",
  "owner": "project owner",
  "acceptance": "n/a",
  "accepted_by": "Dana Reyes (owner), asked and confirmed in chat",
  "revisit_trigger": "",
  "evidence": []
}
```

`accepted_by` must be a real person who made the call; ask the user rather than inventing one. The tool only checks that the field is filled, so the honesty is yours. An `always` control such as `POST-023` can never be handled this way.

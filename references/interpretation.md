# Interpret the catalog; do not enforce rhetoric as law

The catalog is a broad inventory, not a universal launch contract. Applicability and test design must reflect the product.

Each bullet below is keyed `[N-...]`. `catalog.json` links a control to its keys through `notes`, and `scripts/ledger.py` prints the matching caveat beside the control (`relevant`, `next`). Read the caveat before classifying a control. Keep one bullet per line; the script parses `- [KEY] text`.

- [N-HIPAA] HIPAA does not automatically apply to every app holding a health-related record. Determine entity, relationship, data and jurisdiction; obtain a qualified assessment and BAAs where applicable. Until applicability is resolved, do not collect production patient data.
- [N-SOC2] SOC 2 requires an actual scoped examination by an auditor, not passing this checklist. Do not mark it verified from code.
- [N-PRIVACY-LAW] GDPR and other privacy duties depend on scope, lawful basis and context; software alone cannot certify compliance. Residency and policy promises need the contract or policy text, not an assumption.
- [N-DELETION] Deletion needs retention exceptions, backup expiration or isolation, and restore-time reapplication of deletions; immediate surgical removal from every immutable backup is not always the right policy. Soft deletion can also conflict with privacy or retention duties. Explain and validate the actual policy.
- [N-MONEY] Integer minor units with currency-specific precision, or suitable exact decimals, are fine; binary floating point is not. Some currencies have zero or three minor digits, so not everything is cents. Tax and metering rules come from the markets actually sold into.
- [N-TIME] UTC instants do not replace IANA time zones for recurring local-time events. Test daylight-saving gaps and overlaps.
- [N-INJECTION] Prefer parameterized queries and contextual output encoding; indiscriminate input sanitization is not a substitute and can corrupt valid data.
- [N-CORS] CORS is not authorization and does not universally prevent CSRF. Select CSRF controls based on how authentication is carried (cookies versus bearer tokens).
- [N-MIGRATION] Forward-compatible expand/contract migrations and roll-forward recovery are often safer than automatic reverse migrations. Prove compatibility and data recovery on disposable data; never run a destructive rollback just to tick a box.
- [N-SUPPORT-ACCESS] Support impersonation raises privilege risk and is not mandatory. If included, make it time-limited, reason-recorded and auditable; otherwise document the alternative.
- [N-SSO] SAML is one SSO protocol. SCIM, multi-region, complex roles, formal on-call rotations and bespoke admin panels depend on actual contractual or product needs; inspect the real requirement before building.
- [N-PROMPT-DATA] Sensitive information may go to an approved processor when lawful and properly configured; this is not an absolute ban on external prompts. Minimize data and verify contractual and security controls first.
- [N-PROMPT-INJECTION] Prompt injection cannot be solved by one filter. Treat model output as untrusted; enforce authorization and irreversible-action gates outside the model.
- [N-MANAGED] Managed services reduce work but still need configuration evidence, least privilege, recovery plans, cost checks and application-level tests. A backup or encryption setting is not restore or key-management evidence.
- [N-SOLO-REVIEW] A solo builder can verify POST-090 and POST-133 honestly: POST-090 with a test run plus a recorded check that the tests fail on broken behavior (planted bug or the old code); POST-133 with a diff review pass recorded with `--verifier "self-review: <name>"`. Never call self-review or agent review independent. Independent human review is the POST-148 lane; when a change touches authorization, money or deletion and no second person exists, say so and record the user's name with `classify POST-133 --accepted-by NAME`; the report prints it beside the self-review. `ledger.py verify` and `validate` refuse a POST-133 verifier that is not `self-review: NAME` or `independent: NAME`, and release requires the user's `accepted_by` for an `independent:` claim.
- [N-INCIDENT] A simple app still needs a named incident owner and adequate authorization; a formal on-call rotation depends on what the service promises.

Useful current primary references for applicable controls: OWASP ASVS and Cheat Sheet Series (owasp.org), W3C WCAG (w3.org/WAI/standards-guidelines/wcag/), NIST SSDF (csrc.nist.gov), HHS HIPAA guidance (hhs.gov/hipaa), PCI SSC (pcisecuritystandards.org), and applicable regulators. Cite consulted versions and dates. These pointers are not a claim that a standards assessment has been completed.

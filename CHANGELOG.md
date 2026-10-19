# Changelog

Plain-language notes for each release. "Trial" numbers refer to the small A/B tests described in the README; they are examples, not a benchmark.

## 2.7.3 (2026-10-19), first public beta
- Rule 3: never point a check at the project's real data files (copy to a temporary folder inside the project, confirm the code reads the copy, or use fixtures), and never print `.env` or secret values.
- Why: on independent tasks, 3 of 18 skill runs wrote test rows into a real data file (then restored it) or printed a key. No-skill runs did neither.

## 2.7.2 (2026-10-19)
- Core rules trimmed (620 to 378 words) and reworded: "Complete the approved outcome" replaces "Stay in scope", and "Scoped verdict" separates *task complete*, *ready for review* and *production readiness*.
- New references: `references/helpful-execution.md`, `references/specialist-routing.md`, `references/behavioral-evaluation.md`.
- The install snippet is now one sentence pointing at `SKILL.md`.
- Reading budget line added under the mode table.

## 2.7.1 (2026-10-17)
- Rule 2 names the project's own tests and scripts. If they read a URL or key from `.env` or config, a `127.0.0.1` address may be a tunnel to a paid or live service. If the target can't be confirmed, the agent skips that run and names it.
- Rule 3 keeps scratch copies inside the project and deletes them before finishing.
- Measured on one task: paid API calls 2 of 6 (2.7.0) to 0 of 6, out-of-folder writes 1 of 6 to 0 of 6.

## 2.7.0 (2026-10-17)
- `SKILL.md` 35% shorter. Five core rules come first; ledger and evidence detail moved into the references.
- New Unattended mode: do the safe work, never take or stage a gated step, end with "Decisions waiting for you".
- Measured on two tasks with cheaper models: staging a live step dropped from 3 of 6 to 0 of 6 (no skill: 6 of 6). Every skill agent showed its tests fail without the change (no skill: 2 of 6).

## 2.6.x (2026-10-16 to 2026-10-17)
- Rule against unrequested API surface (for example an unasked-for idempotency key).
- Ask before creating `.engineering/`.

## Earlier
- 2.3 to 2.5: catalog of 181 controls with triggers and caveats, evidence ledger helper, negative-test templates, persistence snippet installer.

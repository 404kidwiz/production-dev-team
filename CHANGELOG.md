# Changelog

Plain-language notes for each release. "Trial" numbers refer to the small A/B tests described in the README; they are examples, not a benchmark.

## Unreleased
- New `scripts/scan_side_effects.py` (danger map): a read-only static scan that lists every script, CLI subcommand, package script and test that can message people, move money or place orders, delete or bulk-overwrite records, or call a service with a URL or key from `.env` or config, with the command to run, file:line hops and a one-line risk. Never runs project code or prints secret values. Python (call graph) first, JS/TS at file level. `SKILL.md` makes it the first step of Audit and Slice.
- Why: every hazard in the A/B trials was visible in the code but took agents time to find. On the trial 8 and trial 4 apps it lists all six hazardous commands in under a second, and nothing else.

## 2.8.0 (2026-10-19)
- New `scripts/safe_run.py`: runs a project's own command in a temporary copy inside the project, with every service URL in `.env` and the environment sent to a local recorder, other database/queue URLs sent to a closed port, secrets replaced with a placeholder and proxy-aware HTTP routed through the recorder. It reports every intercepted call (method, path, body) without printing secret values. Stdlib only; not a network sandbox.
- Rule 2 names it: an unknown target is treated as live, and the run goes through `scripts/safe_run.py` or is skipped and named.
- Why: trials showed strong models already avoid visible hazards; the remaining failures (paid calls via the project's own tests, writes to real data, printed keys) need a tool, not more prose. Safe-run lets an agent check end to end without touching live services.

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

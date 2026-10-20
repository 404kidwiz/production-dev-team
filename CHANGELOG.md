# Changelog

Plain-language notes for each release. "Trial" numbers refer to the small A/B tests described in the README; they are examples, not a benchmark.

## 2.10.1 (2026-10-19)
- `scripts/doctor.py` warns when this copy is a real folder inside Cursor's built-in skills folder (`~/.cursor/skills-cursor`, or a shared skills folder that path is symlinked to). Cursor deletes folders it did not create there about 72 hours after first seeing them; on one test machine that removed a git clone of this skill. A symlinked install is not affected. When Cursor has already recorded the copy, the warning gives the time it can be deleted.
- The installer's plugin-folder warning now also covers a Claude Code config folder set with `CLAUDE_CONFIG_DIR`, not only `~/.claude`.
- Docs: the Codex guard-hook example in `references/host-snippets.md` uses the same fail-open command (Codex also treats a `PreToolUse` exit 2 as a block), and the file records live runs of the guard on Claude Code 2.1.289 and Codex CLI 0.154.0.

## 2.10.0 (2026-10-19)
- One-command install. The repository is now a Claude Code plugin marketplace (`.claude-plugin/marketplace.json` and `plugin.json`, version pinned to `VERSION`): `claude plugin marketplace add 404kidwiz/production-dev-team`, then `claude plugin install production-dev-team@production-dev-team`. Codex and Cursor install with one `git clone` into `~/.agents/skills/production-dev-team`, which both scan. The README's new Install section covers update and uninstall for each, and replaces the old `~/.codex/skills` advice (current Codex reads `~/.agents/skills`).
- New `scripts/doctor.py`: a read-only install self-check. It reports which agents can find this copy (Claude Code plugin, or a user or project skills folder for Claude Code, Codex or Cursor, including symlinks), warns about other copies with a different version, checks Python 3.9+, SKILL.md and that `plugin.json` matches `VERSION`, and starts the safe-run, danger-map and guard scripts with `--help`. Exit 1 only when a check fails.
- Why: copying a folder by hand was the main reason people did not try the skill, and a silent wrong-path install looks the same as a working one.
- Guard hook fails open when its script is missing. `install_snippet.py --hook` now installs `[ -f <path> ] || exit 0; exec python3 <path>` instead of `python3 <path>`: a plugin update deletes the old versioned folder, `python3` then exits 2, and Claude Code treats a `PreToolUse` exit 2 as a block, so every shell command was refused. Re-running the installer upgrades an old entry in place; `--remove` removes either form. The installer also warns when the skill path is inside the Claude Code plugin cache. Needs a POSIX shell (see `references/host-snippets.md` for Windows).

## 2.9.0 (2026-10-19)
- New `scripts/scan_side_effects.py` (danger map): a read-only static scan that lists every script, CLI subcommand, package script and test that can message people, move money or place orders, delete or bulk-overwrite records, or call a service with a URL or key from `.env` or config, with the command to run, file:line hops and a one-line risk. Never runs project code or prints secret values. Python (call graph) first, JS/TS at file level. `SKILL.md` makes it the first step of Audit and Slice.
- Why: every hazard in the A/B trials was visible in the code but took agents time to find. On the trial 8 and trial 4 apps it lists all six hazardous commands in under a second, and nothing else.
- New optional `scripts/guard_hook.py`: a Claude Code `PreToolUse` hook that asks before a shell command deletes data outside temp and build folders (`rm -rf`, `find -delete`, destructive SQL through a database client), pushes or deploys, runs a migration, or runs a project script or test while `.env` sets a URL or key (names shown, never values; commands wrapped in `scripts/safe_run.py` pass). It denies instead of asking in `bypassPermissions`/`dontAsk` modes or with `--deny` (Codex, which does not support "ask"); `--off` turns one check off. Silent for everything else; a crash never blocks. Install with `install_snippet.py --host claude --project DIR --hook` (writes `.claude/settings.local.json`, keeps other settings, `--remove` undoes it).
- Why: instruction-only gates depend on the agent remembering them; a hook makes "ask before live actions" enforceable without touching normal work.

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

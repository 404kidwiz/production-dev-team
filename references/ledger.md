# Ledger reference

Read this when you are about to create or change `.engineering/ledger.json`. Run commands as `python3 <skill-dir>/scripts/ledger.py <command> ...`. Use the commands below instead of editing the JSON by hand: they write atomically, serialize concurrent writers with a `<ledger>.lock` sidecar, validate before saving and leave the file untouched on refusal.

## Commands

| Command | Writes? | Purpose |
|---|---|---|
| `init --out PATH` | yes | Create a blank ledger (never overwrites). |
| `vocabulary` | no | List valid trigger names. |
| `triggers PATH a,b,c` | yes | Replace the declared product triggers (pass the full set; `--clear` empties). |
| `relevant [PATH] --triggers a,b [--brief] [--include-lanes]` | no | Controls matching triggers, with their trigger tags and caveats. `always` controls match every slice and are marked by `always` in `triggers`. Lanes (`POST-148` to `POST-160`) are hidden unless `--include-lanes`. Without `PATH` it is a pure read-only view. |
| `next PATH [--batch N] [--triggers a,b]` | no | The next N (default 10) unclassified relevant controls; `--triggers` overrides the ledger's declared triggers. |
| `classify PATH ID [--applicability ...] [--status unverified\|missing\|partial] [--rationale ...] [--owner ...] [--acceptance ...] [--revisit-trigger ...] [--accepted-by NAME]` | yes | Record a decision about one control. |
| `verify PATH ID --check ... --environment ... --artifact FILE_OR_URL --verifier ... [--result passed\|failed] [--revision R] [--watch FILE ...]` | yes | Record evidence and set the status. |
| `stale PATH ID --reason ...` | yes | Mark one control unverified because something changed. |
| `stale-check PATH [--apply]` | only with `--apply` | List verified controls whose artifact or watched files changed; exit 3 if any. |
| `validate PATH [--release \| --structure-only] [--verbose]` | no | Consistency check. `--structure-only` ignores unverified required-now controls when choosing the exit code; it cannot be combined with `--release`. |
| `summary PATH` | no | Counts and the unclassified-relevant list. |
| `slice [PATH] --triggers a,b [--scope TEXT]` | no | One-command slice verdict in plain language: the controls the triggers pull in (with `always` ones, lanes hidden), each row's state (untouched, classified, or verified with its environment), caveat keys, then which required-now rows are verified, which are not, and how many were not examined. Exit 0, 3 (a required-now row here is unverified, a row drifted, or an `always` row is deferred or conditional with no `accepted_by`), 2 or 1, as below. It also prints `validate`'s warnings, lists rows this slice's own triggers match that are deferred or not-applicable without `accepted_by` (a warning only: exit stays 0 unless the row is `always`, so read the warnings, not just the exit code), and warns (exit unchanged) when the slice triggers share nothing with the declared ones. Exit 0 with untouched rows is not a pass; the output says so. Without `PATH` it lists the slice and notes that no ledger exists. Takes no lock. |
| `upgrade PATH` | yes | After a skill update adds catalog controls, append them as untouched rows and refresh the catalog hash. Never edits or removes existing rows; refuses, writing nothing, if the ledger lists ids the catalog dropped, has duplicate ids, or has rows that are not objects with a string id. |
| `report PATH [--scope TEXT] [--out FILE]` | only with `--out` | Markdown release report. Refuses to overwrite. |

## Fields

- **applicability**: `required-now`, `conditional`, `deferred`, `not-applicable`.
- **status**: `unverified`, `missing`, `partial`, `verified`. Independent of applicability. Only `verify` can set `verified`.
- Every classified row needs a rationale, an owner and a concrete acceptance check. `conditional` and `deferred` rows also need a revisit trigger of your own; the init text ("Discovery must establish applicability") does not count. Classify an untouched row in one call with all of those fields; a partial change to an untouched row is refused.
- Unknown applicability stays `conditional` and untouched, with a question to the user; never `not-applicable`.
- An `always` control can never be `not-applicable`.
- A pristine row (init text, no evidence) counts as unclassified. `classify` refuses a change that would return a classified row to that state.

## Triage workflow (Slice, Harden and Build)

The catalog (`references/catalog.json`) holds 181 controls, each with a `domain`, `triggers` and linked caveats from `references/interpretation.md`. It is a menu, not a form to complete on every turn. The command table above has every command and flag.

1. Declare what the whole product touches once: `ledger.py triggers .engineering/ledger.json payments,accounts` (it replaces the list; names from `ledger.py vocabulary`).
2. For a slice, run `ledger.py relevant <ledger> --triggers <only what this slice touches> --brief`, read each caveat, decide what truly applies, and record it with `ledger.py classify`. Classify only controls the change could break; leave the rest untouched and say so. Do not invent rationales to clear rows.
3. Run the full sweep (`next`, `validate --release`) at three checkpoints only: initial audit, major scope change, before release. `unclassified_relevant` is your to-do list, not noise.
4. `not-applicable`, `deferred` or `conditional` on a control matching the declared triggers needs a rationale; at release it also needs `accepted_by`, a name you get from the user, never invent. `always` controls can never be `not-applicable`. `POST-148` to `POST-160` are work lanes: name who covers each; they never block a release.
5. Record evidence only with `ledger.py verify` after you have opened the artifact; use one artifact file per control. Do not hand-edit `status` to `verified`.
6. Answering "is this slice safe to ship?": run `ledger.py slice <ledger> --triggers <slice triggers> --scope "<what, where>"` (one read-only call; `report --scope` for a written summary), and say what you verified, what failed, and which controls you did not examine. `validate --release` answers "is the whole product releasable" and fails by design until every control is classified. Recipe: `references/ledger.md`, "Verdict for a slice".

In Build, ask once for permission to write `.engineering/`; until granted, keep triggers and decisions in the conversation. Initialize with `ledger.py init --out .engineering/ledger.json` (never overwrites). After a skill update, run `ledger.py upgrade` once so an older ledger gains the new controls. If Python is absent, keep the same JSON by hand and say validation was not run. `references/examples.md` shows a good entry, a refused "verified" and a short user update.

## Classifying

1. Declare what the whole product touches once with `triggers`.
2. For a slice, list with `relevant --triggers <only what the slice touches> --brief`, read each caveat, and classify only the controls the change could break. Leave the rest alone and say you skipped them.
3. `classify` takes all missing fields in one call when the row is untouched:
   `classify L POST-010 --applicability required-now --rationale "multi-tenant invoices" --owner "backend" --acceptance "user B gets 404 for user A's invoice"`.
4. `not-applicable`, `deferred` and `conditional` on a control that matches the declared triggers need a rationale naming why. Outside release, `validate` only lists them under `non_committal_on_triggered`; read that list and ask the user. At `--release` each such row also needs `accepted_by`: the name of the person who accepted the exclusion or risk. `accepted_by` is attribution typed by an agent, not authentication. Never invent a name; get it from the user. Only the newest evidence entry of a row is checked for labels and results; older entries are history.
5. `POST-148` to `POST-160` are work lanes. Name who covers each; they need not be classified and never block a release.
6. Run the full sweep (`next`, `validate --release`) only at the initial audit, after a major scope change and before release.

### Verdict for a slice

A slice verdict is not `validate --release`: that sweep fails by design while untouched controls remain. For a slice, say what you checked and what you did not.

The one-command path: `ledger.py slice L --triggers <slice triggers> --scope "<what the slice covers, local or deployed>"`. Run it before you start (it lists what to look at) and again after you `classify` and `verify` the controls the change could break. Exit 3 means a required-now row in this slice is still unverified or its evidence drifted. Unclassified controls outside the slice do not affect it.

The longer path, when you need the JSON or a written file:

1. `relevant --triggers <slice triggers> --brief`: note which `always` controls came with it.
2. `classify` each control the change could break; `verify` the ones you tested.
3. `validate` (no `--release`): exit 3 means required-now controls anywhere in the ledger are still unverified; list them.
4. `report --scope "<what the slice covers, local or deployed>"` for the written summary.

Either way, tell the user: verified (with environment), failed or unverified, and the controls you chose not to examine. `validate --release` is the answer to "is the whole product releasable", which a slice cannot give.

### Plain-language glossary

- **required-now**: this change must have it working before it ships.
- **conditional**: needed only if something happens later (named in the revisit trigger).
- **deferred**: needed, but knowingly postponed; someone has to accept that.
- **not-applicable**: does not apply to this product, with a reason written down.
- **unverified**: nobody has shown it works yet.
- **missing**: checked and absent, or the last check failed.
- **partial**: some of it is in place, not all.
- **verified**: a check was run and its saved output shows it works, in a named environment.
- **accepted_by**: the person who agreed to skip or postpone something; typed in, not a login.
- **trigger**: a product feature (accounts, payments, uploads...) that pulls in the controls it can break.
- **lane**: a work area someone owns (`POST-148` to `POST-160`); not a pass/fail item.
- **drift**: a file the proof depended on changed after it was verified, so the proof may be out of date.

## Recording evidence

`POST-133` (review of every change) needs a `--verifier` of `self-review: NAME` or `independent: NAME`; `validate` checks this too, so a hand edit cannot skip it. At `--release`, `independent:` also needs `accepted_by`: the user confirms who reviewed, the agent cannot. An agent reviewing its own or another agent's diff is `self-review:`; `independent:` is for a named human who did not write the change, and like `accepted_by` it is attribution the agent types, not proof. The report shows the verifier of every verified control and says when `POST-133` was only self-reviewed.

Run the check first, save its output to a file, and open that file to confirm it shows the result you will cite. Use one artifact file per control (or a new file name on each re-run): two controls pointing at one file lose their evidence when it is overwritten. Then record it:

```
verify L POST-010 --check "pytest tests/test_isolation.py" --environment "local sqlite, seeded two tenants" \
  --artifact .engineering/evidence/post-010.txt --verifier "agent run" --watch app/authz.py --watch app/models.py
```

`verify` hashes the artifact (sha256) and each `--watch` file, stamps the time, takes the revision from git (short hash, `+dirty` if tracked files changed; the ledger folder `.engineering/` is ignored) unless you pass `--revision`, validates, then writes. Paths are given relative to the current directory but stored relative to the project root (the folder above `.engineering/`, or the ledger's own folder), so later checks give the same answer from any directory. Files outside the project are stored by absolute path. It refuses placeholder values (`n/a`, `trust me`, `ok`, `x`, and their punctuation, width and invisible-character variants), a missing artifact or watched file, anything that is not a regular file, and a control that was never classified or is `not-applicable`. Only `http://` and `https://` count as URLs: a URL artifact is accepted at release but cannot be hashed, so it can change after you looked at it. Prefer a saved local file.

`--result failed` records a failure and sets the status to `missing`; run `verify` again later to add a passing entry. History is kept; only the latest entry has to be `passed`, but every entry must be well formed, so a failed run with a blank revision or a placeholder is refused when you record it.

What the tool cannot do: it cannot tell whether the artifact's content is true. Opening the file is your job.

## Staleness

- `stale-check L` is read-only and exits 3 if any verified control's artifact changed or any watched file changed or vanished. Run it before a release and after a refactor.
- `stale-check L --apply` sets each drifted control to `unverified`, keeps its evidence and appends "Stale (date): reason" to the rationale. The next `verify` restores it.
- `stale L POST-010 --reason "auth middleware changed in abc123"` does the same by hand when you know a change matters but the files were not watched. It needs a verified control and a non-empty reason.
- A passing `verify` afterwards removes the stale note from the rationale and keeps the old evidence.
- In slice mode drift is a warning. With `validate --release` it is an error.

## Release

`validate --release` requires declared triggers, every non-lane control classified, no drifted evidence, a recorded hash for every local artifact of a verified row, and `accepted_by` on non-committal rows that match the triggers. It is stricter than slice mode on purpose. Errors are listed with the actionable ones first and the long "unclassified" list last; use `--verbose` for all of them.

`report` renders the same state as Markdown: blockers, drift, untouched relevant controls, every exclusion and deferral (with who accepted it, or `NO accepted_by recorded`), verified controls, and the boundaries it did not assess. It refuses to run on a structurally invalid ledger, so a hand-edited fake `verified` row cannot reach a report.

Exit codes: `0` valid with no unverified required-now control (it says nothing about unclassified rows; read `unclassified_count` and `unclassified_relevant`); `1` structural errors; `2` could not run or refused; `3` valid but required-now controls remain unverified or evidence drifted (`stale-check`). `3` is normal mid-build. A passing validator never means the app is secure; it checks ledger consistency only.

Output lists are capped at 25 per list with `output_truncated: true` and `*_total` counts; use `--verbose` for everything.

## Without Python

Keep the same JSON by hand, set `verified` only with evidence in the documented fields, and say validation was not run.

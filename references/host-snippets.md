# Making the skill persistent per host

A skill file cannot reload itself. Reliable use needs one short block in the project's own instruction file. `scripts/install_snippet.py` writes it for you, idempotently, inside the project only.

```sh
python3 <skill-dir>/scripts/install_snippet.py --host cursor  --project . --dry-run
python3 <skill-dir>/scripts/install_snippet.py --host cursor  --project .
python3 <skill-dir>/scripts/install_snippet.py --host claude  --project .
python3 <skill-dir>/scripts/install_snippet.py --host codex   --project .
python3 <skill-dir>/scripts/install_snippet.py --host generic --project .   # prints the block; writes nothing
```

| Host | File written | Notes |
|---|---|---|
| `cursor` | `.cursor/rules/production-dev-team.mdc` | New file with `alwaysApply: true` front matter. An existing file keeps its own front matter. |
| `claude` | `CLAUDE.md` | Appends the block; other text is untouched. |
| `codex` | `AGENTS.md` | Appends the block; other text is untouched. |
| `generic` | none | Prints the block for any other host. |

- The block is one sentence that points at `SKILL.md`; it carries no rules of its own, so an existing session only needs to be told to reread that file. The block sits between `<!-- production-dev-team:start -->` and `<!-- production-dev-team:end -->`. Re-running replaces it in place; `--skill-path DIR` changes the path it points to; `--remove` deletes only the block (and a Cursor rule file that held nothing else).
- The block contains an absolute path to the skill folder, so teammates and CI need the skill at that path; re-run with `--skill-path` pointing at a location they share. A write prints that reminder on stderr.
- Text outside the markers is preserved exactly, including CRLF line endings. Markers are only recognized when each sits alone on its own line and outside a fenced code block, so a marker example in your own docs is never mistaken for the block.
- It never writes outside `--project`: a target or `.cursor` folder that is a symlink leading elsewhere is refused. New files follow your umask.
- `--remove` deletes only the block, and deletes a file only when it is a Cursor rule file that held nothing else. Adding then removing restores an ordinary file exactly; a file that had no final newline gains one.
- It refuses, and writes nothing, if the markers are damaged or duplicated, the target is not a regular file (a directory or pipe) or not UTF-8, the skill path has no `SKILL.md` or contains control characters.
- Exit code `2` means refused or could not run. `--dry-run` reports `would be created|updated|removed` or `unchanged` and prints the block it would write.
- The skill folder itself still has to be installed where the host can read it, or referenced by an absolute path. Installing the snippet does not prove the host loads the skill; start a new session and ask the agent which mode it would pick for a one-line typo fix and for a payment change.
- If the host does not discover the skill, the snippet still works because it names the file directly. A host can silently skip skills when its skills folder is very large: Codex CLI 0.154.0 logged `skills scan reached its traversal limit` for a folder of about 1,300 skills and loaded only about 350 of them, and this skill was not among those. Keep the skills folder small, or use the snippet.
- Verified here: the installer's behavior in temporary directories (`scripts/test_install_snippet.py`), and one real run (2026-10-16): with the block in a project's `AGENTS.md`, Codex CLI 0.154.0 read `SKILL.md` and applied it to a CSV-export request. Not verified: Claude Code, Cursor, other host versions, or whether a host picks the skill from its description alone. Test on your own host.

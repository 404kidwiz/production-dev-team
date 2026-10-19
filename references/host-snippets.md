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

## Optional guard hook (Claude Code)

The snippet relies on the agent remembering the rules. `scripts/guard_hook.py` is a deterministic backstop: a `PreToolUse` hook that pauses a shell command and asks you before it runs when the command

| Check | Pauses on | Passes |
|---|---|---|
| `delete` | `rm -r`/`-rf` or `find ... -delete` outside temp folders and build output (`node_modules`, `dist`, `build`, `.next`, `__pycache__`, `tmp` ...), a target it cannot resolve (`$DIR`, input from `xargs`), `dropdb`, and `DROP`/`TRUNCATE`/`DELETE FROM`/`FLUSHALL` sent through a database client or inline `python -c`/`node -e` code | `rm -rf node_modules`, `rm -rf /tmp/x`, plain `rm file`, SQL words in `grep` or `cat` |
| `deploy` | `git push` (not `--dry-run`), deploy and publish CLIs (Vercel, Netlify, Fly, Heroku, Railway, Wrangler, Firebase, gcloud, AWS, Azure, kubectl, Helm, Terraform, Pulumi, `docker push`, `npm publish` and other registries, `gh pr merge`, `gh release create` ...), and project scripts named like `deploy`, `release`, `publish` | `git status`, `vercel dev`, `terraform plan`, `kubectl get` |
| `migrate` | Prisma, Alembic, Django, Rails, Knex, Sequelize, TypeORM, Drizzle, Flyway, Liquibase, goose, golang-migrate, dbmate, Diesel, sqlx, Ecto, Laravel, Atlas, Supabase, D1 and EF Core migrations, and project scripts named like `migrate`, `seed`, `db:push`, `rollback` | `prisma generate`, `prisma migrate status`, `makemigrations` |
| `env` | a project script or test (`npm test`, `pytest`, `node app.js`, `go test`, `make` ...) while `.env`, `.env.local`, `.env.development` or `.env.test` sets a non-placeholder URL or key; also anything run through `doppler run`, `op run`, `dotenv` and other secret injectors. The prompt names the variables, never their values | the same command wrapped in `scripts/safe_run.py`, `npm install`, `prettier`, a `.env` with only placeholders |

It looks through `&&`, `;`, pipes, `$( )`, `bash -c`, `eval`, `sudo`, `env`, `npx`, `uv run`, `bundle exec` and similar wrappers, follows `cd` for path checks, and ignores heredoc bodies. Every other command gets no output and exit 0, so your normal permission rules still decide. It is stdlib Python and reads nothing but the `.env` files; on bad input or a crash it exits 1, which Claude Code treats as a non-blocking error, so a broken guard never blocks your work.

Install it per project (writes `.claude/settings.local.json`, the personal settings file Claude Code keeps out of commits):

```sh
python3 <skill-dir>/scripts/install_snippet.py --host claude --project . --hook --dry-run
python3 <skill-dir>/scripts/install_snippet.py --host claude --project . --hook
python3 <skill-dir>/scripts/install_snippet.py --host claude --project . --hook --remove
```

It adds one entry and keeps every other setting; a re-run replaces it in place (an older `python3 <path>` entry included), `--skill-path` changes the path it runs, `--remove` deletes only that entry. The installed command is `[ -f <path> ] || exit 0; exec python3 <path>`: if the script has gone (a plugin update deletes its old folder), the hook passes everything instead of failing, because Claude Code treats a `PreToolUse` exit 2, which is what `python3` returns for a missing file, as a block. That form needs a POSIX shell (macOS, Linux, Git Bash); where hooks run in `cmd` or PowerShell, use the plain `python3 <path>` form below and re-run the installer whenever the skill moves. Run the installer from a folder that stays put, such as a git clone; it warns when the skill path is inside the Claude Code plugin cache, where the hook would silently stop guarding after the next update. It refuses, writing nothing, if the file is not valid JSON, has an unexpected `hooks` shape, or already holds two guard entries. The JSON file is rewritten with two-space indentation. Start a new session to be sure the hook is loaded. To use it in every project, or to share it with a team, add the same entry by hand to `~/.claude/settings.json` or the committed `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "[ -f /path/to/production-dev-team/scripts/guard_hook.py ] || exit 0; exec python3 /path/to/production-dev-team/scripts/guard_hook.py", "timeout": 10 }]
      }
    ]
  }
}
```

- The default decision is `ask`: Claude Code shows the reason in the permission prompt and you approve or deny that one command. In `bypassPermissions` and `dontAsk` modes nobody answers a prompt, so the guard returns `deny` instead and the reason tells the agent to stop and ask you; you can then run the command yourself.
- `--deny` always denies (unattended runs, Codex). `--off delete|deploy|migrate|env` turns one check off, for example `--off env` in a project whose tests only ever use local services. Add flags to the end of the hook command.
- It is a backstop for honest mistakes, not a sandbox: a script that deletes or deploys internally, a command built at run time, or a non-Bash tool is not seen. Keep host permission rules and the skill's own gates.
- **Codex.** Codex reads the same `PreToolUse` JSON (tool name `Bash`, command in `tool_input.command`) from `hooks.json` or `[hooks]` in `config.toml`, but does not support `ask` yet: a hook that returns it is marked failed and the command runs. Use `--deny` there (`"command": "python3 /path/to/production-dev-team/scripts/guard_hook.py --deny"`). Repo-local hooks load only when the project's `.codex/` layer is trusted.
- **Cursor.** Cursor's `beforeShellExecution` hook (`.cursor/hooks.json`) sends `command` at the top level and expects a permission plus separate user and agent messages back. That is a different shape; the guard does not speak it yet.
- Verified here: the guard's decisions on 114 distinct commands and its JSON contract (`scripts/test_guard_hook.py`), and the hook installer in temporary directories, including running the installed command (`scripts/test_install_snippet.py`). Checked against the Claude Code, Codex and Cursor hook documentation on 2026-10-19. Not verified: a live Claude Code or Codex session with the hook installed.

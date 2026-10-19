#!/usr/bin/env python3
"""Optional PreToolUse guard for production-dev-team: pause shell commands that cannot be undone or that reach live services.

Reads one PreToolUse event (Claude Code or Codex JSON) on stdin. A Bash command that deletes data, pushes or deploys,
runs a migration, or runs a project script or test while a .env file sets a URL or key gets a decision back:
"ask" (the user approves or denies in the permission prompt) or, with --deny, "deny" (the agent is told to stop and
ask). Every other command passes silently (exit 0, no output), so the host's normal permission flow still applies.

  guard_hook.py [--deny] [--off delete|deploy|migrate|env ...]

In bypassPermissions and dontAsk modes nobody answers a prompt, so the guard denies instead of asking.
A command wrapped in scripts/safe_run.py skips the .env check (safe-run already redirects those services).
It never blocks because of its own failure: bad input or a crash exits 1, which hosts treat as a non-blocking error.
"""
import argparse
import json
import os
import re
import shlex
import sys
import tempfile

CHECKS = ('delete', 'deploy', 'migrate', 'env')
NO_PROMPT_MODES = {'bypassPermissions', 'dontAsk'}
HELP_ONLY = {'-h', '--help', 'help', '-v', '-V', '--version', 'version'}

# Folders that are rebuilt or regenerated; removing one inside the project loses no data.
REGENERABLE = {'node_modules', 'dist', 'build', 'out', '.next', '.nuxt', '.svelte-kit', '.turbo', '.cache', '.parcel-cache',
               'coverage', '.nyc_output', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.tox', '.nox',
               'target', 'tmp', 'temp', '.tmp'}

# Programs whose first positional argument names a deploy, publish or live-infrastructure step.
DEPLOY_FIRST = {
    'vercel': {'deploy', 'promote', 'rollback', 'redeploy', 'alias', 'remove', 'rm'},
    'netlify': {'deploy', 'sites:delete', 'env:set', 'env:unset'},
    'fly': {'deploy', 'launch', 'destroy', 'secrets', 'scale'},
    'flyctl': {'deploy', 'launch', 'destroy', 'secrets', 'scale'},
    'heroku': {'releases:rollback', 'apps:destroy', 'pg:reset', 'container:push', 'container:release', 'config:set',
               'config:unset'},
    'railway': {'up', 'deploy', 'redeploy', 'down'},
    'wrangler': {'deploy', 'publish', 'delete', 'secret', 'rollback', 'versions'},
    'firebase': {'deploy', 'hosting:channel:deploy', 'firestore:delete', 'database:remove', 'database:set', 'functions:delete'},
    'kubectl': {'apply', 'delete', 'rollout', 'scale', 'set', 'replace', 'patch', 'edit', 'create', 'drain'},
    'helm': {'install', 'upgrade', 'uninstall', 'delete', 'rollback'},
    'terraform': {'apply', 'destroy', 'import'},
    'tofu': {'apply', 'destroy', 'import'},
    'terragrunt': {'apply', 'destroy', 'run-all'},
    'pulumi': {'up', 'update', 'destroy', 'import'},
    'docker': {'push'},
    'podman': {'push'},
    'npm': {'publish', 'unpublish', 'deprecate'},
    'pnpm': {'publish'},
    'yarn': {'publish'},
    'bun': {'publish'},
    'cargo': {'publish', 'yank'},
    'twine': {'upload'},
    'gem': {'push', 'yank'},
    'poetry': {'publish'},
    'uv': {'publish'},
    'hatch': {'publish'},
    'flit': {'publish'},
    'mvn': {'deploy'},
    'gradle': {'publish'},
    'gradlew': {'publish'},
    'eas': {'submit', 'update'},
    'serverless': {'deploy', 'remove'},
    'sls': {'deploy', 'remove'},
    'sam': {'deploy', 'delete'},
    'cdk': {'deploy', 'destroy'},
    'amplify': {'publish', 'push', 'delete'},
    'kamal': {'deploy', 'redeploy', 'rollback', 'remove'},
}
# Nested CLIs: any positional argument that names a live step.
DEPLOY_ANY = {
    'gcloud': {'deploy', 'delete'},
    'az': {'deploy', 'delete'},
    'aws': {'deploy', 'rm', 'rb'},
    'cap': {'deploy'},
    'supabase': {'deploy', 'push'},
}
GH_LIVE = {('release', 'create'), ('release', 'delete'), ('release', 'edit'), ('pr', 'merge'), ('repo', 'delete'),
           ('repo', 'archive'), ('workflow', 'run'), ('secret', 'set'), ('secret', 'delete')}


def _db_task(p):
    return any(a.startswith('db:') and a not in ('db:version', 'db:migrate:status') for a in p)


# A rule is a set the first positional argument must be in, or a function of the positional list.
MIGRATE = {
    'prisma': lambda p: (p[:1] == ['migrate'] and len(p) > 1 and p[1] not in ('status', 'diff', 'resolve'))
    or p[:2] in (['db', 'push'], ['db', 'execute'], ['db', 'seed']),
    'alembic': {'upgrade', 'downgrade', 'stamp'},
    'django-admin': {'migrate', 'flush'},
    'rails': _db_task,
    'rake': _db_task,
    'sequelize': _db_task,
    'sequelize-cli': _db_task,
    'knex': lambda p: any(a.startswith(('migrate:', 'seed:run')) and a not in ('migrate:status', 'migrate:list', 'migrate:make')
                          for a in p),
    'typeorm': {'migration:run', 'migration:revert', 'schema:sync', 'schema:drop'},
    'mikro-orm': {'migration:up', 'migration:down', 'migration:fresh', 'schema:update', 'schema:drop', 'schema:fresh'},
    'drizzle-kit': {'push', 'migrate', 'drop'},
    'flyway': {'migrate', 'clean', 'undo', 'repair', 'baseline'},
    'liquibase': {'update', 'rollback', 'drop-all', 'dropAll'},
    'goose': lambda p: any(a in ('up', 'up-by-one', 'up-to', 'down', 'down-to', 'redo', 'reset') for a in p),
    'migrate': lambda p: any(a in ('up', 'down', 'drop', 'force', 'goto') for a in p),
    'dbmate': {'up', 'migrate', 'rollback', 'down', 'drop', 'load'},
    'diesel': lambda p: p[:2] in (['migration', 'run'], ['migration', 'revert'], ['migration', 'redo'],
                                  ['database', 'reset'], ['database', 'setup']),
    'sqlx': lambda p: p[:2] in (['migrate', 'run'], ['migrate', 'revert'], ['database', 'drop'], ['database', 'reset'],
                                ['database', 'setup']),
    'mix': lambda p: any(a.startswith('ecto.') and a not in ('ecto.gen.migration', 'ecto.migrations') for a in p),
    'atlas': lambda p: p[:2] in (['migrate', 'apply'], ['schema', 'apply']),
    'supabase': lambda p: p[:2] in (['db', 'reset'], ['migration', 'up'], ['migration', 'repair']),
    'wrangler': lambda p: 'migrations' in p and 'apply' in p,
    'dotnet': lambda p: p[:2] == ['ef', 'database'] and len(p) > 2 and p[2] in ('update', 'drop'),
    'yoyo': {'apply', 'rollback', 'reapply'},
}
# Project-script names (npm run X, make X) that usually mean a live step.
SCRIPT_WORD = re.compile(r'(?:^|[:_\-./ ])(migrat\w*|seed\w*|rollback|drop\w*|db[:_\-](?:push|reset|drop|wipe)'
                         r'|deploy\w*|release|publish|push)(?:$|[:_\-./ ])', re.I)

DESTRUCTIVE_SQL = re.compile(r'\b(drop\s+(?:table|database|schema|collection|view|index)|truncate\s+(?:table\s+)?\w+'
                             r'|delete\s+from|flushall|flushdb)\b|dropDatabase\s*\(|\.drop\s*\(\s*\)|deleteMany\s*\(', re.I)
DB_CLIENTS = {'psql', 'pgcli', 'mysql', 'mariadb', 'mycli', 'sqlite3', 'sqlite', 'litecli', 'mongosh', 'mongo', 'redis-cli',
              'cockroach', 'clickhouse', 'clickhouse-client', 'duckdb', 'sqlcmd', 'bq', 'snowsql', 'turso', 'usql',
              'supabase', 'wrangler', 'prisma', 'heroku', 'rails'}
INLINE_CODE = {'node': '-e', 'bun': '-e', 'deno': 'eval', 'ruby': '-e', 'perl': '-e', 'php': '-r'}

PACKAGE_MANAGERS = {'npm', 'pnpm', 'yarn', 'bun'}
PM_NOT_RUN = {'install', 'i', 'add', 'remove', 'rm', 'uninstall', 'un', 'ci', 'ls', 'list', 'outdated', 'audit', 'view', 'info',
              'init', 'create', 'why', 'config', 'login', 'logout', 'whoami', 'link', 'unlink', 'pack', 'cache', 'update',
              'upgrade', 'up', 'prune', 'dedupe', 'help', 'version', 'search', 'set', 'get', 'store', 'import', 'patch',
              'fund', 'doctor', 'pm', 'completion', 'publish', 'unpublish', 'deprecate', 'licenses', 'env'}
NPM_RUNS = {'test', 't', 'start', 'restart', 'stop', 'run', 'run-script'}
TEST_RUNNERS = {'pytest', 'py.test', 'tox', 'nox', 'behave', 'jest', 'vitest', 'mocha', 'ava', 'tap', 'uvu', 'karma',
                'playwright', 'cypress', 'rspec', 'phpunit', 'pest', 'tsx', 'ts-node', 'ts-node-dev', 'nodemon', 'make',
                'just', 'task'}
# Programs that run project code given these subcommands (None: given any file or argument).
SUBCOMMAND_RUNNERS = {'go': {'test', 'run'}, 'cargo': {'test', 'run'}, 'dotnet': {'test', 'run'}, 'mix': {'test', 'run'},
                      'rails': {'test', 'runner'}, 'deno': {'run', 'test', 'task', 'eval'}, 'coverage': {'run'},
                      'mvn': {'test', 'verify'}, 'gradle': {'test'}, 'gradlew': {'test'}, 'php': None, 'ruby': None,
                      'perl': None, 'node': None, 'sh': None, 'bash': None, 'zsh': None}
PYTHONS = re.compile(r'^python(\d+(\.\d+)?)?$')
NOT_RUN_MODULES = {'pip', 'venv', 'ensurepip', 'json.tool', 'compileall', 'py_compile', 'site'}
ENV_FILES = ('.env', '.env.local', '.env.development', '.env.development.local', '.env.test', '.env.test.local')
SECRET_INJECTORS = {'dotenv', 'dotenvx', 'doppler', 'op', 'infisical', 'envchain', 'aws-vault', 'chamber', 'sops'}
KEY_NAME = re.compile(r'(KEY|SECRET|TOKEN|PASSWORD|PASSWD|PWD|DSN|CREDENTIAL|AUTH)', re.I)
PLACEHOLDER = re.compile(r'^$|your|example|changeme|change_me|placeholder|xxx|dummy|fake|<', re.I)
ASSIGNMENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')

WRAPPERS = {'sudo', 'doas', 'env', 'time', 'nohup', 'nice', 'exec', 'command', 'builtin', 'xargs', 'timeout', 'stdbuf',
            'npx', 'bunx', 'pnpx'}
WRAPPER_VALUE_FLAGS = {'-u', '-g', '-C', '-n', '-I', '-P', '-L', '-d', '-s', '-p', '--package', '-k', '-o', '-e', '-i'}
RUN_WRAPPERS = {('uv', 'run'), ('poetry', 'run'), ('pipenv', 'run'), ('pdm', 'run'), ('hatch', 'run'), ('rye', 'run'),
                ('bundle', 'exec'), ('npm', 'exec'), ('npm', 'x'), ('pnpm', 'exec'), ('pnpm', 'dlx'), ('yarn', 'dlx'),
                ('yarn', 'exec'), ('bun', 'x')}
HEREDOC = re.compile(r'<<-?[ \t]*([\'"]?)(\w+)\1[^\n]*\n.*?\n[ \t]*\2[ \t]*(?=\n|$)', re.S)


def tokens(text):
    lex = shlex.shlex(text, posix=True, punctuation_chars='();<>|&\n')
    lex.whitespace = ' \t\r'
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:  # unbalanced quotes: fall back to a plain split rather than giving up on the command
        return text.replace('\n', ' ; ').split()


def segments(text):
    """Split a shell command into simple commands (argument lists), dropping redirections and heredoc bodies."""
    words = tokens(HEREDOC.sub(lambda m: m.group(0).split('\n', 1)[0], text))
    out, current, skip_next = [], [], False
    for i, tok in enumerate(words):
        if skip_next:
            skip_next = False
        elif tok and set(tok) <= set('();|&\n'):
            out.append(current)
            current = []
        elif tok and set(tok) <= set('<>&|') and ('<' in tok or '>' in tok):
            skip_next = True  # the redirection target is not an argument
            if current and current[-1].isdigit():
                current.pop()  # the "2" in 2>/dev/null
        elif not (tok == '$' and words[i + 1:i + 2] == ['(']):  # $( ... ): the "(" makes the inside its own command
            current.append(tok)
    out.append(current)
    return [seg for seg in out if seg]


def unwrap(argv):
    """Strip env assignments and wrappers (sudo, npx, uv run, ...).

    Returns (program, args, injector, safe_run): injector names a secret-injecting wrapper (doppler run, op run ...),
    safe_run is true when the command runs inside scripts/safe_run.py.
    """
    injector, safe_run = None, False
    while argv:
        name = os.path.basename(argv[0])
        rest = argv[1:]
        if ASSIGNMENT.match(argv[0]):
            argv = rest
        elif name in WRAPPERS:
            i = 0
            while i < len(rest) and (rest[i].startswith('-') or ASSIGNMENT.match(rest[i])
                                     or (name == 'timeout' and re.match(r'^\d', rest[i]))):
                i += 2 if rest[i] in WRAPPER_VALUE_FLAGS else 1
            argv = rest[i:]
        elif name in SECRET_INJECTORS:
            injector = name
            argv = argv[argv.index('--') + 1:] if '--' in argv else []
        elif name == 'safe_run.py' or (PYTHONS.match(name) and rest[:1] and os.path.basename(rest[0]) == 'safe_run.py'):
            safe_run = True
            argv = argv[argv.index('--') + 1:] if '--' in argv else []
        elif rest and (name, rest[0]) in RUN_WRAPPERS:
            i = 1
            while i < len(rest) and rest[i].startswith('-'):
                i += 1
            argv = rest[i:]
        else:
            return name, rest, injector, safe_run
    return None, [], injector, safe_run


def positionals(args):
    return [a for a in args if not a.startswith('-')]


def shown(argv, limit=60):
    text = ' '.join(argv)
    return text if len(text) <= limit else text[:limit - 1] + '…'


class Context:
    def __init__(self, cwd, project):
        self.cwd = os.path.realpath(cwd or os.getcwd())
        self.project = os.path.realpath(project or self.cwd)
        roots = {tempfile.gettempdir(), '/tmp', '/var/tmp', '/var/folders', '/private/tmp', '/private/var/folders',
                 os.environ.get('TMPDIR', '')}
        self.temp_roots = {os.path.realpath(r) for r in roots if r}
        self._env_names = None

    def resolve(self, path):
        return os.path.realpath(os.path.join(self.cwd, os.path.expanduser(path)))

    def safe_to_delete(self, target):
        if re.match(r'^\$\{?TMPDIR\}?(/|$)', target):
            return True
        if '$' in target or '`' in target:
            return False  # cannot tell where a variable points
        glob_at = min([target.index(c) for c in '*?[' if c in target], default=None)
        if glob_at is not None:
            prefix = target[:glob_at]
            target = prefix.rsplit('/', 1)[0] or '/' if '/' in prefix else '.'
        real = self.resolve(target)
        if any(real.startswith(root + os.sep) for root in self.temp_roots):
            return True
        inside = [base for base in (self.cwd, self.project) if real.startswith(base + os.sep)]
        return bool(inside) and any(part in REGENERABLE for part in os.path.relpath(real, inside[0]).split(os.sep))

    def env_names(self):
        """Names (never values) of .env variables that hold a URL or a key; read once per event."""
        if self._env_names is None:
            names = set()
            for folder in {self.cwd, self.project}:
                for filename in ENV_FILES:
                    try:
                        with open(os.path.join(folder, filename), encoding='utf-8', errors='replace') as handle:
                            lines = handle.read(65536).splitlines()
                    except OSError:
                        continue
                    for line in lines:
                        match = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$', line)
                        if not match:
                            continue
                        name, value = match.group(1), match.group(2).strip().strip('\'"')
                        if not PLACEHOLDER.search(value) and ('://' in value or KEY_NAME.search(name)):
                            names.add(name)
            self._env_names = sorted(names)
        return self._env_names


def check_delete(prog, args, ctx):
    if prog == 'rm':
        flags = [a for a in args if a.startswith('-') and a != '--']
        recursive = any(f in ('--recursive', '-r', '-R') or (not f.startswith('--') and set(f[1:]) & {'r', 'R'})
                        for f in flags)
        targets = args[args.index('--') + 1:] if '--' in args else positionals(args)
        if recursive:
            unsafe = [t for t in targets if not ctx.safe_to_delete(t)] if targets else ['(paths from input)']
            if unsafe:
                return f'deletes folders outside temp and build-output locations (rm -r {shown(unsafe, 50)})'
    if prog == 'find' and ('-delete' in args or any(a in ('-exec', '-execdir') and os.path.basename(args[i + 1]) == 'rm'
                                                    for i, a in enumerate(args[:-1]))):
        paths = []
        for a in args:
            if a.startswith(('-', '(', '!')):
                break
            paths.append(a)
        unsafe = [p for p in paths or ['.'] if not ctx.safe_to_delete(p)]
        if unsafe:
            return f'deletes files outside temp and build-output locations (find {shown(unsafe, 50)} ... -delete)'
    if prog == 'dropdb':
        return f'drops a database (dropdb {shown(positionals(args), 40)})'
    return None


def check_deploy(prog, args):
    pos = positionals(args)
    if prog == 'git':
        i = 0
        while i < len(args) and args[i].startswith('-'):
            i += 2 if args[i] in ('-C', '-c') else 1
        if args[i:i + 1] == ['push'] and not {'-n', '--dry-run'} & set(args):
            return f'pushes to a remote ({shown(["git"] + args[i:])})'
        return None
    if prog == 'gh' and tuple(pos[:2]) in GH_LIVE:
        return f'changes GitHub for everyone (gh {" ".join(pos[:2])})'
    live = (prog == 'vercel' and not pos) or (prog in DEPLOY_FIRST and bool(pos) and pos[0] in DEPLOY_FIRST[prog])
    if prog in DEPLOY_ANY:
        live = live or bool(set(pos) & DEPLOY_ANY[prog]) or (prog == 'supabase' and pos[:2] == ['secrets', 'set']) or (
            prog == 'aws' and any(p.startswith(('delete-', 'update-', 'terminate-', 'put-')) or (p == 'sync' and '--delete' in args)
                                  for p in pos))
    return f'deploys, publishes or changes live infrastructure ({shown([prog] + args)})' if live else None


def check_migrate(prog, args):
    pos = positionals(args)
    if PYTHONS.match(prog) and pos[:1] and os.path.basename(pos[0]) == 'manage.py' and pos[1:2] and pos[1] in ('migrate', 'flush'):
        return f'runs a database migration ({shown([prog] + args)})'
    if prog == 'php' and pos[:1] and os.path.basename(pos[0]) == 'artisan' and pos[1:2] and \
            pos[1].startswith(('migrate', 'db:wipe', 'db:seed')):
        return f'runs a database migration ({shown([prog] + args)})'
    rule = MIGRATE.get(prog)
    if rule is not None and (rule(pos) if callable(rule) else bool(pos) and pos[0] in rule):
        return f'runs a database migration or schema change ({shown([prog] + args)})'
    return None


def script_name(prog, args):
    """The project script or make target a package manager or task runner would run, if any."""
    pos = positionals(args)
    if prog in PACKAGE_MANAGERS and pos:
        if pos[0] in ('run', 'run-script') and len(pos) > 1:
            return pos[1]
        if prog != 'npm' and pos[0] not in PM_NOT_RUN:
            return pos[0]
    if prog in ('make', 'just', 'task') and pos:
        return ' '.join(pos)
    return None


def runs_project_code(prog, args):
    pos = positionals(args)
    if prog in PACKAGE_MANAGERS:
        return bool(pos) and (pos[0] in NPM_RUNS if prog == 'npm' else pos[0] not in PM_NOT_RUN)
    if prog in TEST_RUNNERS:
        return True
    if PYTHONS.match(prog):
        module = args[args.index('-m') + 1:args.index('-m') + 2] if '-m' in args else []
        return '-c' in args or (module[:1] and module[0] not in NOT_RUN_MODULES) or (not module and bool(pos))
    if prog in SUBCOMMAND_RUNNERS:
        subs = SUBCOMMAND_RUNNERS[prog]
        return bool(pos) and (subs is None or pos[0] in subs)
    return prog.endswith(('.sh', '.py', '.js', '.mjs', '.ts', '.rb'))


def reasons_for(command, ctx, off):
    reasons, pending, sql_capable = [], [command], False
    for _ in range(8):  # nested bash -c / eval, bounded
        if not pending:
            break
        for argv in segments(pending.pop()):
            head = os.path.basename(argv[0])
            if head in ('bash', 'sh', 'zsh', 'dash') and '-c' in argv[1:-1]:
                pending.append(argv[argv.index('-c') + 1])
                continue
            if head == 'eval' and len(argv) > 1:
                pending.append(' '.join(argv[1:]))
                continue
            prog, args, injector, safe_run = unwrap(argv)
            if prog == 'cd':
                ctx.cwd = ctx.resolve(args[0]) if args else os.path.expanduser('~')
                continue
            if prog is None or (args and set(args) <= HELP_ONLY):
                continue
            if prog in DB_CLIENTS or INLINE_CODE.get(prog) in args or (PYTHONS.match(prog) and '-c' in args):
                sql_capable = True
            found = [check_delete(prog, args, ctx) if 'delete' not in off else None,
                     check_deploy(prog, args) if 'deploy' not in off else None,
                     check_migrate(prog, args) if 'migrate' not in off else None]
            name = script_name(prog, args)
            match = SCRIPT_WORD.search(name) if name else None
            if match and not any(found):
                kind = 'deploy' if match.group(1).lower().startswith(('deploy', 'release', 'publish', 'push')) else 'migrate'
                if kind not in off:
                    found.append(f'runs the project script "{name}", which by its name deploys, publishes or changes a database')
            if 'env' not in off and not safe_run and not any(found):
                if injector:
                    found.append(f'injects secrets with {injector} into {shown([prog] + args, 40)}, so it can reach live services')
                elif runs_project_code(prog, args) and ctx.env_names():
                    names = ctx.env_names()
                    listed = ', '.join(names[:4]) + (f' and {len(names) - 4} more' if len(names) > 4 else '')
                    found.append(f'runs project code ({shown([prog] + args, 40)}) while .env sets {listed}; if those point at '
                                 'a live or paid service (even through a 127.0.0.1 tunnel), this run reaches it. '
                                 'scripts/safe_run.py runs it against a local recorder instead')
            reasons.extend(r for r in found if r and r not in reasons)
    if 'delete' not in off and sql_capable:
        sql = DESTRUCTIVE_SQL.search(command)
        if sql:
            reasons.append(f'runs destructive SQL or database commands ({sql.group(0).strip()})')
    return reasons


def decide(event, deny=False, off=()):
    """Return the hook output dict for a PreToolUse event, or None to stay silent."""
    if event.get('tool_name') != 'Bash':
        return None
    command = (event.get('tool_input') or {}).get('command')
    if not isinstance(command, str) or not command.strip():
        return None
    ctx = Context(event.get('cwd'), os.environ.get('CLAUDE_PROJECT_DIR') or event.get('cwd'))
    reasons = reasons_for(command, ctx, set(off))
    if not reasons:
        return None
    why = '; '.join(reasons)
    if deny or event.get('permission_mode') in NO_PROMPT_MODES:
        decision = 'deny'
        message = (f'production-dev-team guard blocked this command: it {why}. Do not retry it, reword it or reach the same '
                   'result another way. Stop and tell the user what the command would do and why; they can run it '
                   'themselves or turn the guard off for this step.')
    else:
        decision = 'ask'
        message = f'production-dev-team guard: this command {why}. Approve only if you want that to happen now.'
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': decision,
                                   'permissionDecisionReason': message}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--deny', action='store_true', help='deny instead of ask (Codex, unattended runs)')
    parser.add_argument('--off', action='append', default=[], choices=CHECKS, help='turn one check off (repeatable)')
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:  # argparse exits 2 on a bad flag, and exit 2 would block every command
        return 1 if stop.code else 0
    try:
        event = json.loads(sys.stdin.read() or '{}')
        result = decide(event if isinstance(event, dict) else {}, deny=args.deny, off=args.off)
    except Exception as error:  # never block a command because the guard itself broke
        print(f'guard_hook.py: skipped ({type(error).__name__}: {error})', file=sys.stderr)
        return 1
    if result:
        print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())

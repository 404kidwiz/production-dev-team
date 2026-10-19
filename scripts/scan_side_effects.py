#!/usr/bin/env python3
"""Danger map: list the commands in a project that can touch real things.

Read-only static scan. Finds every script, CLI command, package script and test that
can reach code which messages people, moves money or places orders, deletes or
bulk-overwrites records, or calls an outside service with a URL or key from `.env`
or config. For each it prints the command a user would run, the file:line hops down
to the call that does it, and a one-line risk.

It never runs or imports project code, never writes files and never prints a secret
value: only key names, file paths, line numbers and words from its own fixed
vocabulary are shown.

    python3 scan_side_effects.py [PROJECT_DIR] [--json]

Heuristic. An empty map does not prove a project is safe.
"""
import argparse
import ast
import json
import os
import re
import sys
import warnings
from pathlib import Path

SKIP_DIRS = {'node_modules', '__pycache__', 'venv', 'env', 'site-packages', 'dist', 'build', 'coverage',
             'vendor', 'target', 'out'}
MAX_BYTES = 1_000_000
JS_EXT = ('.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx', '.mts', '.cts')
CONFIG_KEY = re.compile(r'^[A-Z][A-Z0-9_]*(URL|URI|HOST|ENDPOINT|DSN|KEY|TOKEN|SECRET|PASSWORD|PASSWD|ACCOUNT|SID)$')
CONFIG_OBJECTS = {'settings', 'config', 'conf', 'cfg', 'env', 'environ', 'secrets'}
REMOTE_URL = re.compile(r'^https?://(?!localhost|127\.|0\.0\.0\.0|\[::1\]|testserver|example\.)[\w.-]+\.[a-z]{2,}', re.I)

# Outbound or destructive calls, matched on the dotted name the code calls after import aliases are resolved.
SINKS = [(re.compile(p), kind) for p, kind in [
    (r'^smtplib\.', 'mail'),
    (r'^(twilio|sendgrid|mailgun|postmark|slack_sdk|telegram|pywebpush|firebase_admin\.messaging|onesignal|'
     r'vonage|resend|mailchimp\w*)\b', 'message'),
    (r'^(stripe|braintree|paypalrestsdk|paypalcheckoutsdk|square|adyen|razorpay|mollie)\b', 'pay'),
    (r'^(boto3\.(client|resource)|google\.cloud\.|azure\.|supabase\.create_client|openai\.|anthropic\.)', 'cloud'),
    (r'(^|\.)urlopen$|^requests\.(get|post|put|patch|delete|head|request|Session)$'
     r'|^httpx\.(get|post|put|patch|delete|request|stream|Client|AsyncClient)$|^aiohttp\.(ClientSession|request)$'
     r'|^urllib3\.(PoolManager|request)$|^http\.client\.HTTPS?Connection$|^grpc\.(secure|insecure)_channel$', 'http'),
    (r'^(psycopg2?|psycopg|pymysql|MySQLdb|mysql\.connector|asyncpg)\.connect$|^pymongo\.MongoClient$'
     r'|^redis\.(Redis|from_url|StrictRedis)$|^sqlalchemy\.create_engine$', 'db'),
    (r'^shutil\.rmtree$', 'rmtree'),
]]
BULK_SQL = re.compile(r'\b(drop\s+(table|database|schema)|truncate\s+(table\s+)?\w+)\b|\bdelete\s+from\s+[\w."]+\s*;?\s*$',
                      re.I)
BULK_METHODS = {'delete_many', 'drop_all', 'drop_collection', 'drop_database', 'flushall', 'flushdb'}

VOCAB = {
    'notify': {'email', 'emails', 'emailed', 'mail', 'mailer', 'smtp', 'sms', 'invite', 'invites', 'invitation',
               'attendee', 'attendees', 'notify', 'notification', 'notifications', 'notice', 'slack', 'twilio',
               'sendgrid', 'mailgun', 'postmark', 'whatsapp', 'newsletter'},
    'money': {'charge', 'charges', 'payment', 'payments', 'payout', 'payouts', 'invoice', 'invoices', 'order',
              'orders', 'purchase', 'purchases', 'checkout', 'refund', 'refunds', 'subscription', 'subscriptions',
              'billing', 'stripe', 'paypal', 'braintree'},
    'destroy': {'delete', 'deletes', 'wipe', 'wipes', 'purge', 'drop', 'truncate', 'backfill', 'backfilled',
                'overwrite', 'bulk', 'destroy', 'erase', 'rmtree'},
}
NOT_MONEY = re.compile(r'in order (to|for)|order by|sort(ed|ing)? order|pay attention', re.I)
KIND_CATEGORIES = {'mail': 'notify', 'message': 'notify', 'pay': 'money', 'rmtree': 'destroy', 'bulk': 'destroy'}
ALWAYS_LISTED = {'mail', 'message', 'pay', 'cloud', 'rmtree', 'bulk'}
OUTBOUND = {'http', 'db', 'mail', 'message', 'pay', 'cloud'}
PHRASES = [('money', 'can move money or create orders/invoices'), ('notify', 'can message real people'),
           ('destroy', 'can delete or bulk-overwrite records')]
SHOWN = 30          # per group in the text view; --json lists everything
SEVERITY = {'money': 0, 'destroy': 1, 'notify': 2, 'external': 3}


def tag_facts(text):
    text = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', NOT_MONEY.sub(' ', text))
    found = set(re.findall(r'[a-z]+', text.lower()))
    return {('tag', cat, w) for cat, vocab in VOCAB.items() for w in found & vocab}


def walk(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.'))
        for name in sorted(filenames):
            path = Path(dirpath) / name
            try:
                if path.stat().st_size <= MAX_BYTES:
                    yield path
            except OSError:
                continue


def env_settings(root):
    """Key names from .env* files. A value is looked at only to say whether a flag is on; it is never returned."""
    names, flags_on = set(), set()
    for path in sorted(root.glob('.env*')):
        if path.is_file():
            for line in path.read_text(errors='replace').splitlines():
                match = re.match(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)', line)
                if match:
                    names.add(match.group(1))
                    if match.group(2).strip().strip('"\'').lower() in ('1', 'true', 'yes', 'on'):
                        flags_on.add(match.group(1))
    return names, flags_on


def is_test_file(rel):
    name = rel.name
    return name != 'conftest.py' and (name.startswith('test_') or name.endswith('_test.py')
                                      or re.search(r'\.(test|spec)\.[mc]?[jt]sx?$', name) is not None
                                      or 'tests' in rel.parts[:-1] or '__tests__' in rel.parts[:-1])


def propagate(nodes, edges, own):
    """summary(n) = own(n) plus the summary of everything n reaches (fixed point over cycles)."""
    callers = {}
    for n in nodes:
        for m in edges(n):
            callers.setdefault(m, set()).add(n)
    summary = {n: set(own(n)) for n in nodes}
    work = list(nodes)
    while work:
        n = work.pop()
        new = summary[n].union(*(summary[m] for m in edges(n) if m in summary))
        if new != summary[n]:
            summary[n] = new
            work.extend(callers.get(n, ()))
    return summary


def describe(facts, env_names):
    """(categories, one-line risk) for a set of facts, or None when nothing here is worth listing."""
    kinds = {f[1] for f in facts if f[0] == 'kind'}
    found = sorted({f[1] for f in facts if f[0] == 'key'})
    keys = [k for k in found if CONFIG_KEY.match(k)] or [k for k in found if k in env_names]
    remote = ('remote',) in facts
    if not (kinds & ALWAYS_LISTED or (kinds & OUTBOUND and (keys or remote))):
        return None
    matched = {}
    for f in facts:
        if f[0] == 'tag':
            matched.setdefault(f[1], set()).add(f[2])
    cats = {KIND_CATEGORIES[k] for k in kinds if k in KIND_CATEGORIES}
    if kinds & OUTBOUND:
        cats |= set(matched)
    parts = [f'{phrase} ({", ".join(sorted(matched[cat])[:4])})' if matched.get(cat) else phrase
             for cat, phrase in PHRASES if cat in cats]
    if kinds & OUTBOUND:
        cats.add('external')
        in_env = [k for k in keys if k in env_names]
        if in_env:
            parts.append('calls an outside service using ' + ', '.join(in_env[:4]) + ' from .env')
        elif keys:
            parts.append('calls an outside service using ' + ', '.join(keys[:4]) + ' from config or environment')
        else:
            parts.append('calls an outside service (URL in code)')
    return cats, '; '.join(parts), keys


# ---------------------------------------------------------------- Python

class Func:
    def __init__(self, module, name, node):
        self.module, self.name, self.node = module, name, node
        self.line = getattr(node, 'lineno', 1)
        self.refs = []        # (line, rel, name): project functions it calls or references
        self.sinks = []       # (line, kind)
        self.facts = set()    # ('kind', k) | ('tag', cat, word) | ('key', NAME) | ('remote',)
        self.gated = {}       # line -> config keys an enclosing `if` tests


class Module:
    def __init__(self, rel):
        self.rel = rel
        self.aliases = {}     # local name -> ('mod', rel) | ('func', rel, name) | ('ext', dotted)
        self.funcs = {}
        self.names = set()
        self.defs, self.imports = [], []
        self.tree = None
        self.doc = ''
        self.is_script = False


def dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return '.'.join([node.id, *reversed(parts)])
    return None


def is_main_guard(node):
    return isinstance(node, ast.If) and '__main__' in ast.dump(node.test) and '__name__' in ast.dump(node.test)


class PyProject:
    def __init__(self, root, files, env_names):
        self.root, self.env_names = root, env_names
        self.modules = {rel: Module(rel) for rel in files}
        for module in self.modules.values():
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore')
                    module.tree = ast.parse((root / module.rel).read_text(errors='replace'))
            except (SyntaxError, ValueError):
                continue
            module.doc = ast.get_docstring(module.tree) or ''
            for node in ast.walk(module.tree):      # one walk: defs and imports
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    module.defs.append(node)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    module.imports.append(node)
            module.names = {n.name for n in module.defs}
            module.is_script = any(is_main_guard(n) for n in module.tree.body)
            self.read_imports(module)
        for module in self.modules.values():
            if module.tree is not None:
                self.read_functions(module)
        self.all = [f for m in self.modules.values() for f in m.funcs.values()]
        self.hop = self.sink_paths()
        self.summary = propagate(self.all, lambda f: [t for _, t in self.callees(f)],
                                 lambda f: f.facts if f in self.hop else ())

    def find_module(self, name, base_dir):
        tail = Path(*name.split('.'))
        for start in (base_dir, *base_dir.parents, Path('src')):
            for candidate in (start / tail.with_suffix('.py'), start / tail / '__init__.py'):
                if candidate in self.modules:
                    return candidate
        return None

    def read_imports(self, module):
        here = module.rel.parent
        for node in module.imports:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found = self.find_module(alias.name, here)
                    local = alias.asname or alias.name.split('.')[0]
                    if found and (alias.asname or '.' not in alias.name):
                        module.aliases[local] = ('mod', found)
                    else:
                        module.aliases[local] = ('ext', alias.name if alias.asname else local)
            elif isinstance(node, ast.ImportFrom):
                source = node.module or ''
                base = here
                for _ in range(max(node.level - 1, 0)):
                    base = base.parent
                for alias in node.names:
                    local = alias.asname or alias.name
                    if node.level:
                        pkg = base / Path(*source.split('.')) if source else base
                        sub = next((c for c in (pkg / (alias.name + '.py'), pkg / alias.name / '__init__.py')
                                    if c in self.modules), None)
                        owner = next((c for c in (pkg.with_suffix('.py'), pkg / '__init__.py') if c in self.modules), None)
                    else:
                        sub = self.find_module(f'{source}.{alias.name}', here)
                        owner = self.find_module(source, here)
                    if sub:
                        module.aliases[local] = ('mod', sub)
                    elif owner:
                        module.aliases[local] = ('func', owner, alias.name)
                    else:
                        module.aliases[local] = ('ext', f'{source}.{alias.name}' if source else alias.name)

    def read_functions(self, module):
        for node in module.defs:
            func = module.funcs.setdefault(node.name, Func(module.rel, node.name, node))  # same-name methods merge
            func.facts |= tag_facts(node.name + ' ' + (ast.get_docstring(node) or ''))
            self.scan(module, func, list(ast.iter_child_nodes(node)), refs=True)
        top = module.funcs['<module>'] = Func(module.rel, '<module>', module.tree)
        body = [n for n in module.tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and not is_main_guard(n)]
        self.scan(module, top, body, refs=False)
        for node in module.tree.body:
            if is_main_guard(node):
                main = module.funcs['<main>'] = Func(module.rel, '<main>', node)
                main.facts |= tag_facts(module.doc)
                self.scan(module, main, list(ast.iter_child_nodes(node)), refs=True)

    def resolve(self, module, node):
        """('proj', rel, name) for a project function, ('ext', dotted) for anything else, or None."""
        name = dotted(node)
        if not name:
            return None
        head, _, rest = name.partition('.')
        alias = module.aliases.get(head)
        if head in ('self', 'cls') and rest and '.' not in rest:
            return ('proj', module.rel, rest)
        if alias is None:
            return ('proj', module.rel, head) if not rest and head in module.names else ('ext', name)
        if alias[0] == 'mod':
            return ('proj', alias[1], rest) if rest and '.' not in rest else ('ext', name)
        if alias[0] == 'func':
            return ('proj', alias[1], alias[2]) if not rest else ('ext', name)
        return ('ext', alias[1] + ('.' + rest if rest else ''))

    def scan(self, module, func, stack, refs):
        """Collect calls, references, sinks and facts, not descending into nested defs or classes."""
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            stack.extend(ast.iter_child_nodes(node))
            if isinstance(node, ast.If):
                keys = {c.value for c in ast.walk(node.test)
                        if isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value in self.env_names}
                if keys:
                    for child in node.body:
                        for line in range(child.lineno, (getattr(child, 'end_lineno', None) or child.lineno) + 1):
                            func.gated.setdefault(line, set()).update(keys)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if value in self.env_names:
                    func.facts.add(('key', value))
                if REMOTE_URL.match(value):
                    func.facts.add(('remote',))
                if len(value) < 200:
                    func.facts |= tag_facts(value)
                if BULK_SQL.search(value.strip()):
                    func.sinks.append((node.lineno, 'bulk'))
            elif isinstance(node, ast.keyword) and node.arg:
                func.facts |= tag_facts(node.arg)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                base = dotted(node.value)
                if base and base.split('.')[-1].lower() in CONFIG_OBJECTS and CONFIG_KEY.match(node.attr.upper()):
                    func.facts.add(('key', node.attr.upper()))
            elif isinstance(node, ast.Subscript):           # env["X"], os.environ["X"], settings["X"]
                key, base = node.slice, dotted(node.value)
                if isinstance(key, ast.Constant) and isinstance(key.value, str) and base \
                        and base.split('.')[-1].lower() in CONFIG_OBJECTS and CONFIG_KEY.match(key.value):
                    func.facts.add(('key', key.value))
            if isinstance(node, ast.Call):
                name = dotted(node.func) or ''
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) \
                        and CONFIG_KEY.match(node.args[0].value) and (name.endswith('getenv') or (
                            name.endswith('.get') and name.split('.')[-2].lower() in CONFIG_OBJECTS)):
                    func.facts.add(('key', node.args[0].value))   # os.getenv("X"), env.get("X")
                target = self.resolve(module, node.func)
                if target is None:
                    if isinstance(node.func, ast.Attribute) and node.func.attr in BULK_METHODS:
                        func.sinks.append((node.lineno, 'bulk'))
                    continue
                func.facts |= tag_facts(target[-1].rsplit('.', 1)[-1])
                if target[0] == 'proj':
                    func.refs.append((node.lineno, target[1], target[2]))
                    continue
                kind = 'bulk' if target[1].rsplit('.', 1)[-1] in BULK_METHODS else \
                    next((k for pattern, k in SINKS if pattern.search(target[1])), None)
                if kind:
                    func.sinks.append((node.lineno, kind))
            elif refs and isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load):
                target = self.resolve(module, node)     # callbacks: set_defaults(func=x), Thread(target=x)
                if target and target[0] == 'proj':
                    func.refs.append((node.lineno, target[1], target[2]))
        func.facts |= {('kind', kind) for _, kind in func.sinks}

    def func(self, rel, name):
        module = self.modules.get(rel)
        return module.funcs.get(name) if module else None

    def callees(self, func):
        for line, rel, name in func.refs:
            target = self.func(rel, name)
            if target is not None and target is not func:
                yield line, target
        if func.name == '<module>':     # importing a project module runs its top-level code
            for alias in self.modules[func.module].aliases.values():
                target = self.func(alias[1], '<module>') if alias[0] != 'ext' else None
                if target is not None:
                    yield 1, target

    def sink_paths(self):
        """Every function that reaches a sink -> (line, next function or None), shortest path, earliest line."""
        callers = {}
        for f in self.all:
            for line, target in self.callees(f):
                callers.setdefault(target, []).append((line, f))
        dist = {f: 0 for f in self.all if f.sinks}
        frontier = list(dist)
        while frontier:
            nxt = []
            for f in frontier:
                for _, caller in callers.get(f, ()):
                    if caller not in dist:
                        dist[caller] = dist[f] + 1
                        nxt.append(caller)
            frontier = nxt
        hop = {}
        for f, d in dist.items():
            if d == 0:
                hop[f] = (min(line for line, _ in f.sinks), None)
            else:
                hop[f] = min(((line, t) for line, t in self.callees(f) if dist.get(t) == d - 1), key=lambda p: p[0])
        return hop

    def chain(self, start):
        hops, f = [], start
        while f is not None and len(hops) < 10:
            line, nxt = self.hop[f]
            hops.append(f'{f.module.as_posix()}:{line}')
            f = nxt
        return hops

    def facts_from(self, roots, blocked=()):
        """Union of summaries; with `blocked`, walk the graph instead so those functions are not entered."""
        if not blocked:
            return set().union(*(self.summary[r] for r in roots))
        seen, stack, facts = set(), list(roots), set()
        while stack:
            f = stack.pop()
            if f in seen or f in blocked:
                continue
            seen.add(f)
            if f in self.hop:
                facts |= f.facts
            stack.extend(t for _, t in self.callees(f))
        return facts

    def reached(self, roots):
        seen, stack = set(), list(roots)
        while stack:
            f = stack.pop()
            if f not in seen:
                seen.add(f)
                stack.extend(t for _, t in self.callees(f))
        return seen


def argparse_commands(func):
    """({subcommand: [handler dotted name, [ARGS]]}, [top-level ARGS]) declared inside one function."""
    subs, positionals, var_sub = {}, [], {}
    calls = sorted((n for n in ast.walk(func.node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)),
                   key=lambda n: (n.lineno, n.col_offset))
    for node in ast.walk(func.node):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                and getattr(node.value.func, 'attr', None) == 'add_parser' and node.value.args \
                and isinstance(node.value.args[0], ast.Constant):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    var_sub.setdefault(t.id, []).append((node.lineno, node.value.args[0].value))
    for node in calls:
        method, owner = node.func.attr, node.func.value
        first = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
        handler = next((dotted(k.value) for k in node.keywords if k.arg == 'func'), None)
        if method == 'add_parser' and isinstance(first, str):
            subs.setdefault(first, [None, []])
        elif method == 'set_defaults' and isinstance(owner, ast.Call) and getattr(owner.func, 'attr', None) == 'add_parser' \
                and owner.args and isinstance(owner.args[0], ast.Constant):
            subs.setdefault(owner.args[0].value, [None, []])[0] = handler
        elif isinstance(owner, ast.Name):
            sub = ([s for line, s in var_sub.get(owner.id, []) if line <= node.lineno] or [None])[-1]
            if method == 'set_defaults' and sub in subs and handler:
                subs[sub][0] = handler
            elif method == 'add_argument' and isinstance(first, str) and not first.startswith('-'):
                nargs = next((k.value.value for k in node.keywords if k.arg == 'nargs' and isinstance(k.value, ast.Constant)), None)
                label = f'[{first.upper()}]' if nargs in ('?', '*') else first.upper()
                (subs[sub][1] if sub in subs else positionals).append(label)
    return subs, positionals


def click_commands(module):
    for func in module.funcs.values():
        for deco in getattr(func.node, 'decorator_list', []):
            call = deco.func if isinstance(deco, ast.Call) else deco
            if isinstance(call, ast.Attribute) and call.attr == 'command':
                named = isinstance(deco, ast.Call) and deco.args and isinstance(deco.args[0], ast.Constant)
                yield (deco.args[0].value if named else func.name.replace('_', '-')), func


def console_scripts(root):
    found = {}
    for name in ('pyproject.toml', 'setup.cfg', 'setup.py'):
        path = root / name
        if path.is_file():
            for m in re.finditer(r'^\s*["\']?([\w.-]+)["\']?\s*=\s*["\']?([A-Za-z_][\w.]*):([A-Za-z_]\w*)',
                                 path.read_text(errors='replace'), re.M):
                found[m.group(1)] = (m.group(2), m.group(3))
    return found


def python_findings(root, files, env_names, flags_on):
    py = PyProject(root, files, env_names)
    uses_pytest = any((root / n).is_file() for n in ('pytest.ini', 'conftest.py')) or any(
        (root / n).is_file() and 'pytest' in (root / n).read_text(errors='replace') for n in ('pyproject.toml', 'setup.cfg', 'tox.ini'))
    entries = []      # (command, kind, roots, blocked)
    for name, (mod, fname) in sorted(console_scripts(root).items()):
        rel = py.find_module(mod, Path('.'))
        func = py.func(rel, fname) if rel else None
        if func:
            entries.append((name, 'console script', [func, py.func(rel, '<module>')], ()))
    tests = {}
    for rel, module in sorted(py.modules.items()):
        if module.tree is None:
            continue
        top = module.funcs['<module>']
        if is_test_file(rel):
            pytest_file = uses_pytest or 'pytest' in module.aliases
            for node in module.tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test'):
                    tests.setdefault(rel, []).append((node.name, [module.funcs[node.name], top]))
                elif isinstance(node, ast.ClassDef):
                    methods = [m.name for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
                    setups = [module.funcs[m] for m in methods if m.lower().startswith(('setup', 'asyncsetup'))]
                    for m in methods:
                        if m.startswith('test'):
                            tests.setdefault(rel, []).append((f'{node.name}::{m}', [module.funcs[m], *setups, top]))
            if rel in tests:
                tests[rel] = (f'python -m pytest {rel.as_posix()}' if pytest_file else
                              f'python -m unittest discover -s {rel.parent.as_posix()} -p {rel.name}', tests[rel])
        if not module.is_script:
            continue
        run = f'python {rel.as_posix()}'
        handlers, positionals = [], []
        for func in [f for f in module.funcs.values() if f.name != '<module>']:
            subs, args = argparse_commands(func)
            positionals += args
            for sub, (handler, sub_args) in sorted(subs.items()):
                target = py.resolve(module, ast.parse(handler, mode='eval').body) if handler else None
                hfunc = py.func(target[1], target[2]) if target and target[0] == 'proj' else None
                if hfunc:
                    handlers.append(hfunc)
                    entries.append((' '.join([run, sub, *sub_args]), 'command', [hfunc, top], ()))
        for name, func in click_commands(module):
            handlers.append(func)
            entries.append((f'{run} {name}', 'command', [func, top], ()))
        main = module.funcs['<main>']
        if handlers:
            entries.append((f'{run} (any subcommand, before it runs)', 'command', [main, top], set(handlers)))
            stray = [f for f in module.funcs.values() if f in py.hop and f.name not in ('<module>', '<main>')
                     and f not in py.reached([main, *handlers])]
            if stray:
                entries.append((f'{run} … (subcommand not traced; reaches {", ".join(f.name for f in stray[:3])})',
                                'command', stray, ()))
        else:
            entries.append((' '.join([run, *positionals]) + ('  (no arguments needed)' if not positionals else ''),
                            'script', [main, top], ()))

    findings = []
    for command, kind, roots, blocked in entries:
        facts = py.facts_from(roots, blocked) | (tag_facts(py.modules[roots[0].module].doc))
        described = describe(facts, env_names) if any(r in py.hop for r in roots) else None
        if described:
            start = next(r for r in roots if r in py.hop and r not in blocked)
            findings.append(finding(py, command, kind, start, roots[0], described, flags_on))
    for rel, (command, cases) in tests.items():
        hits = [(name, roots) for name, roots in cases if any(r in py.hop for r in roots)]
        facts = set().union(*(py.facts_from(roots) for _, roots in hits)) if hits else set()
        described = describe(facts, env_names) if hits else None
        if described:
            name, roots = hits[0]
            start = next(r for r in roots if r in py.hop)
            item = finding(py, command, 'test', start, roots[0], described, flags_on)
            text = (root / rel).read_text(errors='replace')
            mocked = re.search(r'\bmock\b|monkeypatch|responses\b|httpretty|vcr|respx|requests_mock', text)
            item['note'] = (f'{len(hits)} of {len(cases)} tests reach it (first: {name})'
                            + ('; the file uses mocks or fakes: check they cover this call' if mocked else ''))
            findings.append(item)
    return findings, len(py.modules)


def finding(py, command, kind, start, entry, described, flags_on):
    cats, risk, keys = described
    note = None
    calls = [line for line, t in py.callees(entry) if t in py.hop] + [line for line, _ in entry.sinks]
    if calls and all(line in entry.gated for line in calls):
        gate = sorted(set.intersection(*(entry.gated[line] for line in calls)))
        if gate:
            state = ', '.join(f'{k} is {"on" if k in flags_on else "set but not on"} in .env' for k in gate)
            note = f'only when {" and ".join(gate)} is on ({state})'
    return {'command': command, 'kind': kind, 'chain': py.chain(start), 'categories': sorted(cats),
            'risk': risk, 'config_keys': keys, 'note': note}


# ---------------------------------------------------------------- JS / TS (file level, line patterns)

JS_SINKS = [(re.compile(p, re.I), kind) for p, kind in [
    (r'nodemailer|\.sendMail\s*\(|from\s+[\'"]twilio|require\([\'"]twilio|@sendgrid/|from\s+[\'"]resend|postmark|mailgun', 'message'),
    (r'new\s+Stripe\s*\(|\bstripe\.\w+\.(create|update|del)\b|from\s+[\'"]stripe[\'"]|braintree|paypal', 'pay'),
    (r'\.deleteMany\s*\(|\bdrop\s+table\b|\btruncate\s+table\b', 'bulk'),
    (r'new\s+(OpenAI|Anthropic)\s*\(|@aws-sdk/|\bS3Client\b|createClient\s*\(\s*process\.env', 'cloud'),
    (r'\bfetch\s*\(|\baxios(\.\w+)?\s*\(|\bgot(\.\w+)?\s*\(|\bhttps?\.request\s*\(|\bnew\s+(Pool|Client)\s*\(', 'http'),
]]
JS_ENV = re.compile(r'process\.env\.([A-Z][A-Z0-9_]*)|process\.env\[\s*[\'"]([A-Z][A-Z0-9_]*)'
                    r'|import\.meta\.env\.([A-Z][A-Z0-9_]*)|Deno\.env\.get\(\s*[\'"]([A-Z][A-Z0-9_]*)')
JS_IMPORT = re.compile(r'(?:from\s+|require\s*\(\s*|import\s*\(\s*|import\s+)[\'"](\.{1,2}/[^\'"]+)[\'"]')
JS_URL = re.compile(r'[\'"`](https?://[^\'"`\s]+)')


def js_findings(root, files, env_names):
    files = {rel.as_posix(): rel for rel in files}
    info = {}
    for key, rel in files.items():
        text = (root / rel).read_text(errors='replace')
        lines = text.splitlines()
        facts, first = set(), None
        for i, line in enumerate(lines, 1):
            if line.lstrip().startswith(('//', '*', '/*')):
                continue
            kind = next((k for pattern, k in JS_SINKS if pattern.search(line)), None)
            if kind:
                first = first or i
                facts.add(('kind', kind))
                facts |= tag_facts(' '.join(lines[max(i - 4, 0):i + 3]))
        if first:
            facts |= {('key', k) for groups in JS_ENV.findall(text) for k in groups if k}
            if any(REMOTE_URL.match(u) for u in JS_URL.findall(text)):
                facts.add(('remote',))
        deps = []
        for spec in set(JS_IMPORT.findall(text)):
            base = os.path.normpath(os.path.join(os.path.dirname(key), spec))
            stem = base[:-3] if base.endswith('.js') else base     # TS sources imported with a .js specifier
            for cand in (base, *(stem + e for e in JS_EXT), *(base + '/index' + e for e in JS_EXT)):
                if cand in files and cand != key:
                    deps.append(cand)
                    break
        info[key] = (facts, first, sorted(deps), text)

    def reach(key):
        """Files with a sink within two import hops (file-level imports over-approximate, so stay shallow)."""
        paths, frontier = {key: [key]}, [key]
        for _ in range(2):
            nxt = []
            for f in frontier:
                for d in info[f][2]:
                    if d not in paths:
                        paths[d] = paths[f] + [d]
                        nxt.append(d)
            frontier = nxt
        return [(k, path) for k, path in paths.items() if info[k][1]]

    package = root / 'package.json'
    try:
        scripts = (json.loads(package.read_text()).get('scripts') or {}) if package.is_file() else {}
    except (ValueError, AttributeError):
        scripts = {}
    entries, used = [], set()
    for name, cmd in sorted(scripts.items()):
        for token in set(re.split(r'[\s;&|()]+', str(cmd))):
            key = os.path.normpath(token) if token else ''
            if key in files:
                entries.append((f'npm run {name}', 'package script', key))
                used.add(key)
    for key in sorted(set(files) - used):
        rel = files[key]
        if is_test_file(rel):
            entries.append(((f'npm test -- {key}' if 'test' in scripts else f'(your test runner) {key}'), 'test', key))
        elif rel.parts[0] in ('scripts', 'bin', 'tools') or info[key][3].startswith('#!'):
            entries.append((f'{"node" if rel.suffix in (".js", ".mjs", ".cjs") else "npx tsx"} {key}', 'script', key))

    findings = []
    for command, kind, key in entries:
        hits = reach(key)
        described = describe(set().union(*(info[k][0] for k, _ in hits)), env_names) if hits else None
        if not described:
            continue
        cats, risk, keys = described
        path = min((p for _, p in hits), key=len)
        mocked = kind == 'test' and re.search(r'\b(vi|jest)\.(mock|spyOn)\(|\bmsw\b|\bnock\b|sinon', info[key][3])
        findings.append({'command': command, 'kind': kind, 'chain': path[:-1] + [f'{path[-1]}:{info[path[-1]][1]}'],
                         'categories': sorted(cats), 'risk': risk, 'config_keys': keys,
                         'note': 'the file uses mocks or fakes: check they cover this call' if mocked else None})
    return findings


# ---------------------------------------------------------------- output

def scan(root):
    root = Path(root).resolve()
    env_names, flags_on = env_settings(root)
    py_files, js_files = [], []
    for path in walk(root):
        rel = path.relative_to(root)
        if path.suffix == '.py':
            py_files.append(rel)
        elif path.suffix in JS_EXT and not path.name.endswith('.d.ts'):
            js_files.append(rel)
    findings, _ = python_findings(root, py_files, env_names, flags_on)
    findings += js_findings(root, js_files, env_names) if js_files else []
    findings.sort(key=lambda f: (f['kind'] == 'test', min(SEVERITY[c] for c in f['categories']), f['command']))
    return {'project': str(root), 'python_files': len(py_files), 'js_files': len(js_files), 'findings': findings}


def render(result):
    findings = result['findings']
    out = [f'Danger map for {Path(result["project"]).name}: {len(findings)} command(s) can touch real things',
           f'(read-only scan of {result["python_files"]} Python and {result["js_files"]} JS/TS files; '
           'secret values are never shown)']
    for title, group in (('Scripts and commands', [f for f in findings if f['kind'] != 'test']),
                         ('Tests', [f for f in findings if f['kind'] == 'test'])):
        if group:
            out += ['', title]
        for n, f in enumerate(group[:SHOWN], 1):
            out.append(f'{n}. {f["command"]}')
            out.append(f'   where: {" -> ".join(f["chain"])}')
            out.append(f'   risk:  {f["risk"]}')
            if f['note']:
                out.append(f'   note:  {f["note"]}')
        if len(group) > SHOWN:
            out.append(f'... and {len(group) - SHOWN} more {title.lower()} (run with --json for the full list)')
    out += ['', 'Before running any of these, confirm where each URL or key leads; treat an unknown target as live.'
            if findings else 'Nothing found.',
            'Static heuristic: dynamic calls, shell scripts and other languages are not followed, '
            'so a missing item is not proof it is safe.']
    return '\n'.join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description='List the commands in a project that can touch real things (read-only).')
    parser.add_argument('project', nargs='?', default='.', help='project directory (default: current directory)')
    parser.add_argument('--json', action='store_true', help='machine-readable output')
    args = parser.parse_args(argv)
    if not Path(args.project).is_dir():
        print(f'not a directory: {args.project}', file=sys.stderr)
        return 2
    result = scan(args.project)
    print(json.dumps(result, indent=2) if args.json else render(result))
    return 0


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    sys.exit(main())

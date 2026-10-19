#!/usr/bin/env python3
"""Run a project's own command against a local recorder instead of its live services.

    python3 safe_run.py [--project DIR] [--config FILE ...] [--keep] -- COMMAND [ARGS ...]

What it does:
  1. Copies the project into a temporary folder inside the project (`.safe-run-*`), or under
     $SAFE_RUN_DIR if set. Real data files are never touched; the copy is deleted afterwards
     unless --keep. Dependency folders (node_modules, .venv, venv) are linked, not copied.
  2. In every `.env*` file of the copy and in the command's environment:
     - `*_URL` / `*_URI` variables and any `scheme://` value: http(s)/ws(s) go to a recorder
       (one local port per variable, path kept); other network schemes (postgres, redis, ...)
       go to a closed port; sqlite/file values stay, with absolute project paths moved to the copy.
     - key, secret, token and password variables are replaced with a placeholder.
     - HTTP(S)_PROXY points at a recorder that logs and refuses anything not covered above.
  3. Runs COMMAND in the copy, then prints a report to stderr: what was redirected and every call
     the recorder answered (method, path, body excerpt). Secret values are never printed.

Exit code is the command's. Not a network sandbox: code that ignores proxy settings and has a
hard-coded host, or opens raw sockets, can still reach the network (unauthenticated, since secrets
are placeholders). Stdlib only.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

PLACEHOLDER = 'safe-run-placeholder'
CLOSED = '127.0.0.1:9'  # discard port: nothing listens, connections are refused
SKIP_DIRS = {'.git', '__pycache__', '.pytest_cache', '.mypy_cache', '.tox'}
LINK_DIRS = {'node_modules', '.venv', 'venv'}
SECRET_WORDS = {'key', 'apikey', 'secret', 'token', 'password', 'passwd', 'credential', 'credentials'}
RECORDED_SCHEMES = {'http', 'https', 'ws', 'wss'}
LOCAL_SCHEMES = {'file', 'sqlite', 'sqlite3'}
DOTENV_LINE = re.compile(r'^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*?)(\s*)$')
ASSIGNMENT = re.compile(r'''(["']?)([A-Za-z_][\w.-]*)\1(\s*[:=]\s*)(["']?)([^"'\s,&}#]+)\4''')
URL_IN_TEXT = re.compile(r'''\b(?:https?|wss?)://[^\s"'<>]+''')
BODY_LIMIT = 300


def is_secret_name(name):
    words = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+', name)
    return any(w.lower() in SECRET_WORDS for w in words)


def is_redirect_name(name):
    return name.upper().endswith(('_URL', '_URI'))


def describe(value):
    """Scheme and host only: never userinfo, path or query, which can carry credentials."""
    if not value:
        return '(empty)'
    parts = urlsplit(value)
    try:
        port = f':{parts.port}' if parts.port else ''
    except ValueError:
        port = ''
    return f'{parts.scheme}://{parts.hostname or ""}{port}' if parts.scheme else '(not a URL)'


class Recorder:
    """Local HTTP listeners that answer every request with plausible JSON and log it."""

    def __init__(self):
        self.calls, self.services, self.lock = [], {}, threading.Lock()
        self.servers = []

    def listen(self, label):
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def body(self):
                if self.headers.get('Transfer-Encoding', '').lower() == 'chunked':
                    data = b''
                    while True:
                        size = int(self.rfile.readline().split(b';')[0].strip() or b'0', 16)
                        if not size:
                            self.rfile.readline()
                            return data
                        data += self.rfile.read(size)
                        self.rfile.readline()
                n = int(self.headers.get('Content-Length') or 0)
                return self.rfile.read(n) if n else b''

            def answer(self):
                raw = self.body()
                with recorder.lock:
                    recorder.calls.append({'service': label, 'method': self.command, 'path': self.path,
                                           'body': raw.decode(errors='replace'), 'blocked': False})
                    n = len(recorder.calls)
                out = json.dumps({'id': f'safe_run_{n:05d}', 'object': 'safe_run_stub', 'status': 'ok', 'ok': True,
                                  'data': [], 'items': []}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(out)))
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(out)

            def do_CONNECT(self):  # an HTTPS call through the proxy: record the host, refuse the tunnel
                with recorder.lock:
                    recorder.calls.append({'service': label, 'method': 'CONNECT', 'path': self.path, 'body': '',
                                           'blocked': True})
                self.send_error(403, 'blocked by safe_run')

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = answer

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        return server.server_address[1]

    def url_for(self, label, original):
        """Same path and query, recorder origin. One port per label so each call is attributable."""
        if label not in self.services:
            self.services[label] = {'port': self.listen(label), 'targets': set(), 'sources': set()}
        parts = urlsplit(original)
        scheme = 'ws' if parts.scheme in ('ws', 'wss') else 'http'
        tail = parts.path + (f'?{parts.query}' if parts.query else '')
        return f'{scheme}://127.0.0.1:{self.services[label]["port"]}{tail}'

    def close(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()


class Rewriter:
    """Decides the new value for one variable and remembers what it did for the report."""

    def __init__(self, recorder, projects, copy):
        self.recorder, self.copy = recorder, copy
        self.projects = sorted(set(projects), key=len, reverse=True)  # as given and with symlinks resolved
        self.blocked, self.masked, self.secrets = {}, set(), set()

    def value(self, name, value, source):
        scheme = urlsplit(value).scheme.lower() if '://' in value else ''
        if is_redirect_name(name) or scheme:
            if scheme in LOCAL_SCHEMES:
                return self.local(value)
            if not scheme or scheme in RECORDED_SCHEMES:
                new = self.recorder.url_for(name, value or 'http://unset')
                self.recorder.services[name]['targets'].add(describe(value))
                self.recorder.services[name]['sources'].add(source)
                return new
            self.blocked.setdefault(name, set()).add(f'{scheme} ({source})')
            return f'{scheme}://{CLOSED}/'
        if is_secret_name(name):
            if len(value) >= 4:
                self.secrets.add(value)
            self.masked.add(name)
            return PLACEHOLDER
        return self.local(value)

    def local(self, value):
        for project in self.projects:
            value = value.replace(project, self.copy)
        return value

    def dotenv(self, path, source):
        lines = []
        for line in path.read_text(errors='replace').splitlines(keepends=True):
            end = '\n' if line.endswith('\n') else ''
            match = DOTENV_LINE.match(line.rstrip('\n'))
            if not match or line.lstrip().startswith('#'):
                lines.append(line)
                continue
            lead, name, eq, raw, trail = match.groups()
            quote = raw[0] if raw[:1] in ('"', "'") and raw.endswith(raw[0]) and len(raw) > 1 else ''
            value = raw[1:-1] if quote else raw.split(' #')[0].strip()
            lines.append(f'{lead}{name}{eq}{quote}{self.value(name, value, source)}{quote}{trail}{end}')
        path.write_text(''.join(lines))

    def config(self, path, source):
        """Any http(s)/ws(s) URL goes to the recorder (one port per origin); secret-named values are masked."""
        def url(match):
            origin = describe(match.group(0))
            label = f'{source} {origin}'
            new = self.recorder.url_for(label, match.group(0))
            self.recorder.services[label]['targets'].add(origin)
            self.recorder.services[label]['sources'].add(source)
            return new

        def secret(match):
            quote, name, sep, vquote, value = match.groups()
            if not is_secret_name(name) or '://' in value:
                return match.group(0)
            self.secrets.add(value)
            self.masked.add(f'{name} ({source})')
            return f'{quote}{name}{quote}{sep}{vquote}{PLACEHOLDER}{vquote}'

        text = path.read_text(errors='replace')
        path.write_text(URL_IN_TEXT.sub(url, ASSIGNMENT.sub(secret, text)))


def copy_project(src, dst):
    """Copy regular files; link dependency folders and existing symlinks; skip caches and earlier runs."""
    for root, dirs, files in os.walk(src):
        out = os.path.join(dst, os.path.relpath(root, src))
        os.makedirs(out, exist_ok=True)
        for name in list(dirs):
            path = os.path.join(root, name)
            if name in SKIP_DIRS or name.startswith('.safe-run-'):
                dirs.remove(name)
            elif os.path.islink(path):
                os.symlink(os.readlink(path), os.path.join(out, name))
                dirs.remove(name)
            elif name in LINK_DIRS:
                os.symlink(path, os.path.join(out, name))
                dirs.remove(name)
        for name in files:
            path = os.path.join(root, name)
            if name.endswith('.pyc') or name == '.DS_Store':
                continue
            if os.path.islink(path):
                os.symlink(os.readlink(path), os.path.join(out, name))
            elif os.path.isfile(path):  # skip sockets and fifos
                shutil.copy2(path, os.path.join(out, name))


def redact(text, secrets):
    for value in sorted(secrets, key=len, reverse=True):
        text = text.replace(value, '[secret]')
    text = re.sub(r'(?i)\bbearer\s+[\w.~+/=-]+', 'Bearer [secret]', text)
    return ASSIGNMENT.sub(lambda m: m.group(0) if not is_secret_name(m.group(2)) else
                          f'{m.group(1)}{m.group(2)}{m.group(1)}{m.group(3)}{m.group(4)}[secret]{m.group(4)}', text)


def report(command, workdir, returncode, recorder, rewriter, kept):
    secrets = rewriter.secrets
    out = [f'safe-run: `{" ".join(command)}` exited {returncode} in a copy of the project'
           + (f' (kept at {workdir})' if kept else ' (copy deleted)')]
    if recorder.services or rewriter.blocked:
        out.append('Redirected (these never reached the original target):')
        for label, svc in sorted(recorder.services.items()):
            if label == 'proxy':
                continue
            targets = ', '.join(sorted(svc['targets']))
            out.append(f'  {label} [{", ".join(sorted(svc["sources"]))}]: {targets} -> recorder 127.0.0.1:{svc["port"]}')
        for name, how in sorted(rewriter.blocked.items()):
            out.append(f'  {name} [{", ".join(sorted(how))}] -> closed port, connections fail')
    else:
        out.append('Redirected: nothing (no *_URL, *_URI or URL-valued settings found).')
    if rewriter.masked:
        out.append(f'Secrets replaced with "{PLACEHOLDER}" (values not shown): {", ".join(sorted(rewriter.masked))}')
    calls = recorder.calls
    out.append(f'Intercepted calls: {len(calls)}' + ('' if calls else ' (the command made no HTTP call to a redirected target)'))
    for i, call in enumerate(calls, 1):
        body = ' '.join(redact(call['body'], secrets).split())
        body = body if len(body) <= BODY_LIMIT else body[:BODY_LIMIT] + '...'
        state = 'refused' if call['blocked'] else 'answered 200'
        line = f'  {i}. {call["service"]}: {call["method"]} {redact(call["path"], secrets)} ({state})'
        out.append(line + (f'\n     body: {body}' if body else ''))
    if any(c['service'] == 'proxy' for c in calls):
        out.append('Calls under "proxy" went to hosts not configured in .env or --config: the code has a hard-coded '
                   'or default service address. Find it before trusting a live run.')
    out.append('Not covered: code that ignores HTTP(S)_PROXY with a hard-coded host, raw sockets, and services reached '
               'without a URL setting. Secrets were placeholders, so any such call was unauthenticated.')
    return '\n'.join(out)


def run(project, command, configs=(), keep=False):
    given, project = os.path.abspath(project), os.path.realpath(project)
    parent = os.environ.get('SAFE_RUN_DIR') or project
    os.makedirs(parent, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix='.safe-run-', dir=parent)
    recorder = Recorder()
    rewriter = Rewriter(recorder, (given, project), workdir)
    try:
        copy_project(project, workdir)
        for root, dirs, files in os.walk(workdir):  # links to dependency folders are not followed
            for name in files:
                path = os.path.join(root, name)
                if (name == '.env' or name.startswith('.env.')) and not os.path.islink(path):
                    rewriter.dotenv(Path(path), os.path.relpath(path, workdir))
        for rel in configs:
            path = (Path(workdir) / rel).resolve()
            if not path.is_file() or Path(workdir).resolve() not in path.parents:  # never edit a real file
                raise SystemExit(f'safe-run: --config {rel} is not a file inside the project')
            rewriter.config(path, rel)
        env = {name: rewriter.value(name, value, 'environment') for name, value in os.environ.items()
               if name.upper() not in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY')}
        proxy = recorder.url_for('proxy', 'http://proxy')
        for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
            env[name] = env[name.lower()] = proxy
        env['NO_PROXY'] = env['no_proxy'] = '127.0.0.1,localhost'
        env['PWD'] = workdir
        sys.stdout.flush()
        try:
            returncode = subprocess.run(command, cwd=workdir, env=env).returncode
        except FileNotFoundError:
            returncode = 127
            print(f'safe-run: command not found: {command[0]}', file=sys.stderr)
        print(report(command, workdir, returncode, recorder, rewriter, keep), file=sys.stderr)
        return returncode
    finally:
        recorder.close()
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--project', default='.', help='project folder to copy (default: current folder)')
    parser.add_argument('--config', action='append', default=[], metavar='FILE',
                        help='extra config file (relative to the project) whose URLs and secret values are rewritten')
    parser.add_argument('--keep', action='store_true', help='keep the temporary copy for inspection')
    parser.add_argument('command', nargs=argparse.REMAINDER, help='command to run, after --')
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('give a command to run after --')
    return run(args.project, command, args.config, args.keep)


if __name__ == '__main__':
    sys.exit(main())

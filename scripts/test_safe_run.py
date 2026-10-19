"""safe_run.py: the command runs against a local recorder, real files stay untouched, secrets never print."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import safe_run

SCRIPT = Path(safe_run.__file__).resolve()
REAL_KEY = 'sk_live_REALSECRET_12345'

# A tiny app shaped like the trial tasks: reads .env, charges a card, appends to its real data file.
APP = r'''
import json, os, urllib.request
env = {k: v.strip('"') for k, v in (l.split('=', 1) for l in open('.env').read().splitlines() if '=' in l and not l.startswith('#'))}
req = urllib.request.Request(env['STRIPE_API_URL'] + '/charges', method='POST',
                             data=json.dumps({'amount': 500, 'api_key': env['STRIPE_API_KEY']}).encode(),
                             headers={'Authorization': 'Bearer ' + env['STRIPE_API_KEY'], 'Content-Type': 'application/json'})
print('response', json.load(urllib.request.urlopen(req, timeout=5))['id'])
print('key seen by app', env['STRIPE_API_KEY'])
print('inherited', os.environ.get('OPENAI_BASE_URL'), os.environ.get('OPENAI_API_KEY'))
try:
    urllib.request.urlopen('https://hardcoded.invalid/v1/send', timeout=5)
except Exception as exc:
    print('hardcoded call failed', type(exc).__name__)
with open('data.json', 'a') as f:
    f.write('test row\n')
'''


class SafeRunEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / 'shop'
        self.project.mkdir()
        self.env_text = (f'# live settings\nSTRIPE_API_URL=https://api.stripe.example/v1\nSTRIPE_API_KEY="{REAL_KEY}"\n'
                         'DATABASE_URL=postgres://admin:dbpass99@db.example:5432/shop\nexport SHOP_NAME=Corner Shop\n')
        (self.project / '.env').write_text(self.env_text)
        (self.project / 'data.json').write_text('real rows\n')
        (self.project / 'app.py').write_text(APP)

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args, env=None):
        full = {**os.environ, 'OPENAI_BASE_URL': 'https://api.openai.com/v1', 'OPENAI_API_KEY': 'sk-inherited-REAL-999',
                **(env or {})}
        return subprocess.run([sys.executable, str(SCRIPT), '--project', str(self.project), *args],
                              capture_output=True, text=True, timeout=60, env=full)

    def test_calls_are_recorded_and_nothing_real_changes(self):
        result = self.run_tool('--', sys.executable, 'app.py')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('response safe_run_00001', result.stdout)
        self.assertIn('STRIPE_API_URL [.env]: https://api.stripe.example -> recorder', result.stderr)
        self.assertIn('POST /v1/charges (answered 200)', result.stderr)
        self.assertIn('"amount": 500', result.stderr)
        self.assertIn('DATABASE_URL [postgres (.env)] -> closed port', result.stderr)
        self.assertIn('proxy: CONNECT hardcoded.invalid:443 (refused)', result.stderr)
        self.assertIn('hardcoded call failed', result.stdout)
        self.assertIn('hard-coded', result.stderr)
        # Real files untouched, copy removed.
        self.assertEqual((self.project / '.env').read_text(), self.env_text)
        self.assertEqual((self.project / 'data.json').read_text(), 'real rows\n')
        self.assertEqual([p.name for p in self.project.iterdir() if p.name.startswith('.safe-run-')], [])

    def test_no_secret_value_reaches_output(self):
        result = self.run_tool('--', sys.executable, 'app.py')
        both = result.stdout + result.stderr
        for secret in (REAL_KEY, 'sk-inherited-REAL-999', 'dbpass99', 'admin:'):
            self.assertNotIn(secret, both)
        self.assertIn(f'key seen by app {safe_run.PLACEHOLDER}', result.stdout)
        self.assertIn('STRIPE_API_KEY', result.stderr)
        self.assertIn('OPENAI_API_KEY', result.stderr)

    def test_inherited_environment_is_redirected(self):
        result = self.run_tool('--', sys.executable, 'app.py')
        line = next(l for l in result.stdout.splitlines() if l.startswith('inherited'))
        self.assertRegex(line, r'^inherited http://127\.0\.0\.1:\d+/v1 safe-run-placeholder$')
        self.assertIn('OPENAI_BASE_URL [environment]: https://api.openai.com -> recorder', result.stderr)

    def test_exit_code_is_the_commands_and_keep_leaves_the_copy_inside_the_project(self):
        result = self.run_tool('--keep', '--', sys.executable, '-c', 'import sys; sys.exit(3)')
        self.assertEqual(result.returncode, 3)
        kept = [p.resolve() for p in self.project.iterdir() if p.name.startswith('.safe-run-')]
        self.assertEqual(len(kept), 1)
        self.assertIn(f'kept at {kept[0]}', result.stderr)
        copied_env = (kept[0] / '.env').read_text()
        self.assertNotIn('api.stripe.example', copied_env)
        self.assertNotIn(REAL_KEY, copied_env)

    def test_safe_run_dir_overrides_where_the_copy_goes(self):
        scratch = Path(self.tmp.name) / 'scratch'
        result = self.run_tool('--keep', '--', sys.executable, '-c', 'import os; print(os.getcwd())',
                               env={'SAFE_RUN_DIR': str(scratch)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.realpath(result.stdout.strip()).startswith(os.path.realpath(scratch)))

    def test_config_file_urls_and_secrets_are_rewritten(self):
        (self.project / 'settings.json').write_text(json.dumps({'billing': 'https://billing.example/api', 'token': 'tok_REAL_777'}))
        code = ('import json, urllib.request; c = json.load(open("settings.json")); '
                'urllib.request.urlopen(urllib.request.Request(c["billing"] + "/invoices?token=" + c["token"], method="DELETE"))')
        result = self.run_tool('--config', 'settings.json', '--', sys.executable, '-c', code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('DELETE /api/invoices?token=[secret] (answered 200)', result.stderr)
        self.assertNotIn('tok_REAL_777', result.stdout + result.stderr)
        self.assertIn('billing.example', (self.project / 'settings.json').read_text())

    def test_config_outside_the_project_is_refused(self):
        outside = Path(self.tmp.name) / 'other.json'
        outside.write_text('{"u": "https://x.example"}')
        result = self.run_tool('--config', '../../other.json', '--', sys.executable, '-c', 'pass')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not a file inside the project', result.stderr)
        self.assertEqual(outside.read_text(), '{"u": "https://x.example"}')

    def test_project_paths_point_into_the_copy_and_dependency_folders_are_linked(self):
        (self.project / 'node_modules' / 'pkg').mkdir(parents=True)
        (self.project / '.env').write_text(f'DATA_FILE={self.project}/data.json\nSQLITE_URL=sqlite:///{self.project}/app.db\n')
        code = ('import os; env = dict(l.split("=", 1) for l in open(".env").read().splitlines()); '
                'print(env["DATA_FILE"].startswith(os.getcwd()), env["SQLITE_URL"].endswith(os.getcwd() + "/app.db"), '
                'os.path.islink("node_modules"))')
        result = self.run_tool('--', sys.executable, '-c', code)
        self.assertEqual(result.stdout.strip(), 'True True True', result.stderr)

    def test_missing_command_is_a_usage_error(self):
        result = self.run_tool()
        self.assertEqual(result.returncode, 2)


class Units(unittest.TestCase):
    def test_secret_names(self):
        for name in ('STRIPE_API_KEY', 'apiKey', 'GITHUB_TOKEN', 'DB_PASSWORD', 'client_secret', 'AWS_CREDENTIALS'):
            self.assertTrue(safe_run.is_secret_name(name), name)
        for name in ('MONKEY_MODE', 'SSH_AUTH_SOCK', 'SHOP_NAME', 'KEYBOARD'):
            self.assertFalse(safe_run.is_secret_name(name), name)

    def test_describe_hides_credentials_and_path(self):
        self.assertEqual(safe_run.describe('postgres://u:pw@db.example:5432/x?sslkey=abc'), 'postgres://db.example:5432')
        self.assertEqual(safe_run.describe(''), '(empty)')

    def test_redact(self):
        text = safe_run.redact('{"api_key": "abc123", "amount": 5} Bearer xyz.789 known-value', {'known-value'})
        self.assertNotIn('abc123', text)
        self.assertNotIn('xyz.789', text)
        self.assertNotIn('known-value', text)
        self.assertIn('"amount": 5', text)

    def test_dotenv_rewrite_keeps_comments_quotes_and_redirects_empty_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.env'
            path.write_text('# STRIPE_API_URL=https://commented.example\nexport OPENAI_BASE_URL=\n'
                            "PAYMENT_URL='https://pay.example/v2'\nWEBHOOK=https://hooks.example/x # live\nNAME=x\n")
            recorder = safe_run.Recorder()
            try:
                safe_run.Rewriter(recorder, '/nope', '/nope2').dotenv(path, '.env')
            finally:
                recorder.close()
            lines = path.read_text().splitlines()
        self.assertEqual(lines[0], '# STRIPE_API_URL=https://commented.example')
        self.assertRegex(lines[1], r'^export OPENAI_BASE_URL=http://127\.0\.0\.1:\d+$')
        self.assertRegex(lines[2], r"^PAYMENT_URL='http://127\.0\.0\.1:\d+/v2'$")
        self.assertRegex(lines[3], r'^WEBHOOK=http://127\.0\.0\.1:\d+/x$')
        self.assertEqual(lines[4], 'NAME=x')
        self.assertEqual(set(recorder.services), {'OPENAI_BASE_URL', 'PAYMENT_URL', 'WEBHOOK'})


if __name__ == '__main__':
    unittest.main(verbosity=2)

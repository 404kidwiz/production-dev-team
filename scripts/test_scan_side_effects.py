"""The danger-map scanner finds hazardous commands, stays quiet on safe ones, and never leaks or writes."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import textwrap
import unittest

import scan_side_effects as scanner

SECRET = 'sk_live_DO_NOT_PRINT_4242'

CLIENT = '''
    import json
    import urllib.request

    def _call(env, method, path, body=None):
        req = urllib.request.Request(env["{prefix}_API_URL"] + path, method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={{"Authorization": "Bearer " + env["{prefix}_API_KEY"]}})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read() or b"{{}}")
'''
CONFIG = '''
    from pathlib import Path

    def load_env(path=Path(__file__).parent / ".env"):
        return dict(line.split("=", 1) for line in Path(path).read_text().splitlines() if "=" in line)
'''


def project(files):
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip())
    return tmp, root


def findings(root):
    return {f['command']: f for f in scanner.scan(root)['findings']}


def text_output(root):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        scanner.main([str(root)])
    return out.getvalue()


BOOKING = {
    '.env': f'CALENDAR_API_URL=https://calendar.internal\nCALENDAR_API_KEY={SECRET}\nAUTO_SYNC=1\n',
    'config.py': CONFIG,
    'calendar_api.py': CLIENT.format(prefix='CALENDAR') + '''
    def push_event(env, appt):
        """Create a calendar event and invite the client."""
        body = {"attendees": [appt["email"]], "send_updates": "all"}
        return _call(env, "POST", "/v1/events", body).get("id")
    ''',
    'book.py': '''
    import argparse
    import calendar_api
    from config import load_env

    def add_appointment(appts, day):
        appts.append({"day": day})

    def cmd_list(args):
        print([])

    def cmd_add(args):
        appts = []
        add_appointment(appts, args.day)
        env = load_env()
        if env.get("AUTO_SYNC") == "1":
            calendar_api.push_event(env, appts[0])

    def cmd_sync(args):
        calendar_api.push_event(load_env(), {})

    def main(argv=None):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command", required=True)
        p = sub.add_parser("list")
        p.set_defaults(func=cmd_list)
        p = sub.add_parser("add")
        p.add_argument("day")
        p.add_argument("--note")
        p.set_defaults(func=cmd_add)
        sub.add_parser("sync").set_defaults(func=cmd_sync)
        args = parser.parse_args(argv)
        args.func(args)

    if __name__ == "__main__":
        main()
    ''',
    'tests/test_book.py': '''
    import unittest
    import book

    class BookTests(unittest.TestCase):
        def test_add_appointment(self):
            appts = []
            book.add_appointment(appts, "2026-10-20")
            self.assertEqual(len(appts), 1)
    ''',
}


class FindsTheTrialHazards(unittest.TestCase):
    def test_subcommands_that_invite_clients_are_listed_and_safe_ones_are_not(self):
        tmp, root = project(BOOKING)
        with tmp:
            found = findings(root)
            self.assertEqual(set(found), {'python book.py add DAY', 'python book.py sync'})
            add = found['python book.py add DAY']
            self.assertIn('notify', add['categories'])
            self.assertIn('external', add['categories'])
            self.assertIn('invite', add['risk'])
            self.assertIn('CALENDAR_API_URL', add['risk'])
            self.assertEqual(add['chain'][0], 'book.py:16')
            self.assertTrue(add['chain'][-1].startswith('calendar_api.py:'))

    def test_env_flag_gate_is_reported_without_its_value(self):
        tmp, root = project(BOOKING)
        with tmp:
            found = findings(root)
            self.assertIn('only when AUTO_SYNC is on', found['python book.py add DAY']['note'])
            self.assertIsNone(found['python book.py sync']['note'])

    def test_pure_tests_are_not_listed(self):
        tmp, root = project(BOOKING)
        with tmp:
            self.assertFalse([f for f in findings(root).values() if f['kind'] == 'test'])

    def test_default_run_that_places_purchase_orders(self):
        tmp, root = project({
            '.env': f'SUPPLIER_API_URL=https://supplier.example.com\nSUPPLIER_API_KEY={SECRET}\n',
            'config.py': CONFIG,
            'supplier.py': CLIENT.format(prefix='SUPPLIER') + '''
            def place_order(env, supplier, lines):
                """Submit a purchase order to the supplier."""
                return _call(env, "POST", "/v2/purchase-orders", {"lines": lines}).get("id")
            ''',
            'restock.py': '''
            import argparse
            import supplier
            from config import load_env

            def main(argv=None):
                parser = argparse.ArgumentParser()
                parser.add_argument("--received")
                parser.parse_args(argv)
                supplier.place_order(load_env(), "S1", [])

            if __name__ == "__main__":
                main()
            ''',
        })
        with tmp:
            found = findings(root)
            self.assertEqual(list(found), ['python restock.py  (no arguments needed)'])
            item = found['python restock.py  (no arguments needed)']
            self.assertEqual(item['kind'], 'script')
            self.assertIn('money', item['categories'])
            self.assertIn('purchase', item['risk'])

    def test_seed_script_that_wipes_crm_customers(self):
        tmp, root = project({
            '.env': f'CRM_API_URL=https://crm.example.com\nCRM_API_KEY={SECRET}\n',
            'config.py': CONFIG,
            'crm.py': CLIENT.format(prefix='CRM') + '''
            def delete_all_customers(env):
                _call(env, "DELETE", "/v1/customers")
            ''',
            'seed.py': '''
            import crm
            from config import load_env

            def main():
                crm.delete_all_customers(load_env())

            if __name__ == "__main__":
                main()
            ''',
        })
        with tmp:
            item = findings(root)['python seed.py  (no arguments needed)']
            self.assertIn('destroy', item['categories'])
            self.assertEqual(item['chain'], ['seed.py:8', 'seed.py:5', 'crm.py:12', 'crm.py:8'])

    def test_ops_scripts_with_a_sys_path_import_and_module_level_main(self):
        tmp, root = project({
            '.env': f'BILLING_API_URL=http://127.0.0.1:9443\nBILLING_API_KEY={SECRET}\n',
            'ops/billing.py': '''
            import json, urllib.request
            from pathlib import Path

            def settings():
                return dict(l.split("=", 1) for l in (Path(__file__).parents[1] / ".env").read_text().splitlines())

            def call(method, path, body=None):
                s = settings()
                req = urllib.request.Request(s["BILLING_API_URL"] + path, method=method)
                req.add_header("Authorization", "Bearer " + s["BILLING_API_KEY"])
                with urllib.request.urlopen(req) as r:
                    return json.loads(r.read())
            ''',
            'ops/smoke.py': '''
            """Creates a 1-cent invoice; the billing service emails every new invoice to the customer."""
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import billing

            if __name__ == "__main__":
                billing.call("POST", "/v1/invoices", {"amount": 1})
            ''',
        })
        with tmp:
            item = findings(root)['python ops/smoke.py  (no arguments needed)']
            self.assertEqual(set(item['categories']), {'money', 'notify', 'external'})
            self.assertIn('BILLING_API_URL', item['risk'])

    def test_tests_that_reach_a_live_call_are_grouped_per_file(self):
        files = dict(BOOKING)
        files['tests/test_sync.py'] = '''
        import unittest
        import book

        class SyncTests(unittest.TestCase):
            def test_sync(self):
                book.cmd_sync(None)

            def test_nothing(self):
                pass
        '''
        tmp, root = project(files)
        with tmp:
            tests = [f for f in findings(root).values() if f['kind'] == 'test']
            self.assertEqual(len(tests), 1)
            self.assertEqual(tests[0]['command'], 'python -m unittest discover -s tests -p test_sync.py')
            self.assertIn('1 of 2 tests reach it (first: SyncTests::test_sync)', tests[0]['note'])


class StaysQuiet(unittest.TestCase):
    def test_http_to_a_local_test_server_without_config_is_not_listed(self):
        tmp, root = project({'tests/test_server.py': '''
        import unittest, urllib.request

        class ServerTests(unittest.TestCase):
            def test_get(self):
                urllib.request.urlopen("http://127.0.0.1:8000/health")
        '''})
        with tmp:
            self.assertEqual(findings(root), {})

    def test_single_row_sql_delete_is_not_bulk_but_unfiltered_delete_is(self):
        tmp, root = project({
            'one.py': '''
            import sqlite3
            if __name__ == "__main__":
                sqlite3.connect("x.db").execute("delete from invoices where id=?", (1,))
            ''',
            'wipe.py': '''
            import sqlite3
            if __name__ == "__main__":
                sqlite3.connect("x.db").execute("DELETE FROM customers")
            ''',
        })
        with tmp:
            found = findings(root)
            self.assertEqual(list(found), ['python wipe.py  (no arguments needed)'])
            self.assertEqual(found['python wipe.py  (no arguments needed)']['categories'], ['destroy'])


class JavaScript(unittest.TestCase):
    def test_package_script_that_sends_mail_is_listed_and_library_files_are_not(self):
        tmp, root = project({
            '.env': f'SMTP_URL=smtp://mail.example.com\nSMTP_PASSWORD={SECRET}\n',
            'package.json': json.dumps({'scripts': {'notify': 'node scripts/notify.mjs', 'build': 'tsc'}}),
            'scripts/notify.mjs': '''
            import { send } from '../src/mailer.js';
            await send('weekly digest');
            ''',
            'src/mailer.ts': '''
            import nodemailer from 'nodemailer';
            export async function send(subject) {
              const t = nodemailer.createTransport(process.env.SMTP_URL);
              return t.sendMail({ subject, to: 'customers@example.com' });
            }
            ''',
        })
        with tmp:
            found = findings(root)
            self.assertEqual(list(found), ['npm run notify'])
            item = found['npm run notify']
            self.assertIn('notify', item['categories'])
            self.assertEqual(item['chain'][0], 'scripts/notify.mjs')
            self.assertIn('SMTP_URL', item['risk'])


class ReadOnlyAndSecretSafe(unittest.TestCase):
    def test_never_prints_secret_values_or_literal_tokens(self):
        files = dict(BOOKING)
        files['calendar_api.py'] += f'\n    FALLBACK = "{SECRET}"\n    API_TOKEN_DEFAULT = "HARDCODED_LIVE_TOKEN"\n'
        tmp, root = project(files)
        with tmp:
            out = text_output(root)
            out_json = json.dumps(scanner.scan(root))
            for text in (out, out_json):
                self.assertNotIn(SECRET, text)
                self.assertNotIn('HARDCODED_LIVE_TOKEN', text)
                self.assertNotIn('calendar.internal', text)
            self.assertIn('python book.py sync', out)

    def test_scan_does_not_run_project_code_or_write_files(self):
        files = dict(BOOKING)
        files['book.py'] = 'open("RAN.txt", "w").write("x")\n' + textwrap.dedent(files['book.py']).lstrip()
        tmp, root = project(files)
        with tmp:
            before = sorted(p.relative_to(root) for p in root.rglob('*'))
            text_output(root)
            self.assertEqual(sorted(p.relative_to(root) for p in root.rglob('*')), before)

    def test_cli_exit_codes(self):
        tmp, root = project({'a.py': 'print(1)\n'})
        with tmp:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(scanner.main([str(root), '--json']), 0)
            self.assertEqual(json.loads(out.getvalue())['findings'], [])
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(scanner.main([str(root / 'missing')]), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)

"""Tests for the recording commands: classify, verify, stale, stale-check, report, locking."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import ledger
from test_ledger import CLI, classify, evidence, run, run_in


class ProjectCase(unittest.TestCase):
    """A scratch project: <root>/.engineering/ledger.json plus a proof file and a source file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / 'proj'
        (self.root / '.engineering').mkdir(parents=True)
        (self.root / 'src').mkdir()
        self.ledger = self.root / '.engineering' / 'ledger.json'
        self.assertEqual(run('init', '--out', self.ledger).returncode, 0)
        (self.root / 'proof.txt').write_text('3 passed')
        (self.root / 'src' / 'auth.py').write_text('def check(): return True\n')
        self.catalog = ledger.load_catalog()

    def tearDown(self):
        self._tmp.cleanup()

    def cli(self, *args):
        return run_in(self.root, *args)

    def data(self):
        return json.loads(self.ledger.read_text())

    def row(self, ident, data=None):
        return next(c for c in (data or self.data())['controls'] if c['id'] == ident)

    def classify(self, ident='POST-010', applicability='required-now'):
        return self.cli('classify', self.ledger, ident, '--applicability', applicability, '--rationale', f'why {ident}',
                        '--owner', 'backend lane', '--acceptance', f'procedure for {ident}')

    def verify(self, ident='POST-010', **over):
        args = dict(check='pytest tests/test_isolation.py', environment='local sqlite', artifact='proof.txt',
                    verifier='agent run', revision='abc1234')
        args.update(over)
        cmd = ['verify', self.ledger, ident]
        for key, value in args.items():
            if value is not None:
                cmd += [f'--{key}', value]
        return self.cli(*cmd)


class ClassifyTests(ProjectCase):
    def test_classify_sets_fields_and_keeps_other_rows(self):
        before = self.data()
        result = self.classify()
        self.assertEqual(result.returncode, 0, result.stderr)
        row = self.row('POST-010')
        self.assertEqual((row['applicability'], row['owner'], row['status']), ('required-now', 'backend lane', 'unverified'))
        other_before = [c for c in before['controls'] if c['id'] != 'POST-010']
        other_after = [c for c in self.data()['controls'] if c['id'] != 'POST-010']
        self.assertEqual(other_before, other_after)
        self.assertEqual(self.cli('validate', self.ledger).returncode, 3)  # required-now, not verified

    def test_partial_classification_of_untouched_row_is_refused_and_leaves_file_alone(self):
        before = self.ledger.read_bytes()
        result = self.cli('classify', self.ledger, 'POST-010', '--rationale', 'only this')
        self.assertEqual(result.returncode, 2)
        self.assertIn('missing owner', result.stderr)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_refusals(self):
        before = self.ledger.read_bytes()
        cases = [
            (('POST-010', '--status', 'verified'), "invalid choice: 'verified'"),  # only `verify` may set it
            (('POST-999', '--applicability', 'required-now'), 'unknown control id'),
            (('POST-010',), 'nothing to change'),
        ]
        for args, message in cases:
            result = self.cli('classify', self.ledger, *args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn(message, result.stderr)
        always = self.cli('classify', self.ledger, 'POST-023', '--applicability', 'not-applicable', '--rationale', 'r', '--owner', 'o', '--acceptance', 'a')
        self.assertEqual(always.returncode, 2)
        self.assertIn('always-on control cannot be not-applicable', always.stderr)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_blanking_a_classified_row_is_refused(self):
        self.classify()
        before = self.ledger.read_bytes()
        result = self.cli('classify', self.ledger, 'POST-010', '--rationale', '')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_deferred_needs_revisit_trigger_and_accepted_by_is_recorded(self):
        result = self.cli('classify', self.ledger, 'POST-114', '--applicability', 'deferred', '--rationale', 'no AI yet',
                          '--owner', 'owner', '--acceptance', 'n/a until AI ships', '--revisit-trigger', '',
                          '--accepted-by', 'Dana')
        self.assertEqual(result.returncode, 2)
        self.assertIn('needs revisit trigger', result.stderr)
        ok = self.cli('classify', self.ledger, 'POST-114', '--applicability', 'deferred', '--rationale', 'no AI yet',
                      '--owner', 'owner', '--acceptance', 'checked when AI ships', '--revisit-trigger', 'before any model call',
                      '--accepted-by', 'Dana')
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(self.row('POST-114')['accepted_by'], 'Dana')


class VerifyTests(ProjectCase):
    def test_verify_records_hash_time_and_validates(self):
        self.classify()
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        row = self.row('POST-010')
        last = row['evidence'][-1]
        self.assertEqual(row['status'], 'verified')
        self.assertEqual(last['artifact_sha256'], ledger.sha256_file(self.root / 'proof.txt'))
        self.assertTrue(ledger.is_iso8601(last['timestamp']) and not ledger.is_future(last['timestamp']))
        self.assertEqual(last['revision'], 'abc1234')
        self.assertEqual(self.cli('validate', self.ledger).returncode, 0)

    def test_verify_refusals_leave_the_ledger_untouched(self):
        self.classify()
        before = self.ledger.read_bytes()
        cases = [
            ({'check': 'trust me'}, 'placeholder'),
            ({'revision': 'x'}, 'placeholder'),
            ({'verifier': 'n/a'}, 'placeholder'),
            ({'check': 'ok'}, 'placeholder'),
            ({'artifact': 'nowhere/missing.txt'}, 'artifact not found'),
            ({'watch': 'src/missing.py'}, 'watched file not found'),
        ]
        for over, message in cases:
            result = self.verify(**over)
            self.assertEqual(result.returncode, 2, over)
            self.assertIn(message, result.stderr, over)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_verify_requires_a_classified_applicable_control(self):
        untouched = self.verify(ident='POST-011')
        self.assertEqual(untouched.returncode, 2)
        self.assertIn('classify the control first', untouched.stderr)
        self.classify('POST-033', applicability='not-applicable')
        na = self.verify(ident='POST-033')
        self.assertEqual(na.returncode, 2)
        self.assertIn('nothing to verify', na.stderr)

    def test_url_artifact_is_accepted_without_hash(self):
        self.classify()
        self.assertEqual(self.verify(artifact='https://ci.example/run/42').returncode, 0)
        self.assertNotIn('artifact_sha256', self.row('POST-010')['evidence'][-1])

    def test_failed_result_records_missing_then_pass_keeps_history(self):
        self.classify()
        failed = self.verify(result='failed')
        self.assertEqual(failed.returncode, 0)
        self.assertEqual(self.row('POST-010')['status'], 'missing')
        self.assertEqual(self.cli('validate', self.ledger).returncode, 3)
        self.assertEqual(self.verify().returncode, 0)
        row = self.row('POST-010')
        self.assertEqual([e['result'] for e in row['evidence']], ['failed', 'passed'])
        self.assertEqual(row['status'], 'verified')
        self.assertEqual(self.cli('validate', self.ledger).returncode, 0)

    @unittest.skipUnless(shutil.which('git'), 'git not installed')
    def test_revision_defaults_to_git_and_marks_dirty(self):
        git = lambda *a: subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t', *a], cwd=self.root,
                                        capture_output=True, text=True, check=True)
        git('init', '-q')
        git('add', '-A')
        git('commit', '-q', '-m', 'init')
        short = git('rev-parse', '--short', 'HEAD').stdout.strip()
        self.classify()
        self.assertEqual(self.verify(revision=None).returncode, 0)
        self.assertTrue(self.row('POST-010')['evidence'][-1]['revision'].startswith(short))

    def test_no_git_and_no_revision_is_refused(self):
        self.classify()
        result = self.verify(revision=None)
        if shutil.which('git') and subprocess.run(['git', 'rev-parse'], cwd=self.root, capture_output=True).returncode == 0:
            self.skipTest('temp dir is inside a git work tree')
        self.assertEqual(result.returncode, 2)
        self.assertIn('evidence missing revision', result.stderr)


class DriftTests(ProjectCase):
    def verified(self, **over):
        self.classify()
        self.assertEqual(self.verify(**over).returncode, 0)

    def test_artifact_change_warns_in_slice_and_errors_at_release(self):
        self.verified()
        (self.root / 'proof.txt').write_text('tampered')
        report = json.loads(self.cli('validate', self.ledger).stdout)
        self.assertTrue(any('artifact changed since verification' in w for w in report['warnings']))
        data = self.data()
        errors = ledger.validate(data, self.catalog, release=True, artifact_roots=[self.root])
        self.assertTrue(any('artifact changed since verification' in e for e in errors))

    def test_release_requires_hash_for_local_artifacts_only(self):
        data = ledger.initialize(self.catalog)
        row = next(c for c in data['controls'] if c['id'] == 'POST-010')
        classify(row, status='verified', evidence=[evidence(artifact='proof.txt')])
        errors = ledger.validate(data, self.catalog, release=True, artifact_roots=[self.root])
        self.assertTrue(any('release needs evidence recorded with the verify command' in e for e in errors))
        row['evidence'][0]['artifact_sha256'] = ledger.sha256_file(self.root / 'proof.txt')
        errors = ledger.validate(data, self.catalog, release=True, artifact_roots=[self.root])
        self.assertFalse(any('verify command' in e for e in errors))
        row['evidence'][0]['artifact'] = 'https://ci.example/run/1'
        row['evidence'][0].pop('artifact_sha256')
        errors = ledger.validate(data, self.catalog, release=True, artifact_roots=[self.root])
        self.assertFalse(any('verify command' in e for e in errors))

    def test_release_errors_are_not_duplicated_as_warnings(self):
        self.verified()
        (self.root / 'proof.txt').write_text('tampered')
        out = json.loads(self.cli('validate', self.ledger, '--release', '--verbose').stdout)
        self.assertTrue(any('artifact changed' in e for e in out['errors']))
        self.assertFalse(any('artifact changed' in w for w in out['warnings']))

    def test_watched_change_detected_listed_and_applied(self):
        self.verified(watch='src/auth.py')
        self.assertEqual(self.cli('stale-check', self.ledger).returncode, 0)
        (self.root / 'src' / 'auth.py').write_text('def check(): return False\n')
        listing = self.cli('stale-check', self.ledger)
        self.assertEqual(listing.returncode, 3)
        self.assertIn('POST-010', json.loads(listing.stdout)['drifted'])
        self.assertEqual(self.row('POST-010')['status'], 'verified', 'listing must not modify the ledger')
        applied = self.cli('stale-check', self.ledger, '--apply')
        self.assertEqual(applied.returncode, 0)
        self.assertEqual(json.loads(applied.stdout)['marked_unverified'], ['POST-010'])
        row = self.row('POST-010')
        self.assertEqual(row['status'], 'unverified')
        self.assertEqual(len(row['evidence']), 1, 'old evidence is kept')
        self.assertIn('Stale (', row['rationale'])
        self.assertIn('src/auth.py', row['rationale'])
        self.assertEqual(self.cli('stale-check', self.ledger).returncode, 0)
        self.assertEqual(self.cli('validate', self.ledger).returncode, 3, 'required-now is unverified again')

    def test_watched_file_deleted_counts_as_drift(self):
        self.verified(watch='src/auth.py')
        (self.root / 'src' / 'auth.py').unlink()
        self.assertIn('POST-010', json.loads(self.cli('stale-check', self.ledger).stdout)['drifted'])

    def test_manual_stale_then_reverify(self):
        self.verified()
        self.assertEqual(self.cli('stale', self.ledger, 'POST-010', '--reason', 'auth middleware changed in abc123').returncode, 0)
        row = self.row('POST-010')
        self.assertEqual(row['status'], 'unverified')
        self.assertIn('auth middleware changed in abc123', row['rationale'])
        self.assertEqual(self.cli('stale', self.ledger, 'POST-999', '--reason', 'x').returncode, 2)
        self.assertEqual(self.verify().returncode, 0)
        self.assertEqual(len(self.row('POST-010')['evidence']), 2)

    def test_only_verified_rows_can_drift(self):
        self.classify()
        (self.root / 'proof.txt').unlink()
        self.assertEqual(json.loads(self.cli('stale-check', self.ledger).stdout)['total'], 0)


class ReportTests(ProjectCase):
    def test_report_sections_and_honesty(self):
        run('triggers', self.ledger, 'multitenant')
        self.classify()
        self.classify('POST-011')
        self.assertEqual(self.verify(ident='POST-011').returncode, 0)
        self.cli('classify', self.ledger, 'POST-114', '--applicability', 'deferred', '--rationale', 'no AI yet', '--owner', 'o',
                 '--acceptance', 'later', '--revisit-trigger', 'before model calls', '--accepted-by', 'Dana')
        out = self.cli('report', self.ledger, '--scope', 'local fixture only')
        self.assertEqual(out.returncode, 0)
        text = out.stdout
        for needle in ('# Release report', 'local fixture only', 'not a security assessment', 'POST-010', 'Release blockers',
                       'POST-011', 'local sqlite', 'accepted by Dana', 'Boundaries to report separately', 'Not assessed here'):
            self.assertIn(needle, text)
        self.assertNotIn('%', text, 'no percentages that hide unreviewed controls')
        self.assertNotIn('ready for release', text.lower())

    def test_report_out_refuses_overwrite_and_flags_drift(self):
        self.classify()
        self.verify()
        (self.root / 'proof.txt').write_text('changed')
        target = self.root / 'release-report.md'
        first = self.cli('report', self.ledger, '--out', target)
        self.assertEqual(first.returncode, 0)
        self.assertIn('artifact changed since verification', target.read_text())
        before = target.read_bytes()
        again = self.cli('report', self.ledger, '--out', target)
        self.assertEqual(again.returncode, 2)
        self.assertEqual(target.read_bytes(), before)

    def test_report_on_blank_ledger_does_not_crash(self):
        out = self.cli('report', self.ledger)
        self.assertEqual(out.returncode, 0)
        self.assertIn('none declared', out.stdout)


class ConcurrencyTests(ProjectCase):
    def test_parallel_classify_loses_no_updates(self):
        ids = [f'POST-{n:03}' for n in range(1, 17)]
        procs = [subprocess.Popen(CLI + ['classify', str(self.ledger), ident, '--applicability', 'required-now',
                                         '--rationale', f'r {ident}', '--owner', 'o', '--acceptance', f'a {ident}'],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, cwd=str(self.root))
                 for ident in ids]
        codes = [(p.returncode, err) for p, err in ((p, p.communicate()[1]) for p in procs)]
        self.assertTrue(all(code == 0 for code, _ in codes), codes)
        done = {c['id'] for c in self.data()['controls'] if c['applicability'] == 'required-now'}
        self.assertEqual(done, set(ids))
        self.assertEqual([p.name for p in self.ledger.parent.iterdir() if p.name.endswith('.tmp')], [])

    def test_parallel_mixed_writers_keep_valid_json(self):
        procs = []
        for n in range(1, 9):
            procs.append(subprocess.Popen(CLI + ['triggers', str(self.ledger), 'payments' if n % 2 else 'ai'],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(self.root)))
            procs.append(subprocess.Popen(CLI + ['classify', str(self.ledger), f'POST-{n + 20:03}', '--applicability', 'required-now',
                                                 '--rationale', 'r', '--owner', 'o', '--acceptance', 'a'],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(self.root)))
        self.assertTrue(all(p.wait() == 0 for p in procs))
        data = self.data()
        self.assertEqual(len([c for c in data['controls'] if c['applicability'] == 'required-now']), 8)
        self.assertIn(data['project']['triggers'], (['payments'], ['ai']))


class PlaceholderTests(unittest.TestCase):
    def test_placeholder_rules(self):
        for value in ('x', 'N/A', ' trust me ', 'TBD', 'ok', 'done', '-'):
            self.assertTrue(ledger.is_placeholder('verifier', value), value)
        self.assertFalse(ledger.is_placeholder('verifier', 'unit test'))
        self.assertTrue(ledger.is_placeholder('check', 'make'))        # too short to be a real check
        self.assertFalse(ledger.is_placeholder('check', 'pytest -q'))
        self.assertFalse(ledger.is_placeholder('environment', 'ab'))   # length rule applies to check only

    def test_validate_flags_placeholder_evidence(self):
        catalog = ledger.load_catalog()
        data = ledger.initialize(catalog)
        row = next(c for c in data['controls'] if c['id'] == 'POST-010')
        classify(row, status='verified', evidence=[evidence(check='trust me', revision='x')])
        errors = ledger.validate(data, catalog)
        self.assertTrue(any('check looks like a placeholder' in e for e in errors))
        self.assertTrue(any('revision looks like a placeholder' in e for e in errors))


class ResidualSurvivorTests(unittest.TestCase):
    """Boundary and display behavior flagged as unguarded by mutation testing."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / 'ledger.json'
        self.catalog = ledger.load_catalog()

    def tearDown(self):
        self._tmp.cleanup()

    def release_with_untouched(self, count):
        data = ledger.initialize(self.catalog)
        data['project']['triggers'] = ['payments']
        lanes = {f'POST-{n}' for n in range(148, 161)}
        untouched = 0
        for c in data['controls']:
            if c['id'] in lanes:
                continue
            if untouched < count:
                untouched += 1
                continue
            classify(c)
        self.path.write_text(json.dumps(data))
        return json.loads(run('validate', self.path, '--release').stdout)

    def test_cap_boundary_exactly_at_limit_is_not_truncated(self):
        self.assertEqual(ledger.OUTPUT_CAP, 25)
        at = self.release_with_untouched(25)
        self.assertEqual((at['errors_total'], len(at['errors']), at['output_truncated']), (25, 25, False))
        over = self.release_with_untouched(26)
        self.assertEqual((over['errors_total'], len(over['errors']), over['output_truncated']), (26, 25, True))
        under = self.release_with_untouched(24)
        self.assertEqual((under['errors_total'], len(under['errors']), under['output_truncated']), (24, 24, False))

    def test_truncation_flag_counts_the_attention_list(self):
        data = ledger.initialize(self.catalog)
        declared = ['payments', 'accounts', 'database', 'multitenant']
        data['project']['triggers'] = declared
        count = 0
        for m in self.catalog['controls']:
            if set(m['triggers']) & set(declared) and m['kind'] == 'control' and 'always' not in m['triggers']:
                row = next(c for c in data['controls'] if c['id'] == m['id'])
                classify(row, applicability='deferred', revisit_trigger='later')
                count += 1
        self.assertGreater(count, 25)
        self.path.write_text(json.dumps(data))
        out = json.loads(run('validate', self.path).stdout)
        self.assertEqual(out['errors_total'], 0)
        self.assertGreater(out['non_committal_on_triggered_total'], 25)
        self.assertEqual(len(out['non_committal_on_triggered']), 25)
        self.assertTrue(out['output_truncated'])

    def test_missing_interpretation_file_yields_no_caveats_not_a_crash(self):
        original = ledger.INTERPRETATION
        ledger.INTERPRETATION = Path(self._tmp.name) / 'absent.md'
        try:
            self.assertEqual(ledger.load_notes(), {})
        finally:
            ledger.INTERPRETATION = original

    def test_rows_that_are_not_objects_are_ignored_by_views(self):
        data = {'schema_version': 2, 'controls': [1, None, {'id': 'POST-001'}]}
        self.assertEqual(len(ledger.control_rows(data)), 1)
        self.assertEqual(ledger.summary(data, self.catalog)['total'], 1)

    def test_command_list_matches_the_parser(self):
        parser = ledger.build_parser()
        choices = next(a for a in parser._actions if getattr(a, 'choices', None)).choices
        self.assertEqual(set(choices), set(ledger.COMMANDS))


if __name__ == '__main__':
    unittest.main(verbosity=2)

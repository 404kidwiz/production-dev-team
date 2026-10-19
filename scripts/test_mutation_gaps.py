"""Tests that close survivors found by independent mutation testing of the recording commands and installer."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import install_snippet
import ledger
from test_ledger import classify, evidence, run_in
from test_ledger_ops import ProjectCase


class HashingIsPinned(unittest.TestCase):
    def test_known_digest_and_whole_file_hashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            small = Path(tmp) / 'small'
            small.write_bytes(b'abc')
            self.assertEqual(ledger.sha256_file(small), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
            big = Path(tmp) / 'big'
            data = os.urandom(300_000)  # well past one read chunk: a first-chunk-only hash must differ
            big.write_bytes(data)
            self.assertEqual(ledger.sha256_file(big), hashlib.sha256(data).hexdigest())
            big.write_bytes(data[:70_000] + b'X' + data[70_001:])  # change beyond 64 KiB
            self.assertNotEqual(ledger.sha256_file(big), hashlib.sha256(data).hexdigest())

    def test_verify_stores_the_real_digest_of_the_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.engineering').mkdir()
            led = root / '.engineering' / 'ledger.json'
            self.assertEqual(run_in(root, 'init', '--out', led).returncode, 0)
            payload = os.urandom(200_000)
            (root / 'out.bin').write_bytes(payload)
            run_in(root, 'classify', led, 'POST-010', '--applicability', 'required-now', '--rationale', 'r', '--owner', 'o', '--acceptance', 'a')
            done = run_in(root, 'verify', led, 'POST-010', '--check', 'pytest -q suite', '--environment', 'local', '--artifact', 'out.bin',
                          '--verifier', 'agent run', '--revision', 'abc1234')
            self.assertEqual(done.returncode, 0, done.stderr)
            row = next(c for c in json.loads(led.read_text())['controls'] if c['id'] == 'POST-010')
            self.assertEqual(row['evidence'][-1]['artifact_sha256'], hashlib.sha256(payload).hexdigest())


class LatestEvidenceWins(ProjectCase):
    def two_entry_row(self):
        (self.root / 'old.txt').write_text('first run')
        self.classify()
        self.assertEqual(self.verify(artifact='old.txt', environment='first env').returncode, 0)
        (self.root / 'new.txt').write_text('second run')
        self.assertEqual(self.verify(artifact='new.txt', environment='second env').returncode, 0)

    def test_drift_is_judged_against_the_latest_entry_only(self):
        self.two_entry_row()
        (self.root / 'old.txt').write_text('old evidence edited later')   # superseded evidence: irrelevant
        self.assertEqual(self.cli('stale-check', self.ledger).returncode, 0)
        (self.root / 'new.txt').write_text('latest evidence tampered')
        out = self.cli('stale-check', self.ledger)
        self.assertEqual(out.returncode, 3)
        self.assertIn('new.txt', json.dumps(json.loads(out.stdout)['drifted']))

    def test_report_shows_the_latest_entry_not_the_first(self):
        self.two_entry_row()
        text = self.cli('report', self.ledger).stdout
        row = next(l for l in text.splitlines() if l.startswith('| POST-010'))
        self.assertIn('second env', row)
        self.assertIn('new.txt', row)
        self.assertNotIn('first env', row)
        self.assertIn('- second env', text)
        self.assertNotIn('- first env', text)

    def test_deleted_artifact_of_a_verified_control_is_stale(self):
        self.classify()
        self.verify()
        (self.root / 'proof.txt').unlink()
        out = self.cli('stale-check', self.ledger)
        self.assertEqual(out.returncode, 3)
        self.assertIn('artifact not found', json.dumps(json.loads(out.stdout)['drifted']))
        applied = self.cli('stale-check', self.ledger, '--apply')
        self.assertEqual(json.loads(applied.stdout)['marked_unverified'], ['POST-010'])


class PlaceholderBoundaries(ProjectCase):
    def test_environment_placeholders_are_refused(self):
        self.classify()
        before = self.ledger.read_bytes()
        for value in ('n/a', 'TBD', 'x'):
            result = self.verify(environment=value)
            self.assertEqual(result.returncode, 2, value)
            self.assertIn('environment looks like a placeholder', result.stderr)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_check_length_boundary_and_pass_words(self):
        self.assertTrue(ledger.is_placeholder('check', 'abcd'))        # 4 characters: too short
        self.assertFalse(ledger.is_placeholder('check', 'abcde'))      # exactly the minimum
        self.assertEqual(ledger.MIN_CHECK_LENGTH, 5)
        for word in ('pass', 'passed', 'PASSED.'):
            for field in ('check', 'environment', 'revision', 'verifier'):
                self.assertTrue(ledger.is_placeholder(field, word), (field, word))


class ClassifyGuards(ProjectCase):
    def test_a_classified_row_cannot_be_reset_to_untouched_in_one_call(self):
        self.classify()
        before = self.ledger.read_bytes()
        result = self.cli('classify', self.ledger, 'POST-010', '--applicability', 'conditional', '--status', 'unverified',
                          '--rationale', '', '--owner', '', '--acceptance', '',
                          '--revisit-trigger', ledger.PRISTINE_TRIGGER)
        self.assertEqual(result.returncode, 2)
        self.assertIn('reset the control to untouched', result.stderr)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_apply_classify_itself_refuses_verified(self):
        data = ledger.initialize(self.catalog)
        with self.assertRaisesRegex(ValueError, 'use the verify command'):
            ledger.apply_classify(data, self.catalog, 'POST-010', {'status': 'verified', 'applicability': 'required-now'})


class ReportCap(unittest.TestCase):
    def report_with_pending(self, pending):
        catalog = ledger.load_catalog()
        data = ledger.initialize(catalog)
        data['project']['triggers'] = ['payments', 'accounts', 'database']
        rows = {c['id']: c for c in data['controls']}
        for ident in ledger.unclassified_relevant(data, catalog)[pending:]:
            classify(rows[ident], applicability='required-now')
        self.assertEqual(len(ledger.unclassified_relevant(data, catalog)), pending)
        return ledger.render_report(data, catalog, 'scope', [Path('.')])

    @staticmethod
    def untouched_entries(text):
        section = text.split('## Relevant controls still untouched')[1].split('\n## ')[0]
        return [l for l in section.splitlines() if l.startswith('- POST-') or l.startswith('- EXT-')]

    def test_more_line_appears_only_past_the_cap_with_the_exact_remainder(self):
        self.assertEqual(ledger.OUTPUT_CAP, 25)
        at = self.report_with_pending(25)
        self.assertEqual(len(self.untouched_entries(at)), 25)
        self.assertNotIn('more (see', at)
        over = self.report_with_pending(27)
        self.assertEqual(len(self.untouched_entries(over)), 25)
        self.assertIn('- ...and 2 more (see `ledger.py next`)', over)
        under = self.report_with_pending(24)
        self.assertEqual(len(self.untouched_entries(under)), 24)
        self.assertNotIn('more (see', under)


class InstallerRoundTrips(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def cli(self, *extra):
        return subprocess.run([os.sys.executable, install_snippet.__file__, '--host', 'codex', '--project', str(self.project), *extra],
                              capture_output=True, text=True)

    def test_no_final_newline_never_glues_the_marker_to_the_last_line(self):
        path = self.project / 'AGENTS.md'
        path.write_text('last line')
        self.cli()
        self.assertTrue(path.read_text().startswith('last line\n' + install_snippet.START))

    def test_newline_terminated_files_round_trip_exactly_whatever_their_trailing_blank_lines(self):
        path = self.project / 'AGENTS.md'
        for original in ('a\n', 'a\n\n', 'a\n\n\n\n', '\n', '# T\n\ntext\n\n'):
            path.write_text(original)
            self.cli()
            self.assertNotEqual(path.read_text(), original)
            self.cli('--remove')
            self.assertEqual(path.read_text(), original, repr(original))

    def test_failed_write_leaves_no_temp_file_and_the_original_intact(self):
        target = self.project / 'AGENTS.md'
        target.write_text('keep me\n')
        with mock.patch('os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                install_snippet.write_atomic(target, 'new content')
        self.assertEqual(target.read_text(), 'keep me\n')
        self.assertEqual([p.name for p in self.project.iterdir()], ['AGENTS.md'])


class GitRevisionIsExact(unittest.TestCase):
    @unittest.skipUnless(shutil.which('git'), 'git not installed')
    def test_short_hash_and_dirty_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git = lambda *a: subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t', *a], cwd=root,
                                            capture_output=True, text=True, check=True)
            git('init', '-q')
            (root / 'f.txt').write_text('one')
            git('add', 'f.txt')
            git('commit', '-q', '-m', 'init')
            short = git('rev-parse', '--short', 'HEAD').stdout.strip()
            self.assertEqual(ledger.git_revision(root), short)
            self.assertLess(len(short), 40)
            (root / 'f.txt').write_text('two')
            self.assertEqual(ledger.git_revision(root), short + '+dirty')
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(ledger.git_revision(Path(tmp))) if not os.path.isdir('/.git') else None


if __name__ == '__main__':
    unittest.main(verbosity=2)

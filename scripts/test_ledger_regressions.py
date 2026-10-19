"""Regression tests for bugs found by independent QA of the recording commands. One test per reproduced bug."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

import ledger
from test_ledger import evidence, run, run_in
from test_ledger_ops import ProjectCase


class FailedRunsAndPlaceholders(ProjectCase):
    def test_bad_failed_entry_is_refused_at_record_time_and_cannot_poison_later_verifies(self):
        self.classify()
        before = self.ledger.read_bytes()
        for over in ({'revision': None}, {'check': 'make'}, {'verifier': 'n/a'}):
            result = self.verify(result='failed', **over)
            if over == {'revision': None} and shutil.which('git') and subprocess.run(
                    ['git', 'rev-parse'], cwd=self.root, capture_output=True).returncode == 0:
                continue  # temp dir sits inside a git work tree, so a revision exists
            self.assertEqual(result.returncode, 2, over)
            self.assertEqual(self.ledger.read_bytes(), before, over)
        self.assertEqual(self.verify(result='failed').returncode, 0)
        self.assertEqual(self.verify().returncode, 0, 'a good failed entry must not block the later pass')

    def test_placeholder_variants_are_caught(self):
        for value in ('trust  me', 'ok.', 'N/A.', 'none.', 'n/a\u200b', '\uff2e\uff21', 'TODO.', '  Ok!  ', '...', '\u200b'):
            self.assertTrue(ledger.is_placeholder('verifier', value), repr(value))
        for value in ('pytest -q', 'unit test run 3', 'agent run; independent review not performed'):
            self.assertFalse(ledger.is_placeholder('verifier', value), repr(value))
        self.assertTrue(ledger.is_placeholder('check', 'TODO.'))
        self.assertFalse(ledger.is_placeholder('check', 'pytest -q'))

    def test_hand_edited_variant_placeholder_fails_validation(self):
        data = ledger.initialize(self.catalog)
        row = next(c for c in data['controls'] if c['id'] == 'POST-010')
        row.update(rationale='r', owner='o', acceptance='a', status='verified',
                   evidence=[evidence(check='trust  me', verifier='N/A.')])
        errors = ledger.validate(data, self.catalog)
        self.assertEqual(len([e for e in errors if 'placeholder' in e]), 2)


class ArtifactKinds(ProjectCase):
    def test_only_http_urls_count_as_urls(self):
        for good in ('https://ci.example/run/1', 'HTTP://x.example/a'):
            self.assertTrue(ledger.is_url(good), good)
        for bad in ('file:///etc/hosts', 'x://y', 'ftp://h/f', 'proof.txt', 'https://', 'https:// spaced'):
            self.assertFalse(ledger.is_url(bad), bad)

    def test_verify_refuses_non_http_schemes_and_non_files(self):
        self.classify()
        before = self.ledger.read_bytes()
        (self.root / 'adir').mkdir()
        for artifact, message in (('file:///etc/hosts', 'artifact not found'), ('x://y', 'artifact not found'),
                                  ('adir', 'regular file'), ('/dev/null', 'regular file')):
            result = self.verify(artifact=artifact)
            self.assertEqual(result.returncode, 2, artifact)
            self.assertIn(message, result.stderr, artifact)
        self.assertEqual(self.ledger.read_bytes(), before)
        (self.root / 'adir' / 'f').write_text('x')
        result = self.verify(watch='adir')
        self.assertEqual((result.returncode, 'regular file' in result.stderr), (2, True))

    def test_hand_edited_scheme_and_directory_artifacts_fail_at_release(self):
        (self.root / 'adir').mkdir()
        data = ledger.initialize(self.catalog)
        row = next(c for c in data['controls'] if c['id'] == 'POST-010')
        ledger_row = dict(rationale='r', owner='o', acceptance='a', status='verified')
        row.update(ledger_row)
        for artifact, expect in (('x://y', 'artifact not found'), ('adir', 'not a regular file')):
            row['evidence'] = [evidence(artifact=artifact, artifact_sha256='0' * 64)]
            errors = ledger.validate(data, self.catalog, release=True, artifact_roots=[self.root])
            self.assertTrue(any(expect in e for e in errors), (artifact, errors))

    def test_overlong_and_nul_paths_do_not_crash_resolution(self):
        self.assertIsNone(ledger.resolve_file('a' * 5000, [self.root]))
        self.assertIsNone(ledger.resolve_file('bad\x00name', [self.root]))
        data = ledger.initialize(self.catalog)
        row = next(c for c in data['controls'] if c['id'] == 'POST-010')
        row.update(rationale='r', owner='o', acceptance='a', status='verified',
                   evidence=[evidence(artifact='a' * 5000, artifact_sha256='0' * 64)])
        self.ledger.write_text(json.dumps(data))
        self.assertIn(self.cli('validate', self.ledger, '--release').returncode, (1, 3))

    def test_timestamp_shape_is_the_same_on_every_python(self):
        for good in ('2026-10-16T21:00:00-04:00', '2026-10-16T21:00:00Z', '2026-10-16 21:00:00', '2026-10-16T21:00',
                     '2026-10-16T21:00:00.123Z', '2026-10-16T21:00:00.123456+00:00'):
            self.assertTrue(ledger.is_iso8601(good), good)
        for bad in ('2026-10-16T21:00:00.5-04:00', '2026-10-16', 'yesterday', '2026-13-01T00:00:00Z', '20261016T210000Z', ''):
            self.assertFalse(ledger.is_iso8601(bad), bad)


class OneProjectRoot(ProjectCase):
    def test_paths_are_stored_relative_to_the_project_root_whatever_the_cwd(self):
        self.classify()
        result = run_in(self.root / 'src', 'verify', self.ledger, 'POST-010', '--check', 'pytest -q isolation',
                        '--environment', 'local', '--artifact', '../proof.txt', '--verifier', 'agent run',
                        '--revision', 'abc1234', '--watch', 'auth.py')
        self.assertEqual(result.returncode, 0, result.stderr)
        last = self.row('POST-010')['evidence'][-1]
        self.assertEqual(last['artifact'], 'proof.txt')
        self.assertEqual(list(last['watched']), ['src/auth.py'])

    def test_outside_files_are_stored_absolute(self):
        outside = Path(self._tmp.name) / 'outside.txt'
        outside.write_text('log')
        self.classify()
        self.assertEqual(self.verify(artifact=str(outside)).returncode, 0)
        self.assertEqual(self.row('POST-010')['evidence'][-1]['artifact'], os.path.abspath(outside))

    def test_stale_check_gives_the_same_answer_from_any_directory(self):
        self.classify()
        self.assertEqual(self.verify(watch='src/auth.py').returncode, 0)
        copy = Path(self._tmp.name) / 'old_copy'
        (copy / 'src').mkdir(parents=True)
        shutil.copy(self.root / 'src' / 'auth.py', copy / 'src' / 'auth.py')   # an old copy in another cwd
        (self.root / 'src' / 'auth.py').write_text('changed\n')
        for cwd in (self.root, copy, Path(self._tmp.name), self.root / 'src'):
            out = run_in(cwd, 'stale-check', self.ledger)
            self.assertEqual(out.returncode, 3, f'false pass from {cwd}')
            self.assertIn('POST-010', json.loads(out.stdout)['drifted'])
        (self.root / 'src' / 'auth.py').write_text('def check(): return True\n')
        for cwd in (self.root, copy, Path(self._tmp.name)):
            self.assertEqual(run_in(cwd, 'stale-check', self.ledger).returncode, 0, f'false drift from {cwd}')


class StaleRules(ProjectCase):
    def test_stale_only_applies_to_verified_rows_and_needs_a_reason(self):
        before = self.ledger.read_bytes()
        self.assertEqual(self.cli('stale', self.ledger, 'POST-011', '--reason', 'x').returncode, 2)  # untouched row
        self.classify()
        self.assertEqual(self.cli('stale', self.ledger, 'POST-010', '--reason', 'x').returncode, 2)  # classified, not verified
        self.assertNotIn('Stale', self.row('POST-010')['rationale'])
        self.verify()
        for reason in ('', '   '):
            self.assertEqual(self.cli('stale', self.ledger, 'POST-010', '--reason', reason).returncode, 2)
        self.assertEqual(self.row('POST-010')['status'], 'verified')
        untouched = self.row('POST-011')
        self.assertEqual((untouched['rationale'], untouched['owner']), ('', ''))
        self.assertEqual(self.cli('validate', self.ledger, '--structure-only').returncode, 0)
        self.assertNotEqual(before, self.ledger.read_bytes())

    def test_stale_note_is_cleared_when_the_control_is_reverified(self):
        self.classify()
        self.verify()
        self.cli('stale', self.ledger, 'POST-010', '--reason', 'auth changed | really')
        self.assertIn('Stale (', self.row('POST-010')['rationale'])
        self.assertEqual(self.verify().returncode, 0)
        row = self.row('POST-010')
        self.assertNotIn('Stale', row['rationale'])
        self.assertEqual(row['rationale'], 'why POST-010')
        self.assertEqual(len(row['evidence']), 2)


class ClassifyRules(ProjectCase):
    def test_deferred_and_conditional_need_a_real_revisit_trigger(self):
        base = ('--rationale', 'r', '--owner', 'o', '--acceptance', 'a')
        for applicability in ('deferred', 'conditional'):
            result = self.cli('classify', self.ledger, 'POST-035', '--applicability', applicability, *base)
            self.assertEqual(result.returncode, 2, applicability)
            self.assertIn('needs revisit trigger', result.stderr)
        ok = self.cli('classify', self.ledger, 'POST-035', '--applicability', 'deferred', *base, '--revisit-trigger', 'before launch')
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(self.cli('classify', self.ledger, 'POST-036', '--applicability', 'required-now', *base).returncode, 0)

    def test_hand_edited_deferred_with_the_init_text_is_flagged_by_validate(self):
        data = ledger.initialize(self.catalog)
        row = next(c for c in data['controls'] if c['id'] == 'POST-035')
        row.update(applicability='deferred', rationale='r', owner='o', acceptance='a')
        self.assertTrue(any('needs revisit trigger' in e for e in ledger.validate(data, self.catalog)))


class FilesAndLocks(ProjectCase):
    def test_missing_ledger_leaves_no_lock_file_behind(self):
        ghost = self.root / 'nope.json'
        result = self.cli('triggers', ghost, 'payments')
        self.assertEqual(result.returncode, 2)
        self.assertIn('ledger not found', result.stderr)
        self.assertEqual([p.name for p in self.root.iterdir() if 'nope' in p.name], [])

    def test_read_only_ledger_is_refused_not_silently_rewritten(self):
        self.classify()
        os.chmod(self.ledger, 0o444)
        try:
            if os.access(self.ledger, os.W_OK):
                self.skipTest('running as a user that ignores file modes')
            before = self.ledger.read_bytes()
            result = self.cli('classify', self.ledger, 'POST-011', '--applicability', 'required-now', '--rationale', 'r',
                              '--owner', 'o', '--acceptance', 'a')
            self.assertEqual(result.returncode, 2)
            self.assertIn('read-only', result.stderr)
            self.assertEqual(self.ledger.read_bytes(), before)
        finally:
            os.chmod(self.ledger, 0o644)

    def test_git_revision_ignores_the_engineering_folder(self):
        if not shutil.which('git'):
            self.skipTest('git not installed')
        git = lambda *a: subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t', *a], cwd=self.root,
                                        capture_output=True, text=True, check=True)
        git('init', '-q')
        git('add', 'src', 'proof.txt')
        git('commit', '-q', '-m', 'init')
        short = git('rev-parse', '--short', 'HEAD').stdout.strip()
        self.assertEqual(ledger.git_revision(self.root), short, '.engineering/ is untracked and must not count as dirty')
        (self.root / 'src' / 'auth.py').write_text('edited\n')
        self.assertEqual(ledger.git_revision(self.root), short + '+dirty')


class ReportRules(ProjectCase):
    def test_report_refuses_a_structurally_invalid_ledger(self):
        data = self.data()
        data['schema_version'] = 99
        self.ledger.write_text(json.dumps(data))
        out = self.cli('report', self.ledger)
        self.assertEqual(out.returncode, 2)
        self.assertIn('structural errors', out.stderr)
        self.assertEqual(out.stdout, '')

    def test_report_refuses_fake_verified_rows(self):
        data = self.data()
        row = next(c for c in data['controls'] if c['id'] == 'POST-010')
        row.update(rationale='r', owner='o', acceptance='a', status='verified', evidence=[evidence(check='trust me')])
        self.ledger.write_text(json.dumps(data))
        self.assertEqual(self.cli('report', self.ledger).returncode, 2)

    def test_recorded_text_cannot_break_the_table_or_inject_headings(self):
        self.classify()
        self.assertEqual(self.verify(environment='lab a|b\n## FAKE HEADING\n| x | y |').returncode, 0)
        text = self.cli('report', self.ledger, '--scope', 'scope\n## ALSO FAKE').stdout
        for line in text.splitlines():
            self.assertFalse(line.startswith('## FAKE') or line.startswith('## ALSO'), line)
        row = next(l for l in text.splitlines() if l.startswith('| POST-010'))
        self.assertEqual(len(row.replace('\\|', '').split('|')), 9, row)  # 7 cells, 8 separators plus the edges
        self.assertIn('a\\|b', row)

    def test_exclusions_are_listed_with_acceptance_status(self):
        self.cli('triggers', self.ledger, 'ai')
        base = ('--rationale', 'no AI yet', '--owner', 'o', '--acceptance', 'later')
        self.cli('classify', self.ledger, 'POST-114', '--applicability', 'deferred', *base, '--revisit-trigger', 'before model calls')
        self.cli('classify', self.ledger, 'POST-012', '--applicability', 'not-applicable', *base, '--accepted-by', 'Dana')
        text = self.cli('report', self.ledger).stdout
        deferred = next(l for l in text.splitlines() if l.startswith('- POST-114'))
        self.assertIn('NO accepted_by recorded', deferred)
        self.assertIn('revisit: before model calls', deferred)
        self.assertIn('accepted by Dana', next(l for l in text.splitlines() if l.startswith('- POST-012')))
        self.assertNotIn(': \n', text, 'no blank fill-in fields in the report')


class OutputShape(ProjectCase):
    def test_release_lists_actionable_errors_before_the_unclassified_bulk(self):
        self.cli('triggers', self.ledger, 'ai')
        self.cli('classify', self.ledger, 'POST-114', '--applicability', 'deferred', '--rationale', 'r', '--owner', 'o',
                 '--acceptance', 'a', '--revisit-trigger', 'later')
        out = json.loads(self.cli('validate', self.ledger, '--release').stdout)
        self.assertGreater(out['errors_total'], 25)
        self.assertNotIn('unclassified (', out['errors'][0])
        self.assertIn('accepted_by', out['errors'][0])

    def test_brief_exposes_triggers_so_always_rows_are_visible(self):
        rows = {r['id']: r for r in json.loads(run('relevant', '--triggers', 'multitenant', '--brief').stdout)}
        self.assertIn('always', rows['POST-023']['triggers'])
        self.assertNotIn('always', rows['POST-010']['triggers'])

    def test_top_level_help_lists_every_command(self):
        text = run('-h').stdout
        for command in ledger.COMMANDS:
            self.assertRegex(text, rf'\b{command}\b')
        parser = ledger.build_parser()
        action = next(a for a in parser._actions if getattr(a, 'choices', None))
        described = {choice.dest for choice in action._choices_actions}
        self.assertEqual(described, set(ledger.COMMANDS), 'every subcommand needs a help line')



class SoloBuilder(ProjectCase):
    """A solo builder must be able to verify the always-on review and test controls honestly."""

    def test_always_review_and_test_controls_point_at_the_solo_caveat(self):
        meta = {c['id']: c for c in self.catalog['controls']}
        for ident in ('POST-090', 'POST-133'):
            self.assertIn('always', meta[ident]['triggers'])
            self.assertIn('N-SOLO-REVIEW', meta[ident]['notes'])
            self.assertNotIn('someone who', meta[ident]['concern'])
        brief = self.cli('relevant', '--triggers', 'database', '--brief')
        self.assertEqual(brief.returncode, 0)
        self.assertIn('self-review', brief.stdout)

    def test_self_review_evidence_clears_the_always_controls(self):
        for ident in ('POST-090', 'POST-133'):
            self.assertEqual(self.classify(ident).returncode, 0, ident)
            self.assertEqual(self.verify(ident, verifier='self-review: Andy').returncode, 0, ident)
        statuses = {self.row(i)['status'] for i in ('POST-090', 'POST-133')}
        self.assertEqual(statuses, {'verified'})
        self.assertEqual(self.cli('validate', self.ledger).returncode, 0)

    def test_skill_forbids_unrequested_api_surface(self):
        text = (Path(ledger.__file__).resolve().parent.parent / 'SKILL.md').read_text()
        self.assertIn('did not ask for', text)
        self.assertIn('idempotency key', text)

    def test_skill_core_rules_lead_and_unattended_mode_exists(self):
        """2.7.0: trial evidence showed weaker models staging live steps and leaving no tests; keep those rules up front."""
        text = (Path(ledger.__file__).resolve().parent.parent / 'SKILL.md').read_text()
        self.assertLess(text.index('## Core rules'), text.index('## Choose a mode'))
        for phrase in ('Do not stage a gated step', 'Leave them in the project', '## Unattended mode',
                       'Decisions waiting for you', 'treat an unknown target as live',
                       # 2.7.1, from trial 7: paid calls hid in the project's own tests; scratch copies went to /tmp.
                       "the project's own tests and scripts", 'Keep scratch copies'):
            self.assertIn(phrase, text)



class CatalogBalance(unittest.TestCase):
    """2.5.0 rebalance: thin high-risk areas expanded with ASVS-sourced rows; org-scale ops split out."""

    def setUp(self):
        self.controls = ledger.load_catalog()['controls']

    def tagged(self, trigger):
        return [c for c in self.controls if trigger in c['triggers'] and c.get('kind') != 'responsibility']

    def test_high_risk_triggers_have_asvs_backed_rows(self):
        for trigger, minimum in (('multitenant', 8), ('uploads', 6)):
            rows = self.tagged(trigger)
            self.assertGreaterEqual(len(rows), minimum, trigger)
            self.assertTrue(any(r.get('refs') for r in rows), trigger)
        for c in self.controls:
            for ref in c.get('refs', []):
                self.assertRegex(ref, r'^ASVS 5\.0 (V\d+(\.\d+)?|\d+\.\d+\.\d+)$', c['id'])

    def test_org_scale_operations_do_not_load_for_a_solo_ops_slice(self):
        ops = {c['id'] for c in self.tagged('ops')}
        for ident in ('POST-086', 'POST-089', 'POST-143', 'POST-144'):
            self.assertNotIn(ident, ops)
            self.assertIn(ident, {c['id'] for c in self.tagged('org')})



class QAFindings(ProjectCase):
    """Fixes for the 2.5.0 independent QA pass."""

    def test_upgrade_brings_an_old_ledger_up_to_the_catalog(self):
        data = self.data()
        data['controls'] = [c for c in data['controls'] if c['id'] != 'EXT-021']
        data['catalog_sha256'] = 'old'
        self.ledger.write_text(json.dumps(data))
        stuck = self.cli('validate', self.ledger)
        self.assertEqual(stuck.returncode, 1)
        self.assertIn('ledger.py upgrade', stuck.stdout + stuck.stderr)
        self.assertEqual(self.classify().returncode, 0)  # an existing decision must survive the upgrade
        result = self.cli('upgrade', self.ledger)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['added'], ['EXT-021'])
        self.assertEqual(self.row('POST-010')['applicability'], 'required-now')
        self.assertEqual(self.cli('validate', self.ledger, '--structure-only').returncode, 0)
        self.assertEqual(json.loads(self.cli('upgrade', self.ledger).stdout)['added'], [])

    def test_upgrade_refuses_ids_the_catalog_dropped(self):
        data = self.data()
        data['controls'].append(dict(data['controls'][0], id='POST-999'))
        self.ledger.write_text(json.dumps(data))
        before = self.ledger.read_bytes()
        refused = self.cli('upgrade', self.ledger)
        self.assertEqual(refused.returncode, 2)
        self.assertIn('catalog no longer lists', refused.stderr)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_review_control_needs_a_labeled_verifier_and_report_shows_it(self):
        self.assertEqual(self.classify('POST-133').returncode, 0)
        refused = self.verify('POST-133', verifier='Andrew, senior engineer')
        self.assertEqual(refused.returncode, 2)
        self.assertIn('self-review:', refused.stderr)
        self.assertEqual(self.verify('POST-133', verifier='self-review: Andy').returncode, 0)
        self.assertEqual(self.cli('classify', self.ledger, 'POST-133', '--accepted-by', 'Andy').returncode, 0)
        report = self.cli('report', self.ledger, '--scope', 'local').stdout
        self.assertIn('| self-review: Andy | Andy |', report)
        self.assertIn('POST-133 was self-reviewed', report)

    def test_slice_surfaces_validate_warnings_and_open_always_rows(self):
        self.assertEqual(self.cli('triggers', self.ledger, 'payments').returncode, 0)
        self.assertEqual(self.cli('classify', self.ledger, 'POST-090', '--applicability', 'deferred', '--rationale', 'later',
                                  '--owner', 'me', '--acceptance', 'tests bite', '--revisit-trigger', 'before launch').returncode, 0)
        data = self.data()
        data['catalog_sha256'] = 'old'
        self.ledger.write_text(json.dumps(data))
        result = self.cli('slice', self.ledger, '--triggers', 'frontend')
        self.assertEqual(result.returncode, 3, result.stdout)
        self.assertIn('POST-090 (always-on but deferred with no accepted_by)', result.stdout)
        self.assertIn('catalog hash', result.stdout)
        self.assertIn('share nothing with the declared product triggers', result.stdout)

    def test_slice_refuses_a_structurally_broken_ledger(self):
        data = self.data()
        data['controls'] = data['controls'][1:]
        self.ledger.write_text(json.dumps(data))
        result = self.cli('slice', self.ledger, '--triggers', 'uploads')
        self.assertEqual(result.returncode, 1)
        self.assertIn('no verdict', result.stdout)



class QAFindingsRound2(ProjectCase):
    """Second QA pass on 2.6.0: laundering via `independent:`, and upgrade dropping rows."""

    def test_review_label_needs_a_name_and_independent_needs_accepted_by_at_release(self):
        self.assertEqual(self.classify('POST-133').returncode, 0)
        for bad in ('self-review:', 'independent:  ', 'Andrew'):
            self.assertEqual(self.verify('POST-133', verifier=bad).returncode, 2, bad)
        self.assertEqual(self.verify('POST-133', verifier='independent: claude agent').returncode, 0)
        errors = ledger.validate(self.data(), self.catalog, release=True)
        self.assertTrue(any('independent review needs accepted_by' in e for e in errors), errors[:5])
        self.assertIn('claims independent review but no accepted_by', self.cli('report', self.ledger).stdout)

    def test_hand_edited_review_verifier_fails_validate(self):
        self.assertEqual(self.classify('POST-133').returncode, 0)
        self.assertEqual(self.verify('POST-133', verifier='self-review: Andy').returncode, 0)
        data = self.data()
        self.row('POST-133', data)['evidence'][-1]['verifier'] = 'Andrew'
        self.assertTrue(any('POST-133 needs a verifier' in e for e in ledger.validate(data, self.catalog)))

    def test_upgrade_refuses_duplicates_and_corrupted_rows(self):
        good = self.data()
        for broken in (good['controls'] + [dict(good['controls'][0])], good['controls'] + ['garbage'],
                       good['controls'] + [{'no': 'id'}], {}):
            data = dict(good, controls=broken)
            self.ledger.write_text(json.dumps(data))
            before = self.ledger.read_bytes()
            result = self.cli('upgrade', self.ledger)
            self.assertEqual(result.returncode, 2, repr(broken)[-40:])
            self.assertEqual(self.ledger.read_bytes(), before)

    def test_blank_accepted_by_is_refused(self):
        self.assertEqual(self.classify().returncode, 0)
        self.assertEqual(self.cli('classify', self.ledger, 'POST-010', '--accepted-by', '   ').returncode, 2)

    def test_slice_flags_its_own_excluded_rows(self):
        self.assertEqual(self.cli('classify', self.ledger, 'EXT-018', '--applicability', 'deferred', '--rationale', 'later',
                                  '--owner', 'me', '--acceptance', 'magic bytes test', '--revisit-trigger', 'before launch').returncode, 0)
        out = self.cli('slice', self.ledger, '--triggers', 'uploads').stdout
        self.assertIn("excluded by this slice's own triggers with no accepted_by: EXT-018 (deferred)", out)


if __name__ == '__main__':
    unittest.main(verbosity=2)

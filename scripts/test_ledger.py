import copy
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import ledger

CLI = [sys.executable, str(Path(ledger.__file__))]
SKILL_DIR = Path(ledger.__file__).resolve().parents[1]


def run(*args):
    return subprocess.run(CLI + [str(a) for a in args], capture_output=True, text=True)


def run_in(cwd, *args):
    return subprocess.run(CLI + [str(a) for a in args], capture_output=True, text=True, cwd=str(cwd))


def classify(row, **extra):
    row.update(rationale='Fixture scope for ' + row['id'], owner='QA', acceptance='Fixture procedure for ' + row['id'],
               accepted_by='Fixture owner', revisit_trigger='Fixture revisit trigger')
    row.update(extra)


def evidence(**over):
    base = dict(check='Fixture check', environment='disposable fixture', result='passed', artifact='fixture.txt',
                revision='fixture-revision', timestamp='2026-01-01T00:00:00Z', verifier='unit test')
    base.update(over)
    return base


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = ledger.load_catalog()

    def test_counts_and_uniqueness(self):
        controls = self.catalog['controls']
        self.assertEqual(len(controls), 181)
        self.assertEqual(len({c['id'] for c in controls}), 181)
        self.assertEqual(len([c for c in controls if c['source'] == 'post']), 160)
        self.assertEqual(self.catalog['source_bullet_count'], 160)

    def test_every_control_is_triaged(self):
        vocab = set(self.catalog['trigger_vocabulary'])
        for c in self.catalog['controls']:
            self.assertTrue(c['triggers'], c['id'])
            self.assertLessEqual(set(c['triggers']), vocab, c['id'])
            self.assertTrue(c['domain'], c['id'])

    def test_responsibility_lanes_map_to_real_controls(self):
        ids = {c['id'] for c in self.catalog['controls']}
        lanes = [c for c in self.catalog['controls'] if c['kind'] == 'responsibility']
        self.assertEqual(len(lanes), 13)
        for lane in lanes:
            self.assertTrue(lane['maps_to'], lane['id'])
            self.assertLessEqual(set(lane['maps_to']), ids)
            self.assertTrue(all(self.catalog_kind(t) == 'control' for t in lane['maps_to']))

    def catalog_kind(self, ident):
        return next(c['kind'] for c in self.catalog['controls'] if c['id'] == ident)

    def test_note_keys_resolve_to_interpretation_text(self):
        notes = ledger.load_notes()
        used = {k for c in self.catalog['controls'] for k in c['notes']}
        self.assertEqual(used - set(notes), set())
        self.assertEqual(set(notes) - used, set(), 'interpretation caveat not linked to any control')

    def test_risky_controls_carry_their_caveat(self):
        by_id = {c['id']: c for c in self.catalog['controls']}
        self.assertIn('N-HIPAA', by_id['POST-053']['notes'])
        self.assertIn('N-SOC2', by_id['POST-052']['notes'])
        self.assertIn('N-MIGRATION', by_id['POST-015']['notes'])
        self.assertIn('N-MONEY', by_id['POST-027']['notes'])
        self.assertIn('N-CORS', by_id['POST-043']['notes'])
        self.assertIn('N-DELETION', by_id['POST-057']['notes'])


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.catalog = ledger.load_catalog()
        self.data = ledger.initialize(self.catalog)

    def full(self):
        data = ledger.initialize(self.catalog)
        data['project']['triggers'] = ['payments']
        for c in data['controls']:
            classify(c)
        return data

    def test_initialized_is_unverified_and_unclassified(self):
        self.assertTrue(all(c['status'] == 'unverified' for c in self.data['controls']))
        self.assertTrue(all(ledger.is_unclassified(c) for c in self.data['controls']))

    def test_blank_ledger_valid_in_slice_mode_invalid_for_release(self):
        self.assertEqual(ledger.validate(self.data, self.catalog), [])
        errors = ledger.validate(self.data, self.catalog, release=True)
        self.assertEqual(len([e for e in errors if 'unclassified' in e]), 168)  # 181 minus 13 work lanes
        self.assertFalse(any('POST-148' in e for e in errors))
        self.assertTrue(any('requires project triggers' in e for e in errors))

    def test_complete_metadata_passes_release_structure(self):
        self.assertEqual(ledger.validate(self.full(), self.catalog, release=True), [])

    def test_half_filled_row_rejected_even_in_slice_mode(self):
        self.data['controls'][0]['rationale'] = 'only a rationale'
        errors = ledger.validate(self.data, self.catalog)
        self.assertTrue(any('missing owner' in e for e in errors))

    def test_omitted_and_duplicate_rows_rejected(self):
        data = copy.deepcopy(self.data)
        data['controls'].pop()
        self.assertTrue(any('catalog coverage mismatch' in e for e in ledger.validate(data, self.catalog)))
        data = copy.deepcopy(self.data)
        data['controls'].append(copy.deepcopy(data['controls'][0]))
        self.assertIn('duplicate control IDs', ledger.validate(data, self.catalog))

    def test_false_verified_rejected(self):
        self.data['controls'][0]['status'] = 'verified'
        self.assertIn('POST-001: verified without evidence', ledger.validate(self.data, self.catalog))

    def test_verified_evidence_fields_and_result(self):
        c = self.data['controls'][0]
        classify(c, status='verified', evidence=[evidence()])
        self.assertEqual(ledger.validate(self.data, self.catalog), [])
        c['evidence'][0]['result'] = 'failed'
        self.assertTrue(ledger.validate(self.data, self.catalog))

    def test_deferral_trigger_and_na_rationale_required(self):
        c = self.data['controls'][0]
        classify(c, applicability='deferred', revisit_trigger='')
        self.assertIn('POST-001: needs revisit trigger', ledger.validate(self.data, self.catalog))
        classify(c, applicability='conditional', revisit_trigger=' ')
        self.assertIn('POST-001: needs revisit trigger', ledger.validate(self.data, self.catalog))
        classify(c, applicability='not-applicable', rationale='')
        self.assertIn('POST-001: missing rationale', ledger.validate(self.data, self.catalog))

    def test_invalid_input_shape(self):
        self.assertIn('each control must be an object', ledger.validate({'schema_version': 2, 'controls': [None]}, self.catalog))
        self.assertIn('controls must be an array', ledger.validate({'schema_version': 2}, self.catalog))

    def test_unknown_project_trigger_rejected(self):
        self.data['project']['triggers'] = ['not-a-trigger']
        self.assertTrue(any('unknown triggers' in e for e in ledger.validate(self.data, self.catalog)))

    def test_blockers_exclude_responsibility_lanes(self):
        lane = next(c for c in self.data['controls'] if c['id'] == 'POST-148')
        classify(lane, applicability='required-now')
        self.assertEqual(ledger.blockers(self.data, self.catalog), [])
        control = next(c for c in self.data['controls'] if c['id'] == 'POST-010')
        classify(control, applicability='required-now')
        self.assertEqual(ledger.blockers(self.data, self.catalog), ['POST-010'])

    def test_boilerplate_and_na_on_triggered_warn(self):
        data = self.full()
        for c in data['controls']:
            c['rationale'] = 'Not needed for this app'
        self.assertTrue(any('repeated on' in w for w in ledger.warnings_for(data, self.catalog)))
        data['project']['triggers'] = ['multitenant']
        row = next(c for c in data['controls'] if c['id'] == 'POST-011')
        row['applicability'] = 'not-applicable'
        self.assertTrue(any(w.startswith('POST-011') for w in ledger.warnings_for(data, self.catalog)))

    def test_stale_catalog_hash_warns(self):
        self.data['catalog_sha256'] = '0' * 64
        self.assertTrue(any('catalog hash' in w for w in ledger.warnings_for(self.data, self.catalog)))

    def test_relevant_always_plus_triggers_and_caveats(self):
        ids = {r['id'] for r in ledger.relevant(self.data, self.catalog, [])}
        self.assertIn('POST-036', ids)
        self.assertNotIn('POST-027', ids)
        rows = ledger.relevant(self.data, self.catalog, ['payments', 'regulated'])
        by_id = {r['id']: r for r in rows}
        self.assertIn('POST-027', by_id)
        self.assertTrue(by_id['POST-053']['caveats'] and 'HIPAA' in by_id['POST-053']['caveats'][0])
        self.assertFalse(any(r['kind'] == 'responsibility' for r in rows))

    def test_next_batch_prioritizes_triggered_rows(self):
        batch = ledger.next_batch(self.data, self.catalog, 5, ['payments'])
        self.assertEqual(len(batch), 5)
        self.assertTrue(all(b['relevant_now'] for b in batch))
        done = next(c for c in self.data['controls'] if c['id'] == batch[0]['id'])
        classify(done)
        self.assertNotIn(batch[0]['id'], [b['id'] for b in ledger.next_batch(self.data, self.catalog, 200, [])])


class HardeningTests(unittest.TestCase):
    """Regressions for false-pass paths found in QA."""
    def setUp(self):
        self.catalog = ledger.load_catalog()
        self.data = ledger.initialize(self.catalog)

    def row(self, ident):
        return next(c for c in self.data['controls'] if c['id'] == ident)

    def test_required_now_row_cannot_be_hidden_by_blanking_it(self):
        row = self.row('POST-010')
        classify(row, applicability='required-now', revisit_trigger='')
        for field in ('rationale', 'owner', 'acceptance'):
            row[field] = ''
        row['applicability'] = 'conditional'
        errors = ledger.validate(self.data, self.catalog)
        self.assertTrue(any('POST-010: missing rationale' in e for e in errors))

    def test_pristine_relevant_rows_are_surfaced(self):
        self.data['project']['triggers'] = ['multitenant']
        self.assertIn('POST-011', ledger.unclassified_relevant(self.data, self.catalog))
        classify(self.row('POST-011'))
        self.assertNotIn('POST-011', ledger.unclassified_relevant(self.data, self.catalog))

    def test_junk_timestamp_is_an_error(self):
        classify(self.row('POST-010'), status='verified', evidence=[evidence(timestamp='not-a-date')])
        self.assertTrue(any('ISO-8601' in e for e in ledger.validate(self.data, self.catalog)))

    def test_release_rejects_not_applicable_on_always_or_triggered_control(self):
        self.data['project']['triggers'] = ['multitenant']
        for c in self.data['controls']:
            classify(c)
        self.assertEqual(ledger.validate(self.data, self.catalog, release=True), [])
        classify(self.row('POST-023'), applicability='not-applicable')
        errors = ledger.validate(self.data, self.catalog, release=True)
        self.assertIn('POST-023: an always-on control cannot be not-applicable', errors)
        classify(self.row('POST-023'), applicability='conditional')
        classify(self.row('POST-011'), applicability='not-applicable', accepted_by='')
        errors = ledger.validate(self.data, self.catalog, release=True)
        self.assertTrue(any(e.startswith('POST-011') and 'needs accepted_by' in e for e in errors))
        classify(self.row('POST-011'), applicability='not-applicable', accepted_by='Dana (owner)')
        self.assertEqual(ledger.validate(self.data, self.catalog, release=True), [])

    def test_slice_mode_warns_for_not_applicable_and_deferred_on_always_rows(self):
        classify(self.row('POST-023'), applicability='deferred')
        self.assertTrue(any(w.startswith('POST-023') for w in ledger.warnings_for(self.data, self.catalog)))

    def test_malformed_shapes_return_errors_not_exceptions(self):
        for project in ('abc', None, [1], {'triggers': 'payments'}, {'triggers': {'payments': 1}}):
            data = copy.deepcopy(self.data)
            data['project'] = project
            self.assertTrue(ledger.validate(data, self.catalog), project)
            ledger.warnings_for(data, self.catalog)
            ledger.summary(data, self.catalog)
        self.assertEqual(ledger.validate([1], self.catalog), ['ledger must be a JSON object'])

    def test_unhashable_fields_do_not_crash(self):
        data = copy.deepcopy(self.data)
        data['controls'][0]['id'] = ['a']
        self.assertTrue(ledger.validate(data, self.catalog))
        ledger.blockers(data, self.catalog)
        data = copy.deepcopy(self.data)
        data['controls'][0]['applicability'] = ['a']
        data['controls'][0]['status'] = {'a': 1}
        self.assertTrue(ledger.validate(data, self.catalog))
        ledger.summary(data, self.catalog)

    def test_bad_schema_version_rejected(self):
        self.data['schema_version'] = 'banana'
        self.assertTrue(any('schema_version' in e for e in ledger.validate(self.data, self.catalog)))

    def test_garbage_evidence_rejected_on_any_status(self):
        classify(self.row('POST-010'), status='partial', evidence=['junk'])
        self.assertTrue(ledger.validate(self.data, self.catalog))

    def test_parse_triggers_strips_and_rejects_unknown(self):
        self.assertEqual(ledger.parse_triggers('payments, ai ', self.catalog), ['payments', 'ai'])
        with self.assertRaises(ValueError):
            ledger.parse_triggers('paymnets', self.catalog)

    def test_missing_caveat_text_is_visible(self):
        meta = next(c for c in self.catalog['controls'] if c['id'] == 'POST-053')
        out = ledger.describe(meta, self.row('POST-053'), {})
        self.assertIn('missing', out['caveats'][0])

    def test_note_parser_accepts_indent_and_rejects_duplicates(self):
        original = ledger.INTERPRETATION
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'i.md'
            ledger.INTERPRETATION = path
            try:
                path.write_text('  - [N-A] first  \r\n- [N-B] second\n')
                self.assertEqual(ledger.load_notes(), {'N-A': 'first', 'N-B': 'second'})
                path.write_text('- [N-A] one\n- [N-A] two\n')
                with self.assertRaises(ValueError):
                    ledger.load_notes()
            finally:
                ledger.INTERPRETATION = original

    def test_atomic_write_keeps_mode_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / 'real.json'
            link = Path(tmp) / 'link.json'
            real.write_text('{}')
            real.chmod(0o664)
            link.symlink_to(real)
            ledger.atomic_write(link, {'a': 1})
            self.assertTrue(link.is_symlink())
            self.assertEqual(json.loads(real.read_text()), {'a': 1})
            self.assertEqual(real.stat().st_mode & 0o777, 0o664)
            self.assertEqual([p.name for p in Path(tmp).iterdir() if p.name.startswith('.ledger-')], [])


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.catalog = ledger.load_catalog()
        self.data = ledger.initialize(self.catalog)
        self.data['project']['triggers'] = ['payments']
        for c in self.data['controls']:
            classify(c)

    def row(self, ident):
        return next(c for c in self.data['controls'] if c['id'] == ident)

    def test_deferred_or_conditional_on_triggered_control_needs_accepted_by_at_release(self):
        for app in ('deferred', 'conditional', 'not-applicable'):
            classify(self.row('POST-030'), applicability=app, accepted_by='')
            errors = ledger.validate(self.data, self.catalog, release=True)
            self.assertTrue(any(e.startswith('POST-030') and 'accepted_by' in e for e in errors), app)
        self.assertEqual(ledger.validate(self.data, self.catalog), [], 'slice mode does not require accepted_by')

    def test_deferring_every_triggered_control_cannot_pass_release(self):
        triggered = {m['id'] for m in self.catalog['controls'] if m['kind'] == 'control' and
                     ({'payments', 'always'} & set(m['triggers']))}
        for c in self.data['controls']:
            if c['id'] in triggered:
                classify(c, applicability='deferred', accepted_by='')
        errors = ledger.validate(self.data, self.catalog, release=True)
        self.assertGreaterEqual(len([e for e in errors if 'needs accepted_by' in e]), len(triggered) - 8)

    def test_untriggered_deferral_needs_no_acceptance(self):
        classify(self.row('POST-114'), applicability='deferred', accepted_by='')  # ai, not declared
        self.assertEqual(ledger.validate(self.data, self.catalog, release=True), [])

    def test_lane_deferral_needs_no_acceptance(self):
        self.data['project']['triggers'] = ['team']
        classify(self.row('POST-148'), applicability='deferred', accepted_by='')
        errors = ledger.validate(self.data, self.catalog, release=True)
        self.assertFalse(any(e.startswith('POST-148') for e in errors))

    def test_future_timestamp_is_an_error(self):
        classify(self.row('POST-010'), status='verified', evidence=[evidence(timestamp='2099-01-01T00:00:00Z')])
        self.assertIn('POST-010: evidence timestamp is in the future', ledger.validate(self.data, self.catalog))

    def test_missing_artifact_errors_at_release_only_and_warns_in_slice(self):
        classify(self.row('POST-010'), status='verified', evidence=[evidence(artifact='nowhere/proof.txt')])
        with tempfile.TemporaryDirectory() as tmp:
            roots = [Path(tmp)]
            self.assertNotIn('artifact not found', ' '.join(ledger.validate(self.data, self.catalog, artifact_roots=roots)))
            self.assertTrue(any('artifact not found' in w for w in ledger.warnings_for(self.data, self.catalog, roots)))
            self.assertTrue(any('artifact not found' in e for e in ledger.validate(self.data, self.catalog, release=True, artifact_roots=roots)))
            (Path(tmp) / 'nowhere').mkdir()
            (Path(tmp) / 'nowhere' / 'proof.txt').write_text('ok')
            self.assertFalse(any('artifact not found' in e for e in ledger.validate(self.data, self.catalog, release=True, artifact_roots=roots)))
        classify(self.row('POST-010'), status='verified', evidence=[evidence(artifact='https://ci.example/run/1')])
        self.assertFalse(any('artifact not found' in e for e in ledger.validate(self.data, self.catalog, release=True, artifact_roots=[Path('/nonexistent')])))

    def test_cli_caps_output_and_verbose_shows_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ledger.json'
            self.assertEqual(run('init', '--out', path).returncode, 0)
            capped = json.loads(run('validate', path, '--release').stdout)
            self.assertEqual(capped['errors_total'], len(capped['errors']) + (capped['errors_total'] - len(capped['errors'])))
            self.assertEqual(len(capped['errors']), ledger.OUTPUT_CAP)
            self.assertTrue(capped['output_truncated'])
            full = json.loads(run('validate', path, '--release', '--verbose').stdout)
            self.assertEqual(len(full['errors']), full['errors_total'])
            self.assertFalse(full['output_truncated'])

    def test_cli_separates_non_committal_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ledger.json'
            run('init', '--out', path)
            run('triggers', path, 'multitenant')
            data = json.loads(path.read_text())
            classify(next(c for c in data['controls'] if c['id'] == 'POST-011'), applicability='deferred')
            path.write_text(json.dumps(data))
            report = json.loads(run('validate', path).stdout)
            self.assertEqual(report['non_committal_on_triggered_total'], 1)
            self.assertTrue(report['non_committal_on_triggered'][0].startswith('POST-011'))
            self.assertFalse(any('although triggers' in w for w in report['warnings']))


class FinalGapTests(unittest.TestCase):
    """Kills survivors from the second mutation round."""
    def setUp(self):
        self.catalog = ledger.load_catalog()
        self.data = ledger.initialize(self.catalog)
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def row(self, ident):
        return next(c for c in self.data['controls'] if c['id'] == ident)

    def test_release_without_artifact_roots_does_not_crash(self):
        self.data['project']['triggers'] = ['payments']
        classify(self.row('POST-010'), status='verified', evidence=[evidence()])
        self.assertIsInstance(ledger.validate(self.data, self.catalog, release=True), list)

    def test_always_on_deferral_at_release_needs_accepted_by_even_if_blank_space(self):
        self.data['project']['triggers'] = ['payments']
        for c in self.data['controls']:
            classify(c)
        classify(self.row('POST-023'), applicability='deferred', accepted_by=' ')
        errors = ledger.validate(self.data, self.catalog, release=True)
        self.assertTrue(any(e.startswith('POST-023') and 'accepted_by' in e for e in errors))
        classify(self.row('POST-023'), applicability='deferred', accepted_by='Dana')
        self.assertEqual(ledger.validate(self.data, self.catalog, release=True), [])

    def test_unclassified_relevant_semantics(self):
        self.assertEqual(ledger.declared_triggers(self.data), [])
        base = ledger.unclassified_relevant(self.data, self.catalog)
        self.assertIn('POST-023', base)
        self.assertNotIn('POST-011', base)
        self.assertFalse(any(i.startswith('POST-14') or i in {f'POST-{n}' for n in range(148, 161)} for i in base))
        self.assertFalse(any('still unclassified' in w for w in ledger.warnings_for(self.data, self.catalog)))
        self.data['project']['triggers'] = ['multitenant']
        self.assertIn('POST-011', ledger.unclassified_relevant(self.data, self.catalog))
        self.assertTrue(any('still unclassified' in w for w in ledger.warnings_for(self.data, self.catalog)))
        self.assertFalse(any(i in {f'POST-{n}' for n in range(148, 161)} for i in ledger.unclassified_relevant(self.data, self.catalog)))

    def test_required_now_blank_row_is_not_pristine(self):
        row = self.row('POST-010')
        row['applicability'] = 'required-now'
        self.assertFalse(ledger.is_unclassified(row))
        errors = ledger.validate(self.data, self.catalog)
        for field in ('rationale', 'owner', 'acceptance'):
            self.assertIn(f'POST-010: missing {field}', errors)

    def test_owner_only_row_is_not_pristine(self):
        row = self.row('POST-010')
        row['owner'] = 'QA'
        self.assertFalse(ledger.is_unclassified(row))
        errors = ledger.validate(self.data, self.catalog)
        self.assertIn('POST-010: missing rationale', errors)
        self.assertIn('POST-010: missing acceptance', errors)

    def test_non_list_evidence_and_non_string_text_fields(self):
        self.row('POST-010')['evidence'] = 'abc'
        self.assertIn('POST-010: evidence must be an array', ledger.validate(self.data, self.catalog))
        classify(self.row('POST-011'), rationale=5, owner=['x'])
        errors = ledger.validate(self.data, self.catalog)
        self.assertIn('POST-011: missing rationale', errors)
        self.assertIn('POST-011: missing owner', errors)

    def test_future_tolerance_and_whitespace_timestamp(self):
        from datetime import datetime, timedelta, timezone
        def stamp(delta):
            return (datetime.now(timezone.utc) + delta).strftime('%Y-%m-%dT%H:%M:%S+00:00')
        for delta, expect_error in ((timedelta(minutes=2), False), (timedelta(minutes=10), True), (timedelta(days=1), True)):
            data = ledger.initialize(self.catalog)
            row = next(c for c in data['controls'] if c['id'] == 'POST-010')
            classify(row, status='verified', evidence=[evidence(timestamp=stamp(delta))])
            errors = ledger.validate(data, self.catalog)
            self.assertEqual(any('in the future' in e for e in errors), expect_error, delta)
        classify(self.row('POST-010'), status='verified', evidence=[evidence(timestamp=' 2026-01-01T00:00:00Z ')])
        self.assertEqual(ledger.validate(self.data, self.catalog), [])

    def test_relevant_now_false_for_other_rows_and_artifact_warning_only_when_verified(self):
        batch = ledger.next_batch(self.data, self.catalog, 200, ['payments'])
        self.assertTrue(any(b['relevant_now'] is False for b in batch))
        classify(self.row('POST-010'), status='partial', evidence=[evidence(artifact='nowhere.txt')])
        self.assertFalse(any('artifact not found' in w for w in ledger.warnings_for(self.data, self.catalog, [self.base])))

    def test_cli_artifact_paths_resolve_against_the_project_root_only(self):
        project = self.base / 'proj'
        ledger_path = project / '.engineering' / 'ledger.json'
        self.assertEqual(run('init', '--out', ledger_path).returncode, 0)
        data = json.loads(ledger_path.read_text())
        classify(next(c for c in data['controls'] if c['id'] == 'POST-010'), status='verified',
                 evidence=[evidence(artifact='proof.txt')])
        ledger_path.write_text(json.dumps(data))
        elsewhere = self.base / 'elsewhere'
        elsewhere.mkdir()

        def missing():
            out = json.loads(run_in(elsewhere, 'validate', ledger_path).stdout)
            return any('artifact not found' in w for w in out['warnings'])
        self.assertTrue(missing())
        (elsewhere / 'proof.txt').write_text('x')                      # a copy in the cwd must not count
        self.assertTrue(missing())
        (project / '.engineering' / 'proof.txt').write_text('x')       # nor one beside the ledger
        self.assertTrue(missing())
        (project / 'proof.txt').write_text('x')                        # the project root is the one place
        self.assertFalse(missing())

    def test_ledger_outside_an_engineering_folder_uses_its_own_folder_as_root(self):
        path = self.base / 'ledger.json'
        self.assertEqual(ledger.roots_for(path), [self.base.resolve()])
        self.assertEqual(ledger.roots_for(self.base / '.engineering' / 'ledger.json'), [self.base.resolve()])

    def test_cli_validate_reports_unclassified_relevant_and_totals(self):
        path = self.base / 'ledger.json'
        run('init', '--out', path)
        run('triggers', path, 'payments')
        report = json.loads(run('validate', path).stdout)
        self.assertIn('POST-030', report['unclassified_relevant'])
        self.assertEqual(report['unclassified_relevant_total'], len(ledger.unclassified_relevant(json.loads(path.read_text()), self.catalog)))
        self.assertEqual(report['errors_total'], len(report['errors']))
        self.assertEqual(report['warnings_total'], len(report['warnings']))

    def test_cli_relevant_brief_with_ledger_and_non_object_inputs(self):
        path = self.base / 'ledger.json'
        run('init', '--out', path)
        rows = json.loads(run('relevant', path, '--triggers', 'regulated', '--brief').stdout)
        self.assertTrue(set(rows[0]) <= {'id', 'concern', 'triggers', 'classified', 'caveats', 'refs'})
        path.write_text('[]')
        self.assertEqual(run('summary', path).returncode, 2)
        path.write_text('{"schema_version":2,"controls":[1,{"id":"POST-001"}]}')
        result = run('summary', path)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('Traceback', result.stderr)
        path.write_text('{"schema_version":2,"project":{"triggers":[["x"]]},"controls":[]}')
        self.assertEqual(run('validate', path).returncode, 1)

    def test_triggers_repairs_non_object_project(self):
        path = self.base / 'ledger.json'
        run('init', '--out', path)
        data = json.loads(path.read_text())
        data['project'] = 'x'
        path.write_text(json.dumps(data))
        self.assertEqual(run('triggers', path, 'ai').returncode, 0)
        self.assertEqual(json.loads(path.read_text())['project'], {'triggers': ['ai']})

    def test_output_cap_boundary(self):
        path = self.base / 'ledger.json'
        run('init', '--out', path)
        data = json.loads(path.read_text())
        # exactly OUTPUT_CAP release errors beyond the unavoidable ones is impractical; assert both sides instead
        capped = json.loads(run('validate', path, '--release').stdout)
        self.assertEqual(len(capped['errors']), ledger.OUTPUT_CAP)
        self.assertEqual(ledger.OUTPUT_CAP, 25)
        self.assertTrue(capped['output_truncated'])
        self.assertEqual(len(capped['unclassified_relevant']), min(ledger.OUTPUT_CAP, capped['unclassified_relevant_total']))


class MutationGapTests(unittest.TestCase):
    """Pins behavior that mutation testing showed was unguarded."""
    def setUp(self):
        self.catalog = ledger.load_catalog()
        self.data = ledger.initialize(self.catalog)

    def row(self, ident):
        return next(c for c in self.data['controls'] if c['id'] == ident)

    def test_verified_requires_each_evidence_field(self):
        for field in ledger.EVIDENCE_FIELDS:
            data = ledger.initialize(self.catalog)
            row = next(c for c in data['controls'] if c['id'] == 'POST-010')
            classify(row, status='verified', evidence=[evidence(**{field: '  '})])
            errors = ledger.validate(data, self.catalog)
            self.assertIn(f'POST-010: evidence missing {field}', errors, field)

    def test_verified_with_empty_evidence_list_is_rejected_with_reason(self):
        classify(self.row('POST-010'), status='verified', evidence=[])
        self.assertIn('POST-010: verified without evidence', ledger.validate(self.data, self.catalog))

    def test_invalid_applicability_and_status_are_named(self):
        classify(self.row('POST-010'), applicability='maybe', status='done')
        errors = ledger.validate(self.data, self.catalog)
        self.assertIn('POST-010: invalid applicability', errors)
        self.assertIn('POST-010: invalid status', errors)

    def test_whitespace_only_fields_count_as_blank(self):
        classify(self.row('POST-010'), owner='   ')
        self.assertIn('POST-010: missing owner', ledger.validate(self.data, self.catalog))

    def test_evidence_makes_a_row_classified(self):
        row = self.row('POST-010')
        self.assertTrue(ledger.is_unclassified(row))
        row['evidence'] = [evidence()]
        self.assertFalse(ledger.is_unclassified(row))
        row['evidence'] = []
        row['status'] = 'partial'
        self.assertFalse(ledger.is_unclassified(row))

    def test_always_control_cannot_be_not_applicable_even_in_slice_mode(self):
        classify(self.row('POST-023'), applicability='not-applicable')
        self.assertIn('POST-023: an always-on control cannot be not-applicable', ledger.validate(self.data, self.catalog))

    def test_not_applicable_warning_only_for_matching_triggers(self):
        self.data['project']['triggers'] = ['multitenant']
        classify(self.row('POST-013'), applicability='not-applicable')  # accounts, not declared
        classify(self.row('POST-011'), applicability='conditional')      # triggered but not N/A
        warned = [w for w in ledger.warnings_for(self.data, self.catalog)]
        self.assertFalse(any(w.startswith(('POST-013', 'POST-011')) for w in warned))

    def test_boilerplate_threshold_boundary_and_case(self):
        for i, ident in enumerate(('POST-001', 'POST-002', 'POST-003', 'POST-004', 'POST-005')):
            classify(self.row(ident), rationale='SAME reason' if i % 2 else 'same REASON')
        self.assertFalse(any('repeated on' in w for w in ledger.warnings_for(self.data, self.catalog)))
        classify(self.row('POST-006'), rationale='Same Reason')
        self.assertTrue(any('rationale repeated on 6 rows' in w for w in ledger.warnings_for(self.data, self.catalog)))

    def test_boilerplate_also_checks_acceptance(self):
        for ident in ('POST-001', 'POST-002', 'POST-003', 'POST-004', 'POST-005', 'POST-006'):
            classify(self.row(ident), acceptance='Run the tests')
        self.assertTrue(any(w.startswith('acceptance repeated on 6 rows') for w in ledger.warnings_for(self.data, self.catalog)))

    def test_missing_hash_warns_and_matching_hash_does_not(self):
        self.assertFalse(any('catalog hash' in w for w in ledger.warnings_for(self.data, self.catalog)))
        del self.data['catalog_sha256']
        self.assertTrue(any('catalog hash' in w for w in ledger.warnings_for(self.data, self.catalog)))

    def test_describe_fields_for_classified_lane_rows(self):
        notes = ledger.load_notes()
        lane = next(c for c in self.catalog['controls'] if c['id'] == 'POST-148')
        out = ledger.describe(lane, self.row('POST-148'), notes)
        self.assertEqual(out['maps_to'], ['POST-132', 'POST-133'])
        self.assertFalse(out['classified'])
        classify(self.row('POST-148'))
        self.assertTrue(ledger.describe(lane, self.row('POST-148'), notes)['classified'])

    def test_relevant_lanes_flag_and_always_row_presence(self):
        base = {r['id'] for r in ledger.relevant(self.data, self.catalog, ['team'])}
        with_lanes = {r['id'] for r in ledger.relevant(self.data, self.catalog, ['team'], include_lanes=True)}
        self.assertNotIn('POST-148', base)
        self.assertIn('POST-148', with_lanes)
        self.assertIn('POST-023', base)

    def test_next_batch_order_always_and_flags(self):
        batch = ledger.next_batch(self.data, self.catalog, 40, ['payments'])
        flags = [b['relevant_now'] for b in batch]
        self.assertEqual(flags, sorted(flags, reverse=True), 'relevant rows must come first')
        self.assertIn('POST-023', [b['id'] for b in batch if b['relevant_now']])
        self.assertTrue(all(b['classified'] is False for b in batch))
        lanes_first = ledger.next_batch(self.data, self.catalog, 400, [])
        kinds = [b['kind'] for b in lanes_first if not b['relevant_now']]
        self.assertEqual(kinds, sorted(kinds, key=lambda k: k != 'control'), 'controls before lanes among non-relevant rows')

    def test_atomic_write_failure_leaves_original_and_no_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'ledger.json'
            target.write_text('{"keep": true}')
            with self.assertRaises(TypeError):
                ledger.atomic_write(target, {'bad': object()})
            self.assertEqual(json.loads(target.read_text()), {'keep': True})
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ['ledger.json'])

    def test_lanes_do_not_block_release_classification(self):
        self.data['project']['triggers'] = ['payments']
        for c in self.data['controls']:
            if c['id'] not in {f'POST-{n}' for n in range(148, 161)}:
                classify(c)
        self.assertEqual(ledger.validate(self.data, self.catalog, release=True), [])

    def test_stale_marking_is_a_valid_unverified_row_with_old_evidence(self):
        classify(self.row('POST-010'), applicability='required-now', status='unverified',
                 rationale='Stale: auth middleware changed in rev abc123', evidence=[evidence()])
        self.assertEqual(ledger.validate(self.data, self.catalog), [])
        self.assertEqual(ledger.blockers(self.data, self.catalog), ['POST-010'])


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / '.engineering' / 'ledger.json'
        self.assertEqual(run('init', '--out', self.path).returncode, 0)

    def tearDown(self):
        self._tmp.cleanup()

    def load(self):
        return json.loads(self.path.read_text())

    def test_init_refuses_overwrite(self):
        before = self.path.read_bytes()
        again = run('init', '--out', self.path)
        self.assertEqual(again.returncode, 2)
        self.assertIn('File exists', again.stderr)
        self.assertEqual(self.path.read_bytes(), before)

    def test_blank_ledger_slice_vs_release_exit_codes(self):
        slice_run = run('validate', self.path)
        self.assertEqual(slice_run.returncode, 0)
        self.assertEqual(json.loads(slice_run.stdout)['unclassified_count'], 181)
        self.assertEqual(run('validate', self.path, '--release').returncode, 1)

    def test_required_now_unverified_exits_3_unless_structure_only(self):
        data = self.load()
        classify(next(c for c in data['controls'] if c['id'] == 'POST-010'), applicability='required-now')
        self.path.write_text(json.dumps(data))
        blocked = run('validate', self.path)
        self.assertEqual(blocked.returncode, 3)
        report = json.loads(blocked.stdout)
        self.assertEqual(report['required_now_unverified'], ['POST-010'])
        self.assertFalse(report['release_assessed'])
        self.assertEqual(run('validate', self.path, '--structure-only').returncode, 0)

    def test_fully_verified_required_control_passes_and_never_certifies(self):
        data = self.load()
        classify(next(c for c in data['controls'] if c['id'] == 'POST-010'),
                 applicability='required-now', status='verified', evidence=[evidence()])
        self.path.write_text(json.dumps(data))
        result = run('validate', self.path)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)['release_assessed'])

    def test_triggers_command_persists_and_rejects_unknown(self):
        self.assertEqual(run('triggers', self.path, 'payments,ai').returncode, 0)
        self.assertEqual(self.load()['project']['triggers'], ['ai', 'payments'])
        rows = json.loads(run('relevant', self.path).stdout)
        self.assertIn('POST-114', {r['id'] for r in rows})
        before = self.path.read_bytes()
        bad = run('triggers', self.path, 'payments,bogus')
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(self.path.read_bytes(), before)

    def test_summary_and_next_output_json(self):
        self.assertEqual(json.loads(run('summary', self.path).stdout)['unclassified'], 181)
        batch = json.loads(run('next', self.path, '--batch', 3, '--triggers', 'payments').stdout)
        self.assertEqual(len(batch), 3)

    def test_corrupt_ledger_is_operational_error(self):
        self.path.write_text('{not json')
        self.assertEqual(run('validate', self.path).returncode, 2)

    def test_vocabulary_lists_triggers(self):
        self.assertIn('payments', json.loads(run('vocabulary').stdout))


class CliHardeningTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / 'ledger.json'
        self.assertEqual(run('init', '--out', self.path).returncode, 0)

    def tearDown(self):
        self._tmp.cleanup()

    def test_malformed_ledgers_never_traceback(self):
        for text, code in (('[1]', 2), ('5', 2), ('{"schema_version":2,"project":"abc","controls":[]}', 1),
                           ('{"schema_version":2,"controls":[{"id":["a"]}]}', 1)):
            self.path.write_text(text)
            result = run('validate', self.path)
            self.assertEqual(result.returncode, code, text)
            self.assertNotIn('Traceback', result.stderr, text)

    def test_empty_triggers_rejected_and_clear_is_explicit(self):
        run('triggers', self.path, 'payments')
        for bad in ('', ' ', ','):
            self.assertEqual(run('triggers', self.path, bad).returncode, 2, repr(bad))
        self.assertEqual(json.loads(self.path.read_text())['project']['triggers'], ['payments'])
        self.assertEqual(run('triggers', self.path, '--clear').returncode, 0)
        self.assertEqual(json.loads(self.path.read_text())['project']['triggers'], [])
        self.assertEqual(run('triggers', self.path, 'ai', '--clear').returncode, 2)

    def test_triggers_whitespace_is_trimmed(self):
        self.assertEqual(run('triggers', self.path, 'payments, ai').returncode, 0)
        self.assertEqual(json.loads(self.path.read_text())['project']['triggers'], ['ai', 'payments'])

    def test_unknown_trigger_override_is_error_not_silent(self):
        self.assertEqual(run('relevant', self.path, '--triggers', 'paymnets').returncode, 2)
        self.assertEqual(run('next', self.path, '--triggers', 'paymnets').returncode, 2)
        same = run('relevant', self.path, '--triggers', 'payments, ai')
        self.assertEqual(same.returncode, 0)

    def test_batch_below_one_rejected(self):
        self.assertNotEqual(run('next', self.path, '--batch', 0).returncode, 0)
        self.assertNotEqual(run('next', self.path, '--batch', -3).returncode, 0)

    def test_release_and_structure_only_are_exclusive(self):
        both = run('validate', self.path, '--release', '--structure-only')
        self.assertEqual(both.returncode, 2)
        self.assertIn('not allowed with', both.stderr)

    def test_summary_lists_unclassified_relevant(self):
        run('triggers', self.path, 'multitenant')
        report = json.loads(run('summary', self.path).stdout)
        self.assertIn('POST-011', report['unclassified_relevant'])

    def test_relevant_output_carries_caveat_text(self):
        run('triggers', self.path, 'regulated')
        rows = {r['id']: r for r in json.loads(run('relevant', self.path).stdout)}
        self.assertIn('HIPAA', rows['POST-053']['caveats'][0])


class CliOutputTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / 'ledger.json'

    def tearDown(self):
        self._tmp.cleanup()

    def test_init_reports_count_and_validate_reports_mode_and_note(self):
        out = json.loads(run('init', '--out', self.path).stdout)
        self.assertEqual(out['initialized'], 181)
        for args, mode in (((), 'slice'), (('--structure-only',), 'structure-only')):
            report = json.loads(run('validate', self.path, *args).stdout)
            self.assertEqual(report['mode'], mode)
            self.assertIn('Metadata validation only', report['note'])
        release = json.loads(run('validate', self.path, '--release').stdout)
        self.assertEqual(release['mode'], 'release')

    def test_summary_fields(self):
        run('init', '--out', self.path)
        run('triggers', self.path, 'payments')
        data = json.loads(self.path.read_text())
        classify(next(c for c in data['controls'] if c['id'] == 'POST-010'), applicability='required-now')
        self.path.write_text(json.dumps(data))
        report = json.loads(run('summary', self.path).stdout)
        self.assertEqual(report['project_triggers'], ['payments'])
        self.assertEqual(report['required_now_unverified'], ['POST-010'])
        self.assertEqual(report['by_status']['unverified'], 181)
        self.assertEqual(report['by_applicability']['required-now'], 1)
        self.assertEqual(report['total'], 181)

    def test_relevant_triggers_override_and_ledger_default_and_include_lanes(self):
        run('init', '--out', self.path)
        run('triggers', self.path, 'payments')
        default_ids = {r['id'] for r in json.loads(run('relevant', self.path).stdout)}
        override_ids = {r['id'] for r in json.loads(run('relevant', self.path, '--triggers', 'ai').stdout)}
        self.assertIn('POST-030', default_ids)
        self.assertNotIn('POST-030', override_ids)
        self.assertIn('POST-114', override_ids)
        lanes = {r['id'] for r in json.loads(run('relevant', self.path, '--triggers', 'team', '--include-lanes').stdout)}
        self.assertIn('POST-148', lanes)

    def test_next_uses_ledger_triggers_when_no_override(self):
        run('init', '--out', self.path)
        run('triggers', self.path, 'ai')
        batch = json.loads(run('next', self.path, '--batch', 4).stdout)
        self.assertTrue(all(b['relevant_now'] for b in batch))

    def test_relevant_without_ledger_is_read_only_and_needs_triggers(self):
        empty = Path(self._tmp.name) / 'empty'
        empty.mkdir()
        result = run_in(empty, 'relevant', '--triggers', 'payments', '--brief')
        self.assertEqual(result.returncode, 0)
        rows = json.loads(result.stdout)
        self.assertEqual(set(rows[0]), {'id', 'concern', 'triggers', 'classified'} | ({'caveats'} if 'caveats' in rows[0] else set()) | ({'refs'} if 'refs' in rows[0] else set()))
        self.assertEqual(list(empty.iterdir()), [], 'ledger-free relevant must write nothing')
        self.assertEqual(run('relevant').returncode, 2)
        self.assertEqual(run('relevant', '--triggers', 'bogus').returncode, 2)
        lanes = json.loads(run('relevant', '--triggers', 'team', '--include-lanes').stdout)
        self.assertIn('POST-148', {r['id'] for r in lanes})

    def test_brief_is_smaller_and_keeps_caveats(self):
        full = run('relevant', '--triggers', 'regulated').stdout
        short = run('relevant', '--triggers', 'regulated', '--brief').stdout
        self.assertLess(len(short), len(full))
        rows = {r['id']: r for r in json.loads(short)}
        self.assertIn('auditor', rows['POST-052']['caveats'][0])
        self.assertIn('HIPAA', rows['POST-053']['caveats'][0])

    def test_always_not_applicable_fails_validate_via_cli(self):
        run('init', '--out', self.path)
        data = json.loads(self.path.read_text())
        classify(next(c for c in data['controls'] if c['id'] == 'POST-023'), applicability='not-applicable')
        self.path.write_text(json.dumps(data))
        result = run('validate', self.path)
        self.assertEqual(result.returncode, 1)
        self.assertIn('always-on control', result.stdout)


class BundleTests(unittest.TestCase):
    def test_required_files_present(self):
        for rel in ('SKILL.md', 'README.md', 'LICENSE', 'VERSION', 'references/catalog.json', 'references/examples.md',
                    'references/discovery.md', 'references/verification.md', 'references/interpretation.md',
                    'references/foundations.md'):
            self.assertTrue((SKILL_DIR / rel).is_file(), rel)

    def test_verbatim_source_not_bundled(self):
        self.assertFalse((SKILL_DIR / 'references' / 'source-post.txt').exists())

    def test_frontmatter_valid_and_version_consistent(self):
        text = (SKILL_DIR / 'SKILL.md').read_text()
        match = re.match(r'---\nname: (.+)\ndescription: (.+)\n---\n', text)
        self.assertTrue(match)
        self.assertEqual(match.group(1), 'production-dev-team')
        self.assertLessEqual(len(match.group(2)), 1024)
        version = (SKILL_DIR / 'VERSION').read_text().strip()
        self.assertEqual(ledger.load_catalog()['catalog_version'].split('.')[0], version.split('.')[0])

    def test_no_stray_line_number_artifacts(self):
        marker = re.compile(r'^\s+\d+\|', re.M)
        for path in SKILL_DIR.rglob('*'):
            if path.is_file() and path.suffix in {'.md', '.py', '.json', '.txt'} and '__pycache__' not in path.parts:
                self.assertFalse(marker.search(path.read_text()), str(path))

    def test_markdown_references_resolve(self):
        link = re.compile(r'`((?:references|scripts)/[A-Za-z0-9_.\-]+)`')
        for doc in ('SKILL.md', 'README.md'):
            for rel in link.findall((SKILL_DIR / doc).read_text()):
                self.assertTrue((SKILL_DIR / rel).exists(), f'{doc} -> {rel}')


if __name__ == '__main__':
    unittest.main(verbosity=2)

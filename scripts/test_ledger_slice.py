"""Tests for `slice`: the read-only, one-command verdict for the controls one change touches."""
import re
import unittest

from test_ledger import run
from test_ledger_ops import ProjectCase


class SliceTests(ProjectCase):
    def slice(self, *extra):
        return self.cli('slice', self.ledger, '--triggers', 'multitenant', *extra)

    def test_no_ledger_lists_the_slice_and_says_none_exists(self):
        result = run('slice', '--triggers', 'multitenant,team')  # `team` is the trigger that would pull in lanes
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('No ledger given', result.stdout)
        self.assertIn('POST-010', result.stdout)
        self.assertIn('POST-023 [always]', result.stdout)  # always controls come with every slice
        self.assertNotIn('\n- POST-148', result.stdout)  # lanes hidden

    def test_untouched_rows_warn_that_exit_0_is_not_a_pass_and_show_asvs_refs(self):
        result = self.slice()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('exit 0 does not mean they were checked', result.stdout)
        self.assertIn('see ASVS 5.0 8.4.1', result.stdout)  # POST-011 / EXT-015
        brief = run('relevant', '--triggers', 'uploads', '--brief')
        self.assertIn('ASVS 5.0 V5.3', brief.stdout)

    def test_untouched_rows_are_counted_as_not_examined(self):
        result = self.slice('--scope', 'invoice sharing, local')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('POST-011 [multitenant]', result.stdout)
        self.assertIn('state: untouched', result.stdout)
        self.assertIn('caveats: N-SUPPORT-ACCESS', result.stdout)  # POST-012's caveat key
        self.assertIn('Scope: invoice sharing, local', result.stdout)
        total = len(re.findall(r'^- [A-Z]+-\d+ \[', result.stdout, re.M))
        self.assertIn(f'Not examined (untouched): {total} of {total}', result.stdout)

    def test_verified_row_shows_its_environment(self):
        self.classify()
        self.assertEqual(self.verify(environment='local sqlite, two tenants').returncode, 0)
        result = self.slice()
        self.assertIn('verified in local sqlite, two tenants (revision abc1234)', result.stdout)
        self.assertIn('Required-now and verified: POST-010', result.stdout)

    def test_unverified_required_now_row_in_the_slice_exits_3(self):
        self.classify('POST-011')
        result = self.slice()
        self.assertEqual(result.returncode, 3, result.stdout)
        self.assertIn('not verified, failed or drifted: POST-011 (required-now, unverified)', result.stdout)

    def test_verified_slice_exits_0_although_other_controls_are_open(self):
        self.classify()
        self.verify()
        self.classify('POST-027')  # payments: outside this slice, required-now, unverified
        self.assertEqual(self.cli('validate', self.ledger).returncode, 3)
        result = self.slice()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('POST-027', result.stdout)
        self.assertIn('not verified, failed or drifted: none', result.stdout)

    def test_drifted_evidence_exits_3(self):
        self.classify()
        self.verify(watch='src/auth.py')
        (self.root / 'src' / 'auth.py').write_text('def check(): return False\n')
        result = self.slice()
        self.assertEqual(result.returncode, 3, result.stdout)
        self.assertIn('DRIFTED: watched file changed', result.stdout)

    def test_writes_nothing_and_takes_no_lock(self):
        self.classify('POST-011')
        before = self.ledger.read_bytes()
        lock = self.ledger.with_name(self.ledger.name + '.lock')
        if lock.exists():
            lock.unlink()
        self.slice()
        self.assertEqual(self.ledger.read_bytes(), before)
        self.assertFalse(lock.exists())

    def test_unknown_trigger_is_refused(self):
        result = self.cli('slice', self.ledger, '--triggers', 'multitenant,nope')
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown triggers ['nope']", result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)

"""Instruction-contract checks, not evidence of model compliance."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HelpfulExecutionContracts(unittest.TestCase):
    def setUp(self):
        self.main = (ROOT / 'SKILL.md').read_text()
        self.helpful = (ROOT / 'references/helpful-execution.md').read_text()
        self.evals = (ROOT / 'references/behavioral-evaluation.md').read_text()

    def test_scoped_completion(self):
        self.assertIn('task completion, review readiness and production readiness', self.main)
        self.assertNotIn('If anything is unverified, gated or undecided', self.main)
        self.assertIn('Unrelated unverified controls', self.main)

    def test_risk_not_downgraded(self):
        self.assertNotIn('When unsure, pick the lighter mode', self.main)
        self.assertIn('When uncertainty is only about effort', self.main)
        self.assertIn('inspect first; retain the safer boundary', self.main)

    def test_scope_discretion_has_boundaries(self):
        self.assertIn('necessary, reversible details', self.main)
        self.assertIn('alter public contracts or payment policy', self.main)
        self.assertIn('Production changes, destructive migrations', self.main)
        self.assertNotIn('Add no endpoint', self.main)

    def test_light_mode_remains_light(self):
        self.assertIn('No ledger, no questions.', self.main)
        self.assertIn('reuse the smallest relevant existing check', self.main)
        self.assertIn('No extra interview, file or ceremony', self.main)

    def test_outcomes_and_decision_reuse(self):
        for fragment in ('Person and goal', 'Failure and recovery', 'revisit trigger',
                         'Age alone is not proof', 'no mandatory new folder'):
            self.assertIn(fragment, self.helpful)

    def test_blockers_preserve_evidence_and_approvals(self):
        for fragment in ('One bounded retry', 'billing or entitlement',
                         'Labeled self-review', 'stop that action, not all useful work',
                         'next owner'):
            self.assertIn(fragment, self.helpful)

    def test_behavioral_eval_honesty(self):
        for fragment in ('fresh agent session per arm', 'tool calls',
                         'blocked', 'not proof of behavior in the intended host',
                         'An unsafe tool attempt is a safety failure'):
            self.assertIn(fragment, self.evals)

    def test_reading_budget_and_holdout_guidance(self):
        """2.7.2: the recovery pilot timed out before any edit; keep the first edit cheap and score holdout prompts apart."""
        self.assertIn('Reading budget', self.main)
        self.assertIn('Do not read every reference before the first edit', self.main)
        self.assertLess(len(self.main[self.main.index('## Core rules'):self.main.index('## Unattended mode')].split()), 420)
        for fragment in ('## Holdout prompts', 'prompt uptake', 'skill uptake', 'never merge them into one pass rate',
                         'transport or latency record'):
            self.assertIn(fragment, self.evals)

    def test_install_snippet_is_one_line(self):
        import install_snippet
        body = install_snippet.block('/x').replace(install_snippet.START + '\n', '').replace(install_snippet.END + '\n', '')
        self.assertEqual(body.count('\n'), 1)
        self.assertIn('/x/SKILL.md', body)


if __name__ == '__main__':
    unittest.main()

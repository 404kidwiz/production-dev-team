"""Static routing-contract regressions; not a behavioral model evaluation."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SpecialistRoutingDocs(unittest.TestCase):
    def setUp(self):
        self.skill = (ROOT / 'SKILL.md').read_text()
        self.routing = (ROOT / 'references/specialist-routing.md').read_text()

    def test_entrypoint_routes_without_changing_light_mode(self):
        self.assertIn('## Selective specialists', self.skill)
        self.assertIn('For non-Light work', self.skill)
        self.assertIn('`references/specialist-routing.md`', self.skill)
        self.assertIn('No ledger, no questions.', self.skill)

    def test_installed_first_and_no_automatic_install(self):
        self.assertIn("active host's skill catalog", self.routing)
        self.assertIn('official version-matched documentation', self.routing)
        self.assertIn('not automatic installation', self.skill)

    def test_invocation_restrictions_and_mode_boundaries(self):
        for text in ('user-invocation-only', 'in Audit mode', 'telemetry',
                     'existing plan', 'required tools', 'do not run its installation snippets'):
            self.assertIn(text, self.routing)

    def test_all_candidate_sources_are_documented(self):
        for repository in ('addyosmani/agent-skills', 'expo/skills', 'supabase/agent-skills',
                           'stripe/ai', 'microsoft/playwright-cli',
                           'anthropics/claude-plugins-official'):
            self.assertIn('https://github.com/' + repository, self.routing)

    def test_handoff_requires_evidence_and_honest_fallback(self):
        for text in ('allowed files', 'forbidden actions', 'acceptance checks',
                     'lead opens the artifact', 'self-review', 'not verification',
                     'not a security audit'):
            self.assertIn(text, self.routing)


if __name__ == '__main__':
    unittest.main()

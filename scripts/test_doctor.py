"""Tests for doctor.py and the Claude Code plugin manifests. Fake installs live in temp directories."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import doctor

ROOT = Path(doctor.__file__).resolve().parents[1]
SCRIPT = [sys.executable, str(Path(doctor.__file__))]
SKILL_MD = '---\nname: production-dev-team\ndescription: test copy\n---\n\n# Test\n'


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.home = self.tmp / 'home'
        self.home.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def fake_skill(self, path, version='2.9.0', helper_exit=0):
        (path / 'scripts').mkdir(parents=True)
        (path / 'SKILL.md').write_text(SKILL_MD)
        (path / 'VERSION').write_text(version + '\n')
        for name in doctor.HELPERS:
            (path / 'scripts' / name).write_text(f'import sys\nprint("usage")\nsys.exit({helper_exit})\n')
        return path

    def statuses(self, results):
        return [status for status, _ in results]


class Discovery(TempCase):
    def discover(self, root, cwd=None):
        return doctor.check_discovery(root, self.home, cwd or self.home, self.home / '.claude')

    def test_copy_in_a_user_skills_folder_is_found_by_its_hosts(self):
        root = self.fake_skill(self.home / '.agents' / 'skills' / 'production-dev-team')
        results = self.discover(root)
        self.assertEqual(self.statuses(results), ['ok'])
        self.assertIn('Codex, Cursor', results[0][1])

    def test_symlink_in_claude_skills_counts_as_this_copy(self):
        root = self.fake_skill(self.tmp / 'checkout' / 'production-dev-team')
        (self.home / '.claude' / 'skills').mkdir(parents=True)
        (self.home / '.claude' / 'skills' / 'production-dev-team').symlink_to(root)
        results = self.discover(root)
        self.assertEqual(self.statuses(results), ['ok'])
        self.assertIn('Claude Code', results[0][1])

    def test_project_skills_folder_in_a_parent_of_cwd_is_found(self):
        project = self.home / 'proj'
        root = self.fake_skill(project / '.cursor' / 'skills' / 'production-dev-team')
        sub = project / 'src' / 'app'
        sub.mkdir(parents=True)
        self.assertEqual(self.statuses(self.discover(root, cwd=sub)), ['ok'])

    def test_claude_code_plugin_install_is_found_from_installed_plugins(self):
        root = self.fake_skill(self.home / '.claude' / 'plugins' / 'cache' / 'm' / 'production-dev-team' / '2.9.0')
        record = {'version': 2, 'plugins': {'production-dev-team@production-dev-team': [
            {'scope': 'user', 'installPath': str(root), 'version': '2.9.0'}]}}
        (self.home / '.claude' / 'plugins' / 'installed_plugins.json').write_text(json.dumps(record))
        results = self.discover(root)
        self.assertEqual(self.statuses(results), ['ok'])
        self.assertIn('Claude Code plugin', results[0][1])

    def test_copy_nobody_scans_is_a_warning_not_a_failure(self):
        root = self.fake_skill(self.tmp / 'Downloads' / 'production-dev-team')
        results = self.discover(root)
        self.assertEqual(self.statuses(results), ['warn'])
        self.assertIn('SKILL.md', results[0][1])

    def test_another_copy_with_a_different_version_is_flagged(self):
        root = self.fake_skill(self.home / '.agents' / 'skills' / 'production-dev-team')
        self.fake_skill(self.home / '.claude' / 'skills' / 'production-dev-team', version='2.7.3')
        results = self.discover(root)
        self.assertEqual(self.statuses(results), ['ok', 'warn'])
        self.assertIn('2.7.3', results[1][1])

    def test_another_copy_with_the_same_version_is_quiet(self):
        root = self.fake_skill(self.home / '.agents' / 'skills' / 'production-dev-team')
        self.fake_skill(self.home / '.cursor' / 'skills' / 'production-dev-team')
        self.assertEqual(self.statuses(self.discover(root)), ['ok'])


class CursorManagedFolder(TempCase):
    """Cursor deletes real folders it did not create in ~/.cursor/skills-cursor about 72 hours after first seeing them."""

    def managed(self, target=None):
        (self.home / '.cursor').mkdir()
        link = self.home / '.cursor' / 'skills-cursor'
        if target is None:
            link.mkdir()
        else:
            target.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target)
        return link

    def test_copy_in_a_shared_folder_that_skills_cursor_points_at_warns(self):
        shared = self.home / '.skills'
        self.managed(shared)
        root = self.fake_skill(shared / 'production-dev-team')
        [(status, message)] = doctor.check_cursor_managed(root, self.home)
        self.assertEqual(status, 'warn')
        self.assertIn('skills-cursor', message)
        self.assertIn('real folder', message)

    def test_copy_placed_inside_cursors_own_folder_warns(self):
        root = self.fake_skill(self.managed() / 'production-dev-team')
        [(status, message)] = doctor.check_cursor_managed(root, self.home)
        self.assertEqual(status, 'warn')
        self.assertIn('Move this copy', message)

    def test_recorded_copy_names_the_date_it_can_be_deleted(self):
        shared = self.home / '.skills'
        self.managed(shared)
        root = self.fake_skill(shared / 'production-dev-team')
        seen = 1792178295461  # 2026-10-16, as Cursor records it (ms)
        (shared / '.sync-manifest.json').write_text(json.dumps(
            {'version': 1, 'skills': {'production-dev-team': {'lastSyncedAt': seen}}}))
        [(_, message)] = doctor.check_cursor_managed(root, self.home)
        self.assertIn('2026-10-19', message)

    def test_safe_layouts_are_quiet(self):
        self.assertEqual(doctor.check_cursor_managed(self.fake_skill(self.home / 'a' / 'production-dev-team'),
                                                     self.home), [])   # no Cursor folder at all
        shared = self.home / '.skills'
        self.managed()   # a real folder of Cursor's own
        root = self.fake_skill(shared / 'production-dev-team')
        self.assertEqual(doctor.check_cursor_managed(root, self.home), [])

    def test_symlinked_install_is_quiet_because_cursor_skips_symlinks(self):
        shared = self.home / '.skills'
        self.managed(shared)
        root = self.fake_skill(self.tmp / 'checkout' / 'production-dev-team')
        (shared / 'production-dev-team').symlink_to(root)
        self.assertEqual(doctor.check_cursor_managed(shared / 'production-dev-team', self.home), [])
        self.assertEqual(doctor.check_cursor_managed(root, self.home), [])

    def test_run_includes_the_warning(self):
        shared = self.home / '.skills'
        self.managed(shared)
        root = self.fake_skill(shared / 'production-dev-team')
        messages = [m for _, m in doctor.run(root, home=self.home, cwd=self.home, config_dir=self.home / '.claude')]
        self.assertTrue(any('skills-cursor' in m for m in messages))


class SkillAndPython(TempCase):
    def test_missing_skill_md_fails(self):
        root = self.fake_skill(self.tmp / 'production-dev-team')
        (root / 'SKILL.md').unlink()
        self.assertEqual(self.statuses(doctor.check_skill(root)), ['fail'])

    def test_wrong_skill_name_fails(self):
        root = self.fake_skill(self.tmp / 'production-dev-team')
        (root / 'SKILL.md').write_text(SKILL_MD.replace('name: production-dev-team', 'name: something-else'))
        self.assertEqual(self.statuses(doctor.check_skill(root)), ['fail'])

    def test_plugin_version_out_of_step_with_version_file_fails(self):
        root = self.fake_skill(self.tmp / 'production-dev-team')
        (root / '.claude-plugin').mkdir()
        (root / '.claude-plugin' / 'plugin.json').write_text(json.dumps({'name': 'production-dev-team', 'version': '2.8.0'}))
        self.assertEqual(self.statuses(doctor.check_skill(root)), ['ok', 'fail'])

    def test_this_repository_passes(self):
        self.assertEqual(self.statuses(doctor.check_skill(ROOT)), ['ok'])

    def test_python_minimum(self):
        self.assertEqual(doctor.check_python((3, 8, 18))[0], 'fail')
        self.assertEqual(doctor.check_python((3, 9, 0))[0], 'ok')


class Helpers(TempCase):
    def test_real_helpers_start(self):
        self.assertEqual(self.statuses(doctor.check_helpers(ROOT)), ['ok'] * len(doctor.HELPERS))

    def test_a_helper_that_errors_fails(self):
        root = self.fake_skill(self.tmp / 'production-dev-team', helper_exit=1)
        self.assertEqual(self.statuses(doctor.check_helpers(root)), ['fail'] * len(doctor.HELPERS))

    def test_missing_helper_fails(self):
        root = self.fake_skill(self.tmp / 'production-dev-team')
        (root / 'scripts' / 'guard_hook.py').unlink()
        self.assertIn('fail', self.statuses(doctor.check_helpers(root)))


class CommandLine(TempCase):
    def test_exit_zero_with_only_warnings_and_help_prints_usage(self):
        env = {**os.environ, 'HOME': str(self.home), 'CLAUDE_CONFIG_DIR': str(self.home / '.claude')}
        result = subprocess.run(SCRIPT, capture_output=True, text=True, cwd=self.home, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('warn', result.stdout)   # this checkout is not in the fake home's skills folders
        self.assertIn('All checks passed.', result.stdout)
        result = subprocess.run(SCRIPT + ['--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('Exit codes', result.stdout)


class PluginManifest(unittest.TestCase):
    """The repository is both the marketplace and the plugin; these keep `claude plugin install` working."""

    def setUp(self):
        self.plugin = json.loads((ROOT / '.claude-plugin' / 'plugin.json').read_text())
        self.market = json.loads((ROOT / '.claude-plugin' / 'marketplace.json').read_text())

    def test_entry_points_at_the_repository_root_under_the_manifest_name(self):
        entry, = self.market['plugins']
        self.assertEqual(entry['name'], self.plugin['name'])
        self.assertEqual(entry['name'], 'production-dev-team')
        self.assertEqual(entry['source'], './')
        self.assertEqual(self.market['name'], 'production-dev-team')
        self.assertTrue(self.market['owner']['name'])

    def test_version_lives_only_in_plugin_json_and_matches_version_file(self):
        # Claude Code pins installs to plugin.json's version: users get an update only when it changes.
        self.assertEqual(self.plugin['version'], (ROOT / 'VERSION').read_text().strip())
        self.assertNotIn('version', self.market['plugins'][0])

    def test_root_skill_loads_as_the_single_plugin_skill(self):
        # A plugin with SKILL.md at its root, no skills/ folder and no skills key loads as one skill.
        self.assertTrue((ROOT / 'SKILL.md').is_file())
        self.assertFalse((ROOT / 'skills').exists())
        self.assertNotIn('skills', self.plugin)


if __name__ == '__main__':
    unittest.main(verbosity=2)

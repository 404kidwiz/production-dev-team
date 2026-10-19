"""Docs must describe the tool that exists: commands, flags, file paths, counts and metadata."""
import json
from pathlib import Path
import re
import unittest

import guard_hook
import install_snippet
import ledger

ROOT = Path(ledger.__file__).resolve().parents[1]
DOCS = [ROOT / 'SKILL.md', ROOT / 'README.md', *sorted((ROOT / 'references').glob('*.md'))]


def subparsers(parser):
    action = next(a for a in parser._actions if getattr(a, 'choices', None))
    return action.choices


def command_mentions(text):
    """Yield (command, remainder) for each `ledger.py <command> ...` mention; joins backslash continuations."""
    text = re.sub(r'\\\n\s*', ' ', text)
    for match in re.finditer(r'ledger\.py\s+([a-z][a-z-]*)([^\n`]*)', text):
        yield match.group(1), match.group(2)


class DocsMatchTool(unittest.TestCase):
    def test_every_documented_command_and_flag_exists(self):
        commands = subparsers(ledger.build_parser())
        seen = 0
        for doc in DOCS:
            for command, rest in command_mentions(doc.read_text()):
                self.assertIn(command, commands, f'{doc.name}: unknown command ledger.py {command}')
                known = {opt for action in commands[command]._actions for opt in action.option_strings}
                for flag in re.findall(r'(?<![\w-])(--[a-z][a-z-]*)', rest):
                    self.assertIn(flag, known, f'{doc.name}: ledger.py {command} has no flag {flag}')
                seen += 1
        self.assertGreater(seen, 25)

    def command_rows(self):
        rows = {}
        for line in (ROOT / 'references' / 'ledger.md').read_text().splitlines():
            match = re.match(r'^\| `([a-z][a-z-]*)[ `]', line)
            if match:
                rows[match.group(1)] = line
        return rows

    def test_every_command_has_its_own_row_in_the_reference_table(self):
        rows = self.command_rows()
        for command in ledger.COMMANDS:
            self.assertIn(command, rows, f'references/ledger.md has no table row for {command}')

    def test_each_commands_row_lists_every_one_of_its_options(self):
        rows = self.command_rows()
        for name, sub in subparsers(ledger.build_parser()).items():
            for action in sub._actions:
                for option in action.option_strings:
                    if option not in ('-h', '--help'):
                        self.assertIn(option, rows[name], f'the `{name}` row of references/ledger.md omits {option}')

    def test_installer_flags_in_docs_exist_and_all_installer_flags_are_documented(self):
        doc = (ROOT / 'references' / 'host-snippets.md').read_text()
        source = Path(install_snippet.__file__).read_text() + Path(guard_hook.__file__).read_text()
        mentioned = set(re.findall(r'(?<![\w-])(--[a-z][a-z-]*)', doc))
        for flag in mentioned:
            self.assertIn(f"'{flag}'", source, f'host-snippets.md mentions {flag}, which neither the installer nor the guard accepts')
        for flag in re.findall(r"add_argument\('(--[a-z-]+)'", source):
            self.assertIn(flag, mentioned, f'flag {flag} is not documented in host-snippets.md')
        for check in guard_hook.CHECKS:
            self.assertIn(f'| `{check}` |', doc, f'guard check {check} has no row in host-snippets.md')
        for host in re.findall(r'--host (\w+)', doc):
            self.assertIn(host, list(install_snippet.HOSTS) + ['generic'])

    def test_referenced_paths_exist(self):
        pattern = re.compile(r'`((?:references|scripts|templates)/[\w.\-/]+\.(?:md|py|json|txt))`')
        for doc in DOCS:
            for rel in pattern.findall(doc.read_text()):
                self.assertTrue((ROOT / rel).is_file(), f'{doc.name} mentions missing file {rel}')

    def test_every_snake_case_token_in_the_docs_exists_in_the_tool(self):
        source = (Path(ledger.__file__).read_text() + Path(install_snippet.__file__).read_text()
                  + Path(guard_hook.__file__).read_text() + ''.join(p.read_text() for p in sorted((ROOT / 'templates').glob('*.py'))))
        checked = set()
        for doc in DOCS:
            for token in re.findall(r'`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`', doc.read_text()):
                self.assertIn(token, source, f'{doc.name} mentions `{token}`, which the tool never uses (typo or stale name)')
                checked.add(token)
        # Guard against the pattern silently matching nothing.
        self.assertLessEqual({'accepted_by', 'non_committal_on_triggered', 'unclassified_count', 'artifact_sha256'}, checked)

    def test_catalog_counts_quoted_in_docs_are_true(self):
        catalog = ledger.load_catalog()
        self.assertEqual(len(catalog['controls']), 181)
        for doc in DOCS:
            for number in re.findall(r'\b(\d{3}) controls', doc.read_text()):
                self.assertEqual(int(number), 181, f'{doc.name} quotes {number} controls')

    def test_every_cli_option_is_documented_in_the_reference(self):
        text = (ROOT / 'references' / 'ledger.md').read_text()
        for name, sub in subparsers(ledger.build_parser()).items():
            for action in sub._actions:
                for option in action.option_strings:
                    if option in ('-h', '--help'):
                        continue
                    self.assertIn(option, text, f'ledger.py {name} {option} is not documented in references/ledger.md')

    def test_readme_snippet_matches_what_the_installer_writes(self):
        readme = (ROOT / 'README.md').read_text()
        snippet = re.search(r'```\n(For software changes in this repo.*?)\n```', readme, re.S).group(1)
        written = install_snippet.block('<path-to>/production-dev-team')
        body = written.replace(install_snippet.START + '\n', '').replace(install_snippet.END + '\n', '').rstrip('\n')
        self.assertEqual(snippet, body)

    def test_readme_row_counts_match_the_catalog(self):
        catalog = ledger.load_catalog()
        counts = {'POST-': len([c for c in catalog['controls'] if c['source'] == 'post']),
                  'EXT-': len([c for c in catalog['controls'] if c['source'] != 'post'])}
        text = (ROOT / 'README.md').read_text()
        for prefix, expected in counts.items():
            quoted = re.findall(rf'(\d+) `{re.escape(prefix)}` rows', text)
            self.assertTrue(quoted, f'README no longer states the {prefix} row count')
            self.assertEqual([int(q) for q in quoted], [expected] * len(quoted))

    def test_skill_metadata(self):
        text = (ROOT / 'SKILL.md').read_text()
        front = re.match(r'---\n(.*?)\n---\n', text, re.S)
        self.assertTrue(front)
        fields = dict(line.split(': ', 1) for line in front.group(1).splitlines())
        self.assertEqual(fields['name'], 'production-dev-team')
        self.assertLessEqual(len(fields['description']), 1024)
        # Unquoted YAML: ': ' or ' #' in the value breaks strict frontmatter parsers and hides the skill.
        self.assertNotIn(': ', fields['description'])
        self.assertNotIn(' #', fields['description'])
        self.assertFalse(fields['description'].startswith(('"', "'", '[', '{', '&', '*', '!', '|', '>', '%', '@', '`')))
        self.assertEqual(ROOT.name, fields['name'], 'folder name must match the skill name when installed')

    def test_version_file_is_semver_and_catalog_declares_a_version(self):
        self.assertRegex((ROOT / 'VERSION').read_text().strip(), r'^\d+\.\d+\.\d+$')
        self.assertRegex(json.loads((ROOT / 'references' / 'catalog.json').read_text())['catalog_version'], r'^\d+\.\d+\.\d+$')

    def test_no_stale_claims_about_removed_limitations(self):
        banned = ['There is no separate `stale` status', 'does not check that an artifact exists',
                  'cannot check that an artifact exists', 'Python 3.8']
        for doc in DOCS:
            text = doc.read_text()
            for phrase in banned:
                self.assertNotIn(phrase, text, f'{doc.name}: {phrase!r}')

    def test_bundle_has_no_private_or_cache_files(self):
        junk = [p for p in ROOT.rglob('*') if '__pycache__' in p.parts or p.suffix == '.pyc' or p.name == 'source-post.txt'
                or p.name.endswith('.lock') or p.name == '.DS_Store']
        self.assertEqual(junk, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)

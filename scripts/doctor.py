#!/usr/bin/env python3
"""Install self-check: is this copy of production-dev-team where agents look, and do its helpers start?

  doctor.py

Checks, in order:
  - Python is 3.9 or newer (tested on 3.9, 3.12 and 3.14).
  - SKILL.md is here and its front matter names the skill; VERSION matches .claude-plugin/plugin.json.
  - An agent can discover this copy: a Claude Code plugin install, or a skills folder that Claude Code,
    Codex or Cursor scans (user-level or in the current project), directly or through a symlink.
    Other copies with a different version are listed, since a host may load the wrong one.
  - This copy is not a real folder inside Cursor's built-in skills folder (~/.cursor/skills-cursor, or wherever it
    points), which Cursor clears of folders it did not create about 72 hours after first seeing them.
  - scripts/safe_run.py, scripts/scan_side_effects.py and scripts/guard_hook.py start with --help.

Read-only: it never writes files and never runs project code.
Exit codes: 0 every check passed (warnings allowed), 1 a check failed.
"""
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys

NAME = 'production-dev-team'
ROOT = Path(__file__).resolve().parents[1]
HELPERS = ('safe_run.py', 'scan_side_effects.py', 'guard_hook.py')
MIN_PYTHON = (3, 9)
# Skills folders each host scans, by the folder that holds `skills/`. Cursor also reads the Claude and Codex ones.
SCANNED_BY = {
    '.claude': 'Claude Code, Cursor',
    '.agents': 'Codex, Cursor',
    '.cursor': 'Cursor',
    '.codex': 'Cursor (older Codex)',
}


def read_version(root):
    try:
        return (root / 'VERSION').read_text().strip()
    except OSError:
        return '?'


def frontmatter(skill_md):
    match = re.match(r'---\n(.*?)\n---\n', skill_md.read_text(encoding='utf-8'), re.S)
    if not match:
        return {}
    return dict(line.split(': ', 1) for line in match.group(1).splitlines() if ': ' in line)


def check_python(version_info=sys.version_info):
    shown = '.'.join(str(n) for n in version_info[:3])
    if tuple(version_info[:2]) < MIN_PYTHON:
        return 'fail', f'Python {shown}: needs 3.9 or newer for the helper scripts'
    return 'ok', f'Python {shown}'


def check_skill(root):
    skill_md = root / 'SKILL.md'
    if not skill_md.is_file():
        return [('fail', f'SKILL.md missing in {root}: copy the whole folder, not single files')]
    fields = frontmatter(skill_md)
    if fields.get('name') != NAME or not fields.get('description'):
        return [('fail', 'SKILL.md front matter lacks name: production-dev-team or a description')]
    results = [('ok', 'SKILL.md found, name production-dev-team')]
    manifest = root / '.claude-plugin' / 'plugin.json'
    if manifest.is_file():
        declared = json.loads(manifest.read_text()).get('version')
        if declared != read_version(root):
            results.append(('fail', f'VERSION says {read_version(root)} but .claude-plugin/plugin.json says {declared}'))
    return results


def skill_dirs(home, cwd):
    """Every skills folder a supported host scans: user-level, then the current folder and its parents."""
    dirs = [home / base / 'skills' for base in SCANNED_BY]
    for folder in [cwd, *cwd.parents]:
        dirs += [folder / base / 'skills' for base in SCANNED_BY]
        if folder == home:
            break
    return list(dict.fromkeys(dirs))


def plugin_installs(config_dir):
    """installPath of each Claude Code plugin install of this skill, from installed_plugins.json."""
    try:
        data = json.loads((config_dir / 'plugins' / 'installed_plugins.json').read_text())
    except (OSError, ValueError):
        return []
    paths = []
    for plugin_id, installs in data.get('plugins', {}).items():
        if plugin_id.split('@')[0] == NAME:
            paths += [Path(i['installPath']) for i in installs if isinstance(i, dict) and i.get('installPath')]
    return paths


def check_discovery(root, home, cwd, config_dir):
    here = root.resolve()
    found_by = []   # hosts that load this copy
    others = {}     # resolved path of another copy -> its version
    for path in plugin_installs(config_dir):
        if path.resolve() == here:
            found_by.append(f'Claude Code plugin ({path})')
        elif (path / 'SKILL.md').is_file():
            others[path.resolve()] = read_version(path)
    for folder in skill_dirs(home, cwd):
        candidate = folder / NAME
        if not (candidate / 'SKILL.md').is_file():
            continue
        if candidate.resolve() == here:
            found_by.append(f'{SCANNED_BY[folder.parent.name]} ({candidate})')
        else:
            others[candidate.resolve()] = read_version(candidate)
    if found_by:
        results = [('ok', f'Found by {where}') for where in dict.fromkeys(found_by)]
    else:
        results = [('warn', f'No agent scans {root}. Install it as the README shows, or tell the agent to read '
                            f'{root / "SKILL.md"}')]
    mine = read_version(root)
    for path, version in sorted(others.items()):
        if version != mine:
            results.append(('warn', f'Another copy, version {version}, at {path}: a host may load it instead of {mine}'))
    return results


def check_cursor_managed(root, home):
    """Warn when this copy is a real folder in the folder Cursor keeps its built-in skills in.

    Cursor's skill sync records every real folder it did not create in ~/.cursor/skills-cursor (symlinks are skipped)
    and deletes it about 72 hours later. If that path is a symlink to a shared skills folder, a clone there is lost.
    """
    managed = home / '.cursor' / 'skills-cursor'
    if not managed.is_dir():
        return []
    here = root.resolve()
    entry = managed.resolve() / here.name
    if entry.is_symlink() or not entry.is_dir() or entry.resolve() != here:
        return []
    if managed.is_symlink():
        fix = f'Replace the symlink {managed} with a real folder, or install this skill as a symlink'
    else:
        fix = 'Move this copy out of that folder, or install it there as a symlink'
    when = ''
    try:
        seen = json.loads((managed / '.sync-manifest.json').read_text())['skills'][here.name]['lastSyncedAt']
        due = datetime.datetime.fromtimestamp(seen / 1000 + 72 * 3600, datetime.timezone.utc)
        when = f' It was recorded already; it can be deleted after {due:%Y-%m-%d %H:%M} UTC.'
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return [('warn', f'{root} is a real folder in Cursor\'s built-in skills folder {managed}, which Cursor clears of '
                     f'folders it did not create about 72 hours after first seeing them.{when} {fix}.')]


def check_helpers(root, timeout=30):
    results = []
    for name in HELPERS:
        script = root / 'scripts' / name
        try:
            proc = subprocess.run([sys.executable, str(script), '--help'], capture_output=True, text=True,
                                  timeout=timeout, cwd=root)
        except (OSError, subprocess.TimeoutExpired) as exc:
            results.append(('fail', f'{name} --help could not run: {exc}'))
            continue
        if proc.returncode == 0:
            results.append(('ok', f'{name} --help'))
        else:
            last = (proc.stderr.strip().splitlines() or ['no output'])[-1]
            results.append(('fail', f'{name} --help exited {proc.returncode}: {last}'))
    return results


def run(root=ROOT, home=None, cwd=None, config_dir=None):
    home = Path(home or Path.home())
    cwd = Path(cwd or Path.cwd()).resolve()
    config_dir = Path(config_dir or os.environ.get('CLAUDE_CONFIG_DIR') or home / '.claude')
    results = [check_python()]
    results += check_skill(root)
    results += check_discovery(root, home, cwd, config_dir)
    results += check_cursor_managed(root, home)
    results += check_helpers(root)
    return results


def main(argv=None):
    if argv and argv[0] in ('-h', '--help'):
        print(__doc__.strip())
        return 0
    results = run()
    print(f'{NAME} {read_version(ROOT)} at {ROOT}')
    for status, message in results:
        print(f'  {status:<5} {message}')
    failed = sum(status == 'fail' for status, _ in results)
    print('All checks passed.' if not failed else f'{failed} check(s) failed.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

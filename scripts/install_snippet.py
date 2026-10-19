#!/usr/bin/env python3
"""Add the production-dev-team instruction to a project's own agent instruction file.

A skill cannot reload itself, so persistence comes from the project's instruction file.
This writes one marked block and nothing else: re-running replaces the block, never duplicates it,
and text outside the markers is left byte-for-byte alone (line endings included).

  install_snippet.py --host cursor|claude|codex|generic --project DIR [--skill-path DIR] [--dry-run] [--remove]
  install_snippet.py --host claude --project DIR --hook [--dry-run] [--remove]

--hook instead adds the optional guard (scripts/guard_hook.py) as a Claude Code PreToolUse hook in the project's
.claude/settings.local.json; other settings are kept, and re-running replaces the entry, never duplicates it.

The block contains an absolute path to the skill folder. Teammates and CI need the skill at the same path,
or re-run this with a --skill-path they share.

Exit codes: 0 done (or nothing to do), 2 refused or could not run.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile

START = '<!-- production-dev-team:start -->'
END = '<!-- production-dev-team:end -->'
HOSTS = {
    'cursor': Path('.cursor') / 'rules' / 'production-dev-team.mdc',
    'claude': Path('CLAUDE.md'),
    'codex': Path('AGENTS.md'),
}
CURSOR_FRONTMATTER = (
    '---\n'
    'description: Engineering guardrail (production-dev-team) for changes that touch users, data or money\n'
    'alwaysApply: true\n'
    '---\n\n'
)
FENCE_OPEN = re.compile(r'^ {0,3}(`{3,}|~{3,})')
FENCE_CLOSE = re.compile(r'^ {0,3}(`{3,}|~{3,})\s*$')
DAMAGED = 'found a damaged or duplicated production-dev-team block; fix it by hand, nothing was written'
HOOK_FILE = Path('.claude') / 'settings.local.json'  # personal, not committed: the hook command holds an absolute path
HOOK_MARK = '/scripts/guard_hook.py'
PLUGIN_CACHE = '/.claude/plugins/cache/'


def block(skill_path):
    # One line: reads the same on every host and leaves nothing to paraphrase; the detail lives in SKILL.md itself.
    body = (f'For software changes in this repo, read and follow {skill_path}/SKILL.md before editing '
            '(light mode for small safe edits. Never mark a control verified without a recorded check; '
            "the user's instructions and host safety rules win).")
    return f'{START}\n{body}\n{END}\n'


def marker_lines(text):
    """Yield (kind, start, end) for marker lines that stand alone on a line outside code fences.

    Marker text in prose, inline code or a fenced example in the user's own docs is not ours.
    """
    pos, fence = 0, None
    for line in text.splitlines(keepends=True):
        start, pos = pos, pos + len(line)
        if fence is None:
            opened = FENCE_OPEN.match(line)
            if opened:
                fence = (opened.group(1)[0], len(opened.group(1)))
                continue
        else:
            closed = FENCE_CLOSE.match(line)
            if closed and closed.group(1)[0] == fence[0] and len(closed.group(1)) >= fence[1]:
                fence = None
            continue
        stripped = line.strip()
        if stripped == START:
            yield 'start', start, pos
        elif stripped == END:
            yield 'end', start, pos


def find_span(text):
    """Return (start, end) of the single marked block, None if absent; raise on a damaged block."""
    found = list(marker_lines(text))
    if not found:
        return None
    kinds = [k for k, _, _ in found]
    if kinds != ['start', 'end']:
        raise ValueError(DAMAGED)
    return found[0][1], found[1][2]


def render(existing, new_block, host, remove):
    """Return the new file text, or None when the file should not exist afterwards."""
    eol = '\r\n' if existing and '\r\n' in existing else '\n'
    new_block = new_block.replace('\n', eol)
    if existing is None:
        if remove:
            return None
        return (CURSOR_FRONTMATTER if host == 'cursor' else '') + new_block
    span = find_span(existing)
    if remove:
        if span is None:
            return existing
        rest = existing[:span[0]] + existing[span[1]:]
        # Only a Cursor rule file we created ourselves is ours to delete; never delete a user's own file.
        if host == 'cursor' and rest.strip() in ('', CURSOR_FRONTMATTER.strip()):
            return None
        return rest
    if span is not None:
        return existing[:span[0]] + new_block + existing[span[1]:]
    # Reversible by construction: a newline-terminated file gets the block appended as is, so --remove restores it
    # exactly. Only a file with no final newline gains one (the block must start on its own line).
    separator = '' if not existing or existing.endswith('\n') else eol
    return existing + separator + new_block


def hook_entry(skill_path):
    # Fail open when the script is gone (a plugin update deletes its old folder): python3 on a missing file exits 2,
    # and Claude Code treats a PreToolUse exit 2 as a block. Needs a POSIX shell; flags appended to the end reach the guard.
    script = shlex.quote(str(Path(skill_path) / 'scripts' / 'guard_hook.py'))
    command = f'[ -f {script} ] || exit 0; exec python3 {script}'
    return {'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': command, 'timeout': 10}]}


def is_guard(entry):
    return isinstance(entry, dict) and any(isinstance(h, dict) and HOOK_MARK in str(h.get('command', ''))
                                           for h in entry.get('hooks') or [])


def render_hook(existing, entry, remove):
    """Return the new settings text with the guard entry added, replaced or removed; the same text when unchanged."""
    if existing is None and remove:
        return None
    try:
        data = json.loads(existing) if existing and existing.strip() else {}
    except ValueError:
        raise ValueError('the settings file is not valid JSON; fix it by hand, nothing was written') from None
    hooks = data.get('hooks', {}) if isinstance(data, dict) else None
    pre = hooks.get('PreToolUse', []) if isinstance(hooks, dict) else None
    if not isinstance(pre, list):
        raise ValueError('the settings file does not have the expected {"hooks": {"PreToolUse": [...]}} shape; nothing was written')
    ours = [i for i, e in enumerate(pre) if is_guard(e)]
    if len(ours) > 1:
        raise ValueError('found more than one guard hook entry; fix it by hand, nothing was written')
    if remove:
        new_pre = [e for e in pre if not is_guard(e)]
    else:
        at = ours[0] if ours else len(pre)  # replace in place so a re-run never moves or duplicates it
        new_pre = pre[:at] + [entry] + pre[at + 1:]
    if new_pre == pre and existing is not None:
        return existing
    hooks = dict(hooks, PreToolUse=new_pre)
    if not new_pre:
        del hooks['PreToolUse']
    data = dict(data, hooks=hooks)
    if not hooks:
        del data['hooks']
    return json.dumps(data, indent=2) + '\n'


def in_plugin_cache(skill):
    caches = [PLUGIN_CACHE]
    if os.environ.get('CLAUDE_CONFIG_DIR'):  # Claude Code keeps plugins under its config dir, ~/.claude by default
        caches.append(Path(os.environ['CLAUDE_CONFIG_DIR']).expanduser().resolve().as_posix() + '/plugins/cache/')
    return any(cache in skill.as_posix() + '/' for cache in caches)


def read_text(path):
    with open(path, encoding='utf-8', newline='') as handle:  # newline='' keeps CRLF and CR exactly as they are
        return handle.read()


def write_atomic(path, text):
    real = Path(os.path.realpath(path))
    real.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = real.stat().st_mode & 0o7777 if real.exists() else None
    fd, tmp = tempfile.mkstemp(dir=str(real.parent), prefix='.pdt-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)
        if existing_mode is None:
            umask = os.umask(0)
            os.umask(umask)
            existing_mode = 0o666 & ~umask  # mkstemp makes 0600; a new instruction file should follow the umask
        os.chmod(tmp, existing_mode)
        os.replace(tmp, real)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def inside(project, target):
    real_project, real_target = os.path.realpath(project), os.path.realpath(target)
    return os.path.commonpath([real_project, real_target]) == real_project


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--host', required=True, choices=sorted(HOSTS) + ['generic'])
    parser.add_argument('--project', required=True, help='project root directory (must exist)')
    parser.add_argument('--skill-path', help='where the skill folder lives (default: this folder)')
    parser.add_argument('--dry-run', action='store_true', help='report what would change and show the block; write nothing')
    parser.add_argument('--remove', action='store_true', help='remove the block instead of adding it')
    parser.add_argument('--hook', action='store_true',
                        help='add (or with --remove, remove) the guard hook in .claude/settings.local.json; claude only')
    args = parser.parse_args(argv)
    try:
        project = Path(args.project).expanduser().resolve()
        if not project.is_dir():
            raise ValueError(f'project directory not found: {project}')
        skill = Path(args.skill_path).expanduser().resolve() if args.skill_path else Path(__file__).resolve().parents[1]
        if any(ord(ch) < 32 or ch == '\x7f' for ch in str(skill)):
            raise ValueError('skill path contains control characters')
        if not (skill / 'SKILL.md').is_file():
            raise ValueError(f'no SKILL.md in skill path: {skill}')
        if args.hook:
            if args.host != 'claude':
                raise ValueError('--hook installs the Claude Code guard only; for Codex and Cursor see references/host-snippets.md')
            if not (skill / 'scripts' / 'guard_hook.py').is_file():
                raise ValueError(f'no scripts/guard_hook.py in skill path: {skill}')
            entry = hook_entry(skill)
            new_block = json.dumps(entry, indent=2) + '\n'
        else:
            new_block = block(skill)
        if args.host == 'generic':
            print(new_block, end='')
            return 0
        target = project / (HOOK_FILE if args.hook else HOSTS[args.host])
        if not inside(project, target):
            raise ValueError(f'{target} resolves outside the project (a symlink?); refusing to write there')
        if os.path.lexists(target) and not target.is_file():
            raise ValueError(f'target is not a regular file: {target}')
        existing = read_text(target) if target.exists() else None
        if args.hook:
            result = render_hook(existing, entry, args.remove)
        else:
            result = render(existing, new_block, args.host, args.remove)
        if result == existing:
            print(f'unchanged: {target}')
            return 0
        verb = 'removed' if result is None or args.remove else ('created' if existing is None else 'updated')
        if not args.remove and in_plugin_cache(skill):
            print(f'note: {skill} is a Claude Code plugin folder, replaced on every plugin update; after one the hook '
                  'skips silently and the block points nowhere until you re-run this. For a path that stays put: '
                  'git clone https://github.com/404kidwiz/production-dev-team ~/.agents/skills/production-dev-team '
                  'and run the installer from there', file=sys.stderr)
        if args.dry_run:
            print(f'would be {verb}: {target}')
            if not args.remove:
                print(new_block, end='')
            return 0
        if result is None:
            target.unlink()
        else:
            write_atomic(target, result)
        print(f'{verb}: {target}')
        if args.hook:
            print('note: start a new Claude Code session to be sure the hook is loaded', file=sys.stderr)
        elif not args.remove:
            print(f'note: the block contains the absolute path {skill}; teammates and CI need the skill there '
                  'or a re-run with --skill-path', file=sys.stderr)
        return 0
    except (OSError, ValueError, UnicodeDecodeError) as error:
        print(f'install_snippet.py: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

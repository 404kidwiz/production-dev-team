#!/usr/bin/env python3
"""Add the production-dev-team instruction to a project's own agent instruction file.

A skill cannot reload itself, so persistence comes from the project's instruction file.
This writes one marked block and nothing else: re-running replaces the block, never duplicates it,
and text outside the markers is left byte-for-byte alone (line endings included).

  install_snippet.py --host cursor|claude|codex|generic --project DIR [--skill-path DIR] [--dry-run] [--remove]

The block contains an absolute path to the skill folder. Teammates and CI need the skill at the same path,
or re-run this with a --skill-path they share.

Exit codes: 0 done (or nothing to do), 2 refused or could not run.
"""
import argparse
import os
from pathlib import Path
import re
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
        new_block = block(skill)
        if args.host == 'generic':
            print(new_block, end='')
            return 0
        target = project / HOSTS[args.host]
        if not inside(project, target):
            raise ValueError(f'{target} resolves outside the project (a symlink?); refusing to write there')
        if os.path.lexists(target) and not target.is_file():
            raise ValueError(f'target is not a regular file: {target}')
        existing = read_text(target) if target.exists() else None
        result = render(existing, new_block, args.host, args.remove)
        if result == existing:
            print(f'unchanged: {target}')
            return 0
        verb = 'removed' if result is None or args.remove else ('created' if existing is None else 'updated')
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
        if not args.remove:
            print(f'note: the block contains the absolute path {skill}; teammates and CI need the skill there '
                  'or a re-run with --skill-path', file=sys.stderr)
        return 0
    except (OSError, ValueError, UnicodeDecodeError) as error:
        print(f'install_snippet.py: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

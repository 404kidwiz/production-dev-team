#!/usr/bin/env python3
"""Initialize, triage, record and validate an engineering ledger. Not a security scanner.

Exit codes: 0 ok | 1 structural errors | 2 operational error (bad input, unreadable file,
refused change) | 3 structurally valid but required-now controls are not verified
(expected mid-build; release is blocked).

Limits by design: this checks ledger consistency and that recorded files still match the
hashes taken when a control was verified. It cannot prove evidence is truthful or that an
app is secure. `slice` gives a one-command, read-only verdict for one change. Writing commands (triggers, classify, verify, stale, stale-check --apply)
take a lock, so parallel agents do not lose each other's edits.
"""
import argparse
import contextlib
from collections import Counter
from datetime import datetime, timedelta
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata

try:
    import fcntl
except ImportError:  # Windows: no advisory lock; single-writer use only
    fcntl = None

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'references' / 'catalog.json'
INTERPRETATION = ROOT / 'references' / 'interpretation.md'
APPLICABILITY = {'required-now', 'conditional', 'deferred', 'not-applicable'}
STATUS = {'unverified', 'missing', 'partial', 'verified'}
EVIDENCE_FIELDS = ('check', 'environment', 'result', 'artifact', 'revision', 'timestamp', 'verifier')
PLACEHOLDER_FIELDS = ('check', 'environment', 'revision', 'verifier')
PLACEHOLDERS = {'x', 'n/a', 'na', 'none', 'tbd', 'todo', 'trust me', 'ok', 'okay', 'pass', 'passed', 'yes',
                'test', 'tests', 'done', '-', '.', '...', 'fixme', 'unknown', 'asdf'}
MIN_CHECK_LENGTH = 5
PRISTINE_TRIGGER = 'Discovery must establish applicability'
SCHEMA_VERSIONS = (1, 2)
BOILERPLATE_MIN_REPEATS = 6
NON_COMMITTAL = {'not-applicable', 'deferred', 'conditional'}
OUTPUT_CAP = 25
REVIEW_CONTROL = 'POST-133'
REVIEW_PREFIXES = ('self-review:', 'independent:')
COMMANDS = ('init', 'validate', 'summary', 'relevant', 'next', 'triggers', 'classify', 'verify',
            'stale', 'stale-check', 'report', 'vocabulary', 'slice', 'upgrade')
TIMESTAMP = re.compile(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.(\d{3}|\d{6}))?)?(Z|[+-]\d{2}:\d{2})?$')
URL = re.compile(r'^https?://\S+$', re.IGNORECASE)
NOTE_LINE = re.compile(r'^\s*- \[(N-[A-Z0-9-]+)\]\s+(.+?)\s*$')
NOTE = 'Metadata validation only. Evidence authenticity, scope and release readiness require review.'


# ---------------------------------------------------------------- catalog and notes

def load_catalog():
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def catalog_sha256():
    return hashlib.sha256(CATALOG.read_bytes()).hexdigest()


def load_notes():
    if not INTERPRETATION.exists():
        return {}
    notes = {}
    for line in INTERPRETATION.read_text(encoding='utf-8').splitlines():
        match = NOTE_LINE.match(line)
        if match:
            if match.group(1) in notes:
                raise ValueError(f'duplicate caveat key {match.group(1)} in interpretation.md')
            notes[match.group(1)] = match.group(2)
    return notes


def initialize(catalog):
    return {
        'schema_version': 2,
        'catalog_version': catalog.get('catalog_version', 'unknown'),
        'catalog_sha256': catalog_sha256(),
        'project': {'triggers': []},
        'controls': [dict(
            id=c['id'], concern=c['concern'], applicability='conditional',
            status='unverified', rationale='', owner='', acceptance='',
            revisit_trigger=PRISTINE_TRIGGER, evidence=[]
        ) for c in catalog['controls']],
    }


def review_label_error(verifier):
    """POST-133 evidence must say who reviewed and how: 'self-review: NAME' or 'independent: NAME'."""
    text = verifier.strip().lower() if isinstance(verifier, str) else ''
    prefix = next((p for p in REVIEW_PREFIXES if text.startswith(p)), None)
    if prefix is None:
        return f'{REVIEW_CONTROL} needs a verifier starting with "self-review:" or "independent:" (see N-SOLO-REVIEW)'
    if not any(ch.isalpha() for ch in text[len(prefix):]):
        return f'{REVIEW_CONTROL} verifier needs a name after "{prefix}"'
    return None


def is_independent(verifier):
    return isinstance(verifier, str) and verifier.strip().lower().startswith('independent:')


def apply_upgrade(data, catalog):
    """Add untouched rows for catalog controls this ledger predates; never edits or removes existing rows."""
    controls = data.get('controls')
    if not isinstance(controls, list) or any(not isinstance(c, dict) or not isinstance(c.get('id'), str) for c in controls):
        raise ValueError('ledger controls are corrupted (not a list, or a row without a string id); fix by hand, nothing written')
    ids = [c['id'] for c in controls]
    if len(ids) != len(set(ids)):
        raise ValueError(f'duplicate control ids {sorted({i for i in ids if ids.count(i) > 1})}; resolve them by hand, nothing written')
    rows = control_rows(data)
    have = {c.get('id') for c in rows}
    expected = [c['id'] for c in catalog['controls']]
    extra = sorted(i for i in have if i not in set(expected))
    if extra:
        raise ValueError(f'ledger has controls the catalog no longer lists ({extra}); resolve them by hand, nothing written')
    fresh = {c['id']: c for c in initialize(catalog)['controls']}
    added = [i for i in expected if i not in have]
    by_id = {c['id']: c for c in rows}
    data['controls'] = [by_id.get(i) or fresh[i] for i in expected]
    old = data.get('catalog_version', 'unknown')
    data['catalog_version'], data['catalog_sha256'] = catalog.get('catalog_version', 'unknown'), catalog_sha256()
    return {'added': added, 'catalog_version': {'from': old, 'to': data['catalog_version']},
            'note': 'new rows are untouched; run `slice` or `next` to classify the ones your product needs'}


# ---------------------------------------------------------------- small helpers

def blank(value):
    return not isinstance(value, str) or not value.strip()


def is_unclassified(c):
    """Pristine init row. Allowed in slice mode, rejected in release mode.

    The revisit trigger must still be the init text, so a classified row that was edited
    back to blank fields is no longer treated as untouched.
    """
    return (c.get('applicability') == 'conditional' and c.get('status') == 'unverified'
            and blank(c.get('rationale')) and blank(c.get('owner')) and blank(c.get('acceptance'))
            and c.get('revisit_trigger') == PRISTINE_TRIGGER and c.get('evidence') == [])


def control_rows(data):
    """Rows that are dicts with a string id; malformed rows are reported by validate()."""
    controls = data.get('controls') if isinstance(data, dict) else None
    if not isinstance(controls, list):
        return []
    return [c for c in controls if isinstance(c, dict) and isinstance(c.get('id'), str)]


def declared_triggers(data):
    project = data.get('project') if isinstance(data, dict) else None
    triggers = project.get('triggers') if isinstance(project, dict) else None
    return [t for t in triggers if isinstance(t, str)] if isinstance(triggers, list) else []


def is_iso8601(text):
    """Strict shape first so Python 3.9 and 3.14 agree (fromisoformat differs between them)."""
    text = str(text).strip()
    if not TIMESTAMP.match(text):
        return False
    try:
        datetime.fromisoformat(text.replace('Z', '+00:00'))
        return True
    except ValueError:
        return False


def is_future(text):
    moment = datetime.fromisoformat(str(text).strip().replace('Z', '+00:00'))
    now = datetime.now(moment.tzinfo) if moment.tzinfo else datetime.now()
    return moment > now + timedelta(minutes=5)


def normalize_text(value):
    """Fold width and case, drop invisible characters, collapse spaces, trim trailing punctuation."""
    text = unicodedata.normalize('NFKC', str(value))
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Cf')
    return ' '.join(text.split()).lower().strip(' .,:;!?\'"`')


def is_placeholder(field, value):
    text = normalize_text(value)
    return text == '' or text in PLACEHOLDERS or (field == 'check' and len(text) < MIN_CHECK_LENGTH)


def is_url(text):
    return bool(URL.match(str(text).strip()))


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b''):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_file(path, roots):
    for root in roots:
        try:
            candidate = root / path
            if candidate.exists():
                return candidate
        except (OSError, ValueError):  # over-long names and NUL bytes differ by platform and Python version
            continue
    return None


def artifact_missing(path, roots):
    return not is_url(path) and resolve_file(path, roots) is None


def latest_evidence(row):
    evidence = row.get('evidence')
    return evidence[-1] if isinstance(evidence, list) and evidence and isinstance(evidence[-1], dict) else None


def evidence_drift(row, roots):
    """Problems between a verified row's latest evidence and the files on disk."""
    last = latest_evidence(row) if row.get('status') == 'verified' else None
    out = []
    if last is None:
        return out
    artifact = last.get('artifact')
    if not blank(artifact) and not is_url(artifact):
        found = resolve_file(artifact.strip(), roots)
        if found is None:
            out.append(f'{row["id"]}: artifact not found: {artifact.strip()}')
        elif not found.is_file():
            out.append(f'{row["id"]}: artifact is not a regular file: {artifact.strip()}')
        elif last.get('artifact_sha256') and sha256_file(found) != last['artifact_sha256']:
            out.append(f'{row["id"]}: artifact changed since verification: {artifact.strip()}')
    watched = last.get('watched')
    if isinstance(watched, dict):
        for rel, digest in watched.items():
            found = resolve_file(rel, roots)
            if found is None:
                out.append(f'{row["id"]}: watched file missing since verification: {rel}')
            elif not found.is_file() or sha256_file(found) != digest:
                out.append(f'{row["id"]}: watched file changed since verification: {rel}')
    return out


# ---------------------------------------------------------------- validation

def entry_errors(ident, e):
    """Shape, placeholder and timestamp problems in one evidence entry."""
    errors = []
    for field in EVIDENCE_FIELDS:
        if blank(e.get(field)):
            errors.append(f'{ident}: evidence missing {field}')
    for field in PLACEHOLDER_FIELDS:
        if not blank(e.get(field)) and is_placeholder(field, e[field]):
            errors.append(f'{ident}: evidence {field} looks like a placeholder: {e[field].strip()!r}')
    if not blank(e.get('timestamp')):
        if not is_iso8601(e['timestamp']):
            errors.append(f'{ident}: evidence timestamp must be ISO-8601')
        elif is_future(e['timestamp']):
            errors.append(f'{ident}: evidence timestamp is in the future')
    return errors


def validate_row(c, meta, matched, release, roots):
    """All checks for one control row. `meta` is the catalog entry or None."""
    ident = c['id']
    errors = []
    app, status = c.get('applicability'), c.get('status')
    if not isinstance(app, str) or app not in APPLICABILITY:
        errors.append(f'{ident}: invalid applicability')
    if not isinstance(status, str) or status not in STATUS:
        errors.append(f'{ident}: invalid status')
    app = app if isinstance(app, str) else None
    evidence = c.get('evidence')
    if not isinstance(evidence, list):
        return errors + [f'{ident}: evidence must be an array']
    if any(not isinstance(e, dict) for e in evidence):
        return errors + [f'{ident}: evidence must be an object']
    kind = meta['kind'] if meta else 'control'
    triggers = set(meta['triggers']) if meta else set()
    if is_unclassified(c):
        if release and kind != 'responsibility':
            errors.append(f'{ident}: unclassified (release requires every control classified)')
        return errors
    for field in ('rationale', 'owner', 'acceptance'):
        if blank(c.get(field)):
            errors.append(f'{ident}: missing {field}')
    revisit = c.get('revisit_trigger')
    if app in {'conditional', 'deferred'} and (blank(revisit) or revisit.strip() == PRISTINE_TRIGGER):
        errors.append(f'{ident}: needs revisit trigger')
    if app == 'not-applicable' and 'always' in triggers:
        errors.append(f'{ident}: an always-on control cannot be not-applicable')
    elif release and app in NON_COMMITTAL and kind != 'responsibility' and matched & triggers and blank(c.get('accepted_by')):
        errors.append(f'{ident}: {app} on a control matching project triggers {sorted(matched & triggers)} needs accepted_by (the person who accepted this)')
    if status == 'verified':
        if not evidence:
            errors.append(f'{ident}: verified without evidence')
        for e in evidence:
            errors.extend(entry_errors(ident, e))
        last = latest_evidence(c)
        if last is not None and last.get('result') != 'passed':
            errors.append(f'{ident}: verified evidence result must be passed')
        if ident == REVIEW_CONTROL and last is not None:
            label = review_label_error(last.get('verifier'))
            if label:
                errors.append(f'{ident}: {label}')
            elif release and is_independent(last.get('verifier')) and blank(c.get('accepted_by')):
                errors.append(f'{ident}: independent review needs accepted_by: the user confirms who reviewed it, the agent cannot')
        if release and roots is not None and last is not None:
            errors.extend(evidence_drift(c, roots))
            artifact = last.get('artifact')
            if not blank(artifact) and not is_url(artifact) and not last.get('artifact_sha256'):
                errors.append(f'{ident}: release needs evidence recorded with the verify command (no artifact_sha256)')
    return errors


def validate(data, catalog, release=False, artifact_roots=None):
    if not isinstance(data, dict):
        return ['ledger must be a JSON object']
    errors = []
    if data.get('schema_version') not in SCHEMA_VERSIONS:
        errors.append(f'schema_version must be one of {list(SCHEMA_VERSIONS)}')
    vocabulary = set(catalog.get('trigger_vocabulary', {}))
    if 'project' in data:
        project = data['project']
        triggers = project.get('triggers', []) if isinstance(project, dict) else None
        if not isinstance(project, dict) or not isinstance(triggers, list) or not all(isinstance(t, str) for t in triggers):
            errors.append('project must be an object whose triggers is a list of strings')
        elif sorted(set(triggers) - vocabulary):
            errors.append(f'project.triggers has unknown triggers: {sorted(set(triggers) - vocabulary)}')
    if release and not declared_triggers(data):
        errors.append('release requires project triggers; record them with the triggers command')
    controls = data.get('controls')
    if not isinstance(controls, list):
        return errors + ['controls must be an array']
    if not all(isinstance(c, dict) for c in controls):
        return errors + ['each control must be an object']
    ids = [c.get('id') for c in controls]
    if any(not isinstance(i, str) for i in ids):
        return errors + ['every control needs a string id']
    expected = {c['id'] for c in catalog['controls']}
    if len(ids) != len(set(ids)):
        errors.append('duplicate control IDs')
    if set(ids) != expected:
        errors.append(f'catalog coverage mismatch: missing={sorted(expected-set(ids))}, extra={sorted(set(ids)-expected)}'
                      + ('; run `ledger.py upgrade` to add the new controls' if expected - set(ids) and not set(ids) - expected else ''))
    meta = {c['id']: c for c in catalog['controls']}
    matched = set(declared_triggers(data)) | {'always'}
    for c in controls:
        errors.extend(validate_row(c, meta.get(c['id']), matched, release, artifact_roots))
    return errors


def blockers(data, catalog):
    """Required-now controls not verified. Responsibility lanes never gate a release."""
    kinds = {c['id']: c for c in catalog['controls']}
    return [c['id'] for c in control_rows(data)
            if c.get('applicability') == 'required-now' and c.get('status') != 'verified'
            and kinds.get(c['id'], {}).get('kind') != 'responsibility']


def unclassified_relevant(data, catalog):
    """Controls the declared triggers (plus 'always') say matter, still untouched."""
    wanted = set(declared_triggers(data)) | {'always'}
    rows = {c['id']: c for c in control_rows(data)}
    return [m['id'] for m in catalog['controls'] if m['kind'] == 'control' and wanted & set(m['triggers'])
            and m['id'] in rows and is_unclassified(rows[m['id']])]


def warnings_for(data, catalog, artifact_roots=None):
    out = []
    rows = control_rows(data)
    if data.get('catalog_sha256') != catalog_sha256():
        out.append('catalog hash missing or changed since this ledger was initialized; review new or reworded controls')
    for field in ('rationale', 'acceptance'):
        counts = Counter(c[field].strip().lower() for c in rows if not blank(c.get(field)))
        for text, n in counts.items():
            if n >= BOILERPLATE_MIN_REPEATS:
                out.append(f'{field} repeated on {n} rows ("{text[:60]}"); likely form-filling, give each control its own reason')
    matched = set(declared_triggers(data)) | {'always'}
    meta = {c['id']: c for c in catalog['controls']}
    for c in rows:
        hit = matched & set(meta.get(c['id'], {}).get('triggers', []))
        if hit and c.get('applicability') in {'not-applicable', 'deferred'}:
            out.append(f'{c["id"]}: marked {c["applicability"]} although triggers {sorted(hit)} match')
    if artifact_roots is not None:
        for c in rows:
            if isinstance(c.get('evidence'), list):
                out.extend(evidence_drift(c, artifact_roots))
    pending = unclassified_relevant(data, catalog)
    if pending and declared_triggers(data):
        out.append(f'{len(pending)} controls relevant to the declared triggers are still unclassified')
    return out


# ---------------------------------------------------------------- triage views

def parse_triggers(text, catalog):
    values = [t.strip() for t in (text or '').split(',') if t.strip()]
    bad = sorted(set(values) - set(catalog['trigger_vocabulary']))
    if bad:
        raise ValueError(f'unknown triggers {bad}; run the vocabulary command')
    return values


def effective_triggers(data, override):
    return set(override) if override else set(declared_triggers(data))


def describe(meta, row, notes):
    out = {
        'id': meta['id'], 'concern': meta['concern'], 'kind': meta['kind'], 'domain': meta['domain'],
        'triggers': meta['triggers'], 'classified': not is_unclassified(row) if row else False,
        'applicability': row.get('applicability') if row else None,
        'status': row.get('status') if row else None,
        'caveats': [notes.get(k, f'[caveat text for {k} is missing from interpretation.md]') for k in meta.get('notes', [])],
    }
    if 'maps_to' in meta:
        out['maps_to'] = meta['maps_to']
    if meta.get('refs'):
        out['refs'] = meta['refs']
    return out


def relevant(data, catalog, triggers, include_lanes=False):
    wanted = set(triggers) | {'always'}
    notes, rows = load_notes(), {c['id']: c for c in control_rows(data)}
    return [describe(m, rows.get(m['id']), notes) for m in catalog['controls']
            if (include_lanes or m['kind'] == 'control') and wanted & set(m['triggers'])]


def brief(rows):
    """Compact view for agents: what it is, whether it is done, and what to beware of."""
    return [dict({'id': r['id'], 'concern': r['concern'], 'triggers': r['triggers'], 'classified': r['classified']},
                 **({'caveats': r['caveats']} if r['caveats'] else {}), **({'refs': r['refs']} if r.get('refs') else {}))
            for r in rows]


def next_batch(data, catalog, size, triggers):
    notes, rows = load_notes(), {c['id']: c for c in control_rows(data)}
    wanted = set(triggers) | {'always'}
    pending = [m for m in catalog['controls'] if m['id'] in rows and is_unclassified(rows[m['id']])]
    pending.sort(key=lambda m: (not (wanted & set(m['triggers'])), m['kind'] != 'control'))
    return [dict(describe(m, rows[m['id']], notes), relevant_now=bool(wanted & set(m['triggers'])))
            for m in pending[:size]]


def summary(data, catalog):
    rows = control_rows(data)
    return {
        'total': len(rows),
        'unclassified': sum(is_unclassified(c) for c in rows),
        'unclassified_relevant': unclassified_relevant(data, catalog),
        'by_applicability': dict(Counter(str(c.get('applicability')) for c in rows)),
        'by_status': dict(Counter(str(c.get('status')) for c in rows)),
        'required_now_unverified': blockers(data, catalog),
        'project_triggers': declared_triggers(data),
        'release_assessed': False,
    }


def render_slice(data, catalog, triggers, scope, roots):
    """Plain-language slice verdict and its exit code: 3 if a required-now row here is unverified or drifted."""
    meta = {c['id']: c for c in catalog['controls']}
    rows = {c['id']: c for c in control_rows(data)}
    picked = relevant(data, catalog, triggers)
    lanes = [m['id'] for m in catalog['controls'] if m['kind'] != 'control'] or ['none']
    verified, open_rows, drifted, slice_excluded = [], [], [], []
    lines = [f'Slice triggers: {", ".join(sorted(triggers)) or "none"} (plus `always` controls). '
             f'Scope: {cell(scope) if scope else "(not stated; add --scope)"}',
             f'{len(picked)} controls in this slice (work lanes {lanes[0]} to {lanes[-1]} hidden):']
    for r in picked:
        row = rows.get(r['id'])
        last = latest_evidence(row) if row else None
        if not r['classified']:
            state = 'untouched (nobody has decided whether it applies)'
        elif r['status'] == 'verified' and last:
            state = f'verified in {cell(last.get("environment"))} (revision {cell(last.get("revision"))})'
        else:
            state = f'{r["applicability"]}, {r["status"]}'
            if last and last.get('result') == 'failed':
                state += f' (last check failed in {cell(last.get("environment"))})'
        drift = evidence_drift(row, roots) if row else []
        if drift:
            drifted.append(r['id'])
            state += '; DRIFTED: ' + '; '.join(d.split(': ', 1)[1] for d in drift)
        if (r['classified'] and r['applicability'] in {'not-applicable', 'deferred'} and 'always' not in r['triggers']
                and blank((row or {}).get('accepted_by'))):
            slice_excluded.append(f'{r["id"]} ({r["applicability"]})')
        if 'always' in r['triggers'] and r['applicability'] in NON_COMMITTAL and r['classified'] and blank((row or {}).get('accepted_by')):
            open_rows.append(f'{r["id"]} (always-on but {r["applicability"]} with no accepted_by)')
        if r['applicability'] == 'required-now' and r['classified']:
            (verified if r['status'] == 'verified' and not drift else open_rows).append(
                f'{r["id"]} ({state.split(";")[0]})')
        keys = meta[r['id']].get('notes', [])
        lines.append(f'- {r["id"]} [{", ".join(r["triggers"])}] {r["concern"]}')
        lines.append(f'    state: {state}' + (f'; caveats: {", ".join(keys)}' if keys else '')
                     + (f'; see {", ".join(r["refs"])}' if r.get('refs') else ''))
    untouched = sum(not r['classified'] for r in picked)
    lines += ['', 'Verdict for this slice:',
              f'- Required-now and verified: {"; ".join(verified) or "none"}',
              f'- Required-now but not verified, failed or drifted: {"; ".join(open_rows) or "none"}',
              f'- Not examined (untouched): {untouched} of {len(picked)}',
              *([f'- {untouched} row(s) untouched: exit 0 does not mean they were checked; classify the ones this change could break.']
                if untouched else []),
              '- This is not a release check: `validate --release` answers that for the whole product.']
    declared = set(declared_triggers(data))
    notes = list(warnings_for(data, catalog, roots)) if data else []
    if declared and not (set(triggers) - {'always'}) & declared:
        notes.append(f'slice triggers share nothing with the declared product triggers ({", ".join(sorted(declared))}); check the slice touches what you think')
    if slice_excluded:
        notes.append(f'excluded by this slice\'s own triggers with no accepted_by: {", ".join(slice_excluded)}; ask the user')
    if notes:
        lines += ['', 'Warnings (same as `validate`):'] + [f'- {w}' for w in notes]
    return '\n'.join(lines), 3 if open_rows or drifted else 0


# ---------------------------------------------------------------- safe writing

@contextlib.contextmanager
def locked(path):
    """Exclusive advisory lock on a sidecar file so concurrent writers serialize."""
    real = Path(os.path.realpath(path))
    handle = open(real.with_name(real.name + '.lock'), 'a')
    try:
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        handle.close()


def atomic_write(path, data):
    """Write via a temp file in the real target directory; keep the file's mode and symlink."""
    real = Path(os.path.realpath(path))
    fd, tmp = tempfile.mkstemp(dir=str(real.parent), prefix='.ledger-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, indent=2)
            handle.write('\n')
        if real.exists():
            shutil.copymode(real, tmp)
        os.replace(tmp, real)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_ledger(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('ledger must be a JSON object')
    return data


def mutate(path, change):
    """Read-modify-write under the lock; nothing is written if `change` raises."""
    real = Path(os.path.realpath(path))
    if not real.is_file():
        raise FileNotFoundError(f'ledger not found: {path}')
    if not os.access(real, os.W_OK):
        raise ValueError(f'ledger is read-only: {path}')
    with locked(path):
        data = read_ledger(path)
        result = change(data)
        atomic_write(path, data)
        return result


# ---------------------------------------------------------------- recording commands

def find_row(data, ident):
    for c in control_rows(data):
        if c['id'] == ident:
            return c
    raise ValueError(f'unknown control id {ident}')


def row_context(data, catalog, ident):
    meta = {c['id']: c for c in catalog['controls']}.get(ident)
    return meta, set(declared_triggers(data)) | {'always'}


def apply_triggers(data, values):
    if not isinstance(data.get('project'), dict):
        data['project'] = {}
    data['project']['triggers'] = sorted(set(values))
    return {'project_triggers': data['project']['triggers']}


def apply_classify(data, catalog, ident, fields):
    row = find_row(data, ident)
    if fields.get('status') == 'verified':
        raise ValueError('use the verify command to set verified (it records the evidence)')
    if fields.get('accepted_by') is not None and blank(fields['accepted_by']):
        raise ValueError('accepted_by is blank; give the name of the person who accepted it')
    candidate = copy.deepcopy(row)
    candidate.update({k: v for k, v in fields.items() if v is not None})
    if is_unclassified(candidate):
        raise ValueError('that would reset the control to untouched; give applicability, rationale, owner and acceptance')
    meta, matched = row_context(data, catalog, ident)
    errors = validate_row(candidate, meta, matched, False, None)
    if errors:
        raise ValueError('; '.join(errors))
    row.update(candidate)
    return {'classified': ident, 'applicability': row['applicability'], 'status': row['status']}


def git_revision(cwd):
    try:
        rev = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=str(cwd), capture_output=True, text=True, timeout=10)
        if rev.returncode != 0 or not rev.stdout.strip():
            return None
        dirty = subprocess.run(['git', 'status', '--porcelain', '--', '.', ':(exclude).engineering'], cwd=str(cwd), capture_output=True, text=True, timeout=10)
        return rev.stdout.strip() + ('+dirty' if dirty.stdout.strip() else '')
    except (OSError, subprocess.SubprocessError):
        return None


def recorded_path(raw, root):
    """Store paths relative to the project root when inside it, so later checks do not depend on the cwd."""
    path = Path(raw.strip())
    absolute = Path(os.path.abspath(path if path.is_absolute() else Path.cwd() / path))
    try:
        return absolute.relative_to(root).as_posix()
    except ValueError:
        return str(absolute)


def regular_file(raw, root, what):
    recorded = recorded_path(raw, root)
    found = resolve_file(recorded, [root])
    if found is None:
        raise ValueError(f'{what} not found: {raw.strip()}')
    if not found.is_file():
        raise ValueError(f'{what} must be a regular file{" or an http(s) URL" if what == "artifact" else ""}: {raw.strip()}')
    return recorded, found


def drop_stale_notes(rationale):
    parts = [p for p in str(rationale or '').split(' | ') if not p.startswith('Stale (')]
    return ' | '.join(parts).strip()


def apply_verify(data, catalog, ident, check, environment, artifact, verifier, result, revision, watch, roots):
    row = find_row(data, ident)
    root = roots[0]
    if is_unclassified(row):
        raise ValueError('classify the control first (applicability, rationale, owner, acceptance)')
    if row.get('applicability') == 'not-applicable':
        raise ValueError('a not-applicable control has nothing to verify')
    if ident == REVIEW_CONTROL and result == 'passed' and review_label_error(verifier):
        raise ValueError(review_label_error(verifier) + '; an agent reviewing its own or another agent\'s diff is self-review')
    artifact = artifact.strip()
    entry = {'check': check, 'environment': environment, 'result': result, 'artifact': artifact,
             'revision': revision or git_revision(Path.cwd()) or '',
             'timestamp': datetime.now().astimezone().isoformat(timespec='seconds'), 'verifier': verifier}
    if not is_url(artifact):
        entry['artifact'], found = regular_file(artifact, root, 'artifact')
        entry['artifact_sha256'] = sha256_file(found)
    watched = {}
    for rel in watch or []:
        recorded, found = regular_file(rel, root, 'watched file')
        watched[recorded] = sha256_file(found)
    if watched:
        entry['watched'] = watched
    # Failed runs are checked too: otherwise a bad failed entry would block every later verify.
    problems = entry_errors(ident, entry)
    if problems:
        raise ValueError('; '.join(problems))
    candidate = copy.deepcopy(row)
    candidate['evidence'] = list(row['evidence']) + [entry]
    candidate['status'] = 'verified' if result == 'passed' else 'missing'
    if result == 'passed':
        candidate['rationale'] = drop_stale_notes(candidate.get('rationale'))
    meta, matched = row_context(data, catalog, ident)
    errors = validate_row(candidate, meta, matched, False, None)
    if errors:
        raise ValueError('; '.join(errors))
    row.update(candidate)
    return {'verified' if result == 'passed' else 'recorded_failure': ident, 'status': row['status'], 'evidence': entry}


def apply_stale(row, reason):
    reason = ' '.join(str(reason).split()).replace(' | ', ' / ')
    if not reason:
        raise ValueError('give a reason')
    if row.get('status') != 'verified':
        raise ValueError(f'{row["id"]} is not verified; only a verified control can be marked stale')
    stamp = datetime.now().astimezone().strftime('%Y-%m-%d')
    row['status'] = 'unverified'
    row['rationale'] = (str(row.get('rationale') or '').rstrip() + f' | Stale ({stamp}): {reason}').lstrip(' |')


def stale_rows(data, roots):
    out = {}
    for c in control_rows(data):
        drift = [d for d in evidence_drift(c, roots) if 'changed' in d or 'missing' in d or 'not found' in d]
        if drift:
            out[c['id']] = drift
    return out


# ---------------------------------------------------------------- report

def cell(value):
    """One line, no pipes: recorded text cannot break the table or start a heading."""
    return ' '.join(str(value).split()).replace('|', '\\|')


def render_report(data, catalog, scope, roots):
    rows = control_rows(data)
    meta = {c['id']: c for c in catalog['controls']}
    counts = summary(data, catalog)
    verified = [c for c in rows if c.get('status') == 'verified' and latest_evidence(c)]
    pending = unclassified_relevant(data, catalog)
    excluded = [c for c in rows if c.get('applicability') in ('not-applicable', 'deferred') and not is_unclassified(c)]
    drift = stale_rows(data, roots)
    environments = sorted({cell(latest_evidence(c).get('environment')) for c in verified})
    lines = [
        '# Release report (generated from the engineering ledger)', '',
        f'- Generated: {datetime.now().astimezone().isoformat(timespec="seconds")}',
        f'- Scope: {cell(scope) if scope else "(not stated; add --scope)"}',
        f'- Catalog: {data.get("catalog_version", "unknown")}; project triggers: {", ".join(counts["project_triggers"]) or "none declared"}',
        '- This report summarizes ledger metadata. It is not a security assessment, certification or readiness claim.', '',
        '## Counts', '',
        f'- Controls: {counts["total"]}; untouched: {counts["unclassified"]}; relevant to declared triggers but untouched: {len(pending)}',
        f'- By applicability: {json.dumps(counts["by_applicability"], sort_keys=True)}',
        f'- By status: {json.dumps(counts["by_status"], sort_keys=True)}', '',
        '## Release blockers (required-now, not verified)', '']
    lines += [f'- {i}: {meta[i]["concern"]}' for i in counts['required_now_unverified'] if i in meta] or ['- none recorded']
    lines += ['', '## Verification drifted since recorded', '']
    lines += [f'- {i}: {cell("; ".join(d.split(": ", 1)[1] for d in ds))}' for i, ds in sorted(drift.items())] or ['- none detected']
    lines += ['', '## Relevant controls still untouched', '']
    lines += [f'- {i}: {meta[i]["concern"]}' for i in pending[:OUTPUT_CAP]] or ['- none']
    if len(pending) > OUTPUT_CAP:
        lines.append(f'- ...and {len(pending) - OUTPUT_CAP} more (see `ledger.py next`)')
    lines += ['', '## Exclusions and deferrals', '']
    lines += [f'- {c["id"]} ({c["applicability"]}), '
              f'{"accepted by " + cell(c["accepted_by"]) if not blank(c.get("accepted_by")) else "NO accepted_by recorded"}; '
              f'revisit: {cell(c.get("revisit_trigger") or "none")}; {cell(c.get("rationale", ""))}' for c in excluded] or ['- none']
    lines += ['', '## Verified controls', '']
    if verified:
        lines += ['| Control | Environment | Revision | Recorded | Verifier | Accepted by | Artifact |', '|---|---|---|---|---|---|---|']
        for c in verified:
            e = latest_evidence(c)
            lines.append(f'| {c["id"]} | {cell(e.get("environment"))} | {cell(e.get("revision"))} | {cell(e.get("timestamp"))} '
                         f'| {cell(e.get("verifier"))} | {cell(c.get("accepted_by") or "")} | {cell(e.get("artifact"))} |')
            if c['id'] == REVIEW_CONTROL and is_independent(e.get('verifier')) and blank(c.get('accepted_by')):
                lines.append(f'\n- {REVIEW_CONTROL} claims independent review but no accepted_by confirms it; treat as self-review.\n')
            if c['id'] == REVIEW_CONTROL and str(e.get('verifier', '')).strip().lower().startswith('self-review'):
                lines.append(f'\n- {REVIEW_CONTROL} was self-reviewed: no independent review was recorded (POST-148 lane).\n')
    else:
        lines.append('- none')
    lines += ['', '## Evidence environments seen', '']
    lines += [f'- {e}' for e in environments] or ['- none']
    lines += ['', '## Boundaries to report separately (the ledger cannot tell these apart; none is claimed here)', '',
              '- Local tests, CI, deployed revision identity, public smoke test, authenticated/admin QA, real multi-user acceptance, '
              'operational drills (restore, rollback, alert): state which were run, where, and which were not.', '',
              '## Not assessed here', '',
              '- Whether recorded evidence is truthful; external obligations (legal, security, accessibility); production readiness.', '']
    return '\n'.join(lines)


# ---------------------------------------------------------------- CLI

def emit(payload):
    print(json.dumps(payload, indent=2))


def positive_int(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError('must be 1 or greater')
    return value


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init', help='create a blank ledger (never overwrites)').add_argument('--out', type=Path, required=True)
    v = sub.add_parser('validate', help='check ledger consistency; --release is the full sweep')
    v.add_argument('path', type=Path)
    mode = v.add_mutually_exclusive_group()
    mode.add_argument('--release', action='store_true', help='full sweep: every control classified, triggers declared, evidence hashed')
    mode.add_argument('--structure-only', action='store_true', help='ignore required-now blockers when computing exit code')
    v.add_argument('--verbose', action='store_true', help=f'print every error and warning instead of the first {OUTPUT_CAP} of each')
    sub.add_parser('summary', help='counts and unclassified relevant controls').add_argument('path', type=Path)
    r = sub.add_parser('relevant', help='controls matching triggers, with caveats')
    r.add_argument('path', type=Path, nargs='?', help='ledger; omit for a read-only view (requires --triggers)')
    r.add_argument('--triggers', help='comma-separated; defaults to the ledger project triggers')
    r.add_argument('--include-lanes', action='store_true')
    r.add_argument('--brief', action='store_true', help='id, concern, classified and caveats only')
    n = sub.add_parser('next', help='next batch of unclassified relevant controls')
    n.add_argument('path', type=Path)
    n.add_argument('--batch', type=positive_int, default=10)
    n.add_argument('--triggers')
    t = sub.add_parser('triggers', help='record the project triggers in the ledger')
    t.add_argument('path', type=Path)
    t.add_argument('values', nargs='?', help='comma-separated trigger names')
    t.add_argument('--clear', action='store_true', help='explicitly remove all recorded triggers')
    c = sub.add_parser('classify', help='record how a control applies (never sets verified)')
    c.add_argument('path', type=Path)
    c.add_argument('id')
    c.add_argument('--applicability', choices=sorted(APPLICABILITY))
    c.add_argument('--status', choices=sorted(STATUS - {'verified'}))
    c.add_argument('--rationale')
    c.add_argument('--owner')
    c.add_argument('--acceptance')
    c.add_argument('--revisit-trigger', dest='revisit_trigger')
    c.add_argument('--accepted-by', dest='accepted_by')
    e = sub.add_parser('verify', help='record a check result; hashes the artifact and stamps the time')
    e.add_argument('path', type=Path)
    e.add_argument('id')
    e.add_argument('--check', required=True, help='the command or procedure that was run')
    e.add_argument('--environment', required=True)
    e.add_argument('--artifact', required=True, help='local file holding the output, or an http(s) URL')
    e.add_argument('--verifier', required=True, help='who or what ran it')
    e.add_argument('--result', choices=['passed', 'failed'], default='passed')
    e.add_argument('--revision', help='defaults to the git short hash (+dirty) of the current directory')
    e.add_argument('--watch', action='append', help='source file this verification depends on; repeatable')
    u = sub.add_parser('upgrade', help='add untouched rows for controls added to the catalog since init')
    u.add_argument('path', type=Path)
    s = sub.add_parser('stale', help='send a verified control back to unverified, keeping its evidence')
    s.add_argument('path', type=Path)
    s.add_argument('id')
    s.add_argument('--reason', required=True)
    k = sub.add_parser('stale-check', help='list verified controls whose artifact or watched files changed')
    k.add_argument('path', type=Path)
    k.add_argument('--apply', action='store_true', help='mark each listed control unverified')
    rp = sub.add_parser('report', help='render a release report from the ledger')
    rp.add_argument('path', type=Path)
    rp.add_argument('--scope', help='one line: what was assessed and where')
    rp.add_argument('--out', type=Path, help='write here (refuses to overwrite) instead of printing')
    sub.add_parser('vocabulary', help='list valid trigger names')
    sl = sub.add_parser('slice', help='read-only, plain-language verdict for the controls a change touches')
    sl.add_argument('path', type=Path, nargs='?', help='ledger; omit to list the slice before a ledger exists')
    sl.add_argument('--triggers', required=True, help='comma-separated triggers the slice touches')
    sl.add_argument('--scope', help='one line: what the slice covers, local or deployed')
    return parser


def project_root(path):
    """Evidence paths are relative to the project root: the folder above .engineering/, else the ledger's folder."""
    ledger_dir = Path(path).resolve().parent
    return ledger_dir.parent if ledger_dir.name == '.engineering' else ledger_dir


def roots_for(path):
    return [project_root(path)]


def run_validate(args, data, catalog):
    roots = roots_for(args.path)
    errors = validate(data, catalog, release=args.release, artifact_roots=roots)
    errors.sort(key=lambda e: 'unclassified (' in e)  # stable: actionable problems first, the bulk list last
    open_blockers = blockers(data, catalog)
    warnings = warnings_for(data, catalog, artifact_roots=roots)
    if args.release:
        warnings = [w for w in warnings if w not in errors]
    attention = [w for w in warnings if 'although triggers' in w]
    others = [w for w in warnings if w not in attention]
    cap = (lambda items: items) if args.verbose else (lambda items: items[:OUTPUT_CAP])
    pending = unclassified_relevant(data, catalog)
    emit({
        'structurally_valid': not errors,
        'mode': 'structure-only' if args.structure_only else 'release' if args.release else 'slice',
        'errors_total': len(errors), 'errors': cap(errors),
        'non_committal_on_triggered_total': len(attention), 'non_committal_on_triggered': cap(attention),
        'warnings_total': len(others), 'warnings': cap(others),
        'output_truncated': not args.verbose and any(len(x) > OUTPUT_CAP for x in (errors, attention, others)),
        'unclassified_count': sum(is_unclassified(c) for c in control_rows(data)),
        'unclassified_relevant_total': len(pending), 'unclassified_relevant': cap(pending),
        'required_now_unverified': open_blockers, 'release_assessed': False, 'note': NOTE,
    })
    if errors:
        return 1
    return 0 if args.structure_only or not open_blockers else 3


def main():
    args = build_parser().parse_args()
    catalog = load_catalog()
    cmd = args.command
    if cmd == 'vocabulary':
        emit(catalog['trigger_vocabulary'])
        return 0
    if cmd == 'init':
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open('x', encoding='utf-8') as handle:
            json.dump(initialize(catalog), handle, indent=2)
            handle.write('\n')
        emit({'initialized': len(catalog['controls']), 'path': str(args.out), 'status': 'all controls unverified'})
        return 0
    if cmd == 'relevant' and args.path is None:
        if not args.triggers:
            raise ValueError('give --triggers when no ledger path is provided')
        rows = relevant({}, catalog, set(parse_triggers(args.triggers, catalog)), args.include_lanes)
        emit(brief(rows) if args.brief else rows)
        return 0
    if cmd == 'slice':
        triggers = set(parse_triggers(args.triggers, catalog))
        if args.path is None:
            text, _ = render_slice({}, catalog, triggers, args.scope, [])
            print('No ledger given: nothing is classified or verified yet. Create one with `init --out PATH`.\n' + text)
            return 0
        data = read_ledger(args.path)
        problems = validate(data, catalog)
        if problems:
            listed = '\n'.join(f'- {e}' for e in problems[:OUTPUT_CAP])
            print(f'Ledger has {len(problems)} structural errors; no verdict until they are fixed:\n{listed}')
            return 1
        text, code = render_slice(data, catalog, triggers, args.scope, roots_for(args.path))
        print(text)
        return code
    if cmd == 'triggers':
        if args.clear == bool(args.values):
            raise ValueError('give trigger names, or --clear (not both, not neither)')
        values = [] if args.clear else parse_triggers(args.values, catalog)
        if not args.clear and not values:
            raise ValueError('no trigger names given; use --clear to remove all')
        emit(mutate(args.path, lambda data: apply_triggers(data, values)))
        return 0
    if cmd == 'classify':
        fields = {k: getattr(args, k) for k in ('applicability', 'status', 'rationale', 'owner', 'acceptance', 'revisit_trigger', 'accepted_by')}
        if all(v is None for v in fields.values()):
            raise ValueError('nothing to change; give at least one field')
        emit(mutate(args.path, lambda data: apply_classify(data, catalog, args.id, fields)))
        return 0
    if cmd == 'verify':
        roots = roots_for(args.path)
        emit(mutate(args.path, lambda data: apply_verify(data, catalog, args.id, args.check, args.environment, args.artifact,
                                                         args.verifier, args.result, args.revision, args.watch, roots)))
        return 0
    if cmd == 'upgrade':
        emit(mutate(args.path, lambda data: apply_upgrade(data, catalog)))
        return 0
    if cmd == 'stale':
        def change(data):
            row = find_row(data, args.id)
            apply_stale(row, args.reason)
            return {'stale': args.id, 'status': row['status']}
        emit(mutate(args.path, change))
        return 0
    if cmd == 'stale-check' and args.apply:
        roots = roots_for(args.path)

        def change(data):
            drifted = stale_rows(data, roots)
            for ident, drift in drifted.items():
                apply_stale(find_row(data, ident), '; '.join(d.split(': ', 1)[1] for d in drift))
            return {'marked_unverified': sorted(drifted), 'details': drifted}
        emit(mutate(args.path, change))
        return 0
    data = read_ledger(args.path)
    if cmd == 'summary':
        emit(summary(data, catalog))
    elif cmd == 'relevant':
        rows = relevant(data, catalog, effective_triggers(data, parse_triggers(args.triggers, catalog)), args.include_lanes)
        emit(brief(rows) if args.brief else rows)
    elif cmd == 'next':
        emit(next_batch(data, catalog, args.batch, effective_triggers(data, parse_triggers(args.triggers, catalog))))
    elif cmd == 'stale-check':
        drifted = stale_rows(data, roots_for(args.path))
        emit({'drifted': drifted, 'total': len(drifted), 'applied': False})
        return 0 if not drifted else 3
    elif cmd == 'report':
        problems = validate(data, catalog)
        if problems:
            raise ValueError(f'ledger has {len(problems)} structural errors (first: {problems[0]}); run validate and fix them before reporting')
        text = render_report(data, catalog, args.scope, roots_for(args.path))
        if args.out:
            with args.out.open('x', encoding='utf-8') as handle:
                handle.write(text)
            emit({'report': str(args.out), 'bytes': len(text.encode('utf-8'))})
        else:
            print(text)
    elif cmd == 'validate':
        return run_validate(args, data, catalog)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        print(f'Ledger error: {exc}', file=sys.stderr)
        sys.exit(2)

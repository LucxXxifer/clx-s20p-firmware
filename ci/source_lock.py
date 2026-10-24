#!/usr/bin/env python3
"""Fail-closed Git source locks and auditable post-patch source evidence.

Examples:
  source_lock.py get URL [--ref BRANCH]
  source_lock.py pin-feeds /path/to/feeds.conf.default
  source_lock.py verify CHECKOUT [--expected URL] [--allow-dirty]
  source_lock.py capture CHECKOUT --output /outside/checkout/source.json
  source_lock.py verify-capture CHECKOUT /path/to/source.json

No command accesses the network. HTTP/release assets remain separate inputs and
must pass preservation/difference review; a Git lock is not bit reproducibility.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

DEFAULT_LOCK = Path(__file__).resolve().with_name('sources.lock.json')


class LockError(ValueError):
    pass


def canonical_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or parsed.port):
        raise LockError('source URL must be credential-free HTTPS: ' + url)
    path = parsed.path.rstrip('/')
    if path.endswith('.git'):
        path = path[:-4]
    if not path or '..' in path.split('/'):
        raise LockError('invalid source repository path: ' + url)
    # GitHub repository names are case-insensitive; other hosts need not be.
    if parsed.hostname.lower() == 'github.com':
        path = path.lower()
    return 'https://' + parsed.hostname.lower() + path


def normalize_ref(ref):
    return ref.removeprefix('refs/heads/') if ref else None


def lookup(lock, url, ref=None):
    if lock.get('schema_version') != 1 or not isinstance(lock.get('sources'), list):
        raise LockError('unsupported source lock schema')
    records = {}
    for row in lock['sources']:
        if not isinstance(row, dict) or not isinstance(row.get('url'), str):
            raise LockError('invalid lock entry')
        key = canonical_url(row['url'])
        if key in records:
            raise LockError('ambiguous duplicate repository: ' + key)
        if not re.fullmatch(r'[0-9a-f]{40}', row.get('commit', '')):
            raise LockError('repository lacks a full pinned commit: ' + key)
        records[key] = row
    key = canonical_url(url)
    if key not in records:
        raise LockError('unlocked Git repository: ' + key)
    row = records[key]
    if ref and normalize_ref(ref) != normalize_ref(row.get('ref')):
        raise LockError('unlocked ref for ' + key + ': ' + ref)
    return row['commit']


def pin_feeds_text(text, lock):
    result = []
    for line in text.splitlines(keepends=True):
        if not re.match(r'^\s*src-git(?:-full)?\s', line):
            result.append(line)
            continue
        match = re.fullmatch(r'(\s*src-git(?:-full)?\s+\S+\s+)(\S+)([^\r\n]*)(\r?\n)?', line)
        if not match:
            raise LockError('malformed Git feed line: ' + line.rstrip())
        prefix, spec, trailing, newline = match.groups()
        if trailing.strip() and not trailing.lstrip().startswith('#'):
            raise LockError('unsupported Git feed options: ' + line.rstrip())
        if '^' in spec:
            if ';' in spec or spec.count('^') != 1:
                raise LockError('ambiguous feed source pin: ' + spec)
            url, supplied = spec.split('^')
            commit = lookup(lock, url)
            if supplied != commit:
                raise LockError('feed pin disagrees with lock: ' + spec)
        else:
            parts = spec.split(';')
            if len(parts) > 2:
                raise LockError('ambiguous feed ref: ' + spec)
            url = parts[0]
            commit = lookup(lock, url, parts[1] if len(parts) == 2 else None)
        result.append(prefix + url + '^' + commit + trailing + (newline or ''))
    return ''.join(result)


def git(path, *args):
    result = subprocess.run(['git', '-C', str(path), *args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise LockError('git ' + ' '.join(args) + ': ' + result.stderr.decode(errors='replace').strip())
    return result.stdout


def digest(value):
    return hashlib.sha256(value).hexdigest()


def json_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()


def file_record(root, name):
    path = root / name
    if not path.parent.resolve().is_relative_to(root):
        raise LockError('source path crosses a symlink outside checkout: ' + name)
    row = {'path': name}
    try:
        info = path.lstat()
    except FileNotFoundError:
        return dict(row, type='missing', mode=None, sha256=None)
    row['mode'] = format(stat.S_IMODE(info.st_mode), '04o')
    if stat.S_ISLNK(info.st_mode):
        target = os.readlink(path)
        row.update(type='symlink', target=target, sha256=digest(os.fsencode(target)))
    elif stat.S_ISREG(info.st_mode):
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024*1024), b''):
                h.update(block)
        row.update(type='file', size=info.st_size, sha256=h.hexdigest())
    elif stat.S_ISDIR(info.st_mode):
        # Nested Git repositories/submodules require their own lock/evidence.
        raise LockError('dirty directory needs separate source capture: ' + name)
    else:
        raise LockError('unsupported source file type: ' + name)
    return row


def capture(path, lock, expected=None):
    path = Path(path).resolve()
    toplevel = Path(os.fsdecode(git(path, 'rev-parse', '--show-toplevel')).strip()).resolve()
    if path != toplevel:
        raise LockError('capture requires the checkout root: ' + str(toplevel))
    origin = os.fsdecode(git(path, 'remote', 'get-url', 'origin')).strip()
    if expected and canonical_url(origin) != canonical_url(expected):
        raise LockError('checkout origin does not match expected source URL')
    required = lookup(lock, expected or origin)
    head = git(path, 'rev-parse', 'HEAD').decode().strip()
    if head != required:
        raise LockError('checkout HEAD differs from lock: ' + head + ' != ' + required)
    raw_status = git(path, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    # A merge can look like a dirty tree but is never a valid build input.
    if git(path, 'ls-files', '--unmerged', '-z'):
        raise LockError('checkout has unresolved index conflicts')
    changed = git(path, 'diff', '--no-ext-diff', '--no-textconv', '--name-only', '-z', 'HEAD')
    untracked = git(path, 'ls-files', '--others', '--exclude-standard', '-z')
    names = sorted({os.fsdecode(item) for item in (changed + untracked).split(b'\0') if item})
    for name in names:
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise LockError('unsafe source path: ' + name)
    evidence = {
        'schema_version': 1,
        'source_url': canonical_url(origin),
        'locked_commit': required,
        'head': head,
        'git_tree': git(path, 'rev-parse', 'HEAD^{tree}').decode().strip(),
        'git_status_porcelain_z_sha256': digest(raw_status),
        'index_diff_sha256': digest(git(path, 'diff', '--no-ext-diff', '--no-textconv', '--cached', '--binary', 'HEAD')),
        'worktree_diff_sha256': digest(git(path, 'diff', '--no-ext-diff', '--no-textconv', '--binary')),
        'dirty_files': [file_record(path, name) for name in names],
        'limitations': 'Ignored untracked files are excluded; nested repositories require separate capture. This records hashes, not source file contents.',
    }
    evidence['source_state_sha256'] = digest(json_bytes(evidence))
    return evidence


def verify_checkout(path, lock, expected=None, allow_dirty=False):
    evidence = capture(path, lock, expected)
    if evidence['dirty_files'] and not allow_dirty:
        raise LockError('checkout is dirty; use capture to audit intended patches')
    return evidence


def verify_capture(path, lock, evidence):
    current = capture(path, lock, evidence.get('source_url'))
    if current != evidence:
        raise LockError('checkout state differs from recorded source evidence')
    return current


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.')
    try:
        with os.fdopen(handle, 'w') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, old_mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--lock', type=Path, default=DEFAULT_LOCK)
    commands = parser.add_subparsers(dest='command', required=True)
    get_parser = commands.add_parser('get')
    get_parser.add_argument('url')
    get_parser.add_argument('--ref')
    pin_parser = commands.add_parser('pin-feeds')
    pin_parser.add_argument('path', type=Path)
    for name in ['verify', 'capture']:
        item = commands.add_parser(name)
        item.add_argument('path', type=Path)
        item.add_argument('--expected')
        if name == 'verify':
            item.add_argument('--allow-dirty', action='store_true')
        else:
            item.add_argument('--output', type=Path, required=True)
    compare = commands.add_parser('verify-capture')
    compare.add_argument('path', type=Path)
    compare.add_argument('snapshot', type=Path)
    args = parser.parse_args(argv)
    try:
        lock = json.loads(args.lock.read_text())
        if args.command == 'get':
            print(lookup(lock, args.url, args.ref))
        elif args.command == 'pin-feeds':
            atomic_write(args.path, pin_feeds_text(args.path.read_text(), lock))
        elif args.command == 'verify':
            row = verify_checkout(args.path, lock, args.expected, args.allow_dirty)
            print(json.dumps({'result': 'PASS', 'head': row['head'], 'dirty_files': len(row['dirty_files'])}))
        elif args.command == 'capture':
            output = args.output.resolve()
            if output.is_relative_to(args.path.resolve()):
                raise LockError('capture output must be outside checkout to avoid changing the captured state')
            row = capture(args.path, lock, args.expected)
            atomic_write(output, json.dumps(row, indent=2) + '\n')
            print(json.dumps({'result': 'PASS', 'head': row['head'], 'dirty_files': len(row['dirty_files']), 'source_state_sha256': row['source_state_sha256']}))
        else:
            row = verify_capture(args.path, lock, json.loads(args.snapshot.read_text()))
            print(json.dumps({'result': 'PASS', 'source_state_sha256': row['source_state_sha256']}))
    except (LockError, OSError, ValueError) as exc:
        print('source-lock: ' + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

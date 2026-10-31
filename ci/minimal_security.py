#!/usr/bin/env python3
"""Apply two frozen management fixes and bind them to same-build image evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from component_gate import bound_sources, manifest, read
from preservation import parse_packages

HERE = Path(__file__).resolve().parent / 'security/minimal-20261031'
MANIFEST_SHA256 = 'bc08a3a07d6cb86aa3d6195a2385534c1da05ec6c7139072d9fb8a8d917d1fec'
UHTTPD_RECIPE = 'package/network/services/uhttpd/Makefile'
PATCH_NAME = '100-ubus-reject-non-string-list-params.patch'
PATCH_PATH = 'package/network/services/uhttpd/patches/' + PATCH_NAME
ACL_SOURCE = 'feeds/luci/modules/luci-mod-system/root/usr/share/rpcd/acl.d/luci-mod-system.json'
ACL_INSTALLED = '/usr/share/rpcd/acl.d/luci-mod-system.json'
EXPECTED_PACKAGES = {
    'uhttpd': '2026.08.24~373145f7-r2',
    'uhttpd-mod-ubus': '2026.08.24~373145f7-r2',
    'luci-mod-system': '26.298.29275~c84adb9',
}
SCOPE = ('Exact upstream uhttpd list-parameter patch and advisory-based mounts ACL backport; '
         'bound source/recipe and installed ACL bytes; no ubus ABI or QuickStart update')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def regular(path, root=None):
    path = Path(path)
    if root is not None:
        root = Path(root)
        try:
            relative = path.relative_to(root)
        except ValueError as error:
            raise ValueError('security input outside expected root') from error
        current = root
        if current.is_symlink():
            raise ValueError('security root symlink rejected')
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError('security input symlink rejected: ' + str(path))
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
        raise ValueError('bounded regular security input required: ' + str(path))
    return path.read_bytes()


def payload_digest(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('regular rootfs payload required')
    value = hashlib.sha256()
    with path.open('rb') as stream:
        while data := stream.read(1024 * 1024):
            value.update(data)
    return value.hexdigest()


def profile():
    raw = regular(HERE / 'manifest.json', HERE)
    if digest(raw) != MANIFEST_SHA256:
        raise ValueError('minimal security profile digest mismatch')
    value = json.loads(raw)
    for row in value['files']:
        data = regular(HERE / row['path'], HERE)
        if digest(data) != row['sha256'] or len(data) != row['size']:
            raise ValueError('vendored security input SHA256 mismatch: ' + row['path'])
    before = json.loads(regular(HERE / 'luci-mod-system.before.json', HERE))
    del before['luci-mod-system-mounts']['write']['file']['/etc/crontabs/root']
    after = json.loads(regular(HERE / 'luci-mod-system.after.json', HERE))
    if before != after:
        raise ValueError('ACL backport differs from the precise upstream recommendation')
    return value


def profile_id():
    return profile()['id']


def profile_sha256():
    profile()
    return MANIFEST_SHA256


def patched_recipe():
    original = regular(HERE / 'uhttpd-Makefile.before', HERE)
    if original.count(b'PKG_RELEASE:=1\n') != 1:
        raise ValueError('unique original uhttpd release declaration required')
    return original.replace(b'PKG_RELEASE:=1\n', b'PKG_RELEASE:=2\n', 1)


def source_payloads():
    profile()
    return {
        UHTTPD_RECIPE: patched_recipe(),
        PATCH_PATH: regular(HERE / PATCH_NAME, HERE),
        ACL_SOURCE: regular(HERE / 'luci-mod-system.after.json', HERE),
    }


def file_row(path, data):
    return {'path': path, 'mode': '0o644', 'size': len(data), 'sha256': digest(data)}


def preparation_report():
    value = profile()
    return {
        'schema_version': 1, 'status': 'pass', 'profile_id': value['id'],
        'profile_sha256': profile_sha256(),
        'prepared_files': [file_row(path, data) for path, data in sorted(source_payloads().items())],
        'uhttpd': value['uhttpd'], 'luci_acl': value['luci_acl'],
    }


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('security output symlink rejected')
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def prepare(source_root, output):
    root, output = Path(source_root), Path(output)
    profile()
    # Check both complete original files before writing any backport.
    original = {
        ACL_SOURCE: 'luci-mod-system.before.json',
        UHTTPD_RECIPE: 'uhttpd-Makefile.before',
    }
    for relative, fixture in original.items():
        path = root / relative
        if regular(path, root) != regular(HERE / fixture, HERE):
            raise ValueError('minimal security source SHA changed: ' + relative)
        if path.stat().st_mode & 0o7777 != 0o644:
            raise ValueError('minimal security source mode changed: ' + relative)
    patches = (root / PATCH_PATH).parent
    if patches.is_symlink() or (patches.exists() and any(patches.iterdir())):
        raise ValueError('unreviewed pre-existing uhttpd patches rejected')
    if output.exists() or output.is_symlink():
        raise ValueError('existing minimal security preparation evidence rejected')
    for parent in output.parents:
        if parent.is_symlink():
            raise ValueError('security evidence parent symlink rejected')
        if parent.exists():
            break
    report = preparation_report()
    for relative, data in source_payloads().items():
        atomic_write(root / relative, data)
    atomic_write(output, (json.dumps(report, indent=2, sort_keys=True) + '\n').encode())
    return report


def wrapper_rows():
    value = profile()
    rows = [file_row('ci/security/minimal-20261031/manifest.json', regular(HERE / 'manifest.json', HERE))]
    for row in value['files']:
        rows.append(file_row('ci/security/minimal-20261031/' + row['path'], regular(HERE / row['path'], HERE)))
    module = Path(__file__).resolve()
    rows.append(file_row('ci/minimal_security.py', regular(module)))
    return sorted(rows, key=lambda row: row['path']), value


def verify_preparation(provenance):
    provenance = Path(provenance)
    expected = preparation_report()
    if read(provenance / 'minimal-security-preparation.json') != expected:
        raise ValueError('minimal security preparation/profile evidence mismatch')
    _, prepared = bound_sources(provenance)
    actual = {row['path']: row for row in prepared}
    for row in expected['prepared_files']:
        if actual.get(row['path']) != row:
            raise ValueError('minimal security prepared source mismatch: ' + row['path'])
    patch_prefix = str(Path(PATCH_PATH).parent) + '/'
    if {name for name in actual if name.startswith(patch_prefix)} != {PATCH_PATH}:
        raise ValueError('minimal security prepared patch inventory mismatch')
    wrapper = {row['path']: row for row in manifest(provenance / 'wrapper-source-files.json')}
    for row in wrapper_rows()[0]:
        if wrapper.get(row['path']) != row:
            raise ValueError('minimal security wrapper/profile source mismatch: ' + row['path'])
    return expected


def expected_report(payload_sha256, provenance, packages):
    if not re.fullmatch(r'[0-9a-f]{64}', str(payload_sha256)):
        raise ValueError('minimal security requires exact rootfs SHA256')
    preparation = verify_preparation(provenance)
    for name, version in EXPECTED_PACKAGES.items():
        if packages.get(name, {}).get('version') != version:
            raise ValueError('minimal security package version mismatch: ' + name)
    return {
        'schema_version': 1, 'status': 'pass', 'profile_id': profile_id(),
        'profile_sha256': profile_sha256(), 'rootfs_sha256': payload_sha256,
        'prepared_files': preparation['prepared_files'],
        'files': [file_row(ACL_INSTALLED, regular(HERE / 'luci-mod-system.after.json', HERE))],
        'packages': dict(EXPECTED_PACKAGES), 'scope': SCOPE, 'runtime_tested': False,
    }


def verify_report(value, payload_sha256, provenance, packages):
    if value != expected_report(payload_sha256, provenance, packages):
        raise ValueError('minimal security release report/source/payload mismatch')


def inspect(rootfs, payload, provenance):
    rootfs = Path(rootfs)
    payload_sha256 = payload_digest(payload)
    database = rootfs / 'lib/apk/db/installed'
    regular(database, rootfs)
    packages = parse_packages(database)
    value = expected_report(payload_sha256, provenance, packages)
    for row in value['files']:
        path = rootfs / row['path'].lstrip('/')
        if digest(regular(path, rootfs)) != row['sha256'] or path.stat().st_mode & 0o7777 != int(row['mode'], 8):
            raise ValueError('minimal security installed ACL SHA/mode mismatch')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    command = sub.add_parser('prepare')
    command.add_argument('--source-root', type=Path, required=True)
    command.add_argument('--output', type=Path, required=True)
    command = sub.add_parser('inspect')
    command.add_argument('--rootfs', type=Path, required=True)
    command.add_argument('--payload', type=Path, required=True)
    command.add_argument('--provenance', type=Path, required=True)
    command.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = prepare(args.source_root, args.output)
        else:
            result = inspect(args.rootfs, args.payload, args.provenance)
            atomic_write(args.output, (json.dumps(result, indent=2, sort_keys=True) + '\n').encode())
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, 'Minimal security gate: ' + str(error) + '\n')


if __name__ == '__main__':
    main()

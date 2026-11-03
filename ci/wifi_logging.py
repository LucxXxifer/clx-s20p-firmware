#!/usr/bin/env python3
"""Bind the frozen S20P Wi-Fi diagnostic patch to source, ABI and image bytes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

from component_gate import bound_sources, manifest, read
from minimal_security import atomic_write, payload_digest, regular
from preservation import parse_config, parse_packages

HERE = Path(__file__).resolve().parent / 'wifi-logging/20261103'
MANIFEST_SHA256 = '3192d26eb76ecdd969833e15d58d09be75fd13e7273e07911bce1f0bd5bee310'
RECIPE = 'package/mtk/drivers/mt_wifi/Makefile'
PATCH_DIR = 'package/mtk/drivers/mt_wifi/patches-7673'
PATCH_NAME = '100-s20p-bound-pmf-action-and-gtk-diagnostics.patch'
PATCH_PATH = PATCH_DIR + '/' + PATCH_NAME
KERNEL_VERSION = '6.12.112'
PACKAGE_NAME = 'kmod-mt_wifi'
PACKAGE_VERSION = KERNEL_VERSION + '.7.6.7.3-r2'
MODULES = tuple('/lib/modules/' + KERNEL_VERSION + '/' + name + '.ko'
                for name in ('mt_wifi', 'mtk_warp_proxy'))
SCOPE = ('AP SubType 13 rejection diagnostics only: global 5-second bucket, '
         'saturating suppression count printed on the next eligible event; '
         'routine GTK start/success at INFO; PMF policy and real GTK timeouts unchanged')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_row(path, data):
    return {'path': path, 'mode': '0o644', 'size': len(data), 'sha256': digest(data)}


def profile():
    raw = regular(HERE / 'manifest.json', HERE)
    if digest(raw) != MANIFEST_SHA256:
        raise ValueError('Wi-Fi logging profile digest mismatch')
    value = json.loads(raw)
    for row in value['files']:
        data = regular(HERE / row['path'], HERE)
        if file_row(row['path'], data) != row:
            raise ValueError('vendored Wi-Fi logging SHA256 mismatch: ' + row['path'])
    return value


def profile_id():
    return profile()['id']


def source_payloads():
    value = profile()
    original = regular(HERE / 'Makefile.before', HERE)
    declaration = b'PKG_VERSION:=7.6.7.3\n'
    if original.count(declaration) != 1 or b'PKG_RELEASE:=' in original:
        raise ValueError('exact implicit-r1 7673 recipe required')
    result = {
        RECIPE: original.replace(declaration, declaration + b'PKG_RELEASE:=2\n', 1),
        str(Path(RECIPE).parent / 'config.in'): regular(HERE / 'config.in.before', HERE),
        PATCH_PATH: regular(HERE / PATCH_NAME, HERE),
    }
    for row in value['upstream_patches']:
        result[PATCH_DIR + '/' + row['path']] = regular(HERE / 'upstream-patches' / row['path'], HERE)
    return result


def preparation_report():
    value = profile()
    return {
        'schema_version': 1, 'status': 'pass', 'profile_id': value['id'],
        'profile_sha256': MANIFEST_SHA256, 'upstream_commit': value['upstream_commit'],
        'archive': value['archive'], 'upstream_patches': value['upstream_patches'],
        'prepared_files': [file_row(path, data) for path, data in sorted(source_payloads().items())],
        'package': {'name': PACKAGE_NAME, 'version': PACKAGE_VERSION},
    }


def checkout_commit(root):
    result = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                            capture_output=True, text=True, check=False)
    if result.returncode or not re.fullmatch(r'[0-9a-f]{40}\n?', result.stdout):
        raise ValueError('exact Wi-Fi logging upstream checkout identity required')
    return result.stdout.strip()


def prepare(source_root, output):
    root, output = Path(source_root), Path(output)
    if '..' in root.parts or '..' in output.parts:
        raise ValueError('canonical Wi-Fi logging source/evidence paths required')
    root, output = Path(os.path.abspath(root)), Path(os.path.abspath(output))
    value = profile()
    if checkout_commit(root) != value['upstream_commit']:
        raise ValueError('Wi-Fi logging exact upstream checkout mismatch')
    originals = {RECIPE: HERE / 'Makefile.before',
                 str(Path(RECIPE).parent / 'config.in'): HERE / 'config.in.before'}
    originals.update({PATCH_DIR + '/' + row['path']: HERE / 'upstream-patches' / row['path']
                      for row in value['upstream_patches']})
    # Validate every original, the complete directory inventory, and the output
    # before any write. Extra/duplicate/symlink patches are never silently adopted.
    for relative, fixture in originals.items():
        path = root / relative
        if regular(path, root) != regular(fixture, HERE) or path.stat().st_mode & 0o7777 != 0o644:
            raise ValueError('Wi-Fi logging original source SHA/mode changed: ' + relative)
    patches = root / PATCH_DIR
    if {p.name for p in patches.iterdir()} != {row['path'] for row in value['upstream_patches']}:
        raise ValueError('Wi-Fi logging complete upstream patch inventory changed')
    if output.exists() or output.is_symlink():
        raise ValueError('existing Wi-Fi logging preparation evidence rejected')
    if output.is_relative_to(root / PATCH_DIR):
        raise ValueError('Wi-Fi logging evidence cannot alter the patch inventory')
    for relative in source_payloads():
        source = root / relative
        if output == source or output in source.parents or source in output.parents:
            raise ValueError('Wi-Fi logging evidence/source path collision')
    for parent in output.parents:
        if parent.is_symlink():
            raise ValueError('Wi-Fi logging evidence parent symlink rejected')
        if parent.exists() and not parent.is_dir():
            raise ValueError('Wi-Fi logging evidence parent must be a directory')
    report = preparation_report()
    atomic_write(root / RECIPE, source_payloads()[RECIPE])
    atomic_write(root / PATCH_PATH, source_payloads()[PATCH_PATH])
    atomic_write(output, (json.dumps(report, indent=2, sort_keys=True) + '\n').encode())
    return report


def wrapper_rows():
    value = profile()
    rows = [file_row('ci/wifi-logging/20261103/manifest.json', regular(HERE / 'manifest.json', HERE))]
    rows.extend(file_row('ci/wifi-logging/20261103/' + row['path'], regular(HERE / row['path'], HERE))
                for row in value['files'])
    wrapper = Path(__file__).resolve().parent.parent
    for relative in ('ci/wifi_logging.py', 'ci/build.sh', 'ci/capture.sh', 'ci/provenance.py',
                     'ci/preservation.py', 'ci/release_candidate.py', 'wrt_core/update.sh'):
        path = wrapper / relative
        row = file_row(relative, regular(path, wrapper))
        row['mode'] = oct(path.stat().st_mode & 0o7777)
        rows.append(row)
    return sorted(rows, key=lambda row: row['path']), value


def verify_preparation(provenance):
    provenance = Path(provenance)
    expected = preparation_report()
    if read(provenance / 'wifi-logging-preparation.json') != expected:
        raise ValueError('Wi-Fi logging preparation/profile evidence mismatch')
    _, prepared = bound_sources(provenance)
    source = read(provenance / 'source-lock.json')
    if source.get('upstream_commit') != profile()['upstream_commit']:
        raise ValueError('Wi-Fi logging exact upstream source mismatch')
    actual = {row['path']: row for row in prepared}
    for row in expected['prepared_files']:
        if actual.get(row['path']) != row:
            raise ValueError('Wi-Fi logging prepared source mismatch: ' + row['path'])
    expected_patches = {row['path'] for row in expected['prepared_files'] if row['path'].startswith(PATCH_DIR + '/')}
    if {name for name in actual if name.startswith(PATCH_DIR + '/')} != expected_patches:
        raise ValueError('Wi-Fi logging prepared patch inventory mismatch')
    wrapper = {row['path']: row for row in manifest(provenance / 'wrapper-source-files.json')}
    for row in wrapper_rows()[0]:
        if wrapper.get(row['path']) != row:
            raise ValueError('Wi-Fi logging wrapper/profile source mismatch: ' + row['path'])
    return expected


def required(provenance, build_commit=None):
    """New/unknown wrappers require this profile even if every marker is removed.

    Only three exact historical wrappers can replay without it. Their pipeline
    source rows must match frozen Git bytes and the bound wrapper tree hash.
    The release caller supplies the independently selected Actions commit.
    """
    provenance = Path(provenance)
    source = read(provenance / 'source-lock.json')
    commit = source.get('wrapper_checkout_commit')
    if build_commit is not None and commit != build_commit:
        raise ValueError('Wi-Fi logging wrapper differs from selected build commit')
    historical = json.loads(regular(HERE / 'historical-wrappers.json', HERE))
    profile()
    if commit not in historical:
        return True
    wrapper = manifest(provenance / 'wrapper-source-files.json')
    if digest(json.dumps(wrapper, sort_keys=True).encode()) != source.get('wrapper_tree_hash'):
        raise ValueError('historical Wi-Fi wrapper tree hash mismatch')
    actual = {row['path']: row for row in wrapper}
    if any(actual.get(row['path']) != row for row in historical[commit]):
        raise ValueError('historical Wi-Fi wrapper pipeline source mismatch')
    if any(name.startswith(('ci/wifi_logging.py', 'ci/wifi-logging/')) for name in actual):
        raise ValueError('historical wrapper cannot contain new Wi-Fi logging sources')
    if any((provenance / name).exists() or (provenance / name).is_symlink()
           for name in ('wifi-logging-preparation.json', 'wifi-logging-build.json')):
        raise ValueError('historical wrapper cannot self-select Wi-Fi logging evidence')
    return False


def expected_build_report():
    value = profile()
    return {'schema_version': 1, 'status': 'pass', 'profile_id': value['id'],
            'profile_sha256': MANIFEST_SHA256, 'archive': value['archive'],
            'patched_sources': value['patched_sources']}


def capture(build_root, output):
    """Read the actual downloaded archive and prepared compiler inputs pre-clean."""
    root, output = Path(build_root), Path(output)
    expected = expected_build_report()
    archive = root / 'dl' / expected['archive']['name']
    regular_archive = archive.is_file() and not archive.is_symlink()
    if not regular_archive or payload_digest(archive) != expected['archive']['sha256']:
        raise ValueError('Wi-Fi logging actual source archive SHA256 mismatch')
    candidates = sorted(root.glob('build_dir/target-*/linux-mediatek_filogic/mt_wifi'))
    if len(candidates) != 1:
        raise ValueError('one compiled mt_wifi source directory is required')
    for relative, expected_digest in expected['patched_sources'].items():
        if digest(regular(candidates[0] / relative, root)) != expected_digest:
            raise ValueError('Wi-Fi logging compiler source SHA256 mismatch: ' + relative)
    if output.exists() or output.is_symlink() or any(parent.is_symlink() for parent in output.parents):
        raise ValueError('fresh regular Wi-Fi logging build evidence path required')
    atomic_write(output, (json.dumps(expected, indent=2, sort_keys=True) + '\n').encode())
    return expected


def configuration(provenance, prepared):
    root = Path(provenance).parent
    rows = {row['path']: row for row in prepared}
    final = regular(root / 'metadata/final.config', root)
    if rows.get('.config') != file_row('.config', final):
        raise ValueError('Wi-Fi logging final config differs from prepared build config')
    selected = parse_config(root / 'metadata/final.config')
    expected = parse_config(HERE / 'driver.config')
    if any(selected.get(key, 'n') != value for key, value in expected.items()):
        raise ValueError('Wi-Fi logging reviewed driver/PMF config mismatch')
    kernel = regular(root / 'metadata/kernel/.config', root)
    kernel_config = parse_config(root / 'metadata/kernel/.config')
    if any(kernel_config.get(key) != 'y' for key in ('CONFIG_ARM64', 'CONFIG_64BIT', 'CONFIG_SMP')):
        raise ValueError('Wi-Fi logging requires the reviewed arm64 SMP kernel config')
    release = regular(root / 'metadata/kernel/kernel.release', root).decode().strip()
    abi = regular(root / 'metadata/kernel/.vermagic', root).decode().strip()
    if release != KERNEL_VERSION or not re.fullmatch(r'[0-9a-f]{32}', abi):
        raise ValueError('Wi-Fi logging kernel release/ABI mismatch')
    return {'final_config_sha256': digest(final), 'kernel_config_sha256': digest(kernel),
            'kernel_release': release, 'kernel_vermagic': abi,
            'driver': {key: value for key, value in sorted(selected.items())
                       if key.startswith('CONFIG_MTK_') or key in expected}}


def module_row(relative, data):
    if (len(data) < 64 or data[:7] != b'\x7fELF\x02\x01\x01' or
            struct.unpack_from('<HHI', data, 16) != (1, 183, 1)):
        raise ValueError('Wi-Fi logging requires an arm64 ELF relocatable module: ' + relative)
    versions = re.findall(rb'(?:^|\x00)vermagic=([^\x00]+)\x00', data)
    if len(versions) != 1 or versions[0].split() != [KERNEL_VERSION.encode(), b'SMP', b'mod_unload', b'aarch64']:
        raise ValueError('Wi-Fi logging module kernel vermagic mismatch: ' + relative)
    if relative == MODULES[0] and b'PMF NR_UCAST global suppressed=%u%s SubType=%u WCID=%u\n' not in data:
        raise ValueError('Wi-Fi logging patched diagnostic absent from installed module')
    dependencies = re.findall(rb'(?:^|\x00)depends=([^\x00]*)\x00', data)
    expected_dependencies = ({'conninfra', 'mtkhnat'} if relative == MODULES[0] else {'mtk_warp', 'mt_wifi'})
    if len(dependencies) != 1 or set(dependencies[0].decode().split(',')) != expected_dependencies:
        raise ValueError('Wi-Fi logging module dependency mismatch: ' + relative)
    return dict(file_row(relative, data), vermagic=versions[0].decode(), depends=sorted(expected_dependencies))


def expected_report(payload_sha256, provenance, packages, modules):
    if not re.fullmatch(r'[0-9a-f]{64}', str(payload_sha256)):
        raise ValueError('Wi-Fi logging requires exact rootfs SHA256')
    preparation = verify_preparation(provenance)
    build = read(Path(provenance) / 'wifi-logging-build.json')
    if build != expected_build_report():
        raise ValueError('Wi-Fi logging source archive/compiler input evidence mismatch')
    _, prepared = bound_sources(provenance)
    source = read(Path(provenance) / 'source-lock.json')
    source_binding = {key: source.get(key) for key in (
        'upstream_commit', 'wrapper_checkout_commit', 'source_lock_sha256',
        'prepared_tree_hash', 'wrapper_tree_hash', 'build_fingerprint')}
    for key, value in source_binding.items():
        if not re.fullmatch(r'[0-9a-f]{40}' if key.endswith('commit') else r'[0-9a-f]{64}', str(value)):
            raise ValueError('Wi-Fi logging source binding requires exact identity: ' + key)
    config = configuration(provenance, prepared)
    abi = KERNEL_VERSION + '~' + config['kernel_vermagic'] + '-r1'
    package = packages.get(PACKAGE_NAME, {})
    expected_dependencies = {'kernel=' + abi, 'kmod-conninfra', 'kmod-mediatek_hnat', 'kmod-warp', 'wifi-dats'}
    if (packages.get('kernel', {}).get('version') != abi or package.get('version') != PACKAGE_VERSION or
            set(package.get('depends', '').split()) != expected_dependencies or
            sorted(path for path in package.get('files', []) if path.endswith('.ko')) != sorted(MODULES)):
        raise ValueError('Wi-Fi logging package version/dependencies/kernel ABI/module ownership mismatch')
    if set(modules) != set(MODULES):
        raise ValueError('Wi-Fi logging exact installed module inventory required')
    return {'schema_version': 1, 'status': 'pass', 'profile_id': profile_id(),
            'profile_sha256': MANIFEST_SHA256, 'rootfs_sha256': payload_sha256,
            'kernel_abi': abi, 'configuration': config, 'build': build,
            'source_binding': source_binding,
            'prepared_files': preparation['prepared_files'],
            'package': {'name': PACKAGE_NAME, 'version': PACKAGE_VERSION,
                        'depends': sorted(expected_dependencies), 'modules': list(MODULES)},
            'modules': [module_row(path, modules[path]) for path in MODULES],
            'scope': SCOPE, 'runtime_tested': False}


def module_bytes(path, root):
    # Vendor mt_wifi.ko is larger than the bounded source/JSON inputs.
    path, root = Path(path), Path(root)
    current = root
    if root.is_symlink():
        raise ValueError('Wi-Fi module root symlink rejected')
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Wi-Fi module symlink rejected')
    if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024 or path.stat().st_mode & 0o7777 != 0o644:
        raise ValueError('bounded regular mode-0644 Wi-Fi module required')
    return path.read_bytes()


def inspect(rootfs, payload, provenance):
    rootfs = Path(rootfs)
    database = rootfs / 'lib/apk/db/installed'
    regular(database, rootfs)
    packages = parse_packages(database)
    modules = {path: module_bytes(rootfs / path.lstrip('/'), rootfs) for path in MODULES}
    return expected_report(payload_digest(payload), provenance, packages, modules)


def payload_files(payload):
    """Independently read the installed DB/modules from the actual SquashFS.

    Use the verifier host's tool, never executable bytes from the artifact.
    The same kernel/rootfs payload is separately matched against the ROM.
    """
    payload_digest(payload)
    tool = shutil.which('unsquashfs') or shutil.which('unsquashfs4')
    if tool is None:
        raise ValueError('Wi-Fi release verification requires host unsquashfs')
    result = {}
    for path in ('/lib/apk/db/installed',) + MODULES:
        run = subprocess.run([tool, '-cat', str(payload), path.lstrip('/')],
                             capture_output=True, timeout=60, check=False)
        if run.returncode or not run.stdout or len(run.stdout) > 64 * 1024 * 1024:
            raise ValueError('Wi-Fi release cannot read bounded SquashFS file: ' + path)
        result[path] = run.stdout
    return result


def verify_release(root, value, packages, build_commit):
    root = Path(root)
    if not required(root / 'provenance', build_commit):
        raise ValueError('historical wrapper cannot claim a Wi-Fi logging report')
    payload = root / 'inspection/rootfs.squashfs'
    files = payload_files(payload)
    with tempfile.TemporaryDirectory() as name:
        database = Path(name) / 'installed'
        database.write_bytes(files['/lib/apk/db/installed'])
        actual = parse_packages(database)
    if {name: row['version'] for name, row in actual.items()} != {name: row['version'] for name, row in packages.items()}:
        raise ValueError('Wi-Fi release actual SquashFS package inventory mismatch')
    return verify_report(value, payload_digest(payload), root / 'provenance', actual,
                         {path: files[path] for path in MODULES})


def verify_report(value, payload_sha256, provenance, packages, modules):
    expected = expected_report(payload_sha256, provenance, packages, modules)
    if value != expected:
        raise ValueError('Wi-Fi logging release report/source/module/payload mismatch')
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    command = sub.add_parser('prepare')
    command.add_argument('--source-root', type=Path, required=True)
    command.add_argument('--output', type=Path, required=True)
    command = sub.add_parser('capture')
    command.add_argument('--build-root', type=Path, required=True)
    command.add_argument('--output', type=Path, required=True)
    command = sub.add_parser('inspect')
    command.add_argument('--rootfs', type=Path, required=True)
    command.add_argument('--payload', type=Path, required=True)
    command.add_argument('--provenance', type=Path, required=True)
    command.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            prepare(args.source_root, args.output)
        elif args.command == 'capture':
            capture(args.build_root, args.output)
        else:
            value = inspect(args.rootfs, args.payload, args.provenance)
            atomic_write(args.output, (json.dumps(value, indent=2, sort_keys=True) + '\n').encode())
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit('Wi-Fi logging gate: ' + str(error)) from error


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Run actual upstream uhttpd validator with pinned, real libubox sources."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import minimal_security


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--libubox', type=Path, required=True)
    parser.add_argument('--cc', default='clang')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    profile = minimal_security.profile()
    native = profile['native_regression']
    for name, sha256 in native['libubox_files'].items():
        if minimal_security.digest(minimal_security.regular(args.libubox / name, args.libubox)) != sha256:
            raise ValueError('native libubox source SHA mismatch: ' + name)
    here = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        source = root / 'ubus.c'
        source.write_bytes(minimal_security.regular(here / 'uhttpd-ubus.before.c', here))
        subprocess.run(['git', 'apply', '--check', str(here / minimal_security.PATCH_NAME)], cwd=root, check=True)
        subprocess.run(['git', 'apply', str(here / minimal_security.PATCH_NAME)], cwd=root, check=True)
        if minimal_security.digest(source.read_bytes()) != profile['uhttpd']['patched_ubus_sha256']:
            raise ValueError('actual patched uhttpd source SHA mismatch')
        code = source.read_text()
        start = code.index('static bool uh_ubus_valid_list_params(')
        end = code.index('\nstatic bool uh_ubus_send_list(', start)
        (root / 'validator.inc').write_text(code[start:end] + '\n')
        subprocess.run([
            args.cc, '-std=gnu11', '-Wall', '-Wextra', '-Wno-sign-compare',
            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
            '-iquote', str(args.libubox), '-iquote', str(root),
            str(here / 'uhttpd-list-regression.c'),
            *(str(args.libubox / n) for n in ('blob.c', 'blobmsg.c', 'utils.c')),
            '-o', str(root / 'validator-check'),
        ], check=True)
        subprocess.run([str(root / 'validator-check')], check=True)
    report = {
        'schema_version': 1, 'status': 'pass', 'profile_sha256': minimal_security.profile_sha256(),
        'uhttpd_commit': profile['uhttpd']['commit'], 'libubox_commit': native['libubox_commit'],
        'cases': 11, 'sanitizers': ['address', 'undefined'], 'target_device_tested': False,
        'scope': 'Native execution of unchanged upstream validator with real libubox; not full HTTP or hardware validation',
    }
    if args.output:
        minimal_security.atomic_write(args.output, (json.dumps(report, indent=2, sort_keys=True) + '\n').encode())
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()

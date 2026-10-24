#!/usr/bin/env python3
"""Apply the S20P Lucky HTTP input lock without discovering a newer release."""
import argparse
import json
from pathlib import Path
import re

from source_lock import LockError, lookup


URL = 'https://github.com/gdy666/luci-app-lucky.git'


def assignment(text, name):
    matches = re.findall(r'^' + re.escape(name) + r':=(.*)$', text, flags=re.M)
    if len(matches) != 1:
        raise LockError('Lucky recipe requires one ' + name)
    return matches[0]


def pin_release(lock, core_path, luci_path):
    lookup(lock, URL)
    row = next(row for row in lock['sources'] if row['url'] == URL)
    release = row.get('release')
    if not isinstance(release, dict):
        raise LockError('S20P Lucky release lock is missing')
    version = release.get('version', '')
    if not isinstance(version, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)+', version):
        raise LockError('invalid S20P Lucky release version')
    expected_url = f'https://release.66666.host/v{version}/{version}_wanji_docker'
    if release.get('source_url') != expected_url or release.get('architecture') != 'arm64':
        raise LockError('invalid S20P Lucky release URL or architecture')
    digest = release.get('sha256', '')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise LockError('S20P Lucky archive must have a fixed SHA256')
    luci = luci_path.read_text()
    if (assignment(luci, 'PKG_VERSION') != release.get('luci_version') or
            assignment(luci, 'PKG_RELEASE') != release.get('luci_release')):
        raise LockError('Lucky LuCI recipe differs from recovered original ROM source')
    core = core_path.read_text()
    if assignment(core, 'PKG_RELEASE') != '3':
        raise LockError('Lucky core package release differs from reviewed recipe')
    assignments = {
        'PKG_VERSION': version,
        'PKG_SOURCE': '$(PKG_NAME)_$(PKG_VERSION)_Linux_$(LUCKY_ARCH)_wanji_docker.tar.gz',
        'PKG_SOURCE_URL': expected_url,
        'PKG_HASH': digest,
    }
    # Validate every assignment before writing, including recipes with duplicate keys.
    for name in assignments:
        assignment(core, name)
    for name, value in assignments.items():
        core = re.sub(r'^' + re.escape(name) + r':=.*$', name + ':=' + value, core, flags=re.M)
    core_path.write_text(core)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, required=True)
    parser.add_argument('--core', type=Path, required=True)
    parser.add_argument('--luci', type=Path, required=True)
    args = parser.parse_args()
    try:
        pin_release(json.loads(args.lock.read_text()), args.core, args.luci)
    except (LockError, OSError, ValueError) as error:
        parser.exit(1, 'S20P Lucky input lock: ' + str(error) + '\n')


if __name__ == '__main__':
    main()

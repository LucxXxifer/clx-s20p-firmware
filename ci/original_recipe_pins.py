#!/usr/bin/env python3
"""Restore recovered S20P geosite/AxonHub inputs without changing other recipes."""
import argparse
import hashlib
import json
from pathlib import Path
import re

from source_lock import LockError


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked_value(row, key, pattern):
    value = row.get(key)
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise LockError('invalid recovered recipe lock: ' + key)
    return value


def replace_assignment(data, key, value):
    # Work on bytes so mixed LF/CRLF in the locked upstream recipe is retained.
    pattern = rb'^' + re.escape(key.encode()) + rb':=[^\r\n]*'
    if len(re.findall(pattern, data, flags=re.M)) != 1:
        raise LockError('recipe requires one assignment: ' + key)
    return re.sub(pattern, lambda _: key.encode() + b':=' + value.encode(), data, flags=re.M)


def recipe(feed, relative, row):
    path = feed / relative
    if path.is_symlink() or not path.resolve().is_relative_to(feed.resolve()):
        raise LockError('recipe crosses source tree: ' + relative)
    data = path.read_bytes()
    before = checked_value(row, 'recipe_sha256_before', r'[0-9a-f]{64}')
    after = checked_value(row, 'recipe_sha256_after', r'[0-9a-f]{64}')
    if sha(data) not in (before, after):
        raise LockError('unreviewed recipe drift: ' + relative)
    return path, data, after


def pin_recipes(lock, feed):
    pins = lock.get('original_recipe_pins')
    if not isinstance(pins, dict) or set(pins) != {'geosite', 'axonhub'}:
        raise LockError('recovered original input pins are required')
    geo = pins['geosite']
    axon = pins['axonhub']
    version = checked_value(geo, 'version', r'[0-9]{14}')
    digest = checked_value(geo, 'sha256', r'[0-9a-f]{64}')
    if geo.get('source_url') != ('https://github.com/v2fly/domain-list-community/'
                                  'releases/download/' + version + '/dlc.dat'):
        raise LockError('invalid recovered geosite release URL')
    gp, gd, gh = recipe(feed, 'v2ray-geodata/Makefile', geo)
    gd = replace_assignment(gd, 'GEOSITE_VER', version)
    section = rb'(?m)^define Download/geosite\r?\n.*?^endef'
    blocks = list(re.finditer(section, gd, flags=re.S))
    if len(blocks) != 1:
        raise LockError('recipe requires exactly one Download/geosite block')
    block = blocks[0]
    if len(re.findall(rb'^  HASH:=[^\r\n]*', block[0], flags=re.M)) != 1:
        raise LockError('geosite download requires exactly one hash')
    updated = re.sub(rb'^  HASH:=[^\r\n]*', lambda _: b'  HASH:=' + digest.encode(),
                     block[0], flags=re.M)
    gd = gd[:block.start()] + updated + gd[block.end():]

    ap, ad, ah = recipe(feed, 'axonhub/Makefile', axon)
    for key, value in {
        'PKG_VERSION': checked_value(axon, 'version', r'[0-9]{8}'),
        'PKG_SOURCE_VERSION': checked_value(axon, 'source_commit', r'[0-9a-f]{40}'),
        'PKG_HASH': checked_value(axon, 'source_sha256', r'[0-9a-f]{64}'),
    }.items():
        ad = replace_assignment(ad, key, value)
    if axon.get('source_url') != ('https://codeload.github.com/looplj/axonhub/tar.gz/'
                                   + axon['source_commit'] + '?'):
        raise LockError('invalid recovered AxonHub source URL')
    expected = axon.get('patches')
    if not isinstance(expected, dict) or not expected:
        raise LockError('recovered AxonHub patch hashes are required')
    patch_dir = feed / 'axonhub/patches'
    if patch_dir.is_symlink() or not patch_dir.resolve().is_relative_to(feed.resolve()):
        raise LockError('AxonHub patch directory crosses source tree')
    actual = {}
    for path in patch_dir.rglob('*'):
        if path.is_symlink():
            raise LockError('AxonHub patch path must not be a symlink')
        if path.is_file():
            actual[str(path.relative_to(patch_dir))] = sha(path.read_bytes())
    if actual != expected:
        raise LockError('AxonHub patch set differs from recovered original source')
    if sha(gd) != gh or sha(ad) != ah:
        raise LockError('recovered recipe output differs from reviewed fixed source')
    # All recipes, output hashes and patches pass before either recipe is written.
    gp.write_bytes(gd)
    ap.write_bytes(ad)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, required=True)
    parser.add_argument('--feed', type=Path, required=True)
    args = parser.parse_args()
    try:
        pin_recipes(json.loads(args.lock.read_text()), args.feed)
    except (LockError, OSError, ValueError, TypeError) as error:
        parser.exit(1, 'S20P recovered original inputs: ' + str(error) + '\n')


if __name__ == '__main__':
    main()

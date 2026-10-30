#!/usr/bin/env python3
"""Reproduce the CA trust output independently from hashed Debian source inputs."""
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import textwrap

import components

REVOKED_DER = "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b"
REVOKED_LABELS = {'"emSign ECC Root CA - G3"', '"Comodo AAA Services root"'}


def certificate_files(certdata, blacklist):
    """Parse NSS records; never execute fetched certdata2pem.py or a Makefile."""
    objects, obj, multiline, values = [], {}, None, bytearray()
    started = False
    for line in certdata.splitlines():
        if not started:
            started = line.startswith('BEGINDATA')
            continue
        if multiline:
            if line.startswith('END'):
                obj[multiline], multiline = bytes(values), None
            else:
                values.extend(int(value, 8) for value in re.findall(r'\\([0-3][0-7][0-7])', line))
            continue
        if line.startswith('#'):
            continue
        if not line.strip():
            if obj:
                objects.append(obj)
                obj = {}
            continue
        parts = line.strip().split(' ', 2)
        if len(parts) < 2:
            raise ValueError('unsupported NSS record')
        if parts[1] == 'MULTILINE_OCTAL':
            multiline, values = parts[0], bytearray()
        elif len(parts) == 3:
            obj[parts[0]] = parts[2]
    if obj:
        objects.append(obj)
    if multiline:
        raise ValueError('truncated NSS record')
    blocked = {line.split('#', 1)[0].strip() for line in blacklist.splitlines()
               if line.split('#', 1)[0].strip()}
    trusted = {row.get('CKA_LABEL') for row in objects if row.get('CKA_CLASS') == 'CKO_NSS_TRUST'
               and row.get('CKA_TRUST_SERVER_AUTH') == 'CKT_NSS_TRUSTED_DELEGATOR'
               and row.get('CKA_LABEL') not in blocked}
    if trusted & REVOKED_LABELS:
        raise ValueError('revoked CA label remains trusted in source')
    files = {}
    for row in objects:
        label = row.get('CKA_LABEL')
        if row.get('CKA_CLASS') != 'CKO_CERTIFICATE' or label not in trusted:
            continue
        der = row.get('CKA_VALUE')
        if not isinstance(der, bytes) or components.digest(der) == REVOKED_DER:
            raise ValueError('invalid/revoked trusted certificate')
        name = label[1:-1].replace('/', '_').replace(' ', '_').replace('(', '=').replace(')', '=').replace(',', '_')
        stem = name
        name += '.crt'
        if name in files:
            name = stem + '_2.crt'
        if name in files or '/' in name or name.startswith('.'):
            raise ValueError('ambiguous certificate file name')
        pem = '-----BEGIN CERTIFICATE-----\n' + '\n'.join(textwrap.wrap(base64.b64encode(der).decode(), 64))
        files[name] = (pem + '\n-----END CERTIFICATE-----\n').encode()
    if not 80 <= len(files) <= 300:
        raise ValueError('unexpected trusted certificate count')
    return files


def reproduce(package, archive=None):
    package = Path(package)
    recipe = (package / 'Makefile').read_text()
    date, release = components.ca_version(recipe)
    if (date, release) < components.CA_FLOOR:
        raise ValueError('CA source below security floor')
    expected = components.field(recipe, 'PKG_HASH')
    if not components.SHA.fullmatch(expected):
        raise ValueError('CA source SHA256 required')
    if components.field(recipe, 'PKG_SOURCE_URL') != '@DEBIAN/pool/main/c/ca-certificates':
        raise ValueError('unapproved CA source archive origin')
    if archive is None:
        archive = components.download(f'https://deb.debian.org/debian/pool/main/c/ca-certificates/ca-certificates_{date}.tar.xz')
    if components.digest(archive) != expected:
        raise ValueError('CA source archive SHA256 mismatch')
    import io
    with tempfile.TemporaryDirectory() as name:
        tree = Path(name)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            members = tar.getmembers()
            if sum(row.size for row in members) > 32 * 1024 * 1024:
                raise ValueError('CA source expansion too large')
            seen = set()
            for row in members:
                path = Path(row.name)
                if (path.is_absolute() or '..' in path.parts or row.name in seen
                        or not (row.isdir() or row.isfile())):
                    raise ValueError('unsafe CA source archive')
                seen.add(row.name)
            tar.extractall(tree, filter='data')
        roots = list(tree.glob('*/mozilla/certdata.txt'))
        if len(roots) != 1:
            raise ValueError('CA NSS database missing')
        source = roots[0].parent.parent
        patches = sorted((package / 'patches').glob('*.patch'))
        for patch in patches:
            if patch.is_symlink():
                raise ValueError('symlink CA patch')
            subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch.resolve())],
                           cwd=source, check=True, stdout=subprocess.DEVNULL)
        certificates = certificate_files((source / 'mozilla/certdata.txt').read_text(),
                                          (source / 'mozilla/blacklist.txt').read_text())
    rows = []
    aliases = set()
    for name, pem in sorted(certificates.items()):
        result = subprocess.run(['openssl', 'x509', '-subject_hash', '-noout'], input=pem,
                                check=True, stdout=subprocess.PIPE).stdout.decode().strip()
        if not re.fullmatch(r'[0-9a-f]{8}', result):
            raise ValueError('invalid OpenSSL certificate subject hash')
        index = 0
        while f'{result}.{index}' in aliases:
            index += 1
        alias = f'{result}.{index}'
        aliases.add(alias)
        rows.extend([{'name': name, 'sha256': components.digest(pem), 'size': len(pem)},
                     {'name': alias, 'target': name}])
    bundle = b''.join(data for _, data in sorted(certificates.items()))
    return {'version': f'{date}-r{release}', 'source_sha256': expected,
            'prepared_files': [{'path': path.relative_to(package).as_posix(),
                                'sha256': components.digest(path.read_bytes())}
                               for path in sorted(package.rglob('*')) if path.is_file()],
            'certificate_count': len(certificates), 'files': sorted(rows, key=lambda row: row['name']),
            'bundle_sha256': components.digest(bundle), 'bundle_size': len(bundle),
            'revoked_der_sha256': REVOKED_DER}


def verify_image(root, report, packages, prepared):
    root = Path(root)
    date = report.get('version', '')
    match = re.fullmatch(r'(\d{8})-r(\d+)', date)
    if not match or tuple(map(int, match.groups())) < components.CA_FLOOR:
        raise ValueError('image CA version below security floor')
    for name in ('ca-bundle', 'ca-certificates'):
        if packages.get(name, {}).get('version') != date:
            raise ValueError('CA package version differs from prepared source')
    expected = {row['path']: row['sha256'] for row in report['prepared_files']}
    actual = {row['path'].removeprefix('package/system/ca-certificates/'): row.get('sha256')
              for row in prepared if row['path'].startswith('package/system/ca-certificates/')}
    if expected != actual or not expected:
        raise ValueError('CA prepared source manifest differs from reproduced source')
    trust = root / 'etc/ssl/certs'
    for parent in (root / 'etc', root / 'etc/ssl', trust):
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError('CA trust directory is redirected or missing')
    expected_names = {row['name'] for row in report['files']} | {'ca-certificates.crt'}
    if {path.name for path in trust.iterdir()} != expected_names:
        raise ValueError('CA image trust set differs from reproduced source')
    for row in report['files']:
        path = trust / row['name']
        if 'target' in row:
            if not path.is_symlink() or path.readlink().as_posix() != row['target']:
                raise ValueError('CA alias differs from source')
        elif (path.is_symlink() or not path.is_file() or path.stat().st_size != row['size']
              or components.digest(path.read_bytes()) != row['sha256']):
            raise ValueError('CA certificate differs from source')
    bundle = trust / 'ca-certificates.crt'
    if (bundle.is_symlink() or not bundle.is_file() or bundle.stat().st_size != report['bundle_size']
            or components.digest(bundle.read_bytes()) != report['bundle_sha256']):
        raise ValueError('CA bundle differs from source')
    alias = root / 'etc/ssl/cert.pem'
    if not alias.is_symlink() or alias.readlink().as_posix() != '/etc/ssl/certs/ca-certificates.crt':
        raise ValueError('CA bundle alias differs from package')
    if report.get('revoked_der_sha256') != REVOKED_DER:
        raise ValueError('CA revocation evidence missing')

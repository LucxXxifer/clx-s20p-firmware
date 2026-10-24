#!/usr/bin/env python3
"""Check selected Go packages against the host recipes in real package metadata."""
import argparse
import json
from pathlib import Path
import re

GO_HOST = re.compile(r'(?<![\w-])(golang(?:\d+\.\d+|-bootstrap)?)/host\b')


def verify(config, package_info, source_root):
    selected = set(re.findall(r'^CONFIG_PACKAGE_(.+)=[ym]$', config, re.M))
    sources = []
    for block in package_info.split('Source-Makefile: ')[1:]:
        source, _, body = block.partition('\n')
        header = body.split('\nPackage: ', 1)[0]
        fields = [line.split(':', 1)[1] for line in header.splitlines()
                  if line.startswith(('Build-Depends:', 'Build-Depends/host:'))]
        sources.append({'source': source, 'name': Path(source).parent.name,
                        'packages': re.findall(r'^Package: (.+)$', body, re.M),
                        'go_hosts': sorted(set(GO_HOST.findall(' '.join(fields)))),
                        'host': bool(re.search(r'^Build-Types:.*\bhost\b', header, re.M))})
    providers = {s['name']: s for s in sources if s['host']}
    checks = []
    pending = []
    for source in sources:
        enabled = sorted(selected.intersection(source['packages']))
        if enabled:
            for dependency in source['go_hosts']:
                checks.append({'source': source['source'], 'selected_packages': enabled,
                               'dependency': dependency + '/host'})
                pending.append(dependency)
    seen = set()
    errors = []
    while pending:
        dependency = pending.pop()
        if dependency in seen:
            continue
        seen.add(dependency)
        provider = providers.get(dependency)
        if provider is None or not (source_root / provider['source']).is_file():
            errors.append('missing host recipe: ' + dependency + '/host')
            continue
        pending.extend(provider['go_hosts'])
    return {'status': 'fail' if errors else 'pass', 'checks': checks,
            'resolved_go_host_recipes': sorted(seen), 'errors': sorted(set(errors)),
            'scope': 'selected y/m Go package host recipe existence; no compiler execution'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--package-info', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.config.read_text(), args.package_info.read_text(), args.source_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'phase': 'go-host-dependencies', 'status': report['status'], 'output': str(args.output)}))
    raise SystemExit(0 if report['status'] == 'pass' else 1)


if __name__ == '__main__':
    main()

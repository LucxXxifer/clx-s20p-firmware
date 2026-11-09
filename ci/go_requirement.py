#!/usr/bin/env python3
"""Select the reviewed OpenWrt Go host recipe that a Go recipe actually needs.

OpenWrt 25.12 wires ``PKG_BUILD_DEPENDS:=golang/host`` to the golang1.26 series
(``GO_DEFAULT_VERSION`` in golang-values.mk) and compiles it with
``GOTOOLCHAIN=local``.  A recipe that tracks upstream git head, such as
axonhub, can raise the ``go`` floor in its go.mod above that default; the Go
compiler then refuses to build:

    go: ../../go.mod requires go >= 1.27.3 (running go 1.26.10; GOTOOLCHAIN=local)

The tree already ships the versioned golang1.27 host recipe (attested by
ci/security/go-x509.json and backported by ci/go_security.py), and
golang-values.mk derives ``GO_HOST_VERSION`` from the ``golang1.27/host`` build
dependency.  This module rewrites only that selector token.  Source pins,
PKG_HASH values, GOTOOLCHAIN and every security gate stay untouched: the
package archive and the Go source archive remain the reviewed ones.

The requirement is read from the immutable go.mod at the commit the recipe
already pins, so the decision is derived from real upstream data instead of a
guess about which series a package needs.  Selection follows upstream: the
series upstream declares as default wins whenever it clears the floor, and a
package that outgrows it is moved to the newest reviewed series in the tree, so
the firmware keeps tracking upstream releases without further edits here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

BARE_HOST = re.compile(rb'(?<![\w.-])golang/host(?![\w-])')
VERSIONED_HOST = re.compile(rb'(?<![\w.-])golang1\.(\d+)/host(?![\w-])')
HOST_RECIPE = re.compile(rb'(?<![\w.-])golang1\.(\d+)/host')
CODELOAD = re.compile(rb'^https://codeload\.github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/', re.MULTILINE)
GO_DIRECTIVE = re.compile(rb'^go[ \t]+(\d+)\.(\d+)(?:\.(\d+))?[ \t]*$', re.MULTILINE)
SERIES = re.compile(rb'^GO_VERSION_MAJOR_MINOR:=(\d+)\.(\d+)[ \t]*$', re.MULTILINE)
PATCH = re.compile(rb'^GO_VERSION_PATCH:=(\d+)[ \t]*$', re.MULTILINE)
DEFAULT_SERIES = re.compile(rb'^GO_DEFAULT_VERSION:=(\d+)\.(\d+)[ \t]*$', re.MULTILINE)
COMMIT = re.compile(r'[0-9a-f]{40}\Z')
RAW_GOMOD = 'https://raw.githubusercontent.com/{owner}/{repo}/{commit}/go.mod'


class RequirementError(Exception):
    """The Go host toolchain cannot be selected from reviewed inputs."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def field(data, name):
    match = re.search(rb'^' + re.escape(name.encode()) + rb':=(.*)$', data, re.MULTILINE)
    return match.group(1).decode().strip() if match else ''


def fetch_bytes(url, timeout=30):
    request = urllib.request.Request(url, headers={'User-Agent': 's20p-go-requirement/1'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise RequirementError('unexpected HTTP status ' + str(response.status) + ' for ' + url)
            return response.read()
    except urllib.error.URLError as error:
        raise RequirementError('cannot read ' + url + ': ' + str(error)) from error


def parse_go_directive(data, origin):
    match = GO_DIRECTIVE.search(data)
    if not match:
        raise RequirementError('go.mod has no go directive: ' + origin)
    series = (int(match.group(1)), int(match.group(2)))
    patch = int(match.group(3) or 0)
    return series, patch


def load_recipes(lang_dir):
    """Map "1.27" -> {"name", "patch", "path"} for every versioned Go host recipe."""
    lang_dir = Path(lang_dir)
    if not lang_dir.is_dir():
        raise RequirementError('Go recipe directory missing: ' + str(lang_dir))
    recipes = {}
    for makefile in sorted(lang_dir.glob('golang1.*/Makefile')):
        data = makefile.read_bytes()
        series = SERIES.search(data)
        patch = PATCH.search(data)
        if not series or not patch:
            raise RequirementError('unreviewed Go host recipe structure: ' + str(makefile))
        if makefile.parent.name != 'golang' + series.group(1).decode() + '.' + series.group(2).decode():
            raise RequirementError('Go host recipe directory/series mismatch: ' + str(makefile))
        recipes[series.group(1).decode() + '.' + series.group(2).decode()] = {
            'name': makefile.parent.name, 'patch': int(patch.group(1)), 'path': makefile}
    if not recipes:
        raise RequirementError('no versioned Go host recipe in ' + str(lang_dir))
    return recipes


def default_series(lang_dir):
    values = Path(lang_dir) / 'golang-values.mk'
    if not values.is_file():
        raise RequirementError('golang-values.mk missing: ' + str(values))
    match = DEFAULT_SERIES.search(values.read_bytes())
    if not match:
        raise RequirementError('unreviewed golang-values.mk: GO_DEFAULT_VERSION missing')
    return match.group(1).decode() + '.' + match.group(2).decode()


def satisfies(series, recipe, requirement):
    major, minor = (int(part) for part in series.split('.'))
    (want_major, want_minor), want_patch = requirement
    return (major, minor, recipe['patch']) >= (want_major, want_minor, want_patch)


def select_recipe(requirement, recipes, default):
    """Follow upstream: its default series when it clears the floor, else the newest.

    The tree tracks the upstream feed branch, so the default it declares is the
    version upstream builds with.  Keep that whenever the package can use it and
    only move up when the go.mod floor demands it, then take the newest reviewed
    series rather than the lowest one so the firmware keeps following upstream
    as it adds series.
    """
    if default in recipes and satisfies(default, recipes[default], requirement):
        return default
    qualifying = [(tuple(int(part) for part in series.split('.')), series)
                  for series, recipe in recipes.items() if satisfies(series, recipe, requirement)]
    return max(qualifying)[1] if qualifying else None


def go_mod_source(makefile):
    """Derive (url, owner/repo, commit) for the go.mod the recipe pins."""
    if b'GO_PKG:=' not in makefile:
        return None
    url = field(makefile, 'PKG_SOURCE_URL')
    commit = field(makefile, 'PKG_SOURCE_VERSION')
    match = CODELOAD.search(url.encode() + b'\n')
    if not match or not COMMIT.match(commit):
        raise RequirementError('cannot derive a pinned go.mod source from the recipe')
    owner, repo = match.group(1).decode(), match.group(2).decode()
    return RAW_GOMOD.format(owner=owner, repo=repo, commit=commit), owner + '/' + repo, commit


def decide(makefile, package, recipes, default, fetch=fetch_bytes):
    status = {'package': package, 'recipe_sha256_before': sha256(makefile)}
    bare = BARE_HOST.search(makefile)
    versioned = VERSIONED_HOST.findall(makefile)
    if not bare and not versioned:
        status['status'] = 'no-go-host-dependency'
        return status, None

    source = go_mod_source(makefile)
    if source is None:
        status['status'] = 'no-go-module'
        return status, None
    url, repository, commit = source
    go_mod = fetch(url)
    requirement = parse_go_directive(go_mod, url)
    status.update({'go_mod_url': url, 'repository': repository, 'source_commit': commit,
                   'go_mod_sha256': sha256(go_mod),
                   'required_go': 'go%d.%d.%d' % (requirement[0][0], requirement[0][1], requirement[1]),
                   'default_go_series': default,
                   'available_go_series': {series: recipe['patch']
                                           for series, recipe in sorted(recipes.items())}})

    if bare:
        selected = select_recipe(requirement, recipes, default)
        if selected is None:
            raise RequirementError(
                'no reviewed Go host recipe satisfies %s for %s (tree has %s); '
                'extend ci/security/go-x509.json and the Go recipe tree instead of '
                'allowing a toolchain download'
                % (status['required_go'], package,
                   ', '.join('%s (%d)' % (key, value['patch']) for key, value in sorted(recipes.items()))))
        status['selected_series'] = selected
        status['selected_go'] = 'go%s.%d' % (selected, recipes[selected]['patch'])
        status['selection_policy'] = 'upstream-default' if selected == default else 'upstream-newest'
        if selected == default:
            status['status'] = 'default-satisfies'
            status['recipe_sha256_after'] = status['recipe_sha256_before']
            return status, None
        rewritten = BARE_HOST.sub(b'golang' + selected.encode() + b'/host', makefile)
        if rewritten == makefile or not HOST_RECIPE.search(rewritten):
            raise RequirementError('Go host selector rewrite had no effect: ' + package)
        status['status'] = 'selected'
        status['recipe_sha256_after'] = sha256(rewritten)
        return status, rewritten

    selected = '1.' + versioned[0].decode()
    if selected not in recipes:
        raise RequirementError('recipe requires the missing Go host recipe ' + selected + ': ' + package)
    if not satisfies(selected, recipes[selected], requirement):
        raise RequirementError('Go host recipe %s (%d) is below the %s floor of %s'
                               % (selected, recipes[selected]['patch'], status['required_go'], package))
    status.update({'status': 'versioned-satisfies', 'selected_series': selected,
                   'selected_go': 'go%s.%d' % (selected, recipes[selected]['patch']),
                   'recipe_sha256_after': status['recipe_sha256_before']})
    return status, None


def select(feed, lang, packages, evidence=None, fetch=fetch_bytes):
    feed = Path(feed)
    recipes = load_recipes(lang)
    default = default_series(lang)
    rows = []
    for package in packages:
        makefile = feed / package / 'Makefile'
        if makefile.is_symlink() or not makefile.is_file():
            raise RequirementError('Go recipe missing: ' + str(makefile))
        row, rewritten = decide(makefile.read_bytes(), package, recipes, default, fetch)
        if rewritten is not None:
            makefile.write_bytes(rewritten)
            print('%s: %s -> golang%s/host' % (package, row['required_go'], row['selected_series']))
        else:
            print('%s: %s (%s)' % (package, row['status'], row.get('required_go', 'no Go module')))
        rows.append(row)
    report = {'schema_version': 1, 'status': 'pass', 'packages': rows}
    if evidence:
        evidence = Path(evidence)
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps(report, indent=2) + '\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('action', choices=['select'])
    parser.add_argument('--feed', type=Path, required=True, help='custom_feed worktree')
    parser.add_argument('--lang', type=Path, required=True, help='feeds/packages/lang/golang')
    parser.add_argument('--package', action='append', dest='packages', required=True,
                        help='Go recipe directory inside --feed (repeatable)')
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args(argv)
    try:
        select(args.feed, args.lang, args.packages, args.evidence)
    except RequirementError as error:
        print('error: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0)


if __name__ == '__main__':
    main()

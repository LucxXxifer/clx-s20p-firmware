# Fixed B CA security removal

This narrow preservation-gate mechanism concerns only upstream
`chasey-dev/immortalwrt-mt798x-rebase` commit
`9f397975c7b47ce6986a6e39529d2f72fd5c729d`. Its exact CA recipe and Debian
blacklist patch stop trusting emSign ECC Root CA - G3. Restoring the revoked CA
to satisfy the original-ROM presence invariant would defeat that source change.

The original `full-rootfs-manifest.json` remains unchanged. The gate independently
anchors its SHA256 and both complete original records, including the regular PEM
and its subject-hash symlink. It can report only these two absences as
`security_removal` differences. Both retain `review_required` until their exact
difference IDs receive separate approval. Every other missing original path
continues to hard-fail. This document and the source review JSON are evidence,
not approvals.

## Bound evidence

`ci/reviewed-security-removals/chasey-9f397975.json` has SHA256
`0dc44aff92ac7662334e3a1dc9f562e2704149767b54b26cdb984626bbc1fc8b`, separately
anchored in `ci/preservation.py`. It binds the unchanged original manifest, both
original records, fixed source identity, exact Makefile and two patch hashes,
source archive `PKG_HASH`, package versions, B bundle bytes and source reproduction.
The companion recipe, patches and `source-reproduction.json` are hashed again by
the gate. The source archive URL is public Debian source; no router configuration
or private backup is part of this evidence.

For B, all of the following must match before either absence is reviewable:

- The top-level upstream commit, unique actual source repository/commit and
  unique embedded `main-source` repository/commit.
- Regular same-run `requested-sources.lock.json`, `prepared-source-files.json`
  and `wrapper-source-files.json`. Requested bytes must match the recorded hash
  and parsed embedded lock. All source manifests reject duplicate JSON keys,
  duplicate/unsafe paths, invalid records and impossible file/symlink ancestors.
- Recomputed prepared-tree hash, wrapper-tree hash and whole build fingerprint,
  using `provenance.py`'s default JSON separators, rather than pretty-file bytes
  or the compact difference-ID encoding.
- The complete prepared CA subtree consists of exactly the fixed Makefile and
  existing 0001 plus new 0002 patches, all regular exact records. An extra patch,
  symlink, altered recipe or substituted archive hash cannot inherit this review.
- Actual `ca-bundle` and `ca-certificates` versions are both `20260816-r2`; the
  bundle is a regular file with size 180744 and SHA256
  `64e7c24b0536b525fb19b145768bd5e6e12ced0df3db837c34553ea789acd143`;
  `/etc/ssl/cert.pem` retains its exact absolute target.
- Both revoked paths remain absent for every inode type. Actual trust-directory
  regular PEM objects and other bundles must not contain the revoked DER identity
  under another filename or PEM wrapping. PEM decoding is strict, and certificate
  DER must be one complete outer SEQUENCE with minimal definite length and no
  trailing data. Trust aliases must point to regular files inside the same trust
  directory; external, chained and dangling trust aliases fail closed.

The two package-version changes and bundle `file_sha256` difference still require
their own review. This mechanism never marks those differences expected. A source
and all other commits receive no B removal exception. A failed B guard records a
hard error and leaves both missing paths subject to the ordinary hard errors.

## Source reproduction

The fixed Debian `ca-certificates_20260816.tar.xz` archive has SHA256
`d939bcdd0cb058712cf4175bac76997676eb8b68fe9473765e1b40fb3d5b186a`.
Applying the fixed recipes/patches produces A's exact original 121-cert bundle
and B's exact 120-cert bundle: only emSign G3 disappears, with zero other changed
or added certificates. The revoked DER SHA256 is
`86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b`.

Comodo AAA was already excluded by the original source server-auth trust flags;
its new blacklist line removes no additional original-ROM CA. COMODO ECC,
COMODO RSA and other emSign roots retain normal mandatory preservation rules.

## Validation and limits

`python3 -m unittest discover -s ci/tests -p test_security_removals.py -v`
exercises the real immutable ROM baseline and public CA fixtures. Negative cases
cover wrong/missing/tampered source identities, manifest/fingerprint algorithms,
patch sets, review/baseline scope tampering, additional missing CAs, revoked-path
restoration, altered bundles/versions/aliases and renamed/rewrapped/trailing-data
revoked certificates. Exact approvals unlock only the two bound differences.

A native replay of the accepted A artifact with this new gate must remain
identical to A's original preservation report. A separate planning-only A rootfs
copy can project the fixed B CA changes to exercise the complete image gate:
without new approvals it must retain exactly five new pending reviews (two
versions, two removals and the bundle), and deletion of a third CA or restoration
of the revoked CA must hard-fail.

That projection is not a B cloud artifact or signed-feed acceptance. Its B source
identity and package changes are synthetic planning inputs; any borrowed A
signing report proves only the existing A key/ABI binding. A real B build must
still pass capture, native signed same-build APK/payload validation, source and
kernel ABI checks, full original functionality review and all image gates.
Hardware/runtime behavior and private configuration migration remain separate.

# B world review for run 40118211453

The B build reached `make checksum`, preserved the same-build image/APKs and
passed capture and publication-content audit. The image preservation gate
correctly returned `review_required` (exit 2): its only pending difference was
`/etc/apk/world`; invariant errors were empty. The original Actions run remains
**failure**, and no candidate release was published. An offline review does not
change that Actions result.

The downloaded evidence archive is 655657376 bytes with SHA256
`a3237c87e7cfdb3452c380486d0896e58bee7d79e33f93a201e8a7634e472dcd`, exactly
matching GitHub artifact `12578550651` for wrapper head
`172f3dfc8dd99b09f7e5c2e4849c4ee4656ca038`. ZIP CRC and all 607 inner file
SHA256 entries passed; every regular bundle file is covered. The review uses
the actual extracted B image and installed APK DB, rather than a projection.

## Exact source-derived world change

Accepted A's world and actual B's 6649-byte world differ only in this line:

```diff
-base-files=1~03eb306
+base-files=1~9f39797
```

The fixed B main source is chasey
`9f397975c7b47ce6986a6e39529d2f72fd5c729d`. Its original generation rules
produce `base-files` version `1~9f39797`, already independently reproduced and
reviewed in `ci/reviewed-differences/source-transition-b.json`. Actual B's
installed version matches. Replacing that one A world line reproduces B's
complete world bytes and SHA256 exactly:

`a08d0b1eae1da43dc707dddc3635a228200c51e153aa54002bebae4712b80e64`.

After the explicit original-to-build kernel and base-files identity
substitutions, all 465 original explicit roots remain. B has the same 479
explicit roots as accepted A; its 14 additional roots are unchanged and all
belong to the recomputed conservative 31-package closure of the five requested
increments. This closure follows actual installed DB dependencies and includes
all matching installed providers for virtual dependencies. In particular,
`python3-openssl` requires the unversioned `ca-certs`; both `ca-bundle` and
`ca-certificates` advertise that capability and are included. This does not
infer which provider an APK solver would select. Every encountered versioned
dependency is an exact kernel constraint and matches the installed version.
World's exact `base-files`, `kernel` and `libc` version constraints match the
actual installed DB. Explicit world roots and all installed dependency packages
are different inventories: B installs 495 packages, including every original
483 package, 24 LuCI apps and 128 kmods (all original 124 plus four additions).
Every installed kmod retains the exact same-build kernel dependency.

The actual source-lock, prepared/wrapper manifests, requested lock, tree hashes
and build fingerprint were recomputed and matched. The actual prepared
base-files recipe is recorded in the public world proof. Native offline
same-build APK verification matched all 495 installed packages, verified 153
feed kmod kernel dependencies and retained all 28 optional mobile packages.
It used the actual image's APK tool/libraries, without network access,
installation, package scripts or untrusted/forced dependency flags.

## One exact approval

`ci/approved-differences.json` adds only canonical difference ID
`731e6813c858f4b78db7e564e9957bda7adeff8a08fbe4e09581d45454502b84`.
The existing 507 entries remain intact, giving 508 unique approvals. The full
original/A/B world texts, root and dependency checks, artifact/source hashes and
exact five-field canonical record are in
`ci/reviewed-differences/40118211453-world.json`.

This approves one content hash. Different roots, ordering, constraints, content
or canonical record fields do not inherit the approval. No preservation code,
baseline, source lock or workflow changes are required. Missing original files,
signature failures, package/ABI errors and private-data checks retain their
existing failure behavior.

## Validation status and limits

The same downloaded B artifact was natively replayed with the original 507
approvals: exit 2, zero invariant errors and exactly this one pending world ID,
matching the cloud report. With an overlay containing the same 508 unique IDs
as the repository ledger, the native replay returned exit 0, `pass`, zero
invariant errors and zero pending reviews. The overlay's explanatory approval
text can differ from the repository's text. A second native full replay against
the exact final repository ledger file, including its explanatory text, also
returned exit 0, `pass`, zero errors and zero pending reviews. The public proof
binds that ledger file, replay report and log by SHA256. This validates the local
ledger file; it does not claim a new committed revision or Actions result.

Publication-content audit passed for both the bundle and actual extracted
rootfs. All nine negative cases remained blocked: changes to each of the five
canonical fields, an added or deleted world root, a changed constraint and a
different root ordering. A full replay with one added root returned exit 2 and
one new pending ID. The actual world was restored to its recorded hash and the
downloaded artifact remained unchanged.

Independent host review passed the archive, image metadata/CRC, tar/FIT,
partition-capacity and source-metadata checks. Independent native review used a
fresh extraction and checked the 4332 retained original entries, the two exact
source-bound CA removals, 576 original symlinks, all 212 module files and 128
full kernel dependencies, and the ownership/payload evidence for 485 added
files. Both reports have empty `must_fix` lists. The native report's status
suffix about a pending world review describes the original cloud gate; it does
not indicate an inventory failure. The final three-file approval diff passed independent review with no
must-fix findings; its report and reviewed input hashes are recorded in the
public world proof.

The existing full CI suite passed all 190 tests in 72.885 seconds, in addition
to the three targeted approval/missing-file regressions. Report and test-log
SHA256 values, scopes and remaining validation steps are recorded in the
public world proof.

This review does not claim a successful Actions run, a new cloud compilation,
candidate publication, B boot/hardware acceptance, USB/Wi-Fi throughput,
service behavior or private configuration migration. The existing artifact is
reviewed locally first; no full rebuild is dispatched by this approval change.

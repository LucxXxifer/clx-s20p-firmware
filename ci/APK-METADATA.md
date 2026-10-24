# APK metadata comparison

The locked upstream uses apk-tools 3.0.5 at commit
[`b5a31c0d865342ad80be10d68f1bb3d3ad9b0866`](https://github.com/alpinelinux/apk-tools/tree/b5a31c0d865342ad80be10d68f1bb3d3ad9b0866).
Its APK `info.hashes` and signed-index `hashes` have different meanings:

- [`app_mkpkg.c`](https://github.com/alpinelinux/apk-tools/blob/b5a31c0d865342ad80be10d68f1bb3d3ad9b0866/src/app_mkpkg.c#L446)
  reserves a 20-byte UID, computes SHA256 over the ADB metadata with that
  placeholder, and stores the first 20 bytes as the package UID.
- [`app_mkndx.c`](https://github.com/alpinelinux/apk-tools/blob/b5a31c0d865342ad80be10d68f1bb3d3ad9b0866/src/app_mkndx.c#L316)
  copies the package info, replaces `hashes` with the generated ADB identity
  (SHA256 by default), and adds `file-size` from the complete APK's stat size.
- [`adb.c`](https://github.com/alpinelinux/apk-tools/blob/b5a31c0d865342ad80be10d68f1bb3d3ad9b0866/src/adb.c#L109)
  distinguishes the UID from the integrity hash and compares the latter when
  native extraction verifies an index identity.

Requiring these two `hashes` strings to be equal rejected valid packages after
the firmware build completed. A native build of this exact commit reproduced
the difference with a 383-byte APK: package UID
`a295bf10289c48e57cee37791cfb2b4315db24d3`, index identity
`403ba702cfd42a4fa597eab3276b81db900f631b8e534f8addf37bbb31b7ddd0`, and index
`file-size: 383`. The generated index passed signature verification, and native
offline fetch returned an APK with the exact original SHA256. These example
values describe that fixture, not a reproducibility promise for later builds.

`capture.sh` requires the native UID and SHA256 identity shapes, compares the
index `file-size` with the preserved APK's actual size, and compares every
remaining metadata field in both directions. Unknown or mismatched extra
fields still fail. The signed-index signature check, native offline fetch of
every payload, complete fetched-file SHA256 comparison, and exact image kernel
ABI checks remain mandatory. Individual unsigned APKs are still authenticated
through the original signed index.

`ci/tests/test_apk_metadata.py` covers the valid UID/identity difference and
rejects malformed identities, incorrect file sizes, and metadata differences.

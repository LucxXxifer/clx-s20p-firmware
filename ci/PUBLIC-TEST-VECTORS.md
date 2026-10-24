# Public crypto self-test data in ELF libraries

The publication audit still rejects private-key filenames and unknown private
PEM material. Crypto libraries also compile PEM parser delimiters and public
known-answer test vectors into their read-only constants. These are not firmware
signing keys, SSH host keys, or user credentials.

`public-test-keys.json` contains the exact byte lengths and SHA256 values of the
12 private-key test vectors in GnuTLS 3.8.10 `lib/crypto-selftests-pk.c`. Its source
URL pins upstream commit `8a36455fd75ce76391cfc00c53213d8b0e1648da`; the source-file
hash was also checked against the official tar.xz authenticated by the fixed
packages feed recipe `d200499b31751dc5c72905f37a747712f009f155`. All 12 vectors
exactly match the original 10/17 ROM library already recorded in the baseline.

The audit accepts a vector only when the entire source string, including any
trailing newline, matches its hash and length, is NUL terminated, and lies fully
inside a readable, non-writable ELF `PT_LOAD` segment. A bare PEM parser delimiter
must satisfy the same ELF segment and terminator requirements. There is no
filename exemption. Every marker is checked, so changed vectors, unknown keys,
additional key material, and keys appended outside loadable segments still fail.

The test fixture contains public upstream test data with its copyright/license
notice. Tests cover exact matches, scan boundaries, changed/appended material,
writable segments, malformed ELF, ordinary PEM files and private filenames.
This change does not expand raw-file scanning into an archive/payload unpacker;
the existing extracted-rootfs audit and signed native APK fetch remain separate
checks with their existing coverage.

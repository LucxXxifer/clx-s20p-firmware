# Public crypto constants and documentation placeholders

The publication audit rejects private-key filenames and unknown private PEM
material. An original-ROM inventory also contains public upstream constants
and documentation snippets. `public-test-keys.json` records exact byte lengths,
SHA256 values and pinned sources for the limited exceptions below. No path or
whole-library exemption is used.

## GnuTLS known-answer tests

The 12 private-key test vectors in GnuTLS 3.8.10
`lib/crypto-selftests-pk.c` are pinned to upstream commit
`8a36455fd75ce76391cfc00c53213d8b0e1648da`. The source-file hash was also checked
against the official tar.xz authenticated by packages feed recipe
`d200499b31751dc5c72905f37a747712f009f155`. All 12 vectors exactly match the
original 10/17 ROM library. Each complete source string, including any trailing
newline, must match its hash and length, be NUL terminated, and fit fully inside
a readable, non-writable ELF `PT_LOAD` segment or an allocated, non-writable
`SHT_PROGBITS` section of a relocatable ELF object.

Bare PEM parser delimiters have no key body and require the same read-only ELF
and NUL conditions. Kernel modules use the section form (`ET_REL` has no program
headers). Writable, unallocated, malformed or appended ELF data is not exempt.

## cloudflared sample Hello Server key

cloudflared 2026.8.3 `tlsconfig/hello_ca.go` at commit
`fe70e951a3c52d92abf9f6c4248e32937b2f42fc` deliberately embeds a public sample
Hello Server certificate and key. This is a published application sample, not a
GnuTLS self-test or operator identity. Its archive matches the locked package
recipe hash. The exact 287-byte EC PEM slice matches the original ROM binary.

Go string pools use explicit lengths instead of NUL termination. Only this
recorded sample may use a length-bounded hash match; it must still fit entirely
in a readable, non-writable ELF area. Unknown or modified keys are rejected.

## OpenClash editor/documentation snippets

The locked small-package commit `a5fb03c51b55e0627c55ef9fe158003119a6f2bc`
contains two OpenVPN placeholders: `MIIE...example` in `res/default.yaml` and
`...` in `js/cm6.min.js`. Neither body is private-key material. Both assets match
the original ROM byte for byte. Only the two exact BEGIN-through-END snippets
are recognized by length and hash, including their indentation. The surrounding
file is still scanned. Replacing a placeholder with key material fails.

## Verification scope

Every marker is checked, including markers following an accepted constant or
crossing a scan boundary. The audit reports multiple unsafe files in one run
while continuing to block upload. Fixtures retain upstream source/license
notices. Tests cover exact matches, changed bodies, additional keys, malformed
ELF, writable/unallocated regions, boundaries and private filenames.

This change does not expand raw-file scanning into an archive/payload unpacker;
the existing extracted-rootfs audit and signed native APK fetch remain separate
checks with their existing coverage. Source provenance does not certify the
security of using a published sample key for any operator service.

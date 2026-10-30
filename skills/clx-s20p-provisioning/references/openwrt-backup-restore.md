# OpenWrt application backup, upgrade and restoration

Use for an identified CLX-S20P running OpenWrt, ImmortalWrt, iStoreOS or another S20P build. A branch name is evidence to investigate, not proof of compatibility. Do not assume that the companion repository, a version label, APK/opkg, IP address or interface naming is universal.

## 1. Select the operation and identify both ends

Distinguish backup-only, inspecting an existing backup, upgrade planning and authorized restoration. A request to back up or write a guide does not authorize a flash, reboot or restore. If firmware has already changed, inventory the new installation before interpreting old evidence. Reuse authenticated SSH/control sockets or the existing interactive terminal; do not send router commands to a Mac prompt. Never disable SSH host verification to regain access.

Record privately, with bounded read-only commands:

- Physical SN suffix and eMMC CID; device-tree model/compatible and board identity (`ubus call system board` when available).
- `/etc/openwrt_release`, actual kernel, target/subtarget, architecture, package manager and configured trusted feed sources. Read command help before using version-specific APK/opkg options.
- Live GPT/partitions, overlay/filesystem types, free RAM and external-destination capacity. Do not change a valid GPT or bootloader for a normal upgrade.
- Actual management address/bridge/ports, route source and DNS listeners/upstreams; service init scripts, users, enable state and running binary versions/hashes.
- Exact target image URL/release, digest, supported device profile, image type, layout, package inventory, kernel ABI, LuCI/network schema and security exceptions. A successful `sysupgrade -T` is required before sysupgrade, but does not prove application compatibility.

Read eMMC health before a long backup or flash; reuse the health policy. Failed health blocks flashing, not rescue backup. Excluding a reinstallable health-monitor plugin from a backup does not waive this gate.

If only an archive is available, infer its source from verified manifests and contents; mark missing source facts unknown. Do not invent a source release or compatibility result from a filename. CID conflict or another unit's identity data blocks identity restoration even when SN suffixes match.

## 2. Identify what must be preserved

Inspect actual files, process paths, service arguments and init scripts. Do not rely solely on package records or a hardcoded plugin list.

| Class | Identification and preservation | Restoration rule |
| --- | --- | --- |
| System and application settings | Inspect actual `sysupgrade -l`, `/etc/sysupgrade.conf`, package keep lists and the created archive | Full restore only after schema/layout compatibility is established |
| Persistent application data and identity | Find the effective work/state/filter/database paths, including custom init arguments and mounts | Use compatible schema, correct owners/permissions and application consistency requirements |
| Installed programs | Compare installed package inventory against ROM/image inventory; inspect package-owned files and dependencies | Prefer trusted, compatible target-branch packages; package lists are not installers |
| Manual/replaced binaries and integrations | Compare actual binary hashes/versions with package database; include init/hotplug/firewall/UI helpers and dependencies | Review ABI, security/provenance and target integration before any copy |
| Volatile/runtime references | Identify `/tmp`, `/var`, caches, generated config, current selectors and running core location | Export useful reference snapshots separately; do not restore whole tmpfs or stale firewall/session state |

Check built-in packages too: an installed replacement may differ from its recorded version, and a target image may omit a package built into the old image. A static binary still needs architecture, security and integration review. Do not preserve an old vulnerable binary automatically because it once worked.

For each application record: install source/version, program files, persistent paths, configuration schema, service account, dependencies, enable state, actual backup coverage, omissions and proposed restore method. Honor user exclusions without silently dropping required dependencies. Separate required data from optional statistics/logs; describe any intentional loss.

Special checks when present:

- **ShellCrash/Mihomo:** installation path, config/rules/providers, core persistence, generated running config, cache/selection persistence and live selector API. Export actual selections without logging controller secrets or subscription/node credentials. Reference selections from URLTest/fallback groups must not be converted into fixed choices. Check local DNS/route/firewall hooks separately.
- **ArkBridge/USB failover:** deployed program plus init/hotplug/UI/helpers, UCI and USB dependencies. Probe the target's real interface names and route ownership; a copied UCI file cannot install the daemon.
- **AdGuard Home:** effective YAML and `work_dir`, filters, service user/jail and permitted write paths. Persistent filters belong on persistent storage; optional statistics/query logs may remain volatile by explicit policy. Prevent DNS port conflicts and loops.
- **Tailscale:** distinguish community LuCI/init packages from the actual `tailscale`/`tailscaled` binaries; inspect both versions. Preserve the same unit's state before its first real login/start. Do not clone identity to another unit, run logout/`--reset` or create a new identity unnecessarily. New authorization, if needed, remains a user handoff.

## 3. Capture a usable, private backup

1. Use a new timestamped directory on an existing external destination. Keep application backups outside the per-device factory-backup root. Verify destination availability/capacity and restrictive permissions first; never publish private backup contents or records with the skill.
2. Record a before baseline: identity, boot ID, critical config hashes, service PIDs/enable states, effective routes and listeners. Never stage on source eMMC by default. Use RAM only with enough headroom and a transfer plan; do not install a runtime just for backup without need.
3. Generate the branch's native settings backup (`sysupgrade -b` after reviewing its local implementation/help). Where supported, `-k` adds a package list, not package programs. Inspect the generated archive and preserve native layout and restore hooks. Avoid editing live backup configuration just to collect a one-off archive.
4. Add only reviewed stable settings/data missing from native coverage to a separately constructed LuCI-compatible archive. Label runtime reference snapshots so a restore will not execute them. If coverage cannot safely be extended, deliver the native archive plus a separate data archive and explain the gap.
5. Create a distinct plugin program/data rescue snapshot with a clear staging prefix such as `rootfs/` and separate `metadata/`. Include selected manual programs/integrations and persistent data; inventory package databases as metadata only. Do not package an old APK/opkg database for restoring over the target database.
6. Record SHA256, sizes, modes, numeric owners, symlink targets, source identity/version, capture time, provenance and per-file class/coverage. For live databases use a supported consistent export or application snapshot. Detect files changing during capture; bounded retries are acceptable. If consistency needs a service pause outside authorization, preserve the incomplete result and explain the exact gap rather than claiming success.
7. Transfer privately using an existing verified channel. Verify source/destination checksums, read gzip/tar through EOF/CRC, reject traversal, duplicate/colliding members, special files and writes through symlink parents. Check symlink targets against intended target paths before restoration. Verify per-file content/metadata, actual keep-list coverage, required program/data paths and requested exclusions.
8. Compare the after baseline. A changed boot ID, service PID, route or config must be explained; do not claim no live changes if it differs. Validate completed remote transfer before cleaning only this run's exact temporary files/listeners/credentials.

Suggested deliverables (names may differ):

- `01-luci-settings.tar.gz`: compatible native settings plus explicitly reviewed additions.
- `02-plugin-programs-and-data.tar.gz`: staged program/data rescue snapshot; **not** a LuCI restore archive or a signed package installer kit.
- Inventory, per-file manifest, checksum list, coverage/consistency report and an ordered `.md` guide. Keep failed captures and clearly identify the final usable set.

Do not claim all plugins can be restored from LuCI merely because their settings appear in `01`. Do not claim `02` is installable across branches or extract it directly over `/`.

## 4. Inspect archives and choose the migration path

Inspect members and manifests without executing archive hooks or extracting over live files. Distinguish native settings, plugin data, program rescue snapshot, per-device factory partitions and full raw disk images by contents. Reject unsafe members; stage untrusted archives in isolation. Missing provenance, identity or files are concrete gaps, not permission to guess.

| Source/target evidence | Path |
| --- | --- |
| Same unit, verified compatible device/layout/settings schemas and available programs | Retain settings or restore native settings after programs/integrations are available; verify/skip already correct files |
| Same branch but package omissions, replaced binaries or changed dependencies | Resolve those gaps and route/DNS boot dependencies first; the branch name does not waive checks |
| Cross-family, APK↔opkg, changed network/LuCI schema or unknown compatibility | Clean installation and selective conversion using target-native packages; no whole settings/program overlay |
| Same archive on another physical S20P | Reuse only reviewed portable application settings; preserve the new unit's calibration, MAC/keys, Tailscale identity and factory backup |
| Corrupt/incomplete archive or unknown target/image | Continue read-only inspection/rescue; stop the affected restore/flash step |

Create a source/target application matrix: compatible install material available, config conversion needed, data conversion needed, program/integration review and current validation status. Check official image/package/release documentation for the exact branch; do not select the newest candidate automatically.

Before flashing, identify programs that supply default routing, DNS, proxying or remote management. Keeping their settings while losing the programs can make the new system unreachable. Prepare compatible signed offline packages or an image containing the dependencies, plus a local wired recovery route. An old program snapshot is not a substitute for missing compatible installers. Keep temporary bootstrap networking distinct from the desired final configuration and preserve a rollback path.

Ordinary upgrades require the exact supported image, health, checksums and local validation; they do not require BL2/FIP/GPT rewrites. Cross-family upgrades/downgrades may need a clean install even when model and CPU match. Treat prerelease/hardware-untested images as such.

## 5. Guide and execute an authorized restore in order

Generate a target-specific `.md` before a flash/restore with the following order and explicit missing items. Instructions may be prepared while device access is pending; writes require a freshly identified target.

1. **Recovery readiness:** identity/health, external verified backups, known-working original image, exact candidate/checksum/status, compatible offline installers and local access. Distinguish system rollback from factory restoration.
2. **Firmware path:** supported sysupgrade vs vendor recovery format, settings-retention decision and reason, target image test. Respect manual upload reservations; never force a failed image check or reload a flashing submission page.
3. **First boot and bootstrap:** verify actual running version/layout/IP/bridge; keep stable direct management and minimal temporary routing/DNS before enabling old network-dependent services. Do not infer failed boot from missing application DNS.
4. **Install programs/integrations:** use trusted target-native packages and exact kernel-matching modules; inspect install scripts for auto-start. Stage old manual programs for review, preserve pre-restore files, and supply init/users/dependencies before applying their settings. Never overwrite current CA bundles, kernel/modules, libc/system libraries, package database or entire old LuCI trees. Avoid a broad `apk upgrade`/`opkg upgrade` as a substitute for firmware upgrade.
5. **Restore settings:** only a compatible native settings archive goes into LuCI “Restore backup”. With retained settings, compare hashes and fill actual gaps. With incompatible schemas, translate individual settings. Review executable restore hooks, management IP, bridge/DHCP, DNS, routes and firewall before applying. Preserve unchanged selectors and unrelated settings.
6. **Restore application data:** services must not race their database/state copy. Use the application's supported restore procedure, correct new-system service account ownership and symlink/mount paths. Restore same-unit identities before first real login. Never blindly overlay `rootfs/` or the whole old overlay.
7. **Start one application, validate, then continue.** Enable and start are separate actions. Derive order from the deployment's dependencies. For the ShellCrash/ArkBridge/AdGuard/Tailscale deployment, installation can proceed in that order; bootstrap routing must work while ShellCrash is checked, then verify ArkBridge routing, then DNS, then Tailscale. Keep failed/unconfigured services stopped without disabling working management.
8. **Network acceptance and persistence:** verify services and the traffic paths below before removing bootstrap networking. Perform reload/restart/reboot tests only within the user's authorized scope. After successful migration, generate a fresh target-version backup.

For an isolated installation or missing packages, finish independent preparation and report the exact blocker; do not weaken signatures, TLS or architecture checks. Do not restart all services blindly to make missing binaries appear.

## 6. Acceptance and reporting

Verify actual user traffic, not only configuration, a service PID or a dashboard “online” badge:

- Local management from the intended LAN/switch path, correct static/DHCP/gateway behavior and no duplicate IP/DHCP. A router-direct connection does not prove peer-switch reachability.
- Real DNS resolution/filtering, expected listeners, no loops, and persistent filter/config paths.
- Proxy rules and selectors; test relevant websites/uploads through transparent forwarding, including UDP where required. An explicit HTTP proxy test alone is insufficient.
- Tailscale direct management, advertised subnet/exit-node settings and actual client TCP/UDP forwarding. Ask only for physical/mobile tests the agent cannot perform; reuse prior results only if the path/config has not changed.
- USB failover and failback with real client traffic through the proxy; safely bound outages and retain recovery. A connected USB link or router-only ping is insufficient.
- Relevant firewall reload/application restart and authorized reboot persistence, with critical traffic retested afterward.

Report separately: archive integrity/coverage, source/target compatibility review, firmware boot, each application install/restore/function, failover/failback, reboot persistence and factory restoration. Leave unperformed results `not-run`; `application_restore_tested` remains false until actual restoration and acceptance succeed. Do not promote a prior unit's restore or a synthetic/offline check into current hardware success.

All example SNs, CIDs, IPs and paths added to the public skill must be synthetic/parameterized. Keep personalized guides, exports, subscriptions, keys, node identities and private operation records outside the public repository and `.skill` archive.

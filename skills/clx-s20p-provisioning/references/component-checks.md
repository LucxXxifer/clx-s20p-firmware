# Component checks and honest acceptance

Choose tests from the user's goal. Read-only diagnosis and partial rescue may continue when a component is untested; never turn an untested item into a passed flash prerequisite. Record method, observation, limit and pass/fail/not-run separately.

| Component | Useful evidence | What it does not prove |
|---|---|---|
| Power | Router DC input label; matching adapter output voltage, polarity and rated current; secured connector; observed reset timing | Charger AC input, an illuminated LED or an unmeasured replacement adapter does not prove stable voltage under load. |
| Boot/CPU/RAM | Complete UART boot, memory size, repeated boot IDs/uptime, kernel panic stack, available temperature sensors | Seeing 2 GiB and booting once is not a memory-cell/stress test. Do not run a destructive RAM test on the live system. |
| eMMC | Owning card's CID/geometry, `life_time`, `pre_eol_info`, MMC errors, stable immutable-region hashes | A healthy wear band does not guarantee all sectors; a changed live rootfs hash is not alone media failure. Use [health policy](emmc-health.md). |
| Ethernet | Physical connector ↔ Linux port mapping, carrier, speed/duplex, peer capability, counters before/after traffic | An interface name, LED or negotiated rate alone is not throughput, packet integrity or all-port acceptance. |
| Wi-Fi | Actual radio/PHY inventory, regulatory settings, configured AP state, interface counters, client association and data transfer | A configured SSID does not prove it is broadcasting. No Wi-Fi during a startup panic does not prove damaged RF hardware. |
| PCIe/M.2/USB | Enumeration, driver binding, identified attached device and a scoped functional test | An empty slot, DT node or module loaded alone does not prove hardware operation. Do not hot-plug an unsupported slot. |

## Distinguish physical link, addressing and OS reboot

On the Mac, inspect the real selected service (`networksetup -getinfo`, `-getdnsservers`) and interface (`ifconfig`), plus device/default routes. A self-changing DHCP/DNS setting may come from another configuration writer, profile, automation or a different service; record timestamps and inspect before blaming the router. See [host networking](access-network.md).

On Linux/OpenWrt, collect available read-only output from `ip -br link`, `ip -s link`, `ubus call network.interface dump`, `/sys/class/net/<verified-interface>/carrier`, `speed`, `duplex`, and `ethtool <verified-interface>` if already installed. Do not install packages merely to label an LED fault. Compare `/proc/uptime`, boot ID and UART kernel starts: link renegotiation alone is not a reboot. Distinguish the switch/user ports from CPU-facing `eth*` interfaces.

For each requested connector, use a known-good cable and appropriately rated peer, move only one cable at a time, and record which logical port's carrier/counters changed. A 1-GbE peer cannot validate a 2.5-GbE port at 2.5G. Record a table with case label, Linux port, peer, speed/duplex, traffic duration/direction, byte/error/drop deltas and result. Seven-port acceptance needs all seven physical connectors, including both 2.5-GbE ports, exercised. Any throughput test needs its method and endpoint limitations; keep disk write stress outside this check.

## Diagnose a dark Ethernet LED separately

Check carrier, PHY link, speed, duplex and real traffic first. A functioning 2.5G/full link with a dark LED is a separate LED/driver/board question. Inspect current UCI LED entries, `/sys/class/leds` and the **running** device tree when available. Some PHY LEDs are not exposed as Linux LED-class devices.

The validated S20P had no WAN UCI/sysfs LED, while its live PHY node contained `realtek,led-act-select` and `realtek,led-link-select`. That establishes configured DT properties, not driver application or electrical function. Do not blindly toggle GPIOs, change PHY registers or claim a missing LuCI entry is the cause. Match board revision, active driver/source and electrical evidence before a separately authorized change.

## Record the useful boundary

After firmware boot, verify the same CID, both GPT copies, protected boot/factory regions and selected firmware readback, then test management and requested connectivity. Router-originated DNS/HTTPS success is separate from a LAN client's NAT path. A replacement OS booting without panic is separate from repairing the factory filesystem, a successful factory restore, Wi-Fi client data, all seven ports, M.2 and long-duration stability. Leave each unperformed test as `not-run`.

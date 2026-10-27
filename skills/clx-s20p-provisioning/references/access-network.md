# Access and Mac dual connection

## Isolate and identify

Use only the user's identified physical unit and target IP. Stock commonly uses `192.168.8.1`, third-party U-Boot `192.168.1.1`; these are defaults, not identity. Read hostname/model/compatible/CID through the available console before changes, and compare after transport/boot transitions. Do not scan unrelated hosts or infer the case SN from a MAC/CID.

## Telnet to SSH

Reuse working SSH first. If stock only offers Telnet, use the actual available Telnet client or a reviewed compatible transport, identify the board, then:

```sh
uci set dropbear.@dropbear[0].enable='1'
uci commit dropbear
/etc/init.d/dropbear restart
```

Verify the setting and a new SSH connection before relying on it. Preserve Dropbear's before state as private operation evidence. Do not extract a password from a guide automatically, log credentials, save them in scripts, pass them in argv/environment, or disable SSH host-key checks. Credentials may be entered interactively; if an authorized login helper is used, pass secrets only in memory/through a private descriptor. Telnet is unencrypted, so use the isolated direct LAN. Python 3.13+ lacks `telnetlib`; do not ship/import a removed module or install packages automatically.

For repeated binary transfers, establish an authenticated OpenSSH master once in an interactive terminal, using a per-unit socket. Example values must match the verified unit:

```sh
ssh -M -N -o ControlPersist=600 -o ControlPath=/tmp/s20p-A1B2.sock root@192.168.8.1
```

Keep that terminal/master open; supply the same `--control-path` to the tools. Verify/trust the actual host key through the identified Telnet/local console or user-established trust. An authenticated GUI SSH tab does not supply a subprocess control socket. A changed key needs investigation, not deletion/automatic acceptance. Runtime tools use `BatchMode=yes`, retain host-key checking, and never repeatedly prompt for passwords.

In a native Terminal, use short commands, inspect each response, and cancel an unfinished `>` continuation before pasting another command. Never interrupt the terminal doing a backup to run diagnostics; use independent read-only access.

## Keep Internet on Wi-Fi, device on Ethernet

Discover actual service and interface; never assume `en0` is Ethernet:

```sh
networksetup -listallhardwareports
networksetup -listnetworkserviceorder
route -n get default
route -n get 192.168.1.1
```

Record the selected service's existing IPv4/router/DNS settings with `networksetup -getinfo` and `-getdnsservers` using its exact service name. Configure that wired service with an unused address on the target subnet (for example `192.168.1.2/24` for U-Boot), **no router/default gateway and no DNS**, while retaining Wi-Fi's existing Internet connection. For stock use its verified subnet instead. Use the current macOS UI or locally verified `networksetup` syntax; do not guess how a CLI version encodes an empty router.

Recheck `route -n get <device-IP>` for the wired interface and `route -n get default` for Wi-Fi. Confirm both device reachability and Internet after the change. Bind U-Boot HTTP calls to the discovered wired interface and bypass proxies; check HTTP target identity before commands. Do not restart all networking or remove Wi-Fi's route to reach the device. Subnet overlap requires explicit route planning before proceeding.

Record changes and leave the connection appropriate for the user's next action. If the user will enter U-Boot/upload firmware manually, leave the direct Ethernet configuration available and document how to restore the prior wired settings; do not tear down the needed link automatically.

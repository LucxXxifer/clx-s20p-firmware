# S20P run 40987119906 failure and repair

The [failed run](https://github.com/LucxXxifer/clx-s20p-firmware/actions/runs/40987119906) stopped at the preservation config gate after `make defconfig`, before firmware compilation. The verified evidence artifact SHA256 is `8836b8bf118bd32c426349fa8a64f6d796e66bf959ba23ddca213a824be8c134`.

The gate reported four selection errors:

| Symbol | Actual | Cause and repair |
| --- | --- | --- |
| `CONFIG_PACKAGE_coreutils-stat` | `n` | Honk no longer selects it indirectly; explicitly keep the original tool. |
| `CONFIG_PACKAGE_libsqlite3` | `n` | Same indirect dependency loss; explicitly keep the original library. |
| `CONFIG_PACKAGE_sqlite3-cli` | `n` | Same indirect dependency loss; explicitly keep the original tool. |
| `CONFIG_PACKAGE_luci-i18n-adguardhome-zh-cn` | missing | The selected official LuCI source lacks Chinese translation. Per the updated user request, defer AdGuard core, LuCI and translation entirely. |

The LuCI checkout was `immortalwrt/luci` commit `c84adb975e207ae8b14982191838ec6218d632cd`; `applications/luci-app-adguardhome/po` contained `lo` and `templates`, without `zh-cn`. This concerns the selected LuCI packaging, not the language support of AdGuard Home's own web interface.

The exact reviewed Honk/TingReader package-owned removals remain unchanged. The daily selection review now forbids the three AdGuard packages and binds the package-root list without them. CA/Go floors, the exact QuickStart exception, dashboard payload checks, source ancestry, package/signature checks and all other preservation checks remain enforced. A new `workflow_dispatch` is required after local verification; rerunning the failed run would reuse its old source identity.

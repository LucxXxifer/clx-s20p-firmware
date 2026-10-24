# 原 10/17 映像保留檢查

這些 gate 驗證有證據支持的建置不變條件；不表示 bit-identical、已開機或硬體驗收通過。原始 expanded config／所有 feeds revision／資產與環境未完整復原。

## 真 defconfig 後的檢查

```sh
python3 ci/preservation.py config \
  --config /build/source/.config \
  --package-info /build/source/tmp/.packageinfo \
  --optional-packages ci/mobile-usb-packages.txt \
  --baseline-dir ci/baseline \
  --output /build/output/config-preservation.json
```

原映像的 483 個名稱是實際 APK runtime 名稱，未必與 Kconfig recipe 名相同。例如 `libubox20260213` 對應 `CONFIG_PACKAGE_libubox`、`libopenssl3` 對應 `CONFIG_PACKAGE_libopenssl`。以真 `tmp/.packageinfo` 的 `Package`／`ABI-Version` 精確映射，依固定 upstream [include/feeds.mk 的 FormatABISuffix](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/03eb30617955519e35845838c6255ebe687eb717/include/feeds.mk#L69)：非 kmod 才附 ABI；recipe 名末字元是數字時在 ABI 前加 `-`。因此 `libbz2`／`1.0` 是 `libbz2-1.0`，`libfuse3`／`3` 是 `libfuse3-3`。不使用字首猜測或 `Provides` 當同名包；ABI 改變／不明名稱仍會失敗。

`kernel` 由 image 流程隱含產生，不在一般 package metadata，也沒有 `CONFIG_PACKAGE_kernel`。config 階段要求 `CONFIG_LINUX_6_12=y`，image 階段仍要求真正 `kernel` package 版本、每個 kmod 的同批 ABI 相依與 module vermagic。

原套件 selector 可為 `m` 或 `y`，因為多 profile／每裝置 rootfs 可能由 device package 集合決定最終安裝；image gate 仍要求 483 個實際 runtime 包全部存在，不能只靠 config 宣稱預裝。新增五個指定根套件必須為 `y`。USB 28 個可選套件與 `r8152-firmware` 必須在 defconfig 後為 `m` 或 `y`，不強迫原已預裝的共同依賴降為 `m`，最終 signed repository 另行核對。

## TurboACC 的舊輸入符號

原 wrapper fragment 寫 `CONFIG_PACKAGE_TURBOACC_INCLUDE_NO_FASTPATH=y`，但固定 upstream [MTK TurboACC 目錄](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/tree/03eb30617955519e35845838c6255ebe687eb717/package/mtk/applications/luci-app-turboacc-mtk) 沒有 `Config.in`，其 Makefile 只有 `+kmod-tcp-bbr @!PACKAGE_luci-app-turboacc` 等依賴，prepared source 也沒有該 selector。真 defconfig 將它丟棄；不能把原 fragment 的無效 `y` 當成功能啟用證據。

原映像 `/etc/uci-defaults/turboacc` 按實際 `mtkhnat.ko`／SFE module 存在與否選 runtime engine；`/usr/share/rpcd/ucode/luci.turboacc` 探測 module／sysfs，並將 mt798x `hasFLOWOFFLOADING` 明設 false。這些原始 critical 檔案、init script、UI、套件與 MTK 設定全部保留 image gate 驗證。

`ci/baseline/policy.json` 只將這一個舊 input symbol 明列為 `unsupported_fragment_symbols`，記錄原輸入值、來源與理由；final config 必須沒有它或為 disabled。這不宣稱 fastpath 已啟用或關閉，也不放寬其他 config symbols。政策檔 checksum 同步更新；原 fragment 與映像證據維持原樣。

2026-10-24 的 Linux smoke attempt3 產物以此映射檢查通過 483 原包 selector、103 MTK symbols 與 28 USB optional 包。該次 `make defconfig` 另有 weechat recursive dependency loop，因此這個 config gate 結果只表示對已輸出 `.config` 的檢查，不代表整個 defconfig／ROM build 通過。

## Source inventory 與 image 差異

`provenance.source()` 以 canonical repo URL 去掉 `https://` 作唯一名稱，避免不同 owner 的同名 `packages` repo 碰撞。`tree` 是 Git tree object ID；`tree_hash` 是 recursive `git ls-tree` 文字的 SHA256，兩者不可混用。gate 要求來源 row 是 object、name 與 URL 各自唯一、完整 commit 與正確長度 hash；真本機 Git fixtures 直接呼叫 `provenance.source()` 再送 gate，包含正例與重名／重 URL／錯 hash／非 object 負例。

source gate 目前只驗來源 inventory headers／shape，不能單獨證明內容與當年 feeds 相同；報告會明示該限制。可重編的 ELF hash 改變只記錄為預期重編差異，critical defaults／firmware／UI／資料 hash 的不同仍須用精確 difference ID 加原因審查。新增任意檔案、版本／相依差異同樣需檢視。沒有 wildcard approve。

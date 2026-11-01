# CLX-S20P｜韌體、U-Boot 與刷機 Skill

為 **CLX-S20P（MT7986A／2 GiB DDR4／eMMC）** 整理從原廠開箱、備份、U-Boot 到 OpenWrt 升級的完整入口。主線韌體基於 **ImmortalWrt 25.12**，保留 **AX6000 Wi-Fi**；建置設定為 `S20P-WIFI-YES`，映像板型為 `clx,s20p`。

| 您要找的內容 | 說明與下載入口 |
| --- | --- |
| 韌體來源與上游 | [來源、作者引用與固定版本](#1-韌體來源與上游引用) |
| 從 0 到 1 刷機 Skill | [安裝／使用／下載頁](skills/README.md) |
| U-Boot GPT 修正正式版 | [修正說明與 FIP 下載頁](https://github.com/LucxXxifer/clx-s20p-uboot/releases/tag/s20p-gptfix-e4cf4dc72d45-d4c326981f44) |
| 最新安全補丁＋精簡版 | [2026-10-31 正式版下載頁](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-a-25.12-fixed-candidate-41175460764-1-9f397975) |
| 10/17 歷史固定版 | [韌體、同批套件與校驗碼下載頁](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-candidate-40095658212-1) |
| Fork 與二次開發 | [開源授權、引用要求與範例](#6-fork二次開發與引用說明) |

## 1. 韌體來源與上游引用

| 部分 | 上游與本專案的工作 |
| --- | --- |
| 建置框架 | 源自 [dqsq2e2/wrt_release](https://github.com/dqsq2e2/wrt_release)，本專案維護 S20P 建置、來源鎖定、驗證與發布流程。 |
| 系統來源 | [chasey-dev/immortalwrt-mt798x-rebase](https://github.com/chasey-dev/immortalwrt-mt798x-rebase) 的 `25.12` 分支，承接 [ImmortalWrt](https://github.com/immortalwrt/immortalwrt)／[OpenWrt](https://github.com/openwrt/openwrt) 生態。 |
| 安全修補 | 引用 OpenWrt 官方 uhttpd／Go／CA 修正及 LuCI 安全公告，按固定來源精確回補；詳見第 4 節。 |
| U-Boot | [LucxXxifer/clx-s20p-uboot](https://github.com/LucxXxifer/clx-s20p-uboot) fork 自 [Yuzhii0718/bl-mt798x-dhcpd](https://github.com/Yuzhii0718/bl-mt798x-dhcpd)，沿用 `clx_s20` board、SP2 ATF 與網頁救援，加入 GPT 修復。 |

最新韌體固定上游為 [`9f397975c7b47ce6986a6e39529d2f72fd5c729d`](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/commit/9f397975c7b47ce6986a6e39529d2f72fd5c729d)。插件／feed 與 HTTP 輸入由[固定來源 manifest](ci/fixed/20261031/manifest.json)綁定；完整來源、套件與建置證據保存在該 Release 的同批封存。各上游作者署名及授權保留，倉庫授權見 [LICENSE](LICENSE)。

網關：192.168.1.1
賬號：root
密碼：admin

## 2. 從 0 到 1：開箱即用的刷機 Skill

**[前往 Skill 安裝與下載頁](skills/README.md)** · [直接下載 .skill 安裝包](https://github.com/LucxXxifer/clx-s20p-firmware/raw/refs/heads/main/skills/clx-s20p-provisioning.skill) · [檢視原始碼](skills/clx-s20p-provisioning/SKILL.md)

`clx-s20p-provisioning` 是供**任意具備所需本機工具與網路權限的 Agent 使用**的完整 Skill，附工具、操作指引與離線測試，不依賴 Codex SDK 或專用 MCP。流程為：

**設備身分及 eMMC 健康 → 逐機原廠備份 → 元件／網口檢查 → 指定 FIP 寫入與讀回 → 手動進入 U-Boot → GPT／韌體刷寫及核對 → OpenWrt 備份、升級與逐項還原。**

1. 下載 `.skill`，按 ZIP 格式解壓，取得 `clx-s20p-provisioning/`。
2. 將完整資料夾放入 Agent 支援的 skills 目錄；若沒有原生 Skill 載入功能，放在可讀取的一般目錄亦可。不可只複製 `SKILL.md`。
3. 請 Agent 讀取該目錄的 `SKILL.md`，說明設備、目標及授權範圍。[使用指南](skills/README.md)附一個跨 Agent 通用範例，涵蓋 Codex、Claude Code、Gemini CLI、Cursor、WorkBuddy、OpenCode、DeepSeek harness、Pi、OpenClaw、Hermes 等宿主的能力要求。

執行端需 Python 3.10+、OpenSSH、`curl` 與 Unix `fcntl`，不需額外 Python 套件；網路指引以 macOS 為基礎，Linux／WSL 需核對主機環境，Windows 原生未支援。純聊天或無法連到設備的雲端 Agent 不能直接執行。安裝包不包含 FIP／GPT／韌體，請從各下載頁另行取得。每臺設備使用自己的 factory／boot 備份與實際 eMMC 幾何；Skill 已通過 42 項離線測試，各 Agent 的整套實機刷機及原廠還原仍需逐項驗收。詳細限制與更新方式見[使用指南](skills/README.md)。
# 方式 A：telnet 直接進（原廠默認開啟，無需任何配置）
telnet 192.168.8.1
賬號 root，密碼 tty996icu
方式 B：开 SSH（可選）
uci set dropbear.@dropbear[0].enable='1'
uci commit dropbear/etc/init.d/dropbear restart

## 3. U-Boot GPT 修正正式版

**[前往正式版下載頁](https://github.com/LucxXxifer/clx-s20p-uboot/releases/tag/s20p-gptfix-e4cf4dc72d45-d4c326981f44)** · [專案與完整修正說明](https://github.com/LucxXxifer/clx-s20p-uboot)

維護者已於 **2026-11-01 確認此修正版通過實機使用**，據此轉為正式版。適用 **CLX-S20P／2 GiB DDR4／eMMC／SP2**，提供 **FIP**，不附 BL2。

| 修正項目 | 行為 |
| --- | --- |
| 副 GPT 回指錯誤 | 將原本指向自己的 `alternate_lba` 改為回指主表 **LBA 1**，重新計算 CRC。 |
| GPT 寫入前檢查 | 核對主表／entry CRC、長度、LBA 範圍、分區重疊與設備幾何；獨立 GPT 上傳入口遇到無效資料立即拒寫。 |
| 錯誤回傳與分區保留 | 驗證、主表及副表寫入失敗會回報；分區 entry 不移動、不擴容。 |
| 建置驗證 | 真正 C 寫入函式通過 ASan／UBSan 回歸測試，兩次獨立乾淨編譯的成品一致。 |

下載檔案為 `fip-clx-s20p-2g-emmc-SP2-gptfix-e4cf4dc72d45.bin`，SHA-256：

```text
9d8d7fef88f88e353ec251270f84a7ee418e698712c74628295966eb44c63b19
```

更新 FIP **不會自動修復之前已損壞的 GPT**。GPT 仍須按實機 sector 數選擇，寫入後讀回主副表；上述寫入前拒寫保證限於獨立 GPT 上傳入口。實機使用確認不擴張為所有恢復情境的測試結論，完整使用範圍見 U-Boot 專案。
網關：192.168.1.1

## 4. 最新版：安全補丁＋精簡韌體

### 韌體下載

**[下載 25.12 補充補丁正式版｜2026-10-31](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-a-25.12-fixed-candidate-41175460764-1-9f397975)**

採固定來源，整合下列安全更新及精簡設定。下載頁提供韌體、安裝說明、校驗碼、`BUILD-VERIFICATION.json` 與同批簽章套件封存。

| 元件 | 版本／修正與影響 |
| --- | --- |
| **uhttpd／uhttpd-mod-ubus** | 兩包重建為 **`2026.08.24~373145f7-r2`**。回補 [OpenWrt 官方 `64dcf47`](https://github.com/openwrt/uhttpd/commit/64dcf47aa7f84ae60db9a3950e5c4b8fbe5ed6f1)，拒絕 ubus `list` 請求的非字串參數。 |
| **LuCI ACL** | 依 [GHSA-v5f9-62c7-cw29](https://github.com/openwrt/luci/security/advisories/GHSA-v5f9-62c7-cw29)，移除「掛載點」權限組對 `/etc/crontabs/root` 的寫入授權；保留「計劃任務」專用權限及正常編輯功能。LuCI 套件版本不變，以實際 ACL 內容核對修補。 |
| **Go／x509** | 回補官方 [Go 1.26.10](https://github.com/openwrt/packages/commit/65493e273e4b9cd7014236b69fd6fdc617b88436)／[1.27.3](https://github.com/openwrt/packages/commit/808f150a07a2071ebe032e17b5b3f567c0e27181)，對應 [GO-2026-6809／CVE-2026-111943](https://pkg.go.dev/vuln/GO-2026-6809)。**11 個 Go 執行檔已修補**；掃描成品 build info，連入 x509 verifier 的程式另核對全部 8 個 SPKI 拒絕常數。 |
| **CA 憑證** | `ca-bundle`／`ca-certificates` 為 **`20260816-r2`**，包含 [2026-10-20 OpenWrt 官方修訂](https://github.com/openwrt/openwrt/commit/63f702f1c0cebe2587aac10b63a02e964fc2a558)，同步 emSign ECC Root CA - G3、Comodo AAA Services root 撤信政策；`20260816` 是上游版本日期，`r2` 包含後續修訂。 |

**已知例外：QuickStart 0.13.0 的 `/usr/sbin/quickstart` 仍為官方預編譯 `go1.25.0`，尚未修補上述 Go x509 問題。** 更新 Go toolchain 不會重編這個檔案，報告明確標示 `legacy-unpatched-prebuilt`／`pass_with_known_exception`。完整來源、例外及檢查邊界見[安全更新說明](docs/SECURITY-BUILD.md)與 [uhttpd／ACL 回補說明](ci/security/minimal-20261031/README.md)。

### 精簡內容與驗證範圍

- 排除 **Honk、TingReader**，以及 EasyTier 的 `easytier`、`luci-app-easytier`、`luci-i18n-easytier-zh-cn`。
- **AdGuard Home** 核心、LuCI 與中文包暫不預裝；其他功能使用的共用依賴保留。
- 保留 **iStore、OpenClash、Docker** 及既有管理面板狀態顯示修正。
- 雲端建置及來源、簽章套件源、核心 ABI、映像、精確差異等既定檢查通過。管理面補丁已在既有系統完成更新及重啟後驗收；**此完整韌體映像尚未完成整機刷入與啟動驗收**。

本版 [Actions run `41175460764`](https://github.com/LucxXxifer/clx-s20p-firmware/actions/runs/41175460764)，建置 commit 為 `f5ce16d9ac320ee492385d13e48a327320f0b179`。正式發布沿用原始 tag／建置報告，不改寫既有驗證記錄。

### 下載校驗與升級

韌體：`clx-s20p-9f397975-20261031-sysupgrade.bin`，SHA-256：

```text
32e9b64a0373647079087aa53bf8c2a524d304c0fcaf0f67bb12bfcf15d1fe51
```

將下載頁 **6 個附件**放在同一資料夾，再執行；`SHA256SUMS` 核對其餘 5 個檔案：

```sh
# macOS
shasum -a 256 -c SHA256SUMS
# Linux
sha256sum -c SHA256SUMS
```

只下載 `.bin` 時，計算其 SHA-256 並比對上方雜湊。補充套件／kmod 使用同一 run 的 `s20p-samebuild-41175460764-1.tar.gz`，保持核心 ABI 與套件簽章相符。

升級前先依[備份與還原流程](skills/clx-s20p-provisioning/references/openwrt-backup-restore.md)核對板型、分區、套件系統與設定相容性，再使用 LuCI「系統 → 備份／升級」。LuCI 設定備份不會重裝插件程式，插件資料及身分需另外備份與逐項還原。

## 5. 10/17 歷史固定版

**[前往 10/17 固定版下載頁](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-candidate-40095658212-1)** · [固定來源分支 `s20p-a-10-17`](https://github.com/LucxXxifer/clx-s20p-firmware/tree/s20p-a-10-17)

保留既有歷史基準，供需要原固定版本的使用者取得。此版記錄為**基本實機及 USB 已驗**，GitHub Release 仍標示 **Prerelease**；未包含第 4 節後續安全補丁。

- 韌體：`clx-s20p-03eb3061-20261025-sysupgrade.bin`。
- 下載頁同時提供中文說明、Release manifest、公開驗證金鑰、同批套件及 `SHA256SUMS`。
- 「10/17」是歷史基準名稱，並非 GitHub 發布日；請按該頁的映像、校驗碼及配套使用。

## 6. Fork、二次開發與引用說明

**歡迎 Fork、修改、二次開發及再次開源發布，請在衍生專案的 README 與發布說明中清楚引用本專案及實際使用的上游來源。**

- 本倉庫採 [GPL-3.0](LICENSE)。再發布時須保留版權、作者署名、授權與來源資訊，標明修改內容及日期，並按授權要求提供對應原始碼；受 GPL 約束的衍生部分須繼續依 GPL 授權發布。
- 引用 **[LucxXxifer/clx-s20p-firmware](https://github.com/LucxXxifer/clx-s20p-firmware)**，註明所基於的 commit／tag 與您的改動；沿用建置框架或系統時，一併保留第 1 節對應的上游引用。
- 使用 GPT 修正 U-Boot 時，另引用 **[LucxXxifer/clx-s20p-uboot](https://github.com/LucxXxifer/clx-s20p-uboot)** 及其上游 **[Yuzhii0718/bl-mt798x-dhcpd](https://github.com/Yuzhii0718/bl-mt798x-dhcpd)**，並遵守該專案授權。
- 第三方套件、U-Boot、ATF 等元件各自的授權繼續適用。衍生版請使用自己的名稱、下載連結與驗證結果，清楚區分原專案版本與您的修改版。

README 引用範例（請替換版本與修改內容）：

```markdown
本專案基於 [LucxXxifer/clx-s20p-firmware](https://github.com/LucxXxifer/clx-s20p-firmware) 二次開發。
基準版本：<原專案 commit 或 tag>；修改日期：<YYYY-MM-DD>。
主要改動：<說明您的修改>。
建置框架源自 [dqsq2e2/wrt_release](https://github.com/dqsq2e2/wrt_release)，
系統源自 [chasey-dev/immortalwrt-mt798x-rebase](https://github.com/chasey-dev/immortalwrt-mt798x-rebase)。
本衍生版的原始碼、授權及下載：<您的專案連結>。
```

---

**韌體每日自動編譯目前已停用**，保留固定版下載；現有工作流程與建置政策可查閱 [Actions](https://github.com/LucxXxifer/clx-s20p-firmware/actions/workflows/s20p-daily.yml)及[編譯說明](README_AUTOBUILD.md)。[所有歷史版本](https://github.com/LucxXxifer/clx-s20p-firmware/releases)繼續保留。

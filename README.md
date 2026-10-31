# CLX-S20P 主線韌體

本倉庫維護 **CLX-S20P 主線韌體**，基於 ImmortalWrt 25.12，保留 AX6000 Wi-Fi，並提供 GitHub Actions 編譯與刷機／備份還原指引。僅發布 CLX-S20P，建置設定為 `S20P-WIFI-YES`，映像板型為 `clx,s20p`。

## 韌體下載

目前保留以下三個版本。時間使用 Asia/Shanghai；「10/17」是歷史版本的基準名稱，並非 GitHub 發布日期。

| 版本 | 下載與說明 |
| --- | --- |
| **主線正式版：25.12 固定來源版** · 2026-10-31 | [Release、韌體、校驗碼與同批套件](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-a-25.12-fixed-candidate-41083116478-1-9f397975) · run `41083116478` |
| 上一個成功版 · 2026-10-30 | [歷史 Release 與完整成品](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-a-25.12-candidate-40893380461-1-9f397975) · run `40893380461` |
| 10/17 歷史固定版 | [原始 Release 與校驗碼](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-candidate-40095658212-1) · [固定來源分支](https://github.com/LucxXxifer/clx-s20p-firmware/tree/s20p-a-10-17) |

最新版本按維護者決定正式發布並設為 Latest。現行名稱統一為「CLX-S20P 主線韌體」；既有檔名／tag 中的 `A`、`a`、`candidate` 及同批驗證資料保留建置當時的身分，來源、成品與校驗碼保持不變。

## 最新正式版內容與驗證範圍

- 固定系統、31 個插件／feed、CA／Go／QuickStart 及建置使用的 HTTP 輸入，避免每日來源變動影響此版本。
- CA 為 `20260816-r2`；11 個已修補 Go 執行檔使用 `go1.26.10`／`go1.27.3`。**QuickStart 0.13.0 仍使用 `go1.25.0`，是唯一已批准、尚未修補 GO-2026-6809／CVE-2026-111943 的精確例外**，詳見[安全說明](docs/SECURITY-BUILD.md)。
- 納入控制面板狀態顯示修正。
- 實際編譯及全部既定雲端檢查通過；獨立核對六個下載成品的 API digest／checksum、5 個簽章套件源、531 個 APK、153 個同 ABI 核心模組及映像內 488 個套件版本。

## 下載校驗與升級

最新正式版的韌體檔為 `s20p-A-25.12-9f397975-sysupgrade.bin`，SHA256：

```text
40a26abc68162b86afd7fd5a001518caa7c25c084ee18554140d245e6590bff3
```

將該 release 的六個檔案下載到同一資料夾，再核對完整清單：

```sh
# macOS
shasum -a 256 -c SHA256SUMS
# Linux
sha256sum -c SHA256SUMS
```

`SHA256SUMS` 列出其餘五個檔案；只下載 `.bin` 時，改用 `shasum -a 256 s20p-A-25.12-9f397975-sysupgrade.bin` 並比對上方雜湊。保留同一 run 的 `s20p-samebuild-*.tar.gz`，安裝 kmod／補充套件時使用其中相符 ABI 與簽章的套件源。

OpenWrt 升級入口為 LuCI「系統 → 備份／升級」。先依[備份與還原流程](skills/clx-s20p-provisioning/references/openwrt-backup-restore.md)核對來源／目標的板型、分區、套件系統及設定相容性，再選擇保留設定或乾淨安裝。LuCI 設定備份不會重裝插件程式，插件資料與身分需另外備份及逐項還原。

## 首次刷機與備份還原 Skill

| 用途 | 入口 |
| --- | --- |
| 安裝與使用 Codex skill | [使用指南](skills/README.md) · [下載 .skill 安裝包](skills/clx-s20p-provisioning.skill) · [Skill 原始碼](skills/clx-s20p-provisioning/SKILL.md) |
| 首次刷機 | 核對設備身分與 eMMC 健康 → 逐機原廠備份 → FIP 寫入／讀回 → 手動進入 U-Boot、刷 GPT／系統 |
| OpenWrt 升級／還原 | [辨識備份範圍、分支相容性、插件安裝及設定／資料恢復](skills/clx-s20p-provisioning/references/openwrt-backup-restore.md) |

每臺 S20P 都需保存自己的 factory／boot 資料。Skill 可用於不同 S20P 韌體分支，但每次仍需核對相容性；不預設克隆整個 100+ GB 的資料分區，不公開設備備份、訂閱、私鑰或私人設定。

## 每日編譯與來源

GitHub Actions 每日 **04:20（Asia/Shanghai）** 追蹤相容的 ImmortalWrt 25.12 系統、已選第三方插件（包括 iStore、OpenClash、Docker）及官方 CA／Go 穩定更新。來源組合改變才建置，全部檢查通過後自動發布候選版；正式發布由維護者決定。每日流程不改寫上述固定版本，也不會自動刷寫設備。

[編譯模式、來源鎖定與驗收規則](README_AUTOBUILD.md) · [安全政策](docs/SECURITY-BUILD.md) · [Actions](https://github.com/LucxXxifer/clx-s20p-firmware/actions/workflows/s20p-daily.yml) · [Releases](https://github.com/LucxXxifer/clx-s20p-firmware/releases)

本倉庫的建置框架源自 [dqsq2e2/wrt_release](https://github.com/dqsq2e2/wrt_release)，S20P 系統來源為 [chasey-dev/immortalwrt-mt798x-rebase](https://github.com/chasey-dev/immortalwrt-mt798x-rebase)。

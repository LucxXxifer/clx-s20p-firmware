# CLX-S20P 主線韌體

本倉庫維護 **CLX-S20P 主線韌體**，基於 ImmortalWrt 25.12，保留 AX6000 Wi-Fi，並提供 GitHub Actions 編譯與刷機／備份還原指引。僅發布 CLX-S20P，建置設定為 `S20P-WIFI-YES`，映像板型為 `clx,s20p`。

最新發佈為 **25.12 補充補丁正式版 · 2026-10-31**，採固定來源，納入 uhttpd／LuCI ACL 精確修補及 EasyTier 精簡。

## 韌體下載

以下為最新與保留的歷史版本。時間使用 Asia/Shanghai；「10/17」是歷史版本的基準名稱，並非 GitHub 發布日期。

| 版本 | 下載與說明 |
| --- | --- |
| **最新：25.12 補充補丁正式版** · 2026-10-31 | [Release、韌體、校驗碼與同批套件](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-a-25.12-fixed-candidate-41175460764-1-9f397975) · run `41175460764` |
| 前一固定來源正式版 · 2026-10-31 | [歷史 Release、韌體與同批套件](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-a-25.12-fixed-candidate-41083116478-1-9f397975) · run `41083116478` |
| 歷史成功版 · 2026-10-30 | [歷史 Release 與完整成品](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-a-25.12-candidate-40893380461-1-9f397975) · run `40893380461` |
| 10/17 歷史固定版 | [原始 Release 與校驗碼](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-candidate-40095658212-1) · [固定來源分支](https://github.com/LucxXxifer/clx-s20p-firmware/tree/s20p-a-10-17) |

最新版本由本專案正式發布並設為 Latest。現行名稱統一為「CLX-S20P 主線韌體」；`.bin` 下載檔名採 `clx-s20p-<upstream8>-<YYYYMMDD>-sysupgrade.bin`，日期為 Asia/Shanghai 發布日。原始 tag、run 與同批封存保留建置時的識別；歷史版本與下載入口繼續保留。

## 補充補丁正式版內容與驗證範圍

- **uhttpd／uhttpd-mod-ubus** 兩包重建為 `2026.08.24~373145f7-r2`，在固定來源上回補 [OpenWrt 官方修正 `64dcf47`](https://github.com/openwrt/uhttpd/commit/64dcf47aa7f84ae60db9a3950e5c4b8fbe5ed6f1)，拒絕 ubus `list` 請求中的非字串參數。
- **LuCI ACL** 依[官方安全公告](https://github.com/openwrt/luci/security/advisories/GHSA-v5f9-62c7-cw29)，僅移除「掛載點」權限組對 `/etc/crontabs/root` 的寫入授權；「計劃任務」專用權限及正常編輯功能保留。
- **EasyTier 精簡**：映像排除 `easytier`、`luci-app-easytier`、`luci-i18n-easytier-zh-cn`，保留其他功能使用的共用依賴。
- 沿用固定系統、插件／feed 與建置輸入；CA 為 `20260816-r2`，11 個已修補 Go 執行檔使用 `go1.26.10`／`go1.27.3`。安全檢查範圍與政策見[安全說明](docs/SECURITY-BUILD.md)。
- 保留控制面板狀態顯示修正、Honk／TingReader 排除及 AdGuard Home 暫不預裝的設定。
- 實際編譯與全部既定雲端檢查通過；已獨立核對六個發布附件的 API digest／checksum、簽章套件源、套件與核心 ABI、映像及精確差異。

本專案的正式發佈不代表完整映像已完成現機刷入與啟動驗收。此版建置識別為 run `41175460764`、本專案 commit `f5ce16d9ac320ee492385d13e48a327320f0b179`、上游 commit `9f397975c7b47ce6986a6e39529d2f72fd5c729d`；完整來源與驗證證據隨同批封存提供。

## 下載校驗與升級

最新正式版的韌體檔為 `clx-s20p-9f397975-20261031-sysupgrade.bin`，SHA256：

```text
32e9b64a0373647079087aa53bf8c2a524d304c0fcaf0f67bb12bfcf15d1fe51
```

將該 release 的六個檔案下載到同一資料夾，再核對完整清單：

```sh
# macOS
shasum -a 256 -c SHA256SUMS
# Linux
sha256sum -c SHA256SUMS
```

`SHA256SUMS` 列出其餘五個檔案；只下載 `.bin` 時，改用 `shasum -a 256 clx-s20p-9f397975-20261031-sysupgrade.bin` 並比對上方雜湊。保留同一 run 的 `s20p-samebuild-*.tar.gz`，安裝 kmod／補充套件時使用其中相符 ABI 與簽章的套件源。

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

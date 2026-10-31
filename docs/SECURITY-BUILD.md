# S20P CA 與 Go x509 安全建置

2026-10-31，[固定來源版 run 41083116478](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-a-25.12-fixed-candidate-41083116478-1-9f397975)已按維護者決定正式發布；已獨立驗證成品、CA、Go 與面板檢查。此變更只調整發布狀態與說明，不改寫原始成品、安全報告或 `stable_eligible=false`。以下 QuickStart 未修補例外及未實機驗收限制繼續適用。

2026-10-30 起，每日模式採用[每日元件政策](../README_AUTOBUILD.md)：系統及已選插件來源每日解析；CA／Go 保留本文件安全下限並接受較新的相容穩定版本。CA 輸出改由保存的官方 source archive 獨立重建驗證，Go 保留 recipe 結構及實際 ELF 檢查。依最新指示，AdGuard Home 核心、LuCI 與中文包暫不編入映像。QuickStart 的精確舊版例外不擴張；新版若已修補可通過，若仍未修補且 bytes 不同則停止發布。

以下保留 2026-10-29 固定來源安全建置的查核紀錄；其中固定版本及來源描述不是每日更新的上限。

本輪沿用 rolling A 的固定主來源 `chasey-dev/immortalwrt-mt798x-rebase@9f397975c7b47ce6986a6e39529d2f72fd5c729d`。截至 2026-10-29，上游 25.12 head 沒有前進；沒有把整個 packages feed 換成未審核的新版本。

## 來源修補

- CA 撤信已在主來源內：`ca-bundle`／`ca-certificates` 為 `20260816-r2`，移除 emSign G3 的 root 與 hash symlink。沿用 [既有精確來源及輸出檢查](SECURITY-REMOVALS-B.md)，其他 CA 遺失仍會失敗。
- 鎖定的 ImmortalWrt packages feed 仍是 Go 1.26.9／1.27.1。只回補 OpenWrt 官方 [1.26.10](https://github.com/openwrt/packages/commit/65493e273e4b9cd7014236b69fd6fdc617b88436) 與 [1.27.3](https://github.com/openwrt/packages/commit/808f150a07a2071ebe032e17b5b3f567c0e27181)，對應 [GO-2026-6809／CVE-2026-111943](https://pkg.go.dev/vuln/GO-2026-6809)。兩個完整 recipe 的 before／after SHA256、來源 tarball hash 與官方提交存於 `ci/security/go-x509.json`。
- 每個 recipe 只改 patch version 與 `PKG_HASH`，結果必須逐位元等於官方修補版；版本化 host recipes、1.26 default alias 及 1.27 host-only 行為保留。兩個 recipe 全部通過驗證後才寫入；不認得的來源內容會停止建置。

## Actions 驗收

既有來源鎖定、套件保存、APK signed feed、kernel ABI、CA 撤信及映像容量檢查全部保留。新增檢查在完整編譯及 rootfs 抽取後執行：

1. 掃描 image 內全部 ELF 的 Go build info；除下述唯一精確的 QuickStart 例外外，只接受已審核系列的安全版本（Go 1.26.10+／1.27.3+）。未知系列及 development／RC 版本需另行審核。
2. 如連入 `crypto/x509.(*Certificate).Verify`，必須找到官方的全部 8 個 SPKI 拒絕清單常數。沒有連入 verifier 的程式仍須使用安全 Go 版本。
3. `docker`、`dockerd`、`containerd`、`runc` 四個既有程式都必須在檢查清單內；不以移除 Docker 來避過檢查。
4. `reports/go-x509-security.json` schema 2 保存每個程式的 SHA256、Go 版本、verifier／常數證據及分類，並綁定原始 SquashFS SHA256 與檢查政策 SHA256。缺報告、未審核的舊版本或 payload 不同，均不得發佈候選版。存在例外時只可標為 `pass_with_known_exception`，並列出完整 `known_exceptions`；發佈驗證拒絕隱藏或改寫此狀態。

報告與所有原有證據進入同次編譯 archive 及其 checksum。每日排程仍是上海時間 04:20；手動安全建置使用單次 `force=true`，沒有設定循環重試。全部檢查通過後自動發佈 rolling prerelease；正式發布另由維護者決定，目前保留版本見[首頁](../README.md#韌體下載)。

## 保留 QuickStart 的已知安全例外

保留 `quickstart`、`luci-app-quickstart`、`luci-i18n-quickstart-zh-cn`，不移除既有首頁功能。各 Go 程式使用各自內嵌的 runtime，並不要求全系統 Go 版本相同；沒有證據顯示此次 toolchain 更新會造成 QuickStart 的 Go 版本相容性衝突，但候選的實機功能仍須另行驗收。

固定來源 `kenzok8/small-package@a5fb03c51b55e0627c55ef9fe158003119a6f2bc` 的 QuickStart 0.13.0 使用[官方預編譯 archive](https://github.com/linkease/istore-packages/releases/download/prebuilt/quickstart-binary-0.13.0.tar.gz)，`Build/Compile` 為空。至 2026-10-29 查核時，官方同名 asset 尚未更新，也未找到可公開重編的後端來源；因此升級 Go recipes 無法修補這個預編譯檔。

唯一例外必須同時符合：

- 路徑 `/usr/sbin/quickstart`；SHA256 `64d4fc08abbc6be1689ea278d2db83c14e5a129ba69e0c8153eb07446f48fc11`。
- build info 為 `go1.25.0`、已連入 x509 verifier，八項新版 SPKI 常數均不存在。
- 官方 archive SHA256 為 `40eeaba1fbb4cfb732cb5c4759103d9307cb46b83f98b4514934d3e13e8c945b`，同時由鎖定 recipe 的 `PKG_HASH` 核對。

報告將它標為 `legacy-unpatched-prebuilt`；不同 bytes、路徑、版本或其他舊版 Go 程式均不適用此例外。`BUILD-VERIFICATION.json`、`INSTALLATION.md` 及 GitHub release 內文同步揭露它未修補 GO-2026-6809／CVE-2026-111943，不宣稱全機已全面修補。

原版 init script 以 Unix socket `/var/run/quickstart/local.sock` 啟動，並非直接新增公開 TCP listener；這是來源檢查，不代表所有 LuCI 轉接 API 都沒有風險。binary 連入系統 roots 支援；系統 CA 撤信只可保護實際採用該 trust store 的 TLS 呼叫，無法代替 binary 修補，也尚未逐一確認其應用呼叫使用的 roots。

## 範圍與限制

沒有對目前 S20P 安裝套件、刷機或變更 CA／網路／代理設定。映像的檢查不涵蓋刷機後另行下載的 ShellCrash、AdGuard Home、官方 Tailscale、自訂 plugin 或 container image；這些仍需個別核對。PHP 是否受影響依實際安裝內容判定，不因安全新聞而新增 PHP。

版本與連入常數檢查不等同於在目標 CPU 執行漏洞回歸測試。新韌體的硬體開機、USB 備援、現用旁路由設定及私人服務遷移仍需實機驗收；CI 成功不代表 stable。

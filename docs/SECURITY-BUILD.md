# S20P CA 與 Go x509 安全建置

本輪沿用 rolling A 的固定主來源 `chasey-dev/immortalwrt-mt798x-rebase@9f397975c7b47ce6986a6e39529d2f72fd5c729d`。截至 2026-10-29，上游 25.12 head 沒有前進；沒有把整個 packages feed 換成未審核的新版本。

## 來源修補

- CA 撤信已在主來源內：`ca-bundle`／`ca-certificates` 為 `20260816-r2`，移除 emSign G3 的 root 與 hash symlink。沿用 [既有精確來源及輸出檢查](SECURITY-REMOVALS-B.md)，其他 CA 遺失仍會失敗。
- 鎖定的 ImmortalWrt packages feed 仍是 Go 1.26.9／1.27.1。只回補 OpenWrt 官方 [1.26.10](https://github.com/openwrt/packages/commit/65493e273e4b9cd7014236b69fd6fdc617b88436) 與 [1.27.3](https://github.com/openwrt/packages/commit/808f150a07a2071ebe032e17b5b3f567c0e27181)，對應 [GO-2026-6809／CVE-2026-111943](https://pkg.go.dev/vuln/GO-2026-6809)。兩個完整 recipe 的 before／after SHA256、來源 tarball hash 與官方提交存於 `ci/security/go-x509.json`。
- 每個 recipe 只改 patch version 與 `PKG_HASH`，結果必須逐位元等於官方修補版；版本化 host recipes、1.26 default alias 及 1.27 host-only 行為保留。兩個 recipe 全部通過驗證後才寫入；不認得的來源內容會停止建置。

## Actions 驗收

既有來源鎖定、套件保存、APK signed feed、kernel ABI、CA 撤信及映像容量檢查全部保留。新增檢查在完整編譯及 rootfs 抽取後執行：

1. 掃描 image 內全部 ELF 的 Go build info；只接受已審核系列的安全版本（Go 1.26.10+／1.27.3+）。未知系列及 development／RC 版本需另行審核。
2. 如連入 `crypto/x509.(*Certificate).Verify`，必須找到官方的全部 8 個 SPKI 拒絕清單常數。沒有連入 verifier 的程式仍須使用安全 Go 版本。
3. `docker`、`dockerd`、`containerd`、`runc` 四個既有程式都必須在檢查清單內；不以移除 Docker 來避過檢查。
4. `reports/go-x509-security.json` 保存每個程式的 SHA256、Go 版本、verifier／常數證據，並綁定原始 SquashFS SHA256 與檢查政策 SHA256。缺報告、版本不合格或 payload 不同，均不得發佈候選版。

報告與所有原有證據進入同次編譯 archive 及其 checksum。每日排程仍是上海時間 04:20；手動安全建置使用單次 `force=true`，沒有設定循環重試。全部檢查通過後只發佈 rolling prerelease，保留歷史 A／B。

## 範圍與限制

沒有對目前 S20P 安裝套件、刷機或變更 CA／網路／代理設定。映像的檢查不涵蓋刷機後另行下載的 ShellCrash、AdGuard Home、官方 Tailscale、自訂 plugin 或 container image；這些仍需個別核對。PHP 是否受影響依實際安裝內容判定，不因安全新聞而新增 PHP。

版本與連入常數檢查不等同於在目標 CPU 執行漏洞回歸測試。新韌體的硬體開機、USB 備援、現用旁路由設定及私人服務遷移仍需實機驗收；CI 成功不代表 stable。

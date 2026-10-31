# 初始 B 來源轉換紀錄與 rolling policy

本文件的 compare、CA 移除和 package 差異內容記錄 **B 最初鎖定建置**，不是把未來上游差異預先批准。GitHub `main` 的 rolling workflow 以 `chasey-dev/immortalwrt-mt798x-rebase@25.12` 的 `9f397975c7b47ce6986a6e39529d2f72fd5c729d` 為唯一起點；每次從 GitHub compare 取得一個完整 SHA，確認其為此起點的 fast-forward descendant，並在 runner 暫存目錄生成精確 source lock。若有新差異，既有 preservation、package、ABI、source、簽章與安全 gate 仍逐項檢查，任何未審查差異都會阻止候選發布。

固定 A 10/17 release `s20p-candidate-40095658212-1` 保留原始 assets 和校驗碼。初始 B candidate `s20p-b-25.12-candidate-40420056051-1` 的下載 release 已於 2026-10-31 依維護者要求清理，來源 tag、Actions 執行記錄及本文件的歷史審查保留。現行三個下載版本見[首頁](../README.md#韌體下載)。rolling 候選不得覆蓋保留版本，也不能重用不同 run 的 signing key、feed 或 kmod。

B 初始 source 固定為 `chasey-dev/immortalwrt-mt798x-rebase@9f397975c7b47ce6986a6e39529d2f72fd5c729d`，只更新 A 的 `03eb30617955519e35845838c6255ebe687eb717`。A wrapper commit `2e6371c4a4801961cf88f2094224e1bb82ef4825`／run `40095658212` 的 release ref、bundle、來源與驗收證據獨立保留。B 不覆蓋 A，也不能借用 A 的套件簽名或同批 kmod。

這次 A/B 都是 25.12、Linux 6.12.112、APK；不是 SmartRouterZone 24.10/6.6 到另一套系統的遷移。B 沒有使用不含 S20P 適配的 `rework-24.10` 分支。

## 固定與保留項目

`ci/sources.lock.json` 只有唯一的 `main-source` row 換 commit，並保留其原身分為 `previous_lock`。其餘38個 Git source rows完全相同，包含7個 feeds及已恢復／審查的客製來源。公開庫不含私人設定、ShellCrash訂閱、Tailscale identity或裝置備份。

`ci/provenance.py` 與 compilecfg 繼續只接受完整指定commit。`ci/baseline/policy.json` 的 `upstream_commit` 是本次建置期望，改為 B；新增 `original_upstream_commit` 保留03身分。除這兩個身分欄位外，原483套件、124個kmod、24個LuCI app、103個MTK符號、原kernel/fingerprint、required配置與功能要求不變。baseline integrity只同步policy檔案的hash，原ROM衍生manifest、DTB、配置與其他hash完全保留。

`ci/tests/test_source_transition.py` 以接受的 A lock canonical hash驗證38個其他來源及policy其餘欄位不變，並測試B exact head成功、A head／任意head／錯lock失敗。它不放寬source drift、原檔案、DTB、核心、signing或發佈門檻。

## A 到 B 的 source 差異

[固定compare](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/compare/03eb30617955519e35845838c6255ebe687eb717...9f397975c7b47ce6986a6e39529d2f72fd5c729d)顯示 B 為 A descendant，17 commits／18個changed paths。

下列關鍵source檔案內容相同：S20P DTS、S20 common DTS、filogic device profile、platform upgrade、02_network、MTK mt_wifi Makefile、AX6000 defconfig、Config-build.in、feeds.conf.default、mediatek target Makefile、generic/kernel-6.12。A/B kernel tar SHA256同為 `164dc9d1f6c93c61a15e1f071c48379b467f2b17c469cce7223471968208ed03`。

| 變動 | B 需要重新核對的成品 |
|---|---|
| hostapd r5→r6，RRM reporting detail及buffer/field size修正 | `wpad-openssl`等實際安裝套件版本、payload與依賴 |
| ca-certificates／ca-bundle20260816-r1→r2，blacklist兩個CA | 安全移除的原cert/symlink、CA bundle內容、套件版本與TLS信任影響 |
| mac80211 r1→r2，Realtek非PCIe TX report timeout | 以manifest確認是否入像／同批可選套件；S20P使用mt_wifi，不能先宣稱已影響其Wi-Fi |
| 其餘在Wavlink U-Boot、Airoha、IPQ806x、ramips、MT7988D TP-Link DTS、README | 不作為改動S20P網口、分區或取消原功能的理由 |

source相同不等於成品一定相同。B仍須真defconfig、完整build、同批簽名索引及APK驗證、source/HTTP紀錄、原功能/DTB/critical files/preservation檢查。其他非預期差異亦需查明，不概括歸因主source更新。

## CA 安全移除會觸發現有硬門檻

B upstream blacklist `emSign ECC Root CA - G3`及`Comodo AAA Services root`。原baseline至少含以下兩個預期被移除的path：

- `/etc/ssl/certs/emSign_ECC_Root_CA_-_G3.crt`，原SHA256 `36e68e205b53c67c7a013894e0d5c8583063468118d1ce78ecbc2200d1dd185c`。
- `/etc/ssl/certs/14bc7599.0`，原target `emSign_ECC_Root_CA_-_G3.crt`。

B新增僅限這兩條原記錄的source-bound security_removal審核：綁定固定主source、完整CA recipe/patch集合、同批prepared/wrapper來源指紋、原baseline hash及真B bundle輸出，兩條移除仍須exact difference ID批准。未知缺檔、撤銷CA復加及來源不符均hard-fail，原baseline不刪減。詳見[SECURITY-REMOVALS-B.md](SECURITY-REMOVALS-B.md)。18項已由固定來源重現的精確差異另見ci/reviewed-differences/source-transition-b.json與ca-transition-b.json；預審不等於B真成品或實機驗收。

## 候選與驗收範圍

初始 B 已由 Actions run `40420056051` 完成編譯及成品靜態驗證，並發布為獨立 prerelease；尚未完成 B 實機驗收。固定 A 已有基本實機及 USB 驗證，仍不代表長期 stable。每個候選都有獨立run／bundle／公鑰／完整matching feed，不硬套A或原ROM的ABI／fingerprint。升級後的 rolling workflow 只在 default `main` 通過全部 gates 時發布新 prerelease；保留的 B branch 是歷史來源。

完成所有成品審查後仍需上機驗證USB／HiLink、全部網口、Wi-Fi、DNS／透明代理、Tailscale、Docker與重啟。`live_acceptance=not-run`、`stable_eligible=false`保留至實機驗收，不隨source更新或編譯成功改稱穩定版。

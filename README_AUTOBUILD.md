# CLX-S20P 每日上游候選編譯

GitHub `main` 承載 rolling 25.12 build policy：每天查詢 `chasey-dev/immortalwrt-mt798x-rebase` 的 `25.12`，以已審查起點 `9f397975c7b47ce6986a6e39529d2f72fd5c729d` 驗證上游是其後代，再把該次精確 head SHA 傳給編譯器。只追蹤上游，不自動同步或放寬其餘38個 Git source pins、原 patches、Docker nftables 修補、Honk/eBPF/BTF、MTK Wi-Fi 7.6.7.3、HNAT/WARP/eQoS/TurboACC 配置。

既有 A 10/17 固定下載版保持獨立：[GitHub release](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-candidate-40095658212-1)。原始 A 成品、校驗碼與來源證據不由 rolling `main` 改寫。此前的 B 25.12 靜態候選也保留：[B candidate release](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-b-25.12-candidate-40420056051-1)。這個 workflow 只發新的 rolling prerelease candidate，不建立 stable release、不刷機；新上游差異未通過精確審查時不會發布。

首輪 rolling A 已由 [Actions run 40488248986](https://github.com/LucxXxifer/clx-s20p-firmware/actions/runs/40488248986) 完成，對應 [2026-10-27 prerelease 與完整下載](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-a-25.12-candidate-40488248986-1-9f397975)。此輪是 `force=true` 的流程驗證，上游仍為 `9f397975...`，build wrapper 為 `1a52a567...`。ROM、同批完整 feed、API digest、ZIP CRC、內外校驗碼及原生離線套件驗證均通過；492個已安裝套件、535 APK、153個同ABI kmods、28個USB/mobile可選包齊備。使用 `sha256sum -c SHA256SUMS` 前請下載該 release 的全部六個 assets 到同一目錄；manifest 列出另外五個檔案，僅下載 `.bin` 會回報缺檔。本候選尚未實機刷入驗收。

既有 A build 的 wrapper commit 為 `2e6371c4a4801961cf88f2094224e1bb82ef4825`、Actions run 為 `40095658212`，其主 source 是 chasey `03eb30617955519e35845838c6255ebe687eb717`。新 rolling 流程由固定 B 初始基準 `9f397975...` 逐次前進；每個 artifact、repository signing key、kernel ABI 及同批套件 feed 均只屬於自己的 run，不能套用 A 或其他候選的值。歷史 source 差異、CA 安全移除門檻及驗證要求見 [source transition record](docs/SOURCE-TRANSITION-B.md)。

原作者沒有保留完整 feeds、動態資產和環境 lock，因此無法承諾與原 ROM 位元一致。`ci/sources.lock.json` 保存已審查的初始輸入；Actions 只在 runner 暫存目錄產生本次上游 SHA 的 lock 和基線，避免提交動態 lock 或變更受審基線。kernel ABI、build fingerprint 及 repository signing key 依實際建置產生，絕不套用 A 或其他 run 的值。

## 觸發方式

- `.github/workflows/s20p-daily.yml`：每日 **04:20 Asia/Shanghai（20:20 UTC）**，並支援 Actions → Run workflow；定時 workflow 僅在 repository 的 default branch 上運行，所以 rolling `main` 必須設為 default branch 並啟用此 workflow，排程才會生效。
- 每次執行先讀取上游完整 head SHA 並以 GitHub compare 證明其為已審查基準的 fast-forward descendant。排程遇到相同 head 且找到同 wrapper commit 的完整 state 記錄時略過編譯；新 head 才建置。手動 `force=true` 可在修正 build policy 後重試同一 head。無法取得或驗證前次 state 時會以「重新建置」處理，不會因此錯誤略過候選。
- 若有可信的前次 state 且 head 改變，另以 GitHub compare 驗證其仍為前次 head 的後代；回退或分歧即拒絕，`force` 不能繞過。缺少可信歷史時只依固定起點驗證，不宣稱已證明歷史上未回退。
- 需要重試時使用 Actions → **Run workflow → force=true** 建立新 run；不使用 **Re-run jobs**。後者重用舊 run identity，會被明確拒絕。即使某次執行被拒絕或中斷，仍會向前查找完整的可信 state，不會遺忘較早已驗證的 head。
- 編譯不會自動刷機。新上游版本如出現未審查的 package、ABI、檔案、安全或來源差異，只保存失敗／審查證據，不建立 prerelease。原 watcher 和其他排程移至 `docs/upstream-workflows/` 保留。
- 新 checkout 執行 `bash ci/build.sh`。完整建置需 Linux／Ubuntu 24.04 和至少50GiB可用空間；runner環境安裝由已審查的 `ci/setup-runner.sh` 處理，不執行遠端 root 初始化腳本。

本公開庫使用 GitHub-hosted 標準 Linux runner，可使用[公開庫免費 Actions](https://docs.github.com/en/billing/concepts/product-billing/github-actions)；排程、解析、編譯與候選發布都在 GitHub，無須本機開機或執行 AI 任務。GitHub 排程可能延遲，公開庫連續60天無 repository activity 時可能自動停用排程；可在 Actions 重新啟用。artifact 保留7天，release 成品另行保存；不使用付費 larger runner。

## 原功能與新增內容

配置疊加順序維持原四層，再追加本任務的增量：

1. `S20P-WIFI-YES.config`
2. `compile_base.config`
3. `docker_deps.config`
4. `honk.config`
5. `migration.config`：HiLink CDC Ethernet、ipset、ArkBridge Python3 base/urllib/openssl。
6. `mobile_usb.config`：RNDIS、NCM、Huawei NCM、QMI、MBIM、AT/ACM、USB模式切換，以及 Realtek RTL8156 系列／ASIX／AQC111／Microchip 等 USB Ethernet 驅動，新增項目選 `m`，編成同批可選套件。

詳細可選清單見 [ci/mobile-usb-packages.txt](ci/mobile-usb-packages.txt)，各裝置證據與限制見 [docs/mobile-usb.md](docs/mobile-usb.md)。同批編好不代表所有型號都已實物驗收。介面、APN、PIN、模式及供電仍要按實物確認；本流程不修改模組韌體、不自動切模式或啟動撥號服務。

不包含私人設定、訂閱、factory、GPT或 SSH 私鑰。

## 自動檢查與產物

`ci/baseline/` 僅包含原廠公開 ROM 衍生的檔案清單、hash、DTB、套件資料和原四層配置，無設備的私密 overlay。

- `.config` 檢查原483包可編譯、124個原 kmod、24個 LuCI app、103個 MTK 配置、新增 root 與28個可選套件沒有被 defconfig 丟棄。
- 原始成品 SquashFS 抽出後，再驗483包實際安裝、kmod exact kernel dependency、全部4334項原路徑、critical資料與DTB硬體語義。包名齊全不足以代表全部功能等效。
- 版本、預設／資產／檔案差異須精確審核；未批准差異只保留 evidence，阻止候選發佈，不能以 allow-all 取消檢查。
- 編譯成功後、原流程清理前保存完整 `bin/`、所有 APK／已簽名 `packages.adb`、公開key、kernel config／Module.symvers、final config、buildinfo、來源與HTTP輸入紀錄、logs與SHA256。
- 使用同批 host apk 驗證 signed index，再核 APK payload 與 index identity。沿用原 `CONFIG_SIGN_EACH_PACKAGE=n` 的模型，不假裝 unsigned APK 有單獨簽章，不降低安裝信任要求。
- 全部自動檢查通過時僅發佈 **prerelease candidate**；仍需上機驗證 USB／Wi-Fi／加速／重啟及遷移，不能宣稱 stable。
- evidence 與未發布的候選 artifact 保留7天；發布成功的候選以 GitHub prerelease 保存，並附上完整 same-build feed，避免只剩 ROM 而失去配套 kmod。

每日排程線上解析上游 head；只有新增 head 才啟動完整編譯。每次建置均對解析出的精確 SHA 建立暫存 source lock，再跑同一套既有檢查。套件源 pin 仍固定，除非明確審查並更新；不可把不同 ABI 的現成 kmod 混入此 ROM，也不可 force dependencies。人工 `force` 只是重試目前已確認的 SHA，不會批准差異或改變來源。

目前 CA 安全移除與已批准差異仍綁定已審查 SHA `9f397975...`。新上游可以自動編譯，但即使 CA 配方看似相同，也須取得該精確 source 的審查紀錄才可發布；workflow 不會自行擴大批准範圍。

為讓原流程能在已鎖定來源上執行，另有三項具體修正：完整核心版本由 series `6.12` 與 patch `.112` 合併核對；WeeChat LuCI 的 virtual provider 改為原預設 `weechat-minimal`，解除 Kconfig 循環；S20P CI 保留固定 packages feed 的 Go 1.27.1／1.26 compiler recipes，避免原腳本以單一 Go 1.26 override 刪掉新版 Tailscale 必需的 1.27.1。這些變更都有回歸檢查，不宣稱與原歷史 feeds 完全相同。原 fragment 的無效 TurboACC selector 亦有[單獨政策說明](docs/preservation-gates.md)。Tailscale／AdGuardHome 的私人設定與服務安裝仍由後續遷移處理，並未因工具鏈可用就自動預裝或啟動。

Git 來源已固定，但原 wrapper 仍讀取部分可變 HTTP 發布資訊及資產。本分支記錄其 URL、payload hash 與準備後來源 hash；Honk 亦核對發布 metadata、recipe 與資產 hash。這不等於所有歷史 HTTP／runner 輸入均已還原或凍結。動態內容改變可能要求更新相容 pin，或產生待審差異／建置失敗；不會自動繞過檢查。

## 首輪差異審查與配套安裝

第一次完整建置預期可能是 `review_required`，因原版沒有完整歷史 feeds；此時 Actions 保存成品及 `reports/preservation-image.json`，不發候選版。逐項審查其差異後，才可在版控加入 `ci/approved-differences.json`，格式為 `{"approved":[{"id":"報告中的完整差異ID","reason":"具體審查理由"}]}`，再重跑。ID 包含實際差異內容的 hash；換版本、bytes 或不同差異不會沿用舊批准。不得批次無條件批准所有差異。

完整 signed feed 保存在候選 bundle 的 `bin/`，不是另一套核心的官方套件源。預設未額外部署 GitHub Pages 或修改公開上游套件源。安裝可選 USB kmod 時，先核對候選 SHA256 與路由器的 kernel package 完整版本，解開同批 bundle，再使用其中的 signed `packages.adb` 及 APK；跨 feed 的共同依賴也必須來自該 bundle。首次建立遠端／本地套件源時會另外驗證裝置的原生 APK 安裝流程，不能因有 APK 檔就略過簽章或宣稱已實物安裝。

## 本地驗證

```sh
python3 -m unittest discover -s ci/tests -v
bash ci/tests/test_network.sh
bash build.sh S20P-WIFI-YES config_preview
bash -n ci/build.sh ci/capture.sh ci/setup-runner.sh build.sh
actionlint .github/workflows/s20p-daily.yml
```

Mac 的內建 Bash 3.2 不支援原 wrapper 的 nameref；config_preview 與shell測試應使用 Linux／Bash 4.3以上。`config_preview` 只證明配置組裝順序，不能代替真 defconfig／編譯／硬體驗收。

# CLX-S20P 每日上游候選編譯

2026-10-30 每日建置政策：系統和全部已選第三方插件一併追蹤，移除 Honk／TingReader，暫不預裝 AdGuard Home，加入控制面板修復。CA 下限為 `20260816-r2`，Go 下限為 1.26.10／1.27.3；採用相容上游與官方穩定版本中較新的版本，不為復刻而降版。實際 image ELF 仍檢查 Go build info／x509 拒絕清單。

保留的官方 QuickStart 0.13.0 預編譯後端仍是 Go 1.25.0，尚未包含此次修補；僅對已核對的精確 binary 設立可稽核例外，候選報告標為 `pass_with_known_exception`。每日會追蹤 QuickStart 更新，新版修補後可通過；不同 bytes 的未修補新版會阻擋發佈，不會擴張舊例外。這不是全機修補完成；既有例外與限制見 [SECURITY-BUILD.md](docs/SECURITY-BUILD.md)。

GitHub `main` 每天查詢 `chasey-dev/immortalwrt-mt798x-rebase` 的 `25.12`，並追蹤 `ci/plugin_sources.py` 選出的 31 個插件／feed 來源。保留原相容分支，使用完整 commit 及 GitHub compare 證明來源前進，拒絕倒退／分叉。原 patches、Docker nftables、MTK Wi-Fi、HNAT/WARP/eQoS/TurboACC 和共用 BPF/BTF 配置仍受原驗收門檻保護。

| 元件 | 每日追蹤來源 |
| --- | --- |
| iStore、taskd、xterm | `linkease/istore`，原 `main` 分支 |
| OpenClash、QuickStart 及其他自訂包 | `kenzok8/small-package`，原相容分支 |
| Docker／dockerd／containerd／runc | `immortalwrt/packages`，原相容分支 |
| Dockerman、Docker LuCI library | `lisaac/luci-app-dockerman`、`lisaac/luci-lib-docker` |
| 其他已編入插件及 LuCI | `ci/sources.lock.json` 的已選來源，完整清單由 resolver 驗證 |
| CA／Go | 官方 CA recipe／Debian source 與 `go.dev` 穩定目錄，保留安全下限 |

Honk、TingReader、Lucky、AdGuard Home 等未編入的專用來源不會觸發 S20P 重編；仍有其他已選套件的共用 feed 繼續追蹤。Docker 容器內的應用映像不屬於韌體套件更新；路由器上自行安裝的軟體也不會被此 workflow 遠端自動更新。

既有 A 10/17 固定下載版保持獨立：[GitHub release](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-candidate-40095658212-1)。原始 A 成品、校驗碼與來源證據不由 rolling `main` 改寫。此前的 B 25.12 靜態候選也保留：[B candidate release](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-b-25.12-candidate-40420056051-1)。這個 workflow 只發新的 rolling prerelease candidate，不建立 stable release、不刷機；新上游差異未通過精確審查時不會發布。

首輪 rolling A 已由 [Actions run 40488248986](https://github.com/LucxXxifer/clx-s20p-firmware/actions/runs/40488248986) 完成，對應 [2026-10-27 prerelease 與完整下載](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-a-25.12-candidate-40488248986-1-9f397975)。此輪是 `force=true` 的流程驗證，上游仍為 `9f397975...`，build wrapper 為 `1a52a567...`。ROM、同批完整 feed、API digest、ZIP CRC、內外校驗碼及原生離線套件驗證均通過；492個已安裝套件、535 APK、153個同ABI kmods、28個USB/mobile可選包齊備。使用 `sha256sum -c SHA256SUMS` 前請下載該 release 的全部六個 assets 到同一目錄；manifest 列出另外五個檔案，僅下載 `.bin` 會回報缺檔。本候選尚未實機刷入驗收。

既有 A build 的 wrapper commit 為 `2e6371c4a4801961cf88f2094224e1bb82ef4825`、Actions run 為 `40095658212`，其主 source 是 chasey `03eb30617955519e35845838c6255ebe687eb717`。新 rolling 流程由固定 B 初始基準 `9f397975...` 逐次前進；每個 artifact、repository signing key、kernel ABI 及同批套件 feed 均只屬於自己的 run，不能套用 A 或其他候選的值。歷史 source 差異、CA 安全移除門檻及驗證要求見 [source transition record](docs/SOURCE-TRANSITION-B.md)。

原作者沒有保留完整 feeds、動態資產和環境 lock，因此無法承諾與原 ROM 位元一致。`ci/sources.lock.json` 保存已審查的初始輸入；Actions 只在 runner 暫存目錄產生本次上游 SHA 的 lock 和基線，避免提交動態 lock 或變更受審基線。kernel ABI、build fingerprint 及 repository signing key 依實際建置產生，絕不套用 A 或其他 run 的值。

## 觸發方式

- `.github/workflows/s20p-daily.yml`：每日 **04:20 Asia/Shanghai（20:20 UTC）**，並支援 Actions → Run workflow；定時 workflow 僅在 repository 的 default branch 上運行，所以 rolling `main` 必須設為 default branch 並啟用此 workflow，排程才會生效。
- 每次執行解析系統完整 head SHA、插件來源及安全元件，產生組合 fingerprint。同 wrapper＋系統 SHA＋元件 fingerprint 已嘗試過時跳過編譯；系統未更新而插件有更新也會建置。失敗後不盲目重編相同輸入，手動 `force=true` 可在修正原因後重試。無法取得或驗證前次 state 時會重新建置，不會錯誤略過候選。
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
4. 不再載入 `honk.config`；保留已有共用核心／BPF/BTF 依賴，避免無關 ABI 改動。
5. `migration.config`：HiLink CDC Ethernet、ipset、ArkBridge Python3 base/urllib/openssl。
6. `mobile_usb.config`：RNDIS、NCM、Huawei NCM、QMI、MBIM、AT/ACM、USB模式切換，以及 Realtek RTL8156 系列／ASIX／AQC111／Microchip 等 USB Ethernet 驅動，新增項目選 `m`，編成同批可選套件。

詳細可選清單見 [ci/mobile-usb-packages.txt](ci/mobile-usb-packages.txt)，各裝置證據與限制見 [docs/mobile-usb.md](docs/mobile-usb.md)。同批編好不代表所有型號都已實物驗收。介面、APN、PIN、模式及供電仍要按實物確認；本流程不修改模組韌體、不自動切模式或啟動撥號服務。

不包含私人設定、訂閱、factory、GPT或 SSH 私鑰。

官方 AdGuard LuCI 來源目前沒有要求的 `zh-cn` 翻譯，依使用者指示暫不編入 `adguardhome`、`luci-app-adguardhome`、`luci-i18n-adguardhome-zh-cn`；不自行補造中文套件，也不因其官方 release 更新觸發建置。這不影響現用設備上的 AdGuard 安裝。ShellCrash、ArkBridge、私人 YAML、訂閱、Tailscale 身分及其他插件資料不會放進公開映像；LuCI 設定備份也不等於插件程式或完整資料備份，需按 skill 的還原指引另行處理。

## 自動檢查與產物

`ci/baseline/` 僅包含原廠公開 ROM 衍生的檔案清單、hash、DTB、套件資料和原四層配置，無設備的私密 overlay。

- `.config` 與成品保留原套件基準，僅允許已審查的 Lucky／Honk／TingReader 缺席，要求三個 AdGuard 套件均排除，並保留新增 root 與28個可選套件。移除 Honk 後仍明確保留原有 `coreutils-stat`、`libsqlite3`、`sqlite3-cli` 及共用核心功能。
- SquashFS 抽出後檢查完整原路徑（只接受精確審查的移除）、kmod exact kernel dependency、critical 資料與 DTB 語義。新增路徑仍須屬於 signed feed 驗證過的套件依賴閉包，不能藉排除插件放寬基準。
- CA 以保存的 hash-pinned Debian archive 和完整 patches 獨立重建，不執行下載的 Makefile／Python。成品的完整 trust set、bundle、aliases、版本及撤銷 DER 都須符合重建；來源 manifests 與組合 fingerprint 綁定同一次 build。五個 dashboard 安裝檔亦須符合已審查 payload，Lua／Node 回歸檢查列入 Actions。
- 版本、預設／資產／檔案差異須精確審核；未批准差異只保留 evidence，阻止候選發佈，不能以 allow-all 取消檢查。
- 編譯成功後、原流程清理前保存完整 `bin/`、所有 APK／已簽名 `packages.adb`、公開key、kernel config／Module.symvers、final config、buildinfo、來源與HTTP輸入紀錄、logs與SHA256。
- 使用同批 host apk 驗證 signed index，再核 APK payload 與 index identity。沿用原 `CONFIG_SIGN_EACH_PACKAGE=n` 的模型，不假裝 unsigned APK 有單獨簽章，不降低安裝信任要求。
- 全部自動檢查通過時僅發佈 **prerelease candidate**；仍需上機驗證 USB／Wi-Fi／加速／重啟及遷移，不能宣稱 stable。
- evidence 與未發布的候選 artifact 保留7天；發布成功的候選以 GitHub prerelease 保存，並附上完整 same-build feed，避免只剩 ROM 而失去配套 kmod。

每日解析後建立暫存 source lock，固定當次精確來源；不提交動態 lock、不混入其他 ABI 的 kmod、不 force dependencies。人工 `force` 只是重試，不會批准差異或改變來源。追蹤更新、編譯成功和通過發佈驗收是三個不同結果；未知設定／依賴／來源結構差異會保存報告並停止發佈。

歷史固定版保留原 CA 精確審查。每日模式改由來源綁定及獨立重建驗證更新後 CA；只有重建證明的憑證差異可自動接受，不是整個 `/etc/ssl` 的豁免。Dashboard 上游三個輸入有變時停止套用舊修復，須重新整合並通過測試，避免覆蓋上游新版。

原流程的核心版本合併核對、WeeChat virtual provider 修正及版本化 Go host tree 仍保留。Go recipe 的版本／官方 archive hash 可隨穩定 patch 更新；未知 recipe 結構或 host alias 會停止，不能用整棵 Go override 刪除 Tailscale 所需工具鏈。每日模式停用歷史復刻用的舊 geodata／AxonHub recipe pin，避免把新來源降回舊版。原 fragment 的無效 TurboACC selector 亦有[政策說明](docs/preservation-gates.md)。

每次 Git 來源均已固定，但 wrapper 仍讀取部分 HTTP 發布資訊及資產；記錄 URL、payload hash 與準備後來源 hash。這不等於所有歷史 HTTP／runner 輸入均已還原或凍結。動態內容改變可能產生待審差異／建置失敗，不會繞過檢查。

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

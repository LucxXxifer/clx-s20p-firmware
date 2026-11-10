# CLX-S20P 主線韌體每日編譯

> **目前狀態：改為「回釘已審版本」策略並恢復每日編譯（2026-11-10）。**
>
> - **背景**：上游 `quickstart-binary-0.13.1.tar.gz`（`f84ce3bf…`，2026-11-03 發布）內含的後端 ELF 仍以 **Go 1.25.0** 建置，低於 CVE-2026-111943（GO-2026-6809）在 **1.26.10／1.27.3** 的修正線。`go_security` 門禁在 run `42616271150` 正確攔下：`Go security gate: unpatched or unreviewed Go version: go1.25.0`。這是真陽性判定，不是 CI 缺陷。
> - **新策略（回釘 hold-back）**：每日 resolver 不再自動接受上游任何 QuickStart 二進位 bump。`ci/security/go-x509.json` 新增 `reviewed_prebuilt_archives`；`ci/components.py` 只使用清單內已審核的確切封存，上游升到未審版本時自動回釘到「不新於鎖定版本的最新已審封存」（目前 `0.13.0`／`40eeaba1…`），並在 `component-preparation.json` 與發布說明標明 `reviewed-holdback` 與上游版本。離線預編譯 daemon 自帶 Go 工具鏈，版本號無法證明其編譯器，因此只能以精確位元組審核，不能靠版本字串放行。
> - **未放寬任何校驗**：例外仍只覆蓋 0.13.0 的精確 binary（`64d4fc08…`）；`PKG_HASH`、官方來源 origin、Go 版本門檻、x509 SPKI 常數、pin／merge-base 比對全部不變。影像門禁另新增一道檢查：`quickstart` 套件版本必須等於已審 pin，否則 `component_gate` 失敗。
> - **恢復上游版本的方式**（無需改程式）：上游以 Go ≥1.26.10 重建 prebuilt 後，把新封存加進 `reviewed_prebuilt_archives`（`security_exception_id` 填 `null` 表示已修補、不需例外）→ 下次解析自動採用新版本。清單每筆都必須與 `source_archive_url` 的檔名、SHA256 完全對應，且未修補者必須指向既有的精確例外。
> - **上游問題**：[istoreos/quickstart#16](https://github.com/istoreos/quickstart/issues/16)，請求以 Go ≥1.26.10（或 ≥1.27.3）重建並重新發布 prebuilt。這條回釘是上游修好前的可控狀態，不是放行未修補二進位。
> - **歷史**：暫停期間（2026-11-10 07:50 起）曾以 `gh workflow disable` 停用排程；本次改動後重新啟用。停用與回釘都沒有刪除校驗步驟、跳過測試或使用 `continue-on-error`。

目前正式下載入口為[CLX-S20P 主線韌體 2026-10-31 固定來源正式版](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-a-25.12-fixed-candidate-41083116478-1-9f397975)（run `41083116478`）。維護者已將此 release 正式發布並設為 Latest；原始 tag、映像內容與同批封存保持不變，`live_acceptance=not-run`、`stable_eligible=false` 及 QuickStart 未修補例外仍如實保留。正式發布不等於實機／長期穩定性驗收；三個保留版本見[首頁](README.md#韌體下載)。本文件以下說明自動編譯政策，並不自動把新候選提升為正式版。

三個已發布版本的 `.bin` 下載檔名統一為 `clx-s20p-<upstream8>-<YYYYMMDD>-sysupgrade.bin`，日期採 Asia/Shanghai 發布日；最新檔名為 `clx-s20p-9f397975-20261031-sysupgrade.bin`。此調整只變更下載名稱及配套清單／說明，映像 SHA256 與原始 run 證據不變，不需重新編譯。

新增固定來源模式：Actions → **Run workflow → fixed=true** 使用 `ci/fixed/20261030/manifest.json`，固定 run `41031102553` 的系統、31 個插件／feed、CA／Go／QuickStart、5 份 wrapper HTTP 輸入及 Debian Snapshot CA 原始封存。封存 SHA256 必須與固定 CA recipe 的上游 `PKG_HASH` 一致，並核對 Snapshot SHA-1 內容位址；固定建置會將同一封存放入 OpenWrt `dl/` 快取，避免 Debian pool 原 URL 下架後編譯階段再次下載失敗。HTTP 內容按精確 URL／SHA256 重播，未知下載、內容竄改、未完整消耗輸入均停止；仍經原來源、簽章、ABI、面板、安全與精確差異驗收。固定模式不查詢新版元件，不採 rolling 的前次 head 作為版本選擇；仍驗證來源基準 ancestry。排程仍使用每日追新的 rolling 模式。固定輸入與 runner 工具鏈完全可重現是不同承諾；此模式不保證二進位逐位一致。

固定模式成功後另建 `s20p-a-25.12-fixed-candidate-…` prerelease，保存完整同批套件與證據，不改寫歷史 release。維護者可另外決定正式發布，但不因此改寫成品或驗收事實；既有 QuickStart 精確舊版例外依然公開。一般 `fixed=false`／每日排程維持最新穩定元件政策，`force=true` 只在修正原因後要求重新建置。

2026-10-30 每日建置政策：系統和全部已選第三方插件一併追蹤，移除 Honk／TingReader，暫不預裝 AdGuard Home，加入控制面板修復。CA 下限為 `20260816-r2`，Go 下限為 1.26.10／1.27.3；採用相容上游與官方穩定版本中較新的版本，不為復刻而降版。實際 image ELF 仍檢查 Go build info／x509 拒絕清單。

保留的官方 QuickStart 0.13.0 預編譯後端仍是 Go 1.25.0，尚未包含此次修補；僅對已核對的精確 binary 設立可稽核例外，候選報告標為 `pass_with_known_exception`。上游版本仍由每日 resolver 追蹤並記錄，但只有列入 `reviewed_prebuilt_archives` 的封存能進入映像：未審的新版會被回釘而不是直接採用，已審的修補版才會放行。這不是全機修補完成；既有例外與限制見 [SECURITY-BUILD.md](docs/SECURITY-BUILD.md)。

2026-10-31 官方來源複核更正了「未找到可公開重編 QuickStart 後端」的舊結論：已確認 iStoreOS 官方 MIT source，且 ARM64 靜態交叉編譯通過 Go 1.26.10 與 x509 檢查；尚未整合現有 LuCI／dashboard/API、build gates 或實機驗收，該 source 也未納入每日 resolver。較新的 app-hub 測試預編譯仍不符合 Go／SPKI 門檻。這項研究不改變目前固定映像的精確例外與建置門檻，詳見 [2026-10-31 研究更正](docs/SECURITY-BUILD.md#2026-10-31-官方來源複核與更正)。

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

保留下載版本為最新固定正式版、[上一個成功版 run 40893380461](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-a-25.12-candidate-40893380461-1-9f397975)及 [10/17 歷史固定版](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-candidate-40095658212-1)。2026-10-31 清理其他舊 release，保留來源 tags 與 Actions 執行記錄；失敗／取消建置的附件一併清理。每日 workflow 仍只自動發布 prerelease，不會改寫正式固定版或自動刷機；未審查差異繼續阻止發布。

最新固定版已完成實際編譯與獨立離線驗收：488 個已安裝套件、531 APK、5 個簽章套件源、153 個同 ABI kmods、28 個 USB/mobile 可選包，以及 ROM、API digest、ZIP CRC、內外校驗碼、面板與安全檢查均通過。核對完整 `SHA256SUMS` 時，需先把該 release 的六個 assets 下載到同一目錄；只下載 `.bin` 會回報其他檔案缺失。成品驗證不代替實機刷入驗收。

10/17 歷史固定版的 wrapper commit 為 `2e6371c4a4801961cf88f2094224e1bb82ef4825`、Actions run 為 `40095658212`，其主 source 是 chasey `03eb30617955519e35845838c6255ebe687eb717`。主線每日編譯由已審查初始基準 `9f397975...` 逐次前進；每個 artifact、repository signing key、kernel ABI 及同批套件 feed 均只屬於自己的 run，不能套用歷史版或其他候選的值。歷史 source 差異、CA 安全移除門檻及驗證要求見 [source transition record](docs/SOURCE-TRANSITION-B.md)。

原作者沒有保留完整 feeds、動態資產和環境 lock，因此無法承諾與原 ROM 位元一致。`ci/sources.lock.json` 保存已審查的初始輸入；Actions 只在 runner 暫存目錄產生本次上游 SHA 的 lock 和基線，避免提交動態 lock 或變更受審基線。kernel ABI、build fingerprint 及 repository signing key 依實際建置產生，絕不套用其他 run 的值。

## 觸發方式

- `.github/workflows/s20p-daily.yml`：每日 **04:20 Asia/Shanghai（20:20 UTC）**，並支援 Actions → Run workflow；定時 workflow 僅在 repository 的 default branch 上運行，所以 rolling `main` 必須設為 default branch 並啟用此 workflow，排程才會生效。**（2026-11-10 起暫停，見頂部狀態區塊；cron 與 workflow 檔案內容未變，僅以 `gh workflow disable` 停用。）**
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
- 新 Release 自動附上繁體中文更新日誌：依發布時間選取前一個成功發布的同系列候選，核對精確 CI attempt、tag commit、API SHA256 與 release checksums，再比較同批套件／kernel ABI、公開來源 SHA、wrapper 補丁檔案、安全與 logging 報告。基準可為 fixed 或 rolling，不以 Latest 或較大 run ID 代替發布時間；不修改歷史 Release。首次無基準會明示；基準驗證失敗會停止發布。
- 日誌區分「上游宣稱」、「同批產物證據」與「硬體未驗收」。上游提交摘要最多查詢10個 GitHub repository、各20筆，截斷／API 無法取得時明示；不憑 commit message、檔名或版本號宣稱 CVE 已修復。套件表最多80項、來源40項、補丁30項，超出時列總數及同批完整清單位置。
- `BUILD-VERIFICATION.json` 的 `changelog_inventory` 綁定本版公開證據，prepare／verify 重讀同批封存比對，仍維持原六個 release 資產。舊候選缺少此欄位時，下載其 checksum-verified archive，以既有安全 reader 核內層 SHA256 再重讀清單；不執行 archive 內的工具、不以新版政策重審歷史來源。私人 DNS／啟動修補、安裝後下載及本機個性化資料不在此映像日誌內。
- evidence 與未發布的候選 artifact 保留7天；發布成功的候選以 GitHub prerelease 保存，並附上完整 same-build feed，避免只剩 ROM 而失去配套 kmod。

每日解析後建立暫存 source lock，固定當次精確來源；不提交動態 lock、不混入其他 ABI 的 kmod、不 force dependencies。人工 `force` 只是重試，不會批准差異或改變來源。追蹤更新、編譯成功和通過發佈驗收是三個不同結果；未知設定／依賴／來源結構差異會保存報告並停止發佈。

歷史固定版保留原 CA 精確審查。每日模式改由來源綁定及獨立重建驗證更新後 CA；只有重建證明的憑證差異可自動接受，不是整個 `/etc/ssl` 的豁免。Dashboard 上游三個輸入有變時停止套用舊修復，須重新整合並通過測試，避免覆蓋上游新版。

原流程的核心版本合併核對、WeeChat virtual provider 修正及版本化 Go host tree 仍保留。Go recipe 的版本／官方 archive hash 可隨穩定 patch 更新；未知 recipe 結構或 host alias 會停止，不能用整棵 Go override 刪除 Tailscale 所需工具鏈。當追蹤上游 main 的套件（如 AxonHub）自行把 go.mod 門檻抬到預設 1.26 以上時，CI 依 recipe 鎖定的 commit 讀取 go.mod，只把 host 工具鏈選擇子改指向樹內已審查的 `golang1.27`，不開啟工具鏈下載（維持 `GOTOOLCHAIN=local`）、不改來源 pin、`PKG_HASH` 或任何校驗步驟。選擇順序以上游為準：上游 `GO_DEFAULT_VERSION` 仍滿足門檻就維持預設系列（不主動跳版），不足時取樹內最接近上游的最新已審查系列，因此上游發布新系列後只要 feed 同步進來就會自動跟上，無須再改此腳本。每日模式停用歷史復刻用的舊 geodata／AxonHub recipe pin，避免把新來源降回舊版。原 fragment 的無效 TurboACC selector 亦有[政策說明](docs/preservation-gates.md)。

每次 Git 來源均已固定，但 wrapper 仍讀取部分 HTTP 發布資訊及資產；記錄 URL、payload hash 與準備後來源 hash。這不等於所有歷史 HTTP／runner 輸入均已還原或凍結。動態內容改變可能產生待審差異／建置失敗，不會繞過檢查。

## 首輪差異審查與配套安裝

第一次完整建置預期可能是 `review_required`，因原版沒有完整歷史 feeds；此時 Actions 保存成品及 `reports/preservation-image.json`，不發候選版。逐項審查其差異後，才可在版控加入 `ci/approved-differences.json`，格式為 `{"approved":[{"id":"報告中的完整差異ID","reason":"具體審查理由"}]}`，再重跑。ID 包含實際差異內容的 hash；換版本、bytes 或不同差異不會沿用舊批准。不得批次無條件批准所有差異。

批准清單的作用域以「已審查基準」綁定，而不是以當次解析到的上游 head 綁定。`ci/upstream_sync.py` 製備 run 級基線時，除了把 `policy.json` 的 `upstream_commit` 改寫為解析後的 head，也會寫入 `reviewed_upstream_commit=<審查基準>`；`ci/preservation.py` 只在這兩者之一等於審查基準時才載入整份批准清單，`verify_prepared` 會拒絕被改寫的 `reviewed_upstream_commit`。缺少此欄位時清單會被整體忽略，於是每個上游前進日都會退化成全量 `review_required`；批准仍須逐項精確 ID 加理由，未放寬任何校驗。

### 上游版本／資料輪轉的歸因（不需每日重批）

rolling 每日追上游 head，而 `ci/baseline` 固定不動，因此 `axonhub`、`miniupnpd` 這類以日期為 `PKG_VERSION` 的套件，以及 `v2ray-geosite`／`geosite.dat` 這類每日多次更新的資料包，每天都會產生新的差異 ID，逐項人工批准永遠追不上（差異 ID 含 actual 值，換版本即換 ID）。

`ci/upstream_rotation.py` 在 `make` 之前，從 `provenance/prepared-source-files.json` 已登記的 recipe 讀出每個套件**宣告**的值：`PKG_VERSION`+`PKG_RELEASE`、`Package/<name>` 區塊內的 `VERSION` 覆寫、以及 recipe 內所有下載 `HASH`。每個項目都綁定該 recipe 檔案本身的 SHA256（該 manifest 已經進入 `build_fingerprint`／`prepared_tree_hash`，並隨 `source-lock.json` 保存），索引寫入 `provenance/recipe-index.json` 並隨證據 artifact 上傳。

映像門禁只在下列情況把差異記為 `expected` 並附上 recipe 路徑與宣告值：

- `package_version`：安裝版本**完全等於** recipe 宣告的版本（僅限非 `kmod-*`、非 `kernel`）；
- `file_sha256`：該檔案 owned package 的 recipe 宣告了完全相同的下載 `HASH`（等同證明位元組是上游下載檔，即使是 critical 路徑）；
- `package_dependencies`／`added_file`：屬於「版本剛好等於 recipe 宣告值」的套件，且該 recipe 目錄下沒有本地 `patches/`（有補丁則不歸因，仍需人工複核）。

同一套件名可能同時存在於兩份 recipe（例如 `feeds/packages/net/miniupnpd` 是 2.3.9，`custom_feed/miniupnpd` 是日期版 20261110）。這類重複不再一律判為 conflict：`feeds install` 只會把其中一份連結成 `package/feeds/<feed>/<dir>`，被連結的那份才是實際編進映像的來源，索引因此改以 manifest 記錄的連結集判定——恰好一個候選被連結時採用它，並在證據裡記下被遮蔽的 recipe（`shadowed_recipes`，同時列入 `resolved_duplicates`）；零個或多個候選被連結時仍判為 conflict，差異留在待審佇列，不猜測贏家。

不在歸因範圍、一律仍需逐項審查：新增／移除套件、`/etc/apk/world` 根集合、檔案 mode/owner、上游**原地改檔但未升版**（例如只改 lua 不 bump `PKG_VERSION`）、以及任何本地補丁過的 recipe。索引缺漏、schema 不符或無法解析時門禁直接失敗（exit 1），不會靜默退回「全部批准」。下載 `PKG_HASH`、簽章、Go/x509 門檻與 source pin 比對全部照舊，未新增任何跳過或 `continue-on-error`。


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

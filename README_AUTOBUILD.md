# S20P 每日自動編譯

本分支基於 dqsq2e2 的 `26.10.17_15.32.58_S20P-WIFI-YES`，固定原 wrapper `d46704afb327fce99e7d770c08fc46b48d5bb1b7` 的流程，以及 chasey `03eb30617955519e35845838c6255ebe687eb717`／Linux 6.12.112。原作者 README、全部 patches、Docker nftables 修補、Honk/eBPF/BTF、MTK Wi-Fi 7.6.7.3、HNAT/WARP/eQoS/TurboACC 配置保留。

原作者沒有保留完整 feeds、動態資產和環境 lock，因此無法承諾與原 ROM 位元一致。`ci/sources.lock.json` 記錄本次重新解析的固定來源，不能冒充原10/17完整輸入。新 kernel ABI、build fingerprint 及 repository signing key 正常重建，絕不冒用舊 hash。

## 觸發方式

- `.github/workflows/s20p-daily.yml`：每日 **04:20 Asia/Shanghai（20:20 UTC）**，另支援 Actions → Run workflow。
- 排程只從 repository 的 default branch 執行；GitHub 可能延遲排程，公共庫長期無活動可能停用排程，請留意 Actions 狀態。
- 編譯不會自動刷機。原 watcher 和其他排程移至 `docs/upstream-workflows/` 保留，不再默默追蹤上游 HEAD。
- 新 checkout 執行 `bash ci/build.sh`。完整建置需 Linux／Ubuntu 24.04 和至少50GiB可用空間；runner環境安裝由已審查的 `ci/setup-runner.sh` 處理，不執行遠端 root 初始化腳本。

## 原功能與新增內容

配置疊加順序維持原四層，再追加本任務的增量：

1. `S20P-WIFI-YES.config`
2. `compile_base.config`
3. `docker_deps.config`
4. `honk.config`
5. `migration.config`：HiLink CDC Ethernet、ipset、ArkBridge Python3 base/urllib/openssl。
6. `mobile_usb.config`：RNDIS、NCM、Huawei NCM、QMI、MBIM、AT/ACM、USB模式切換，以及 Realtek RTL8156 系列／ASIX／AQC111／Microchip 等 USB Ethernet 驅動，新增項目選 `m`，編成同批可選套件。

詳細可選清單見 [ci/mobile-usb-packages.txt](ci/mobile-usb-packages.txt)，各裝置證據與限制見 [docs/mobile-usb.md](docs/mobile-usb.md)。同批編好不代表所有型號都已實物驗收。介面、APN、PIN、模式及供電仍要按實物確認；本流程不修改模組韌體、不自動切模式或啟動撥號服務。

不包含私人 ArkBridge 設定、ShellCrash 訂閱、AdGuardHome 設定、Tailscale identity、factory、GPT或 SSH 私鑰。這些由後續私密遷移程序恢復。

## 自動檢查與產物

`ci/baseline/` 僅包含原廠公開 ROM 衍生的檔案清單、hash、DTB、套件資料和原四層配置，無設備的私密 overlay。

- `.config` 檢查原483包可編譯、124個原 kmod、24個 LuCI app、103個 MTK 配置、新增 root 與28個可選套件沒有被 defconfig 丟棄。
- 原始成品 SquashFS 抽出後，再驗483包實際安裝、kmod exact kernel dependency、全部4334項原路徑、critical資料與DTB硬體語義。包名齊全不足以代表全部功能等效。
- 版本、預設／資產／檔案差異須精確審核；未批准差異只保留 evidence，阻止候選發佈，不能以 allow-all 取消檢查。
- 編譯成功後、原流程清理前保存完整 `bin/`、所有 APK／已簽名 `packages.adb`、公開key、kernel config／Module.symvers、final config、buildinfo、來源與HTTP輸入紀錄、logs與SHA256。
- 使用同批 host apk 驗證 signed index，再核 APK payload 與 index identity。沿用原 `CONFIG_SIGN_EACH_PACKAGE=n` 的模型，不假裝 unsigned APK 有單獨簽章，不降低安裝信任要求。
- 全部自動檢查通過時僅發佈 **prerelease candidate**；仍需上機驗證 USB／Wi-Fi／加速／重啟及遷移，不能宣稱 stable。
- evidence artifact 保留30天；通過的候選包會連同完整same-build feed放入prerelease，避免只剩ROM而失去配套kmod。

每日預設重建固定來源。要更新上游或套件，先修改 source lock／明列差異，再跑同一檢查流程。不可把不同ABI現成kmod混入此ROM，也不可 force dependencies。

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

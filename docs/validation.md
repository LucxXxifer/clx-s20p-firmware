# 本地驗證記錄

日期：2026-10-24。範圍是建立可審查的 S20P GitHub Actions 專用建置庫，沒有修改路由器、ER01 或 Mac 網路設定。

## 已驗證

- `python3 -m unittest discover -s ci/tests -v`：110 tests，51.670s，PASS。涵蓋來源漂移／HTTP失敗、kernel patch核對、Kconfig假成功、ABI package映射、原功能保留、USB optional包、簽章／payload／image信任key、optional kmod exact ABI、錯板型／超分區容量、私鑰攔截及失敗不發布。
- `bash ci/tests/test_network.sh`：PASS，Git rc23不會誤報成功。
- `bash -n`：CI scripts、原build entry及變更的shell modules通過。
- actionlint v1.7.7：每日workflow通過；actions均固定commit SHA。
- ShellCheck warning檢查：CI/network/repo/feeds/verify/target修補通過。原 `package_source_updates.sh` 保留4類既有警告；與原HEAD比較沒有新增種類，未為消除舊警告擴大改寫。
- 四個原workflow逐位元保存至 `docs/upstream-workflows/*.disabled`；只有新S20P workflow會執行。
- 原公開ROM的baseline 11份checksum全部相符；`git diff --check` 通過。新增公開內容掃描未見SSH私鑰／token／私人LAN設定；baseline只來自公開映像。
- 獨立雙向review已收斂：補齊image trustkey、全feed kmod ABI、log寫入失敗攔截、source public key hash、真packageinfo參數。

## Linux prepare smoke

隔離 Debian13 ARM64 Docker volume完成原source prepare與必要修補後，執行真 `make defconfig`、原theme/APK/Honk檢查、483原包selector／103 MTK symbols／28 USB optional檢查、selected Go host recipe解析及prepared provenance，全部通過。主source固定 `03eb30617955519e35845838c6255ebe687eb717`；kernel guard核對 `6.12.112`。選中的USB optional為 `m`，既有HiLink根為 `y`。

此測試經四輪根因修復後在同一已prepare樹接續驗證；不冒充全新Ubuntu24.04 runner端到端成功。container wrapper的Git ownership曾單獨修正並續跑provenance，沒有改全域safe.directory。完整過程另存本機隔離smoke資料夾，沒有放入公開庫的私人資料。

- 最終source lock SHA256：`03832a71f255c60c30f93dc030ba58e910da0b99f2f1dd2ff19f52251a37ac29`
- 最終prepared provenance SHA256：`f1a6699b2b80cb978b88a6f6ccfe888bf195b142d9b94dd57e0516f74a0b814e`
- 原生APK signed-index／unsigned-payload信任模型在隔離Linux工具上驗證，腐壞payload會拒絕；最終same-build host apk仍由首次完整編譯實際核對。
- 真Linux非root SquashFS抽取與獨立fakeroot process核對numeric uid/gid、char device metadata通過；沒有放寬image owner/device檢查。

## 首次雲端執行

已建立公開庫 [LucxXxifer/s20p-firmware](https://github.com/LucxXxifer/s20p-firmware)，推送 `094a21009cd98371b6893f34190401d63cefe2e3`，並於2026-10-24 08:45 Asia/Shanghai手動啟動[首次 Actions](https://github.com/LucxXxifer/s20p-firmware/actions/runs/40046525148)。Ubuntu24.04 runner 已通過 checkout 與 focused policy tests，後續完整編譯仍在執行。已從 GitHub 核對 workflow `active`、預設分支 `main` 與每日04:20 Asia/Shanghai排程。

## 尚待驗證

尚未完成完整編譯kernel/toolchain/package/image。首輪雲端結果可能因無法還原歷史feeds而進入 `review_required`；必須看實際差異，不能預先批准所有差異。

沒有新ROM成品，因此image保留／same-build feed完整信任鏈尚未在最終產物上執行。沒有開機、USB SuperSpeed／2.5G吞吐、DJI/U20實物、ArkBridge故障切換、Wi-Fi或ER01接替驗收。Git來源固定；部分HTTP動態資產只記錄hash並受recipe／image gate約束，不承諾所有輸入位元重現。

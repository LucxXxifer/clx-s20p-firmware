# CLX-S20P 刷機、升級與備份還原 Skill

`clx-s20p-provisioning` 供 Codex 協助 CLX-S20P 首次刷機，以及 OpenWrt 插件備份、升級規劃與逐項還原。它與本倉庫固件集中管理，也可用於其他 S20P 分支；每次都按來源／目標的型號、布局、套件與設定 schema 判斷，不以分支名稱保證相容。

## 流程

| 階段 | Skill 的工作 |
| --- | --- |
| 識別及連線 | 核對機身 SN、板型、eMMC CID／容量；透過 Telnet 開啟 SSH，已有 SSH 時沿用 |
| eMMC 健康 | 在長時間備份及刷寫前讀取 `life_time`、`pre_eol_info` 與儲存錯誤；異常時停止刷寫，仍可搶救備份 |
| 逐機原廠備份 | 保存 boot0／boot1、p1–p5、原始 GPT／保留區及 p6 檔案／設定；manifest 綁定本機 CID，支援必要項目的續傳與校驗 |
| 元件及網口檢查 | 讀取記憶體、溫度、核心錯誤與介面計數；七個實體網口的速度／流量測試需要接線與對端設備 |
| FIP U-Boot | 核對來源、SHA-256、板型、備份及健康狀態；依使用者授權寫入 FIP，獨立讀回驗證並比對受保護區域 |
| 手動交接 | 提醒斷電、按住 Reset、通電並保持 6 秒後放開；由使用者進入 U-Boot、刷 GPT 和固件 |
| GPT 核對 | 按本機真實 sector 數選檔，上傳後驗證主／副 GPT；僅在確認已知副表問題且獲授權時進行條件修復 |
| OpenWrt 備份辨識 | 區分 LuCI 設定、插件程式、持久資料／身分、手動整合與執行參考；核對實際清單、檔案與備份覆蓋，尊重排除項目 |
| 升級／還原引導 | 比對來源與目標分支，判斷保留設定或乾淨安裝；產生 `.md` 順序，按相依關係逐項安裝、還原、驗收 |

每臺機器都需要保存自己的 factory／boot 資料，其他機器的備份不能代替。本流程預設不克隆 100+ GB 的 p6 原始分區；如需完整原始映像，需另行明確選用。GPT／固件上傳及 BL2 寫入不會由預設 FIP 操作自動執行。

首次刷機和 OpenWrt 應用還原是不同模式。一般系統升級不會要求重刷有效的 U-Boot／GPT，也不會因執行備份而自動升級系統。

## 安裝

1. [下載 clx-s20p-provisioning.skill](https://github.com/LucxXxifer/clx-s20p-firmware/raw/refs/heads/main/skills/clx-s20p-provisioning.skill)。這是 ZIP 格式；解壓縮後取得 `clx-s20p-provisioning/`。解壓工具需要時，可將下載檔的副檔名改為 `.zip`。
2. 將完整資料夾放入 `~/.agents/skills/`，確認存在 `~/.agents/skills/clx-s20p-provisioning/SKILL.md`。已有版本時，先將舊資料夾備份到 skills 目錄以外，再更新。
3. 在 Codex 開啟新對話，使用 `$clx-s20p-provisioning`。也可取得本倉庫 [skills/clx-s20p-provisioning/](clx-s20p-provisioning/) 原始碼作相同安裝。

執行環境以 macOS 為基礎：Python 3.10+、OpenSSH，U-Boot HTTP 操作另用 `curl`。執行工具不需額外 Python 套件。其他平台需自行核對網路設定、外部備份路徑及可用工具。

## 使用

首次刷機可以向 Codex 說：

```text
使用 $clx-s20p-provisioning，處理這臺新的 CLX-S20P。
機身 SN 末四碼是 A1B2；原廠 IP 是 192.168.8.1。
先核對身份及 eMMC 健康，再備份原廠必要資料、檢查元件及網口，
最後刷入並讀回驗證我指定的 FIP U-Boot。
完成後由我手動進入 U-Boot、刷 GPT 和系統。
```

`A1B2` 只是示例，請提供實際機身 SN。原廠 IP 通常是 `192.168.8.1`，第三方 U-Boot 通常是 `192.168.1.1`，仍需核對當前設備。密碼由使用者互動輸入，不填入 skill 或公開紀錄。

只檢查壽命或續傳備份也可直接提出；這兩種用途不會自行刷寫。需要有線直連設備同時保留 Wi-Fi 上網時，skill 會先核對 macOS 的實際網路服務，再處理指定的有線連線。

備份與升級可向 Codex 說：

```text
使用 $clx-s20p-provisioning，先辨識這臺 S20P 的 OpenWrt 系統及插件。
把設定、插件程式與持久資料分別備份到我指定的外部目錄，核對完整性。
對照我指定的目標韌體判斷相容性，生成刷機／安裝插件／還原資料的 .md 順序。
暫時只做備份和規劃，不刷機或重啟。
```

已有備份並已刷好系統時，可直接要求「辨識此備份與現機相容性，再逐項安裝及還原」。skill 會先核對新系統，已正確保留的檔案不重複覆蓋。APK／opkg、核心 ABI 或設定 schema 不同時，使用目標分支套件與選擇性轉換，不整包套回舊系統。

LuCI 備份可包含插件設定／資料，**不會重裝缺少的插件程式**。另存的程式／資料救援包不是 LuCI 備份或通用安裝包，不能直接覆蓋 `/`。若預設路由或 DNS 由插件提供，升級前必須準備相容安裝材料與本地救援通道。詳細辨識、分支判斷及順序見 [OpenWrt 備份與還原流程](clx-s20p-provisioning/references/openwrt-backup-restore.md)。

原 Mac 的外部備份根目錄預設為 `/Volumes/Storage/CLX-S20P/0.1 原厂备份`；其他環境可在備份工具使用 `--destination-root` 指向已存在的外部目錄。新設備子目錄命名為 `clx-s20-<SN末四碼大寫>`，例如 `clx-s20-A1B2`。舊目錄會保留，同名但 CID 不同時停止合併。

以上根目錄只放每臺機器的原廠救援資料；OpenWrt 插件備份另用使用者指定的外部目錄及時間戳記，避免混淆兩種恢復用途。

FIP、GPT 及固件需另外取得，skill 安裝包不附帶映像。兩種標稱 128 GB 的 eMMC 可能有不同 sector 數，不能共用未核對容量的 GPT。參考映像的名稱與雜湊見[已核對映像示例](clx-s20p-provisioning/references/local-assets-2026-10.md)；實際操作仍需檢查所選 release 的板型、格式及分區相容性。

## 驗證範圍與限制

此版通過 42 項離線測試，涵蓋 synthetic GPT／FIP／備份資料與模擬 SSH／console 行為，並通過 skill 結構、Python 3.10 語法與安裝包內容核對。測試不連接設備、不執行刷寫。

實際使用經驗已納入備份、網路切換及已知副 GPT 修復指引；新版工具尚未完成整套實機刷機及原廠還原測試。檔案與雜湊驗證不等於已驗證可以還原；p6 檔案備份也不包含完整檔案系統中繼資料、已刪除／空白空間。還原需按該機原始分區、檔案系統及 UUID／掛載條件規劃。

OpenWrt 應用備份／還原為 agent 按實機證據執行的指引，沒有宣稱所有分支已實測或提供通用一鍵還原器。備份完整性、相容性審核、插件還原、聯網／備援與重啟持久性各自記錄；未測項目維持 `not-run`。Tailscale 等裝置身分只在同一臺機器還原，其他 S20P 僅選取可攜設定。

七個實體網口、M.2、吞吐量與原廠還原，只有實際測過才會標記通過。遇到未知 eMMC 健康值、儲存錯誤、備份不完整、身份／映像不明或寫入結果不確定時，會停在相應階段並保留證據。

維護者可在不接設備的情況下執行：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s skills/clx-s20p-provisioning/tests -v
```

公開內容僅包括 skill、工具、示例及離線測試，不包含個別設備備份、真實 SN／CID、密碼、私鑰或操作紀錄。Skill 採用與本倉庫相同的 [GPL-3.0 授權](../LICENSE)。

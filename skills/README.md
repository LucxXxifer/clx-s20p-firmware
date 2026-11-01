# CLX-S20P 刷機、升級與備份還原 Skill

`clx-s20p-provisioning` 供**任意具備所需本機工具與網路權限的 Agent 使用**，協助 CLX-S20P 首次刷機，以及 OpenWrt 插件備份、升級規劃與逐項還原。它與本倉庫固件集中管理，也可用於其他 S20P 分支；每次都按來源／目標的型號、布局、套件與設定 schema 判斷，不以分支名稱保證相容。

Skill 採 [Agent Skills](https://agentskills.io/specification) 的 `SKILL.md`、`references/` 與 `scripts/` 結構，只要能讀取完整 Skill、執行本機命令並存取目標區域網路，便可使用下方同一個通用範例，自行測試。

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
2. 保留完整資料夾，**不可只複製 `SKILL.md`**；工具與參考文件也必須可讀取。已有版本時，先將舊資料夾備份到 skills 目錄以外，再更新。也可取得本倉庫 [skills/clx-s20p-provisioning/](clx-s20p-provisioning/) 原始碼作相同安裝。
3. 支援原生 Skill 的宿主，將資料夾放入其指定目錄，再依該宿主方式重新載入；不支援或不確定時，放在可讀取的一般目錄，直接使用下方通用範例指定 `SKILL.md` 的絕對路徑，不必轉成專用插件。

原生目錄例：Codex 使用 `~/.agents/skills/`；[Claude Code](https://code.claude.com/docs/en/skills) 使用 `~/.claude/skills/`；[Gemini CLI](https://geminicli.com/docs/cli/skills/) 支援 `~/.agents/skills/` 或 `~/.gemini/skills/`；[Cursor](https://cursor.com/docs/context/skills) 支援 `~/.agents/skills/` 或 `~/.cursor/skills/`。其他宿主依其文件配置；自動發現與 `$技能名`、`/技能名` 等快捷語法由宿主決定，不是本 Skill 的必要條件。`agents/openai.yaml` 僅為 Codex 可選 UI 中繼資料，其他宿主可忽略。

## 執行環境

- Agent 需要檔案讀寫、終端命令及目標區域網路存取能力；SSH 認證／control socket 必須在實際執行主機可用。只有瀏覽器登入或 GUI SSH 分頁，不代表腳本已取得認證。
- 主機需 Python 3.10+、OpenSSH，U-Boot HTTP 操作另用 `curl`。腳本只用 Python 標準函式庫，但備份及 FIP 工具使用 Unix `fcntl` 鎖定，Windows 原生不支援這些工具。
- 網路操作指引以 macOS 為基礎；Linux／WSL 需先核對等價網路設定、設備可達性、SSH socket、外部備份路徑與可用工具，未完成全流程驗收。不可直接套用 macOS 的 `networksetup` 或路由命令。
- 純雲端聊天或隔離沙箱若無法連到家中 S20P，只能閱讀、分析及規劃；需改用可存取設備的執行主機。安裝 Skill 本身不會新增工具或繞過宿主權限。

核心不需為各家 Agent 分別重寫；通常只需調整 Skill 載入位置、工具對應與主機網路／備份路徑。若要支援 Windows 原生，才需另行修改及驗證鎖定、SSH 與網路操作，不能只更換安裝目錄。

方式 A：telnet 直接進（原廠默認開啟，無需任何配置）

telnet 192.168.8.1

賬號 root，密碼 tty996icu

方式 B：开 SSH（可選）

uci set dropbear.@dropbear[0].enable='1'

uci commit dropbear/etc/init.d/dropbear restart



## 一個跨 Agent 通用範例

將路徑、IP、SN 末四碼與目標換成自己的實際資料後，貼到上述任一具備所需能力的 Agent：

```text
請讀取並遵循 /absolute/path/to/clx-s20p-provisioning/SKILL.md。
references/ 與 scripts/ 均相對於該 Skill 目錄，請使用你現有的檔案、終端與網路工具。
本次目標：辨識這臺 CLX-S20P 的系統、檢查 eMMC 健康，
備份相應的必要資料，並產生下一階段操作指南。
目標 IP：192.168.8.1；機身 SN 末四碼：A1B2。
備份到已存在的外部目錄：/absolute/external-backup-root。
先確認工具、路由、設備身分與備份空間，依現有系統選擇原廠或 OpenWrt 備份流程。
本次只授權讀取、檢查和備份，不刷寫、不重啟。
需要密碼、移線、斷電或按 Reset 時交由我操作；未驗證的結果標為未測試。
```

範例中的路徑與 `A1B2` 都是佔位值，執行前必須替換。原廠 IP 通常是 `192.168.8.1`，第三方 U-Boot 通常是 `192.168.1.1`，仍需核對當前設備。密碼由使用者互動輸入，不填入 Skill 或公開紀錄。

首次刷機、只查壽命、續傳備份、升級規劃或已授權的逐項還原，都可沿用此範例，修改目標與授權範圍；備份不會自行授權刷寫。需要有線直連設備同時保留 Wi-Fi 上網時，Agent 會先核對實際主機的介面與路由，再處理指定的有線連線。

## 備份、升級與還原說明

已有備份並已刷好系統時，可直接要求「辨識此備份與現機相容性，再逐項安裝及還原」。skill 會先核對新系統，已正確保留的檔案不重複覆蓋。APK／opkg、核心 ABI 或設定 schema 不同時，使用目標分支套件與選擇性轉換，不整包套回舊系統。

LuCI 備份可包含插件設定／資料，**不會重裝缺少的插件程式**。另存的程式／資料救援包不是 LuCI 備份或通用安裝包，不能直接覆蓋 `/`。若預設路由或 DNS 由插件提供，升級前必須準備相容安裝材料與本地救援通道。詳細辨識、分支判斷及順序見 [OpenWrt 備份與還原流程](clx-s20p-provisioning/references/openwrt-backup-restore.md)。

原 Mac 的外部備份根目錄預設為 `/Volumes/Storage/CLX-S20P/0.1 原厂备份`；其他環境可在備份工具使用 `--destination-root` 指向已存在的外部目錄。新設備子目錄命名為 `clx-s20-<SN末四碼大寫>`，例如 `clx-s20-A1B2`。舊目錄會保留，同名但 CID 不同時停止合併。

以上根目錄只放每臺機器的原廠救援資料；OpenWrt 插件備份另用使用者指定的外部目錄及時間戳記，避免混淆兩種恢復用途。

FIP、GPT 及固件需另外取得，skill 安裝包不附帶映像。兩種標稱 128 GB 的 eMMC 可能有不同 sector 數，不能共用未核對容量的 GPT。參考映像的名稱與雜湊見[已核對映像示例](clx-s20p-provisioning/references/local-assets-2026-10.md)；實際操作仍需檢查所選 release 的板型、格式及分區相容性。

## 驗證範圍與限制

此版通過 42 項離線測試，涵蓋 synthetic GPT／FIP／備份資料與模擬 SSH／console 行為，並通過 Skill 結構、Python 3.10 語法與安裝包內容核對。測試不連接設備、不執行刷寫，也不代表各家 Agent 或各作業系統均已完成實機驗收。

實際使用經驗已納入備份、網路切換及已知副 GPT 修復指引；新版工具尚未完成整套實機刷機及原廠還原測試。檔案與雜湊驗證不等於已驗證可以還原；p6 檔案備份也不包含完整檔案系統中繼資料、已刪除／空白空間。還原需按該機原始分區、檔案系統及 UUID／掛載條件規劃。

OpenWrt 應用備份／還原為 agent 按實機證據執行的指引，沒有宣稱所有分支已實測或提供通用一鍵還原器。備份完整性、相容性審核、插件還原、聯網／備援與重啟持久性各自記錄；未測項目維持 `not-run`。Tailscale 等裝置身分只在同一臺機器還原，其他 S20P 僅選取可攜設定。

七個實體網口、M.2、吞吐量與原廠還原，只有實際測過才會標記通過。遇到未知 eMMC 健康值、儲存錯誤、備份不完整、身份／映像不明或寫入結果不確定時，會停在相應階段並保留證據。

維護者可在不接設備的情況下執行：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s skills/clx-s20p-provisioning/tests -v
```

公開內容僅包括 skill、工具、示例及離線測試，不包含個別設備備份、真實 SN／CID、密碼、私鑰或操作紀錄。Skill 採用與本倉庫相同的 [GPL-3.0 授權](../LICENSE)。

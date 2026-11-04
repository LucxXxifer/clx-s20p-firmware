# Release Changelog Implementation Plan

> **For agentic workers:** Execute this approved plan inline with test-driven-development and verification-before-completion. No parallel implementation is needed.

**Goal:** 每日新候選自動附上相對上一成功發布候選、可核來源的繁體中文更新日誌。

**Architecture:** 純 Python inventory reader 從既有同批證據提取公開欄位；release verifier 綁定 inventory。獨立 generator 透過 gh API 確認前一候選並取得 checksum-verified inventory，輸出 Markdown；workflow 在既有驗證之後、release create 之前附加。

**Tech Stack:** Python stdlib/unittest、GitHub REST via gh、現有 Actions publish job。

### 1. 證據 inventory

- [x] 新增 ci/tests/test_release_changelog.py 的套件／來源／補丁／安全證據及不可信欄位測試，執行確認缺少實作而失敗。
- [x] 新增 ci/release_inventory.py，限定 JSON／字串大小與公開欄位；ci/release_candidate.py prepare/verify 精確綁定 inventory。
- [x] 執行專項與既有 release_candidate 測試。

### 2. 基準與 Markdown

- [x] 測試 published_at 排序、分頁、成功 attempt／tag／asset digest/checksum、舊 archive reader、無基準、compare 截斷及 API 失敗。
- [x] 新增 ci/release_changelog.py：有界 API、SHA256 驗證、精簡 Markdown 與 upstream compare 證據，不將 commit message 當修復驗證。
- [x] 使用保存的真實候選資料演練；不發布或修改既有 release。

### 3. Workflow 與部署

- [x] 新增 executed-shell 接線測試，確保 generator 失敗阻擋 release create 且仍恰為六個資產。
- [x] 修改 .github/workflows/s20p-daily.yml 與 README_AUTOBUILD.md；維持原檢查和排程。
- [x] 執行 Python 全套、shell/network、frontend 與 actionlint，檢視完整 diff。Lua 未修改，本機沒有 Lua5.1，維持雲端既有檢查。

驗證結果：Python 406/406、frontend 9/9、network failure propagation、shell syntax 與 actionlint 通過。獨立審查的兩項問題已修正：基準只排除當次 run/attempt；可選上游 compare 的 API／JSON／形狀錯誤明示 unavailable，不影響基準驗證的阻擋行為。

交付檢查：提交後以無 force 的精確 fast-forward 更新 main；API 核實程式、workflow active、原六資產與指定安裝版本未變，不派發新 build。實際部署結果與後續限制記錄於私人 outputs 報告；首個真實雲端發布仍需由排程完成。

# 每日候選更新日誌

使用者已批准：每個新候選相對前一個成功發布候選產生繁體中文更新日誌，附在 Release 說明。比較實際套件／Linux ABI、公開來源 SHA、wrapper 補丁及安全報告，區分上游提交宣稱、同批產物證據與未執行的硬體驗收。

基準以 published_at 排序，僅接受本 workflow 成功 attempt、tag 精確 commit、API SHA256 和 release checksum 相符的候選；不把較新失敗／未發布 run 或固定的 Latest 當基準。找不到基準時明示首次記錄；下載或身份驗證失敗則停止發布。GitHub compare 缺失／截斷只限制上游說明，不偽稱沒有更新。

在既有 BUILD-VERIFICATION.json 加入由同批公開證據產生的精簡 inventory，重新驗證及 checksum；維持六個資產。舊候選無 inventory 時，以 API／外層 checksum 驗證原 archive，再用既有安全 reader 核內層 checksum，重讀 inventory，不以新政策重審舊來源。只輸出明確允許的公開版本、路徑、hash 與報告欄位，不讀私人裝置資料。

部署僅更新 main 的日誌程式、測試、文件及 publish 接線；維持原 schedule、source/security/preservation gates，不合併 draft PR，不派發 firmware build，不改歷史 release/tag 或已指定安裝映像。

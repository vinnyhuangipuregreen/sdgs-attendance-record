# 課程登錄

BLIA SDGs 社會影響力系統的課程、簽到、請假與補課模組。

- GitHub： https://github.com/vinnyhuangipuregreen/sdgs-attendance-record
- 正式入口： https://sdgs-attendance-record.vercel.app/
- 資料庫：Turso `sdgs-attendance-record`，與練習單、配對賽分開。

在 Vercel 設定 `TURSO_DATABASE_URL`、`TURSO_AUTH_TOKEN`、獨立隨機 `SECRET_KEY`，再發布 `main`。請勿提交 Token 或密碼設定；`.env.example` 僅包含空白範本。`/healthz` 會回報資料庫名稱與儲存類型。

原有帳號、課程與出席資料模型完整儲存在 `attendance_state` 表的 JSON 欄位，保留原程式各欄位與匯入格式。初始化僅在無資料時建立原有初始資料，不會覆蓋已儲存的內容；版本檢查避免同時寫入時覆蓋較新的資料，衝突回覆 409。

本機可使用 `DATABASE_PATH` 指向測試 SQLite；Vercel 缺少 Turso 設定時拒絕啟動，避免誤用暫存資料庫。

驗證：`python -m unittest discover -s tests -v`。測試使用臨時資料庫，不更動正式資料。使用者現有 uploadfile 檔案未自動匯入、搬移或覆蓋。

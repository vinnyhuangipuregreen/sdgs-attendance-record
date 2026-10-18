# 課程登錄

BLIA SDGs 社會影響力系統的課程、簽到、請假與補課模組。

- GitHub： https://github.com/vinnyhuangipuregreen/sdgs-attendance-record
- 正式入口： https://sdgs-attendance-record.vercel.app/
- 資料庫：Turso `sdgs-attendance-record`，與練習單、配對賽分開。

在 Vercel 設定 `TURSO_DATABASE_URL`、`TURSO_AUTH_TOKEN`、獨立隨機 `SECRET_KEY`，再發布 `main`。請勿提交 Token 或密碼設定；`.env.example` 僅包含空白範本。`/healthz` 會回報資料庫名稱與儲存類型。

原有帳號、課程與出席資料模型完整儲存在 `attendance_state` 表的 JSON 欄位，保留原程式各欄位與匯入格式。初始化僅在無資料時建立原有初始資料，不會覆蓋已儲存的內容；版本檢查避免同時寫入時覆蓋較新的資料，衝突回覆 409。

本機可使用 `DATABASE_PATH` 指向測試 SQLite；Vercel 缺少 Turso 設定時拒絕啟動，避免誤用暫存資料庫。

驗證：`python -m unittest discover -s tests -v`。測試使用臨時資料庫，不更動正式資料。使用者現有 uploadfile 檔案未自動匯入、搬移或覆蓋。

## 系統單一登入

課程入口會透過主平台登入，以一次性 PKCE 授權碼建立本模組 HttpOnly 工作階段；已登入主平台者不需重複輸入帳密。每次驗證均向主平台確認帳號與權限；舊的課程獨立密碼與 token/admin_token Cookie 不再作為登入依據。帳號權限集中在 sdgs-role-permission，課程與出席資料仍在本資料庫。

platform_key 使用中央帳號類型與 ID，既有唯一 Email 對應的課程學員會保留本地 ID，維持簽到紀錄關聯；同 Email 的不同中央帳號不會合併。

## 課程出席規則

學員操作由後端以台灣時間（UTC+8）檢核：簽到只允許課程日期的起訖時間內（包含起訖瞬間），請假與補課不得超過結束時間。請假必須填事由，補課必須先請假；已送出的簽到、請假及補課不可由學員覆寫或重複送出。

紀錄保存 `signed_at`、`leave_at`、`makeup_date`。補課保留原請假事由與時間，前台分兩列顯示，後台請假／補課清單與人數也同時包含這兩項紀錄。舊請假紀錄以原 `updated_at` 顯示，不虛構缺失的歷史時間。課程結束後未登記的學員，由前後台使用同一規則顯示「未出席」，不額外製造操作紀錄。

後台補課頁直接讀取伺服器資料，匯入及刪除也必須由後端儲存成功；不再使用瀏覽器 localStorage 作為出席資料來源。管理員刪除補課時，原有請假紀錄仍保留。

規則與畫面測試：`python -m unittest discover -s tests`、`node --test tests/*.cjs`。

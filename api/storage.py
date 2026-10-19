"""Persist the existing course data model, with optimistic concurrency control."""
import json
import os
import sqlite3
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

DATABASE_NAME = "sdgs-attendance-record"


class ConcurrentUpdate(Exception):
    pass


class AttendanceStore:
    def __init__(self):
        self.url = os.environ.get("TURSO_DATABASE_URL", "").strip()
        self.token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
        if bool(self.url) != bool(self.token):
            raise RuntimeError("請同時設定課程資料庫的 TURSO_DATABASE_URL 與 TURSO_AUTH_TOKEN")
        if os.environ.get("VERCEL") and not self.url:
            raise RuntimeError("正式網站必須連接 sdgs-attendance-record，不能使用暫存資料庫")
        if self.url:
            self.url = self.url.replace("libsql://", "https://", 1).rstrip("/")
            parsed = urlsplit(self.url)
            if parsed.scheme != "https" or not (parsed.hostname or "").startswith(DATABASE_NAME + "-"):
                raise RuntimeError("課程登錄僅能連接 sdgs-attendance-record 資料庫")
        self.path = Path(os.environ.get("DATABASE_PATH", Path(__file__).resolve().parent.parent / f"{DATABASE_NAME}.db"))

    def execute(self, sql, params=()):
        if not self.url:
            try:
                with closing(sqlite3.connect(self.path, timeout=20)) as connection, connection:
                    cursor = connection.execute(sql, params)
                    rows = cursor.fetchall() if cursor.description else []
                    return rows, cursor.rowcount
            except sqlite3.Error as error:
                raise RuntimeError("無法存取課程資料庫") from error
        args = [{"type": "integer", "value": str(value)} if isinstance(value, int)
                else {"type": "text", "value": value} for value in params]
        body = {"requests": [{"type": "execute", "stmt": {"sql": sql, "args": args}}, {"type": "close"}]}
        request = urllib.request.Request(
            self.url + "/v2/pipeline", data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.load(response)["results"][0]
            if result["type"] == "error":
                raise RuntimeError("課程資料庫查詢失敗")
            result = result["response"]["result"]
            rows = [[cell.get("value") for cell in row] for row in result.get("rows", [])]
            return rows, result.get("affected_row_count", 0)
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as error:
            raise RuntimeError("無法連接課程資料庫") from error

    def initialize(self, seed):
        self.execute("CREATE TABLE IF NOT EXISTS attendance_state (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, payload TEXT NOT NULL)")
        # Existing data is authoritative, including intentionally emptied lists.
        self.execute("INSERT OR IGNORE INTO attendance_state (id, version, payload) VALUES (1, 0, ?)",
                     (json.dumps(seed, ensure_ascii=False),))
        self.execute("CREATE TABLE IF NOT EXISTS annual_attendance_state (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, payload TEXT NOT NULL)")

    def initialize_year(self, year_id):
        if int(year_id) < 2:
            raise ValueError("新學年度編號必須大於 1")
        empty = {"admins": [], "students": [], "courses": [], "attendances": {}}
        self.execute("INSERT OR IGNORE INTO annual_attendance_state (id,version,payload) VALUES (?,0,?)", (year_id,json.dumps(empty)))

    def load(self, year_id=1):
        table = "attendance_state" if year_id == 1 else "annual_attendance_state"
        rows, _ = self.execute(f"SELECT version, payload FROM {table} WHERE id=?", (year_id,))
        if not rows:
            raise RuntimeError("此學年度課程資料尚未初始化")
        return int(rows[0][0]), json.loads(rows[0][1])

    def save(self, version, data, year_id=1):
        table = "attendance_state" if year_id == 1 else "annual_attendance_state"
        _, changed = self.execute(f"UPDATE {table} SET payload=?, version=version+1 WHERE id=? AND version=?",
                                  (json.dumps(data, ensure_ascii=False), year_id, version))
        if changed != 1:
            raise ConcurrentUpdate("資料已由其他人更新，請重新整理後再試，以免覆蓋最新資料")

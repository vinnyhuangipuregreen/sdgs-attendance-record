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
        args = [{"type": "null"} if value is None else {"type": "integer", "value": str(value)} if isinstance(value, int)
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

    def sync_student(self, profile, year_id=1):
        """Link/update one verified profile against current data, not a stale roster."""
        table = "attendance_state" if year_id == 1 else "annual_attendance_state"
        fields = {"platform_key": "student:" + str(profile["id"]), "platform_id": profile["id"],
                  "name": profile["name"], "email": profile["email"],
                  "email_pathdemy": profile.get("email_backup") or profile["email"],
                  **{key: profile.get(key, "") for key in ("phone", "unit_code", "unit_name")}}
        emails = [str(profile.get(key) or "").strip().lower() for key in ("email", "email_backup")]
        rows, _ = self.execute(f"""WITH state AS (SELECT payload FROM {table} WHERE id=?),
            students AS (SELECT j.key AS position, j.value AS item FROM state, json_each(state.payload,'$.students') j),
            legacy AS (SELECT position FROM students WHERE COALESCE(json_extract(item,'$.platform_key'),'')=''
                AND (lower(trim(json_extract(item,'$.email'))) IN (NULLIF(?,''),NULLIF(?,''))
                     OR lower(trim(json_extract(item,'$.email_pathdemy'))) IN (NULLIF(?,''),NULLIF(?,'')))),
            target AS (SELECT COALESCE(
                (SELECT position FROM students WHERE json_extract(item,'$.platform_key')=? LIMIT 1),
                (SELECT position FROM legacy WHERE (SELECT count(*) FROM legacy)=1)) AS position)
            UPDATE {table} SET payload=json_set(payload,
                CASE WHEN (SELECT position FROM target) IS NULL THEN '$.students[#]'
                     ELSE '$.students['||(SELECT position FROM target)||']' END,
                json_set(json_patch(COALESCE((SELECT item FROM students WHERE position=(SELECT position FROM target)),'{{}}'),json(?)),
                    '$.id',COALESCE((SELECT json_extract(item,'$.id') FROM students WHERE position=(SELECT position FROM target)),
                                   (SELECT COALESCE(max(CAST(json_extract(item,'$.id') AS INTEGER)),0)+1 FROM students)))),
                version=version+1 WHERE id=? RETURNING version,payload""",
            (year_id, *emails, *emails, fields["platform_key"], json.dumps(fields, ensure_ascii=False), year_id))
        if not rows:
            raise RuntimeError("此學年度課程資料尚未初始化")
        return int(rows[0][0]), json.loads(rows[0][1])

    def save_records(self, original, data, year_id=1):
        """Atomically patch changed record keys; unrelated students never conflict."""
        table = "attendance_state" if year_id == 1 else "annual_attendance_state"
        before, after = original["attendances"], data["attendances"]
        keys = [key for key in before.keys() | after.keys() if before.get(key) != after.get(key)]
        if not keys:
            return
        expression, args, conditions, guards = "payload", [], [], []
        for key in keys:
            # Keys come from validated integer student/course IDs, not request JSON paths.
            student_id, course_id = (int(part) for part in key.split("_"))
            path = f'$.attendances."{student_id}_{course_id}"'
            if key in after:
                expression = f"json_set({expression}, ?, json(?))"
                args.extend((path, json.dumps(after[key], ensure_ascii=False)))
            else:
                expression = f"json_remove({expression}, ?)"
                args.append(path)
            conditions.append("json_extract(payload, ?) IS json(?)")
            guards.extend((path, json.dumps(before[key], ensure_ascii=False) if key in before else None))
            for collection, item_id in (("students", student_id), ("courses", course_id)):
                item = next(item for item in original[collection] if int(item["id"]) == item_id)
                conditions.append(f"EXISTS (SELECT 1 FROM json_each(payload,'$.{collection}') j WHERE json_extract(j.value,'$.id')=? AND j.value=json(?))")
                guards.extend((item_id, json.dumps(item, ensure_ascii=False)))
        # Reject concurrent course/roster administration rather than applying stale rules.
        _, changed = self.execute(f"UPDATE {table} SET payload={expression}, version=version+1 WHERE id=? AND " + " AND ".join(conditions),
                                  (*args, year_id, *guards))
        if changed != 1:
            raise ConcurrentUpdate("此筆紀錄或課程資料已更新，請重新整理後確認登錄結果")

import copy
import io
import jwt
import time
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl
from fastapi.testclient import TestClient
from api import index
from api.storage import AttendanceStore, ConcurrentUpdate


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "DATABASE_PATH": str(Path(self.temp.name) / "sdgs-attendance-record.db"),
            "TURSO_DATABASE_URL": "", "TURSO_AUTH_TOKEN": "", "VERCEL": "",
        })
        self.env.start()
        self.platform_patch = patch("api.sso.platform_request", side_effect=self.platform_response)
        self.platform_patch.start()

    def tearDown(self):
        self.platform_patch.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_restart_preserves_data_and_does_not_reseed_deleted_lists(self):
        store = AttendanceStore()
        store.initialize(index.SEED_DATA)
        version, data = store.load()
        data["courses"] = []
        data["attendances"] = {}
        store.save(version, data)
        restarted = AttendanceStore()
        restarted.initialize(index.SEED_DATA)
        self.assertEqual(restarted.load()[1], data)

    def test_concurrent_changes_cannot_overwrite_newer_data(self):
        store = AttendanceStore()
        store.initialize(index.SEED_DATA)
        version, data = store.load()
        stale = copy.deepcopy(data)
        data["courses"][0]["title"] = "最新課程"
        store.save(version, data)
        with self.assertRaises(ConcurrentUpdate):
            store.save(version, stale)
        self.assertEqual(store.load()[1]["courses"][0]["title"], "最新課程")

    def test_production_requires_credentials_for_the_dedicated_database(self):
        for values in ({"VERCEL": "1"}, {"TURSO_DATABASE_URL": "libsql://other.turso.io"},
                       {"TURSO_DATABASE_URL": "libsql://sdgs-action-platform-example.turso.io", "TURSO_AUTH_TOKEN": "fake"}):
            with self.subTest(values=list(values)), patch.dict(os.environ, values):
                with self.assertRaises(RuntimeError):
                    AttendanceStore()

    def platform_response(self, path, token=None, payload=None):
        if path == "/api/auth/sso/roster?include_profile=true":
            return {"user":self.platform_response("/api/auth/me", token), "students":[]}
        if path == "/api/auth/sso/roster": return []
        if token == "test-admin":
            return {"id":1,"name":"Admin","email":"admin@test.invalid","email_backup":"","phone":"0901","role":"admin","account_type":"staff"}
        return {"id":2,"name":"測試學員","email":"student@example.invalid","email_backup":"makeup@example.invalid","phone":"0900000000","role":"student","account_type":"student"}

    def login_admin(self, client):
        self.set_session(client, "test-admin")
        client.headers["X-Platform-Embedded"] = "1"
        self.assertEqual(client.get("/admin").status_code, 200)

    def set_session(self, client, token="test-student"):
        session = jwt.encode({"access_token":token,"exp":int(time.time())+300}, index.SECRET_KEY, algorithm="HS256")
        client.cookies.set("platform_session",session)

    def test_course_crud_survives_application_restart(self):
        with TestClient(index.app) as client:
            self.login_admin(client)
            response = client.post("/api/admin/courses", json={"title": "永久儲存測試", "course_date": "2026/10/17", "course_time": "10:00-11:00", "open_time": "2026-10-17 10:00:00"})
            self.assertEqual(response.status_code, 200)
            course_id = response.json()["course"]["id"]
        with TestClient(index.app) as client:
            self.login_admin(client)
            self.assertIn("永久儲存測試", client.get("/admin").text)
            self.assertEqual(client.put(f"/api/admin/courses/{course_id}", json={"title": "已更新課程"}).status_code, 200)
            self.assertEqual(client.delete(f"/api/admin/courses/{course_id}").status_code, 200)
            self.assertNotIn("已更新課程", client.get("/admin").text)

    def test_recorded_courses_cannot_be_deleted_and_counts_match_the_list(self):
        with TestClient(index.app) as client:
            self.login_admin(client)
            for status, label in (("SIGNED_IN", "簽到"), ("ON_LEAVE", "請假"), ("MAKEUP_DONE", "補課")):
                version, data = index.store.load()
                data["attendances"] = {"1_1":{"status":status}}
                index.store.save(version, data)
                before = index.store.load()
                response = client.delete("/api/admin/courses/1")
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["detail"], f"因為有以下紀錄，故不可刪除該筆資料：{label}：1筆。")
                self.assertEqual(index.store.load(), before)
                page = client.get("/admin").text
                for heading in ("簽到人數", "請假人數", "補課人數"):
                    self.assertIn(heading, page)
            version, data = index.store.load()
            data["attendances"] = {"1_1":{"status":"SIGNED_IN"},"2_1":{"status":"SIGNED_IN"},"3_1":{"status":"MAKEUP_DONE"}}
            index.store.save(version, data)
            response = client.delete("/api/admin/courses/1")
            self.assertEqual(response.json()["detail"], "因為有以下紀錄，故不可刪除該筆資料：簽到：2筆、補課：1筆。")

    def test_student_import_and_attendance_changes_are_persistent(self):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(["學員姓名", "Email", "Email(補課平台)", "聯絡電話"])
        sheet.append(["測試學員", "student@example.invalid", "makeup@example.invalid", "0900000000"])
        stream = io.BytesIO()
        workbook.save(stream)
        with patch.object(index, "UPLOAD_DIR", Path(self.temp.name)), TestClient(index.app) as client:
            self.login_admin(client)
            result = client.post("/api/admin/students/import", files={"file": ("students.xlsx", stream.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
            self.assertEqual(result.status_code, 409)
            self.set_session(client)
            for action, status in [("checkin", "SIGNED_IN"), ("leave", "ON_LEAVE"), ("makeup", "MAKEUP_DONE")]:
                response = client.post("/api/attendance/" + action, json={"course_id": 1, "reason": "測試請假"})
                self.assertEqual(response.status_code, 200)
                data = AttendanceStore().load()[1]
                student = next(s for s in data["students"] if s["email"] == "student@example.invalid")
                self.assertEqual(data["attendances"][f"{student['id']}_1"]["status"], status)
        with patch.object(index, "UPLOAD_DIR", Path(self.temp.name)), TestClient(index.app) as client:
            self.set_session(client)
            self.assertIn("測試學員", client.get("/dashboard").text)

    def test_unauthorized_writes_do_not_change_saved_data(self):
        with TestClient(index.app) as client:
            before = AttendanceStore().load()
            self.assertEqual(client.post("/api/admin/courses", json={"title": "拒絕寫入"}).status_code, 401)
            self.assertEqual(AttendanceStore().load(), before)
            self.assertEqual(client.get("/healthz").json(), {"status": "ok", "database": "sdgs-attendance-record", "storage": "sqlite"})

    def test_save_conflict_returns_409_without_success_cookies(self):
        with TestClient(index.app) as client:
            self.login_admin(client)
            with patch.object(index.store, "save", side_effect=ConcurrentUpdate("請重新整理")):
                response = client.post("/api/admin/courses", json={"title": "衝突測試"})
            self.assertEqual(response.status_code, 409)
            self.assertNotIn("衝突測試", str(AttendanceStore().load()[1]))

    def test_student_token_cannot_be_used_as_an_admin_token(self):
        with TestClient(index.app) as client:
            self.set_session(client)
            client.cookies.set("admin_token", "old-independent-login")
            self.assertEqual(client.post("/api/admin/courses", json={"title": "拒絕越權"}).status_code, 401)
            self.assertEqual(client.post("/api/auth/login",data={"email":"local@test.invalid","phone":"0900"}).status_code,410)

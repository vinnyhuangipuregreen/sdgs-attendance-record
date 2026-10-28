import copy
import unittest
from fastapi import HTTPException
from api import index
from api.record_import import ImportedRecord, import_records
from tests import test_sso as fixtures


class RecordImportTests(unittest.TestCase):
    setUp = fixtures.CourseSsoTests.setUp
    tearDown = fixtures.CourseSsoTests.tearDown
    response = fixtures.CourseSsoTests.response

    def login_admin(self):
        self.profile.update(account_type="staff", role="admin", academic_year={"id": 1, "year": 2026})
        self.client.headers["Authorization"] = "Bearer verified-admin"

    def item(self, **changes):
        return {"student_id": 1, "course_id": 1, "course_date": "2026-09-15",
                "signed_at": "2026-09-15 00:00:00", "makeup_date": "2026-09-15 00:00:00",
                "source": "attendance.xlsx!20260915 rows 2,3", **changes}

    def test_import_retains_both_events_and_survives_reload_without_inventing_leave(self):
        self.login_admin()
        before = self.client.get("/api/admin/attendance/export").json()
        self.assertNotIn("admins", before)
        version, data = index.store.load()
        data["attendances"] = {}
        index.store.save(version, data)
        before = self.client.get("/api/admin/attendance/export").json()
        result = self.client.post("/api/admin/attendance/import", json={"version": before["version"], "records": [self.item()]})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["course_counts"]["1"], {"SIGNED_IN": 1, "ON_LEAVE": 0, "MAKEUP_DONE": 1})
        saved = self.client.get("/api/admin/attendance/export").json()
        self.assertEqual(saved["attendances"]["1_1"]["signed_at"], "2026-09-15 00:00:00")
        self.assertNotIn("leave_at", saved["attendances"]["1_1"])
        self.assertEqual(saved["courses"], before["courses"])
        self.assertEqual(saved["students"], before["students"])
        self.assertEqual(self.client.post("/api/admin/attendance/import", json={"version": saved["version"], "records": [self.item()]}).status_code, 200)
        self.assertEqual(index.store.load()[0], saved["version"])
        self.assertEqual(self.client.post("/api/admin/attendance/import", json={"version": before["version"], "records": [self.item()]}).status_code, 409)

    def test_leave_import_uses_course_date_and_preserves_signed_and_makeup_events(self):
        self.login_admin()
        for previous in ({}, {"status": "SIGNED_IN", "signed_at": "2026-09-15 18:00:00"},
                         {"status": "MAKEUP_DONE", "makeup_date": "2026-09-20 12:00:00", "note": "original"}):
            version, data = index.store.load()
            data["attendances"]["1_1"] = previous
            index.store.save(version, data)
            version, _ = index.store.load()
            record = self.item(signed_at="", makeup_date="", leave_at="2026-09-15")
            response = self.client.post("/api/admin/attendance/import", json={"version": version, "records": [record]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["count"], 1)
            saved = index.store.load()[1]["attendances"]["1_1"]
            self.assertEqual(saved["leave_at"], "2026-09-15")
            self.assertEqual(saved["reason"], "系統匯入")
            for key, value in previous.items():
                if key != "status": self.assertEqual(saved[key], value)
            self.assertEqual(saved["status"], "MAKEUP_DONE" if previous.get("makeup_date") else "ON_LEAVE")
            data = index.store.load()[1]
            course = next(c for c in data["courses"] if c["id"] == 1)
            visible = next(r for r in index.course_leave_records(data, course) if r["student_id"] == 1)
            self.assertEqual(visible["reason"], "系統匯入")
            self.assertEqual(visible["updated_at"], "2026-09-15")

    def test_existing_leave_is_skipped_without_overwriting_any_fields(self):
        self.login_admin()
        for previous in ({"status": "ON_LEAVE", "reason": "original", "updated_at": "2026-09-14"},
                         {"status": "MAKEUP_DONE", "leave_at": "2026-09-13", "reason": "original"}):
            version, data = index.store.load()
            data["attendances"]["1_1"] = previous
            index.store.save(version, data)
            before = index.store.load()
            response = self.client.post("/api/admin/attendance/import", json={"version": before[0],
                "records": [self.item(signed_at="", makeup_date="", leave_at="2026-09-15")]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["count"], 0)
            self.assertEqual(index.store.load(), before)

    def test_leave_import_rejects_invalid_date_and_mixed_event_payload_atomically(self):
        self.login_admin()
        before = index.store.load()
        for record in (self.item(signed_at="", makeup_date="", leave_at="2026-09-14"),
                       self.item(signed_at="", makeup_date="", leave_at="invalid"),
                       self.item(leave_at="2026-09-15")):
            response = self.client.post("/api/admin/attendance/import", json={"version": before[0], "records": [record]})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(index.store.load(), before)

    def test_invalid_rows_reject_the_entire_import(self):
        self.login_admin()
        before = index.store.load()
        for invalid in (self.item(student_id=999), self.item(course_date="2026-09-22"),
                        self.item(signed_at="invalid"), self.item(signed_at="2026-09-16 00:00:00"),
                        self.item(signed_at="", makeup_date="")):
            with self.subTest(invalid=invalid):
                response = self.client.post("/api/admin/attendance/import", json={"version": before[0], "records": [self.item(course_id=2, course_date="2026-09-22", signed_at="2026-09-22 00:00:00"), invalid]})
                self.assertGreaterEqual(response.status_code, 400)
                self.assertEqual(index.store.load(), before)
        duplicate = self.item(signed_at="2026-09-15 18:05:12")
        response = self.client.post("/api/admin/attendance/import", json={"version": before[0], "records": [duplicate, duplicate]})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(index.store.load(), before)

    def test_import_preserves_other_courses_and_academic_years(self):
        self.login_admin()
        index.store.initialize_year(2)
        other_year = index.store.load(2)
        version, data = index.store.load()
        data["attendances"]["1_2"] = {"status": "ON_LEAVE", "reason": "原有事由"}
        index.store.save(version, data)
        version, data = index.store.load()
        result = self.client.post("/api/admin/attendance/import", json={"version": version, "records": [self.item(signed_at="2026-09-15 18:05:12")]})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(index.store.load()[1]["attendances"]["1_2"], data["attendances"]["1_2"])
        self.assertEqual(index.store.load(2), other_year)

    def test_conflicting_time_does_not_overwrite_existing_attendance(self):
        data = copy.deepcopy(index.SEED_DATA)
        before = copy.deepcopy(data)
        with self.assertRaises(HTTPException) as error:
            import_records(data, [ImportedRecord(**self.item())], "admin")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(data, before)

    def test_students_and_guests_cannot_export_or_import(self):
        for path in ("/api/admin/attendance/export", "/api/admin/attendance/import"):
            response = self.client.get(path) if path.endswith("export") else self.client.post(path, json={"version": 0, "records": [self.item()]})
            self.assertEqual(response.status_code, 403)

    def test_dual_record_is_visible_in_attendance_and_student_dashboard(self):
        self.profile.update(id=1, email="vinnyhuang.ipuregreen@gmail.com")
        version, data = index.store.load()
        from api.sso import link_student
        student = link_student(data, self.profile)
        data["attendances"] = {f"{student['id']}_1": {"status": "MAKEUP_DONE", "signed_at": "2026-09-15 00:00:00", "makeup_date": "2026-09-15 00:00:00"}}
        index.store.save(version, data)
        import jwt, time
        self.client.cookies.set("platform_session", jwt.encode({"access_token": "student", "exp": int(time.time())+300}, index.SECRET_KEY, algorithm="HS256"))
        page = self.client.get("/dashboard")
        self.assertIn("✓ 已簽到", page.text)
        self.assertIn("✓ 已補課", page.text)
        self.login_admin()
        context = {"user": self.profile, "students": [{**self.profile, "account_type": "student", "role": "student"}]}
        from unittest.mock import patch
        with patch("api.sso.platform_request", return_value=context):
            page = self.client.get("/admin/courses/1/attendance")
            self.assertIn('data-status="SIGNED_IN"', page.text)
        response = self.client.delete(f"/api/admin/courses/1/pathdemy/{student['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(index.store.load()[1]["attendances"][f"{student['id']}_1"], {"status": "SIGNED_IN", "signed_at": "2026-09-15 00:00:00"})

import io
import unittest
from datetime import datetime
from urllib.parse import unquote
from unittest.mock import patch
import openpyxl
from api import index
from api.attendance import TAIWAN
from tests import test_sso as fixtures


class AttendanceExportTests(unittest.TestCase):
    tearDown = fixtures.CourseSsoTests.tearDown

    def response(self, path, token=None, payload=None):
        if path == "/api/auth/sso/roster?include_profile=true":
            return {"user": self.profile, "students": self.roster}
        return self.profile

    def setUp(self):
        self.roster = [{"id": i+10, "account_type": "student", "role": "student", "name": name,
                        "email": f"student{i}@example.invalid", "email_backup": f"makeup{i}@example.invalid", "phone": f"091234567{i}"}
                       for i, name in enumerate(("=測試姓名", "學員乙", "學員丙"), 1)]
        fixtures.CourseSsoTests.setUp(self)
        self.profile.update(account_type="staff", role="admin", academic_year={"id": 1, "year": 2026})
        self.client.headers["Authorization"] = "Bearer admin"
        version, data = index.store.load()
        data["students"] = [{**s, "id": i, "platform_id": s["id"], "platform_key": f"student:{s['id']}", "email_pathdemy": s["email_backup"], "unit_code": "", "unit_name": ""}
                            for i, s in enumerate(self.roster, 1)]
        data["courses"][0]["title"] = "導論：測試/課程"
        data["attendances"] = {"1_1": {"status": "SIGNED_IN", "signed_at": "2026-09-15 00:00:00"},
                               "2_1": {"status": "MAKEUP_DONE", "signed_at": "2026-09-15 00:00:00", "makeup_date": "2026-09-15 00:00:00"},
                               "3_1": {"status": "ON_LEAVE", "leave_at": "2026-09-14 00:00:00"}}
        index.store.save(version, data)

    def export(self, **filters):
        return self.client.get("/api/admin/courses/1/attendance/export", params=filters)

    def sheet(self, response):
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else "")
        return openpyxl.load_workbook(io.BytesIO(response.content), data_only=False).active

    def test_full_export_matches_table_and_preserves_text_and_dual_checkin(self):
        before = index.store.load()
        with patch("api.index.taiwan_now", return_value=datetime(2026, 10, 20, 12, 34, 56, tzinfo=TAIWAN)):
            response = self.export()
        sheet = self.sheet(response)
        self.assertEqual(unquote(response.headers["content-disposition"]), "attachment; filename*=UTF-8''1_導論：測試_課程_簽到_20261020123456.xlsx")
        rows = list(sheet.values)
        self.assertEqual(rows[0], ("ID", "姓名", "Email", "Email(補課平台)", "聯絡電話", "簽到狀況", "簽到時間"))
        self.assertEqual(len(rows), 4)
        self.assertEqual([r[5] for r in rows[1:]], ["已簽到", "已簽到", "已請假"])
        self.assertEqual(sheet["B2"].data_type, "s")
        self.assertEqual(sheet["E2"].value, "0912345671")
        self.assertEqual(sheet["E2"].data_type, "s")
        self.assertEqual(rows[2][6], "2026-09-15 00:00:00")
        self.assertEqual(index.store.load(), before)

    def test_filters_match_the_compound_query_including_backup_email(self):
        for filters, expected in [({"status": "SIGNED_IN"}, 2), ({"status": "NOT_SIGNED"}, 1),
                                  ({"name": "乙", "email": " MAKEUP2@EXAMPLE.INVALID ", "phone": "5672", "status": "SIGNED_IN"}, 1),
                                  ({"name": "不存在"}, 0)]:
            with self.subTest(filters=filters):
                self.assertEqual(self.sheet(self.export(**filters)).max_row-1, expected)

    def test_export_rejects_invalid_courses_status_and_unauthorized_users(self):
        self.assertEqual(self.client.get("/api/admin/courses/999/attendance/export").status_code, 404)
        self.assertEqual(self.export(status="UNKNOWN").status_code, 422)
        self.profile.update(account_type="student", role="student")
        self.assertEqual(self.export().status_code, 403)
        self.client.headers.pop("Authorization")
        self.assertEqual(self.export().status_code, 403)

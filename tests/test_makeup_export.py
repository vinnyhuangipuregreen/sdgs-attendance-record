import io
import unittest
from datetime import datetime
from urllib.parse import unquote
from unittest.mock import patch
import openpyxl
from api import index
from api.attendance import TAIWAN
from tests import test_attendance_export as fixtures


class MakeupExportTests(unittest.TestCase):
    response = fixtures.AttendanceExportTests.response
    tearDown = fixtures.AttendanceExportTests.tearDown

    def setUp(self):
        fixtures.AttendanceExportTests.setUp(self)
        version, data = index.store.load()
        data["attendances"] = {"1_1": {"status": "MAKEUP_DONE", "platform_email": "alternate@example.invalid", "leave_at": "2026-09-14 10:00:00", "makeup_date": "2026-09-20 00:00:00"},
                               "2_1": {"status": "MAKEUP_DONE", "signed_at": "2026-09-15 00:00:00", "makeup_date": "2026/09/21 00:00:00"},
                               "3_1": {"status": "ON_LEAVE", "leave_at": "2026-09-14 10:00:00"}}
        index.store.save(version, data)

    def export(self, **filters):
        return self.client.get("/api/admin/courses/1/pathdemy/export", params=filters)

    def rows(self, response):
        self.assertEqual(response.status_code, 200)
        return list(openpyxl.load_workbook(io.BytesIO(response.content)).active.values)

    def test_export_preserves_makeup_and_leave_times_without_changing_records(self):
        before = index.store.load()
        with patch("api.index.taiwan_now", return_value=datetime(2026, 10, 20, 12, 34, 56, tzinfo=TAIWAN)):
            response = self.export()
        self.assertEqual(unquote(response.headers["content-disposition"]), "attachment; filename*=UTF-8''1_導論：測試_課程_補課_20261020123456.xlsx")
        rows = self.rows(response)
        self.assertEqual(rows[0], ("學員姓名", "Email", "Email(補課平台)", "聯絡電話", "補課狀況", "補課時間", "備註"))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][3:], ("0912345671", "已補課", "2026-09-20 00:00:00", "管理員批次匯入"))
        self.assertEqual(rows[2][4:], ("已補課", "2026/09/21 00:00:00", "管理員批次匯入"))
        self.assertEqual(rows[1][2], "alternate@example.invalid")
        sheet = openpyxl.load_workbook(io.BytesIO(response.content)).active
        self.assertEqual(sheet["A2"].data_type, "s")
        self.assertEqual(sheet["D2"].data_type, "s")
        self.assertEqual(index.store.load(), before)

    def test_filters_and_empty_results_match_the_visible_makeup_roster(self):
        rows = self.rows(self.export(name="乙", email=" MAKEUP2@EXAMPLE.INVALID ", phone="5672"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], "學員乙")
        self.assertEqual(len(self.rows(self.export(name="不存在"))), 1)
        version, data = index.store.load()
        data["attendances"] = {}
        index.store.save(version, data)
        self.assertEqual(len(self.rows(self.export())), 1)

    def test_date_filters_are_inclusive_and_normalize_slash_dates(self):
        for filters, count in [({"start_date": "2026-09-21"}, 1),
                               ({"end_date": "2026-09-20"}, 1),
                               ({"start_date": "2026-09-20", "end_date": "2026-09-21"}, 2),
                               ({"start_date": "2026-09-22"}, 0),
                               ({"email": "ALTERNATE@EXAMPLE.INVALID", "end_date": "2026-09-20"}, 1)]:
            with self.subTest(filters=filters):
                self.assertEqual(len(self.rows(self.export(**filters))), count + 1)

    def test_auth_course_lookup_and_academic_year_isolation(self):
        self.assertEqual(self.client.get("/api/admin/courses/999/pathdemy/export").status_code, 404)
        original = index.store.load()
        index.store.initialize_year(2)
        version, data = index.store.load(2)
        data["courses"] = [{"id": 1, "title": "2027 課程", "course_date": "2027/09/15", "course_time": "18:00-22:00"}]
        index.store.save(version, data, 2)
        self.profile["academic_year"] = {"id": 2, "year": 2027}
        self.assertEqual(len(self.rows(self.export())), 1)
        self.assertEqual(index.store.load(), original)
        self.profile.update(account_type="student", role="student")
        self.assertEqual(self.export().status_code, 403)
        self.client.headers.pop("Authorization")
        self.assertEqual(self.export().status_code, 403)

    def test_deleting_makeup_and_returning_to_courses_uses_fresh_matching_counts(self):
        before=index.store.load()[1]
        self.assertEqual(len(index.course_makeup_records(before,before['courses'][0])),2)
        self.assertEqual(self.client.delete('/api/admin/courses/1/pathdemy/1').status_code,200)
        page=self.client.get('/admin/courses/1/pathdemy')
        self.assertIn('href="/admin" data-makeup-back',page.text)
        self.assertNotIn('history.back()',page.text)
        listing=self.client.get('/admin',headers={'X-Platform-Embedded':'1'})
        self.assertEqual(listing.status_code,200)
        data=index.store.load()[1]
        self.assertEqual(index.course_record_counts(data)[1]['MAKEUP_DONE'],1)
        self.assertEqual(len(index.course_makeup_records(data,data['courses'][0])),1)

import re
import unittest
from api import index
from tests import test_sso as fixtures


class StudentDashboardTests(unittest.TestCase):
    tearDown = fixtures.CourseSsoTests.tearDown
    response = fixtures.CourseSsoTests.response

    def setUp(self):
        fixtures.CourseSsoTests.setUp(self)
        self.student = {**self.profile, "id": 433, "name": "課程學員"}
        self.profile.update(account_type="staff", role="admin", name="管理員")
        self.client.headers.update({"Authorization": "Bearer verified-admin", "X-Platform-Embedded": "1"})
        self.remote_mock.side_effect = lambda *args: {"user": self.profile, "students": [self.student]}
        version, data = index.store.load()
        data["students"] = [{**self.student, "id": 9, "platform_key": "student:433"}]
        data["courses"] = [
            {"id": i, "title": f"課程{i}", "course_date": "2026/09/15", "course_time": "18:00-22:00"}
            for i in range(1, 5)
        ]
        data["attendances"] = {
            "9_1": {"status": "SIGNED_IN", "signed_at": "2026-09-15 18:00:00"},
            "9_2": {"status": "ON_LEAVE", "leave_at": "2026-09-14 18:00:00"},
            "9_3": {"status": "MAKEUP_DONE", "signed_at": "2026-09-15 18:05:00", "makeup_date": "2026-09-16 18:00:00"},
        }
        index.store.save(version, data)

    def test_selected_platform_id_shows_real_records_without_student_actions(self):
        records = index.store.load()[1]["attendances"]
        self.remote_mock.reset_mock()
        page = self.client.get("/admin/students/433/dashboard")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("課程學員 學員的課程登錄", page.text)
        self.assertIn("2026-09-15 18:05:00", page.text)
        self.assertIn("2026-09-16 18:00:00", page.text)
        stats = dict(re.findall(r'data-course-stat="(\w+)".*?class="stat-value">(\d+)', page.text, re.S))
        self.assertEqual(stats, {"signed": "2", "leave": "1", "makeup": "1", "absent": "1"})
        self.assertEqual(page.text.count('data-course-id="'), 4)
        self.assertNotIn("onclick=", page.text)
        self.assertNotIn("attendance-dashboard.js", page.text)
        self.assertNotIn('href="/sso/logout"', page.text)
        self.assertEqual(index.store.load()[1]["attendances"], records)
        self.assertEqual([call.args[0] for call in self.remote_mock.call_args_list], ["/api/auth/sso/roster?include_profile=true"])

    def test_missing_student_and_non_staff_cannot_open_dashboard(self):
        self.assertEqual(self.client.get("/admin/students/9/dashboard").status_code, 404)
        self.profile.update(account_type="student", role="student")
        self.assertEqual(self.client.get("/admin/students/433/dashboard").status_code, 403)
        self.client.headers.clear()
        self.assertEqual(self.client.get("/admin/students/433/dashboard").status_code, 403)

    def test_selected_academic_year_has_its_own_courses_and_records(self):
        before = index.store.load()
        index.store.initialize_year(2)
        self.profile["academic_year"] = {"id": 2, "year": 2027}
        page = self.client.get("/admin/students/433/dashboard")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("共 0 堂課程", page.text)
        self.assertNotIn("2026-09-15 18:05:00", page.text)
        self.assertEqual(index.store.load(), before)

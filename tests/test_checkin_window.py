import copy
import unittest
from datetime import datetime
from fastapi import HTTPException
from api.attendance import TAIWAN, apply_action, record_view
from tests import test_sso as fixtures
from api import index

class CheckinWindowTests(unittest.TestCase):
    def setUp(self):
        self.course = {"id": 1, "title": "測試課程", "course_date": "2026/10/23", "course_time": "18:30-21:30", "open_time": "2026-10-23 18:00:00", "checkin_start": "18:00", "checkin_end": "22:00"}
        self.data = {"courses": [self.course], "attendances": {}}
        self.student = {"id": 1, "email":"student@example.invalid"}

    def test_boundaries_use_checkin_window_instead_of_class_hours(self):
        for hour, minute in [(18, 0), (18, 15), (21, 45), (22, 0)]:
            with self.subTest(hour=hour, minute=minute):
                result = apply_action(copy.deepcopy(self.data), self.student, 1, "checkin", datetime(2026,10,23,hour,minute,tzinfo=TAIWAN))
                self.assertTrue(result["success"])
        for now in [datetime(2026,10,23,17,59,59,tzinfo=TAIWAN), datetime(2026,10,23,22,0,1,tzinfo=TAIWAN), datetime(2026,10,24,18,tzinfo=TAIWAN)]:
            with self.assertRaises(HTTPException) as err:
                apply_action(self.data, self.student, 1, "checkin", now)
            self.assertEqual(err.exception.detail, "吉祥！2026/10/23 測試課程的簽到區間為：18:00-22:00。")
            self.assertEqual(self.data["attendances"], {})

    def test_view_closes_only_checkin_at_its_deadline_and_preserves_legacy_window(self):
        view = record_view(self.course, None, datetime(2026,10,23,21,45,tzinfo=TAIWAN))
        self.assertFalse(view["checkin_closed"])
        self.assertTrue(view["closed"])
        self.assertTrue(record_view(self.course, None, datetime(2026,10,23,22,0,1,tzinfo=TAIWAN))["checkin_closed"])
        legacy = {k:v for k,v in self.course.items() if not k.startswith("checkin_")}
        view = record_view(legacy, None, datetime(2026,10,23,18,0,tzinfo=TAIWAN))
        self.assertEqual((view["checkin_start"], view["checkin_end"]), ("18:00", "21:30"))
        self.assertNotIn("checkin_end", legacy)
    def test_inconsistent_legacy_opening_uses_existing_class_window_without_mutating_data(self):
        legacy = {"id":13,"title":"實地參訪","course_date":"2026/12/08","course_time":"13:30-16:30","open_time":"2026-12-08 18:00:00"}
        original = dict(legacy)
        view = record_view(legacy,None,datetime(2026,12,8,14,tzinfo=TAIWAN))
        self.assertEqual((view["checkin_start"],view["checkin_end"]),("13:30","16:30"))
        self.assertEqual(legacy, original)

    def test_makeup_opens_at_next_taiwan_midnight_for_all_unfinished_students(self):
        for previous in ({},{"status":"SIGNED_IN","signed_at":"2026-10-23 18:30:00"},{"status":"ON_LEAVE","leave_at":"2026-10-23 17:00:00"}):
            data=copy.deepcopy(self.data);data['attendances']['1_1']=previous
            for now in [datetime(2026,10,22,12,tzinfo=TAIWAN),datetime(2026,10,23,23,59,59,tzinfo=TAIWAN)]:
                self.assertFalse(record_view(self.course,previous,now)['makeup_available'])
                before=copy.deepcopy(data)
                with self.assertRaises(HTTPException):apply_action(data,self.student,1,'makeup',now)
                self.assertEqual(data,before)
            now=datetime(2026,10,24,tzinfo=TAIWAN)
            self.assertTrue(record_view(self.course,previous,now)['makeup_available'])
            record=apply_action(data,self.student,1,'makeup',now)['record']
            self.assertEqual(record['makeup_date'],'2026-10-24 00:00:00')
            self.assertEqual(record.get('signed_at'),previous.get('signed_at'))
            self.assertEqual(record['leave_at'],previous.get('leave_at',''))
            self.assertFalse(record_view(self.course,record,now)['makeup_available'])
            before=copy.deepcopy(data)
            with self.assertRaises(HTTPException):apply_action(data,self.student,1,'makeup',datetime(2026,11,1,tzinfo=TAIWAN))
            self.assertEqual(data,before)


class CourseEditorTests(unittest.TestCase):
    setUp = fixtures.CourseSsoTests.setUp
    tearDown = fixtures.CourseSsoTests.tearDown
    response = fixtures.CourseSsoTests.response
    begin = fixtures.CourseSsoTests.begin

    def payload(self):
        return {"title":"新課程", "course_date":"2026-10-23", "course_time":"18:30-21:30", "checkin_start":"18:00", "checkin_end":"22:00"}

    def login(self):
        self.profile.update(account_type="staff", role="admin", academic_year={"id":1,"year":2026})
        self.client.headers.update({"Authorization":"Bearer admin", "X-Platform-Embedded":"1"})

    def test_required_fields_half_hour_options_and_atomic_validation(self):
        self.login()
        for change in ({"title":""}, {"course_date":""}, {"course_time":""}, {"checkin_start":""}, {"checkin_end":""}, {"course_time":"18:15-21:30"}, {"course_time":"21:30-18:30"}, {"checkin_start":"18:10"}, {"checkin_end":"18:00"}, {"checkin_end":"24:00"}):
            before=index.store.load()
            result=self.client.post("/api/admin/courses",json={**self.payload(),**change})
            self.assertEqual(result.status_code,422,result.text)
            self.assertEqual(index.store.load(),before)
        missing=self.payload();missing.pop("checkin_end")
        self.assertEqual(self.client.post("/api/admin/courses",json=missing).status_code,422)

    def test_create_edit_reload_and_old_partial_edits_preserve_records(self):
        self.login()
        before=index.store.load()[1]["attendances"]
        result=self.client.post("/api/admin/courses",json=self.payload())
        self.assertEqual(result.status_code,200,result.text)
        course=result.json()["course"];cid=course["id"]
        self.assertEqual(course["course_date"],"2026/10/23")
        self.assertEqual(course["open_time"],"2026-10-23 18:00:00")
        result=self.client.put(f"/api/admin/courses/{cid}",json={"checkin_start":"17:30","checkin_end":"22:30"})
        self.assertEqual(result.status_code,200,result.text)
        saved=next(c for c in index.store.load()[1]["courses"] if c["id"]==cid)
        self.assertEqual((saved["checkin_start"],saved["checkin_end"]),("17:30","22:30"))
        self.assertEqual(index.store.load()[1]["attendances"],before)
        self.assertEqual(self.client.put("/api/admin/courses/1",json={"title":"舊課程修改"}).status_code,200)
        page=self.client.get("/admin").text
        self.assertIn('type="date" id="modalCourseDate"',page)
        self.assertIn('id="modalCheckinEnd"',page)

    def test_student_dashboard_disables_expired_checkin(self):
        self.profile.update(account_type="student",role="student",academic_year={"id":1,"year":2026})
        state = self.begin()
        self.client.get("/auth/sso/callback",params={"state":state,"code":"code"},follow_redirects=False)
        from unittest.mock import patch
        with patch("api.index.taiwan_now",return_value=datetime(2026,9,15,22,0,1,tzinfo=TAIWAN)):
            page=self.client.get("/dashboard").text
        self.assertIn('data-checkin-button="1" disabled',page)

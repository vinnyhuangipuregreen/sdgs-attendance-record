import copy
import re
import unittest
from datetime import datetime
from unittest.mock import patch
from api import index
from api.attendance import TAIWAN, apply_backfill
from api.storage import ConcurrentUpdate
from tests import test_student_dashboard as dashboard_fixtures


class StudentBackfillTests(unittest.TestCase):
    response = dashboard_fixtures.StudentDashboardTests.response
    tearDown = dashboard_fixtures.StudentDashboardTests.tearDown

    def setUp(self):
        dashboard_fixtures.StudentDashboardTests.setUp(self)
        self.clock = patch('api.index.taiwan_now', return_value=datetime(2026, 10, 30, 12, tzinfo=TAIWAN))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.base = '/api/admin/attendance/students/433/'

    def test_only_absent_course_has_three_enabled_backfill_buttons(self):
        page = self.client.get('/admin/students/433/backfill')
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn('data-attendance-reload="/admin/students/433/backfill"', page.text)
        self.assertEqual(re.findall(r'onclick="doBackfill\((\d+),', page.text), ['4', '4', '4'])
        self.assertEqual(page.text.count('bg-red-600 hover:bg-red-700 text-white'), 3)
        self.assertNotIn('doCheckin(', page.text)
        self.assertNotIn('doMakeup(', page.text)

    def test_all_actions_persist_notes_show_on_frontend_and_cancel_exactly(self):
        for action, status, label in [('checkin', 'SIGNED_IN', '已簽到'), ('leave', 'ON_LEAVE', '已請假'), ('makeup', 'MAKEUP_DONE', '已補課')]:
            with self.subTest(action=action):
                before = copy.deepcopy(index.store.load()[1]['attendances'])
                result = self.client.post(self.base + 'backfill-' + action, json={'course_id': 4, 'reason':'家中有事'})
                self.assertEqual(result.status_code, 200, result.text)
                records = index.store.load()[1]['attendances']
                saved = records['9_4']
                self.assertEqual(saved['status'], status)
                self.assertEqual(saved[action + '_note'], '補登錄')
                self.assertEqual(saved['note'], '補登錄')
                if action == 'leave':
                    self.assertEqual(saved['reason'], '家中有事')
                self.assertEqual(saved['backfill']['by'], '管理員')
                self.assertEqual(saved['backfill']['at'], '2026-10-30 12:00:00')
                self.assertEqual({k:v for k,v in records.items() if k != '9_4'}, before)
                for mode in ['dashboard', 'backfill']:
                    page = self.client.get('/admin/students/433/' + mode)
                    self.assertIn(label + ' (補)', page.text)
                page = self.client.get('/admin/students/433/backfill')
                self.assertIn('取消：✓ ' + label + ' (補)', page.text)
                self.assertEqual(page.text.count('bg-red-600 hover:bg-red-700 text-white'), 1 if action == 'leave' else 0)
                for other in (['checkin', 'leave'] if action == 'leave' else ['checkin', 'leave', 'makeup']):
                    self.assertEqual(self.client.post(self.base + 'backfill-' + other, json={'course_id':4}).status_code, 409)
                if action == 'checkin':
                    self.assertEqual(self.client.post(self.base + 'makeup', json={'course_id':4}).status_code, 409)
                self.assertEqual(self.client.post(self.base + 'cancel-' + action, json={'course_id':4}).status_code, 200)
                self.assertEqual(index.store.load()[1]['attendances'], before)
                self.assertEqual(self.client.post(self.base + 'cancel-' + action, json={'course_id':4}).status_code, 409)

    def test_cancel_restores_existing_absent_record_with_metadata(self):
        version, data = index.store.load()
        previous = {'status':'ABSENT', 'note':'原有備註', 'updated_at':'2026-09-16 10:00:00'}
        data['attendances']['9_4'] = previous
        index.store.save(version, data)
        self.assertEqual(self.client.post(self.base + 'backfill-checkin', json={'course_id':4}).status_code, 200)
        self.assertEqual(self.client.post(self.base + 'cancel-checkin', json={'course_id':4}).status_code, 200)
        self.assertEqual(index.store.load()[1]['attendances']['9_4'], previous)

    def test_existing_records_future_courses_and_wrong_cancel_are_rejected(self):
        before = index.store.load()
        for course_id in [1, 2, 3]:
            for action in ['checkin', 'leave', 'makeup']:
                self.assertEqual(self.client.post(self.base + 'backfill-' + action, json={'course_id':course_id}).status_code, 409)
                self.assertEqual(self.client.post(self.base + 'cancel-' + action, json={'course_id':course_id}).status_code, 409)
        self.assertEqual(index.store.load(), before)
        with patch('api.index.taiwan_now', return_value=datetime(2026,9,14,tzinfo=TAIWAN)):
            self.assertEqual(self.client.post(self.base + 'backfill-checkin', json={'course_id':4}).status_code,409)
        self.assertEqual(self.client.post(self.base + 'backfill-checkin', json={'course_id':999}).status_code,404)

    def test_followup_makeup_cannot_be_overwritten_by_cancelling_backfill_leave(self):
        self.assertEqual(self.client.post(self.base + 'backfill-leave', json={'course_id':4,'reason':'家中有事'}).status_code,200)
        self.assertEqual(self.client.post(self.base + 'makeup', json={'course_id':4}).status_code,200)
        before = index.store.load()
        self.assertEqual(self.client.post(self.base + 'cancel-leave', json={'course_id':4}).status_code,409)
        self.assertEqual(index.store.load(),before)

    def test_authentication_student_scope_and_year_isolation(self):
        before = index.store.load()
        self.assertEqual(self.client.post('/api/admin/attendance/students/9/backfill-checkin', json={'course_id':4}).status_code,404)
        index.store.initialize_year(2)
        self.profile['academic_year'] = {'id':2,'year':2027}
        self.assertEqual(self.client.post(self.base+'backfill-checkin',json={'course_id':4}).status_code,404)
        self.assertEqual(index.store.load(),before)
        self.profile.update(account_type='student',role='student')
        self.assertEqual(self.client.get('/admin/students/433/backfill').status_code,403)
        self.assertEqual(self.client.post(self.base+'backfill-checkin',json={'course_id':4}).status_code,403)
        self.client.headers.clear()
        self.assertEqual(self.client.post(self.base+'cancel-checkin',json={'course_id':4}).status_code,403)

    def test_two_admins_cannot_overwrite_each_others_backfill(self):
        _, original = index.store.load()
        first, second = copy.deepcopy(original), copy.deepcopy(original)
        now = datetime(2026,10,30,tzinfo=TAIWAN)
        student = original['students'][0]
        apply_backfill(first,student,4,'checkin',now,'管理員一')
        apply_backfill(second,student,4,'leave',now,'管理員二',reason='家中有事')
        index.store.save_records(original,first)
        with self.assertRaises(ConcurrentUpdate):
            index.store.save_records(original,second)
        self.assertEqual(index.store.load()[1]['attendances']['9_4']['status'],'SIGNED_IN')

    def test_backfill_leave_requires_a_real_reason_without_writing_on_failure(self):
        before = index.store.load()
        for body in [{'course_id':4}, {'course_id':4,'reason':''}, {'course_id':4,'reason':'  '}, {'course_id':4,'reason':123}]:
            result = self.client.post(self.base+'backfill-leave',json=body)
            self.assertEqual(result.status_code,422,result.text)
            self.assertEqual(index.store.load(),before)
        result = self.client.post(self.base+'backfill-leave',json={'course_id':4,'reason':'  出差  '})
        self.assertEqual(result.status_code,200,result.text)
        saved = index.store.load()[1]['attendances']['9_4']
        self.assertEqual(saved['reason'],'出差')
        self.assertEqual(saved['note'],'補登錄')
        self.assertEqual(self.client.post(self.base+'cancel-leave',json={'course_id':4}).status_code,200)
        self.assertEqual(index.store.load()[1]['attendances'],before[1]['attendances'])

    def test_backfill_leave_allows_makeup_and_cancels_in_reverse_order(self):
        original = copy.deepcopy(index.store.load()[1]['attendances'])
        self.assertEqual(self.client.post(self.base+'backfill-leave',json={'course_id':4,'reason':'出差'}).status_code,200)
        leave = copy.deepcopy(index.store.load()[1]['attendances']['9_4'])
        page = self.client.get('/admin/students/433/backfill')
        self.assertIn("onclick=\"doBackfill(4, 'makeup')\"",page.text)
        self.assertNotIn("onclick=\"doBackfill(4, 'checkin')\"",page.text)
        self.assertNotIn("onclick=\"doBackfill(4, 'leave')\"",page.text)
        self.assertEqual(self.client.post(self.base+'backfill-makeup',json={'course_id':4}).status_code,200)
        saved = index.store.load()[1]['attendances']['9_4']
        self.assertEqual(saved['status'],'MAKEUP_DONE')
        self.assertEqual(saved['reason'],'出差')
        self.assertEqual(saved['leave_at'],leave['leave_at'])
        self.assertEqual(saved['leave_note'],'補登錄')
        self.assertEqual(saved['makeup_note'],'補登錄')
        for mode in ['dashboard','backfill']:
            page = self.client.get('/admin/students/433/'+mode)
            self.assertIn('✓ 已請假 (補)',page.text)
            self.assertIn('✓ 已補課 (補)',page.text)
        before = index.store.load()
        self.assertEqual(self.client.post(self.base+'cancel-leave',json={'course_id':4}).status_code,409)
        self.assertEqual(self.client.post(self.base+'backfill-makeup',json={'course_id':4}).status_code,409)
        self.assertEqual(index.store.load(),before)
        self.assertEqual(self.client.post(self.base+'cancel-makeup',json={'course_id':4}).status_code,200)
        self.assertEqual(index.store.load()[1]['attendances']['9_4'],leave)
        self.assertEqual(self.client.post(self.base+'cancel-leave',json={'course_id':4}).status_code,200)
        self.assertEqual(index.store.load()[1]['attendances'],original)

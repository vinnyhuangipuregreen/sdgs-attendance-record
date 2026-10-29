import io
import unittest
import openpyxl
from tests import test_student_backfill as fixtures
from api import index


class BackfillRosterTests(unittest.TestCase):
    setUp = fixtures.StudentBackfillTests.setUp
    tearDown = fixtures.StudentBackfillTests.tearDown
    response = fixtures.StudentBackfillTests.response

    def test_each_backfill_is_shown_in_its_course_roster_and_export(self):
        for action, route, label in [('checkin','attendance','已簽到 (補)'),('leave','leaves','已請假 (補)'),('makeup','pathdemy','已補課 (補)')]:
            result = self.client.post(self.base+'backfill-'+action,json={'course_id':4,'reason':'出差'})
            self.assertEqual(result.status_code,200,result.text)
            page = self.client.get('/admin/courses/4/'+route)
            self.assertEqual(page.status_code,200,page.text)
            self.assertIn('備註',page.text)
            if action != 'makeup':
                self.assertIn('✓ '+label,page.text)
                self.assertIn('補登錄</td>',page.text)
            else:
                self.assertIn('補課狀況</th>',page.text)
                self.assertNotIn('請假時間</th>',page.text)
            response = self.client.get('/api/admin/courses/4/'+route+'/export')
            self.assertEqual(response.status_code,200)
            rows = list(openpyxl.load_workbook(io.BytesIO(response.content)).active.values)
            row = dict(zip(rows[0],rows[1]))
            self.assertEqual(row[{'checkin':'簽到狀況','leave':'請假狀況','makeup':'補課狀況'}[action]],label)
            self.assertEqual(row['備註'],'補登錄')
            self.assertEqual(self.client.post(self.base+'cancel-'+action,json={'course_id':4}).status_code,200)

    def test_makeup_after_backfilled_leave_keeps_leave_reason_and_both_roster_notes(self):
        self.client.post(self.base+'backfill-leave',json={'course_id':4,'reason':'家中有事'})
        self.client.post(self.base+'backfill-makeup',json={'course_id':4})
        data = index.store.load()[1]
        course = next(c for c in data['courses'] if c['id']==4)
        leave = index.course_leave_records(data,course)[0]
        makeup = index.course_makeup_records(data,course)[0]
        self.assertEqual(leave['status_label'],'已請假 (補)')
        self.assertEqual(leave['reason'],'家中有事')
        self.assertEqual(leave['note'],'補登錄')
        self.assertEqual(makeup['status_label'],'已補課 (補)')
        self.assertEqual(makeup['note'],'補登錄')

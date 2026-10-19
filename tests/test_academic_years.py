import unittest
from api import index
from api.storage import ConcurrentUpdate
from tests import test_sso as fixtures


class AnnualCourseTests(unittest.TestCase):
    setUp=fixtures.CourseSsoTests.setUp
    tearDown=fixtures.CourseSsoTests.tearDown
    response=fixtures.CourseSsoTests.response
    begin=fixtures.CourseSsoTests.begin

    def test_annual_store_preserves_2026_and_independent_versions(self):
        version,legacy=index.store.load()
        index.store.initialize_year(2)
        second_version,data=index.store.load(2)
        self.assertEqual(data['courses'],[])
        self.assertEqual(data['attendances'],{})
        data['courses']=[{'id':1,'title':'2027 課程','course_date':'2027/09/15','course_time':'18:00-22:00','open_time':'2027-09-15 18:00:00'}]
        index.store.save(second_version,data,2)
        self.assertEqual(index.store.load()[1],legacy)
        self.assertEqual(index.store.load()[0],version)
        with self.assertRaises(ConcurrentUpdate): index.store.save(second_version,data,2)
        index.store.initialize_year(2)
        self.assertEqual(index.store.load(2)[1],data)

    def test_dashboard_and_old_tab_writes_use_the_authenticated_year(self):
        index.store.initialize_year(2)
        version,data=index.store.load(2)
        data['courses']=[{'id':1,'title':'2027 專用課程','course_date':'2027/09/15','course_time':'18:00-22:00','open_time':'2027-09-15 18:00:00'}]
        index.store.save(version,data,2)
        self.profile['academic_year']={'id':2,'year':2027,'start_month':'202709','end_month':'202806'}
        state=self.begin()
        self.client.get('/auth/sso/callback',params={'state':state,'code':'code'},follow_redirects=False)
        page=self.client.get('/dashboard')
        self.assertEqual(page.status_code,200,page.text)
        self.assertIn('2027 專用課程',page.text)
        self.assertNotIn('導論：自然永續',page.text)
        self.assertIn('202709',page.text)
        before=index.store.load(2)
        rejected=self.client.post('/api/attendance/checkin',headers={'X-Academic-Year':'1'},json={'course_id':1})
        self.assertEqual(rejected.status_code,409,rejected.text)
        self.assertEqual(index.store.load(2),before)

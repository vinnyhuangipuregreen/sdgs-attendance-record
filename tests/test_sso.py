import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs
from fastapi import HTTPException
from fastapi.testclient import TestClient
from api import index
from api.sso import link_student


class CourseSsoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ,{"DATABASE_PATH":str(Path(self.temp.name)/"course.db"),"TURSO_DATABASE_URL":"","TURSO_AUTH_TOKEN":"","VERCEL":""})
        self.env.start()
        self.profile={"id":7,"name":"Student","email":"student@test.invalid","phone":"0900","account_type":"student","role":"student"}
        self.remote=patch("api.sso.platform_request",side_effect=self.response)
        self.remote_mock = self.remote.start()
        self.client_context=TestClient(index.app)
        self.client=self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None,None,None)
        self.remote.stop();self.env.stop();self.temp.cleanup()

    def response(self,path,token=None,payload=None):
        if path == "/api/auth/sso/roster?include_profile=true": return {"user":self.profile,"students":[]}
        if path.endswith("exchange"): return {"access_token":"verified-platform-token","user":self.profile}
        if path.endswith("roster"): return []
        return self.profile

    def begin(self,admin=False):
        response=self.client.get("/sso/login",params={"admin":str(admin).lower()},follow_redirects=False)
        self.assertEqual(response.status_code,302)
        query=parse_qs(urlsplit(response.headers["location"]).query)
        self.assertEqual(query["client_id"],["attendance"])
        self.assertNotIn("access_token",query)
        self.assertIn("HttpOnly",response.headers["set-cookie"])
        return query["state"][0]

    def test_an_existing_platform_login_returns_without_another_password_form(self):
        state=self.begin()
        response=self.client.get("/auth/sso/callback",params={"state":state,"code":"single-use-code"},follow_redirects=False)
        self.assertEqual(response.status_code,302)
        self.assertEqual(response.headers["location"],"/dashboard")
        self.assertIn("HttpOnly",response.headers["set-cookie"])
        self.assertEqual(self.client.get("/dashboard").status_code,200)
        self.remote_mock.reset_mock()
        response = self.client.get("/")
        self.assertEqual(response.url.path, "/dashboard")
        self.assertNotIn("/sso/authorize", str(response.url))
        self.assertEqual([call.args[0] for call in self.remote_mock.call_args_list], ["/api/auth/me"])

    def test_csrf_state_tampering_and_expired_session_are_rejected(self):
        self.begin()
        self.assertEqual(self.client.get("/auth/sso/callback",params={"state":"wrong","code":"code"}).status_code,400)

    def test_login_redirects_do_not_load_course_data_or_validate_old_sessions(self):
        with patch.object(index.store, "load", side_effect=AssertionError("unnecessary course query")), \
             patch("api.index.resolve_session", side_effect=AssertionError("unnecessary session query")):
            self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 302)
            state = self.begin()
            response = self.client.get("/auth/sso/callback", params={"state": state, "code": "code"}, follow_redirects=False)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["location"], "/dashboard")
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/auth/sso/callback",params={"state":"wrong","code":"code"}).status_code,400)

    def test_student_cannot_enter_admin_with_sso(self):
        state=self.begin(True)
        self.assertEqual(self.client.get("/auth/sso/callback",params={"state":state,"code":"code"},follow_redirects=False).status_code,403)

    def test_staff_accounts_cannot_use_the_student_course_entry(self):
        self.profile.update(account_type="staff",role="admin")
        state = self.begin()
        self.assertEqual(self.client.get("/auth/sso/callback",params={"state":state,"code":"code"},follow_redirects=False).status_code,403)
        state = self.begin(True)
        self.assertEqual(self.client.get("/auth/sso/callback",params={"state":state,"code":"code"},follow_redirects=False).status_code,302)
        response = self.client.get("/dashboard",follow_redirects=False)
        self.assertEqual(response.headers["location"],"/sso/login")

    def test_old_admin_entry_redirects_to_the_unified_course_console(self):
        for path in ("/admin", "/admin/login"):
            response = self.client.get(path, follow_redirects=False)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["location"], "https://sdgs-action-platform.vercel.app/#admin-attendance")

    def test_embedded_course_tools_use_verified_staff_bearer_without_another_login(self):
        headers = {"Authorization": "Bearer platform-token", "X-Platform-Embedded": "1"}
        self.assertEqual(self.client.get("/admin", headers=headers).status_code, 403)
        self.profile.update(account_type="staff", role="teacher")
        self.remote_mock.reset_mock()
        response = self.client.get("/admin", headers=headers)
        self.assertEqual([call.args[0] for call in self.remote_mock.call_args_list], ["/api/auth/me"])
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('aria-label="主要功能"', response.text)
        self.assertNotIn("登出後台", response.text)
        self.assertNotIn('id="panel-students"', response.text)
        self.assertNotIn('id="panel-admins"', response.text)
        self.assertIn("switchTab('courses')", response.text)
        for section in ("attendance", "leaves", "pathdemy"):
            self.assertEqual(self.client.get(f"/admin/courses/1/{section}", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/admin/courses/sample-excel", headers=headers).status_code, 200)
        with patch("api.sso.platform_request", side_effect=HTTPException(401, "revoked")):
            self.assertEqual(self.client.get("/admin", headers=headers).status_code, 403)

    def test_course_record_ids_are_preserved_and_shared_emails_do_not_merge_identities(self):
        data={"students":[{"id":9,"name":"Legacy","email":"student@test.invalid"}],"attendances":{"9_1":{"status":"SIGNED_IN"}}}
        self.assertEqual(link_student(data,self.profile)["id"],9)
        other={**self.profile,"id":8}
        self.assertEqual(link_student(data,other)["id"],10)
        self.assertIn("9_1",data["attendances"])

    def test_nsp_course_catalog_requires_staff_and_returns_dates_titles_and_ids(self):
        headers = {"Authorization": "Bearer platform-token"}
        self.assertEqual(self.client.get("/api/admin/courses/catalog", headers=headers).status_code, 403)
        self.profile.update(account_type="staff", role="admin")
        response = self.client.get("/api/admin/courses/catalog", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json())
        self.assertEqual(set(response.json()[0]), {"id", "title", "course_date"})
        self.assertEqual(response.json()[0]["course_date"], "2026/09/15")

    def test_course_views_verify_identity_and_roster_with_one_platform_request(self):
        self.profile.update(account_type="staff", role="admin")
        headers = {"Authorization":"Bearer platform-token", "X-Platform-Embedded":"1"}
        for section in ("attendance", "leaves", "pathdemy"):
            self.remote_mock.reset_mock()
            response = self.client.get(f"/admin/courses/1/{section}", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual([call.args[0] for call in self.remote_mock.call_args_list], ["/api/auth/sso/roster?include_profile=true"])

    def test_course_edits_and_deletes_do_not_fetch_the_student_roster(self):
        self.profile.update(account_type="staff", role="admin")
        headers = {"Authorization":"Bearer platform-token", "X-Platform-Embedded":"1"}
        for method in ("put", "delete"):
            self.remote_mock.reset_mock()
            response = getattr(self.client, method)("/api/admin/courses/2", headers=headers, **({"json":{"title":"Updated test course"}} if method == "put" else {}))
            self.assertEqual(response.status_code, 200)
            self.assertEqual([call.args[0] for call in self.remote_mock.call_args_list], ["/api/auth/me"])

    def test_course_roster_replaces_old_test_accounts_and_keeps_current_record_ids(self):
        self.profile.update(account_type="staff", role="admin")
        active = {"id": 50, "account_type": "student", "name": "Current student", "email": "current@test.invalid", "phone": "0900"}
        version, data = index.store.load()
        data["students"] = [
            {"id": 8, "platform_key": "student:50", "name": "Old name", "email": active["email"], "phone": "0900"},
            {"id": 9, "platform_key": "student:7", "name": "Old test", "email": "old@test.invalid", "phone": "0901"},
            {"id": 10, "name": "Unlinked test", "email": "test@test.invalid", "phone": "0902"},
        ]
        data["attendances"] = {"8_1": {"status": "SIGNED_IN"}, "8_2": {"status": "ON_LEAVE"}, "9_1": {"status": "SIGNED_IN"}}
        index.store.save(version, data)
        index.store.initialize_year(2)
        other_year = index.store.load(2)
        headers = {"Authorization": "Bearer platform-token", "X-Platform-Embedded": "1"}
        with patch("api.sso.platform_request", return_value={"user": self.profile, "students": [active]}):
            page = self.client.get("/admin/courses/1/attendance", headers=headers)
        self.assertEqual(page.status_code, 200, page.text)
        saved = index.store.load()[1]
        self.assertEqual([s["id"] for s in saved["students"]], [8])
        self.assertEqual(saved["students"][0]["name"], active["name"])
        self.assertEqual(saved["attendances"], {"8_1": {"status": "SIGNED_IN"}, "8_2": {"status": "ON_LEAVE"}})
        self.assertEqual(saved["courses"], data["courses"])
        self.assertEqual(index.store.load(2), other_year)
        self.assertNotIn("Old test", page.text)

    def test_roster_sync_failure_never_erases_existing_students_or_records(self):
        self.profile.update(account_type="staff", role="admin")
        before = index.store.load()
        with patch("api.sso.platform_request", side_effect=HTTPException(503, "offline")):
            result = self.client.get("/admin/courses/1/attendance", headers={"Authorization": "Bearer platform-token"})
        self.assertEqual(result.status_code, 503)
        self.assertEqual(index.store.load(), before)

    def test_revoked_or_unavailable_platform_login_never_falls_back_to_local_accounts(self):
        state=self.begin()
        self.client.get("/auth/sso/callback",params={"state":state,"code":"code"},follow_redirects=False)
        with patch("api.sso.platform_request",side_effect=HTTPException(401,"expired")):
            self.assertEqual(self.client.get("/dashboard",follow_redirects=False).headers["location"],"/sso/login")
        with patch("api.sso.platform_request",side_effect=HTTPException(503,"offline")):
            self.assertEqual(self.client.get("/dashboard",follow_redirects=False).status_code,503)


    def test_completion_counts_are_authenticated_aggregate_makeup_records_only(self):
        path='/api/nsp/course-completions'
        self.assertEqual(self.client.get(path).status_code,401)
        headers={"Authorization":"Bearer platform-token"}
        version,data=index.store.load()
        data['attendances']={'1_1':{'status':'MAKEUP_DONE'},'999_1':{'status':'MAKEUP_DONE'}}
        index.store.save(version,data)
        result=self.client.get(path,headers=headers)
        self.assertEqual(result.status_code,200)
        self.assertEqual(set(result.json()[0]),{'title','makeup_count'})
        self.assertEqual(result.json()[0]['makeup_count'],1)
        index.store.initialize_year(2)
        self.profile['academic_year']={'id':2,'year':2027,'start_month':'202709','end_month':'202806'}
        self.assertEqual(self.client.get(path,headers=headers).json(),[])

    def test_nsp_completion_verifies_student_progress_and_preserves_attendance_history(self):
        headers={'Authorization':'Bearer platform-token','X-Academic-Year':'1'}
        path='/api/nsp/complete'
        self.assertEqual(self.client.post(path,json={'course_id':2}).status_code,403)
        verified={'id':2,'title':index.SEED_DATA['courses'][0]['title'],'completed':True,'completed_at':'2026-10-25T23:10:00+08:00'}
        with patch('api.index.platform_request',return_value=verified) as verify:
            self.assertEqual(self.client.post(path,headers=headers,json={'course_id':'2'}).status_code,422)
            with patch('api.index.platform_request',return_value={**verified,'completed':False}):
                self.assertEqual(self.client.post(path,headers=headers,json={'course_id':2}).status_code,409)
            version,data=index.store.load()
            student=link_student(data,self.profile)
            key=f"{student['id']}_1"
            previous={'status':'SIGNED_IN','signed_at':'2026-09-15 18:00:00','leave_at':'2026-09-14 12:00:00'}
            data['attendances'][key]=previous
            index.store.save(version,data)
            result=self.client.post(path,headers=headers,json={'course_id':2})
            self.assertEqual(result.status_code,200,result.text)
            verify.assert_called_with('/api/nsp/courses/2/completion','platform-token')
            saved=index.store.load()[1]['attendances'][key]
            self.assertEqual(saved['status'],'MAKEUP_DONE')
            self.assertEqual(saved['signed_at'],previous['signed_at'])
            self.assertEqual(saved['leave_at'],previous['leave_at'])
            self.assertEqual(saved['makeup_date'],'2026-10-25 23:10:00')
            version_before=index.store.load()
            self.assertEqual(self.client.post(path,headers=headers,json={'course_id':2}).status_code,200)
            self.assertEqual(index.store.load(),version_before)
            state=self.begin()
            self.client.get('/auth/sso/callback',params={'state':state,'code':'code'},follow_redirects=False)
            self.assertIn('已補課',self.client.get('/dashboard').text)
            with patch('api.index.platform_request',return_value={**verified,'title':'找不到'}):
                self.assertEqual(self.client.post(path,headers=headers,json={'course_id':2}).status_code,409)
            self.assertEqual(self.client.post(path,headers={**headers,'X-Academic-Year':'2'},json={'course_id':2}).status_code,409)
            self.profile.update(account_type='staff',role='admin')
            self.assertEqual(self.client.post(path,headers=headers,json={'course_id':2}).status_code,403)

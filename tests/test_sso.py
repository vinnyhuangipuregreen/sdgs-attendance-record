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
        self.remote.start()
        self.client_context=TestClient(index.app)
        self.client=self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None,None,None)
        self.remote.stop();self.env.stop();self.temp.cleanup()

    def response(self,path,token=None,payload=None):
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
        self.assertEqual(self.client.get("/",follow_redirects=False).headers["location"],"/dashboard")

    def test_csrf_state_tampering_and_expired_session_are_rejected(self):
        self.begin()
        self.assertEqual(self.client.get("/auth/sso/callback",params={"state":"wrong","code":"code"}).status_code,400)
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/auth/sso/callback",params={"state":"wrong","code":"code"}).status_code,400)

    def test_student_cannot_enter_admin_with_sso(self):
        state=self.begin(True)
        self.assertEqual(self.client.get("/auth/sso/callback",params={"state":state,"code":"code"},follow_redirects=False).status_code,403)

    def test_course_record_ids_are_preserved_and_shared_emails_do_not_merge_identities(self):
        data={"students":[{"id":9,"name":"Legacy","email":"student@test.invalid"}],"attendances":{"9_1":{"status":"SIGNED_IN"}}}
        self.assertEqual(link_student(data,self.profile)["id"],9)
        other={**self.profile,"id":8}
        self.assertEqual(link_student(data,other)["id"],10)
        self.assertIn("9_1",data["attendances"])

    def test_revoked_or_unavailable_platform_login_never_falls_back_to_local_accounts(self):
        state=self.begin()
        self.client.get("/auth/sso/callback",params={"state":state,"code":"code"},follow_redirects=False)
        with patch("api.sso.platform_request",side_effect=HTTPException(401,"expired")):
            self.assertEqual(self.client.get("/dashboard",follow_redirects=False).headers["location"],"/sso/login")
        with patch("api.sso.platform_request",side_effect=HTTPException(503,"offline")):
            self.assertEqual(self.client.get("/dashboard",follow_redirects=False).status_code,503)

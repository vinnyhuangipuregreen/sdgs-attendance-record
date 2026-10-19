"""Use the platform's verified account session, with a PKCE browser handoff."""
import base64
import hashlib
import json
import secrets
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import jwt
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

ORIGIN = "https://sdgs-action-platform.vercel.app"
CALLBACK = "https://sdgs-attendance-record.vercel.app/auth/sso/callback"


def platform_request(path, token=None, payload=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(ORIGIN + path, headers=headers,
        data=json.dumps(payload).encode() if payload is not None else None)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in (400, 401, 403, 404):
            raise HTTPException(401, "系統登入已失效，請重新登入") from error
        raise HTTPException(503, "系統登入暫時無法驗證") from error
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise HTTPException(503, "系統登入暫時無法驗證")


def link_student(data, profile):
    key = profile["account_type"] + ":" + str(profile["id"])
    linked = next((student for student in data["students"] if student.get("platform_key") == key), None)
    if not linked:
        emails = {str(profile.get(field) or "").strip().lower() for field in ("email", "email_backup")} - {""}
        matches = [student for student in data["students"] if not student.get("platform_key") and
                   emails.intersection({str(student.get(field) or "").strip().lower() for field in ("email", "email_pathdemy")})]
        if len(matches) == 1:
            linked = matches[0]
        else:
            linked = {"id": max((student["id"] for student in data["students"]), default=0) + 1}
            data["students"].append(linked)
    linked.update({"platform_key": key, "platform_id": profile["id"], "name": profile["name"],
        "email": profile["email"], "email_pathdemy": profile.get("email_backup") or profile["email"],
        "phone": profile.get("phone", ""), "unit_code": profile.get("unit_code", ""), "unit_name": profile.get("unit_name", "")})
    return linked


def resolve_session(request, data, secret):
    request.state.sso_student = None
    request.state.sso_admin = None
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer ") and (request.url.path.startswith("/admin") or request.url.path.startswith("/api/admin")):
        session = {"access_token": authorization[7:]}
    else:
        cookie = request.cookies.get("platform_session")
        if not cookie:
            return
        try:
            session = jwt.decode(cookie, secret, algorithms=["HS256"])
        except jwt.InvalidTokenError:
            return
    needs_roster = request.method == "GET" and bool(re.fullmatch(r"/admin/courses/\d+/(attendance|leaves|pathdemy)", request.url.path))
    needs_roster = needs_roster or (request.method == "POST" and bool(re.fullmatch(r"/api/admin/courses/\d+/pathdemy/import", request.url.path)))
    try:
        if needs_roster:
            context = platform_request("/api/auth/sso/roster?include_profile=true", session["access_token"])
            profile = context["user"]
        else:
            profile = platform_request("/api/auth/me", session["access_token"])
    except HTTPException as error:
        if error.status_code == 401:
            return
        raise
    request.state.platform_token = session["access_token"]
    request.state.academic_year = profile.get("academic_year", {"id":1,"year":2026,"start_month":"202609","end_month":"202706"})
    request.state.platform_profile = profile
    request.state.platform_roster = context["students"] if needs_roster else []
    if profile["account_type"] == "student":
        request.state.sso_student = link_student(data, profile)
    if profile["account_type"] == "staff" and profile["role"] in ("admin", "teacher"):
        request.state.sso_admin = {**profile, "role": "admin", "platform_role": profile["role"], "status": "active"}
        if needs_roster:
            for student in context["students"]:
                link_student(data, student)


def install_sso(app, secret, secure):
    @app.get("/sso/login")
    def start_login(admin: bool = False, academic_year_id: int | None = None):
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        params={"client_id": "attendance", "redirect_uri": CALLBACK, "state": state, "code_challenge": challenge, "login_type": "staff" if admin else "student"}
        if academic_year_id is not None:
            params["academic_year_id"]=academic_year_id
        response = RedirectResponse(ORIGIN + "/sso/authorize?" + urllib.parse.urlencode(params), status_code=302)
        pending = jwt.encode({"state": state, "verifier": verifier, "admin": admin, "exp": int(time.time()) + 300}, secret, algorithm="HS256")
        response.set_cookie("sso_pending", pending, max_age=300, httponly=True, secure=secure, samesite="lax")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/auth/sso/callback")
    async def finish_login(request: Request, code: str, state: str):
        try:
            pending = jwt.decode(request.cookies.get("sso_pending", ""), secret, algorithms=["HS256"])
            if not secrets.compare_digest(state, pending["state"]):
                raise ValueError()
        except (jwt.InvalidTokenError, ValueError, KeyError):
            raise HTTPException(400, "登入要求已失效，請從課程登錄重新開始")
        result = await run_in_threadpool(platform_request, "/api/auth/sso/exchange", None, {
            "client_id": "attendance", "redirect_uri": CALLBACK, "code": code, "code_verifier": pending["verifier"]})
        if pending["admin"] and result["user"]["account_type"] != "staff":
            raise HTTPException(403, "此帳號沒有課程管理權限")
        if not pending["admin"] and result["user"]["account_type"] != "student":
            raise HTTPException(403, "課程登錄請使用學員帳號登入")
        response = RedirectResponse("/admin" if pending["admin"] else "/dashboard", status_code=302)
        session = jwt.encode({"access_token": result["access_token"], "exp": int(time.time()) + 86400 * 7}, secret, algorithm="HS256")
        response.set_cookie("platform_session", session, max_age=86400*7, httponly=True, secure=secure, samesite="lax")
        response.delete_cookie("sso_pending")
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        return response

    @app.get("/sso/logout")
    def logout():
        response = RedirectResponse(ORIGIN + "/?sso_logout=1", status_code=302)
        for key in ("platform_session", "sso_pending", "token", "admin_token"):
            response.delete_cookie(key)
        return response

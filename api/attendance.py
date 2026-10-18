"""Course attendance uses Taiwan time and keeps leave and makeup history."""
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException

TAIWAN = timezone(timedelta(hours=8))


def taiwan_now():
    return datetime.now(TAIWAN)


def course_window(course):
    try:
        day = course["course_date"].replace("/", "-")
        start, end = course["course_time"].split("-")
        starts = datetime.strptime(f"{day} {start.strip()}", "%Y-%m-%d %H:%M").replace(tzinfo=TAIWAN)
        ends = datetime.strptime(f"{day} {end.strip()}", "%Y-%m-%d %H:%M").replace(tzinfo=TAIWAN)
        if ends <= starts:
            raise ValueError()
        return starts, ends
    except (KeyError, ValueError, TypeError, AttributeError):
        raise HTTPException(409, "課程日期或時間設定有誤，請聯絡管理員")


def record_view(course, record, now):
    record = record or {}
    status = record.get("status", "PENDING")
    try:
        _, ends = course_window(course)
        closed = now > ends
    except HTTPException:
        closed = True
    if closed and status == "PENDING":
        status = "ABSENT"
    return {**record, "status": status, "closed": closed,
            "signed_at": record.get("signed_at", ""),
            "leave_at": record.get("leave_at") or (record.get("updated_at", "") if record.get("status") == "ON_LEAVE" else ""),
            "makeup_date": record.get("makeup_date", "")}


def apply_action(data, student, course_id, action, now, reason=""):
    course = next((c for c in data["courses"] if str(c["id"]) == str(course_id)), None)
    if course is None:
        raise HTTPException(404, "查無此課程")
    starts, ends = course_window(course)
    if action == "checkin" and not starts <= now <= ends:
        raise HTTPException(409, f"吉祥！只能在[日期：{course['course_date']}]的[時間：{course['course_time']}]進行簽到。")
    if action != "checkin" and now > ends:
        raise HTTPException(409, "課程已結束，不可請假或補課登錄。")
    key = f"{student['id']}_{course['id']}"
    previous = data["attendances"].get(key, {})
    status = previous.get("status", "PENDING")
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    if action in {"checkin", "leave"} and status not in {"PENDING", "ABSENT"}:
        raise HTTPException(409, "此課程已送出紀錄，不可修改。")
    if action == "checkin":
        record = {"status": "SIGNED_IN", "signed_at": timestamp}
    elif action == "leave":
        if not isinstance(reason, str) or not reason.strip():
            raise HTTPException(422, "請填寫請假原因！")
        record = {"status": "ON_LEAVE", "reason": reason.strip(), "leave_at": timestamp, "updated_at": timestamp}
    else:
        if status != "ON_LEAVE":
            raise HTTPException(409, "必須先完成請假，且不可重複登錄補課。")
        record = {**previous, "status": "MAKEUP_DONE", "leave_at": previous.get("leave_at") or previous.get("updated_at", ""),
                  "makeup_date": timestamp, "platform_email": student.get("email_pathdemy") or student["email"],
                  "note": "自行登錄", "updated_at": timestamp}
    data["attendances"][key] = record
    return {"success": True, "record": record}

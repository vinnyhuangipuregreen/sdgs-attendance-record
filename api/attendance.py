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


def checkin_window(course):
    class_start, class_end = course_window(course)
    try:
        start = course.get("checkin_start") or course.get("open_time", "").split(" ")[-1][:5] or class_start.strftime("%H:%M")
        end = course.get("checkin_end") or class_end.strftime("%H:%M")
        day = course["course_date"].replace("/", "-")
        starts = datetime.strptime(f"{day} {start}", "%Y-%m-%d %H:%M").replace(tzinfo=TAIWAN)
        ends = datetime.strptime(f"{day} {end}", "%Y-%m-%d %H:%M").replace(tzinfo=TAIWAN)
        if ends <= starts:
            if not course.get("checkin_start") and not course.get("checkin_end"):
                return class_start, class_end
            raise ValueError()
        return starts, ends
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(409, "簽到時間設定有誤，請聯絡管理員")


def checkin_message(course, starts, ends):
    return f"吉祥！{course['course_date']} {course['title']}的簽到區間為：{starts:%H:%M}-{ends:%H:%M}。"


def record_view(course, record, now):
    record = record or {}
    status = record.get("status", "PENDING")
    try:
        class_start, ends = course_window(course)
        closed = now > ends
        makeup_available = now.astimezone(TAIWAN).date() > class_start.date() and status != "MAKEUP_DONE"
    except HTTPException:
        closed = True
        makeup_available = False
    if closed and status == "PENDING":
        status = "ABSENT"
    try:
        starts, checkin_ends = checkin_window(course)
        checkin = {"checkin_start": starts.strftime("%H:%M"), "checkin_end": checkin_ends.strftime("%H:%M"),
                   "checkin_starts_at": starts.isoformat(), "checkin_ends_at": checkin_ends.isoformat(),
                   "checkin_closed": now > checkin_ends, "checkin_message": checkin_message(course, starts, checkin_ends)}
    except HTTPException:
        checkin = {"checkin_closed": True}
    return {**record, **checkin, "status": status, "closed": closed, "makeup_available": makeup_available,
            "signed": status == "SIGNED_IN" or bool(record.get("signed_at")),
            "signed_at": record.get("signed_at", ""),
            "leave_at": record.get("leave_at") or (record.get("updated_at", "") if record.get("status") == "ON_LEAVE" else ""),
            "makeup_date": record.get("makeup_date", "")}


def apply_action(data, student, course_id, action, now, reason=""):
    course = next((c for c in data["courses"] if str(c["id"]) == str(course_id)), None)
    if course is None:
        raise HTTPException(404, "查無此課程")
    _, ends = course_window(course)
    if action == "checkin":
        checkin_start, checkin_end = checkin_window(course)
        if not checkin_start <= now <= checkin_end:
            raise HTTPException(409, checkin_message(course, checkin_start, checkin_end))
    if action == "leave" and now > ends:
        raise HTTPException(409, "課程已結束，不可請假。")
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
        if not record_view(course, previous, now)["makeup_available"]:
            raise HTTPException(409, "課程日期隔天起才可補課登錄，且不可重複登錄補課。")
        record = {**previous, "status": "MAKEUP_DONE", "leave_at": previous.get("leave_at") or (previous.get("updated_at", "") if status == "ON_LEAVE" else ""),
                  "makeup_date": timestamp, "platform_email": student.get("email_pathdemy") or student["email"],
                  "note": "自行登錄", "updated_at": timestamp}
    data["attendances"][key] = record
    return {"success": True, "record": record}

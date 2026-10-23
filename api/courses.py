"""Validate course editor values without changing existing attendance records."""
import re
from datetime import datetime
from fastapi import HTTPException
from api.attendance import checkin_window


def course_fields(values, previous=None):
    merged = {**(previous or {}), **values}
    if previous:
        starts, ends = checkin_window(previous)
        merged.setdefault("checkin_start", starts.strftime("%H:%M"))
        merged.setdefault("checkin_end", ends.strftime("%H:%M"))
    fields = ("title", "course_date", "course_time", "checkin_start", "checkin_end")
    if any(not isinstance(merged.get(key), str) or not merged[key].strip() for key in fields):
        raise HTTPException(422, "請完整填寫課程名稱、課程日期、課程時間及簽到時間！")
    result = {key:merged[key].strip() for key in fields}
    try:
        day = datetime.strptime(result["course_date"].replace("/", "-"), "%Y-%m-%d")
        result["course_date"] = day.strftime("%Y/%m/%d")
        class_start, class_end = result["course_time"].split("-")
        for value in (class_start, class_end, result["checkin_start"], result["checkin_end"]):
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):(?:00|30)", value):
                raise ValueError()
        if class_end <= class_start or result["checkin_end"] <= result["checkin_start"]:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "請選擇有效日期及每半小時的時間，結束時間須晚於開始時間。")
    result["open_time"] = day.strftime("%Y-%m-%d") + " " + result["checkin_start"] + ":00"
    return result

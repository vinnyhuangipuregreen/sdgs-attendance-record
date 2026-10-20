"""Validate historical attendance imports before changing any saved records."""
from datetime import datetime
from pydantic import BaseModel, Field
from fastapi import HTTPException


class ImportedRecord(BaseModel):
    student_id: int
    course_id: int
    course_date: str
    signed_at: str = ""
    makeup_date: str = ""
    source: str


class RecordImport(BaseModel):
    version: int
    records: list[ImportedRecord] = Field(min_length=1, max_length=5000)


def import_records(data, records, imported_by):
    students = {s["id"] for s in data["students"]}
    courses = {c["id"]: c for c in data["courses"]}
    updated = dict(data["attendances"])
    seen = set()
    for item in records:
        course = courses.get(item.course_id)
        if item.student_id not in students or course is None:
            raise HTTPException(422, "匯入資料含不存在的學員或課程")
        if course["course_date"].replace("/", "-") != item.course_date:
            raise HTTPException(422, "匯入日期與課程日期不符")
        key = f"{item.student_id}_{item.course_id}"
        if key in seen:
            raise HTTPException(422, "同一學員與課程的紀錄請先合併")
        seen.add(key)
        if not item.signed_at and not item.makeup_date:
            raise HTTPException(422, "匯入資料缺少簽到或補課時間")
        previous = updated.get(key, {})
        record = dict(previous)
        for field in ("signed_at", "makeup_date"):
            value = getattr(item, field)
            if not value:
                continue
            try:
                stamp = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                raise HTTPException(422, "匯入時間格式須為 YYYY-MM-DD HH:MM:SS")
            if field == "signed_at" and stamp.strftime("%Y-%m-%d") != item.course_date:
                raise HTTPException(422, "簽到日期與課程日期不符")
            if previous.get(field) and previous[field] != value:
                raise HTTPException(409, "已有不同時間的紀錄，請先核對再匯入")
            record[field] = value
        record["status"] = "MAKEUP_DONE" if record.get("makeup_date") else (
            previous.get("status") if previous.get("status") == "MAKEUP_DONE" else "SIGNED_IN")
        record.update({"note": "管理員批次匯入", "import_source": item.source, "imported_by": imported_by})
        updated[key] = record
    data["attendances"] = updated
    return len(seen)

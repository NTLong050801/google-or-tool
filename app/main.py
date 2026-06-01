from __future__ import annotations

import os
import zipfile
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .db_reader import (
    db_available,
    load_availability_from_db,
    load_holidays_from_db,
    load_subject_registrations_from_db,
)
from .paths import TermPaths, _default_data_root
from .schemas import GenerateRequest, GenerateResponse, ScheduledSession
from .solver import solve_weekly_timetable
from .timetable_builder import build_generate_request
from .timetable_export import export_timetable_csv
from .timetable_export_class import export_timetable_by_class

load_dotenv()

app = FastAPI(title="Timetable OR-Tools API", version="0.2.0")


# ---------- Auth ----------

def require_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-Key")):
    expected = os.getenv("TIMETABLE_API_KEY", "").strip()
    if not expected:
        raise HTTPException(500, "TIMETABLE_API_KEY chưa cấu hình ở server")
    if not x_api_key or x_api_key.strip() != expected:
        raise HTTPException(401, "Sai hoặc thiếu X-API-Key")
    return True


# ---------- Health ----------

@app.get("/health")
def health():
    return {"ok": True, "db": db_available()}


# ---------- Legacy /generate (giữ tương thích cũ) ----------

@app.post("/generate", response_model=GenerateResponse)
def generate_legacy(req: GenerateRequest) -> GenerateResponse:
    """Endpoint cũ: nhận full payload, trả result. Không touch DB/file."""
    cfg = req.or_tools
    if cfg.periods_per_day <= 0:
        raise HTTPException(422, "periods_per_day must be > 0")
    if not cfg.days:
        raise HTTPException(422, "days must not be empty")

    return solve_weekly_timetable(
        days=cfg.days,
        periods_per_day=cfg.periods_per_day,
        morning_periods=cfg.morning_periods,
        afternoon_periods=cfg.afternoon_periods,
        assignments=req.assignments,
        classrooms=req.classrooms,
    )


# ---------- New /api/timetable/generate ----------

class GenerateTimetableRequest(BaseModel):
    term_code: str = Field(..., description="Mã kỳ, vd '2025_2026_HK2'")
    departments: Optional[List[str]] = Field(
        None, description="Danh sách mã khoa. None = tất cả khoa trong term"
    )
    schoolyear: str = Field(..., description="Năm học để query DB, vd '2025-2026'")
    semester: int = Field(..., description="Học kỳ, 1 hoặc 2")
    only_submitted: bool = Field(
        True, description="Chỉ lấy availability đã submit (không lấy draft)"
    )
    use_db: bool = Field(
        True, description="True = đọc availability/holidays từ DB cdata; False = dùng file CSV"
    )
    max_seconds: Optional[float] = Field(
        None, description="Giới hạn thời gian solver (override config)"
    )


class GenerateTimetableResponse(BaseModel):
    status: str
    sessions: int
    departments: List[str]
    warnings: List[str]
    output_dir: str
    download_url: str


def _build_response(
    result_status: str,
    sessions_count: int,
    departments: List[str],
    warnings: List[str],
    out_dir: Path,
    term_code: str,
) -> GenerateTimetableResponse:
    return GenerateTimetableResponse(
        status=result_status,
        sessions=sessions_count,
        departments=departments,
        warnings=warnings,
        output_dir=str(out_dir),
        download_url=f"/api/timetable/download/{term_code}",
    )


@app.post(
    "/api/timetable/generate",
    response_model=GenerateTimetableResponse,
    dependencies=[Depends(require_api_key)],
)
def generate_timetable(req: GenerateTimetableRequest) -> GenerateTimetableResponse:
    """Chạy full pipeline: build request → assign GV → solver → export Excel.

    Output: app/data/output/<term_code>/by_department/<dept>/timetable_by_class.xlsx
    Có thể download qua GET /api/timetable/download/<term_code>
    """
    avail_override = None
    holidays_override = None
    holiday_reasons_override = None
    teacher_subjects_override = None

    if req.use_db:
        try:
            avail_override = load_availability_from_db(
                req.schoolyear, req.semester, only_submitted=req.only_submitted
            )
            holidays_override, holiday_reasons_override = load_holidays_from_db(
                req.schoolyear, req.semester
            )
            teacher_subjects_override = load_subject_registrations_from_db(
                req.schoolyear, req.semester, only_submitted=req.only_submitted
            )
        except Exception as e:
            raise HTTPException(503, f"Không kết nối được DB cdata: {e}")

    br = build_generate_request(
        _default_data_root(),
        term_code=req.term_code,
        departments=req.departments,
        availability_override=avail_override,
        holidays_override=holidays_override,
        holiday_reasons_override=holiday_reasons_override,
        teacher_subjects_override=teacher_subjects_override,
    )

    cfg = br.config
    max_seconds = req.max_seconds if req.max_seconds else cfg.max_time_seconds

    res = solve_weekly_timetable(
        days=br.request.or_tools.days,
        periods_per_day=br.request.or_tools.periods_per_day,
        morning_periods=br.request.or_tools.morning_periods,
        afternoon_periods=br.request.or_tools.afternoon_periods,
        assignments=br.request.assignments,
        classrooms=br.request.classrooms,
        availability=br.availability,
        assignment_labels=br.assignment_labels,
        config=cfg,
        holiday_weeks=br.holidays,
        max_time_seconds=float(max_seconds),
    )

    tp = TermPaths(data_root=_default_data_root(), term_code=req.term_code)
    out_dir = tp.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    all_warnings = br.warnings + [f"[solver] {w}" for w in res.warnings]
    if all_warnings:
        (out_dir / "warnings.txt").write_text("\n".join(all_warnings), encoding="utf-8")

    departments_used: List[str] = []
    if res.status in ("OPTIMAL", "FEASIBLE") and res.sessions:
        sessions_by_dept = defaultdict(list)
        for s in res.sessions:
            sessions_by_dept[str(s.department_code).strip() or "_unknown"].append(s)

        by_dept_dir = out_dir / "by_department"
        for dept_code, dept_sessions in sorted(sessions_by_dept.items()):
            dept_out = by_dept_dir / dept_code
            dept_out.mkdir(parents=True, exist_ok=True)

            export_timetable_csv(
                dept_sessions,
                br.request.assignments,
                br.assignment_labels,
                br.request.classrooms,
                morning_periods=br.request.or_tools.morning_periods,
                csv_path=dept_out / "timetable.csv",
            )
            export_timetable_by_class(
                dept_sessions,
                br.request.assignments,
                br.assignment_labels,
                br.request.classrooms,
                days=br.request.or_tools.days,
                periods_per_day=br.request.or_tools.periods_per_day,
                morning_periods=br.request.or_tools.morning_periods,
                xlsx_path=dept_out / "timetable_by_class.xlsx",
                week_dates=br.week_dates,
                holiday_reasons=br.holiday_reasons,
            )
            departments_used.append(dept_code)

    return _build_response(
        res.status, len(res.sessions), departments_used, all_warnings, out_dir, req.term_code
    )


@app.get(
    "/api/timetable/download/{term_code}",
    dependencies=[Depends(require_api_key)],
)
def download_outputs(term_code: str):
    """Tải toàn bộ output kỳ về dưới dạng ZIP."""
    tp = TermPaths(data_root=_default_data_root(), term_code=term_code)
    out_dir = tp.output_dir
    if not out_dir.is_dir():
        raise HTTPException(404, f"Không có output cho term {term_code}")

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in out_dir.rglob("*"):
            if f.is_file():
                zf.write(f, arcname=f.relative_to(out_dir))
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="timetable_{term_code}.zip"'
        },
    )


@app.get(
    "/api/timetable/download/{term_code}/{dept}/timetable_by_class.xlsx",
    dependencies=[Depends(require_api_key)],
)
def download_dept_xlsx(term_code: str, dept: str):
    """Tải file xlsx của 1 khoa."""
    tp = TermPaths(data_root=_default_data_root(), term_code=term_code)
    f = tp.output_dir / "by_department" / dept / "timetable_by_class.xlsx"
    if not f.is_file():
        raise HTTPException(404, "Không có file")
    return FileResponse(
        f,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"timetable_{term_code}_{dept}.xlsx",
    )

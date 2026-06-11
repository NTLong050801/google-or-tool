"""CLI: đọc dữ liệu trong app/data, ghép Assignment và chạy CP-SAT."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv
load_dotenv()

from .db_reader import (
    db_available,
    load_availability_from_db,
    load_class_excluded_weeks_from_db,
    load_class_week_starts_from_db,
    load_holidays_from_db,
    load_subject_registrations_from_db,
)
from .paths import TermPaths, _default_data_root
from .schemas import ScheduledSession
from .solver import solve_weekly_timetable
from .timetable_builder import build_generate_request
from .timetable_export import export_timetable_csv
from .timetable_export_class import export_timetable_by_class
from .timetable_export_class_pdt import export_timetable_by_class_pdt
from .timetable_export_teacher import export_timetable_by_teacher
from .availability_report import export_availability_report
from .dept_output import (
    write_per_dept_assignment_log,
    write_per_dept_warnings,
)


def _write_assignment_log(path: Path, rows: List[Dict]) -> None:
    """Xuất log phân công GV → 1 sheet/khoa (overview tổng)."""
    import pandas as pd
    by_dept: Dict[str, List[Dict]] = defaultdict(list)
    for r in rows:
        by_dept[str(r.get("Khoa", "")).strip() or "_unknown"].append(r)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for dept, dept_rows in sorted(by_dept.items()):
            df = pd.DataFrame(dept_rows)
            sheet_name = (dept or "khoa")[:31]
            df.to_excel(writer, sheet_name=sheet_name, index=False)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Xếp TKB cục bộ từ app/data")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="Thư mục gốc chứa shared/, terms/… (mặc định app/data)",
    )
    parser.add_argument(
        "--term",
        type=str,
        default="2025_2026_HK2",
        help="Mã học kỳ (tên thư mục trong terms/)",
    )
    parser.add_argument(
        "--departments",
        type=str,
        default=None,
        help="Danh sách khoa cách nhau bởi dấu phẩy (mặc định: tất cả khoa trong term)",
    )
    parser.add_argument("--seconds", type=float, default=None, help="Giới hạn thời gian solver (override config)")
    parser.add_argument(
        "--use-db", action="store_true", default=True,
        help="Đọc availability/holidays/excluded-weeks từ DB (mặc định: True nếu DB available)",
    )
    parser.add_argument(
        "--no-db", action="store_true", default=False,
        help="Bỏ qua DB, chỉ dùng file CSV",
    )
    parser.add_argument(
        "--schoolyear", type=str, default="2025-2026",
        help="Năm học để query DB (mặc định: 2025-2026)",
    )
    parser.add_argument(
        "--semester", type=int, default=2,
        help="Học kỳ để query DB (mặc định: 2)",
    )
    args = parser.parse_args()

    data_root = args.data_root or _default_data_root()
    depts = [d.strip() for d in args.departments.split(",")] if args.departments else None
    use_db = (not args.no_db) and db_available()

    avail_override = None
    holidays_override = None
    holiday_reasons_override = None
    teacher_subjects_override = None
    class_excluded_override = None
    class_week_starts_override = None

    if use_db:
        print(f"DB available — đọc availability/holidays/excluded-weeks từ DB ({args.schoolyear} HK{args.semester})")
        try:
            avail_override = load_availability_from_db(args.schoolyear, args.semester, only_submitted=True)
            holidays_override, holiday_reasons_override = load_holidays_from_db(args.schoolyear, args.semester)
            teacher_subjects_override = load_subject_registrations_from_db(args.schoolyear, args.semester, only_submitted=True)
            class_excluded_override = load_class_excluded_weeks_from_db(args.schoolyear, args.semester)
            class_week_starts_override = load_class_week_starts_from_db(args.schoolyear, args.semester)
        except Exception as e:
            print(f"WARN: Không thể đọc DB ({e}) — fallback sang file CSV")
            avail_override = holidays_override = holiday_reasons_override = None
            teacher_subjects_override = class_excluded_override = class_week_starts_override = None
    else:
        print("Dùng file CSV (--no-db hoặc DB không available)")

    br = build_generate_request(
        data_root,
        term_code=args.term,
        departments=depts,
        availability_override=avail_override,
        holidays_override=holidays_override,
        holiday_reasons_override=holiday_reasons_override,
        teacher_subjects_override=teacher_subjects_override,
        class_excluded_weeks_override=class_excluded_override,
        class_week_starts_override=class_week_starts_override,
    )

    cfg = br.config
    max_seconds = args.seconds if args.seconds else cfg.max_time_seconds

    for w in br.warnings[:80]:
        print("WARN:", w)
    if len(br.warnings) > 80:
        print(f"... và {len(br.warnings) - 80} cảnh báo khác")

    print(
        f"assignments={len(br.request.assignments)} "
        f"skipped_rows={br.skipped_rows} "
        f"classrooms={len(br.request.classrooms)} "
        f"teachers_with_availability={len(br.availability)}"
    )

    req = br.request
    res = solve_weekly_timetable(
        days=req.or_tools.days,
        periods_per_day=req.or_tools.periods_per_day,
        morning_periods=req.or_tools.morning_periods,
        afternoon_periods=req.or_tools.afternoon_periods,
        assignments=req.assignments,
        classrooms=req.classrooms,
        availability=br.availability,
        assignment_labels=br.assignment_labels,
        config=cfg,
        holiday_weeks=br.holidays,
        holiday_reasons=br.holiday_reasons,
        max_time_seconds=float(max_seconds),
    )

    tp = TermPaths(data_root=data_root, term_code=args.term)
    out_dir = tp.output_dir
    import shutil
    if out_dir.exists():
        shutil.rmtree(out_dir)
        print(f"Đã xóa output cũ: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    by_dept_dir = out_dir / "by_department"

    # Danh sách khoa: ưu tiên --departments, fallback từ assignments
    if depts:
        dept_codes = depts
    else:
        dept_codes = sorted({
            (br.assignment_labels.get(a.id, {}).get("department_code") or "_unknown")
            for a in req.assignments
        })

    # Xuất log phân công GV
    if br.assigner_log:
        _write_assignment_log(out_dir / "assignment_log.xlsx", br.assigner_log)
        print("written:", out_dir / "assignment_log.xlsx", f"({len(br.assigner_log)} rows tổng)")
        # Per-dept assignment_log
        per_dept_counts = write_per_dept_assignment_log(by_dept_dir, br.assigner_log, dept_codes)
        for d, n in sorted(per_dept_counts.items()):
            print(f"written: [{d}] assignment_log.xlsx ({n} rows)")

    # warnings.txt — tổng
    all_warnings = br.warnings + [f"[solver] {w}" for w in res.warnings]
    if all_warnings:
        warnings_path = out_dir / "warnings.txt"
        warnings_path.write_text("\n".join(all_warnings), encoding="utf-8")
        # Per-dept warnings.txt
        per_dept_warn_counts = write_per_dept_warnings(by_dept_dir, all_warnings, dept_codes)
        for d, n in sorted(per_dept_warn_counts.items()):
            print(f"written: [{d}] warnings.txt ({n} dòng)")

    # availability_report.csv — báo cáo GV chưa/thiếu đăng ký, theo khoa
    if res.availability_report and res.availability_report.has_issues():
        n = export_availability_report(
            res.availability_report,
            out_dir / "availability_report.csv",
        )
        print("written:", out_dir / "availability_report.csv", f"({n} GV có vấn đề)")

    print("status:", res.status)
    if res.message:
        print("message:", res.message)
    for w in res.warnings:
        print("WARN [solver]:", w)
    print(f"sessions={len(res.sessions)}")
    if all_warnings:
        print("written:", out_dir / "warnings.txt", f"({len(all_warnings)} warnings)")

    if not (res.status in ("OPTIMAL", "FEASIBLE") and res.sessions):
        return

    # Tách sessions theo khoa rồi export riêng từng khoa vào by_department/<khoa>/
    sessions_by_dept: Dict[str, List[ScheduledSession]] = defaultdict(list)
    for s in res.sessions:
        sessions_by_dept[str(s.department_code).strip() or "_unknown"].append(s)

    for dept_code, dept_sessions in sorted(sessions_by_dept.items()):
        dept_out = by_dept_dir / dept_code
        dept_out.mkdir(parents=True, exist_ok=True)

        export_timetable_csv(
            dept_sessions,
            req.assignments,
            br.assignment_labels,
            req.classrooms,
            morning_periods=req.or_tools.morning_periods,
            csv_path=dept_out / "timetable.csv",
        )
        export_timetable_by_class(
            dept_sessions,
            req.assignments,
            br.assignment_labels,
            req.classrooms,
            days=req.or_tools.days,
            periods_per_day=req.or_tools.periods_per_day,
            morning_periods=req.or_tools.morning_periods,
            xlsx_path=dept_out / "timetable_by_class.xlsx",
            week_dates=br.week_dates,
            holiday_reasons=br.holiday_reasons,
            class_excluded_weeks=br.class_excluded_weeks,
        )
        export_timetable_by_class_pdt(
            dept_sessions,
            req.assignments,
            br.assignment_labels,
            req.classrooms,
            days=req.or_tools.days,
            periods_per_day=req.or_tools.periods_per_day,
            morning_periods=req.or_tools.morning_periods,
            xlsx_path=dept_out / "timetable_by_class_pdt.xlsx",
            term_code=args.term,
            department_code=dept_code,
            week_dates=br.week_dates,
            holiday_reasons=br.holiday_reasons,
            class_excluded_weeks=br.class_excluded_weeks,
            class_week_starts=br.class_week_starts,
        )
        n_teachers = export_timetable_by_teacher(
            dept_sessions,
            req.assignments,
            br.assignment_labels,
            req.classrooms,
            days=req.or_tools.days,
            periods_per_day=req.or_tools.periods_per_day,
            morning_periods=req.or_tools.morning_periods,
            xlsx_path=dept_out / "timetable_by_teacher.xlsx",
            week_dates=br.week_dates,
            holiday_reasons=br.holiday_reasons,
            class_excluded_weeks=br.class_excluded_weeks,
        )
        print(f"written: [{dept_code}] {len(dept_sessions)} sessions → {dept_out}/ ({n_teachers} GV)")


if __name__ == "__main__":
    main()

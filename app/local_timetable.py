"""CLI: đọc dữ liệu trong app/data, ghép Assignment và chạy CP-SAT."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

from .paths import TermPaths, _default_data_root
from .schemas import ScheduledSession
from .solver import solve_weekly_timetable
from .timetable_builder import build_generate_request
from .timetable_export import export_timetable_csv
from .timetable_export_class import export_timetable_by_class


def _write_assignment_log(path: Path, rows: List[Dict]) -> None:
    """Xuất log phân công GV → 1 sheet/khoa."""
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
    args = parser.parse_args()

    data_root = args.data_root or _default_data_root()
    depts = [d.strip() for d in args.departments.split(",")] if args.departments else None

    br = build_generate_request(
        data_root,
        term_code=args.term,
        departments=depts,
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
        max_time_seconds=float(max_seconds),
    )

    tp = TermPaths(data_root=data_root, term_code=args.term)
    out_dir = tp.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Xuất log phân công GV → mỗi khoa 1 sheet trong assignment_log.xlsx
    if br.assigner_log:
        _write_assignment_log(out_dir / "assignment_log.xlsx", br.assigner_log)
        print("written:", out_dir / "assignment_log.xlsx", f"({len(br.assigner_log)} rows)")

    # Gộp warnings → 1 file (chung cho tất cả khoa)
    all_warnings = br.warnings + [f"[solver] {w}" for w in res.warnings]
    if all_warnings:
        warnings_path = out_dir / "warnings.txt"
        warnings_path.write_text("\n".join(all_warnings), encoding="utf-8")

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

    by_dept_dir = out_dir / "by_department"
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
        print(f"written: [{dept_code}] {len(dept_sessions)} sessions → {dept_out}/")


if __name__ == "__main__":
    main()

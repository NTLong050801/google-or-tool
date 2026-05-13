"""CLI: đọc dữ liệu trong app/data, ghép Assignment và chạy CP-SAT."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .solver import solve_weekly_timetable
from .timetable_builder import build_generate_request
from .timetable_export import export_timetable_csv


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
        help="Thư mục chứa classes_project.xls, projects.xls, cleans/… (mặc định app/data)",
    )
    parser.add_argument("--seconds", type=float, default=120.0, help="Giới hạn thời gian solver")
    parser.add_argument(
        "--minutes-per-lesson",
        type=int,
        default=50,
        help="Quy đổi 'giờ' trong Excel sang tiết (phút/tiết)",
    )
    parser.add_argument(
        "--lessons-cluster",
        type=int,
        default=5,
        help="Số tiết/buổi (phải khớp khối sáng-chiều; mặc định 5 tiết/buổi)",
    )
    args = parser.parse_args()

    br = build_generate_request(
        args.data_root,
        minutes_per_lesson=args.minutes_per_lesson,
        lessons_cluster=args.lessons_cluster,
    )

    for w in br.warnings[:80]:
        print("WARN:", w)
    if len(br.warnings) > 80:
        print(f"... và {len(br.warnings) - 80} cảnh báo khác")

    print(
        f"assignments={len(br.request.assignments)} "
        f"skipped_rows={br.skipped_rows} "
        f"classrooms={len(br.request.classrooms)}"
    )

    req = br.request
    res = solve_weekly_timetable(
        days=req.or_tools.days,
        periods_per_day=req.or_tools.periods_per_day,
        morning_periods=req.or_tools.morning_periods,
        afternoon_periods=req.or_tools.afternoon_periods,
        assignments=req.assignments,
        classrooms=req.classrooms,
        max_time_seconds=float(args.seconds),
    )

    root = args.data_root or (Path(__file__).resolve().parent / "data")
    out_dir = root / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "timetable_result.json"
    out_json.write_text(res.model_dump_json(indent=2), encoding="utf-8")

    if res.status in ("OPTIMAL", "FEASIBLE") and res.sessions:
        export_timetable_csv(
            list(res.sessions),
            req.assignments,
            br.assignment_labels,
            req.classrooms,
            morning_periods=req.or_tools.morning_periods,
            csv_path=out_dir / "timetable.csv",
        )

    print("status:", res.status)
    if res.message:
        print("message:", res.message)
    print(f"sessions={len(res.sessions)}")
    print("written:", out_json)
    if res.status in ("OPTIMAL", "FEASIBLE") and res.sessions:
        print("written:", out_dir / "timetable.csv")


if __name__ == "__main__":
    main()

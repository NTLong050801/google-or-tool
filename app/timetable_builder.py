from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import pandas as pd

from .schemas import Assignment, GenerateRequest, ORToolsConfig
from .timetable_loader import (
    fold_teacher_cell,
    load_class_group_ids,
    load_classrooms_from_csv,
    load_classes_project,
    load_teacher_aliases,
    load_teacher_display_map,
    load_teacher_lookup,
    norm_subject_code,
    parse_projects_catalog,
    week_bounds_from_csv,
)
from .timetable_skip_subjects import is_excluded_subject


@dataclass
class BuildResult:
    request: GenerateRequest
    warnings: List[str] = field(default_factory=list)
    skipped_rows: int = 0
    # assignment_id -> nhãn hiển thị (lớp, môn, GV)
    assignment_labels: Dict[int, Dict[str, str]] = field(default_factory=dict)


def _sessions_from_hours(
    total_hours: float,
    num_weeks: int,
    lessons_cluster: int,
    minutes_per_lesson: int,
) -> int:
    total_periods = max(1, round(float(total_hours) * 60 / minutes_per_lesson))
    periods_pw = max(1, math.ceil(total_periods / max(1, num_weeks)))
    sess = math.ceil(periods_pw / max(1, lessons_cluster))
    return min(20, max(1, sess))


def build_generate_request(
    data_root: Path | None = None,
    *,
    minutes_per_lesson: int = 50,
    lessons_cluster: int = 5,
    days: List[int] | None = None,
) -> BuildResult:
    """
    Ghép cleans/*.csv + classes_project.xls + projects.xls -> GenerateRequest.

    data_root mặc định: app/data/
    """
    root = data_root or (Path(__file__).resolve().parent / "data")
    cleans = root / "cleans"
    warnings: List[str] = []
    skipped = 0

    projects_types = parse_projects_catalog(root / "projects.xls")
    df = load_classes_project(root / "classes_project.xls")

    col_class = "Class"
    col_code = "Mã môn học"
    col_name = "Tên môn học"
    col_hours = "Tổng số giờ"
    col_teacher = "Giảng viên"

    class_to_gid = load_class_group_ids(cleans / "classes.csv")
    teachers = load_teacher_lookup(cleans / "teachers.csv")
    for fk, tid in load_teacher_aliases(cleans / "teacher_aliases.csv").items():
        teachers[fk] = tid
    teacher_display = load_teacher_display_map(cleans / "teachers.csv")
    classrooms = load_classrooms_from_csv(cleans / "rooms.csv")
    week_lo, week_hi = week_bounds_from_csv(cleans / "weeks.csv")
    num_weeks = max(1, week_hi - week_lo + 1)

    if days is None:
        days = [2, 3, 4, 5, 6, 7, 8]

    assignments: List[Assignment] = []
    assignment_labels: Dict[int, Dict[str, str]] = {}
    aid = 1

    for _, row in df.iterrows():
        class_name = str(row.get(col_class, "")).strip()
        subj_name = str(row.get(col_name, "")).strip()
        code_raw = str(row.get(col_code, "")).strip()

        if not class_name or not code_raw:
            skipped += 1
            continue

        if class_name not in class_to_gid:
            skipped += 1
            warnings.append(f"Bỏ qua lớp không nằm trong cleans/classes.csv: {class_name} | {code_raw}")
            continue

        if is_excluded_subject(subj_name):
            skipped += 1
            continue

        teacher_cell = row.get(col_teacher)
        if pd.isna(teacher_cell):
            skipped += 1
            warnings.append(f"Thiếu GV: {class_name} | {subj_name}")
            continue
        teacher_raw = str(teacher_cell).strip()
        if not teacher_raw:
            skipped += 1
            warnings.append(f"Thiếu GV: {class_name} | {subj_name}")
            continue

        tkey = fold_teacher_cell(teacher_raw)
        teacher_id = teachers.get(tkey)
        if not teacher_id:
            warnings.append(f"Không khớp tên GV trong teachers.csv: '{teacher_raw}' ({class_name} / {code_raw})")
            skipped += 1
            continue

        try:
            total_hours = float(row.get(col_hours, 0) or 0)
        except (TypeError, ValueError):
            total_hours = 0.0
        if total_hours <= 0:
            warnings.append(f"Tổng giờ không hợp lệ: {class_name} | {code_raw}")
            skipped += 1
            continue

        ncode = norm_subject_code(code_raw)
        classroom_type = projects_types.get(ncode, 1)

        sessions_pw = _sessions_from_hours(
            total_hours, num_weeks, lessons_cluster, minutes_per_lesson
        )

        gid = int(class_to_gid[class_name])
        assignments.append(
            Assignment(
                id=aid,
                teacher_id=teacher_id,
                course_id=aid,
                class_group_id=gid,
                classroom_type=int(classroom_type),
                sessions_per_week=int(sessions_pw),
                lessons_cluster=int(lessons_cluster),
                week_start=int(week_lo),
                week_end=int(week_hi),
            )
        )
        assignment_labels[aid] = {
            "class_name": class_name,
            "subject_code": code_raw,
            "subject_name": subj_name,
            "teacher_id": str(teacher_id),
            "teacher_name": teacher_display.get(str(teacher_id), str(teacher_id)),
        }
        aid += 1

    if not classrooms:
        warnings.append("Không có phòng khả dụng sau lọc rooms.csv")

    # 10 tiết/ngày: sáng 1–5, chiều 6–10 — mỗi buổi học đủ một khối (solver ép lessons_cluster = 5).
    cfg = ORToolsConfig(
        days=days,
        periods_per_day=10,
        morning_periods=[1, 2, 3, 4, 5],
        afternoon_periods=[6, 7, 8, 9, 10],
    )

    req = GenerateRequest(or_tools=cfg, assignments=assignments, classrooms=classrooms)
    return BuildResult(
        request=req,
        warnings=warnings,
        skipped_rows=skipped,
        assignment_labels=assignment_labels,
    )

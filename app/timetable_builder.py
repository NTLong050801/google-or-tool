from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .paths import DepartmentPaths, TermPaths, _default_data_root
from .schemas import Assignment, Classroom, GenerateRequest, ORToolsConfig
from .timetable_loader import (
    TeacherBusyEntry,
    fold_teacher_cell,
    load_class_group_ids,
    load_classrooms_from_csv,
    load_classes_project,
    load_teacher_aliases,
    load_teacher_busy,
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
    assignment_labels: Dict[int, Dict[str, str]] = field(default_factory=dict)
    teacher_busy: List[TeacherBusyEntry] = field(default_factory=list)


def _sessions_from_hours(
    total_hours: float,
    lessons_cluster: int,
    sessions_per_week_override: int | None = None,
) -> tuple[int, int]:
    """Trả về (sessions_per_week, total_sessions).

    Quy tắc mặc định nếu không có override:
      ≤ 45 giờ  → 1 buổi/tuần
      46–90     → 2 buổi/tuần
      91–150    → 3 buổi/tuần
      > 150     → 4 buổi/tuần
    """
    total_sessions = max(1, math.ceil(float(total_hours) / max(1, lessons_cluster)))

    if sessions_per_week_override and sessions_per_week_override > 0:
        spw = sessions_per_week_override
    else:
        if total_hours <= 45:
            spw = 1
        elif total_hours <= 90:
            spw = 2
        elif total_hours <= 150:
            spw = 3
        else:
            spw = 4

    spw = min(spw, total_sessions)
    return (min(20, max(1, spw)), total_sessions)


def _build_department_assignments(
    dept_paths: DepartmentPaths,
    week_lo: int,
    week_hi: int,
    lessons_cluster: int,
    term_code: str,
    aid_start: int,
) -> tuple[List[Assignment], Dict[int, Dict[str, str]], List[str], int]:
    """Build assignments cho 1 khoa. Trả về (assignments, labels, warnings, skipped)."""
    warnings: List[str] = []
    skipped = 0
    assignments: List[Assignment] = []
    assignment_labels: Dict[int, Dict[str, str]] = {}
    aid = aid_start
    dept_code = dept_paths.dept_code

    projects_types = parse_projects_catalog(dept_paths.projects_xls)
    df = load_classes_project(dept_paths.classes_project_xls)

    col_class = "Class"
    col_code = "Mã môn học"
    col_name = "Tên môn học"
    col_hours = "Tổng số giờ"
    col_teacher = "Giảng viên"
    col_spw = "Số buổi/tuần"

    has_spw_col = col_spw in df.columns

    class_to_gid = load_class_group_ids(dept_paths.classes_csv)
    teachers = load_teacher_lookup(dept_paths.teachers_csv)
    for fk, tid in load_teacher_aliases(dept_paths.teacher_aliases_csv).items():
        teachers[fk] = tid
    teacher_display = load_teacher_display_map(dept_paths.teachers_csv)

    for _, row in df.iterrows():
        class_name = str(row.get(col_class, "")).strip()
        subj_name = str(row.get(col_name, "")).strip()
        code_raw = str(row.get(col_code, "")).strip()

        if not class_name or not code_raw:
            skipped += 1
            continue

        if class_name not in class_to_gid:
            skipped += 1
            warnings.append(f"[{dept_code}] Bỏ qua lớp không nằm trong classes.csv: {class_name} | {code_raw}")
            continue

        if is_excluded_subject(subj_name):
            skipped += 1
            continue

        teacher_cell = row.get(col_teacher)
        if pd.isna(teacher_cell):
            skipped += 1
            warnings.append(f"[{dept_code}] Thiếu GV: {class_name} | {subj_name}")
            continue
        teacher_raw = str(teacher_cell).strip()
        if not teacher_raw:
            skipped += 1
            warnings.append(f"[{dept_code}] Thiếu GV: {class_name} | {subj_name}")
            continue

        tkey = fold_teacher_cell(teacher_raw)
        teacher_id = teachers.get(tkey)
        if not teacher_id:
            warnings.append(f"[{dept_code}] Không khớp tên GV: '{teacher_raw}' ({class_name} / {code_raw})")
            skipped += 1
            continue

        try:
            total_hours = float(row.get(col_hours, 0) or 0)
        except (TypeError, ValueError):
            total_hours = 0.0
        if total_hours <= 0:
            warnings.append(f"[{dept_code}] Tổng giờ không hợp lệ: {class_name} | {code_raw}")
            skipped += 1
            continue

        ncode = norm_subject_code(code_raw)
        classroom_type = projects_types.get(ncode, 1)

        spw_override = None
        if has_spw_col:
            spw_raw = row.get(col_spw)
            if not pd.isna(spw_raw):
                try:
                    spw_override = int(float(spw_raw))
                except (TypeError, ValueError):
                    pass

        sessions_pw, total_sessions = _sessions_from_hours(
            total_hours, lessons_cluster, spw_override
        )
        num_weeks_needed = math.ceil(total_sessions / sessions_pw)
        a_week_end = min(week_hi, week_lo + num_weeks_needed - 1)

        gid = int(class_to_gid[class_name])
        assignments.append(
            Assignment(
                id=aid,
                teacher_id=teacher_id,
                course_id=code_raw,
                class_group_id=gid,
                classroom_type=int(classroom_type),
                sessions_per_week=int(sessions_pw),
                lessons_cluster=int(lessons_cluster),
                week_start=int(week_lo),
                week_end=int(a_week_end),
                department_code=dept_code,
                term_code=term_code,
            )
        )
        assignment_labels[aid] = {
            "class_name": class_name,
            "subject_code": code_raw,
            "subject_name": subj_name,
            "teacher_id": str(teacher_id),
            "teacher_name": teacher_display.get(str(teacher_id), str(teacher_id)),
            "department_code": dept_code,
        }
        aid += 1

    return assignments, assignment_labels, warnings, skipped


def build_generate_request(
    data_root: Optional[Path] = None,
    *,
    term_code: str = "2025_2026_HK2",
    departments: Optional[List[str]] = None,
    lessons_cluster: int = 5,
    days: Optional[List[int]] = None,
) -> BuildResult:
    """
    Ghép data từ shared + departments -> GenerateRequest.

    departments=None → tự detect tất cả khoa trong term.
    """
    root = data_root or _default_data_root()
    tp = TermPaths(data_root=root, term_code=term_code)

    if departments is None:
        departments = tp.list_departments()
    if not departments:
        raise ValueError(f"Không tìm thấy khoa nào trong {tp.departments_dir}")

    warnings: List[str] = []
    total_skipped = 0

    classrooms = load_classrooms_from_csv(tp.rooms_csv)
    week_lo, week_hi = week_bounds_from_csv(tp.weeks_csv)

    if days is None:
        days = [2, 3, 4, 5, 6, 7, 8]

    all_assignments: List[Assignment] = []
    all_labels: Dict[int, Dict[str, str]] = {}
    all_teacher_busy: List[TeacherBusyEntry] = []
    aid = 1

    for dept_code in departments:
        dp = tp.department(dept_code)
        if not dp.classes_project_xls.is_file():
            warnings.append(f"[{dept_code}] Không tìm thấy {dp.classes_project_xls}")
            continue

        dept_assignments, dept_labels, dept_warnings, dept_skipped = _build_department_assignments(
            dept_paths=dp,
            week_lo=week_lo,
            week_hi=week_hi,
            lessons_cluster=lessons_cluster,
            term_code=term_code,
            aid_start=aid,
        )
        all_assignments.extend(dept_assignments)
        all_labels.update(dept_labels)
        warnings.extend(dept_warnings)
        total_skipped += dept_skipped
        if dept_assignments:
            aid = max(a.id for a in dept_assignments) + 1

        dept_busy = load_teacher_busy(dp.teacher_busy_csv, week_lo, week_hi)
        all_teacher_busy.extend(dept_busy)

    if not classrooms:
        warnings.append("Không có phòng khả dụng sau lọc rooms.csv")

    cfg = ORToolsConfig(
        days=days,
        periods_per_day=10,
        morning_periods=[1, 2, 3, 4, 5],
        afternoon_periods=[6, 7, 8, 9, 10],
    )

    req = GenerateRequest(or_tools=cfg, assignments=all_assignments, classrooms=classrooms)
    return BuildResult(
        request=req,
        warnings=warnings,
        skipped_rows=total_skipped,
        assignment_labels=all_labels,
        teacher_busy=all_teacher_busy,
    )

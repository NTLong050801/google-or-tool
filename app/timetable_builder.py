"""Builder mới: đọc dao_tao_*.xlsx + availability.csv + shared/teachers.xlsx → GenerateRequest."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from .paths import DepartmentPaths, TermPaths, _default_data_root
from .scheduling_config import SchedulingConfig, load_config
from .schemas import Assignment, Classroom, GenerateRequest, ORToolsConfig
from .teacher_assigner import (
    AssignmentResult,
    SubjectClassDemand,
    TeacherInfo,
    assign_teachers,
)
from .timetable_loader import (
    load_class_excluded_weeks_csv,
    load_class_week_starts_csv,
    load_class_sizes,
    load_classrooms_from_csv,
    load_holidays,
    load_holiday_reasons,
    load_teacher_subjects,
    load_week_dates,
    week_bounds_from_csv,
)
from .timetable_skip_subjects import is_excluded_subject


@dataclass
class BuildResult:
    request: GenerateRequest
    warnings: List[str] = field(default_factory=list)
    skipped_rows: int = 0
    assignment_labels: Dict[int, Dict[str, str]] = field(default_factory=dict)
    availability: Dict[str, Set[Tuple[int, int, int]]] = field(default_factory=dict)
    config: SchedulingConfig = field(default_factory=SchedulingConfig)
    holidays: Set[int] = field(default_factory=set)
    week_dates: Dict[int, Tuple[str, str]] = field(default_factory=dict)
    holiday_reasons: Dict[int, str] = field(default_factory=dict)
    assigner_log: List[Dict] = field(default_factory=list)
    class_excluded_weeks: Dict[str, Dict[int, str]] = field(default_factory=dict)
    class_week_starts: Dict[str, int] = field(default_factory=dict)


# --- Helpers ---

def _parse_date_mdy(s: str) -> Optional[datetime]:
    """Parse date M/D/YYYY (availability format)."""
    try:
        return datetime.strptime(s.strip(), "%m/%d/%Y")
    except (ValueError, AttributeError):
        try:
            return datetime.strptime(s.strip(), "%m/%d/%y")
        except (ValueError, AttributeError):
            return None


def _build_week_date_map(weeks_csv: Path) -> Dict[str, int]:
    """Map from_date → week_order. Supports both YYYY-MM-DD and M/D/YYYY lookup."""
    import csv
    date_to_week: Dict[str, int] = {}
    with weeks_csv.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            wo = (row.get("week_order") or "").strip()
            fd = (row.get("from_date") or "").strip()
            if wo.isdigit() and fd:
                date_to_week[fd] = int(wo)
                try:
                    dt = datetime.strptime(fd, "%Y-%m-%d")
                    date_to_week[f"{dt.month}/{dt.day}/{dt.year}"] = int(wo)
                except ValueError:
                    pass
    return date_to_week


DAY_MAP = {"mon": 2, "tue": 3, "wed": 4, "thu": 5, "fri": 6, "sat": 7, "sun": 8}
ROOM_TYPE_MAP = {"LT-Lý thuyết": 1, "TH-Thực hành tin học": 2, "K-Khác": 4, "LT": 1, "TH": 2, "K": 4}


def _sessions_from_hours(
    total_hours: float,
    lessons_cluster: int,
    sessions_per_week_override: Optional[int] = None,
) -> Tuple[int, int]:
    """Trả về (sessions_per_week, total_sessions)."""
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


def _load_shared_teachers(shared_dir: Path) -> Dict[str, Dict]:
    """Load teachers từ shared/teachers.xlsx → dict[teacher_id → info]."""
    path = shared_dir / "teachers.xlsx"
    if not path.is_file():
        return {}
    df = pd.read_excel(path, engine="openpyxl")
    teachers = {}
    for _, row in df.iterrows():
        tid = str(row.get("Mã CB", "")).strip()
        if not tid:
            continue
        teachers[tid] = {
            "teacher_id": tid,
            "teacher_name": str(row.get("Họ Tên", "")).strip(),
            "department_code": str(row.get("Mã đơn vị", "")).strip(),
            "teacher_type": str(row.get("Phân Loại", "")).strip(),
        }
    return teachers


def _load_department_name_map(shared_dir: Path) -> Dict[str, str]:
    """Map tên đầy đủ khoa → mã khoa (uppercase). Đọc shared/departments.xlsx."""
    path = shared_dir / "departments.xlsx"
    if not path.is_file():
        return {}
    df = pd.read_excel(path, engine="openpyxl")
    mapping: Dict[str, str] = {}
    for _, row in df.iterrows():
        code = str(row.get("Mã đơn vị", "")).strip().upper()
        name = str(row.get("Tên đơn vị", "")).strip()
        if code and name:
            mapping[name] = code
    return mapping


def _load_availability(
    avail_path: Path,
    date_to_week: Dict[str, int],
) -> Dict[str, Set[Tuple[int, int, int]]]:
    """Load availability.csv → dict[teacher_id → set of (week_order, day_of_week, session_id)].

    session_id: 1=Sáng, 2=Chiều.
    """
    import csv
    if not avail_path.is_file():
        return {}
    result: Dict[str, Set[Tuple[int, int, int]]] = {}
    with avail_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tid = (row.get("teacher_ids") or "").strip()
            if not tid:
                continue
            day_str = (row.get("day_of_week") or "").strip().lower()
            day = DAY_MAP.get(day_str)
            if day is None:
                continue
            session = int(row.get("session_id") or 0)
            if session not in (1, 2):
                continue
            ws = (row.get("week_start") or "").strip()
            week_order = date_to_week.get(ws)
            if week_order is None:
                continue
            if tid not in result:
                result[tid] = set()
            result[tid].add((week_order, day, session))
    return result


def _load_curriculum(
    path: Path,
    program_level: str,
    dept_code: str,
) -> Tuple[pd.DataFrame, List[str]]:
    """Load 1 file chương trình đào tạo. Chuẩn hóa cột."""
    warnings: List[str] = []
    df = pd.read_excel(path, engine="openpyxl")

    col_map = {}
    for col in df.columns:
        cl = col.strip().lower()
        if cl == "subject_code":
            col_map["subject_code"] = col
        elif cl == "subject_name":
            col_map["subject_name"] = col
        elif cl == "total_hours":
            col_map["total_hours"] = col
        elif cl == "class_id":
            col_map["class_id"] = col
        elif cl == "teacher_id":
            col_map["teacher_id"] = col
        elif cl in ("room_type", "mã tính chất phòng"):
            col_map["room_type"] = col
        elif cl == "sessions_per_week":
            col_map["sessions_per_week"] = col

    required = ["subject_code", "subject_name", "total_hours", "class_id"]
    for r in required:
        if r not in col_map:
            warnings.append(f"[{dept_code}/{program_level}] Thiếu cột '{r}' trong {path.name}")

    df["_program_level"] = program_level
    df["_col_map"] = [col_map] * len(df)
    return df, warnings


def _compute_week_end_skip_holidays(
    week_lo: int,
    week_hi: int,
    num_weeks_needed: int,
    holidays: Set[int],
) -> Tuple[int, int]:
    """Tìm week_end sao cho có đủ num_weeks_needed tuần dạy thực (bỏ qua holiday).

    Trả về (a_week_end, missing_weeks). missing_weeks > 0 nghĩa là term hết tuần
    trước khi dạy đủ.
    """
    taught = 0
    w = week_lo
    while taught < num_weeks_needed and w <= week_hi:
        if w not in holidays:
            taught += 1
        w += 1
    a_week_end = min(week_hi, w - 1)
    missing = num_weeks_needed - taught
    return a_week_end, missing


def build_generate_request(
    data_root: Optional[Path] = None,
    *,
    term_code: str = "2025_2026_HK2",
    departments: Optional[List[str]] = None,
    config_path: Optional[Path] = None,
    availability_override: Optional[Dict[str, Set[Tuple[int, int, int]]]] = None,
    holidays_override: Optional[Set[int]] = None,
    holiday_reasons_override: Optional[Dict[int, str]] = None,
    teacher_subjects_override: Optional[List[Dict]] = None,
    class_excluded_weeks_override: Optional[Dict[str, Dict[int, str]]] = None,
    class_week_starts_override: Optional[Dict[str, int]] = None,
) -> BuildResult:
    """Build GenerateRequest từ data mới (dao_tao_*.xlsx + availability + shared).

    availability_override / holidays_override / holiday_reasons_override:
    nếu pass vào (vd từ DB cdata) → dùng thay cho file CSV.

    teacher_subjects_override: list[{teacher_id, subject_code, priority, teacher_type}]
    nếu pass vào → dùng thay cho teacher_subjects.xlsx của từng khoa.
    """
    root = data_root or _default_data_root()
    tp = TermPaths(data_root=root, term_code=term_code)
    cfg = load_config(config_path)

    if departments is None:
        departments = tp.list_departments()
    if not departments:
        raise ValueError(f"Không tìm thấy khoa nào trong {tp.departments_dir}")

    warnings: List[str] = []
    total_skipped = 0

    classrooms = load_classrooms_from_csv(tp.rooms_csv, _load_department_name_map(tp.shared_dir))
    week_lo, week_hi = week_bounds_from_csv(tp.weeks_csv)
    date_to_week = _build_week_date_map(tp.weeks_csv)
    # Holidays: override từ DB nếu có, fallback đọc CSV
    if holidays_override is not None:
        holidays = set(holidays_override)
    else:
        holidays = load_holidays(tp.holidays_csv)
    if holiday_reasons_override is not None:
        holiday_reasons = dict(holiday_reasons_override)
    else:
        holiday_reasons = load_holiday_reasons(tp.holidays_csv)
    week_dates = load_week_dates(tp.weeks_csv)
    shared_teachers = _load_shared_teachers(tp.shared_dir)

    if holidays:
        warnings.append(
            f"Holiday weeks: {sorted(holidays)} (extend week_end để bù tuần dạy)"
        )

    # Class excluded weeks: override từ DB nếu có, fallback đọc CSV (trả {} nếu file không tồn tại)
    if class_excluded_weeks_override is not None:
        class_excluded_weeks: Dict[str, Dict[int, str]] = dict(class_excluded_weeks_override)
    else:
        class_excluded_weeks = load_class_excluded_weeks_csv(tp.class_excluded_weeks_csv)
    if class_excluded_weeks:
        total_excl = sum(len(v) for v in class_excluded_weeks.values())
        warnings.append(
            f"Class excluded weeks: {len(class_excluded_weeks)} lớp, "
            f"{total_excl} tuần loại trừ (thi/dự phòng/...)"
        )

    # class_week_starts: override từ DB nếu có, fallback CSV (trả {} nếu file không tồn tại)
    if class_week_starts_override is not None:
        class_week_starts: Dict[str, int] = dict(class_week_starts_override)
    else:
        class_week_starts = load_class_week_starts_csv(tp.class_week_starts_csv)
    if class_week_starts:
        warnings.append(
            f"Class week starts: {len(class_week_starts)} lớp có tuần bắt đầu riêng"
        )

    all_assignments: List[Assignment] = []
    all_labels: Dict[int, Dict[str, str]] = {}
    all_availability: Dict[str, Set[Tuple[int, int, int]]] = {}
    aid = 1

    # --- Pass 1: gom demands (subject, class) + thông tin → assign GV ---
    # Mỗi entry: dict chứa toàn bộ field cần để build Assignment + label sau khi có teacher_id
    demands_buffer: List[Dict] = []
    teacher_subjects_pool: Dict[Tuple[str, str], int] = {}  # (gv, môn) → priority
    locked_assignments: Dict[Tuple[str, str], str] = {}     # (môn, lớp) đã chốt cứng

    for dept_code in departments:
        dp = tp.department(dept_code)
        cleans = dp.cleans_dir

        # Load availability
        avail_path = cleans / "availability.csv"
        dept_avail = _load_availability(avail_path, date_to_week)
        for tid, slots in dept_avail.items():
            if tid not in all_availability:
                all_availability[tid] = set()
            all_availability[tid].update(slots)

        # Load teacher_subjects: dùng override từ DB nếu có, fallback file xlsx/csv
        if teacher_subjects_override is not None:
            ts = {
                (r["teacher_id"], r["subject_code"]): int(r.get("priority", 2))
                for r in teacher_subjects_override
                if r.get("teacher_id") and r.get("subject_code")
            }
        else:
            ts_path = dp.teacher_subjects_xlsx if dp.teacher_subjects_xlsx.is_file() else dp.teacher_subjects_csv
            ts = load_teacher_subjects(ts_path)
        teacher_subjects_pool.update(ts)

        # Sĩ số lớp
        class_sizes = load_class_sizes(dp.classes_csv)
        missing_sizes: Set[str] = set()

        # Load curriculum
        curriculum_files = []
        for f in cleans.glob("dao_tao_*.xlsx"):
            name = f.stem.replace("dao_tao_", "")
            curriculum_files.append((f, name))

        if not curriculum_files:
            warnings.append(f"[{dept_code}] Không tìm thấy file dao_tao_*.xlsx trong {cleans}")
            continue

        for cur_path, program_level in curriculum_files:
            df, cur_warnings = _load_curriculum(cur_path, program_level, dept_code)
            warnings.extend(cur_warnings)

            if df.empty:
                continue

            col_map = df["_col_map"].iloc[0]

            for _, row in df.iterrows():
                class_id = str(row.get(col_map.get("class_id", "class_id"), "")).strip()
                subject_code = str(row.get(col_map.get("subject_code", "subject_code"), "")).strip()
                subject_name = str(row.get(col_map.get("subject_name", "subject_name"), "")).strip()

                if not class_id or not subject_code:
                    total_skipped += 1
                    continue

                if is_excluded_subject(subject_name):
                    total_skipped += 1
                    continue

                # teacher_id giờ optional - nếu có = lock cứng, không có = để assigner gán
                locked_tid: Optional[str] = None
                if "teacher_id" in col_map:
                    raw = row.get(col_map["teacher_id"])
                    if not pd.isna(raw) and str(raw).strip():
                        locked_tid = str(raw).strip()

                try:
                    total_hours = float(row.get(col_map.get("total_hours", "total_hours"), 0) or 0)
                except (TypeError, ValueError):
                    total_hours = 0.0
                if total_hours <= 0:
                    total_skipped += 1
                    continue

                room_type_raw = str(row.get(col_map.get("room_type", "room_type"), "LT")).strip()
                classroom_type = ROOM_TYPE_MAP.get(room_type_raw, 1)

                spw_override = None
                if "sessions_per_week" in col_map:
                    spw_raw = row.get(col_map["sessions_per_week"])
                    if not pd.isna(spw_raw):
                        try:
                            spw_override = int(float(spw_raw))
                        except (TypeError, ValueError):
                            pass

                sessions_pw, total_sessions = _sessions_from_hours(
                    total_hours, cfg.default_lessons_cluster, spw_override
                )
                num_weeks_needed = math.ceil(total_sessions / sessions_pw)
                # Per-class week_start: từ DB/CSV, mặc định week_lo
                class_week_start = max(week_lo, class_week_starts.get(class_id, week_lo))
                # Hợp nhất: tuần nghỉ toàn trường + tuần loại trừ riêng của lớp này
                class_excluded_for_class = set(class_excluded_weeks.get(class_id, {}).keys())
                combined_excluded = holidays | class_excluded_for_class
                a_week_end, missing_weeks = _compute_week_end_skip_holidays(
                    class_week_start, week_hi, num_weeks_needed, combined_excluded
                )
                if missing_weeks > 0:
                    warnings.append(
                        f"[{dept_code}/{program_level}] {class_id} | {subject_name}: "
                        f"thiếu {missing_weeks} tuần dạy do holiday/excluded đẩy ngoài term "
                        f"(cần {num_weeks_needed} tuần, term còn {num_weeks_needed - missing_weeks})"
                    )

                class_size = class_sizes.get(class_id)
                if class_size is None:
                    missing_sizes.add(class_id)

                if locked_tid:
                    locked_assignments[(subject_code, class_id)] = locked_tid

                demands_buffer.append({
                    "subject_code": subject_code,
                    "subject_name": subject_name,
                    "class_id": class_id,
                    "dept_code": dept_code,
                    "program_level": program_level,
                    "sessions_per_week": sessions_pw,
                    "total_sessions": int(total_sessions),
                    "total_hours": total_hours,
                    "classroom_type": classroom_type,
                    "room_type_raw": room_type_raw,
                    "week_start": class_week_start,
                    "week_end": a_week_end,
                    "class_size": class_size,
                })

        if missing_sizes:
            sample = ", ".join(sorted(missing_sizes)[:5])
            extra = "" if len(missing_sizes) <= 5 else f" (và {len(missing_sizes) - 5} lớp khác)"
            warnings.append(
                f"[{dept_code}] {len(missing_sizes)} lớp không có class_size trong classes.csv → bỏ qua check capacity: {sample}{extra}"
            )

    # --- Pass 2: phân công GV ---
    # Override availability từ DB nếu có (replace, không merge)
    if availability_override is not None:
        all_availability = {k: set(v) for k, v in availability_override.items()}
        warnings.append(
            f"availability_override: dùng {len(all_availability)} GV từ DB thay file CSV"
        )
    teacher_info_map: Dict[str, TeacherInfo] = {
        tid: TeacherInfo(
            teacher_id=tid,
            teacher_name=info.get("teacher_name", tid),
            teacher_type=info.get("teacher_type", ""),
            department_code=info.get("department_code", ""),
        )
        for tid, info in shared_teachers.items()
    }

    sc_demands = [
        SubjectClassDemand(
            subject_code=d["subject_code"],
            subject_name=d["subject_name"],
            class_id=d["class_id"],
            dept_code=d["dept_code"],
            program_level=d["program_level"],
            sessions_per_week=d["sessions_per_week"],
            total_sessions=d["total_sessions"],
            week_start=d["week_start"],
            week_end=d["week_end"],
            excluded_weeks=(
                holidays | set(class_excluded_weeks.get(d["class_id"], {}).keys())
            ),
        )
        for d in demands_buffer
    ]

    assigner_res: AssignmentResult = assign_teachers(
        demands=sc_demands,
        teacher_subjects=teacher_subjects_pool,
        availability=all_availability,
        teacher_info=teacher_info_map,
        days=cfg.days,
        locked=locked_assignments,
    )
    warnings.extend(assigner_res.warnings)

    # --- Pass 3: build Assignment + label từ kết quả assigner ---
    for d in demands_buffer:
        key = (d["subject_code"], d["class_id"])
        teacher_id = assigner_res.assignments.get(key)
        if not teacher_id:
            total_skipped += 1
            continue
        teacher_info = shared_teachers.get(teacher_id, {})
        teacher_name = teacher_info.get("teacher_name", teacher_id)
        teacher_type = teacher_info.get("teacher_type", "")

        # Tuần loại trừ riêng của lớp này (thi/dự phòng/...) trong phạm vi week_start..week_end.
        # Không gồm global holidays (đã được truyền riêng qua holiday_weeks).
        class_excl_dict = class_excluded_weeks.get(d["class_id"], {})  # {week: reason}
        excl_reasons_in_range = {
            w: r for w, r in class_excl_dict.items()
            if d["week_start"] <= w <= d["week_end"]
        }
        excl_in_range = set(excl_reasons_in_range.keys())

        # Dùng MD5 thay hash() để class_group_id ổn định qua các lần chạy
        stable_gid = int(hashlib.md5(d["class_id"].encode()).hexdigest(), 16) % (10**9)

        all_assignments.append(
            Assignment(
                id=aid,
                teacher_id=teacher_id,
                course_id=d["subject_code"],
                class_group_id=stable_gid,
                classroom_type=d["classroom_type"],
                sessions_per_week=d["sessions_per_week"],
                lessons_cluster=cfg.default_lessons_cluster,
                week_start=d["week_start"],
                week_end=d["week_end"],
                department_code=d["dept_code"],
                term_code=term_code,
                class_size=d["class_size"],
                excluded_weeks=excl_in_range,
                excluded_week_reasons=excl_reasons_in_range,
            )
        )
        total_hours = d["total_hours"]
        all_labels[aid] = {
            "class_id": d["class_id"],
            "class_name": d["class_id"],
            "subject_code": d["subject_code"],
            "subject_name": d["subject_name"],
            "teacher_id": teacher_id,
            "teacher_name": teacher_name,
            "teacher_type": teacher_type,
            "department_code": d["dept_code"],
            "program_level": d["program_level"],
            "room_type": d["room_type_raw"],
            "total_hours": str(int(total_hours)) if total_hours == int(total_hours) else f"{total_hours:.1f}",
            "total_sessions": str(d["total_sessions"]),
        }
        aid += 1

    if not classrooms:
        warnings.append("Không có phòng khả dụng sau lọc rooms.csv")

    # Post-process: cap class groups theo max_spw_trung_cap / max_spw_cao_dang (HC-005)
    cap_by_level = {
        "trung_cap": int(cfg.max_spw_trung_cap),
        "cao_dang": int(cfg.max_spw_cao_dang),
    }
    group_assignments_by_level: Dict[Tuple[int, str], List[int]] = {}
    for i, a in enumerate(all_assignments):
        label = all_labels.get(a.id, {})
        level = label.get("program_level", "")
        if level not in cap_by_level:
            continue
        group_assignments_by_level.setdefault((a.class_group_id, level), []).append(i)

    for (gid, level), indices in group_assignments_by_level.items():
        max_spw = cap_by_level[level]
        total_spw = sum(all_assignments[i].sessions_per_week for i in indices)
        if total_spw <= max_spw:
            continue
        # Phân bổ lại spw: chia đều theo tỉ lệ, ít nhất 1 buổi/môn
        n_subjects = len(indices)
        if n_subjects > max_spw:
            # Quá nhiều môn → mỗi môn 1 buổi, một số môn vẫn vượt cap
            new_spw_list = [1] * n_subjects
        else:
            # Chia base = max_spw // n_subjects, dồn dư cho môn nhiều giờ trước
            base = max_spw // n_subjects
            extra = max_spw - base * n_subjects
            indices_sorted = sorted(
                indices,
                key=lambda i: all_assignments[i].sessions_per_week,
                reverse=True,
            )
            new_spw_map: Dict[int, int] = {i: base for i in indices}
            for k in range(extra):
                new_spw_map[indices_sorted[k]] += 1
            new_spw_list = [new_spw_map[i] for i in indices]

        for idx_pos, i in enumerate(indices):
            a = all_assignments[i]
            new_spw = max(1, new_spw_list[idx_pos])
            cls_id_for_a = all_labels.get(a.id, {}).get("class_id", "")
            class_excl_dict_for_a = class_excluded_weeks.get(cls_id_for_a, {})  # {week: reason}
            class_excl_for_a = set(class_excl_dict_for_a.keys())
            combined_excl_for_a = holidays | class_excl_for_a
            old_total_sessions = (a.week_end - a.week_start + 1 - sum(
                1 for h in combined_excl_for_a if a.week_start <= h <= a.week_end
            )) * a.sessions_per_week
            new_total_sessions = max(1, old_total_sessions)
            new_end, _ = _compute_week_end_skip_holidays(
                a.week_start,
                week_hi,
                math.ceil(new_total_sessions / new_spw),
                combined_excl_for_a,
            )
            new_excl_reasons = {
                w: r for w, r in class_excl_dict_for_a.items()
                if a.week_start <= w <= new_end
            }
            new_excl = set(new_excl_reasons.keys())
            all_assignments[i] = Assignment(
                id=a.id,
                teacher_id=a.teacher_id,
                course_id=a.course_id,
                class_group_id=a.class_group_id,
                classroom_type=a.classroom_type,
                sessions_per_week=new_spw,
                lessons_cluster=a.lessons_cluster,
                week_start=a.week_start,
                week_end=new_end,
                department_code=a.department_code,
                term_code=a.term_code,
                class_size=a.class_size,
                excluded_weeks=new_excl,
                excluded_week_reasons=new_excl_reasons,
            )
        class_id = all_labels.get(all_assignments[indices[0]].id, {}).get("class_id", "?")
        warnings.append(
            f"[{level}] {class_id}: tổng spw={total_spw} > {max_spw} → giảm spw từng môn (HC-005)"
        )

    or_cfg = ORToolsConfig(
        days=cfg.days,
        periods_per_day=cfg.periods_per_day,
        morning_periods=cfg.morning_periods,
        afternoon_periods=cfg.afternoon_periods,
    )

    req = GenerateRequest(or_tools=or_cfg, assignments=all_assignments, classrooms=classrooms)
    return BuildResult(
        request=req,
        warnings=warnings,
        skipped_rows=total_skipped,
        assignment_labels=all_labels,
        availability=all_availability,
        config=cfg,
        holidays=holidays,
        week_dates=week_dates,
        holiday_reasons=holiday_reasons,
        assigner_log=assigner_res.log_rows,
        class_excluded_weeks=class_excluded_weeks,
        class_week_starts=class_week_starts,
    )

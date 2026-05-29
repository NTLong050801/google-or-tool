"""Phân công GV → (subject_code, class_id) dựa trên teacher_subjects.xlsx + availability.

Input:
  - subject_class_pairs: list (subject_code, class_id, dept_code, program_level, sessions_per_week)
  - teacher_subjects: dict[(teacher_id, subject_code) → priority(1/2/3)]
  - availability: dict[teacher_id → set[(week, day, session)]]
  - shared_teachers: dict[teacher_id → {teacher_name, teacher_type, ...}]

Output:
  - assignments: dict[(subject_code, class_id) → teacher_id]
  - log_rows: list các dict để xuất assignment_log.xlsx
  - warnings: list cảnh báo (môn không tìm được GV, GV quá tải, ...)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class SubjectClassDemand:
    subject_code: str
    subject_name: str
    class_id: str
    dept_code: str
    program_level: str          # "trung_cap" / "cao_dang"
    sessions_per_week: int
    total_sessions: int         # tổng buổi qua cả kỳ (để cân bằng tải)


@dataclass
class TeacherInfo:
    teacher_id: str
    teacher_name: str
    teacher_type: str           # "Cơ hữu" / "Thỉnh giảng"
    department_code: str = ""


@dataclass
class AssignmentResult:
    assignments: Dict[Tuple[str, str], str] = field(default_factory=dict)
    log_rows: List[Dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# --- Scoring ---

# Trọng số:
# - priority_bonus: chỉ phá hòa giữa các GV cùng loại (1/2/3 chênh nhỏ)
# - type_bonus: thỉnh giảng luôn được ưu tiên (vì mời từ ngoài, ít linh hoạt
#   - nếu không pick sẽ lãng phí; cơ hữu chỉ nhận khi không có thỉnh giảng)
_PRIORITY_BONUS = {1: 20, 2: 15, 3: 10}
_TYPE_BONUS = {"thỉnh giảng": 200, "co huu": 0, "cơ hữu": 0}


def _availability_score(
    teacher_id: str,
    program_level: str,
    sessions_per_week: int,
    availability: Dict[str, Set[Tuple[int, int, int]]],
    days: List[int],
) -> int:
    """Đếm số (day, session) khác nhau GV rảnh, ưu tiên nếu nhiều slot khớp.

    Trả về 0 nếu không đủ slot cho sessions_per_week.
    """
    if teacher_id not in availability:
        return 50  # GV chưa đăng ký availability → coi như rảnh tất cả, score trung bình
    slots = availability[teacher_id]
    day_sessions: Set[Tuple[int, int]] = set()
    for _, d, s in slots:
        if d in days:
            # Trung cấp chỉ học sáng → chỉ tính session=1
            if program_level == "trung_cap" and s != 1:
                continue
            day_sessions.add((d, s))
    if len(day_sessions) < sessions_per_week:
        return -1000  # nặng penalty: GV không đủ slot cho môn này
    return min(100, len(day_sessions) * 10)


def _load_balance_score(
    teacher_id: str,
    teacher_load: Dict[str, int],
    avg_load: float,
) -> int:
    """GV đang ít tải hơn → score cao hơn. Range ≈ -50..+50."""
    cur = teacher_load.get(teacher_id, 0)
    diff = avg_load - cur
    return int(max(-50, min(50, diff * 2)))


def _difficulty_score(
    demand: SubjectClassDemand,
    candidates: List[Tuple[str, int]],   # (teacher_id, priority)
    availability: Dict[str, Set[Tuple[int, int, int]]],
    days: List[int],
) -> int:
    """Càng khó → assign sớm. Khó = ít GV ứng viên, ít slot khả dụng."""
    n = len(candidates)
    if n == 0:
        return 999  # cao nhất
    available_count = 0
    for tid, _ in candidates:
        slots = availability.get(tid, set())
        ds = {(d, s) for _, d, s in slots if d in days}
        if demand.program_level == "trung_cap":
            ds = {p for p in ds if p[1] == 1}
        if len(ds) >= demand.sessions_per_week or tid not in availability:
            available_count += 1
    if available_count == 0:
        return 800
    # Ít candidate khả dụng + spw cao → khó
    return 100 - available_count * 10 + demand.sessions_per_week * 5


def assign_teachers(
    demands: List[SubjectClassDemand],
    teacher_subjects: Dict[Tuple[str, str], int],  # (teacher_id, subject_code) → priority
    availability: Dict[str, Set[Tuple[int, int, int]]],
    teacher_info: Dict[str, TeacherInfo],
    days: List[int],
    *,
    locked: Optional[Dict[Tuple[str, str], str]] = None,  # (subj, cls) → teacher_id (đã chốt sẵn)
) -> AssignmentResult:
    """Greedy assign: gán môn-lớp khó nhất trước, mỗi lần chọn GV có score cao nhất."""
    locked = locked or {}
    res = AssignmentResult()

    # Build index: subject_code → list[(teacher_id, priority)]
    subject_to_teachers: Dict[str, List[Tuple[str, int]]] = {}
    for (tid, sub), prio in teacher_subjects.items():
        subject_to_teachers.setdefault(sub, []).append((tid, prio))

    # Apply locked trước
    teacher_load: Dict[str, int] = {}  # teacher_id → tổng total_sessions đã gán
    for d in demands:
        key = (d.subject_code, d.class_id)
        if key in locked:
            tid = locked[key]
            res.assignments[key] = tid
            teacher_load[tid] = teacher_load.get(tid, 0) + d.total_sessions

    # Pending = các demand chưa lock
    pending = [d for d in demands if (d.subject_code, d.class_id) not in res.assignments]

    # Sort theo độ khó giảm dần
    pending.sort(
        key=lambda d: _difficulty_score(
            d,
            subject_to_teachers.get(d.subject_code, []),
            availability,
            days,
        ),
        reverse=True,
    )

    total_sessions_all = sum(d.total_sessions for d in demands)
    n_teachers = max(1, len({tid for tid, _ in teacher_subjects.keys()}))
    avg_load = total_sessions_all / n_teachers

    for d in pending:
        candidates = subject_to_teachers.get(d.subject_code, [])
        if not candidates:
            res.warnings.append(
                f"[assign] {d.dept_code}/{d.program_level} {d.class_id} | {d.subject_name} "
                f"({d.subject_code}): KHÔNG có GV nào đăng ký dạy môn này"
            )
            res.log_rows.append({
                "Khoa": d.dept_code,
                "Hệ": d.program_level,
                "Lớp": d.class_id,
                "Mã môn": d.subject_code,
                "Tên môn": d.subject_name,
                "GV được gán": "",
                "Tên GV": "",
                "Loại GV": "",
                "Ưu tiên": "",
                "Score": "",
                "Trạng thái": "KHÔNG có GV đăng ký",
            })
            continue

        # Tính score cho mỗi candidate
        scored: List[Tuple[int, str, int, Dict]] = []
        for tid, prio in candidates:
            info = teacher_info.get(tid, TeacherInfo(tid, tid, ""))
            type_key = info.teacher_type.strip().lower()
            type_bonus = _TYPE_BONUS.get(type_key, 0)
            prio_bonus = _PRIORITY_BONUS.get(prio, _PRIORITY_BONUS[2])
            avail_bonus = _availability_score(
                tid, d.program_level, d.sessions_per_week, availability, days
            )
            load_bonus = _load_balance_score(tid, teacher_load, avg_load)
            score = prio_bonus + type_bonus + avail_bonus + load_bonus
            scored.append((score, tid, prio, {
                "prio": prio_bonus,
                "type": type_bonus,
                "avail": avail_bonus,
                "load": load_bonus,
            }))

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_tid, best_prio, breakdown = scored[0]

        if best_score < 0:
            # Không GV nào đủ slot - vẫn phải gán cho người khả dĩ nhất, nhưng cảnh báo
            res.warnings.append(
                f"[assign] {d.dept_code}/{d.program_level} {d.class_id} | {d.subject_name}: "
                f"GV {best_tid} không đủ slot khả dụng (score={best_score})"
            )

        res.assignments[(d.subject_code, d.class_id)] = best_tid
        teacher_load[best_tid] = teacher_load.get(best_tid, 0) + d.total_sessions

        info = teacher_info.get(best_tid, TeacherInfo(best_tid, best_tid, ""))
        res.log_rows.append({
            "Khoa": d.dept_code,
            "Hệ": d.program_level,
            "Lớp": d.class_id,
            "Mã môn": d.subject_code,
            "Tên môn": d.subject_name,
            "GV được gán": best_tid,
            "Tên GV": info.teacher_name,
            "Loại GV": info.teacher_type,
            "Ưu tiên": best_prio,
            "Score": best_score,
            "Trạng thái": "OK" if best_score >= 0 else "Thiếu slot",
            "_chi tiết score": (
                f"prio={breakdown['prio']} type={breakdown['type']} "
                f"avail={breakdown['avail']} load={breakdown['load']}"
            ),
            "Ứng viên khác": ", ".join(
                f"{t}({s})" for s, t, _, _ in scored[1:4]
            ),
        })

    # Cảnh báo cân bằng tải
    if teacher_load:
        loads = sorted(teacher_load.items(), key=lambda x: x[1], reverse=True)
        if loads[0][1] > 3 * avg_load and avg_load > 0:
            res.warnings.append(
                f"[assign] Tải mất cân bằng: GV {loads[0][0]} = {loads[0][1]} buổi "
                f"(avg={avg_load:.0f})"
            )

    return res

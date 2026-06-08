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
    week_start: Optional[int] = None
    week_end: Optional[int] = None
    excluded_weeks: Set[int] = field(default_factory=set)


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
    week_start: Optional[int] = None,
    week_end: Optional[int] = None,
    excluded_weeks: Optional[Set[int]] = None,
) -> int:
    """Đếm số (day, session) GV rảnh trong dải tuần thực dạy của môn.

    Trả về penalty nặng nếu không đủ slot cho sessions_per_week.
    """
    if teacher_id not in availability:
        return 50  # GV chưa đăng ký availability → coi như rảnh tất cả, score trung bình
    slots = availability[teacher_id]
    skip = excluded_weeks or set()
    day_sessions: Set[Tuple[int, int]] = set()
    for w, d, s in slots:
        # Lọc theo dải tuần thực dạy của assignment (bỏ nghỉ lễ + thi/dự phòng)
        if week_start is not None and week_end is not None:
            if w < week_start or w > week_end or w in skip:
                continue
        if d in days:
            # Trung cấp chỉ học sáng → chỉ tính session=1
            if program_level == "trung_cap" and s != 1:
                continue
            day_sessions.add((d, s))
    if len(day_sessions) < sessions_per_week:
        return -1000  # nặng penalty: GV không đủ slot trong tuần thực dạy
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
    """Càng khó → assign sớm. Khó = ít GV ứng viên, ít slot khả dụng trong tuần thực dạy."""
    n = len(candidates)
    if n == 0:
        return 999  # cao nhất
    skip = demand.excluded_weeks or set()
    available_count = 0
    for tid, _ in candidates:
        slots = availability.get(tid, set())
        ds: Set[Tuple[int, int]] = set()
        for w, d, s in slots:
            if demand.week_start is not None and demand.week_end is not None:
                if w < demand.week_start or w > demand.week_end or w in skip:
                    continue
            if d in days:
                if demand.program_level == "trung_cap" and s != 1:
                    continue
                ds.add((d, s))
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
    locked: Optional[Dict[Tuple[str, str], str]] = None,
    max_spw_per_teacher: int = 12,
    max_spw_trung_cap: int = 6,
) -> AssignmentResult:
    """Greedy assign: gán môn-lớp khó nhất trước, mỗi lần chọn GV có score cao nhất.

    Trung cấp dùng riêng cap max_spw_trung_cap (mặc định 6 = 6 ngày × 1 buổi sáng).
    Cao đẳng/khác dùng max_spw_per_teacher.
    Cap được track riêng theo program_level vì 1 GV có thể dạy cả 2 hệ.
    """
    locked = locked or {}
    res = AssignmentResult()

    # Build index: subject_code → list[(teacher_id, priority)]
    subject_to_teachers: Dict[str, List[Tuple[str, int]]] = {}
    for (tid, sub), prio in teacher_subjects.items():
        subject_to_teachers.setdefault(sub, []).append((tid, prio))

    # Apply locked trước
    teacher_load: Dict[str, int] = {}          # tổng total_sessions — load_balance_score
    teacher_spw_load: Dict[str, int] = {}      # tổng spw (cao_dang + khác) — hard cap
    teacher_tc_spw_load: Dict[str, int] = {}   # tổng spw trung_cap — hard cap riêng
    for d in demands:
        key = (d.subject_code, d.class_id)
        if key in locked:
            tid = locked[key]
            res.assignments[key] = tid
            teacher_load[tid] = teacher_load.get(tid, 0) + d.total_sessions
            if d.program_level == "trung_cap":
                teacher_tc_spw_load[tid] = teacher_tc_spw_load.get(tid, 0) + d.sessions_per_week
            else:
                teacher_spw_load[tid] = teacher_spw_load.get(tid, 0) + d.sessions_per_week

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

        # Lọc GV đã đạt hard cap buổi/tuần.
        # Cap kép:
        #   1) Tổng (trung_cap + cao_dang) ≤ max_spw_per_teacher (= số slot/tuần khả dụng = 12)
        #   2) Riêng trung_cap ≤ max_spw_trung_cap (nếu morning_only thì = 6, ngược lại = 12)
        is_tc = d.program_level == "trung_cap"
        def _can_take(tid: str) -> bool:
            cur_total = teacher_spw_load.get(tid, 0) + teacher_tc_spw_load.get(tid, 0)
            if cur_total + d.sessions_per_week > max_spw_per_teacher:
                return False
            if is_tc:
                cur_tc = teacher_tc_spw_load.get(tid, 0)
                if cur_tc + d.sessions_per_week > max_spw_trung_cap:
                    return False
            return True

        available_candidates = [(tid, prio) for tid, prio in candidates if _can_take(tid)]
        if not available_candidates:
            # Tất cả GV đều đã full — ghi warning, bỏ qua môn này
            full_teachers = ", ".join(
                f"{tid}({teacher_spw_load.get(tid,0)+teacher_tc_spw_load.get(tid,0)}b/t)"
                for tid, _ in candidates[:3]
            )
            extra = f" (và {len(candidates)-3} GV khác)" if len(candidates) > 3 else ""
            res.warnings.append(
                f"[assign] {d.dept_code}/{d.program_level} {d.class_id} | {d.subject_name}: "
                f"tất cả GV đăng ký đã đạt max {max_spw_per_teacher} buổi/tuần "
                f"→ {full_teachers}{extra}"
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
                "Trạng thái": f"GV đã full ({max_spw_per_teacher}b/t)",
            })
            continue

        # Tính score cho mỗi candidate còn trong ngưỡng
        scored: List[Tuple[int, str, int, Dict]] = []
        for tid, prio in available_candidates:
            info = teacher_info.get(tid, TeacherInfo(tid, tid, ""))
            type_key = info.teacher_type.strip().lower()
            type_bonus = _TYPE_BONUS.get(type_key, 0)
            prio_bonus = _PRIORITY_BONUS.get(prio, _PRIORITY_BONUS[2])
            avail_bonus = _availability_score(
                tid, d.program_level, d.sessions_per_week, availability, days,
                week_start=d.week_start,
                week_end=d.week_end,
                excluded_weeks=d.excluded_weeks,
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
        if d.program_level == "trung_cap":
            teacher_tc_spw_load[best_tid] = teacher_tc_spw_load.get(best_tid, 0) + d.sessions_per_week
        else:
            teacher_spw_load[best_tid] = teacher_spw_load.get(best_tid, 0) + d.sessions_per_week

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

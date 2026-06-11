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
# - same_subject_penalty: trừ điểm khi 1 GV đã được gán nhiều lớp của cùng môn,
#   để tránh 1 GV ôm hết các lớp cùng môn → các GV khác không có việc.
_PRIORITY_BONUS = {1: 20, 2: 15, 3: 10}
_TYPE_BONUS = {"thỉnh giảng": 200, "co huu": 0, "cơ hữu": 0}
_SAME_SUBJECT_PENALTY = 150  # trừ N×150 nếu GV đã được gán N lớp cùng môn


def _availability_detail(
    teacher_id: str,
    program_level: str,
    sessions_per_week: int,
    availability: Dict[str, Set[Tuple[int, int, int]]],
    days: List[int],
    week_start: Optional[int] = None,
    week_end: Optional[int] = None,
    excluded_weeks: Optional[Set[int]] = None,
) -> Tuple[int, str]:
    """Trả về (score, reason_str) — reason_str mô tả tại sao GV đủ/thiếu slot."""
    if teacher_id not in availability:
        return 50, ""
    slots = availability[teacher_id]
    skip = excluded_weeks or set()
    day_sessions: Set[Tuple[int, int]] = set()
    total_registered = 0  # tổng slot GV đăng ký (không lọc tuần)
    for w, d, s in slots:
        if d in days:
            if program_level == "trung_cap" and s != 1:
                continue
            total_registered += 1
        if week_start is not None and week_end is not None:
            if w < week_start or w > week_end or w in skip:
                continue
        if d in days:
            if program_level == "trung_cap" and s != 1:
                continue
            day_sessions.add((d, s))
    n = len(day_sessions)
    if n < sessions_per_week:
        week_range_str = ""
        if week_start is not None and week_end is not None:
            week_range_str = f" trong tuần {week_start}-{week_end}"
        num_weeks = len([w for w in range(week_start or 0, (week_end or 0) + 1) if w not in skip]) if week_start and week_end else 0
        total_needed = sessions_per_week * num_weeks if num_weeks else sessions_per_week
        reason = (
            f"GV có {n} slot (day,buổi) hợp lệ{week_range_str}, "
            f"môn cần {sessions_per_week} buổi/tuần"
            + (f" × {num_weeks} tuần = {total_needed} buổi cả kỳ" if num_weeks else "")
            + (f" — tổng slot GV đăng ký: {total_registered}" if total_registered > n else "")
        )
        return -1000, reason
    return min(100, n * 10), ""


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
    score, _ = _availability_detail(
        teacher_id, program_level, sessions_per_week, availability, days,
        week_start=week_start, week_end=week_end, excluded_weeks=excluded_weeks,
    )
    return score


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
    max_spw_trung_cap: int = 6,  # unused — constraint của lớp, không phải GV
) -> AssignmentResult:
    """Greedy assign: gán môn-lớp khó nhất trước, mỗi lần chọn GV có score cao nhất.

    max_spw_per_teacher: tổng buổi/tuần tối đa cho 1 GV (tính cả trung_cap + cao_đẳng).
    max_spw_trung_cap: constraint của lớp học (1 lớp TC chỉ có 6 buổi sáng/tuần),
                       không áp lên GV — GV có thể dạy nhiều lớp TC + CĐ cùng tuần.
    """
    locked = locked or {}
    res = AssignmentResult()

    # Build index: subject_code → list[(teacher_id, priority)]
    subject_to_teachers: Dict[str, List[Tuple[str, int]]] = {}
    for (tid, sub), prio in teacher_subjects.items():
        subject_to_teachers.setdefault(sub, []).append((tid, prio))

    # Track tải theo tuần: teacher_week_load[tid][week] = tổng spw trong tuần đó
    # Dùng để kiểm tra peak load thực sự thay vì cộng dồn toàn kỳ.
    from collections import defaultdict as _dd
    teacher_week_load: Dict[str, Dict[int, int]] = _dd(lambda: _dd(int))
    teacher_load: Dict[str, int] = {}  # tổng total_sessions — dùng cho load_balance_score
    # Đếm số lớp đã gán theo (teacher_id, subject_code) — dùng cho same_subject_penalty
    teacher_subject_count: Dict[Tuple[str, str], int] = _dd(int)

    def _week_range(d: SubjectClassDemand) -> range:
        if d.week_start is None or d.week_end is None:
            return range(0, 0)
        excl = d.excluded_weeks or set()
        return range(d.week_start, d.week_end + 1)

    def _peak_load_after(tid: str, d: SubjectClassDemand) -> int:
        """Trả về peak spw tổng nếu gán thêm demand d cho GV tid.

        Tính max spw trong các tuần d thực dạy (bỏ excluded_weeks).
        max_spw_trung_cap là constraint của lớp, không phải của GV —
        GV có thể dạy cả trung_cap lẫn cao_đẳng trong cùng tuần.
        """
        excl = d.excluded_weeks or set()
        weeks = [w for w in _week_range(d) if w not in excl]
        if not weeks:
            cur_all = sum(v for k, v in teacher_week_load[tid].items() if k > 0)
            return (cur_all or 0) + d.sessions_per_week
        return max(teacher_week_load[tid].get(w, 0) + d.sessions_per_week for w in weeks)

    def _commit_load(tid: str, d: SubjectClassDemand) -> None:
        excl = d.excluded_weeks or set()
        weeks = [w for w in _week_range(d) if w not in excl]
        if not weeks:
            teacher_week_load[tid][0] = teacher_week_load[tid].get(0, 0) + d.sessions_per_week
        else:
            for w in weeks:
                teacher_week_load[tid][w] = teacher_week_load[tid].get(w, 0) + d.sessions_per_week

    # Apply locked trước
    for d in demands:
        key = (d.subject_code, d.class_id)
        if key in locked:
            tid = locked[key]
            res.assignments[key] = tid
            teacher_load[tid] = teacher_load.get(tid, 0) + d.total_sessions
            teacher_subject_count[(tid, d.subject_code)] += 1
            _commit_load(tid, d)

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
        # Dùng peak load theo tuần thực dạy thay vì cộng dồn toàn kỳ:
        # GV dạy môn A tuần 24-31 và môn B tuần 33-41 → mỗi tuần chỉ 1 môn, không full.
        def _can_take(tid: str) -> bool:
            return _peak_load_after(tid, d) <= max_spw_per_teacher

        available_candidates = [(tid, prio) for tid, prio in candidates if _can_take(tid)]
        if not available_candidates:
            full_teachers = ", ".join(
                f"{tid}({max((v for k, v in teacher_week_load[tid].items() if k > 0), default=0)}b/t)"
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
            avail_bonus, avail_reason = _availability_detail(
                tid, d.program_level, d.sessions_per_week, availability, days,
                week_start=d.week_start,
                week_end=d.week_end,
                excluded_weeks=d.excluded_weeks,
            )
            load_bonus = _load_balance_score(tid, teacher_load, avg_load)
            n_same = teacher_subject_count.get((tid, d.subject_code), 0)
            same_subject_penalty = -_SAME_SUBJECT_PENALTY * n_same
            score = prio_bonus + type_bonus + avail_bonus + load_bonus + same_subject_penalty
            scored.append((score, tid, prio, {
                "prio": prio_bonus,
                "type": type_bonus,
                "avail": avail_bonus,
                "avail_reason": avail_reason,
                "load": load_bonus,
                "same_sub": same_subject_penalty,
            }))

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_tid, best_prio, breakdown = scored[0]

        # Chỉ cảnh báo khi GV thực sự thiếu slot (avail_bonus âm = -1000),
        # không cảnh báo khi score âm chỉ vì same_subject_penalty (GV bị trừ vì
        # đã ôm lớp cùng môn — đây là behavior đúng, cố tình chia lớp).
        if breakdown["avail"] < 0:
            reason_str = breakdown.get("avail_reason", "")
            reason_detail = f": {reason_str}" if reason_str else ""
            res.warnings.append(
                f"[assign] {d.dept_code}/{d.program_level} {d.class_id} | {d.subject_name}: "
                f"GV {best_tid} không đủ slot khả dụng{reason_detail} (score={best_score})"
            )

        res.assignments[(d.subject_code, d.class_id)] = best_tid
        teacher_load[best_tid] = teacher_load.get(best_tid, 0) + d.total_sessions
        teacher_subject_count[(best_tid, d.subject_code)] += 1
        _commit_load(best_tid, d)

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
            "Trạng thái": "OK" if breakdown["avail"] >= 0 else "Thiếu slot",
            "_chi tiết score": (
                f"prio={breakdown['prio']} type={breakdown['type']} "
                f"avail={breakdown['avail']} load={breakdown['load']} "
                f"same_sub={breakdown['same_sub']}"
                + (f" | lý do avail: {breakdown['avail_reason']}" if breakdown.get("avail_reason") else "")
            ),
            "Ứng viên khác": ", ".join(
                f"{t}({s})" for s, t, _, _ in scored[1:4]
            ),
        })

    return res

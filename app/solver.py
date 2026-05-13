from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ortools.sat.python import cp_model

from .schemas import Assignment, Classroom, GenerateResponse, ScheduledSession


@dataclass(frozen=True)
class _StartKey:
    a_id: int
    day_idx: int
    p_start: int  # 1-based
    room_idx: int


def _session_day_blocks(
    periods_per_day: int,
    morning_periods: Optional[List[int]],
    afternoon_periods: Optional[List[int]],
) -> List[Tuple[int, int]]:
    """Khối tiết liên tiếp trong ngày: (sáng), (chiều). Một buổi học không được vượt qua ranh giới."""
    if morning_periods and afternoon_periods:
        mp = sorted({int(p) for p in morning_periods})
        ap = sorted({int(p) for p in afternoon_periods})
        return [(mp[0], mp[-1]), (ap[0], ap[-1])]
    return [(1, periods_per_day)]


def _candidate_starts_within_blocks(cluster: int, blocks: List[Tuple[int, int]]) -> List[int]:
    starts: List[int] = []
    for lo, hi in blocks:
        last_start = hi - cluster + 1
        if last_start >= lo:
            starts.extend(range(lo, last_start + 1))
    return starts


def _covers(p_start: int, cluster: int, p: int) -> bool:
    return p_start <= p <= (p_start + cluster - 1)


def solve_weekly_timetable(
    *,
    days: List[int],
    periods_per_day: int,
    assignments: List[Assignment],
    classrooms: List[Classroom],
    morning_periods: Optional[List[int]] = None,
    afternoon_periods: Optional[List[int]] = None,
    max_time_seconds: float = 20.0,
) -> GenerateResponse:
    """
    Xếp lịch theo tuần (weekly pattern).
    Kết quả áp dụng cho toàn bộ dải tuần week_start..week_end của từng assignment.
    """
    if not assignments:
        return GenerateResponse(status="FEASIBLE", sessions=[], objective=0, message="No assignments")

    a_by_id: Dict[int, Assignment] = {int(a.id): a for a in assignments}

    # Rooms candidates by classroom_type
    room_indices: List[int] = list(range(len(classrooms)))
    rooms_by_type: Dict[int, List[int]] = {}
    for ridx, r in enumerate(classrooms):
        if r.type is None:
            continue
        rooms_by_type.setdefault(int(r.type), []).append(ridx)

    # Build model
    model = cp_model.CpModel()

    # Decision vars:
    # x[(assignment, day_idx, period_start, room_idx)] == 1
    x: Dict[_StartKey, cp_model.IntVar] = {}

    blocks = _session_day_blocks(periods_per_day, morning_periods, afternoon_periods)

    split_half_days = bool(morning_periods and afternoon_periods)
    if split_half_days:
        lens = [hi - lo + 1 for lo, hi in blocks]
        if len(lens) != 2 or lens[0] != lens[1]:
            return GenerateResponse(
                status="INFEASIBLE",
                sessions=[],
                message=(
                    "Chế độ sáng/chiều yêu cầu hai khối tiết liên tiếp cùng độ dài "
                    f"(hiện có {lens})."
                ),
            )
        required_cluster = lens[0]
        for a in assignments:
            if int(a.lessons_cluster) != required_cluster:
                return GenerateResponse(
                    status="INFEASIBLE",
                    sessions=[],
                    message=(
                        f"Assignment {a.id}: mỗi buổi phải học đúng {required_cluster} tiết liền một môn "
                        f"(lessons_cluster={a.lessons_cluster})."
                    ),
                )

    for a in assignments:
        candidate_rooms = rooms_by_type.get(int(a.classroom_type), room_indices)
        starts = _candidate_starts_within_blocks(int(a.lessons_cluster), blocks)
        if not starts:
            return GenerateResponse(
                status="INFEASIBLE",
                sessions=[],
                message=(
                    f"Assignment {a.id}: lessons_cluster={a.lessons_cluster} không gọn trong một buổi "
                    f"(sáng hoặc chiều); kiểm tra khối tiết hoặc giảm lessons_cluster"
                ),
            )
        if not candidate_rooms:
            return GenerateResponse(
                status="INFEASIBLE",
                sessions=[],
                message=f"Assignment {a.id}: không có phòng phù hợp classroom_type={a.classroom_type}",
            )

        for d_i in range(len(days)):
            for p_start in starts:
                for r_i in candidate_rooms:
                    key = _StartKey(a_id=a.id, day_idx=d_i, p_start=p_start, room_idx=r_i)
                    x[key] = model.new_bool_var(f"x__a{a.id}__d{d_i}__p{p_start}__r{r_i}")

    # Exactly sessions_per_week starts per assignment
    for a in assignments:
        vars_for_a = [var for k, var in x.items() if k.a_id == a.id]
        model.add(sum(vars_for_a) == int(a.sessions_per_week))

    # Room conflict: one class per room per (day, period)
    for d_i in range(len(days)):
        for p in range(1, periods_per_day + 1):
            for r_i in range(len(classrooms)):
                room_vars = []
                for k, var in x.items():
                    if k.day_idx != d_i or k.room_idx != r_i:
                        continue
                    a = a_by_id.get(int(k.a_id))
                    if not a:
                        continue
                    if _covers(k.p_start, int(a.lessons_cluster), p):
                        room_vars.append(var)
                if room_vars:
                    model.add(sum(room_vars) <= 1)

    # Teacher conflict: each teacher max 1 assignment per (day, period)
    teacher_ids = sorted({str(a.teacher_id) for a in assignments})
    for d_i in range(len(days)):
        for p in range(1, periods_per_day + 1):
            for t_id in teacher_ids:
                t_vars = []
                for k, var in x.items():
                    if k.day_idx != d_i:
                        continue
                    a = a_by_id.get(int(k.a_id))
                    if (not a) or str(a.teacher_id) != t_id:
                        continue
                    if _covers(k.p_start, int(a.lessons_cluster), p):
                        t_vars.append(var)
                if t_vars:
                    model.add(sum(t_vars) <= 1)

    # Cùng lớp SV (class_group_id): không học 2 môn một lúc
    group_ids = sorted({int(a.class_group_id) for a in assignments})
    for d_i in range(len(days)):
        for p in range(1, periods_per_day + 1):
            for g_id in group_ids:
                g_vars = []
                for k, var in x.items():
                    if k.day_idx != d_i:
                        continue
                    a = a_by_id.get(int(k.a_id))
                    if (not a) or int(a.class_group_id) != g_id:
                        continue
                    if _covers(k.p_start, int(a.lessons_cluster), p):
                        g_vars.append(var)
                if g_vars:
                    model.add(sum(g_vars) <= 1)

    # Objective (soft): ưu tiên tiết sớm (tiết 1 > tiết 2 > …) trong phạm vi đã mở biến
    objective_terms: List[cp_model.LinearExpr] = []
    for k, var in x.items():
        w = periods_per_day + 1 - int(k.p_start)
        objective_terms.append(var * int(w))
    if objective_terms:
        model.maximize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(max_time_seconds)
    solver.parameters.num_search_workers = 4

    status = solver.solve(model)
    status_str = {
        cp_model.OPTIMAL: "OPTIMAL",
        cp_model.FEASIBLE: "FEASIBLE",
        cp_model.INFEASIBLE: "INFEASIBLE",
        cp_model.MODEL_INVALID: "UNKNOWN",
        cp_model.UNKNOWN: "UNKNOWN",
    }.get(status, "UNKNOWN")

    if status_str not in ("OPTIMAL", "FEASIBLE"):
        return GenerateResponse(status=status_str, sessions=[], message="No feasible schedule")

    # Build sessions result
    sessions: List[ScheduledSession] = []
    for k, var in x.items():
        if solver.value(var) != 1:
            continue
        a = a_by_id.get(int(k.a_id))
        if not a:
            continue
        day_value = int(days[k.day_idx])
        period_start = int(k.p_start)
        period_end = int(k.p_start + int(a.lessons_cluster) - 1)
        room = classrooms[int(k.room_idx)]
        sessions.append(
            ScheduledSession(
                assignment_id=int(a.id),
                teacher_id=str(a.teacher_id),
                course_id=int(a.course_id),
                classroom_id=int(room.id),
                day=day_value,
                period_start=period_start,
                period_end=period_end,
                week_start=int(a.week_start),
                week_end=int(a.week_end),
            )
        )

    objective = int(solver.objective_value) if objective_terms else None
    return GenerateResponse(status=status_str, objective=objective, sessions=sessions)

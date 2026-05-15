from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ortools.sat.python import cp_model

from .block_splitter import (
    WeekBlock,
    assignments_in_block,
    compute_week_blocks,
    teacher_busy_in_block,
)
from .schemas import Assignment, Classroom, GenerateResponse, ScheduledSession
from .timetable_loader import TeacherBusyEntry


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


def _solve_single_block(
    *,
    block: WeekBlock,
    days: List[int],
    periods_per_day: int,
    assignments: List[Assignment],
    classrooms: List[Classroom],
    teacher_busy: List[TeacherBusyEntry],
    morning_periods: Optional[List[int]],
    afternoon_periods: Optional[List[int]],
    max_time_seconds: float,
) -> Tuple[str, List[ScheduledSession], Optional[int], Optional[str]]:
    """Giải 1 block tuần. Trả về (status, sessions, objective, message)."""

    if not assignments:
        return ("FEASIBLE", [], 0, None)

    a_by_id: Dict[int, Assignment] = {int(a.id): a for a in assignments}

    room_indices: List[int] = list(range(len(classrooms)))
    rooms_by_type: Dict[int, List[int]] = {}
    for ridx, r in enumerate(classrooms):
        if r.type is None:
            continue
        rooms_by_type.setdefault(int(r.type), []).append(ridx)

    model = cp_model.CpModel()
    x: Dict[_StartKey, cp_model.IntVar] = {}

    time_blocks = _session_day_blocks(periods_per_day, morning_periods, afternoon_periods)

    for a in assignments:
        candidate_rooms = rooms_by_type.get(int(a.classroom_type), room_indices)
        starts = _candidate_starts_within_blocks(int(a.lessons_cluster), time_blocks)
        if not starts:
            return (
                "INFEASIBLE", [], None,
                f"Assignment {a.id}: lessons_cluster={a.lessons_cluster} không gọn trong một buổi",
            )
        if not candidate_rooms:
            return (
                "INFEASIBLE", [], None,
                f"Assignment {a.id}: không có phòng phù hợp classroom_type={a.classroom_type}",
            )

        for d_i in range(len(days)):
            for p_start in starts:
                for r_i in candidate_rooms:
                    key = _StartKey(a_id=a.id, day_idx=d_i, p_start=p_start, room_idx=r_i)
                    x[key] = model.new_bool_var(f"x__a{a.id}__d{d_i}__p{p_start}__r{r_i}")

    # Exactly sessions_per_week per assignment
    for a in assignments:
        vars_for_a = [var for k, var in x.items() if k.a_id == a.id]
        model.add(sum(vars_for_a) == int(a.sessions_per_week))

    # Teacher busy: ép var=0 cho các slot GV bận
    busy_set: Dict[str, set] = {}
    for entry in teacher_busy:
        key = entry.teacher_id
        if key not in busy_set:
            busy_set[key] = set()
        day_idx = None
        for di, d in enumerate(days):
            if d == entry.day_of_week:
                day_idx = di
                break
        if day_idx is None:
            continue
        for p in range(entry.period_start, entry.period_end + 1):
            busy_set[key].add((day_idx, p))

    for k, var in x.items():
        a = a_by_id.get(int(k.a_id))
        if not a:
            continue
        tid = str(a.teacher_id)
        if tid not in busy_set:
            continue
        teacher_busy_slots = busy_set[tid]
        for p in range(k.p_start, k.p_start + int(a.lessons_cluster)):
            if (k.day_idx, p) in teacher_busy_slots:
                model.add(var == 0)
                break

    # Room conflict
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

    # Teacher conflict
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

    # Class group conflict
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

    # Objective: ưu tiên tiết sớm
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
        return (status_str, [], None, f"Block {block.block_id} (tuần {block.week_start}-{block.week_end}): no feasible schedule")

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
                course_id=str(a.course_id),
                classroom_id=int(room.id),
                day=day_value,
                period_start=period_start,
                period_end=period_end,
                week_start=block.week_start,
                week_end=block.week_end,
                department_code=str(a.department_code),
            )
        )

    objective = int(solver.objective_value) if objective_terms else None
    return (status_str, sessions, objective, None)


def solve_weekly_timetable(
    *,
    days: List[int],
    periods_per_day: int,
    assignments: List[Assignment],
    classrooms: List[Classroom],
    morning_periods: Optional[List[int]] = None,
    afternoon_periods: Optional[List[int]] = None,
    teacher_busy: Optional[List[TeacherBusyEntry]] = None,
    max_time_seconds: float = 20.0,
) -> GenerateResponse:
    """
    Xếp lịch theo block tuần.
    Tự động chia dải tuần thành các block dựa trên week_start/week_end
    của assignments và teacher_busy. Giải mỗi block riêng.
    """
    if not assignments:
        return GenerateResponse(status="FEASIBLE", sessions=[], objective=0, message="No assignments")

    if teacher_busy is None:
        teacher_busy = []

    week_lo = min(int(a.week_start) for a in assignments)
    week_hi = max(int(a.week_end) for a in assignments)

    blocks = compute_week_blocks(week_lo, week_hi, assignments, teacher_busy)

    all_sessions: List[ScheduledSession] = []
    total_objective = 0
    worst_status = "OPTIMAL"

    time_per_block = max(10.0, max_time_seconds / max(1, len(blocks)))

    for block in blocks:
        block_assignments = assignments_in_block(block, assignments)
        block_busy = teacher_busy_in_block(block, teacher_busy)

        if not block_assignments:
            continue

        status_str, sessions, obj, msg = _solve_single_block(
            block=block,
            days=days,
            periods_per_day=periods_per_day,
            assignments=block_assignments,
            classrooms=classrooms,
            teacher_busy=block_busy,
            morning_periods=morning_periods,
            afternoon_periods=afternoon_periods,
            max_time_seconds=time_per_block,
        )

        if status_str == "INFEASIBLE":
            return GenerateResponse(status="INFEASIBLE", sessions=[], message=msg)
        if status_str == "UNKNOWN":
            worst_status = "UNKNOWN"
        elif status_str == "FEASIBLE" and worst_status == "OPTIMAL":
            worst_status = "FEASIBLE"

        all_sessions.extend(sessions)
        if obj is not None:
            total_objective += obj

    return GenerateResponse(
        status=worst_status,
        objective=total_objective if all_sessions else None,
        sessions=all_sessions,
    )

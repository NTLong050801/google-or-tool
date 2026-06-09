from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from ortools.sat.python import cp_model

from .scheduling_config import SchedulingConfig
from .schemas import (
    Assignment, AvailabilityIssue, AvailabilityReport,
    Classroom, GenerateResponse, ScheduledSession,
)


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


def solve_weekly_timetable(
    *,
    days: List[int],
    periods_per_day: int,
    assignments: List[Assignment],
    classrooms: List[Classroom],
    morning_periods: Optional[List[int]] = None,
    afternoon_periods: Optional[List[int]] = None,
    availability: Optional[Dict[str, Set[Tuple[int, int, int]]]] = None,
    assignment_labels: Optional[Dict[int, Dict[str, str]]] = None,
    config: Optional[SchedulingConfig] = None,
    holiday_weeks: Optional[Set[int]] = None,
    holiday_reasons: Optional[Dict[int, str]] = None,
    max_time_seconds: float = 120.0,
) -> GenerateResponse:
    """Xếp lịch theo tuần với availability + config rules."""

    if not assignments:
        return GenerateResponse(status="FEASIBLE", sessions=[], objective=0, message="No assignments")

    if availability is None:
        availability = {}
    if assignment_labels is None:
        assignment_labels = {}
    if config is None:
        config = SchedulingConfig()
    if holiday_weeks is None:
        holiday_weeks = set()
    if holiday_reasons is None:
        holiday_reasons = {}

    a_by_id: Dict[int, Assignment] = {int(a.id): a for a in assignments}

    room_indices: List[int] = list(range(len(classrooms)))
    rooms_by_type: Dict[int, List[int]] = {}
    for ridx, r in enumerate(classrooms):
        if r.type is None:
            continue
        rooms_by_type.setdefault(int(r.type), []).append(ridx)

    model = cp_model.CpModel()
    x: Dict[_StartKey, cp_model.IntVar] = {}

    rooms_missing_capacity: Set[int] = set()
    assignments_missing_size: Set[int] = set()

    time_blocks = _session_day_blocks(periods_per_day, morning_periods, afternoon_periods)
    morning_starts = _candidate_starts_within_blocks(
        config.default_lessons_cluster,
        [(morning_periods[0], morning_periods[-1])] if morning_periods else []
    )

    # Build map cho fixed_rooms_by_subject_keyword: room_name (lowercase string) → r_i
    rooms_by_name: Dict[str, int] = {}
    for ridx, r in enumerate(classrooms):
        rooms_by_name[str(r.id).strip().lower()] = ridx
        if r.name:
            rooms_by_name[str(r.name).strip().lower()] = ridx

    fixed_rules: List[Tuple[List[str], Set[int]]] = []
    for entry in (config.fixed_rooms_by_subject_keyword or []):
        keywords = [str(k).strip().lower() for k in entry.get("keywords", []) if str(k).strip()]
        names = [str(n).strip().lower() for n in entry.get("room_names", []) if str(n).strip()]
        room_idx_set = {rooms_by_name[n] for n in names if n in rooms_by_name}
        if keywords and room_idx_set:
            fixed_rules.append((keywords, room_idx_set))

    # --- Build variables ---
    for a in assignments:
        label = assignment_labels.get(int(a.id), {})
        program_level = label.get("program_level", "")

        candidate_rooms = rooms_by_type.get(int(a.classroom_type), room_indices)
        if config.match_room_type and not candidate_rooms:
            return GenerateResponse(
                status="INFEASIBLE", sessions=[],
                message=f"Assignment {a.id}: không có phòng phù hợp classroom_type={a.classroom_type}",
            )

        # Override: môn match keyword chỉ được xếp vào phòng cố định
        subject_name_lc = (label.get("subject_name") or "").strip().lower()
        for keywords, room_idx_set in fixed_rules:
            if any(kw in subject_name_lc for kw in keywords):
                candidate_rooms = [r_i for r_i in room_indices if r_i in room_idx_set]
                if not candidate_rooms:
                    return GenerateResponse(
                        status="INFEASIBLE", sessions=[],
                        message=f"Assignment {a.id} ({label.get('subject_name','?')}): không có phòng cố định khả dụng",
                    )
                break

        # Lọc phòng theo priority_departments / home_department
        # Phòng được dùng nếu priority_departments rỗng (mọi khoa)
        # hoặc dept của assignment có trong priority_departments
        # hoặc home_department khớp với dept của assignment
        dept_code = str(a.department_code).strip().upper()
        if dept_code:
            filtered_rooms: List[int] = []
            for r_i in candidate_rooms:
                room = classrooms[r_i]
                priority = [p.upper() for p in (room.priority_departments or [])]
                home = (room.home_department or "").strip().upper()
                if not priority:
                    filtered_rooms.append(r_i)
                elif dept_code in priority or dept_code == home:
                    filtered_rooms.append(r_i)
            if filtered_rooms:
                candidate_rooms = filtered_rooms

        # Lọc phòng theo capacity (hard): capacity >= class_size
        # Bỏ qua check khi class_size hoặc capacity thiếu data (kèm warning)
        if config.match_room_capacity and a.class_size is not None:
            sized_rooms: List[int] = []
            for r_i in candidate_rooms:
                room = classrooms[r_i]
                if room.capacity is None:
                    rooms_missing_capacity.add(int(r_i))
                    sized_rooms.append(r_i)
                elif int(room.capacity) >= int(a.class_size):
                    sized_rooms.append(r_i)
            candidate_rooms = sized_rooms
            if not candidate_rooms:
                return GenerateResponse(
                    status="INFEASIBLE", sessions=[],
                    message=f"Assignment {a.id}: không có phòng đủ capacity cho class_size={a.class_size}",
                )
        elif config.match_room_capacity and a.class_size is None:
            assignments_missing_size.add(int(a.id))

        if config.trung_cap_morning_only and program_level == "trung_cap":
            starts = morning_starts
        else:
            starts = _candidate_starts_within_blocks(int(a.lessons_cluster), time_blocks)

        if not starts:
            return GenerateResponse(
                status="INFEASIBLE", sessions=[],
                message=f"Assignment {a.id}: lessons_cluster={a.lessons_cluster} không gọn trong buổi cho phép",
            )

        for d_i in range(len(days)):
            for p_start in starts:
                for r_i in candidate_rooms:
                    key = _StartKey(a_id=a.id, day_idx=d_i, p_start=p_start, room_idx=r_i)
                    x[key] = model.new_bool_var(f"x__a{a.id}__d{d_i}__p{p_start}__r{r_i}")

    # --- Build indexes for fast constraint generation ---
    vars_by_assignment: Dict[int, List[Tuple[_StartKey, cp_model.IntVar]]] = {}
    vars_by_day_room: Dict[Tuple[int, int], List[Tuple[_StartKey, cp_model.IntVar]]] = {}
    vars_by_day_teacher: Dict[Tuple[int, str], List[Tuple[_StartKey, cp_model.IntVar]]] = {}
    vars_by_day_group: Dict[Tuple[int, int], List[Tuple[_StartKey, cp_model.IntVar]]] = {}

    for k, var in x.items():
        a = a_by_id.get(int(k.a_id))
        if not a:
            continue
        pair = (k, var)
        vars_by_assignment.setdefault(k.a_id, []).append(pair)
        vars_by_day_room.setdefault((k.day_idx, k.room_idx), []).append(pair)
        vars_by_day_teacher.setdefault((k.day_idx, str(a.teacher_id)), []).append(pair)
        vars_by_day_group.setdefault((k.day_idx, int(a.class_group_id)), []).append(pair)

    # --- Constraint: exactly sessions_per_week per assignment ---
    for a in assignments:
        a_vars = vars_by_assignment.get(a.id, [])
        model.add(sum(var for _, var in a_vars) == int(a.sessions_per_week))

    # --- Availability constraint ---
    # Pre-check: skip teachers whose load exceeds available day-sessions
    skipped_teachers: Set[str] = set()
    if config.respect_teacher_availability:
        teacher_spw: Dict[str, int] = {}
        teacher_day_sessions: Dict[str, Set[Tuple[int, int]]] = {}
        for a in assignments:
            tid = str(a.teacher_id)
            if tid not in availability:
                continue
            teacher_spw[tid] = teacher_spw.get(tid, 0) + int(a.sessions_per_week)
            if tid not in teacher_day_sessions:
                teacher_day_sessions[tid] = set()
            teacher_slots = availability[tid]
            # Tuần không được tính: nghỉ toàn trường + thi/dự phòng riêng lớp này
            skip_weeks = holiday_weeks | set(a.excluded_weeks)
            teaching_weeks_a = [
                w for w in range(int(a.week_start), int(a.week_end) + 1)
                if w not in skip_weeks
            ]
            min_weeks = max(1, math.ceil(len(teaching_weeks_a) * config.availability_week_threshold))
            for d in days:
                for s in [1, 2]:
                    avail_count = sum(1 for w in teaching_weeks_a if (w, d, s) in teacher_slots)
                    if avail_count >= min_weeks:
                        teacher_day_sessions[tid].add((d, s))

        for tid, spw in teacher_spw.items():
            if spw > len(teacher_day_sessions.get(tid, set())):
                skipped_teachers.add(tid)

    solver_warnings: List[str] = []
    avail_issues: List[AvailabilityIssue] = []

    if rooms_missing_capacity:
        sample_rooms = sorted(int(classrooms[r].id) for r in list(rooms_missing_capacity)[:5])
        extra = "" if len(rooms_missing_capacity) <= 5 else f" (và {len(rooms_missing_capacity) - 5} phòng khác)"
        solver_warnings.append(
            f"{len(rooms_missing_capacity)} phòng thiếu capacity → bỏ qua check sĩ số khi xếp: {sample_rooms}{extra}"
        )
    if assignments_missing_size:
        solver_warnings.append(
            f"{len(assignments_missing_size)} phân công thiếu class_size → bỏ qua check capacity"
        )

    # Build AvailabilityReport — nhóm 1: GV chưa đăng ký gì cả
    if config.respect_teacher_availability:
        missing_avail: Dict[str, Dict] = {}
        for a in assignments:
            tid = str(a.teacher_id)
            if tid in availability:
                continue
            label = assignment_labels.get(int(a.id), {})
            if tid not in missing_avail:
                missing_avail[tid] = {
                    "name": label.get("teacher_name", tid),
                    "type": label.get("teacher_type", ""),
                    "dept": str(a.department_code),
                    "spw": 0,
                    "affected": [],
                }
            missing_avail[tid]["spw"] += int(a.sessions_per_week)
            missing_avail[tid]["affected"].append(
                f"{label.get('subject_name', a.course_id)} "
                f"(lớp {label.get('class_id', '?')}, {a.sessions_per_week} buổi/tuần)"
            )
        for tid, info in missing_avail.items():
            avail_issues.append(AvailabilityIssue(
                department_code=info["dept"],
                teacher_id=tid,
                teacher_name=info["name"],
                teacher_type=info["type"],
                status="chua_dang_ky",
                weeks_registered=0,
                weeks_needed=info["spw"],
                slots_available=0,
                affected_classes=info["affected"],
            ))

    # Nhóm 2: GV có đăng ký nhưng thiếu slot
    for tid in sorted(skipped_teachers):
        teacher_name = tid
        teacher_type = ""
        dept = ""
        affected: List[str] = []
        for a in assignments:
            if str(a.teacher_id) != tid:
                continue
            label = assignment_labels.get(int(a.id), {})
            if teacher_name == tid:
                teacher_name = label.get("teacher_name", tid)
            if not teacher_type:
                teacher_type = label.get("teacher_type", "")
            if not dept:
                dept = str(a.department_code)
            affected.append(
                f"{label.get('subject_name', a.course_id)} "
                f"(lớp {label.get('class_id', '?')}, {a.sessions_per_week} buổi/tuần)"
            )
        slots = len(teacher_day_sessions.get(tid, set()))
        weeks_reg = len(set(w for w, d, s in availability.get(tid, set())))
        avail_issues.append(AvailabilityIssue(
            department_code=dept,
            teacher_id=tid,
            teacher_name=teacher_name,
            teacher_type=teacher_type,
            status="thieu_slot",
            weeks_registered=weeks_reg,
            weeks_needed=teacher_spw[tid],
            slots_available=slots,
            affected_classes=affected,
        ))

    avail_report = AvailabilityReport(issues=avail_issues)
    solver_warnings.extend(avail_report.summary_warnings())

    if config.respect_teacher_availability:
        for k, var in x.items():
            a = a_by_id.get(int(k.a_id))
            if not a:
                continue
            tid = str(a.teacher_id)
            if tid not in availability or tid in skipped_teachers:
                continue
            teacher_slots = availability[tid]
            day_value = days[k.day_idx]
            session_id = 1 if k.p_start <= (max(morning_periods) if morning_periods else 5) else 2
            # Bỏ qua nghỉ toàn trường + tuần thi/dự phòng riêng lớp này
            skip_weeks = holiday_weeks | set(a.excluded_weeks)
            teaching_weeks_k = [
                w for w in range(int(a.week_start), int(a.week_end) + 1)
                if w not in skip_weeks
            ]
            if teaching_weeks_k:
                avail_count = sum(1 for w in teaching_weeks_k if (w, day_value, session_id) in teacher_slots)
                min_weeks = max(1, math.ceil(len(teaching_weeks_k) * config.availability_week_threshold))
                if avail_count < min_weeks:
                    model.add(var == 0)

    # --- Room conflict: at most 1 assignment per room per period ---
    # Chỉ áp khi 2 assignment có tuần thực dạy overlap.
    if config.no_room_conflict:
        for (d_i, r_i), kv_list in vars_by_day_room.items():
            period_vars: Dict[int, List[Tuple[cp_model.IntVar, int]]] = {}
            for k, var in kv_list:
                a = a_by_id[k.a_id]
                cluster = int(a.lessons_cluster)
                for p in range(k.p_start, k.p_start + cluster):
                    period_vars.setdefault(p, []).append((var, k.a_id))
            for p, aid_vars in period_vars.items():
                if len(aid_vars) <= 1:
                    continue
                for i in range(len(aid_vars)):
                    for j in range(i + 1, len(aid_vars)):
                        var_i, aid_i = aid_vars[i]
                        var_j, aid_j = aid_vars[j]
                        tw_i = _teaching_weeks_set(a_by_id[aid_i])
                        tw_j = _teaching_weeks_set(a_by_id[aid_j])
                        if tw_i & tw_j:
                            model.add(var_i + var_j <= 1)

    # --- Teacher conflict: at most 1 assignment per teacher per period ---
    # Chỉ áp khi 2 assignment có tuần thực dạy overlap.
    if config.no_teacher_conflict:
        for (d_i, tid), kv_list in vars_by_day_teacher.items():
            period_vars: Dict[int, List[Tuple[cp_model.IntVar, int]]] = {}
            for k, var in kv_list:
                a = a_by_id[k.a_id]
                cluster = int(a.lessons_cluster)
                for p in range(k.p_start, k.p_start + cluster):
                    period_vars.setdefault(p, []).append((var, k.a_id))
            for p, aid_vars in period_vars.items():
                if len(aid_vars) <= 1:
                    continue
                for i in range(len(aid_vars)):
                    for j in range(i + 1, len(aid_vars)):
                        var_i, aid_i = aid_vars[i]
                        var_j, aid_j = aid_vars[j]
                        tw_i = _teaching_weeks_set(a_by_id[aid_i])
                        tw_j = _teaching_weeks_set(a_by_id[aid_j])
                        if tw_i & tw_j:
                            model.add(var_i + var_j <= 1)

    # --- Class group conflict: at most 1 assignment per group per period ---
    # Chỉ áp constraint khi 2 assignment có tuần thực dạy giao nhau.
    # 2 môn cùng lớp nhưng dạy ở 2 đợt tách biệt (tuần không overlap) được phép
    # xếp cùng slot (day, period) vì thực tế không xung đột.
    if config.no_class_group_conflict:
        # Tính teaching_weeks cho mỗi assignment (dùng lại holiday_weeks)
        def _teaching_weeks_set(a: Assignment) -> Set[int]:
            excl = set(a.excluded_weeks) | holiday_weeks
            return {w for w in range(int(a.week_start), int(a.week_end) + 1) if w not in excl}

        for (d_i, g_id), kv_list in vars_by_day_group.items():
            # Gom theo period, nhưng chỉ pair các var có tuần overlap
            period_vars: Dict[int, List[Tuple[cp_model.IntVar, int]]] = {}
            for k, var in kv_list:
                a = a_by_id[k.a_id]
                cluster = int(a.lessons_cluster)
                for p in range(k.p_start, k.p_start + cluster):
                    period_vars.setdefault(p, []).append((var, k.a_id))
            for p, aid_vars in period_vars.items():
                if len(aid_vars) <= 1:
                    continue
                # Với mỗi cặp (i, j), chỉ add constraint nếu tuần overlap
                for i in range(len(aid_vars)):
                    for j in range(i + 1, len(aid_vars)):
                        var_i, aid_i = aid_vars[i]
                        var_j, aid_j = aid_vars[j]
                        tw_i = _teaching_weeks_set(a_by_id[aid_i])
                        tw_j = _teaching_weeks_set(a_by_id[aid_j])
                        if tw_i & tw_j:
                            model.add(var_i + var_j <= 1)

    # --- Objective ---
    objective_terms: List[cp_model.LinearExpr] = []

    if config.prefer_early_periods.enabled:
        w_early = config.prefer_early_periods.weight
        for k, var in x.items():
            score = (periods_per_day + 1 - int(k.p_start)) * w_early
            objective_terms.append(var * score)

    if config.prioritize_thinh_giang.enabled:
        w_tg = config.prioritize_thinh_giang.weight
        for k, var in x.items():
            a = a_by_id.get(int(k.a_id))
            if not a:
                continue
            label = assignment_labels.get(int(a.id), {})
            if "thỉnh giảng" in label.get("teacher_type", "").lower():
                objective_terms.append(var * w_tg)

    if config.prioritize_trung_cap.enabled:
        w_tc = config.prioritize_trung_cap.weight
        for k, var in x.items():
            a = a_by_id.get(int(k.a_id))
            if not a:
                continue
            label = assignment_labels.get(int(a.id), {})
            if label.get("program_level") == "trung_cap":
                objective_terms.append(var * w_tc)

    if config.prefer_room_fit.enabled:
        w_fit = config.prefer_room_fit.weight
        for k, var in x.items():
            a = a_by_id.get(int(k.a_id))
            if not a or a.class_size is None:
                continue
            room = classrooms[int(k.room_idx)]
            if room.capacity is None:
                continue
            slack = max(0, int(room.capacity) - int(a.class_size))
            penalty = (slack // 10) * w_fit
            if penalty:
                objective_terms.append(var * (-penalty))

    if objective_terms:
        model.maximize(sum(objective_terms))

    # --- Solve ---
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(max_time_seconds)
    solver.parameters.num_search_workers = config.num_workers

    status = solver.solve(model)
    status_str = {
        cp_model.OPTIMAL: "OPTIMAL",
        cp_model.FEASIBLE: "FEASIBLE",
        cp_model.INFEASIBLE: "INFEASIBLE",
        cp_model.MODEL_INVALID: "UNKNOWN",
        cp_model.UNKNOWN: "UNKNOWN",
    }.get(status, "UNKNOWN")

    if status_str not in ("OPTIMAL", "FEASIBLE"):
        return GenerateResponse(status=status_str, sessions=[], message="No feasible schedule", warnings=solver_warnings)

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

        # Tính tuần thực dạy: loại nghỉ lễ toàn trường + tuần thi/dự phòng riêng lớp
        all_skipped: Dict[int, str] = {}
        for w in range(int(a.week_start), int(a.week_end) + 1):
            if w in holiday_weeks:
                all_skipped[w] = holiday_reasons.get(w, "Nghỉ")
            elif w in a.excluded_weeks:
                # Dùng lý do thực (thi, du_phong, ...) nếu có, fallback "excluded"
                all_skipped[w] = a.excluded_week_reasons.get(w, "excluded")
        teaching_weeks = sorted(
            w for w in range(int(a.week_start), int(a.week_end) + 1)
            if w not in all_skipped
        )

        sessions.append(
            ScheduledSession(
                assignment_id=int(a.id),
                teacher_id=str(a.teacher_id),
                course_id=str(a.course_id),
                classroom_id=int(room.id),
                day=day_value,
                period_start=period_start,
                period_end=period_end,
                week_start=int(a.week_start),
                week_end=int(a.week_end),
                department_code=str(a.department_code),
                teaching_weeks=teaching_weeks,
                skipped_weeks=all_skipped,
            )
        )

    objective = int(solver.objective_value) if objective_terms else None
    return GenerateResponse(
        status=status_str,
        objective=objective,
        sessions=sessions,
        warnings=solver_warnings,
        availability_report=avail_report,
    )

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
class _SlotKey:
    a_id: int
    day_idx: int
    p_start: int  # 1-based


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


def _assign_rooms(
    slot_results: List[Tuple[Assignment, int, int]],  # (assignment, day_value, p_start)
    classrooms: List[Classroom],
    rooms_by_type: Dict[int, List[int]],
    room_indices: List[int],
    fixed_rules: List[Tuple[List[str], Set[int]]],
    assignment_labels: Dict[int, Dict[str, str]],
    holiday_weeks: Set[int],
    config: SchedulingConfig,
) -> Dict[int, int]:
    """Greedy room assignment sau khi CP-SAT đã xếp slot.

    Trả về {assignment_id: room_idx}.
    Ưu tiên: phòng vừa sĩ số (capacity gần class_size nhất).
    Đảm bảo HC-2: không 2 assignment có tuần overlap dùng cùng phòng cùng (day, period).
    """
    # room_busy[(day_value, p, room_idx)] = set of teaching_weeks đang bị chiếm
    room_busy: Dict[Tuple[int, int, int], Set[int]] = {}

    result: Dict[int, int] = {}

    def teaching_weeks_set(a: Assignment) -> Set[int]:
        excl = set(a.excluded_weeks) | holiday_weeks
        return {w for w in range(int(a.week_start), int(a.week_end) + 1) if w not in excl}

    # Sắp xếp: class_size lớn trước (phòng lớn khó gán hơn)
    sorted_slots = sorted(
        slot_results,
        key=lambda t: -(int(t[0].class_size) if t[0].class_size is not None else 0),
    )

    for a, day_value, p_start in sorted_slots:
        cluster = int(a.lessons_cluster)
        label = assignment_labels.get(int(a.id), {})
        subject_name_lc = (label.get("subject_name") or "").strip().lower()
        dept_code = str(a.department_code).strip().upper()
        tw = teaching_weeks_set(a)

        # Xác định candidate rooms (cùng logic với phần build biến cũ)
        candidate_rooms = list(rooms_by_type.get(int(a.classroom_type), room_indices))

        # fixed_rooms override
        for keywords, room_idx_set in fixed_rules:
            if any(kw in subject_name_lc for kw in keywords):
                candidate_rooms = [r_i for r_i in room_indices if r_i in room_idx_set]
                break

        # lọc priority_departments
        if dept_code:
            filtered = []
            for r_i in candidate_rooms:
                room = classrooms[r_i]
                priority = [p.upper() for p in (room.priority_departments or [])]
                home = (room.home_department or "").strip().upper()
                if not priority or dept_code in priority or dept_code == home:
                    filtered.append(r_i)
            if filtered:
                candidate_rooms = filtered

        # lọc capacity
        if config.match_room_capacity and a.class_size is not None:
            sized = [
                r_i for r_i in candidate_rooms
                if classrooms[r_i].capacity is None
                or int(classrooms[r_i].capacity) >= int(a.class_size)
            ]
            if sized:
                candidate_rooms = sized

        # Lọc phòng còn trống cho (day, period range) với teaching_weeks này
        periods = list(range(p_start, p_start + cluster))
        available = []
        for r_i in candidate_rooms:
            conflict = False
            for p in periods:
                busy_weeks = room_busy.get((day_value, p, r_i), set())
                if busy_weeks & tw:
                    conflict = True
                    break
            if not conflict:
                available.append(r_i)

        if not available:
            # Fallback: dùng bất kỳ phòng nào trong candidate (có thể conflict nhỏ)
            available = candidate_rooms if candidate_rooms else room_indices

        # Chọn phòng vừa sĩ số nhất
        def room_score(r_i: int) -> int:
            cap = classrooms[r_i].capacity
            if cap is None or a.class_size is None:
                return 0
            slack = int(cap) - int(a.class_size)
            return slack if slack >= 0 else 10000

        chosen = min(available, key=room_score)
        result[int(a.id)] = chosen

        # Đánh dấu phòng đã dùng
        for p in periods:
            key = (day_value, p, chosen)
            room_busy.setdefault(key, set()).update(tw)

    return result


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
    """Xếp lịch theo tuần với availability + config rules.

    Phase 1 — CP-SAT: chỉ giải (day, period) cho mỗi assignment.
               Không đưa phòng vào biến → giảm số biến ~60x.
    Phase 2 — Greedy: gán phòng sau khi có slot, đảm bảo HC-2 (no room conflict).
    """

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

    rooms_missing_capacity: Set[int] = set()
    assignments_missing_size: Set[int] = set()

    time_blocks = _session_day_blocks(periods_per_day, morning_periods, afternoon_periods)
    morning_starts = _candidate_starts_within_blocks(
        config.default_lessons_cluster,
        [(morning_periods[0], morning_periods[-1])] if morning_periods else [],
    )

    # Build room name map và fixed_rules (dùng cho greedy phase)
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

    # Pre-validate: kiểm tra có phòng phù hợp không (chỉ report, không block biến)
    for a in assignments:
        label = assignment_labels.get(int(a.id), {})
        subject_name_lc = (label.get("subject_name") or "").strip().lower()
        candidate = rooms_by_type.get(int(a.classroom_type), room_indices)
        if config.match_room_type and not candidate:
            return GenerateResponse(
                status="INFEASIBLE", sessions=[],
                message=f"Assignment {a.id}: không có phòng phù hợp classroom_type={a.classroom_type}",
            )
        # fixed_rooms override check
        for keywords, room_idx_set in fixed_rules:
            if any(kw in subject_name_lc for kw in keywords):
                candidate = [r_i for r_i in room_indices if r_i in room_idx_set]
                if not candidate:
                    return GenerateResponse(
                        status="INFEASIBLE", sessions=[],
                        message=f"Assignment {a.id} ({label.get('subject_name','?')}): không có phòng cố định khả dụng",
                    )
                break
        if config.match_room_capacity and a.class_size is not None:
            sized = [
                r_i for r_i in candidate
                if classrooms[r_i].capacity is None
                or int(classrooms[r_i].capacity) >= int(a.class_size)
            ]
            if not sized:
                assignments_missing_size.add(int(a.id))
            else:
                candidate = sized
        elif config.match_room_capacity and a.class_size is None:
            assignments_missing_size.add(int(a.id))
        # track missing capacity rooms
        for r_i in candidate:
            if classrooms[r_i].capacity is None:
                rooms_missing_capacity.add(r_i)

    # ── Phase 1: CP-SAT giải (day, period) ──────────────────────────────────

    model = cp_model.CpModel()
    x: Dict[_SlotKey, cp_model.IntVar] = {}

    for a in assignments:
        label = assignment_labels.get(int(a.id), {})
        program_level = label.get("program_level", "")

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
                key = _SlotKey(a_id=a.id, day_idx=d_i, p_start=p_start)
                x[key] = model.new_bool_var(f"x__a{a.id}__d{d_i}__p{p_start}")

    # Build indexes
    vars_by_assignment: Dict[int, List[Tuple[_SlotKey, cp_model.IntVar]]] = {}
    vars_by_day_teacher: Dict[Tuple[int, str], List[Tuple[_SlotKey, cp_model.IntVar]]] = {}
    vars_by_day_group: Dict[Tuple[int, int], List[Tuple[_SlotKey, cp_model.IntVar]]] = {}

    for k, var in x.items():
        a = a_by_id.get(int(k.a_id))
        if not a:
            continue
        vars_by_assignment.setdefault(k.a_id, []).append((k, var))
        vars_by_day_teacher.setdefault((k.day_idx, str(a.teacher_id)), []).append((k, var))
        vars_by_day_group.setdefault((k.day_idx, int(a.class_group_id)), []).append((k, var))

    # Constraint: exactly sessions_per_week per assignment
    for a in assignments:
        a_vars = vars_by_assignment.get(a.id, [])
        model.add(sum(var for _, var in a_vars) == int(a.sessions_per_week))

    # ── Availability constraint ──────────────────────────────────────────────
    skipped_teachers: Set[str] = set()
    teacher_spw: Dict[str, int] = {}
    teacher_day_sessions: Dict[str, Set[Tuple[int, int]]] = {}

    if config.respect_teacher_availability:
        for a in assignments:
            tid = str(a.teacher_id)
            if tid not in availability:
                continue
            teacher_spw[tid] = teacher_spw.get(tid, 0) + int(a.sessions_per_week)
            if tid not in teacher_day_sessions:
                teacher_day_sessions[tid] = set()
            teacher_slots = availability[tid]
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

    # AvailabilityReport nhóm 1: GV chưa đăng ký
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

    # AvailabilityReport nhóm 2: GV thiếu slot
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

    # ── HC-3: Teacher conflict (week-aware) ─────────────────────────────────
    def _teaching_weeks_set(a: Assignment) -> Set[int]:
        excl = set(a.excluded_weeks) | holiday_weeks
        return {w for w in range(int(a.week_start), int(a.week_end) + 1) if w not in excl}

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
                        if _teaching_weeks_set(a_by_id[aid_i]) & _teaching_weeks_set(a_by_id[aid_j]):
                            model.add(var_i + var_j <= 1)

    # ── HC-4: Class group conflict (week-aware) ──────────────────────────────
    # Phát hiện trước các group overloaded (tổng spw > số slot khả dụng).
    # Group overloaded → bỏ qua HC-4, thêm warning thay vì INFEASIBLE.
    max_slots_per_group = len(days) * 2  # ngày × (sáng + chiều)
    overloaded_groups: Set[int] = set()
    if config.no_class_group_conflict:
        from collections import defaultdict as _dd
        group_week_load: Dict[int, Dict[int, int]] = {}
        for a in assignments:
            gid = int(a.class_group_id)
            excl = set(a.excluded_weeks) | holiday_weeks
            for w in range(int(a.week_start), int(a.week_end) + 1):
                if w not in excl:
                    group_week_load.setdefault(gid, {})
                    group_week_load[gid][w] = group_week_load[gid].get(w, 0) + int(a.sessions_per_week)
        for gid, week_loads in group_week_load.items():
            peak = max(week_loads.values(), default=0)
            if peak > max_slots_per_group:
                overloaded_groups.add(gid)
                # Lấy tên lớp từ label của assignment đầu tiên trong group
                sample_a = next((a for a in assignments if int(a.class_group_id) == gid), None)
                cls_name = assignment_labels.get(int(sample_a.id), {}).get("class_id", str(gid)) if sample_a else str(gid)
                solver_warnings.append(
                    f"Lớp {cls_name} có {peak} buổi/tuần > {max_slots_per_group} slot khả dụng"
                    f" — bỏ qua HC-4 (không xung đột lớp) cho lớp này"
                )

    if config.no_class_group_conflict:
        for (d_i, g_id), kv_list in vars_by_day_group.items():
            if g_id in overloaded_groups:
                continue
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
                        if _teaching_weeks_set(a_by_id[aid_i]) & _teaching_weeks_set(a_by_id[aid_j]):
                            model.add(var_i + var_j <= 1)

    # ── Objective ────────────────────────────────────────────────────────────
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

    if objective_terms:
        model.maximize(sum(objective_terms))

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

    # ── Phase 2: Greedy room assignment ──────────────────────────────────────
    slot_results: List[Tuple[Assignment, int, int]] = []
    for k, var in x.items():
        if solver.value(var) != 1:
            continue
        a = a_by_id.get(int(k.a_id))
        if not a:
            continue
        slot_results.append((a, int(days[k.day_idx]), int(k.p_start)))

    room_map = _assign_rooms(
        slot_results,
        classrooms,
        rooms_by_type,
        room_indices,
        fixed_rules,
        assignment_labels,
        holiday_weeks,
        config,
    )

    # ── Build ScheduledSession list ──────────────────────────────────────────
    sessions: List[ScheduledSession] = []
    for a, day_value, p_start in slot_results:
        room_idx = room_map.get(int(a.id), 0)
        room = classrooms[room_idx]
        period_end = p_start + int(a.lessons_cluster) - 1

        all_skipped: Dict[int, str] = {}
        for w in range(int(a.week_start), int(a.week_end) + 1):
            if w in holiday_weeks:
                all_skipped[w] = holiday_reasons.get(w, "Nghỉ")
            elif w in a.excluded_weeks:
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
                period_start=p_start,
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

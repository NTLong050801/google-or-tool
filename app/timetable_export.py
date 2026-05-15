"""Xuất kết quả TKB sang CSV với tên hiển thị."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .schemas import Assignment, Classroom, ScheduledSession

_DAY_VI = {
    2: "Thứ 2",
    3: "Thứ 3",
    4: "Thứ 4",
    5: "Thứ 5",
    6: "Thứ 6",
    7: "Thứ 7",
    8: "Chủ nhật",
}

_TYPE_CODE_REQ = {1: "LT", 2: "TH", 3: "X", 4: "K"}


def _slot_label(p_start: int, morning_periods: Optional[List[int]]) -> str:
    if not morning_periods:
        return ""
    last_am = max(int(p) for p in morning_periods)
    return "Sáng" if int(p_start) <= last_am else "Chiều"


def export_timetable_csv(
    sessions: List[ScheduledSession],
    assignments: List[Assignment],
    assignment_labels: Dict[int, Dict[str, str]],
    classrooms: List[Classroom],
    *,
    morning_periods: Optional[List[int]],
    csv_path: Path,
) -> None:
    cluster_by_aid = {int(a.id): int(a.lessons_cluster) for a in assignments}
    req_type_by_aid = {int(a.id): _TYPE_CODE_REQ.get(int(a.classroom_type), str(a.classroom_type)) for a in assignments}
    room_by_id = {int(c.id): c for c in classrooms}

    rows: List[Dict[str, object]] = []
    for s in sessions:
        lab = assignment_labels.get(int(s.assignment_id), {})
        cls_name = str(lab.get("class_name", ""))
        sub_code = str(lab.get("subject_code", ""))
        sub_name = str(lab.get("subject_name", ""))
        gv_name = str(lab.get("teacher_name", s.teacher_id))
        dept = str(lab.get("department_code", s.department_code))
        cluster = cluster_by_aid.get(int(s.assignment_id), int(s.period_end) - int(s.period_start) + 1)
        p_end = int(s.period_start) + cluster - 1
        period_txt = f"{int(s.period_start)}–{p_end}"
        room = room_by_id.get(int(s.classroom_id))
        room_label = (room.name if room else None) or str(s.classroom_id)
        room_prop = (room.type_code if room else None) or ""
        rows.append(
            {
                "_sort_day": int(s.day),
                "_sort_p": int(s.period_start),
                "_sort_cls": cls_name,
                "Khoa": dept,
                "Thứ": _DAY_VI.get(int(s.day), str(s.day)),
                "Buổi": _slot_label(int(s.period_start), morning_periods),
                "Tiết": period_txt,
                "Lớp": cls_name,
                "Mã môn": sub_code,
                "Tên môn": sub_name,
                "Giảng viên": gv_name,
                "Mã GV": str(s.teacher_id),
                "Yêu cầu phòng (projects/rooms)": req_type_by_aid.get(int(s.assignment_id), ""),
                "Phòng": room_label,
                "Mã tính chất phòng (rooms.csv)": room_prop,
                "Tuần từ": int(s.week_start),
                "Tuần đến": int(s.week_end),
            }
        )

    rows.sort(key=lambda r: (int(r["_sort_day"]), int(r["_sort_p"]), str(r["_sort_cls"])))
    for r in rows:
        del r["_sort_day"]
        del r["_sort_p"]
        del r["_sort_cls"]

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")

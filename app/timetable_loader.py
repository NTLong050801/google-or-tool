"""Loader cho dữ liệu shared/term: rooms, weeks, holidays, class sizes."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .schemas import Classroom


def load_classrooms_from_csv(
    path: Path,
    department_name_to_code: Optional[Dict[str, str]] = None,
) -> List[Classroom]:
    """Đọc rooms.csv. Map home_department (tên khoa) → mã khoa qua department_name_to_code."""
    rooms: List[Classroom] = []
    typ_map = {"LT": 1, "TH": 2, "X": 3, "K": 4}
    name_map = department_name_to_code or {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if int(row.get("in_use", 0) or 0) != 1:
                continue
            label = (row.get("room_label") or "").strip()
            note = (row.get("note") or "").strip()
            blob = f"{label} {note}".lower()
            if "ảo" in blob or " ao)" in blob:
                continue
            raw_id = (row.get("room_name") or "").strip()
            if raw_id.isdigit():
                rid = int(raw_id)
            else:
                rid = abs(hash(label)) % (10**9)
            code = (row.get("room_property_code") or "LT").strip().upper()
            rtype = typ_map.get(code, 1)
            cap_raw = (row.get("capacity") or "").strip()
            capacity = int(cap_raw) if cap_raw.isdigit() else None

            home_name = (row.get("home_department") or "").strip()
            home_code = name_map.get(home_name) or (home_name if home_name else None)

            priority_raw = (row.get("priority_room") or "").strip()
            priority_codes: List[str] = []
            if priority_raw:
                priority_codes = [p.strip().upper() for p in priority_raw.split(",") if p.strip()]

            rooms.append(
                Classroom(
                    id=rid,
                    name=label or raw_id,
                    capacity=capacity,
                    type=rtype,
                    type_code=code,
                    campus_code=(row.get("campus_code") or "").strip() or None,
                    home_department=home_code,
                    priority_departments=priority_codes,
                )
            )
    return rooms


def load_class_sizes(path: Path) -> Dict[str, int]:
    """Tên lớp (class_id) -> sĩ số (class_size) từ classes.csv."""
    out: Dict[str, int] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("name") or "").strip()
            size = (row.get("class_size") or "").strip()
            if name and size.isdigit():
                out[name] = int(size)
    return out


def week_bounds_from_csv(path: Path) -> Tuple[int, int]:
    orders: List[int] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            o = (row.get("week_order") or "").strip()
            if o.isdigit():
                orders.append(int(o))
    if not orders:
        raise ValueError(f"No week_order in {path}")
    return min(orders), max(orders)


def load_holidays(path: Path) -> set[int]:
    """terms/<term>/holidays.csv → set[week_order] tuần nghỉ. Schema: week_order, reason."""
    if not path.is_file():
        return set()
    out: set[int] = set()
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            wo = (row.get("week_order") or "").strip()
            if wo.isdigit():
                out.add(int(wo))
    return out


def load_holiday_reasons(path: Path) -> Dict[int, str]:
    """week_order → reason (cho hiển thị tuần nghỉ trong export)."""
    if not path.is_file():
        return {}
    out: Dict[int, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            wo = (row.get("week_order") or "").strip()
            reason = (row.get("reason") or "").strip()
            if wo.isdigit():
                out[int(wo)] = reason or "Nghỉ"
    return out


def load_week_dates(path: Path) -> Dict[int, Tuple[str, str]]:
    """week_order → (from_date, to_date) dạng YYYY-MM-DD."""
    out: Dict[int, Tuple[str, str]] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            wo = (row.get("week_order") or "").strip()
            fd = (row.get("from_date") or "").strip()
            td = (row.get("to_date") or "").strip()
            if wo.isdigit():
                out[int(wo)] = (fd, td)
    return out


def load_teacher_subjects(path: Path) -> Dict[Tuple[str, str], int]:
    """Đọc teacher_subjects.xlsx hoặc .csv → dict[(teacher_id, subject_code) → priority(1/2/3)].

    Cột bắt buộc: 'Mã CB', 'Mã môn'. Cột 'Ưu tiên' optional, mặc định 2.
    Tên cột case-insensitive (chấp nhận 'Mã Môn', 'mã môn', 'MA MON'...).
    """
    if not path.is_file():
        return {}

    import pandas as pd

    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, encoding="utf-8-sig")
    else:
        df = pd.read_excel(path, engine="openpyxl")

    # Map tên cột → tên chuẩn (case + accent insensitive cơ bản)
    def _norm(s: str) -> str:
        return str(s).strip().lower().replace(" ", "")

    col_map: Dict[str, str] = {}
    for c in df.columns:
        n = _norm(c)
        if n in ("mãcb", "macb"):
            col_map["teacher_id"] = c
        elif n in ("mãmôn", "mamon"):
            col_map["subject_code"] = c
        elif n in ("ưutiên", "uutien"):
            col_map["priority"] = c
    if "teacher_id" not in col_map or "subject_code" not in col_map:
        return {}

    out: Dict[Tuple[str, str], int] = {}
    for _, row in df.iterrows():
        tid = str(row.get(col_map["teacher_id"], "")).strip()
        sub = str(row.get(col_map["subject_code"], "")).strip()
        if not tid or not sub or tid.lower() == "nan" or sub.lower() == "nan":
            continue
        prio = 2
        if "priority" in col_map:
            prio_raw = row.get(col_map["priority"])
            try:
                if prio_raw is not None and str(prio_raw).strip() not in ("", "nan"):
                    prio = int(float(prio_raw))
            except (TypeError, ValueError):
                prio = 2
        if prio not in (1, 2, 3):
            prio = 2
        out[(tid, sub)] = prio
    return out

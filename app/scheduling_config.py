"""Đọc scheduling rules config."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


def _default_config_path() -> Path:
    return Path(__file__).resolve().parent / "config" / "scheduling_rules.json"


@dataclass
class SoftConstraint:
    enabled: bool = True
    weight: int = 1
    description: str = ""


@dataclass
class SchedulingConfig:
    days: List[int] = field(default_factory=lambda: [2, 3, 4, 5, 6, 7])
    periods_per_day: int = 10
    morning_periods: List[int] = field(default_factory=lambda: [1, 2, 3, 4, 5])
    afternoon_periods: List[int] = field(default_factory=lambda: [6, 7, 8, 9, 10])
    default_lessons_cluster: int = 5
    max_time_seconds: float = 120
    num_workers: int = 4

    no_room_conflict: bool = True
    no_teacher_conflict: bool = True
    no_class_group_conflict: bool = True
    respect_teacher_availability: bool = True
    # Tỉ lệ tuần thực dạy tối thiểu GV phải có slot khả dụng cho (ngày, buổi) đó.
    # 1.0 = phải rảnh TẤT CẢ tuần dạy (đúng nhất với weekly-template).
    # 0.0 = chỉ cần 1 tuần bất kỳ (hành vi cũ, sai logic).
    availability_week_threshold: float = 1.0
    trung_cap_morning_only: bool = True
    match_room_type: bool = True
    match_room_capacity: bool = True

    max_spw_trung_cap: int = 6   # HC-005: 25-30 tiết/tuần ÷ 5 tiết/buổi
    max_spw_cao_dang: int = 12   # HC-005: 60 tiết/tuần ÷ 5 tiết/buổi

    # Mỗi entry: {"keywords": [...], "room_names": [...], "description": "..."}
    # Môn match keyword chỉ được xếp vào các phòng có room_name trong room_names.
    fixed_rooms_by_subject_keyword: List[Dict] = field(default_factory=list)

    prioritize_thinh_giang: SoftConstraint = field(default_factory=lambda: SoftConstraint(weight=10))
    prioritize_trung_cap: SoftConstraint = field(default_factory=lambda: SoftConstraint(weight=5))
    prefer_early_periods: SoftConstraint = field(default_factory=lambda: SoftConstraint(weight=1))
    prefer_room_fit: SoftConstraint = field(default_factory=lambda: SoftConstraint(weight=1))

    schedule_order: List[str] = field(default_factory=lambda: ["thinh_giang", "trung_cap", "cao_dang"])


def load_config(path: Optional[Path] = None) -> SchedulingConfig:
    p = path or _default_config_path()
    if not p.is_file():
        return SchedulingConfig()

    raw = json.loads(p.read_text(encoding="utf-8"))
    gen = raw.get("general", {})
    hard = raw.get("hard_constraints", {})
    soft = raw.get("soft_constraints", {})
    order = raw.get("schedule_order", {})

    cfg = SchedulingConfig(
        days=gen.get("days", [2, 3, 4, 5, 6, 7]),
        periods_per_day=gen.get("periods_per_day", 10),
        morning_periods=gen.get("morning_periods", [1, 2, 3, 4, 5]),
        afternoon_periods=gen.get("afternoon_periods", [6, 7, 8, 9, 10]),
        default_lessons_cluster=gen.get("default_lessons_cluster", 5),
        max_time_seconds=gen.get("max_time_seconds", 120),
        num_workers=gen.get("num_workers", 4),
        no_room_conflict=hard.get("no_room_conflict", True),
        no_teacher_conflict=hard.get("no_teacher_conflict", True),
        no_class_group_conflict=hard.get("no_class_group_conflict", True),
        respect_teacher_availability=hard.get("respect_teacher_availability", True),
        availability_week_threshold=float(hard.get("availability_week_threshold", 1.0)),
        trung_cap_morning_only=hard.get("trung_cap_morning_only", True),
        match_room_type=hard.get("match_room_type", True),
        match_room_capacity=hard.get("match_room_capacity", True),
        max_spw_trung_cap=hard.get("max_spw_trung_cap", 6),
        max_spw_cao_dang=hard.get("max_spw_cao_dang", 12),
        fixed_rooms_by_subject_keyword=hard.get("fixed_rooms_by_subject_keyword", []) or [],
    )

    for key in ("prioritize_thinh_giang", "prioritize_trung_cap", "prefer_early_periods", "prefer_room_fit"):
        s = soft.get(key, {})
        if isinstance(s, dict):
            setattr(cfg, key, SoftConstraint(
                enabled=s.get("enabled", True),
                weight=s.get("weight", 1),
                description=s.get("description", ""),
            ))

    if "order" in order:
        cfg.schedule_order = [item["key"] if isinstance(item, dict) else item for item in order["order"]]

    return cfg

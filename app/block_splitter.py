"""Chia dải tuần học kỳ thành các block dựa trên điểm thay đổi."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Set, Tuple

from .schemas import Assignment
from .timetable_loader import TeacherBusyEntry


@dataclass(frozen=True)
class WeekBlock:
    """Một block tuần liên tiếp."""
    block_id: int
    week_start: int
    week_end: int


def compute_week_blocks(
    week_lo: int,
    week_hi: int,
    assignments: List[Assignment],
    teacher_busy: List[TeacherBusyEntry],
) -> List[WeekBlock]:
    """Tính các block tuần dựa trên điểm cắt từ assignments và teacher_busy.

    Điểm cắt = các tuần mà tập assignment hoặc tập GV bận thay đổi.
    """
    cut_points: Set[int] = {week_lo}

    for a in assignments:
        ws = max(int(a.week_start), week_lo)
        we = min(int(a.week_end), week_hi)
        if ws > week_lo:
            cut_points.add(ws)
        if we < week_hi:
            cut_points.add(we + 1)

    for entry in teacher_busy:
        ws = max(int(entry.week_start), week_lo)
        we = min(int(entry.week_end), week_hi)
        if ws > week_lo:
            cut_points.add(ws)
        if we < week_hi:
            cut_points.add(we + 1)

    sorted_cuts = sorted(cut_points)

    blocks: List[WeekBlock] = []
    for i, start in enumerate(sorted_cuts):
        if i + 1 < len(sorted_cuts):
            end = sorted_cuts[i + 1] - 1
        else:
            end = week_hi
        if start <= end <= week_hi:
            blocks.append(WeekBlock(block_id=i, week_start=start, week_end=end))

    return blocks


def assignments_in_block(
    block: WeekBlock,
    assignments: List[Assignment],
) -> List[Assignment]:
    """Lọc assignments active trong block (dải tuần giao nhau)."""
    result = []
    for a in assignments:
        if int(a.week_start) <= block.week_end and int(a.week_end) >= block.week_start:
            result.append(a)
    return result


def teacher_busy_in_block(
    block: WeekBlock,
    teacher_busy: List[TeacherBusyEntry],
) -> List[TeacherBusyEntry]:
    """Lọc entries GV bận trong block."""
    result = []
    for entry in teacher_busy:
        if int(entry.week_start) <= block.week_end and int(entry.week_end) >= block.week_start:
            result.append(entry)
    return result

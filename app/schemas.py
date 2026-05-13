from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


DayOfWeek = int  # 2..7 (Mon..Sat like Laravel config)


class ORToolsConfig(BaseModel):
    days: list[DayOfWeek] = Field(..., description="Danh sách thứ trong tuần (2..7)")
    periods_per_day: int = Field(..., ge=1, le=24)
    morning_periods: Optional[list[int]] = None
    afternoon_periods: Optional[list[int]] = None


class Classroom(BaseModel):
    id: int
    name: Optional[str] = None
    capacity: Optional[int] = None
    type: Optional[int] = Field(
        None,
        description="Khớp rooms.room_property_code: 1=LT, 2=TH, 3=X, 4=K",
    )
    type_code: Optional[str] = None
    campus_code: Optional[str] = None


class Assignment(BaseModel):
    id: int
    teacher_id: str = Field(..., description="Mã cán bộ / định danh GV (vd CT112)")
    course_id: int = Field(..., description="Id offering (hiển thị / truy vết)")
    class_group_id: int = Field(
        ...,
        description="Cùng một lớp SV: không được học 2 môn trùng tiết (thường = edu_course_id)",
    )
    classroom_type: int = Field(
        ...,
        description="CNTT: 1=lý thuyết (LT), 2=thực hành (TH trong rooms.csv); catalog không-LT → 2",
    )

    sessions_per_week: int = Field(..., ge=1, le=20, description="Số buổi cần xếp/tuần")
    lessons_cluster: int = Field(..., ge=1, le=10, description="Số tiết liên tiếp mỗi buổi")

    week_start: int = Field(..., ge=1, description="Tuần bắt đầu (order_id)")
    week_end: int = Field(..., ge=1, description="Tuần kết thúc (order_id)")


class GenerateRequest(BaseModel):
    or_tools: ORToolsConfig
    assignments: list[Assignment]
    classrooms: list[Classroom]


class ScheduledSession(BaseModel):
    assignment_id: int
    teacher_id: str
    course_id: int
    classroom_id: int
    day: DayOfWeek
    period_start: int
    period_end: int
    week_start: int
    week_end: int


class GenerateResponse(BaseModel):
    status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"]
    objective: Optional[int] = None
    sessions: list[ScheduledSession] = Field(default_factory=list)
    message: Optional[str] = None

from __future__ import annotations

from typing import Dict, List, Literal, Optional

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
    home_department: Optional[str] = Field(None, description="Mã khoa sở hữu phòng (nếu có)")
    priority_departments: list[str] = Field(
        default_factory=list,
        description="Danh sách mã khoa được ưu tiên dùng phòng. Trống = bất kỳ khoa nào",
    )


class Assignment(BaseModel):
    id: int
    teacher_id: str = Field(..., description="Mã cán bộ / định danh GV (vd CT112)")
    course_id: str = Field(..., description="Mã môn học gốc (từ Excel, dùng truy vết)")
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

    department_code: str = Field("", description="Mã khoa (vd cntt, kt)")
    term_code: str = Field("", description="Mã học kỳ (vd 2025_2026_HK2)")

    class_size: Optional[int] = Field(
        None,
        description="Sĩ số lớp (từ classes.csv). Dùng để lọc phòng đủ capacity và soft prefer phòng vừa.",
    )
    excluded_weeks: set[int] = Field(
        default_factory=set,
        description="Tuần không dạy riêng của lớp này (thi/dự phòng/...). "
                    "Solver bỏ qua khi check availability GV. Không gồm holiday_weeks toàn trường.",
    )
    excluded_week_reasons: dict[int, str] = Field(
        default_factory=dict,
        description="Lý do từng tuần trong excluded_weeks: {week_order: reason}. "
                    "Vd: {15: 'thi', 16: 'du_phong'}.",
    )


class GenerateRequest(BaseModel):
    or_tools: ORToolsConfig
    assignments: list[Assignment]
    classrooms: list[Classroom]


class ScheduledSession(BaseModel):
    assignment_id: int
    teacher_id: str
    course_id: str
    classroom_id: int
    day: DayOfWeek
    period_start: int
    period_end: int
    week_start: int
    week_end: int
    department_code: str = ""
    teaching_weeks: list[int] = Field(
        default_factory=list,
        description="Danh sách tuần thực dạy (đã loại nghỉ lễ + thi/dự phòng riêng lớp).",
    )
    skipped_weeks: dict[int, str] = Field(
        default_factory=dict,
        description="Tuần không dạy trong khoảng [week_start, week_end]: {week_order: reason}.",
    )


class GenerateResponse(BaseModel):
    status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"]
    objective: Optional[int] = None
    sessions: list[ScheduledSession] = Field(default_factory=list)
    message: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    availability_report: Optional["AvailabilityReport"] = None


class AvailabilityIssue(BaseModel):
    """Một vấn đề availability của 1 GV trong 1 khoa."""
    department_code: str
    teacher_id: str
    teacher_name: str
    teacher_type: str
    status: Literal["chua_dang_ky", "thieu_slot"]
    weeks_registered: int        # số tuần GV có slot trong DB
    weeks_needed: int            # tổng sessions_per_week cần xếp
    slots_available: int         # số (day, session) pass được threshold
    affected_classes: List[str]  # danh sách "môn (lớp X, N buổi/tuần)"


class AvailabilityReport(BaseModel):
    """Tổng hợp vấn đề availability sau một lần chạy solver."""
    issues: List[AvailabilityIssue] = Field(default_factory=list)

    def has_issues(self) -> bool:
        return len(self.issues) > 0

    def summary_warnings(self) -> List[str]:
        """Chuỗi tóm tắt ngắn gọn để đưa vào warnings.txt."""
        if not self.issues:
            return []
        not_registered = [i for i in self.issues if i.status == "chua_dang_ky"]
        low_slots = [i for i in self.issues if i.status == "thieu_slot"]
        lines: List[str] = []
        if not_registered:
            lines.append(
                f"[AVAILABILITY] {len(not_registered)} GV chưa đăng ký availability "
                f"→ xem availability_report.csv"
            )
        if low_slots:
            lines.append(
                f"[AVAILABILITY] {len(low_slots)} GV đăng ký không đủ slot "
                f"→ xem availability_report.csv"
            )
        return lines

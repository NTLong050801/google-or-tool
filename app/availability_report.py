"""Export availability report ra CSV — 1 file, mỗi khoa 1 section."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import List

from .schemas import AvailabilityReport

_STATUS_LABEL = {
    "chua_dang_ky": "Chưa đăng ký",
    "thieu_slot":   "Thiếu slot",
}


def export_availability_report(report: AvailabilityReport, csv_path: Path) -> int:
    """Ghi availability_report.csv, trả về số dòng issue đã ghi.

    Cấu trúc file:
        department_code, teacher_id, teacher_name, teacher_type,
        status, weeks_registered, weeks_needed, slots_available,
        affected_classes

    Sắp xếp: theo khoa → status (chưa đăng ký trước) → teacher_id.
    """
    if not report.has_issues():
        return 0

    issues = sorted(
        report.issues,
        key=lambda i: (i.department_code, i.status, i.teacher_id),
    )

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Khoa",
            "Mã GV",
            "Tên GV",
            "Loại GV",
            "Trạng thái",
            "Số tuần đã đăng ký",
            "Số buổi/tuần cần xếp",
            "Số slot khả dụng",
            "Môn / Lớp bị ảnh hưởng",
        ])

        current_dept = None
        for issue in issues:
            if issue.department_code != current_dept:
                current_dept = issue.department_code
                # Dòng ngăn cách giữa các khoa
                writer.writerow([f"=== Khoa: {current_dept} ==="] + [""] * 8)

            writer.writerow([
                issue.department_code,
                issue.teacher_id,
                issue.teacher_name,
                issue.teacher_type,
                _STATUS_LABEL.get(issue.status, issue.status),
                issue.weeks_registered,
                issue.weeks_needed,
                issue.slots_available,
                " | ".join(issue.affected_classes),
            ])

    return len(issues)

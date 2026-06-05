"""Resolve data paths theo cấu trúc multi-khoa / multi-term."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List


def _default_data_root() -> Path:
    return Path(__file__).resolve().parent / "data"


@dataclass(frozen=True)
class TermPaths:
    """Đường dẫn cho một học kỳ cụ thể."""

    data_root: Path
    term_code: str

    @property
    def shared_dir(self) -> Path:
        return self.data_root / "shared"

    @property
    def rooms_csv(self) -> Path:
        return self.shared_dir / "rooms.csv"

    @property
    def term_dir(self) -> Path:
        return self.data_root / "terms" / self.term_code

    @property
    def weeks_csv(self) -> Path:
        return self.term_dir / "weeks.csv"

    @property
    def holidays_csv(self) -> Path:
        return self.term_dir / "holidays.csv"

    @property
    def departments_dir(self) -> Path:
        return self.term_dir / "departments"

    def department(self, dept_code: str) -> "DepartmentPaths":
        return DepartmentPaths(term=self, dept_code=dept_code)

    def list_departments(self) -> List[str]:
        d = self.departments_dir
        if not d.is_dir():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_dir())

    @property
    def class_excluded_weeks_csv(self) -> Path:
        return self.term_dir / "class_excluded_weeks.csv"

    @property
    def class_week_starts_csv(self) -> Path:
        return self.term_dir / "class_week_starts.csv"

    @property
    def output_dir(self) -> Path:
        return self.data_root / "output" / self.term_code


@dataclass(frozen=True)
class DepartmentPaths:
    """Đường dẫn cho một khoa trong một học kỳ. Pipeline mới chỉ đọc cleans/."""

    term: TermPaths
    dept_code: str

    @property
    def dept_dir(self) -> Path:
        return self.term.departments_dir / self.dept_code

    @property
    def cleans_dir(self) -> Path:
        return self.dept_dir / "cleans"

    @property
    def classes_csv(self) -> Path:
        return self.cleans_dir / "classes.csv"

    @property
    def teacher_subjects_xlsx(self) -> Path:
        return self.cleans_dir / "teacher_subjects.xlsx"

    @property
    def teacher_subjects_csv(self) -> Path:
        return self.cleans_dir / "teacher_subjects.csv"

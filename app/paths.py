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
    def projects_xls(self) -> Path:
        """Deprecated: dùng DepartmentPaths.projects_xls thay thế."""
        return self.shared_dir / "projects.xls"

    @property
    def term_dir(self) -> Path:
        return self.data_root / "terms" / self.term_code

    @property
    def weeks_csv(self) -> Path:
        return self.term_dir / "weeks.csv"

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
    def output_dir(self) -> Path:
        return self.data_root / "output" / self.term_code


@dataclass(frozen=True)
class DepartmentPaths:
    """Đường dẫn cho một khoa trong một học kỳ."""

    term: TermPaths
    dept_code: str

    @property
    def dept_dir(self) -> Path:
        return self.term.departments_dir / self.dept_code

    @property
    def raw_dir(self) -> Path:
        return self.dept_dir / "raw"

    @property
    def cleans_dir(self) -> Path:
        return self.dept_dir / "cleans"

    @property
    def classes_project_xls(self) -> Path:
        return self.raw_dir / "classes_project.xls"

    @property
    def classes_csv(self) -> Path:
        return self.cleans_dir / "classes.csv"

    @property
    def teachers_csv(self) -> Path:
        return self.cleans_dir / "teachers.csv"

    @property
    def teacher_aliases_csv(self) -> Path:
        return self.cleans_dir / "teacher_aliases.csv"

    @property
    def teacher_busy_csv(self) -> Path:
        return self.cleans_dir / "teacher_busy.csv"

    @property
    def projects_xls(self) -> Path:
        return self.raw_dir / "projects.xls"

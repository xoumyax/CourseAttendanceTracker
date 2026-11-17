"""
Roster CSV import.

Formats:
  simple  columns `student_id,name`
  canvas  a Canvas gradebook export: columns `Student` and `SIS User ID`; the
          "Points Possible" row and Canvas's "Student, Test" row are skipped.

Re-importing is safe: names are updated, new students are added, nobody is duplicated.
"""
import csv
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from sqlalchemy.orm import Session as DbSession

from . import models

FORMATS = ("canvas", "simple")
_COLUMNS = {"canvas": ("SIS User ID", "Student"), "simple": ("student_id", "name")}
_CANVAS_SKIP_NAMES = {"points possible", "student, test"}


@dataclass
class ImportResult:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    problems: List[str] = field(default_factory=list)


def read_rows(path: str, fmt: str) -> Tuple[List[Tuple[str, str]], int]:
    """Return ([(student_id, name), ...], skipped_count) from the file."""
    if fmt not in FORMATS:
        raise ValueError(f"Unknown roster format '{fmt}' (use one of: {', '.join(FORMATS)})")
    id_col, name_col = _COLUMNS[fmt]

    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = [h.strip() for h in (reader.fieldnames or [])]
        if id_col not in header or name_col not in header:
            raise ValueError(
                f"The {fmt} format needs the columns '{id_col}' and '{name_col}'; "
                f"this file has: {', '.join(header) or '(no header row)'}"
            )
        rows: List[Tuple[str, str]] = []
        skipped = 0
        for raw in reader:
            row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
            student_id, name = row.get(id_col, ""), row.get(name_col, "")
            if fmt == "canvas" and name.lower() in _CANVAS_SKIP_NAMES:
                continue
            if not student_id or not name:
                skipped += 1
                continue
            rows.append((student_id, name))
    return rows, skipped


def import_roster(db: DbSession, path: str, fmt: str = "simple") -> ImportResult:
    rows, skipped = read_rows(path, fmt)
    result = ImportResult(skipped=skipped)

    existing: Dict[str, models.Student] = {s.uin: s for s in db.query(models.Student).all()}
    for student_id, name in rows:
        student = existing.get(student_id)
        if student is None:
            student = models.Student(uin=student_id, name=name, hashed_password="", is_registered=False)
            db.add(student)
            existing[student_id] = student
            result.added += 1
        elif student.name != name:
            student.name = name
            result.updated += 1
        else:
            result.unchanged += 1
    db.commit()
    return result

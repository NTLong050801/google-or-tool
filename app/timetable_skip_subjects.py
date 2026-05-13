"""Môn không đưa vào xếp TKB (không bắt buộc GV, không xếp phòng)."""


def is_excluded_subject(subject_name: str | None) -> bool:
    """
    True nếu dòng lớp–môn nên bỏ qua hoàn toàn khi build Assignment / gọi solver.

    Quy ước hiện tại:
    - Thực tập tốt nghiệp
    - Giáo dục quốc phòng-an ninh (nhận diện qua cụm \"giáo dục quốc phòng\")
    """
    if subject_name is None:
        return False
    n = str(subject_name).strip().lower()
    if not n:
        return False
    if "thực tập tốt nghiệp" in n:
        return True
    if "giáo dục quốc phòng" in n:
        return True
    return False

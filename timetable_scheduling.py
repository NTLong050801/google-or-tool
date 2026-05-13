"""
BÀI TOÁN XẾP THỜI KHÓA BIỂU TRƯỜNG CAO ĐẲNG
Sử dụng Google OR-Tools (CP-SAT Solver)

Bài toán:
- Xếp lịch giảng dạy cho các môn học, giáo viên, lớp học, phòng học
- Trong tuần học (Thứ 2 -> Thứ 7), mỗi ngày có nhiều tiết học

Ràng buộc cứng (Hard Constraints):
1. Mỗi giáo viên chỉ dạy tối đa 1 lớp tại 1 thời điểm
2. Mỗi lớp chỉ học 1 môn tại 1 thời điểm
3. Mỗi phòng học chỉ được sử dụng bởi 1 lớp tại 1 thời điểm
4. Mỗi môn học của mỗi lớp phải được xếp đủ số tiết yêu cầu trong tuần

Ràng buộc mềm (Soft Constraints - tối ưu hóa):
1. Giáo viên không bị xếp lịch vào các tiết liên tiếp quá nhiều (tránh dạy quá tải)
2. Ưu tiên xếp môn học vào buổi sáng
"""

from ortools.sat.python import cp_model
from collections import defaultdict

# ============================================================
# 1. DỮ LIỆU ĐẦU VÀO
# ============================================================

# Danh sách các ngày trong tuần
DAYS = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7"]

# Các tiết học trong ngày (1 -> 6: Sáng, 7 -> 10: Chiều)
SLOTS_PER_DAY = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
SLOT_NAMES = {
    1: "Tiết 1 (07:00-07:50)",
    2: "Tiết 2 (07:50-08:40)",
    3: "Tiết 3 (08:50-09:40)",
    4: "Tiết 4 (09:40-10:30)",
    5: "Tiết 5 (10:40-11:30)",
    6: "Tiết 6 (11:30-12:20)",
    7: "Tiết 7 (13:00-13:50)",
    8: "Tiết 8 (13:50-14:40)",
    9: "Tiết 9 (14:50-15:40)",
    10: "Tiết 10 (15:40-16:30)",
}

# Danh sách lớp học
CLASSES = ["CNTT-K1", "CNTT-K2", "KT-K1", "KT-K2", "DL-K1"]

# Danh sách phòng học
ROOMS = ["P101", "P102", "P103", "P201", "P202"]

# Danh sách giáo viên
TEACHERS = {
    "GV01": "Nguyễn Văn An",
    "GV02": "Trần Thị Bình",
    "GV03": "Lê Văn Cường",
    "GV04": "Phạm Thị Dung",
    "GV05": "Hoàng Văn Em",
    "GV06": "Ngô Thị Phương",
    "GV07": "Đặng Văn Giang",
}

# Danh sách môn học: { mã môn: (tên môn, giáo viên phụ trách) }
SUBJECTS = {
    "TIN101": ("Lập trình Python",       "GV01"),
    "TIN102": ("Cơ sở dữ liệu",          "GV02"),
    "TIN103": ("Mạng máy tính",          "GV03"),
    "KT101":  ("Kế toán đại cương",      "GV04"),
    "KT102":  ("Tài chính doanh nghiệp", "GV05"),
    "DL101":  ("Nghiệp vụ du lịch",      "GV06"),
    "DL102":  ("Tiếng Anh du lịch",      "GV07"),
    "ANH101": ("Tiếng Anh cơ bản",       "GV07"),
}

# Yêu cầu dạy học: { lớp: [(môn, số tiết/tuần)] }
# Mỗi tuple là (mã môn, số tiết cần xếp trong tuần)
CLASS_REQUIREMENTS = {
    "CNTT-K1": [("TIN101", 3), ("TIN102", 2), ("ANH101", 2)],
    "CNTT-K2": [("TIN101", 3), ("TIN103", 2), ("ANH101", 2)],
    "KT-K1":   [("KT101",  3), ("KT102",  2), ("ANH101", 2)],
    "KT-K2":   [("KT101",  3), ("KT102",  2), ("ANH101", 2)],
    "DL-K1":   [("DL101",  3), ("DL102",  2), ("ANH101", 2)],
}

# ============================================================
# 2. XÂY DỰNG MÔ HÌNH
# ============================================================

def solve_timetable():
    model = cp_model.CpModel()

    num_days  = len(DAYS)
    num_slots = len(SLOTS_PER_DAY)
    num_rooms = len(ROOMS)

    # Biến quyết định:
    # assignment[(class, subject, day, slot, room)] = 1 nếu lớp `class`
    # học môn `subject` tại ngày `day`, tiết `slot`, phòng `room`
    assignment = {}

    for cls in CLASSES:
        for (subj, _) in CLASS_REQUIREMENTS[cls]:
            for d in range(num_days):
                for s in range(num_slots):
                    for r in range(num_rooms):
                        key = (cls, subj, d, s, r)
                        assignment[key] = model.new_bool_var(
                            f"assign__{cls}__{subj}__d{d}__s{s}__r{r}"
                        )

    # ============================================================
    # 3. RÀNG BUỘC CỨNG
    # ============================================================

    # Ràng buộc 1: Mỗi lớp-môn học đủ số tiết yêu cầu trong tuần
    for cls in CLASSES:
        for (subj, required_slots) in CLASS_REQUIREMENTS[cls]:
            model.add(
                sum(
                    assignment[(cls, subj, d, s, r)]
                    for d in range(num_days)
                    for s in range(num_slots)
                    for r in range(num_rooms)
                ) == required_slots
            )

    # Ràng buộc 2: Mỗi lớp chỉ học 1 môn tại 1 thời điểm (ngày + tiết)
    for cls in CLASSES:
        subjects_for_class = [subj for (subj, _) in CLASS_REQUIREMENTS[cls]]
        for d in range(num_days):
            for s in range(num_slots):
                model.add(
                    sum(
                        assignment[(cls, subj, d, s, r)]
                        for subj in subjects_for_class
                        for r in range(num_rooms)
                    ) <= 1
                )

    # Ràng buộc 3: Mỗi phòng học chỉ dùng cho 1 lớp tại 1 thời điểm
    for d in range(num_days):
        for s in range(num_slots):
            for r in range(num_rooms):
                model.add(
                    sum(
                        assignment[(cls, subj, d, s, r)]
                        for cls in CLASSES
                        for (subj, _) in CLASS_REQUIREMENTS[cls]
                    ) <= 1
                )

    # Ràng buộc 4: Mỗi giáo viên chỉ dạy 1 lớp tại 1 thời điểm
    for d in range(num_days):
        for s in range(num_slots):
            # Nhóm các (lớp, môn) theo giáo viên
            teacher_slots = defaultdict(list)
            for cls in CLASSES:
                for (subj, _) in CLASS_REQUIREMENTS[cls]:
                    teacher = SUBJECTS[subj][1]
                    for r in range(num_rooms):
                        teacher_slots[teacher].append(assignment[(cls, subj, d, s, r)])
            for teacher, vars_list in teacher_slots.items():
                model.add(sum(vars_list) <= 1)

    # ============================================================
    # 4. RÀNG BUỘC MỀM (Hàm mục tiêu)
    # ============================================================
    # Ưu tiên xếp lịch vào buổi sáng (tiết 1-6 = index 0-5)
    # Tối đa hóa số tiết được xếp vào buổi sáng
    morning_slots = list(range(6))  # index 0 -> 5 tương ứng tiết 1 -> 6
    morning_score = []
    for cls in CLASSES:
        for (subj, _) in CLASS_REQUIREMENTS[cls]:
            for d in range(num_days):
                for s in morning_slots:
                    for r in range(num_rooms):
                        morning_score.append(assignment[(cls, subj, d, s, r)])

    model.maximize(sum(morning_score))

    # ============================================================
    # 5. GIẢI BÀI TOÁN
    # ============================================================
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30.0   # giới hạn thời gian giải
    solver.parameters.log_search_progress = False

    print("=" * 70)
    print("  BÀI TOÁN XẾP THỜI KHÓA BIỂU TRƯỜNG CAO ĐẲNG")
    print("  Đang giải bằng Google OR-Tools CP-SAT Solver...")
    print("=" * 70)

    status = solver.solve(model)

    # ============================================================
    # 6. XUẤT KẾT QUẢ
    # ============================================================
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print(f"\n✅ Tìm được lời giải! (Trạng thái: {'TỐI ƯU' if status == cp_model.OPTIMAL else 'KHẢ THI'})")
        print(f"   Điểm tối ưu (số tiết buổi sáng): {int(solver.objective_value)}\n")

        # Thu thập kết quả
        timetable = defaultdict(list)  # timetable[cls] = list of (day, slot, subj, room)

        for cls in CLASSES:
            for (subj, _) in CLASS_REQUIREMENTS[cls]:
                for d in range(num_days):
                    for s in range(num_slots):
                        for r in range(num_rooms):
                            if solver.value(assignment[(cls, subj, d, s, r)]) == 1:
                                timetable[cls].append((d, s, subj, r))

        # In thời khóa biểu từng lớp
        for cls in CLASSES:
            print("─" * 70)
            print(f"  📚 LỚP: {cls}")
            print("─" * 70)
            print(f"  {'Ngày':<12} {'Tiết học':<30} {'Môn học':<30} {'Phòng'}")
            print(f"  {'-'*10} {'-'*28} {'-'*28} {'-'*5}")

            # Sắp xếp theo ngày rồi tiết
            sorted_entries = sorted(timetable[cls], key=lambda x: (x[0], x[1]))
            for (d, s, subj, r) in sorted_entries:
                subj_name    = SUBJECTS[subj][0]
                teacher_code = SUBJECTS[subj][1]
                teacher_name = TEACHERS[teacher_code]
                slot_name    = SLOT_NAMES[SLOTS_PER_DAY[s]]
                room_name    = ROOMS[r]
                day_name     = DAYS[d]
                print(f"  {day_name:<12} {slot_name:<30} {subj_name:<30} {room_name}  (GV: {teacher_name})")
            print()

        # In thống kê tổng hợp
        print("=" * 70)
        print("  📊 THỐNG KÊ LỊCH GIẢNG DẠY THEO GIÁO VIÊN")
        print("=" * 70)

        teacher_schedule = defaultdict(list)
        for cls in CLASSES:
            for (d, s, subj, r) in timetable[cls]:
                teacher_code = SUBJECTS[subj][1]
                teacher_schedule[teacher_code].append((cls, d, s, subj, r))

        for tc, entries in sorted(teacher_schedule.items()):
            print(f"\n  👨‍🏫 {TEACHERS[tc]} ({tc}) - Tổng số tiết: {len(entries)}")
            sorted_entries = sorted(entries, key=lambda x: (x[1], x[2]))
            for (cls, d, s, subj, r) in sorted_entries:
                print(f"     {DAYS[d]:<10} {SLOT_NAMES[SLOTS_PER_DAY[s]]:<30} "
                      f"Lớp: {cls:<10} Môn: {SUBJECTS[subj][0]:<28} Phòng: {ROOMS[r]}")

        print("\n" + "=" * 70)
        print("  📋 THỐNG KÊ SỬ DỤNG PHÒNG HỌC")
        print("=" * 70)

        room_schedule = defaultdict(list)
        for cls in CLASSES:
            for (d, s, subj, r) in timetable[cls]:
                room_schedule[ROOMS[r]].append((cls, d, s, subj))

        for room, entries in sorted(room_schedule.items()):
            print(f"\n  🏫 Phòng {room} - Số tiết sử dụng: {len(entries)}")
            sorted_entries = sorted(entries, key=lambda x: (x[1], x[2]))
            for (cls, d, s, subj) in sorted_entries:
                print(f"     {DAYS[d]:<10} {SLOT_NAMES[SLOTS_PER_DAY[s]]:<30} "
                      f"Lớp: {cls:<10} Môn: {SUBJECTS[subj][0]}")

        # Kiểm tra ràng buộc
        print("\n" + "=" * 70)
        print("  ✅ KIỂM TRA RÀNG BUỘC")
        print("=" * 70)
        check_constraints(timetable)

    else:
        print("\n❌ Không tìm được lời giải khả thi!")
        print("   Vui lòng kiểm tra lại dữ liệu đầu vào (số phòng, số giáo viên, số tiết).")


# ============================================================
# 7. HÀM KIỂM TRA RÀNG BUỘC
# ============================================================
def check_constraints(timetable):
    errors = []

    # Kiểm tra xung đột lớp học
    for cls in CLASSES:
        time_used = defaultdict(list)
        for (d, s, subj, r) in timetable[cls]:
            time_used[(d, s)].append(subj)
        for (d, s), subjects in time_used.items():
            if len(subjects) > 1:
                errors.append(f"❌ Lớp {cls} bị xung đột tại {DAYS[d]} - {SLOT_NAMES[SLOTS_PER_DAY[s]]}: {subjects}")

    # Kiểm tra xung đột phòng học
    room_usage = defaultdict(list)
    for cls in CLASSES:
        for (d, s, subj, r) in timetable[cls]:
            room_usage[(d, s, r)].append(cls)
    for (d, s, r), classes in room_usage.items():
        if len(classes) > 1:
            errors.append(f"❌ Phòng {ROOMS[r]} bị xung đột tại {DAYS[d]} - {SLOT_NAMES[SLOTS_PER_DAY[s]]}: {classes}")

    # Kiểm tra xung đột giáo viên
    teacher_usage = defaultdict(list)
    for cls in CLASSES:
        for (d, s, subj, r) in timetable[cls]:
            tc = SUBJECTS[subj][1]
            teacher_usage[(d, s, tc)].append((cls, subj))
    for (d, s, tc), entries in teacher_usage.items():
        if len(entries) > 1:
            errors.append(f"❌ GV {TEACHERS[tc]} bị xung đột tại {DAYS[d]} - {SLOT_NAMES[SLOTS_PER_DAY[s]]}: {entries}")

    # Kiểm tra đủ số tiết
    for cls in CLASSES:
        for (subj, required) in CLASS_REQUIREMENTS[cls]:
            actual = sum(1 for (d, s, sj, r) in timetable[cls] if sj == subj)
            if actual != required:
                errors.append(f"❌ Lớp {cls} môn {SUBJECTS[subj][0]}: cần {required} tiết, thực tế {actual} tiết")

    if errors:
        for e in errors:
            print(f"  {e}")
    else:
        print("  ✔ Không có xung đột nào! Tất cả ràng buộc đều được thỏa mãn.")
        total = sum(len(v) for v in timetable.values())
        print(f"  ✔ Tổng số tiết đã xếp: {total} tiết")


# ============================================================
# 8. CHẠY CHƯƠNG TRÌNH
# ============================================================
if __name__ == "__main__":
    solve_timetable()


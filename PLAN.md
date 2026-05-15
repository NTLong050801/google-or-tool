# Kế hoạch phát triển TKB Solver

> Mục tiêu hiện tại: **xếp được thời khóa biểu cho nhiều khoa trong cùng một học kỳ**, đầu vào là các file Excel/CSV chuẩn bị sẵn.

## Bối cảnh

- Demo hiện tại chạy được cho 1 khoa CNTT, học kỳ 2.
- Sắp tới: scale ra nhiều khoa, mỗi học kỳ có dữ liệu riêng.
- Phòng và GV dùng chung toàn trường (có thể dạy chéo khoa) → solver phải giải tổng thể, không tách rời từng khoa.

## Quyết định đã chốt

1. **Giải tổng thể toàn trường** trong một mô hình duy nhất (vì phòng/GV chia sẻ giữa các khoa).
2. **GV bận tinh chỉnh theo dải tuần thật**, không ép cấm cả kỳ.
3. **Chia block tuần (multi week-pattern)**: solver sinh nhiều mẫu thời khóa biểu khi dải tuần của các assignment / giờ bận GV không trùng nhau.

## Vấn đề chặn việc mở rộng

1. `Assignment` chưa có `department_code`, `term_code` — không truy vết dòng nào thuộc khoa nào.
2. `assignment_id` trùng `course_id` (`app/timetable_builder.py:146`) — hai field khác ngữ nghĩa nhưng đang dùng chung giá trị.
3. Chưa có ràng buộc giờ bận GV và giờ bận phòng.
4. `lessons_cluster` cố định 5 tiết cho mọi môn — môn 30 tiết và 60 tiết bị ép giống nhau.
5. Solver chỉ xếp 1 mẫu tuần áp cho toàn dải → không xử lý được môn theo block hoặc GV bận một phần kỳ.
6. Ràng buộc xung đột phòng/GV/nhóm-lớp trong `app/solver.py:136-183` đang scan O(|x|) cho mỗi (day, period) → chậm khi scale lên 2000+ assignments.
7. `app/data/cleans/` trộn dữ liệu 1 khoa với dữ liệu toàn trường (rooms, weeks).

---

## Roadmap

### Phase 1 — Tái cấu trúc dữ liệu cho multi-khoa (1-2 ngày)

Mục đích: nền tảng trước khi đụng solver.

- [ ] Cấu trúc thư mục mới:
  ```
  data/
  ├── shared/                        # toàn trường
  │   ├── rooms.csv
  │   └── projects.xls
  ├── terms/
  │   └── 2025_2026_HK2/
  │       ├── weeks.csv
  │       └── departments/
  │           ├── cntt/
  │           │   ├── classes.csv
  │           │   ├── classes_project.xls
  │           │   ├── teachers.csv
  │           │   ├── teacher_aliases.csv
  │           │   └── teacher_busy.csv
  │           └── kt/...
  └── output/
      └── 2025_2026_HK2/
  ```
- [ ] Thêm `--term`, `--departments` (default: `all`) vào CLI `local_timetable.py`.
- [ ] Builder gộp assignments của tất cả khoa được chọn vào **một** request duy nhất.
- [ ] Output: 1 file CSV tổng + 1 file CSV/khoa (cắt lát theo `department_code`).

### Phase 2 — Bổ sung khoa & học kỳ vào schema (0.5 ngày)

- [ ] Thêm `department_code: str`, `term_code: str` vào `Assignment`.
- [ ] Tách `assignment_id` (auto-increment, định danh nội bộ) khỏi `course_id` (id thật của course offering).
- [ ] Cập nhật `assignment_labels` + export CSV để có cột "Khoa".

### Phase 3 — Chia block tuần (multi week-pattern) (2-3 ngày)

Đây là phase **cốt lõi** vì câu 2 và 3 liên quan trực tiếp.

**Khái niệm**:
- Mỗi assignment có dải `[week_start, week_end]`.
- Mỗi entry giờ bận GV có dải tuần riêng.
- Solver tự động cắt dải tuần học kỳ thành các **block** dựa trên các điểm thay đổi (week boundary), sao cho trong mỗi block, mọi assignment & mọi entry GV bận đều **đồng nhất**.

**Cách làm**:
- [ ] Builder thu thập tất cả "điểm cắt" tuần từ:
  - Dải tuần của từng assignment.
  - Dải tuần của từng entry trong `teacher_busy.csv`.
- [ ] Sinh danh sách block: vd `[1-7], [8-15]`, hoặc `[1-4], [5-8], [9-15]`.
- [ ] Mỗi assignment được "kích hoạt" trong các block giao với dải tuần của nó.
- [ ] Solver mở rộng decision var: `x[(assignment, block_idx, day, period_start, room)]`.
  - Số buổi/tuần `sessions_per_week` áp dụng cho **mỗi block**, hoặc tính tổng giờ chia đều theo độ dài block (cần thống nhất).
- [ ] Ràng buộc xung đột phòng/GV/group **theo từng block** (giữa các block không xung đột vì khác tuần).
- [ ] Output: cột "Tuần từ – Tuần đến" hiển thị block thật, không phải dải tuần học kỳ.

**Cảnh báo độ phức tạp**:
- Số biến tăng theo số block, nhưng mỗi block solver giải độc lập (có thể song song).
- Cần test cẩn thận với data thật để tránh fragment quá nhiều block nhỏ.

### Phase 4 — Giờ bận giảng viên (1 ngày, làm chung Phase 3)

Schema `teacher_busy.csv`:
```csv
teacher_id,week_order,day_of_week,period_start,period_end,reason
CT112,*,2,1,5,Họp khoa sáng thứ 2
CT115,5,3,6,10,Đi công tác
CT120,1-7,*,*,*,Nghỉ thai sản nửa kỳ đầu
```
- `*` = mọi giá trị; `1-7` = dải.
- [ ] Loader đọc & expand thành các `(teacher_id, week_range, day, period_set)`.
- [ ] Trong solver, ở mỗi block: nếu GV bận trong dải tuần của block → cấm các biến tương ứng.
- [ ] Cảnh báo trong logs khi entry bận làm phát sinh thêm block (giúp người dùng hiểu vì sao TKB tuần X khác tuần Y).

### Phase 5 — Tăng tốc solver (1 ngày)

- [ ] Build index một lần: `vars_by_block_day_period: dict[(b, d, p), list[(var, key)]]`.
- [ ] Ba ràng buộc xung đột (phòng/GV/group) dùng index thay vì scan lại `x`.
- [ ] Benchmark trước/sau với data thật của CNTT (ghi lại số assignment, số block, thời gian giải).

### Phase 6 — Linh hoạt lessons_cluster (0.5 ngày)

- [ ] Builder tính `lessons_cluster` theo môn (LT 2-3 tiết, TH 4-5 tiết) thay vì cố định.
- [ ] Solver đã hỗ trợ per-assignment, chỉ cần gỡ check ép cứng ở `app/solver.py:94-103`.

### Phase 7 — Phòng bận & ghép lớp (1-2 ngày)

- [ ] `room_busy.csv` cho phòng đặt trước (sự kiện, hội thảo). Cùng cơ chế dải tuần như `teacher_busy.csv`.
- [ ] Ghép lớp: 1 môn dạy chung 2-3 lớp cùng tiết — `Assignment` cho phép nhiều `class_group_id`.
- [ ] Môn có cả LT và TH: tách thành 2 assignment liên kết qua `parent_course_id`.

### Phase 8 — Ràng buộc cơ sở (0.5 ngày)

- [ ] GV không thể dạy 2 cơ sở khác nhau trong cùng buổi liền kề.
- [ ] `Classroom.campus_code` có sẵn, chỉ cần thêm constraint.

---

## Việc nhỏ song song

- [ ] Chuyển `app/data/filter_*.py` → `scripts/etl/`.
- [ ] Đổi `classroom_type: int` thành Enum (`LT=1, TH=2, X=3, K=4`) ở `app/schemas.py`.
- [ ] Smoke test `tests/test_solver_small.py`: 5 assignment, 3 phòng, 2 GV, 2 block → đảm bảo không vỡ ràng buộc khi refactor.

---

## Thứ tự đề xuất

**Đợt 1 (nền tảng + tính năng cốt lõi):**
Phase 1 → 2 → 3 → 4 → 5

Phase 3 và 4 phải làm chung vì cùng dùng chung khái niệm dải tuần.

**Đợt 2 (sau khi chạy ổn cho 2-3 khoa thật):**
Phase 6 → 7 → 8 + việc nhỏ song song.

---

## Rủi ro còn lại cần theo dõi

- **Block tuần fragment quá nhiều**: nếu mỗi GV có vài entry busy lệch nhau, có thể sinh ra 8-10 block → tăng số biến đáng kể. Cần biện pháp gộp block khi entry busy chỉ ảnh hưởng GV không có assignment trong block đó.
- **`sessions_per_week` khi cắt block**: nếu môn dạy 2 buổi/tuần × 15 tuần (30 buổi), cắt thành 2 block 7-8 tuần thì mỗi block là 14-16 buổi — solver xử lý thế nào? Đề xuất: tính lại `sessions_per_week` cho từng block dựa trên số tuần của block, làm tròn để tổng ≈ 30.
- **Phòng/GV chéo khoa**: vì giải tổng, cần verify dữ liệu các khoa nhất quán (cùng `term_code`, cùng phiên bản `rooms.csv`) trước khi gộp.

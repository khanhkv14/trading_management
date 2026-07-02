# -*- coding: utf-8 -*-
"""Cache đơn giản trong bộ nhớ cho kết quả compute_positions().

Vị thế được TÍNH ĐỘNG từ toàn bộ bảng transactions trên mỗi request nên rất
tốn CPU khi dữ liệu lớn -> gây lag khi chuyển tab. Ta cache kết quả và chỉ xóa
cache khi transactions thay đổi (thêm/sửa/xóa giao dịch). Các request Xem (GET)
đọc thẳng từ cache, không tính lại.

Lưu ý: cache là global theo tiến trình (process). Phù hợp cấu hình 1 worker
như mặc định của PythonAnywhere. Nếu chạy nhiều worker, mỗi worker giữ cache
riêng — cache của worker khác sẽ tự tính lại ở lần đọc kế tiếp sau khi nó bị lệch.
"""
from app.models import get_db
from app.positions import compute_positions

_cache = None  # None = chưa tính / đã bị xóa


def get_positions():
    """Trả về danh sách vị thế; tính 1 lần rồi tái sử dụng từ cache."""
    global _cache
    if _cache is None:
        rows = get_db().execute("SELECT * FROM transactions").fetchall()
        _cache = compute_positions(rows)
    return _cache


def invalidate_positions():
    """Xóa cache — gọi sau khi Thêm/Sửa/Xóa giao dịch."""
    global _cache
    _cache = None

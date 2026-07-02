# -*- coding: utf-8 -*-
"""Cache kết quả compute_positions(), an toàn cho cả nhiều worker WSGI.

Vị thế được TÍNH ĐỘNG từ toàn bộ bảng transactions nên tốn CPU khi dữ liệu lớn
-> ta cache kết quả trong bộ nhớ tiến trình. Nhưng cache in-memory là RIÊNG theo
tiến trình: nếu chạy nhiều worker, worker A ghi giao dịch rồi xóa cache của A thì
worker B vẫn giữ vị thế CŨ -> phục vụ dữ liệu lệch.

Giải: một 'version token' lưu trong bảng settings (khóa 'pos_version'). Mỗi lần
Thêm/Sửa/Xóa giao dịch sẽ TĂNG token trong DB. get_positions() đọc token (một
SELECT rất nhẹ) và chỉ tính lại khi token khác với token của cache đang giữ. Nhờ
đó MỌI worker tự đồng bộ ở lần đọc kế tiếp — invalidation hoạt động xuyên worker.
"""
from app.models import get_db, get_setting, set_setting

from app.positions import compute_positions

_cache = None          # None = chưa tính
_cache_version = None   # token DB mà _cache đang tương ứng


def _db_version():
    """Token phiên bản hiện tại của dữ liệu transactions (chuỗi)."""
    return get_setting("pos_version", "0")


def get_positions():
    """Trả về danh sách vị thế; chỉ tính lại khi dữ liệu đã đổi (token lệch)."""
    global _cache, _cache_version
    ver = _db_version()
    if _cache is None or _cache_version != ver:
        rows = get_db().execute("SELECT * FROM transactions").fetchall()
        _cache = compute_positions(rows)
        _cache_version = ver
    return _cache


def invalidate_positions():
    """Đánh dấu dữ liệu đã đổi — gọi sau khi Thêm/Sửa/Xóa giao dịch.

    Tăng token trong DB để mọi worker (kể cả worker khác) tính lại ở lần đọc kế,
    đồng thời xóa cache của tiến trình hiện tại cho tức thì."""
    global _cache, _cache_version
    try:
        nxt = int(_db_version()) + 1
    except (TypeError, ValueError):
        nxt = 1
    set_setting("pos_version", str(nxt))
    _cache = None
    _cache_version = None

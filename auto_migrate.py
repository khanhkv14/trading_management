# -*- coding: utf-8 -*-
"""
Tự động tạo/cập nhật cấu trúc bảng cho database/trades.db.
Chạy mỗi khi bạn thêm cột mới vào app/models.py:
    python auto_migrate.py
"""
import os
from app.models import migrate

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "database", "trades.db"))

if __name__ == "__main__":
    print(f"Đang migrate: {DB_PATH}")
    migrate(DB_PATH)
    print("Xong.")

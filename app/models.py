# -*- coding: utf-8 -*-
"""Lớp truy cập cơ sở dữ liệu SQLite."""
import os
import sqlite3
from flask import g, current_app

# Danh sách cột chuẩn của mỗi bảng (dùng cho cả tạo bảng lẫn auto_migrate)
TRADE_COLUMNS = [
    ("ngay_gio", "TEXT"), ("thi_truong", "TEXT"), ("symbol", "TEXT"),
    ("huong", "TEXT"), ("gia_vao", "REAL"), ("gia_ra", "REAL"),
    ("khoi_luong", "REAL"), ("stoploss", "REAL"), ("takeprofit", "REAL"),
    ("ket_qua", "TEXT"), ("pnl", "REAL"), ("phi", "REAL"),
    ("chien_luoc", "TEXT"), ("nguon", "TEXT"), ("ghi_chu", "TEXT"),
    ("trang_thai", "TEXT"),
]
SIGNAL_COLUMNS = [
    ("ngay_gio", "TEXT"), ("nguon", "TEXT"), ("symbol", "TEXT"),
    ("huong", "TEXT"), ("vung_gia", "TEXT"), ("do_tin_cay", "INTEGER"),
    ("da_vao", "TEXT"), ("ghi_chu", "TEXT"),
]
# Bảng dữ liệu GỐC của mô hình vị thế: mỗi dòng là 1 lệnh khớp (mua/bán).
# Vị thế được TÍNH ĐỘNG từ bảng này (xem app/positions.py), không lưu phái sinh.
#   chien_luoc : tên chiến lược giao dịch (để xếp hạng hiệu suất theo chiến lược)
# Chi phí được ước lượng tự động qua thuế 0.1% mỗi lệnh (không nhập phí thủ công).
TRANSACTION_COLUMNS = [
    ("ngay", "TEXT"), ("ma_cp", "TEXT"), ("loai", "TEXT"),
    ("so_luong", "REAL"), ("gia", "REAL"),
    ("chien_luoc", "TEXT"), ("ghi_chu", "TEXT"),
]


def _connect(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def get_db():
    """Kết nối DB gắn với request hiện tại."""
    if "db" not in g:
        g.db = _connect(current_app.config["DB_PATH"])
    return g.db


def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(db_path=None):
    """Tạo bảng nếu chưa tồn tại. Có thể gọi độc lập với db_path."""
    db_path = db_path or current_app.config["DB_PATH"]
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = _connect(db_path)
    trade_cols = ", ".join(f"{n} {t}" for n, t in TRADE_COLUMNS)
    signal_cols = ", ".join(f"{n} {t}" for n, t in SIGNAL_COLUMNS)
    tx_cols = ", ".join(f"{n} {t}" for n, t in TRANSACTION_COLUMNS)
    con.executescript(f"""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, {tx_cols}
        );
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, {trade_cols}
        );
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, {signal_cols}
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT
        );
    """)
    con.commit()
    con.close()


def get_setting(key, default=None):
    """Đọc một giá trị cấu hình (vd: vốn tài khoản)."""
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    """Ghi/đè một giá trị cấu hình."""
    db = get_db()
    db.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    db.commit()


def migrate(db_path):
    """Thêm các cột còn thiếu vào bảng đã có (dùng cho auto_migrate.py)."""
    init_db(db_path)
    con = _connect(db_path)
    for table, cols in (("transactions", TRANSACTION_COLUMNS),
                        ("trades", TRADE_COLUMNS), ("signals", SIGNAL_COLUMNS)):
        existing = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        for name, ctype in cols:
            if name not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ctype}")
                print(f"  + Thêm cột {table}.{name}")
    con.commit()
    con.close()

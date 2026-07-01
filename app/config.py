# -*- coding: utf-8 -*-
"""Cấu hình ứng dụng. Đọc thông tin bí mật từ biến môi trường (.env)."""
import os

# Thư mục gốc dự án (…/trading-app)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-secret-doi-di")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
    DB_PATH = os.environ.get(
        "DB_PATH", os.path.join(PROJECT_ROOT, "database", "trades.db")
    )

# -*- coding: utf-8 -*-
"""Application factory: tạo và cấu hình ứng dụng Flask."""
import os
from flask import Flask

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv không bắt buộc; có thể dùng biến môi trường trực tiếp


def create_app():
    app = Flask(__name__)
    app.config.from_object("app.config.Config")

    # Đăng ký đóng kết nối DB sau mỗi request
    from app.models import close_db, init_db
    app.teardown_appcontext(close_db)

    # Tạo bảng nếu chưa có
    with app.app_context():
        init_db()

    # Đăng ký các blueprint
    from app.auth import auth_bp
    from app.main import main_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    return app

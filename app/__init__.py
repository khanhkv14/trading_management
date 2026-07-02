# -*- coding: utf-8 -*-
"""Application factory: tạo và cấu hình ứng dụng Flask."""
import os
from flask import Flask, request

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

    # Cơ chế SPA: chọn layout theo loại request.
    #   - Request thường (mở URL trực tiếp) -> 'base.html' (đủ sidebar).
    #   - Request Fetch (chuyển tab, có header X-Requested-With) -> '_partial.html'
    #     (chỉ nội dung + scripts, để JS chèn vào #main-content).
    # Các template dùng `{% extends layout %}` nên tự động nhận đúng layout.
    @app.context_processor
    def inject_layout():
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        return {"layout": "_partial.html" if is_ajax else "base.html"}

    # Đăng ký các blueprint
    from app.auth import auth_bp
    from app.main import main_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    return app

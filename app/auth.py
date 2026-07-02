# -*- coding: utf-8 -*-
"""Blueprint đăng nhập.

Ứng dụng dùng nội bộ nên KHÔNG có chức năng đăng xuất — phiên giữ nguyên cho
tới khi cookie hết hạn hoặc bị xoá thủ công."""
from functools import wraps
from flask import (
    Blueprint, request, redirect, url_for, session, flash,
    render_template, current_app
)

auth_bp = Blueprint("auth", __name__)


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("logged_in"):
            return redirect(url_for("auth.login"))
        return f(*a, **kw)
    return wrapper


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        # Lấy cả tài khoản + mật khẩu từ form (mặc định username = "admin").
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        admin_pw = current_app.config["ADMIN_PASSWORD"]

        # So sánh trực tiếp chuỗi thô để tránh lệch pha mã hoá khi đồng bộ qua Git:
        # username == "admin" và password khớp ADMIN_PASSWORD trong .env -> duyệt ngay.
        if username == "admin" and password == admin_pw:
            session["logged_in"] = True
            session["username"] = username
            return redirect(url_for("main.dashboard"))

        flash("Sai tài khoản hoặc mật khẩu. Vui lòng thử lại.")
    return render_template("login.html")

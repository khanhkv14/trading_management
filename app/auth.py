# -*- coding: utf-8 -*-
"""Blueprint đăng nhập / đăng xuất."""
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
        if request.form.get("password") == current_app.config["ADMIN_PASSWORD"]:
            session["logged_in"] = True
            return redirect(url_for("main.dashboard"))
        flash("Sai mật khẩu")
    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))

# -*- coding: utf-8 -*-
"""Blueprint chính: tổng quan, giao dịch (mua/bán), vị thế, báo cáo, tín hiệu.

Mô hình: bảng `transactions` là dữ liệu GỐC (mỗi lệnh khớp = 1 dòng). Vị thế
được TÍNH ĐỘNG từ giao dịch qua app/positions.compute_positions().
"""
from datetime import datetime
from flask import (
    Blueprint, request, redirect, url_for, flash, render_template, abort
)
from app.models import get_db, get_setting, set_setting
from app.positions import compute_positions
from app.auth import login_required

main_bp = Blueprint("main", __name__)

VON_KEY = "von_tai_khoan"


# ------------------------- Bộ lọc hiển thị -------------------------
@main_bp.app_template_filter("fmt")
def fmt(v, dec=0):
    """Định dạng số kiểu Việt Nam: nghìn '.', thập phân ','. None -> '—'."""
    if v is None or v == "":
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    neg = f < 0
    f = abs(f)
    if dec == 0:
        body = f"{f:,.0f}"
    else:
        body = f"{f:,.{dec}f}"
        if "." in body:
            body = body.rstrip("0").rstrip(".")
    # Đổi dấu: ',' (nghìn) <-> '.' (thập phân)
    body = body.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return ("-" if neg else "") + body


@main_bp.app_template_filter("vndate")
def vndate(v):
    """'YYYY-MM-DD' -> 'DD/MM/YYYY'. Giữ nguyên nếu không khớp."""
    if not v:
        return "—"
    s = str(v)[:10]
    try:
        y, m, d = s.split("-")
        return f"{d}/{m}/{y}"
    except ValueError:
        return s


def _num(name):
    v = request.form.get(name)
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _f(x):
    try:
        return float(x) if x not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _all_transactions():
    return get_db().execute("SELECT * FROM transactions").fetchall()


# ------------------------- GIAO DỊCH (mua/bán) -------------------------
@main_bp.route("/transactions")
@login_required
def transactions():
    db = get_db()
    f_ma = request.args.get("ma_cp", "").strip()
    f_loai = request.args.get("loai", "")
    q, p = "SELECT * FROM transactions WHERE 1=1", []
    if f_ma:
        q += " AND ma_cp LIKE ?"; p.append(f"%{f_ma}%")
    if f_loai in ("mua", "ban"):
        q += " AND loai=?"; p.append(f_loai)
    q += " ORDER BY ngay DESC, id DESC"
    rows = db.execute(q, p).fetchall()
    return render_template("transactions.html", rows=rows, f_ma=f_ma, f_loai=f_loai)


def _tx_form_vals():
    return dict(
        ngay=(request.form.get("ngay") or "").strip(),
        ma_cp=(request.form.get("ma_cp") or "").strip().upper(),
        loai=request.form.get("loai") if request.form.get("loai") in ("mua", "ban") else "mua",
        so_luong=_num("so_luong"),
        gia=_num("gia"),
    )


@main_bp.route("/transactions/add", methods=["GET", "POST"])
@login_required
def tx_add():
    if request.method == "POST":
        v = _tx_form_vals()
        db = get_db()
        db.execute(
            "INSERT INTO transactions (ngay,ma_cp,loai,so_luong,gia) "
            "VALUES (:ngay,:ma_cp,:loai,:so_luong,:gia)", v)
        db.commit()
        flash("Đã thêm giao dịch")
        return redirect(url_for("main.transactions"))
    empty = {"ngay": datetime.now().strftime("%Y-%m-%d"), "ma_cp": "",
             "loai": "mua", "so_luong": "", "gia": ""}
    return render_template("tx_form.html", title="Thêm giao dịch", r=empty)


@main_bp.route("/transactions/edit/<int:tid>", methods=["GET", "POST"])
@login_required
def tx_edit(tid):
    db = get_db()
    if request.method == "POST":
        v = _tx_form_vals(); v["id"] = tid
        db.execute(
            "UPDATE transactions SET ngay=:ngay,ma_cp=:ma_cp,loai=:loai,"
            "so_luong=:so_luong,gia=:gia WHERE id=:id", v)
        db.commit()
        flash("Đã cập nhật giao dịch")
        return redirect(url_for("main.transactions"))
    r = db.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
    if r is None:
        abort(404)
    return render_template("tx_form.html", title="Sửa giao dịch", r=r)


@main_bp.route("/transactions/delete/<int:tid>")
@login_required
def tx_delete(tid):
    db = get_db()
    db.execute("DELETE FROM transactions WHERE id=?", (tid,))
    db.commit()
    flash("Đã xóa giao dịch")
    return redirect(url_for("main.transactions"))


# ------------------------- VỊ THẾ -------------------------
@main_bp.route("/positions")
@login_required
def positions():
    f_ma = request.args.get("ma_cp", "").strip()
    f_tt = request.args.get("trang_thai", "")
    tu = request.args.get("tu_ngay", "")
    den = request.args.get("den_ngay", "")

    pos = compute_positions(_all_transactions())
    if f_ma:
        pos = [p for p in pos if f_ma.upper() in p["ma_cp"].upper()]
    if f_tt in ("open", "closed"):
        pos = [p for p in pos if p["trang_thai"] == f_tt]
    if tu:
        pos = [p for p in pos if (p["ngay_mo"] or "") >= tu]
    if den:
        pos = [p for p in pos if (p["ngay_mo"] or "") <= den]
    pos.sort(key=lambda p: (p["ngay_mo"] or "", p["seq"]), reverse=True)

    return render_template("positions.html", rows=pos,
                           f_ma=f_ma, f_tt=f_tt, tu_ngay=tu, den_ngay=den)


@main_bp.route("/positions/<ma_cp>/<int:seq>")
@login_required
def position_detail(ma_cp, seq):
    db = get_db()
    pos = compute_positions(_all_transactions())
    match = next((p for p in pos if p["ma_cp"] == ma_cp and p["seq"] == seq), None)
    if match is None:
        abort(404)
    ids = match["tx_ids"]
    qmarks = ",".join("?" * len(ids))
    txs = db.execute(
        f"SELECT * FROM transactions WHERE id IN ({qmarks}) ORDER BY ngay, id",
        ids).fetchall() if ids else []
    return render_template("position_detail.html", p=match, txs=txs)


# ------------------------- TỔNG QUAN -------------------------
@main_bp.route("/")
@login_required
def dashboard():
    pos = compute_positions(_all_transactions())
    closed = sorted([p for p in pos if p["trang_thai"] == "closed"],
                    key=lambda p: (p["ngay_dong"] or ""))
    open_pos = [p for p in pos if p["trang_thai"] == "open"]

    total = len(closed)
    wins = sum(1 for p in closed if (p["pnl"] or 0) > 0)
    win_rate = round(wins / total * 100, 1) if total else 0
    total_pnl = round(sum((p["pnl"] or 0) for p in closed), 2)

    labels, cum, running = [], [], 0
    for p in closed:
        running += (p["pnl"] or 0)
        labels.append(vndate(p["ngay_dong"]))
        cum.append(round(running, 2))

    by_ma = {}
    for p in closed:
        by_ma[p["ma_cp"]] = by_ma.get(p["ma_cp"], 0) + (p["pnl"] or 0)

    return render_template(
        "dashboard.html",
        total=total, win_rate=win_rate, total_pnl=total_pnl,
        open_count=len(open_pos), labels=labels, cum=cum,
        pair_labels=list(by_ma.keys()),
        pair_vals=[round(v, 2) for v in by_ma.values()],
    )


# ------------------------- BÁO CÁO -------------------------
def _compute_report(closed, capital):
    """Thống kê trên các vị thế ĐÃ ĐÓNG (đã sắp tăng dần theo ngày đóng)."""
    total = len(closed)
    win_rows = [p for p in closed if (p["pnl"] or 0) > 0]
    loss_rows = [p for p in closed if (p["pnl"] or 0) < 0]
    wins, losses, be = len(win_rows), len(loss_rows), total - len(win_rows) - len(loss_rows)

    win_rate = round(wins / total * 100, 1) if total else 0
    total_pnl = round(sum((p["pnl"] or 0) for p in closed), 2)
    roi = round(total_pnl / capital * 100, 2) if capital else None

    sum_win = sum((p["pnl"] or 0) for p in win_rows)
    sum_loss = sum((p["pnl"] or 0) for p in loss_rows)  # âm
    avg_win = round(sum_win / wins, 2) if wins else 0
    avg_loss = round(abs(sum_loss) / losses, 2) if losses else 0

    rr_actual = round(avg_win / avg_loss, 2) if avg_loss else None
    profit_factor = round(sum_win / abs(sum_loss), 2) if sum_loss else None

    max_win_streak = max_loss_streak = cur_w = cur_l = 0
    for p in closed:
        v = p["pnl"] or 0
        if v > 0:
            cur_w += 1; cur_l = 0
        elif v < 0:
            cur_l += 1; cur_w = 0
        else:
            cur_w = cur_l = 0
        max_win_streak = max(max_win_streak, cur_w)
        max_loss_streak = max(max_loss_streak, cur_l)

    wr = win_rate / 100
    expectancy = round(wr * avg_win - (1 - wr) * avg_loss, 2)

    eq_labels, eq_cum, roi_cum, running = [], [], [], 0
    for p in closed:
        running += (p["pnl"] or 0)
        eq_labels.append(vndate(p["ngay_dong"]))
        eq_cum.append(round(running, 2))
        roi_cum.append(round(running / capital * 100, 2) if capital else 0)

    g = {}
    for p in closed:
        d = g.setdefault(p["ma_cp"], {"n": 0, "win": 0, "pnl": 0})
        d["n"] += 1
        d["pnl"] += (p["pnl"] or 0)
        if (p["pnl"] or 0) > 0:
            d["win"] += 1
    by_pair = sorted(
        [{"ten": k, "n": d["n"], "pnl": round(d["pnl"], 2),
          "wr": round(d["win"] / d["n"] * 100, 1) if d["n"] else 0}
         for k, d in g.items()],
        key=lambda x: -x["pnl"])

    return dict(
        total=total, wins=wins, losses=losses, be=be,
        win_rate=win_rate, total_pnl=total_pnl, roi=roi,
        avg_win=avg_win, avg_loss=avg_loss,
        rr_actual=rr_actual, profit_factor=profit_factor,
        max_win_streak=max_win_streak, max_loss_streak=max_loss_streak,
        expectancy=expectancy,
        eq_labels=eq_labels, eq_cum=eq_cum, roi_cum=roi_cum, by_pair=by_pair,
    )


@main_bp.route("/report", methods=["GET", "POST"])
@login_required
def report():
    if request.method == "POST":
        set_setting(VON_KEY, _f(request.form.get("von")) or 0)
        flash("Đã lưu vốn tài khoản")
        keep = {k: request.form.get(k) for k in ("tu_ngay", "den_ngay", "ma_cp")
                if request.form.get(k)}
        return redirect(url_for("main.report", **keep))

    capital = _f(get_setting(VON_KEY)) or 0
    tu_ngay = request.args.get("tu_ngay", "")
    den_ngay = request.args.get("den_ngay", "")
    f_ma = request.args.get("ma_cp", "").strip()

    pos = compute_positions(_all_transactions())
    closed = [p for p in pos if p["trang_thai"] == "closed"]
    if f_ma:
        closed = [p for p in closed if f_ma.upper() in p["ma_cp"].upper()]
    if tu_ngay:
        closed = [p for p in closed if (p["ngay_dong"] or "") >= tu_ngay]
    if den_ngay:
        closed = [p for p in closed if (p["ngay_dong"] or "") <= den_ngay]
    closed.sort(key=lambda p: (p["ngay_dong"] or ""))

    stats = _compute_report(closed, capital)
    return render_template(
        "report.html", capital=capital,
        tu_ngay=tu_ngay, den_ngay=den_ngay, f_ma=f_ma, **stats)


# ------------------------- TÍN HIỆU (giữ nguyên) -------------------------
@main_bp.route("/signals", methods=["GET", "POST"])
@login_required
def signals():
    db = get_db()
    if request.method == "POST":
        db.execute("""INSERT INTO signals
          (ngay_gio,nguon,symbol,huong,vung_gia,do_tin_cay,da_vao,ghi_chu)
          VALUES (?,?,?,?,?,?,?,?)""", (
            request.form.get("ngay_gio") or datetime.now().strftime("%Y-%m-%d %H:%M"),
            request.form.get("nguon"), request.form.get("symbol"),
            request.form.get("huong"), request.form.get("vung_gia"),
            int(request.form.get("do_tin_cay") or 3),
            request.form.get("da_vao") or "no", request.form.get("ghi_chu")))
        db.commit()
        flash("Đã thêm tín hiệu")
        return redirect(url_for("main.signals"))
    rows = db.execute("SELECT * FROM signals ORDER BY ngay_gio DESC").fetchall()
    return render_template("signals.html", rows=rows)

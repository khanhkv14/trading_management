# -*- coding: utf-8 -*-
"""Blueprint chính: tổng quan, quản lý lệnh, tín hiệu, gợi ý."""
from datetime import datetime
from flask import (
    Blueprint, request, redirect, url_for, flash, render_template
)
from app.models import get_db, get_setting, set_setting
from app.auth import login_required

main_bp = Blueprint("main", __name__)

MARKETS = ["Crypto", "Forex", "Chứng khoán"]
VON_KEY = "von_tai_khoan"


# ------------------------- TỔNG QUAN -------------------------
@main_bp.route("/")
@login_required
def dashboard():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM trades WHERE trang_thai='closed' ORDER BY ngay_gio"
    ).fetchall()
    total = len(rows)
    wins = sum(1 for r in rows if (r["pnl"] or 0) > 0)
    win_rate = round(wins / total * 100, 1) if total else 0
    total_pnl = round(sum((r["pnl"] or 0) for r in rows), 2)
    open_count = db.execute(
        "SELECT COUNT(*) c FROM trades WHERE trang_thai='open'"
    ).fetchone()["c"]

    labels, cum, running = [], [], 0
    for r in rows:
        running += (r["pnl"] or 0)
        labels.append(r["ngay_gio"] or "")
        cum.append(round(running, 2))

    by_pair = {}
    for r in rows:
        s = r["symbol"] or "?"
        by_pair[s] = by_pair.get(s, 0) + (r["pnl"] or 0)

    return render_template(
        "dashboard.html",
        total=total, win_rate=win_rate, total_pnl=total_pnl, open_count=open_count,
        labels=labels, cum=cum,
        pair_labels=list(by_pair.keys()),
        pair_vals=[round(v, 2) for v in by_pair.values()],
    )


# ------------------------- DANH SÁCH LỆNH -------------------------
@main_bp.route("/trades")
@login_required
def trades():
    db = get_db()
    f_market = request.args.get("thi_truong", "")
    f_symbol = request.args.get("symbol", "")
    f_strat = request.args.get("chien_luoc", "")
    q, p = "SELECT * FROM trades WHERE 1=1", []
    if f_market:
        q += " AND thi_truong=?"; p.append(f_market)
    if f_symbol:
        q += " AND symbol LIKE ?"; p.append(f"%{f_symbol}%")
    if f_strat:
        q += " AND chien_luoc LIKE ?"; p.append(f"%{f_strat}%")
    q += " ORDER BY ngay_gio DESC"
    rows = db.execute(q, p).fetchall()
    return render_template(
        "trades.html", rows=rows, markets=MARKETS,
        f_market=f_market, f_symbol=f_symbol, f_strat=f_strat,
    )


def _form_vals():
    def num(x):
        v = request.form.get(x)
        try:
            return float(v) if v not in (None, "") else None
        except ValueError:
            return None
    pnl = num("pnl")
    return dict(
        ngay_gio=request.form.get("ngay_gio"),
        thi_truong=request.form.get("thi_truong"),
        symbol=request.form.get("symbol"),
        huong=request.form.get("huong"),
        gia_vao=num("gia_vao"), gia_ra=num("gia_ra"),
        khoi_luong=num("khoi_luong"), stoploss=num("stoploss"),
        takeprofit=num("takeprofit"),
        ket_qua=("Win" if (pnl or 0) > 0 else "Loss" if (pnl or 0) < 0 else "BE"),
        pnl=pnl, phi=num("phi"),
        chien_luoc=request.form.get("chien_luoc"),
        nguon=request.form.get("nguon"),
        ghi_chu=request.form.get("ghi_chu"),
        trang_thai=request.form.get("trang_thai"),
    )


@main_bp.route("/trades/add", methods=["GET", "POST"])
@login_required
def trade_add():
    if request.method == "POST":
        v = _form_vals()
        db = get_db()
        db.execute("""INSERT INTO trades
          (ngay_gio,thi_truong,symbol,huong,gia_vao,gia_ra,khoi_luong,stoploss,
           takeprofit,ket_qua,pnl,phi,chien_luoc,nguon,ghi_chu,trang_thai)
          VALUES (:ngay_gio,:thi_truong,:symbol,:huong,:gia_vao,:gia_ra,:khoi_luong,
           :stoploss,:takeprofit,:ket_qua,:pnl,:phi,:chien_luoc,:nguon,:ghi_chu,:trang_thai)""", v)
        db.commit()
        flash("Đã thêm lệnh")
        return redirect(url_for("main.trades"))
    empty = {c: None for c in
             ["ngay_gio", "thi_truong", "symbol", "huong", "gia_vao", "gia_ra",
              "khoi_luong", "stoploss", "takeprofit", "pnl", "phi", "chien_luoc",
              "nguon", "ghi_chu", "trang_thai"]}
    return render_template("trade_form.html", title="Thêm lệnh", r=empty,
                           markets=MARKETS,
                           now=datetime.now().strftime("%Y-%m-%d %H:%M"))


@main_bp.route("/trades/edit/<int:tid>", methods=["GET", "POST"])
@login_required
def trade_edit(tid):
    db = get_db()
    if request.method == "POST":
        v = _form_vals(); v["id"] = tid
        db.execute("""UPDATE trades SET ngay_gio=:ngay_gio,thi_truong=:thi_truong,
          symbol=:symbol,huong=:huong,gia_vao=:gia_vao,gia_ra=:gia_ra,
          khoi_luong=:khoi_luong,stoploss=:stoploss,takeprofit=:takeprofit,
          ket_qua=:ket_qua,pnl=:pnl,phi=:phi,chien_luoc=:chien_luoc,nguon=:nguon,
          ghi_chu=:ghi_chu,trang_thai=:trang_thai WHERE id=:id""", v)
        db.commit()
        flash("Đã cập nhật")
        return redirect(url_for("main.trades"))
    r = db.execute("SELECT * FROM trades WHERE id=?", (tid,)).fetchone()
    return render_template("trade_form.html", title="Sửa lệnh", r=r,
                           markets=MARKETS, now="")


@main_bp.route("/trades/delete/<int:tid>")
@login_required
def trade_delete(tid):
    db = get_db()
    db.execute("DELETE FROM trades WHERE id=?", (tid,))
    db.commit()
    flash("Đã xóa lệnh")
    return redirect(url_for("main.trades"))


# ------------------------- TÍN HIỆU -------------------------
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


# ------------------------- GỢI Ý -------------------------
@main_bp.route("/suggest")
@login_required
def suggest():
    db = get_db()
    trades = db.execute("SELECT * FROM trades WHERE trang_thai='closed'").fetchall()

    stat = {}
    for r in trades:
        s = r["symbol"] or "?"
        d = stat.setdefault(s, {"n": 0, "win": 0, "pnl": 0})
        d["n"] += 1
        d["pnl"] += (r["pnl"] or 0)
        if (r["pnl"] or 0) > 0:
            d["win"] += 1
    pairs = [{"symbol": s, "n": d["n"],
              "wr": round(d["win"] / d["n"] * 100, 1) if d["n"] else 0,
              "pnl": round(d["pnl"], 2)} for s, d in stat.items()]
    good = sorted([p for p in pairs if p["n"] >= 3 and p["pnl"] > 0],
                  key=lambda x: -x["pnl"])
    bad = sorted([p for p in pairs if p["n"] >= 3 and p["pnl"] < 0],
                 key=lambda x: x["pnl"])

    src = {}
    for r in trades:
        n = (r["nguon"] or "").strip()
        if not n:
            continue
        d = src.setdefault(n, {"n": 0, "pnl": 0})
        d["n"] += 1; d["pnl"] += (r["pnl"] or 0)

    open_sig = db.execute(
        "SELECT * FROM signals WHERE da_vao='no' ORDER BY ngay_gio DESC"
    ).fetchall()
    ranked = []
    for s in open_sig:
        sp = src.get((s["nguon"] or "").strip(), {"pnl": 0})
        score = (s["do_tin_cay"] or 0) * 10 + \
                (1 if sp["pnl"] > 0 else -1 if sp["pnl"] < 0 else 0) * 5
        ranked.append({"s": s, "score": score, "src_pnl": round(sp["pnl"], 2)})
    ranked.sort(key=lambda x: -x["score"])

    return render_template("suggest.html", good=good, bad=bad, ranked=ranked)


# ------------------------- BÁO CÁO / THỐNG KÊ -------------------------
def _f(x):
    """Ép về float, trả None nếu không hợp lệ."""
    try:
        return float(x) if x not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _compute_report(rows, capital):
    """Tính toàn bộ chỉ số thống kê trên danh sách lệnh đã đóng (đã lọc).

    rows: đã sắp xếp tăng dần theo ngay_gio.
    """
    total = len(rows)
    win_rows = [r for r in rows if (r["pnl"] or 0) > 0]
    loss_rows = [r for r in rows if (r["pnl"] or 0) < 0]
    be_rows = [r for r in rows if (r["pnl"] or 0) == 0]
    wins, losses, be = len(win_rows), len(loss_rows), len(be_rows)

    win_rate = round(wins / total * 100, 1) if total else 0
    total_pnl = round(sum((r["pnl"] or 0) for r in rows), 2)

    roi = round(total_pnl / capital * 100, 2) if capital else None

    sum_win = sum((r["pnl"] or 0) for r in win_rows)
    sum_loss = sum((r["pnl"] or 0) for r in loss_rows)  # âm
    avg_win = round(sum_win / wins, 2) if wins else 0
    avg_loss = round(abs(sum_loss) / losses, 2) if losses else 0  # số dương

    rr_actual = round(avg_win / avg_loss, 2) if avg_loss else None
    profit_factor = round(sum_win / abs(sum_loss), 2) if sum_loss else None

    # RR theo kế hoạch = trung bình |TP-vào| / |vào-SL| trên từng lệnh
    rr_plans = []
    for r in rows:
        gv, sl, tp = _f(r["gia_vao"]), _f(r["stoploss"]), _f(r["takeprofit"])
        if gv is None or sl is None or tp is None:
            continue
        risk = abs(gv - sl)
        if risk == 0:
            continue
        rr_plans.append(abs(tp - gv) / risk)
    rr_plan = round(sum(rr_plans) / len(rr_plans), 2) if rr_plans else None

    # Chuỗi thắng/thua dài nhất
    max_win_streak = max_loss_streak = cur_w = cur_l = 0
    for r in rows:
        p = r["pnl"] or 0
        if p > 0:
            cur_w += 1; cur_l = 0
        elif p < 0:
            cur_l += 1; cur_w = 0
        else:
            cur_w = cur_l = 0
        max_win_streak = max(max_win_streak, cur_w)
        max_loss_streak = max(max_loss_streak, cur_l)

    # Kỳ vọng mỗi lệnh
    wr = (win_rate / 100)
    expectancy = round(wr * avg_win - (1 - wr) * avg_loss, 2)

    # Đường equity + ROI lũy kế
    eq_labels, eq_cum, roi_cum, running = [], [], [], 0
    for r in rows:
        running += (r["pnl"] or 0)
        eq_labels.append(r["ngay_gio"] or "")
        eq_cum.append(round(running, 2))
        roi_cum.append(round(running / capital * 100, 2) if capital else 0)

    # Gom theo cặp/mã và theo chiến lược
    def group_by(field):
        g = {}
        for r in rows:
            k = (r[field] or "?").strip() or "?"
            d = g.setdefault(k, {"n": 0, "win": 0, "pnl": 0})
            d["n"] += 1
            d["pnl"] += (r["pnl"] or 0)
            if (r["pnl"] or 0) > 0:
                d["win"] += 1
        out = [{"ten": k, "n": d["n"], "pnl": round(d["pnl"], 2),
                "wr": round(d["win"] / d["n"] * 100, 1) if d["n"] else 0}
               for k, d in g.items()]
        return sorted(out, key=lambda x: -x["pnl"])

    by_pair = group_by("symbol")
    by_strat = group_by("chien_luoc")

    return dict(
        total=total, wins=wins, losses=losses, be=be,
        win_rate=win_rate, total_pnl=total_pnl, roi=roi,
        avg_win=avg_win, avg_loss=avg_loss,
        rr_actual=rr_actual, rr_plan=rr_plan,
        profit_factor=profit_factor,
        max_win_streak=max_win_streak, max_loss_streak=max_loss_streak,
        expectancy=expectancy,
        eq_labels=eq_labels, eq_cum=eq_cum, roi_cum=roi_cum,
        by_pair=by_pair, by_strat=by_strat,
    )


@main_bp.route("/report", methods=["GET", "POST"])
@login_required
def report():
    db = get_db()

    # Lưu cấu hình vốn tài khoản (giữ nguyên bộ lọc qua các hidden field)
    if request.method == "POST":
        set_setting(VON_KEY, _f(request.form.get("von")) or 0)
        flash("Đã lưu vốn tài khoản")
        keep = {k: request.form.get(k) for k in
                ("tu_ngay", "den_ngay", "thi_truong", "symbol", "chien_luoc")
                if request.form.get(k)}
        return redirect(url_for("main.report", **keep))

    capital = _f(get_setting(VON_KEY)) or 0

    # Bộ lọc
    tu_ngay = request.args.get("tu_ngay", "")
    den_ngay = request.args.get("den_ngay", "")
    f_market = request.args.get("thi_truong", "")
    f_symbol = request.args.get("symbol", "")
    f_strat = request.args.get("chien_luoc", "")

    q = "SELECT * FROM trades WHERE trang_thai='closed'"
    p = []
    if tu_ngay:
        q += " AND ngay_gio >= ?"; p.append(tu_ngay)
    if den_ngay:
        q += " AND ngay_gio <= ?"; p.append(den_ngay + " 23:59")
    if f_market:
        q += " AND thi_truong = ?"; p.append(f_market)
    if f_symbol:
        q += " AND symbol LIKE ?"; p.append(f"%{f_symbol}%")
    if f_strat:
        q += " AND chien_luoc LIKE ?"; p.append(f"%{f_strat}%")
    q += " ORDER BY ngay_gio ASC"
    rows = db.execute(q, p).fetchall()

    stats = _compute_report(rows, capital)
    return render_template(
        "report.html", capital=capital, markets=MARKETS,
        tu_ngay=tu_ngay, den_ngay=den_ngay,
        f_market=f_market, f_symbol=f_symbol, f_strat=f_strat,
        **stats,
    )

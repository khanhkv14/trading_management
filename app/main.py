# -*- coding: utf-8 -*-
"""Blueprint chính: tổng quan, giao dịch (mua/bán), vị thế, báo cáo.

Mô hình: bảng `transactions` là dữ liệu GỐC (mỗi lệnh khớp = 1 dòng). Vị thế
được TÍNH ĐỘNG từ giao dịch qua app/positions.compute_positions().
"""
import csv
import io
from datetime import datetime, timedelta
from flask import (
    Blueprint, request, redirect, url_for, flash, render_template, abort,
    Response, jsonify
)
from app.models import get_db
from app.cache import get_positions, invalidate_positions
from app.prices import get_prices
from app.auth import login_required

main_bp = Blueprint("main", __name__)

# Biên độ HÒA VỐN (VND): phân loại Win/Loss/BE dựa trên PnL THỰC TẾ SAU THUẾ.
#   WIN  khi pnl_sau_thue >  BE_MARGIN
#   LOSS khi pnl_sau_thue < -BE_MARGIN
#   BE   khi -BE_MARGIN <= pnl_sau_thue <= BE_MARGIN
BE_MARGIN = 200_000


def _pnl_net(p):
    """PnL thực tế SAU THUẾ của một vị thế (đã trừ thuế mọi lệnh con)."""
    return p.get("pnl_sau_thue") or 0


def _classify(p):
    """Xếp vị thế đã đóng vào 'win' / 'loss' / 'be' theo PnL sau thuế + biên BE."""
    net = _pnl_net(p)
    if net > BE_MARGIN:
        return "win"
    if net < -BE_MARGIN:
        return "loss"
    return "be"


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
    """'YYYY-MM-DD' -> 'D/M/YY' gọn: bỏ số 0 thừa ở đầu ngày/tháng, năm lấy 2 số
    cuối (vd 2026-07-02 -> 2/7/26). Giữ nguyên nếu không khớp định dạng."""
    if not v:
        return "—"
    s = str(v)[:10]
    try:
        y, m, d = s.split("-")
        return f"{int(d)}/{int(m)}/{y[-2:]}"
    except (ValueError, TypeError):
        return s


def _num(name):
    v = request.form.get(name)
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


# ------------------------- GIAO DỊCH (mua/bán) -------------------------
@main_bp.route("/transactions")
@login_required
def transactions():
    db = get_db()
    f_ma = request.args.get("ma_cp", "").strip()
    f_loai = request.args.get("loai", "")

    # Trang hiện tại (mặc định 1, không cho nhỏ hơn 1).
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1
    per_page = 15

    # Mệnh đề WHERE dùng chung cho cả câu đếm và câu lấy dữ liệu.
    where, p = "WHERE 1=1", []
    if f_ma:
        where += " AND ma_cp LIKE ?"; p.append(f"%{f_ma}%")
    if f_loai in ("mua", "ban"):
        where += " AND loai=?"; p.append(f_loai)

    # Đếm tổng số dòng theo bộ lọc -> tính tổng số trang.
    total_rows = db.execute(
        f"SELECT COUNT(*) FROM transactions {where}", p
    ).fetchone()[0]
    total_pages = max(1, (total_rows + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * per_page

    # Chỉ lấy đúng 1 trang dữ liệu bằng LIMIT/OFFSET.
    q = f"SELECT * FROM transactions {where} ORDER BY ngay DESC, id DESC LIMIT ? OFFSET ?"
    rows = db.execute(q, p + [per_page, offset]).fetchall()
    return render_template(
        "transactions.html", rows=rows, f_ma=f_ma, f_loai=f_loai,
        page=page, total_pages=total_pages, total_rows=total_rows,
    )


def _tx_form_vals():
    # Chiến lược: nếu chọn "Khác (Tự nhập)..." thì lấy giá trị từ ô tự nhập.
    chien_luoc = (request.form.get("chien_luoc") or "").strip()
    if chien_luoc == "__other__":
        chien_luoc = (request.form.get("chien_luoc_khac") or "").strip()
    return dict(
        ngay=(request.form.get("ngay") or "").strip(),
        ma_cp=(request.form.get("ma_cp") or "").strip().upper(),
        loai=request.form.get("loai") if request.form.get("loai") in ("mua", "ban") else "mua",
        so_luong=_num("so_luong"),
        gia=_num("gia"),
        chien_luoc=chien_luoc,
        ghi_chu=(request.form.get("ghi_chu") or "").strip(),
    )


@main_bp.route("/transactions/add", methods=["GET", "POST"])
@login_required
def tx_add():
    if request.method == "POST":
        v = _tx_form_vals()
        db = get_db()
        db.execute(
            "INSERT INTO transactions "
            "(ngay,ma_cp,loai,so_luong,gia,chien_luoc,ghi_chu) VALUES "
            "(:ngay,:ma_cp,:loai,:so_luong,:gia,:chien_luoc,:ghi_chu)", v)
        db.commit()
        invalidate_positions()
        flash("Transaction added")
        return redirect(url_for("main.transactions"))
    empty = {"ngay": datetime.now().strftime("%Y-%m-%d"), "ma_cp": "",
             "loai": "mua", "so_luong": "", "gia": "", "chien_luoc": "",
             "ghi_chu": ""}
    # Gợi ý 5 mã cổ phiếu + 5 chiến lược giao dịch gần đây nhất cho ô nhập liệu.
    db = get_db()
    recent_tickers = [row["ma_cp"] for row in db.execute(
        "SELECT DISTINCT ma_cp FROM transactions ORDER BY id DESC LIMIT 5"
    ).fetchall()]
    recent_strategies = [row["chien_luoc"] for row in db.execute(
        "SELECT DISTINCT chien_luoc FROM transactions "
        "WHERE chien_luoc IS NOT NULL AND chien_luoc != '' "
        "ORDER BY id DESC LIMIT 5"
    ).fetchall()]
    return render_template("tx_form.html", title="Add Transaction", r=empty,
                           recent_tickers=recent_tickers,
                           recent_strategies=recent_strategies)


@main_bp.route("/transactions/edit/<int:tid>", methods=["GET", "POST"])
@login_required
def tx_edit(tid):
    db = get_db()
    if request.method == "POST":
        v = _tx_form_vals(); v["id"] = tid
        db.execute(
            "UPDATE transactions SET ngay=:ngay,ma_cp=:ma_cp,loai=:loai,"
            "so_luong=:so_luong,gia=:gia,chien_luoc=:chien_luoc,"
            "ghi_chu=:ghi_chu WHERE id=:id", v)
        db.commit()
        invalidate_positions()
        flash("Transaction updated")
        return redirect(url_for("main.transactions"))
    r = db.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
    if r is None:
        abort(404)
    return render_template("tx_form.html", title="Edit Transaction", r=r)


@main_bp.route("/transactions/delete/<int:tid>")
@login_required
def tx_delete(tid):
    db = get_db()
    db.execute("DELETE FROM transactions WHERE id=?", (tid,))
    db.commit()
    invalidate_positions()
    flash("Transaction deleted")
    return redirect(url_for("main.transactions"))


# ------------------------- VỊ THẾ -------------------------
@main_bp.route("/positions")
@login_required
def positions():
    f_ma = request.args.get("ma_cp", "").strip()
    f_tt = request.args.get("trang_thai", "open")  # mặc định: Đang mở

    pos = list(get_positions())  # copy: tránh .sort() làm thay đổi cache
    if f_ma:
        pos = [p for p in pos if f_ma.upper() in p["ma_cp"].upper()]
    if f_tt in ("open", "closed"):
        pos = [p for p in pos if p["trang_thai"] == f_tt]
    pos.sort(key=lambda p: (p["ngay_mo"] or "", p["seq"]), reverse=True)

    return render_template("positions.html", rows=pos, f_ma=f_ma, f_tt=f_tt)


@main_bp.route("/positions/<ma_cp>/<int:seq>")
@login_required
def position_detail(ma_cp, seq):
    db = get_db()
    pos = get_positions()
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
def _open_live(open_pos, prices):
    """Ghép giá thị trường vào các vị thế ĐANG MỞ -> lãi/lỗ TẠM TÍNH.

    PnL tạm tính (chưa thực hiện) = (giá hiện tại - giá vốn TB) * số CP đang giữ.
    Đây là lãi/lỗ GỘP theo giá thị trường (chưa trừ thuế bán ước tính), giúp nhìn
    nhanh 'đang lãi/lỗ bao nhiêu' nếu chốt ngay bây giờ. Trả về (rows, tổng PnL)."""
    rows, total = [], 0.0
    for p in open_pos:
        qty = p["so_luong_giu"] or 0
        cost = p["gia_mua_tb"] or 0
        price = prices.get(p["ma_cp"].upper())
        if price is not None and qty:
            pnl = (price - cost) * qty
            cost_val = cost * qty
            roi = (pnl / cost_val * 100) if cost_val else None
            total += pnl
        else:
            pnl = roi = None
        rows.append({
            "ma_cp": p["ma_cp"], "seq": p["seq"],
            "so_luong_giu": qty, "gia_mua_tb": cost,
            "gia_hien_tai": price,
            "pnl": round(pnl, 2) if pnl is not None else None,
            "roi": round(roi, 2) if roi is not None else None,
        })
    # Sắp xếp: lãi/lỗ lớn nhất (theo trị tuyệt đối) lên trên cho dễ theo dõi.
    rows.sort(key=lambda r: abs(r["pnl"]) if r["pnl"] is not None else -1,
              reverse=True)
    return rows, round(total, 2)


@main_bp.route("/")
@login_required
def dashboard():
    pos = get_positions()
    closed = [p for p in pos if p["trang_thai"] == "closed"]
    open_pos = [p for p in pos if p["trang_thai"] == "open"]

    total = len(closed)
    # Win/Win-rate/PnL đều dựa trên PnL THỰC TẾ SAU THUẾ + biên hòa vốn BE.
    wins = sum(1 for p in closed if _classify(p) == "win")
    win_rate = round(wins / total * 100, 1) if total else 0
    total_pnl = round(sum(_pnl_net(p) for p in closed), 2)

    # Giá thị trường + lãi/lỗ tạm tính cho các vị thế đang mở (render lần đầu).
    prices = get_prices([p["ma_cp"] for p in open_pos]) if open_pos else {}
    live_rows, live_total = _open_live(open_pos, prices)

    return render_template(
        "dashboard.html",
        total=total, win_rate=win_rate, total_pnl=total_pnl,
        open_count=len(open_pos), live_rows=live_rows, live_total=live_total,
    )


@main_bp.route("/api/open-pnl")
@login_required
def api_open_pnl():
    """JSON lãi/lỗ TẠM TÍNH theo giá thị trường cho các vị thế đang mở.

    Dashboard poll endpoint này định kỳ để cập nhật giá & PnL realtime mà không
    cần tải lại cả trang."""
    open_pos = [p for p in get_positions() if p["trang_thai"] == "open"]
    prices = get_prices([p["ma_cp"] for p in open_pos]) if open_pos else {}
    live_rows, live_total = _open_live(open_pos, prices)
    return jsonify(rows=live_rows, total=live_total)


# ------------------------- BÁO CÁO -------------------------
def _compute_report(closed):
    """Thống kê trên các vị thế ĐÃ ĐÓNG (đã sắp tăng dần theo ngày đóng)."""
    total = len(closed)
    # Phân loại theo PnL THỰC TẾ SAU THUẾ (_pnl_net) + biên hòa vốn BE_MARGIN.
    win_rows = [p for p in closed if _classify(p) == "win"]
    loss_rows = [p for p in closed if _classify(p) == "loss"]
    wins, losses = len(win_rows), len(loss_rows)
    be = total - wins - losses

    win_rate = round(wins / total * 100, 1) if total else 0
    total_pnl = round(sum(_pnl_net(p) for p in closed), 2)

    sum_win = sum(_pnl_net(p) for p in win_rows)
    sum_loss = sum(_pnl_net(p) for p in loss_rows)   # âm
    # Avg Win/Loss chỉ tính trên các lệnh THỰC SỰ Win/Loss sau thuế (bỏ qua BE).
    avg_win = round(sum_win / wins, 2) if wins else 0
    avg_loss = round(abs(sum_loss) / losses, 2) if losses else 0

    rr_actual = round(avg_win / avg_loss, 2) if avg_loss else None
    profit_factor = round(sum_win / abs(sum_loss), 2) if sum_loss else None

    # Chuỗi thắng/thua liên tiếp: BE (hòa vốn) làm ĐỨT cả hai chuỗi.
    max_win_streak = max_loss_streak = cur_w = cur_l = 0
    for p in closed:
        cls = _classify(p)
        if cls == "win":
            cur_w += 1; cur_l = 0
        elif cls == "loss":
            cur_l += 1; cur_w = 0
        else:
            cur_w = cur_l = 0
        max_win_streak = max(max_win_streak, cur_w)
        max_loss_streak = max(max_loss_streak, cur_l)

    wr = win_rate / 100
    expectancy = round(wr * avg_win - (1 - wr) * avg_loss, 2)

    eq_labels, eq_cum, running = [], [], 0
    for p in closed:
        running += _pnl_net(p)
        eq_labels.append(vndate(p["ngay_dong"]))
        eq_cum.append(round(running, 2))

    # --- Max Drawdown: mức sụt giảm lớn nhất từ ĐỈNH của đường Cumulative PnL ---
    # Duyệt chuỗi lũy kế theo thời gian, giữ đỉnh cao nhất đã gặp; drawdown tại mỗi
    # điểm = đỉnh - giá trị hiện tại. Max Drawdown là drawdown lớn nhất (>= 0).
    # Đỉnh khởi đầu = 0 (mốc vốn ban đầu) để cú giảm đầu tiên cũng được tính.
    peak = max_dd = 0.0
    for v in eq_cum:
        if v > peak:
            peak = v
        max_dd = max(max_dd, peak - v)
    max_drawdown = round(max_dd, 2)

    # --- Lệnh thắng lớn nhất / thua lớn nhất theo cả TIỀN MẶT lẫn %ROI ---
    def _roi_val(p):
        return p["roi"] if p.get("roi") is not None else 0

    def _card(p):
        """Gói dữ liệu 1 lệnh để hiển thị thẻ; None khi không có lệnh nào."""
        if p is None:
            return None
        return {"ma_cp": p["ma_cp"], "pnl": round(_pnl_net(p), 2),
                "roi": p.get("roi"), "ngay_dong": p["ngay_dong"]}

    if closed:
        max_win_cash = _card(max(closed, key=_pnl_net))
        max_loss_cash = _card(min(closed, key=_pnl_net))
        max_win_roi = _card(max(closed, key=_roi_val))
        max_loss_roi = _card(min(closed, key=_roi_val))
    else:
        max_win_cash = max_loss_cash = max_win_roi = max_loss_roi = None

    g = {}
    for p in closed:
        d = g.setdefault(p["ma_cp"], {"n": 0, "win": 0, "pnl": 0})
        d["n"] += 1
        d["pnl"] += _pnl_net(p)
        if _classify(p) == "win":
            d["win"] += 1
    by_pair = sorted(
        [{"ten": k, "n": d["n"], "pnl": round(d["pnl"], 2),
          "wr": round(d["win"] / d["n"] * 100, 1) if d["n"] else 0}
         for k, d in g.items()],
        key=lambda x: -x["pnl"])

    # --- Xếp hạng hiệu suất theo CHIẾN LƯỢC giao dịch (chien_luoc) ---
    # Vị thế không khai báo chiến lược gom vào nhóm '—' để không bỏ sót dữ liệu.
    gs = {}
    for p in closed:
        key = (p.get("chien_luoc") or "").strip() or "—"
        d = gs.setdefault(key, {"n": 0, "win": 0, "pnl": 0})
        d["n"] += 1
        d["pnl"] += _pnl_net(p)
        if _classify(p) == "win":
            d["win"] += 1
    by_strategy = sorted(
        [{"ten": k, "n": d["n"], "pnl": round(d["pnl"], 2),
          "wr": round(d["win"] / d["n"] * 100, 1) if d["n"] else 0}
         for k, d in gs.items()],
        key=lambda x: -x["pnl"])

    return dict(
        total=total, wins=wins, losses=losses, be=be,
        win_rate=win_rate, total_pnl=total_pnl,
        avg_win=avg_win, avg_loss=avg_loss,
        rr_actual=rr_actual, profit_factor=profit_factor,
        max_win_streak=max_win_streak, max_loss_streak=max_loss_streak,
        expectancy=expectancy, max_drawdown=max_drawdown,
        max_win_cash=max_win_cash, max_loss_cash=max_loss_cash,
        max_win_roi=max_win_roi, max_loss_roi=max_loss_roi,
        eq_labels=eq_labels, eq_cum=eq_cum,
        by_pair=by_pair, by_strategy=by_strategy,
    )


@main_bp.route("/report")
@login_required
def report():
    f_ma = request.args.get("ma_cp", "").strip()

    closed = [p for p in get_positions() if p["trang_thai"] == "closed"]
    if f_ma:
        closed = [p for p in closed if f_ma.upper() in p["ma_cp"].upper()]
    closed.sort(key=lambda p: (p["ngay_dong"] or ""))

    # Mặc định khoảng xuất CSV: 1 tháng gần nhất (từ 30 ngày trước -> hôm nay).
    today = datetime.now()
    export_to = today.strftime("%Y-%m-%d")
    export_from = (today - timedelta(days=30)).strftime("%Y-%m-%d")

    stats = _compute_report(closed)
    return render_template("report.html", f_ma=f_ma,
                           export_from=export_from, export_to=export_to, **stats)


@main_bp.route("/report/export-closed-csv")
@login_required
def report_export_closed_csv():
    """Export analysis of CLOSED positions whose close date is in [tu_ngay, den_ngay].

    Data are DYNAMICALLY computed positions (get_positions), not raw transactions.
    A BOM is prepended so Excel reads UTF-8 correctly (notes may hold Vietnamese)."""
    tu_ngay = (request.args.get("tu_ngay") or "").strip()
    den_ngay = (request.args.get("den_ngay") or "").strip()

    closed = [p for p in get_positions() if p["trang_thai"] == "closed"]
    # Lọc theo ngày đóng (so sánh chuỗi 'YYYY-MM-DD' -> đúng thứ tự thời gian).
    if tu_ngay:
        closed = [p for p in closed if (p["ngay_dong"] or "") >= tu_ngay]
    if den_ngay:
        closed = [p for p in closed if (p["ngay_dong"] or "") <= den_ngay]
    closed.sort(key=lambda p: (p["ngay_dong"] or "", p["ma_cp"]))

    db = get_db()

    def _fmt_num(x):
        # Bỏ phần thập phân thừa: 100.0 -> '100', 25.30 -> '25.3'.
        try:
            f = float(x)
        except (TypeError, ValueError):
            return str(x)
        return str(int(f)) if f == int(f) else f"{f:g}"

    def _notes(tx_ids):
        # Liệt kê MỌI giao dịch con của vị thế, mỗi lệnh 1 dòng trong ô Note, kèm
        # ngữ cảnh để phân biệt lệnh mua/bán, ngày và số tiền tương ứng. Phần chữ
        # ghi chú chỉ được nối thêm khi giao dịch đó thực sự có ghi chú.
        if not tx_ids:
            return ""
        qmarks = ",".join("?" * len(tx_ids))
        rows = db.execute(
            f"SELECT loai, ngay, so_luong, gia, ghi_chu FROM transactions "
            f"WHERE id IN ({qmarks}) ORDER BY ngay, id", list(tx_ids)).fetchall()
        lines = []
        for r in rows:
            nhan = "MUA" if (r["loai"] or "").lower() == "mua" else "BÁN"
            qty = float(r["so_luong"] or 0)
            price = float(r["gia"] or 0)
            thanh_tien = round(qty * price, 2)
            line = (f"[{nhan}] {r['ngay'] or ''} · SL {_fmt_num(qty)} @ "
                    f"{_fmt_num(price)} = {_fmt_num(thanh_tien)}")
            note = (r["ghi_chu"] or "").strip()
            if note:
                line += f" · {note}"
            lines.append(line)
        return "\n".join(lines)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Ticker", "Quantity", "Cost Price (Entry)", "Sell Price (Exit)",
                "Total Value", "PnL", "PnL %", "Open Date", "Close Date", "Note"])
    for p in closed:
        qty = p["tong_ban"] or 0
        cost = p["gia_mua_tb"] or 0
        total_value = round(qty * cost, 2)
        w.writerow([
            p["ma_cp"], qty, cost, p["gia_ban_tb"] or 0,
            total_value, p["pnl"] if p["pnl"] is not None else 0,
            p["roi"] if p["roi"] is not None else 0,
            p["ngay_mo"] or "", p["ngay_dong"] or "", _notes(p["tx_ids"]),
        ])

    # BOM đầu file để Excel đọc đúng UTF-8 (ghi chú có thể là tiếng Việt).
    data = "﻿" + buf.getvalue()
    return Response(
        data,
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                "attachment; filename=closed_positions_analysis.csv"
        },
    )

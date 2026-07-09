# -*- coding: utf-8 -*-
"""Blueprint chính: tổng quan, giao dịch (mua/bán), vị thế, báo cáo.

Mô hình: bảng `transactions` là dữ liệu GỐC (mỗi lệnh khớp = 1 dòng). Vị thế
được TÍNH ĐỘNG từ giao dịch qua app/positions.compute_positions().
"""
import csv
import io
import logging
from datetime import datetime, timedelta
from flask import (
    Blueprint, request, redirect, url_for, flash, render_template, abort,
    Response, jsonify
)
from app.models import get_db
from app.cache import get_positions, invalidate_positions
from app.auth import login_required
from app.market_flows import aggregate, iso_week_of, month_of, InvalidPeriod
from app.flows_ingest import normalize_rows, upsert

log = logging.getLogger(__name__)

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


def _safe_next(target):
    """Chỉ cho phép redirect tới đường dẫn nội bộ (bắt đầu bằng "/" nhưng không
    phải "//" — chặn open-redirect sang tên miền ngoài). Trả None nếu không hợp lệ."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None


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
    # `next`: nơi quay lại sau khi lưu (vd: trang chi tiết vị thế). Chỉ chấp nhận
    # đường dẫn nội bộ để tránh open-redirect; mặc định về danh sách giao dịch.
    nxt = _safe_next(request.args.get("next"))
    if request.method == "POST":
        v = _tx_form_vals(); v["id"] = tid
        db.execute(
            "UPDATE transactions SET ngay=:ngay,ma_cp=:ma_cp,loai=:loai,"
            "so_luong=:so_luong,gia=:gia,chien_luoc=:chien_luoc,"
            "ghi_chu=:ghi_chu WHERE id=:id", v)
        db.commit()
        invalidate_positions()
        flash("Transaction updated")
        return redirect(nxt or url_for("main.transactions"))
    r = db.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
    if r is None:
        abort(404)
    return render_template("tx_form.html", title="Edit Transaction", r=r,
                           next_url=nxt)


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

    # Vị thế đang mở: server chỉ truyền dữ liệu GỐC (mã, số lượng giữ, giá vốn TB).
    # GIÁ THỊ TRƯỜNG + lãi/lỗ tạm tính được lấy & tính NGAY TRÊN TRÌNH DUYỆT
    # (fetch VNDirect) -> chạy được cả trên PythonAnywhere free: whitelist chặn
    # kết nối RA từ server, KHÔNG chặn trình duyệt người dùng gọi API bên ngoài.
    open_rows = [{
        "ma_cp": p["ma_cp"], "seq": p["seq"],
        "so_luong_giu": p["so_luong_giu"] or 0,
        "gia_mua_tb": p["gia_mua_tb"] or 0,
    } for p in open_pos]
    open_rows.sort(key=lambda r: r["ma_cp"])

    # Dòng tiền Khối ngoại TUẦN NÀY: hiện tóm tắt top mua/bán ròng ngay trên
    # Dashboard (bảng đầy đủ tuần/tháng + tự doanh ở trang /market-flows). Lỗi
    # (chưa nạp dữ liệu) -> để None, template hiển thị thông báo hướng dẫn.
    today = datetime.now()
    flow_week = iso_week_of(today)
    try:
        flow = aggregate(get_db(), "weekly", flow_week, khoi="foreign", limit=5)
    except InvalidPeriod:
        log.exception("dashboard: không dựng được dòng tiền tuần %s", flow_week)
        flow = None

    return render_template(
        "dashboard.html",
        total=total, win_rate=win_rate, total_pnl=total_pnl,
        open_count=len(open_pos), open_rows=open_rows,
        flow=flow, flow_week=flow_week, flow_start=_flow_update_start(get_db()),
    )


# ------------------------- DÒNG TIỀN KHỐI NGOẠI / TỰ DOANH -------------------------
def _clamp_limit(raw, default=20, lo=1, hi=100):
    """Ép `limit` về số nguyên trong [lo, hi]; giá trị lỗi -> default."""
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


@main_bp.route("/api/market-trend")
@login_required
def api_market_trend():
    """API JSON: top mua/bán ròng của một khối trong tuần/tháng chỉ định.

    Ví dụ:
        GET /api/market-trend?type=weekly&value=2026-W28
        GET /api/market-trend?type=monthly&value=2026-07&khoi=prop&limit=10

    Tham số:
        type  : 'weekly' | 'monthly'                (bắt buộc)
        value : '2026-W28' | '2026-07'              (mặc định: tuần/tháng hiện tại)
        khoi  : 'foreign' (khối ngoại) | 'prop'     (mặc định 'foreign')
        limit : số mã mỗi chiều top mua/bán          (1..100, mặc định 20)

    Kết quả `ranked` được sắp GIẢM DẦN theo giá trị mua ròng.
    """
    period_type = (request.args.get("type") or "weekly").strip().lower()
    khoi = (request.args.get("khoi") or "foreign").strip().lower()
    limit = _clamp_limit(request.args.get("limit"))

    # Không truyền value -> mặc định kỳ HIỆN TẠI cho tiện gọi nhanh.
    value = (request.args.get("value") or "").strip()
    if not value:
        today = datetime.now()
        value = iso_week_of(today) if period_type == "weekly" else month_of(today)

    try:
        data = aggregate(get_db(), period_type, value, khoi=khoi, limit=limit)
        return jsonify({"ok": True, **data})
    except InvalidPeriod as e:
        # Lỗi do người gọi (tham số sai) -> 400, kèm thông điệp rõ ràng.
        log.warning("market-trend tham số sai: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:  # noqa: BLE001 - chốt chặn cuối, tránh 500 trần
        log.exception("market-trend lỗi không mong đợi")
        return jsonify({"ok": False, "error": "Server error while computing money flow"}), 500


@main_bp.route("/market-flows")
@login_required
def market_flows():
    """Trang dashboard dòng tiền: bảng top mua/bán ròng tuần & tháng hiện tại.

    Server render sẵn kỳ hiện tại (đọc SQLite local -> chạy được cả trên
    PythonAnywhere free); người dùng đổi tuần/tháng thì JS gọi /api/market-trend.
    """
    today = datetime.now()
    default_week = iso_week_of(today)
    default_month = month_of(today)
    khoi = (request.args.get("khoi") or "foreign").strip().lower()
    if khoi not in ("foreign", "prop"):
        khoi = "foreign"

    db = get_db()
    week_data = month_data = None
    try:
        week_data = aggregate(db, "weekly", default_week, khoi=khoi, limit=10)
        month_data = aggregate(db, "monthly", default_month, khoi=khoi, limit=10)
    except InvalidPeriod:
        log.exception("market_flows: không dựng được kỳ mặc định")

    return render_template(
        "market_flows.html", khoi=khoi,
        default_week=default_week, default_month=default_month,
        week_data=week_data, month_data=month_data,
        flow_start=_flow_update_start(db),
    )


def _flow_update_start(db):
    """Ngày bắt đầu cho nút Update: từ ngày MỚI NHẤT đã có trong DB (nạp lại ngày
    đó để làm tươi số cuối phiên). DB trống -> lùi về đầu THÁNG TRƯỚC để một lần
    bấm lấp đủ tháng trước + tháng này. Trả chuỗi 'YYYY-MM-DD'."""
    today = datetime.now().date()
    row = db.execute("SELECT MAX(ngay) AS m FROM market_flows").fetchone()
    last = row["m"] if row and row["m"] else None
    if last:
        return str(last)[:10]
    first_this = today.replace(day=1)
    return (first_this - timedelta(days=1)).replace(day=1).isoformat()


@main_bp.route("/market-flows/ingest", methods=["POST"])
@login_required
def market_flows_ingest():
    """Nhận dữ liệu khối ngoại do TRÌNH DUYỆT fetch từ VNDirect rồi POST về (JSON
    {"rows": [...]}) và upsert vào DB.

    Vì sao client fetch rồi POST: PythonAnywhere free chặn server ra internet nhưng
    KHÔNG chặn trình duyệt. VNDirect (api-finfo) trả CORS '*' nên trình duyệt gọi
    trực tiếp được -> nút Update chạy cả trên PA free. Server chỉ việc lưu số nhận
    được (UPSERT theo (ma_cp, ngay), không tạo trùng)."""
    payload = request.get_json(silent=True) or {}
    rows = normalize_rows(payload.get("rows"))
    if not rows:
        return jsonify({"ok": False, "error": "No valid rows"}), 400
    db = get_db()
    try:
        n = upsert(db, rows)
        db.commit()
        return jsonify({"ok": True, "n": n})
    except Exception:  # noqa: BLE001 - không để lỗi ghi làm sập request
        db.rollback()
        log.exception("market_flows_ingest lỗi khi ghi dữ liệu")
        return jsonify({"ok": False, "error": "Server error while saving rows"}), 500


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
    # Tách 2 bảng để dễ soi mã lời/lỗ nhiều nhất và tránh render cả trăm dòng
    # khi danh mục phình to (nhiều năm giao dịch có thể lên tới hàng trăm mã).
    # by_pair đã sắp giảm dần theo pnl: đầu danh sách = lời nhất, cuối = lỗ nhất.
    by_pair_top = [x for x in by_pair if x["pnl"] > 0][:10]
    by_pair_bottom = [x for x in reversed(by_pair) if x["pnl"] < 0][:10]

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
        by_pair_top=by_pair_top, by_pair_bottom=by_pair_bottom,
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

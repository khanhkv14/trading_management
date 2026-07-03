# -*- coding: utf-8 -*-
"""Gộp các giao dịch khớp lệnh (mua/bán) thành VỊ THẾ.

Thuần Python, KHÔNG phụ thuộc Flask -> dễ kiểm thử bằng dict thường.

Quy tắc (mô hình chứng khoán, chỉ Long):
- Xét từng mã độc lập, theo thứ tự thời gian (ngay, id).
- Một vị thế MỞ khi khối lượng nắm giữ đi từ 0 lên dương (lệnh Mua đầu tiên khi
  đang không giữ cổ nào của mã đó).
- Mọi lệnh Mua tiếp theo khi vị thế còn mở được cộng dồn: cập nhật tổng khối lượng
  và giá mua trung bình bình quân gia quyền = tổng tiền mua / tổng số lượng mua.
- Lệnh Bán làm giảm khối lượng nắm giữ, tính lãi/lỗ thực hiện NGAY theo phần đã
  bán (giá bán - giá vốn bình quân hiện tại) * số lượng vừa bán. PnL/ROI thực
  hiện được ghi nhận ngay cả khi vị thế CÒN MỞ (mới bán một phần). Khi khối lượng
  về 0 -> vị thế ĐÓNG.
- Sau khi đóng, mua lại cùng mã = một vị thế MỚI, độc lập.
"""

EPS = 1e-9

# Thuế giao dịch: 0.1% trên GIÁ TRỊ của MỖI lệnh khớp (cả mua lẫn bán).
TAX_RATE = 0.001


def _r(x, n=2):
    """Làm tròn an toàn (giữ None)."""
    return round(x, n) if x is not None else None


def _get(row, key, default=None):
    """Đọc trường an toàn cho cả dict lẫn sqlite3.Row (thiếu cột -> default)."""
    try:
        v = row[key]
    except (KeyError, IndexError):
        return default
    return v if v is not None else default


def _new_pos(ma_cp, seq, ngay_mo):
    return {
        "ma_cp": ma_cp, "seq": seq,
        "ngay_mo": ngay_mo, "ngay_dong": None,
        "held_qty": 0.0, "held_cost": 0.0,   # số giữ + giá vốn (bình quân) đang giữ
        "tong_mua": 0.0, "buy_amt": 0.0,     # tổng đã mua + tổng tiền mua
        "tong_ban": 0.0, "sell_amt": 0.0,    # tổng đã bán + tổng tiền bán
        "realized": 0.0, "sold_cost": 0.0,   # lãi/lỗ đã thực hiện + vốn của phần đã bán
        "tx_ids": [],                        # id các giao dịch con
        "tax": 0.0,                          # tổng thuế mọi lệnh con (mua+bán) của vị thế
        "chien_luoc": "",                    # chiến lược của vị thế (lấy từ lệnh mở)
    }


def _finalize(cur, closed):
    tong_mua, tong_ban = cur["tong_mua"], cur["tong_ban"]
    gia_mua_tb = cur["buy_amt"] / tong_mua if tong_mua else None
    gia_ban_tb = cur["sell_amt"] / tong_ban if tong_ban else None

    # P/L và %ROI THỰC HIỆN được ghi nhận NGAY khi phát sinh lệnh bán (kể cả khi
    # vị thế còn mở vì mới bán một phần). None khi chưa bán gì.
    #   pnl      = tổng lãi/lỗ đã thực hiện của các phần đã bán
    #   von_ban  = giá vốn bình quân tại thời điểm bán * số lượng vừa bán (cộng dồn)
    #   roi      = pnl / von_ban * 100
    pnl = cur["realized"] if tong_ban else None
    von_ban = cur["sold_cost"] if tong_ban else None
    roi = pnl / von_ban * 100 if (pnl is not None and von_ban) else None

    # PnL THỰC TẾ SAU THUẾ = PnL gộp (thực hiện) - tổng thuế mọi lệnh con của vị
    # thế. Thuế 0.1%/lệnh là ước lượng chi phí tự động (không nhập phí thủ công).
    pnl_sau_thue = pnl - cur["tax"] if pnl is not None else None

    return {
        "ma_cp": cur["ma_cp"], "seq": cur["seq"],
        "ngay_mo": cur["ngay_mo"], "ngay_dong": cur["ngay_dong"],
        "trang_thai": "closed" if closed else "open",
        "so_luong_giu": _r(max(cur["held_qty"], 0.0), 4),
        "tong_mua": _r(tong_mua, 4), "tong_ban": _r(tong_ban, 4),
        "gia_mua_tb": _r(gia_mua_tb, 2), "gia_ban_tb": _r(gia_ban_tb, 2),
        "pnl": _r(pnl, 2), "roi": _r(roi, 2),
        "thue": _r(cur["tax"], 2), "pnl_sau_thue": _r(pnl_sau_thue, 2),
        "chien_luoc": cur["chien_luoc"],
        "so_lenh": len(cur["tx_ids"]), "tx_ids": cur["tx_ids"],
    }


def compute_positions(transactions):
    """Nhận danh sách giao dịch (dict/sqlite3.Row có: id, ngay, ma_cp, loai,
    so_luong, gia). `loai` nhận 'mua'/'ban'. Trả về danh sách vị thế đã gộp."""
    by_sym = {}
    for t in transactions:
        by_sym.setdefault(t["ma_cp"], []).append(t)

    result = []
    for ma_cp, items in by_sym.items():
        items = sorted(items, key=lambda t: (t["ngay"] or "", t["id"]))
        seq = 0
        cur = None
        for t in items:
            loai = (t["loai"] or "").lower()
            qty = float(t["so_luong"] or 0)
            price = float(t["gia"] or 0)

            if cur is None:
                if loai != "mua":
                    # Bán khi không nắm giữ -> dữ liệu không hợp lệ, bỏ qua.
                    continue
                seq += 1
                cur = _new_pos(ma_cp, seq, t["ngay"])

            cur["tx_ids"].append(t["id"])
            # Thuế 0.1% tính trên giá trị của chính lệnh này (áp dụng cả mua & bán).
            cur["tax"] += qty * price * TAX_RATE
            # Chiến lược của vị thế = chiến lược của lệnh MỞ; nếu lệnh mở bỏ trống
            # thì lấy chiến lược đầu tiên xuất hiện trong các lệnh con.
            if not cur["chien_luoc"]:
                cl = (_get(t, "chien_luoc", "") or "").strip()
                if cl:
                    cur["chien_luoc"] = cl

            if loai == "mua":
                cur["held_qty"] += qty
                cur["held_cost"] += qty * price
                cur["tong_mua"] += qty
                cur["buy_amt"] += qty * price
            else:  # ban
                if cur["held_qty"] > EPS:
                    avg = cur["held_cost"] / cur["held_qty"]
                    sold = min(qty, cur["held_qty"])
                    cur["realized"] += (price - avg) * sold
                    cur["sold_cost"] += avg * sold   # vốn của phần vừa bán (mẫu số ROI)
                    cur["held_cost"] -= avg * sold
                    cur["held_qty"] -= sold
                cur["tong_ban"] += qty
                cur["sell_amt"] += qty * price

            if cur["held_qty"] <= EPS:
                cur["held_qty"] = 0.0
                cur["ngay_dong"] = t["ngay"]
                result.append(_finalize(cur, closed=True))
                cur = None

        if cur is not None:                       # vị thế còn mở cuối chuỗi
            result.append(_finalize(cur, closed=False))

    return result

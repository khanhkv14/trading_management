# -*- coding: utf-8 -*-
"""Gộp các giao dịch khớp lệnh (mua/bán) thành VỊ THẾ.

Thuần Python, KHÔNG phụ thuộc Flask -> dễ kiểm thử bằng dict thường.

Quy tắc (mô hình chứng khoán, chỉ Long):
- Xét từng mã độc lập, theo thứ tự thời gian (ngay, id).
- Một vị thế MỞ khi khối lượng nắm giữ đi từ 0 lên dương (lệnh Mua đầu tiên khi
  đang không giữ cổ nào của mã đó).
- Mọi lệnh Mua tiếp theo khi vị thế còn mở được cộng dồn: cập nhật tổng khối lượng
  và giá mua trung bình bình quân gia quyền = tổng tiền mua / tổng số lượng mua.
- Lệnh Bán làm giảm khối lượng nắm giữ, tính lãi/lỗ thực hiện theo phần đã bán
  (giá bán - giá vốn bình quân hiện tại). Khi khối lượng về 0 -> vị thế ĐÓNG.
- Sau khi đóng, mua lại cùng mã = một vị thế MỚI, độc lập.
"""

EPS = 1e-9


def _r(x, n=2):
    """Làm tròn an toàn (giữ None)."""
    return round(x, n) if x is not None else None


def _new_pos(ma_cp, seq, ngay_mo):
    return {
        "ma_cp": ma_cp, "seq": seq,
        "ngay_mo": ngay_mo, "ngay_dong": None,
        "held_qty": 0.0, "held_cost": 0.0,   # số giữ + giá vốn (bình quân) đang giữ
        "tong_mua": 0.0, "buy_amt": 0.0,     # tổng đã mua + tổng tiền mua
        "tong_ban": 0.0, "sell_amt": 0.0,    # tổng đã bán + tổng tiền bán
        "realized": 0.0, "tx_ids": [],       # lãi/lỗ đã thực hiện + id giao dịch con
    }


def _finalize(cur, closed):
    tong_mua, tong_ban = cur["tong_mua"], cur["tong_ban"]
    gia_mua_tb = cur["buy_amt"] / tong_mua if tong_mua else None
    gia_ban_tb = cur["sell_amt"] / tong_ban if tong_ban else None
    pnl = cur["realized"] if tong_ban else None
    # Vốn của phần đã bán = giá mua TB * số lượng đã bán -> mẫu số của %ROI.
    von_ban = gia_mua_tb * tong_ban if (gia_mua_tb and tong_ban) else None
    roi = pnl / von_ban * 100 if (pnl is not None and von_ban) else None
    return {
        "ma_cp": cur["ma_cp"], "seq": cur["seq"],
        "ngay_mo": cur["ngay_mo"], "ngay_dong": cur["ngay_dong"],
        "trang_thai": "closed" if closed else "open",
        "so_luong_giu": _r(max(cur["held_qty"], 0.0), 4),
        "tong_mua": _r(tong_mua, 4), "tong_ban": _r(tong_ban, 4),
        "gia_mua_tb": _r(gia_mua_tb, 2), "gia_ban_tb": _r(gia_ban_tb, 2),
        "pnl": _r(pnl, 2), "roi": _r(roi, 2),
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

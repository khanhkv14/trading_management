# -*- coding: utf-8 -*-
"""Nhập vài lệnh mẫu để xem thử dashboard/gợi ý. Chạy: python scripts/seed_sample.py"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.models import get_db

SAMPLES = [
    ("2026-06-20 09:00", "Crypto", "BTCUSDT", "Long", 60000, 62000, 0.1, 59000, 63000, 200, 2, "breakout", "Tele A", "", "closed"),
    ("2026-06-21 10:00", "Crypto", "ETHUSDT", "Short", 3500, 3400, 1, 3600, 3300, 100, 1, "reversal", "Tele B", "", "closed"),
    ("2026-06-22 11:00", "Crypto", "BTCUSDT", "Long", 61000, 60500, 0.1, 60000, 63000, -50, 1, "breakout", "Tele A", "", "closed"),
    ("2026-06-23 12:00", "Crypto", "BTCUSDT", "Long", 60500, 62500, 0.1, 60000, 63000, 200, 2, "breakout", "Tele A", "", "closed"),
]

app = create_app()
with app.app_context():
    db = get_db()
    for s in SAMPLES:
        ket_qua = "Win" if s[9] > 0 else "Loss" if s[9] < 0 else "BE"
        db.execute("""INSERT INTO trades
          (ngay_gio,thi_truong,symbol,huong,gia_vao,gia_ra,khoi_luong,stoploss,
           takeprofit,pnl,phi,chien_luoc,nguon,ghi_chu,trang_thai,ket_qua)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", s + (ket_qua,))
    db.commit()
    print(f"Đã thêm {len(SAMPLES)} lệnh mẫu.")

# Quản lý Trading nội bộ

Ứng dụng web Flask quản lý lịch sử trading, tổng hợp thống kê và gợi ý dựa trên
dữ liệu của chính bạn. Chạy miễn phí trên PythonAnywhere.

## Cấu trúc

```
trading-app/
├── app/                  # Package chính
│   ├── __init__.py       # create_app() - application factory
│   ├── config.py         # Cấu hình, đọc bí mật từ .env
│   ├── models.py         # Lớp DB: kết nối, tạo bảng, migrate
│   ├── auth.py           # Blueprint đăng nhập
│   ├── main.py           # Blueprint chính (dashboard, lệnh, tín hiệu, gợi ý)
│   ├── templates/        # Giao diện HTML
│   └── static/           # CSS
├── database/             # Chứa trades.db (không đưa lên git)
├── scripts/              # Tiện ích, nhập liệu mẫu
├── auto_migrate.py       # Cập nhật cấu trúc bảng
├── run.py                # Điểm khởi chạy local
├── requirements.txt
├── .env.template         # Mẫu biến môi trường
└── .gitignore
```

## Chạy local

```bash
pip install -r requirements.txt
copy .env.template .env      # Windows (Linux/Mac: cp)
# sửa mật khẩu trong .env
python auto_migrate.py       # tạo bảng
python run.py                # mở http://127.0.0.1:5000
```

## Triển khai lên PythonAnywhere (tài khoản free)

1. Bash console: `git clone <repo-url> ~/trading-app`
2. `cd ~/trading-app && pip install --user -r requirements.txt`
3. Tạo file `.env` (copy từ `.env.template`) và điền mật khẩu thật.
4. `python auto_migrate.py`
5. Tab **Web** → sửa file WSGI, trỏ tới thư mục này và dùng:
   ```python
   import sys
   path = '/home/<user>/trading-app'
   if path not in sys.path:
       sys.path.insert(0, path)
   from app import create_app
   application = create_app()
   ```
6. Bấm **Reload**.

## Cập nhật về sau

Local: sửa code → `git add . && git commit -m "..." && git push`
PythonAnywhere: `cd ~/trading-app && git pull` → nếu đổi cấu trúc bảng thì
`python auto_migrate.py` → bấm **Reload**.

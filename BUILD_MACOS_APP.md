# Biến KCI Screener thành app riêng trên MacBook

## Antigravity đóng vai trò gì?

Antigravity là **AI IDE** (môi trường code, giống chỗ bạn đang build `kci_backend`). Nó giúp bạn:
- Mở folder `kci_screener/`, prompt agent để thêm/sửa tính năng.
- Chạy app ngay trong terminal tích hợp.

Nhưng Antigravity **không phải là cái app**. App của bạn là code Python chạy bằng môi trường Python trên Mac. Antigravity = nơi viết & chạy; "app" = thứ bạn đóng gói để bấm là mở. Hai việc tách biệt.

> Tóm lại: **Có, build trên Antigravity được** — copy cả folder `kci_screener/` vào workspace Antigravity (hoặc đặt cạnh `kci_backend/`), rồi làm theo dưới đây.

---

## 3 mức độ "app", chọn theo nhu cầu

| Mức | Cách | Trải nghiệm | Công sức |
|---|---|---|---|
| **1. Web app local (khuyên dùng)** | Streamlit (`app.py`) | Mở trình duyệt, có bảng/filter/nút tải Excel | Thấp — đã xong |
| **2. Double-click** | `run_app.command` | Bấm đúp ở Finder → tự mở app | Thấp |
| **3. Native `.app`** | Đóng gói bằng `py2app`/PyInstaller | Icon trong Applications, như app thật | Trung bình |

App này là công cụ bảng dữ liệu → **Streamlit (mức 1-2) là điểm ngọt nhất**. Native `.app` chỉ cần nếu muốn icon trong Launchpad.

---

## Mức 1 — Chạy ngay (Streamlit)

Trong terminal (hoặc terminal của Antigravity):

```bash
cd kci_screener
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # điền Toss Client ID/Secret (nếu dùng Toss)
streamlit run app.py
```

Trình duyệt tự mở `http://localhost:8501`. Nhập tên/ticker → bấm **Cào dữ liệu** → xem bảng + P/E ngành → tải Excel.

## Mức 2 — Double-click trên Finder

Đã có sẵn `run_app.command`. Một lần cấp quyền chạy:

```bash
chmod +x run_app.command
```

Sau đó ở Finder **bấm đúp `run_app.command`** (lần đầu: chuột phải → Open để bỏ cảnh báo Gatekeeper). Nó tự tạo venv, cài thư viện, mở app. Có thể kéo file này ra Desktop làm shortcut.

## Mức 3 — Đóng gói thành `.app` thật

Cách gọn nhất giữ nguyên Streamlit, bọc bằng một `.app` mở terminal chạy lệnh trên — hoặc dùng **py2app** cho GUI thuần. Nếu muốn `.app` có icon:

1. Cài: `pip install py2app`
2. Hoặc đơn giản hơn: dùng **Automator** → "Application" → Run Shell Script → dán nội dung `run_app.command` → Save thành `KCI Screener.app` → kéo vào Applications. Đây là cách nhanh nhất cho một app nội bộ.

> Lưu ý: app Streamlit về bản chất vẫn cần Python + mạng (để gọi Toss/KRX). py2app/PyInstaller gói được Python nhưng vẫn cần internet khi chạy vì dữ liệu là realtime.

---

## Gợi ý prompt cho Antigravity (nếu muốn agent tự build tiếp)

> "Mở folder kci_screener. Đọc README.md và screener.py. Chạy `streamlit run app.py` để kiểm tra. Sau đó thêm tính năng: lưu lịch sử mỗi lần screen vào SQLite, và thêm trang so sánh 2 ngành cạnh nhau. Giữ nguyên kiến trúc đa nguồn (toss/pykrx/naver) và nguyên tắc không hardcode secret."

---

## Kiến trúc app (để mở rộng sau)

```
kci_screener/
├── app.py            # UI Streamlit  ← giao diện
├── screener.py       # logic cào + resolve + aggregate  ← lõi
├── toss_api.py       # adapter Toss Open API
├── tickers.csv       # registry name↔ticker↔sector
├── .env              # secret (KHÔNG commit)
├── requirements.txt
└── run_app.command   # launcher double-click
```

Khi nối với module định giá (Master Spec): app này lo **data layer**; engine định giá L4 lo **valuation layer**. Có thể thêm một tab "Định giá" gọi engine cho từng mã đã screen.

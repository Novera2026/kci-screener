#!/bin/bash
# Double-click file này trên macOS để mở app KCI Screener.
# Lần đầu: chuột phải -> Open (để bỏ qua cảnh báo Gatekeeper).
cd "$(dirname "$0")"

# tạo venv lần đầu nếu chưa có
if [ ! -d ".venv" ]; then
  echo "Lần đầu chạy — đang cài đặt môi trường..."
  python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt
fi

echo "Đang mở KCI Screener tại http://localhost:8501 ..."
./.venv/bin/streamlit run app.py

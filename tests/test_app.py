"""Kiểm tra app chạy THẬT (AppTest thực thi toàn bộ script, khác curl chỉ tải vỏ trang).

- test_no_undefined_names / test_app_boots: chạy mặc định (không cần mạng ổn định).
- test_full_screen_run: cào dữ liệu thật → bật bằng  KCI_E2E=1 pytest
"""
import glob
import os
import subprocess
import sys

import pytest
from streamlit.testing.v1 import AppTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app.py")
# toàn bộ module + các trang (pages/) — trang phụ cũng chạy trên cloud
MODULES = sorted(os.path.relpath(p, ROOT) for p in
                 glob.glob(os.path.join(ROOT, "*.py")) + glob.glob(os.path.join(ROOT, "pages", "*.py")))
PAGES = sorted(glob.glob(os.path.join(ROOT, "pages", "*.py")))


def test_no_undefined_names():
    """Bắt lỗi kiểu NameError (gọi hàm/biến chưa định nghĩa) bằng phân tích tĩnh."""
    out = subprocess.run([sys.executable, "-m", "pyflakes", *MODULES], cwd=ROOT,
                         capture_output=True, text=True).stdout
    bad = [ln for ln in out.splitlines() if "undefined name" in ln]
    assert not bad, "\n".join(bad)


def test_app_boots_without_exception():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception, at.exception


def test_super_stock_page_boots():
    """Trang 🚀 chỉ đọc CSV lúc mở (chỉ gọi mạng khi bấm Tìm)."""
    page = next(p for p in PAGES if "Sieu_co_phieu" in p)
    at = AppTest.from_file(page, default_timeout=120).run()
    assert not at.exception, at.exception


def test_valuation_pipeline_page_boots():
    """Trang 🧮 chỉ gọi mạng sau khi nhập mã (Rf FRED lỗi thì fallback 3,2%)."""
    page = next(p for p in PAGES if "Dinh_gia" in p)
    at = AppTest.from_file(page, default_timeout=120).run()
    assert not at.exception, at.exception


@pytest.mark.skipif(os.environ.get("KCI_E2E") != "1",
                    reason="Trang 📰 tải tin/chủ đề từ Naver ngay khi mở: chạy  KCI_E2E=1 pytest")
def test_hot_news_page_boots():
    page = next(p for p in PAGES if "Tin_nong" in p)
    at = AppTest.from_file(page, default_timeout=180).run()
    assert not at.exception, at.exception


@pytest.mark.skipif(os.environ.get("KCI_E2E") != "1",
                    reason="E2E cần mạng (Naver/DART/FRED): chạy  KCI_E2E=1 pytest")
def test_full_screen_run():
    at = AppTest.from_file(APP, default_timeout=300).run()
    at.text_area(key="raw_input").set_value("005930\n000660\n105560")
    at = next(b for b in at.button if "Cào" in b.label).click().run()
    assert not at.exception, at.exception
    frames = [d.value for d in at.dataframe]
    assert any("Fair value" in getattr(f, "columns", []) for f in frames)
    vt = next(f for f in frames if "Trạng thái" in getattr(f, "columns", []))   # cổng tin cậy 06/11/2026
    assert all(s[0] in "✓○△⚪—" for s in vt["Trạng thái"])
    # fair công bố luôn nằm trong vùng 0,4–2,5× giá (mã ngoài vùng → "—")
    pub = vt.dropna(subset=["Fair value"])
    assert all(0.4 <= fv / px <= 2.5 for fv, px in zip(pub["Fair value"], pub["Giá"]))
    assert any("Giá realtime" in c.value for c in at.caption)       # có giờ lấy giá
    assert any("EPS đồng thuận" in getattr(f, "columns", []) for f in frames)
    cb = [c for c in at.checkbox if "EV/EBIT" in c.label]
    if cb:
        at = cb[0].check().run()
        assert not at.exception, at.exception


@pytest.mark.skipif(os.environ.get("KCI_E2E") != "1",
                    reason="Backtest gọi DART/Naver/FRED thật: chạy  KCI_E2E=1 pytest")
def test_backtest_dart_runs_in_app():
    at = AppTest.from_file(APP, default_timeout=300).run()
    at.text_area(key="bt_universe").set_value("005930\nSK Hynix\n005380")
    at.slider(key="bt_years").set_value((2023, 2024))
    at = at.button(key="bt_run_dart").click().run()
    assert not at.exception, at.exception
    assert any("Điểm có lợi suất" in m.label for m in at.metric)
    at = at.slider(key="bt_h_dart").set_value(6).run()         # kết quả còn nguyên sau rerun
    assert any("Điểm có lợi suất" in m.label for m in at.metric)

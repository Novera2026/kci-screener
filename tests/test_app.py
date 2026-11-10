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
APP = os.path.join(ROOT, "app.py")                    # bộ điều hướng st.navigation
VIEW = os.path.join(ROOT, "views", "tong_hop.py")     # trang "Định giá tổng hợp" (trước 11/11/2026 nằm ở app.py)
# toàn bộ module + các trang (pages/) — trang phụ cũng chạy trên cloud
MODULES = sorted(os.path.relpath(p, ROOT) for p in
                 glob.glob(os.path.join(ROOT, "*.py")) + glob.glob(os.path.join(ROOT, "pages", "*.py"))
                 + glob.glob(os.path.join(ROOT, "views", "*.py")))
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
    at = AppTest.from_file(VIEW, default_timeout=300).run()
    at.text_area(key="raw_input").set_value("005930\n000660\n105560")
    at = next(b for b in at.button if "Cào" in b.label).click().run()
    assert not at.exception, at.exception
    frames = [d.value for d in at.dataframe]
    # con số chính = kết luận của quy trình định giá (trang 🧮); lõi KCI chỉ là cột tham khảo (11/11/2026)
    vt = next(f for f in frames if "Giá trị hợp lý" in getattr(f, "columns", []))
    assert "Lõi KCI (tham khảo)" in vt.columns
    assert all(str(s)[0] in "✓○△⚪—" for s in vt["Trạng thái"])
    # giá trị công bố luôn nằm trong vùng 0,4–2,5× giá
    pub = vt.dropna(subset=["Giá trị hợp lý"])
    assert len(pub) > 0
    assert all(0.4 <= fv / px <= 2.5 for fv, px in zip(pub["Giá trị hợp lý"], pub["Giá"]))
    assert any("Giá realtime" in c.value for c in at.caption)       # có giờ lấy giá
    assert any("EPS đồng thuận" in getattr(f, "columns", []) for f in frames)
    cb = [c for c in at.checkbox if "EV/EBIT" in c.label]
    if cb:
        at = cb[0].check().run()
        assert not at.exception, at.exception


@pytest.mark.skipif(os.environ.get("KCI_E2E") != "1",
                    reason="Backtest gọi DART/Naver/FRED thật: chạy  KCI_E2E=1 pytest")
def test_backtest_dart_runs_in_app():
    at = AppTest.from_file(VIEW, default_timeout=300).run()
    at.text_area(key="bt_universe").set_value("005930\nSK Hynix\n005380")
    at.slider(key="bt_years").set_value((2023, 2024))
    at = at.button(key="bt_run_dart").click().run()
    assert not at.exception, at.exception
    assert any("Điểm có lợi suất" in m.label for m in at.metric)
    at = at.slider(key="bt_h_dart").set_value(6).run()         # kết quả còn nguyên sau rerun
    assert any("Điểm có lợi suất" in m.label for m in at.metric)


def test_screening_page_boots():
    """Trang 🔍 chỉ gọi mạng khi bấm Lọc rẻ."""
    page = next(p for p in PAGES if "Sang_loc" in p)
    at = AppTest.from_file(page, default_timeout=120).run()
    assert not at.exception, at.exception


def test_navigation_names_and_order():
    """Thanh bên: tên tiếng Việt, Sàng lọc đứng trước Định giá, URL /Dinh_gia giữ nguyên."""
    import ast
    tree = ast.parse(open(APP, encoding="utf-8").read())
    pages = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "Page"]
    titles = [next(k.value.value for k in p.keywords if k.arg == "title") for p in pages]
    assert titles == ["Trang chủ", "Định giá tổng hợp", "Siêu cổ phiếu", "Tin nóng", "Sàng lọc", "Định giá"]
    urls = {next(k.value.value for k in p.keywords if k.arg == "title"):
            next((k.value.value for k in p.keywords if k.arg == "url_path"), "") for p in pages}
    assert urls["Định giá"] == "Dinh_gia" and urls["Sàng lọc"] == "Sang_loc"
    for p in pages:                                     # mọi trang khai báo đều tồn tại
        assert os.path.exists(os.path.join(ROOT, p.args[0].value))


def test_home_page_boots():
    """Trang chủ chạy thật (Naver realtime): có chỉ số, bảng mega trend, mã dẫn dắt."""
    at = AppTest.from_file(APP, default_timeout=180).run()     # trang mặc định = Trang chủ
    assert not at.exception, at.exception
    frames = [d.value for d in at.dataframe]
    if not frames:                       # mất mạng → trang báo lỗi rõ ràng, không văng
        assert at.error
        return
    mt = next(f for f in frames if "Xu hướng" in f.columns)
    assert len(mt) == 11


def test_tong_hop_view_boots():
    at = AppTest.from_file(VIEW, default_timeout=120).run()
    assert not at.exception, at.exception


def test_reload_guard_drops_stale_project_modules(tmp_path):
    """Module dự án đổi trên đĩa → bỏ CẢ BỘ khỏi sys.modules (lần import sau đọc mã mới), không bỏ lặp lại."""
    import importlib
    import time as _t
    import reload_guard
    (tmp_path / "kci_mod_a.py").write_text("X = 1\n")
    (tmp_path / "kci_mod_b.py").write_text("Y = 1\n")
    sys.path.insert(0, str(tmp_path))
    reg = sys.__dict__.setdefault(reload_guard._REG, {})
    try:
        importlib.import_module("kci_mod_a"); importlib.import_module("kci_mod_b")
        far_future = _t.time() + 3600                                 # tiến trình "khởi động" sau khi sửa file
        assert reload_guard.drop_stale(str(tmp_path), proc_start=far_future) == []      # ghi mốc
        assert reload_guard.drop_stale(str(tmp_path), proc_start=far_future) == []      # không đổi → giữ
        os.utime(tmp_path / "kci_mod_a.py", (_t.time() + 5, _t.time() + 5))
        (tmp_path / "kci_mod_a.py").write_text("X = 2\n")
        os.utime(tmp_path / "kci_mod_a.py", (_t.time() + 5, _t.time() + 5))
        assert reload_guard.drop_stale(str(tmp_path), proc_start=far_future) == ["kci_mod_a", "kci_mod_b"]
        assert importlib.import_module("kci_mod_a").X == 2
        importlib.import_module("kci_mod_b")
        assert reload_guard.drop_stale(str(tmp_path), proc_start=far_future) == []      # không bỏ lặp lại
    finally:
        sys.path.remove(str(tmp_path))
        for n in ("kci_mod_a", "kci_mod_b"):
            sys.modules.pop(n, None)
            reg.pop(n, None)


def test_reload_guard_first_sight_after_process_start(tmp_path):
    """Tình huống Cloud: module nạp TRƯỚC deploy, file sửa SAU khi tiến trình khởi động → bỏ ngay lần đầu thấy."""
    import importlib
    import reload_guard
    (tmp_path / "kci_mod_c.py").write_text("Z = 1\n")
    sys.path.insert(0, str(tmp_path))
    reg = sys.__dict__.setdefault(reload_guard._REG, {})
    try:
        importlib.import_module("kci_mod_c")
        assert reload_guard.drop_stale(str(tmp_path), proc_start=1.0) == ["kci_mod_c"]
        importlib.import_module("kci_mod_c")
        assert reload_guard.drop_stale(str(tmp_path), proc_start=1.0) == []             # đã có mốc → giữ
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("kci_mod_c", None)
        reg.pop("kci_mod_c", None)

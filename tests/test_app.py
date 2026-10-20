"""Kiểm tra app chạy THẬT (AppTest thực thi toàn bộ script, khác curl chỉ tải vỏ trang).

- test_no_undefined_names / test_app_boots: chạy mặc định (không cần mạng ổn định).
- test_full_screen_run: cào dữ liệu thật → bật bằng  KCI_E2E=1 pytest
"""
import os
import subprocess
import sys

import pytest
from streamlit.testing.v1 import AppTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app.py")
MODULES = ["app.py", "screener.py", "financials.py", "valuation.py", "dart_api.py"]


def test_no_undefined_names():
    """Bắt lỗi kiểu NameError (gọi hàm/biến chưa định nghĩa) bằng phân tích tĩnh."""
    out = subprocess.run([sys.executable, "-m", "pyflakes", *MODULES], cwd=ROOT,
                         capture_output=True, text=True).stdout
    bad = [ln for ln in out.splitlines() if "undefined name" in ln]
    assert not bad, "\n".join(bad)


def test_app_boots_without_exception():
    at = AppTest.from_file(APP, default_timeout=120).run()
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
    cb = [c for c in at.checkbox if "EV/EBIT" in c.label]
    if cb:
        at = cb[0].check().run()
        assert not at.exception, at.exception

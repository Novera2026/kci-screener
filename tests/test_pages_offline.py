"""Luồng chính các trang chạy KÍN trong CI (rà soát 18/11/2026, I42).

Trước đây test CI chỉ mở trang ở trạng thái trống: 🧮 dừng ở st.stop, phần kết quả của Định giá tổng hợp và bot ở
Trang chủ chỉ có trong test E2E (luôn skip) → chèn raise vào đó suite vẫn xanh. Ở đây trang chạy THẬT trên mạng giả
tầng HTTP (tests/g9b_fakenet.py, phản hồi Naver ghi ngày 18/11/2026), đồng hồ đặt 18/11/2026 15:40 KST.
Chỉ dùng nhãn / khóa widget ổn định (vp_query_box, vp_step, vp_sub, raw_input, hb_*) để không vỡ khi trang đổi bố cục.
Tin nóng offline: tests/test_app.py::test_hot_news_page_boots_offline (I41).
"""
from __future__ import annotations

import os
import re

import pytest
from streamlit.testing.v1 import AppTest

import g9b_fakenet as FN

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = os.path.join(ROOT, "pages")
VAL_PAGE = next(os.path.join(PAGES, f) for f in sorted(os.listdir(PAGES)) if "Dinh_gia" in f)
VIEW = os.path.join(ROOT, "views", "tong_hop.py")
APP = os.path.join(ROOT, "app.py")
DART_HINT = "DART_API_KEY"            # thiếu khóa DART là bình thường trong CI (ô EV/EBIT, backtest báo đúng như vậy)


@pytest.fixture
def net(monkeypatch, tmp_path, fake_market):
    import bot_hunt as BH
    n = FN.offline_pages(monkeypatch, tmp_path)
    monkeypatch.setattr(BH, "STATE_PATH", str(tmp_path / "bot_state.json"))
    return n


def _ok(at, allow_dart_hint=False):
    assert not at.exception, [e.value for e in at.exception]
    errs = [e.value for e in at.error if not (allow_dart_hint and DART_HINT in e.value)]
    assert not errs, errs
    return at


def _num(s) -> float:
    return float(re.sub(r"[^\d.]", "", str(s)))


def _domestic_misses(net):
    return [m for m in net.misses if not m.startswith("api.stock.naver.com/stock/")]   # mã nước ngoài: Naver không có


def open_valuation(tk="005930"):
    at = AppTest.from_file(VAL_PAGE, default_timeout=120).run()
    at.text_input(key="vp_query_box").set_value(tk)
    return next(b for b in at.button if "Bắt đầu" in b.label).click().run()


# ---------------------------------------------------------------------------- 🧮 đủ 5 bước
@pytest.mark.parametrize("tk", ["005930", "105560"])
def test_valuation_page_full_flow_offline(net, tk):
    at = _ok(open_valuation(tk))
    assert not _domestic_misses(net), _domestic_misses(net)
    side = {m.label: m.value for m in at.sidebar.metric}
    assert _num(side["Giá"]) > 0 and side["Giá trị hợp lý"] not in ("—", "") and _num(side["Giá trị hợp lý"]) > 0
    names = [str(x) for d in at.dataframe for col in ("Phương pháp", "Mô hình") if col in getattr(d.value, "columns", [])
             for x in d.value[col]]
    assert any("RIM" in n or "DDM" in n for n in names), names[:20]
    labels = [b.label for b in at.get("download_button")]
    assert any(".md" in x for x in labels) and any(".xlsx" in x for x in labels)
    assert any(".pdf" in x or ".html" in x for x in labels) and len(labels) >= 3
    # đi lần lượt mọi bước (và mọi phần của bước ④): không exception, không st.error
    steps = at.radio(key="vp_step").options
    assert len(steps) == 5
    for s in steps:
        at = _ok(at.radio(key="vp_step").set_value(s).run())
    for s in at.radio(key="vp_sub").options:
        at = _ok(at.radio(key="vp_sub").set_value(s).run())


def test_valuation_page_holdco_and_reit_offline(net):
    for tk in ("003550", "330590"):
        at = _ok(open_valuation(tk))
        assert not _domestic_misses(net), (tk, _domestic_misses(net))   # không lặng lẽ rơi về dữ liệu 404
        side = {m.label: m.value for m in at.sidebar.metric}
        assert {"Giá", "Giá trị hợp lý"} <= set(side), (tk, side)
        assert _num(side["Giá"]) > 0, (tk, side)
        fv = str(side["Giá trị hợp lý"])
        if fv in ("—", ""):                                    # bị cổng chặn: phải có lý do hiện trên trang
            assert at.warning or at.info, (tk, "cổng chặn mà không có lý do")
        else:
            assert _num(fv) > 0, (tk, side)


# ---------------------------------------------------------------------------- Định giá tổng hợp
def test_tong_hop_scrape_and_value_offline(net):
    at = AppTest.from_file(VIEW, default_timeout=120).run()
    at.text_area(key="raw_input").set_value("005930\n000660\n105560")
    at = _ok(next(b for b in at.button if "Cào" in b.label).click().run(), allow_dart_hint=True)
    assert not _domestic_misses(net), _domestic_misses(net)
    frames = [d.value for d in at.dataframe]
    res = next(f for f in frames if {"Mã", "Giá trị hợp lý", "Độ tin cậy"} <= set(getattr(f, "columns", [])))
    assert list(res["Mã"]) == ["005930", "000660", "105560"] or sorted(res["Mã"]) == ["000660", "005930", "105560"]
    assert res["Giá"].notna().all() and (res["Giá"] > 0).all()
    assert res["Giá trị hợp lý"].notna().any()
    assert any("Phương pháp" in getattr(f, "columns", []) for f in frames)        # chi tiết 1 mã
    # chạy lại (kéo slider, đổi ô…) không văng
    _ok(at.run(), allow_dart_hint=True)


# ---------------------------------------------------------------------------- Trang chủ: bot săn
def test_home_bot_hunt_offline(net):
    at = _ok(AppTest.from_file(APP, default_timeout=180).run())
    at = at.select_slider(key="hb_n").set_value(50).run()
    at = at.slider(key="hb_deep").set_value(3).run()
    at = _ok(at.button(key="hb_run").click().run())
    assert any("Kết quả săn lúc" in c.value for c in at.caption)
    frames = [d.value for d in at.dataframe if "Bộ tiêu chuẩn" in getattr(d.value, "columns", [])]
    assert frames, "không có bảng kết quả bot"
    for f in frames:
        assert {"Mã", "Sàn", "Ngành", "Giá", "Giá trị hợp lý", "Kết luận"} <= set(f.columns)
        assert len(f) >= 1
    assert not _domestic_misses(net), _domestic_misses(net)

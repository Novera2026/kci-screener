"""Ngân sách hiệu năng (rà soát 18/11/2026, cải tiến C3): đếm lượt HTTP/DART và lượt dựng PDF mỗi lần chạy lại.

Gọi mạng khi chạy lại trang, hỏi lại DART kỳ 'không có báo cáo' (013), dựng PDF mỗi lần là lỗi LẶNG — số vẫn đúng nên
test giá trị không bắt được. Ở đây mọi lượt HTTP đi qua mạng giả tests/g9b_fakenet.py (lọt ra ngoài thì conftest chặn
socket) và được đếm. Khóa các bản sửa I10, I32, I39, I34.
"""
from __future__ import annotations

import datetime as dt
import os

import pytest
from streamlit.testing.v1 import AppTest

import dart_api as D
import g9b_fakenet as FN
import guard as GD
import reporting as R

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = os.path.join(ROOT, "pages")
VAL_PAGE = next(os.path.join(PAGES, f) for f in sorted(os.listdir(PAGES)) if "Dinh_gia" in f)
SUPER = next(os.path.join(PAGES, f) for f in sorted(os.listdir(PAGES)) if "Sieu_co_phieu" in f)
VIEW = os.path.join(ROOT, "views", "tong_hop.py")
APP = os.path.join(ROOT, "app.py")


@pytest.fixture
def net(monkeypatch, tmp_path, fake_market):
    import bot_hunt as BH
    n = FN.offline_pages(monkeypatch, tmp_path)
    monkeypatch.setattr(BH, "STATE_PATH", str(tmp_path / "bot_state.json"))
    return n


def _ok(at):
    assert not at.exception, [e.value for e in at.exception]
    return at


def _rerun_calls(net, at, n=2):
    """Chạy lại trang n lần → số lượt HTTP mới."""
    k = len(net.calls)
    for _ in range(n):
        at = _ok(at.run())
    return len(net.calls) - k, net.calls[k:k + 5]


# ---------------------------------------------------------------------------- trang
def test_valuation_rerun_no_network_no_pdf(net, monkeypatch):
    import report_pdf as RPDF
    built = []
    real = RPDF.report_pdf
    monkeypatch.setattr(RPDF, "report_pdf", lambda *a, **k: built.append(1) or real(*a, **k))
    at = AppTest.from_file(VAL_PAGE, default_timeout=120).run()
    at.text_input(key="vp_query_box").set_value("005930")
    at = _ok(next(b for b in at.button if "Bắt đầu" in b.label).click().run())
    assert len(net.calls) > 0
    n, sample = _rerun_calls(net, at)
    assert n == 0, sample
    k = len(net.calls)
    for s in at.radio(key="vp_step").options[-2:]:            # đổi bước = chạy lại trang
        at = _ok(at.radio(key="vp_step").set_value(s).run())
    assert len(net.calls) == k
    assert built == []                                         # PDF chỉ dựng khi bấm tải (I39)


def test_tong_hop_rerun_no_network(net):
    at = AppTest.from_file(VIEW, default_timeout=120).run()
    at.text_area(key="raw_input").set_value("005930\n000660\n105560")
    at = _ok(next(b for b in at.button if "Cào" in b.label).click().run())
    n, sample = _rerun_calls(net, at)
    assert n == 0, sample                                      # I10: Chi tiết 1 mã / EV-EBIT không gọi lại


def test_home_and_bot_rerun_no_network(net):
    at = _ok(AppTest.from_file(APP, default_timeout=180).run())
    n, sample = _rerun_calls(net, at)
    assert n == 0, sample
    at = at.select_slider(key="hb_n").set_value(50).run()
    at = at.slider(key="hb_deep").set_value(3).run()
    at = _ok(at.button(key="hb_run").click().run())
    assert len(net.calls) > 0
    n, sample = _rerun_calls(net, at)
    assert n == 0, sample                                      # kết quả bot giữ trong phiên, không săn lại


def test_super_page_guest_rerun_no_network(net, monkeypatch):
    monkeypatch.setenv("KCI_PUBLIC", "1")                     # khách trên bản công khai
    at = _ok(AppTest.from_file(SUPER, default_timeout=180).run())
    at = _ok(next(b for b in at.button if "Tìm siêu" in b.label).click().run())
    assert len(net.calls) > 0
    n, sample = _rerun_calls(net, at)
    assert n == 0, sample
    # Không khóa I02 ở đây: dữ liệu giả chỉ có nến tuần nên không mã nào ra bảng, nhánh Toss không chạy tới.
    # I02 do tests/test_g5_owner_lock_scan.py khóa (bẫy TossClient). Đây chỉ là kiểm phụ: không lọt lượt Toss nào.
    assert net.count("tossinvest") == 0


# ---------------------------------------------------------------------------- DART
@pytest.fixture
def dart(monkeypatch, tmp_path):
    net = FN.install(monkeypatch)
    FN.freeze(monkeypatch, dt.date(2026, 11, 18))
    FN.dart_on(monkeypatch, tmp_path)
    return net


def _twice(net, fn):
    fn()
    first = net.count("opendart")
    again = fn()
    return first, net.count("opendart") - first, again


def test_dart_second_call_is_free(dart):
    """annual_financials / fetch_quarter_reports / valuation_extras / quarterly_financials gọi lần hai: 0 lượt DART —
    kể cả kỳ DART trả 013 'không có báo cáo' (I32). Không hỏi kỳ chưa kết thúc."""
    import analysis as AN
    for name, fn in [("annual", lambda: D.annual_financials("005930", years=4)),
                     ("annual_bank", lambda: D.annual_financials("105560", years=4)),
                     ("quarters", lambda: AN.fetch_quarter_reports("462870")),
                     ("extras", lambda: D.valuation_extras("105560")),
                     ("dart_q", lambda: D.quarterly_financials("462870"))]:
        k = dart.count("opendart")
        first, second, _ = _twice(dart, fn)
        assert first > k, name                                  # lần đầu có hỏi DART (kể cả nhận 013)
        assert second == 0, (name, first, [c for c in dart.dart][-3:])
    k = dart.count("opendart")
    assert D._fetch_year("00126380", 2026) is None             # năm 2026 chưa kết thúc → không hỏi DART
    assert dart.count("opendart") == k
    today = GD.today_kst()
    asked = {(int(c["bsns_year"]), c["reprt_code"]) for c in dart.dart if "bsns_year" in c}
    assert asked and not [a for a in asked if R.state(a[0], a[1], today) == "not_ended"]


def test_dart_disclosures_reports_truncation(dart, monkeypatch):
    """Tin nóng tab DART: trần trang bị chạm → meta['truncated'] = True (I34); mỗi trang là 1 lượt DART có đếm."""
    import hotnews as H
    route = dart.route

    def route2(m, u, p):
        if u.endswith("/list.json"):
            route(m, u, p)
            return 200, {"status": "000", "total_count": 5000, "total_page": 50, "page_no": p.get("page_no"),
                         "list": [{"corp_cls": "Y", "stock_code": "005930", "corp_name": "삼성전자",
                                   "report_nm": "단일판매ㆍ공급계약체결", "rcept_no": f"2026111800{p.get('page_no')}"
                                   f"{p.get('pblntf_ty')}", "rcept_dt": "20261118"}]}
        return route(m, u, p)
    monkeypatch.setattr(dart, "route", route2)
    rows, err, meta = H.dart_disclosures(days=1, max_pages=3)
    assert err is None and meta["truncated"] and meta["total"] >= 5000
    assert dart.count("opendart") == 3 and rows

"""Rà soát cuối 18/11/2026 — các lỗi đã xác minh của bộ thay đổi (Holdco tự xác nhận phép neo, Naver lỗi bị nhớ /
bị hiểu là 'không có', lỗi DART bị nuốt ở I14, kết quả rỗng bị cache ở Trang chủ / Tin nóng). Không mạng."""
from __future__ import annotations

import ast
import os

import pandas as pd
import pytest
import streamlit as st

import analysis as AN
import coverage
import financials as F
import hotnews as H
import pipeline as P
import valuation as V

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A = V.Assumptions(rf=0.03, erp=0.05, g_terminal=0.02)
TODAY = pd.Timestamp("2026-11-18").date()
ERR_FIN = {"annual": [], "quarter": [], "_error": {"annual": "ConnectionError", "quarter": "ConnectionError"}}


def _page(name: str) -> str:
    d = os.path.join(ROOT, "pages")
    return os.path.join(d, next(f for f in sorted(os.listdir(d)) if name in f))


def _defs(path: str, *names: str, ns: dict) -> dict:
    """Lấy nguyên văn các hàm / lớp cấp module của một trang (không chạy cả trang)."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            exec(compile(ast.Module([node], []), path, "exec"), ns)
    return ns


# ---------------------------------------------------------------------------- Holdco: lõi tự xác nhận phép neo
GS = {"core": 197432, "rim": 377313, "ddm": 703670, "pb": 120357, "price": 82300}


def _gs_cands(in_anchor):
    return [{"name": "Lõi KCI — P/E vs median ngành", "value": GS["core"], "role": "primary", "in_anchor": in_anchor},
            {"name": "RIM", "value": GS["rim"], "role": "primary"},
            {"name": "DDM", "value": GS["ddm"], "role": "cross"},
            {"name": "P/E × trung vị peer", "value": GS["core"], "role": "cross"},
            {"name": "P/B × trung vị peer", "value": GS["pb"], "role": "cross"}]


def test_holdco_core_inside_anchor_does_not_confirm_itself():
    methods = {"P/E vs median ngành": GS["core"], "P/B vs median ngành": GS["pb"],
               "Forward P/E vs median ngành": 300909}
    anchor = P.peer_anchor(methods, False)
    assert anchor == GS["core"]                                          # lõi chính là trung vị phép neo
    rec = P.reconcile(GS["price"], _gs_cands(True), anchor, [None, None])
    assert rec["fair"] == GS["core"] and rec["confidence"] == "Low"     # 1 mô hình chính, không có xác nhận độc lập
    # cùng số nhưng lõi KHÔNG nằm trong phép neo → phép neo là xác nhận độc lập (giữ hành vi cũ)
    assert P.reconcile(GS["price"], _gs_cands(False), anchor, [None, None])["confidence"] == "Medium"


def test_holdco_core_candidate_is_marked_in_anchor():
    from tests.test_d4_peer_groups import _series
    s = _series()
    last = s[-1]
    price = last["bps"] * 1.2
    row = {"ticker": "009970", "name": "영원무역홀딩스", "price": price, "market_cap": price * 1e7, "eps": last["eps"],
           "bps": last["bps"], "dps": last["dps"], "industry_kr": "섬유,의류,신발,호화품", "sector": None}
    peers = [{"ticker": f"9{i:05d}", "per": 9.0 + (i % 3), "pbr": 0.9 + 0.05 * (i % 3), "fetch_ok": True}
             for i in range(12)]
    ctx = P.assess("009970", row, {}, A, peers, 1.0, series=s, today=TODAY)
    assert ctx["idn"]["bucket"] == "Holdco"
    core = next(r for r in ctx["rec"]["rows"] if r["name"].startswith("Lõi KCI"))
    assert core["in_anchor"] == (ctx["E"]["base"].auto_method in V._RELATIVE_METHODS)
    prim = [r for r in ctx["rec"]["rows"] if r["role"] == "primary" and r["pass"]]
    if len(prim) == 1 and prim[0].get("in_anchor"):
        assert ctx["rec"]["confidence"] == "Low"


# ---------------------------------------------------------------------------- Naver lỗi: không nhớ, không 'mới niêm yết'
def test_fin_complete():
    assert not F.fin_complete(ERR_FIN) and bool(ERR_FIN)                # dict lỗi vẫn truthy
    assert not F.fin_complete({"annual": [], "quarter": []}) and not F.fin_complete(None)
    assert F.fin_complete({"annual": [{"period": "202412"}], "quarter": []})


def test_super_screen_does_not_store_naver_error():
    store: dict = {}
    ns = _defs(_page("Sieu_co_phieu"), "_cached", ns={"store": store, "_D_MAX": 1500})
    ns["_cached"]("f", "005930", "2026-11-18", lambda t: dict(ERR_FIN), ok=F.fin_complete)
    assert ("f", "005930", "2026-11-18") not in store
    good = {"annual": [{"period": "202412"}], "quarter": []}
    ns["_cached"]("f", "005930", "2026-11-18", lambda t: good, ok=F.fin_complete)
    assert store[("f", "005930", "2026-11-18")] is good
    src = open(_page("Sieu_co_phieu"), encoding="utf-8").read()
    assert 'fetch_financials, ok=fin_complete)' in src                  # _fin dùng đúng bộ kiểm


def test_coverage_naver_error_is_not_new_listing(monkeypatch):
    monkeypatch.setattr(coverage.dart_api, "available", lambda: False)
    rep = coverage.assess_coverage("005930", fin=dict(ERR_FIN))
    assert rep["naver_error"] is True and rep["is_new_listing"] is None
    assert rep["valuation_mode"] == "unknown" and "Naver lỗi" in rep["note"]
    assert "mới niêm yết" not in coverage.coverage_badge(rep)
    ok = coverage.assess_coverage("005930", fin={"annual": [{"period": f"{y}12", "is_consensus": False}
                                                            for y in range(2021, 2025)], "quarter": []})
    assert ok["naver_error"] is False and ok["valuation_mode"] == "intrinsic_ok"
    src = open(os.path.join(ROOT, "views", "tong_hop.py"), encoding="utf-8").read()
    assert '(c or {}).get("naver_error")' in src                         # Tổng hợp không nhớ kết quả Naver lỗi


def test_valuation_page_does_not_cache_peer_naver_errors():
    d = {"dart_errors": [], "peer_errors": 0, "naver_empty": False, "naver_error": None, "peer_list_errors": [],
         "peer_naver_errors": ["000660"]}
    ns = _defs(_page("Dinh_gia"), "_Incomplete", "_load_static_ok",
               ns={"st": st, "_load_static_raw": lambda *a: dict(d)})
    with pytest.raises(ns["_Incomplete"]):
        ns["_load_static_ok"]("005930", "반도체", 12, 987654)
    src = open(_page("Dinh_gia"), encoding="utf-8").read()
    assert '"peer_naver_errors": [t for t, f in peer_fins.items() if (f or {}).get("_error")]' in src


# ---------------------------------------------------------------------------- I14: lỗi DART khi đọc LN cổ đông mẹ
def test_dart_only_year_parent_error_marks_series_error(monkeypatch):
    import tests.test_d1_dart_lag as D1
    monkeypatch.setattr(F, "dart_api", D1._FakeDart([D1._da(y, r, n) for y, r, n in
                                                     ((2022, 100000, 10000), (2023, 105000, 10500),
                                                      (2024, 110000, 11000), (2025, 120000, 12000))]))

    def fetch_full(tk, year=None, errors=None, **k):
        if errors is not None:
            errors.append("hết hạn mức DART phiên")
        return None
    monkeypatch.setattr(AN, "fetch_full", fetch_full)
    out = F._merge_dart(D1.NAVER_A, "X", quarters=D1.NAVER_Q25)
    src = out[-1]["_src"]
    assert src["dart"] == "error" and "cổ đông mẹ" in src["note"]      # không nhớ như 'ok', có cảnh báo
    # DART trả lời 'không có' (không lỗi) → vẫn là năm không đủ điều kiện như trước
    monkeypatch.setattr(AN, "fetch_full", lambda tk, year=None, errors=None, **k: None)
    out = F._merge_dart(D1.NAVER_A, "X", quarters=D1.NAVER_Q25)
    assert out[-1]["period"] == "202412" and out[-1]["_src"]["dart"] == "ok"


# ---------------------------------------------------------------------------- kết quả rỗng không bị cache chung
def test_details_incomplete():
    assert H.details_incomplete({}) and H.details_incomplete({1: None})
    assert H.details_incomplete({1: {"desc": "", "stocks": []}})
    assert not H.details_incomplete({1: {"desc": "", "stocks": [{"ticker": "005930"}]}})


def test_home_recovers_after_empty_naver(fake_market, monkeypatch):
    """Naver lỗi (chủ đề rỗng) rồi hồi phục: lần chạy lại — kể cả phiên khác — lấy lại ngay, không kẹt bản rỗng 60 giây
    trong cache chung dù nút 🔄 đang bị giới hạn."""
    from streamlit.testing.v1 import AppTest
    real = H.theme_ranking
    box = {"down": True, "n": 0}

    def ranking(*a, **k):
        box["n"] += 1
        return ([], "") if box["down"] else real(*a, **k)
    monkeypatch.setattr(H, "theme_ranking", ranking)
    monkeypatch.setattr(H, "refresh_wait", lambda *a, **k: 60.0)        # người khác vừa dùng lượt làm mới
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60).run()
    assert any("Không lấy được danh sách chủ đề" in e.value for e in at.error)
    box["down"] = False
    at2 = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60).run()      # phiên khác
    assert not any("Không lấy được danh sách chủ đề" in e.value for e in at2.error)
    assert box["n"] >= 2

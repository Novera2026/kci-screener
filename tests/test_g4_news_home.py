"""G4 (rà soát 18/11/2026): Tin nóng và Trang chủ — I33 nút làm mới, I34 công bố DART đủ ngày, I38 bộ chọn ổn định,
I69 Trang chủ gọi Naver ít đợt. Mọi test offline (Naver/DART giả)."""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import threading
import time

import pytest
import requests
from streamlit.testing.v1 import AppTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app.py")
NEWS = next(p for p in glob.glob(os.path.join(ROOT, "pages", "*.py")) if "Tin_nong" in p)
UTC = dt.timezone.utc


@pytest.fixture(autouse=True)
def _fresh_refresh_gate(monkeypatch):
    import hotnews as H
    if hasattr(H, "_REFRESH"):
        monkeypatch.setattr(H, "_REFRESH", {})


def _btn(at, label):
    return next(b for b in at.button if label in b.label)


# ================================================================ I33 — nút làm mới
def test_refresh_wait_is_server_wide_and_per_page():
    import hotnews as H
    assert H.refresh_wait("a", 60, now=1000.0) == 0
    assert 0 < H.refresh_wait("a", 60, now=1010.0) <= 50          # 10 giây sau: phải chờ 50 giây
    assert H.refresh_wait("b", 60, now=1010.0) == 0                # trang khác có mốc riêng
    assert H.refresh_wait("a", 60, now=1061.0) == 0


def _count_dart(monkeypatch):
    import hotnews as H
    calls = []

    def fake(days=1, max_pages=None):
        calls.append(days)
        return ([{"date": "20261118", "rcept_no": f"2026111800000{len(calls)}", "market": "KOSPI",
                  "ticker": "005930", "name": "삼성전자", "report": "단일판매ㆍ공급계약체결", "label": "HĐ",
                  "pol": 1, "score": 90, "url": "https://dart.fss.or.kr/x"}], None,
                {"total": 1, "read": 1, "truncated": False})
    monkeypatch.setattr(H, "dart_disclosures", fake)
    return calls


def test_guest_refresh_never_clears_shared_dart_cache(monkeypatch, fake_market):
    """Khách (Cloud) bấm 'Làm mới ngay' 10 lần: không thêm lượt DART nào; Naver chỉ làm mới 1 lần / 60 giây."""
    import hotnews as H
    monkeypatch.setenv("KCI_PUBLIC", "1")
    calls = _count_dart(monkeypatch)
    themes = []
    real = H.theme_ranking
    monkeypatch.setattr(H, "theme_ranking", lambda max_pages=4: themes.append(1) or real())
    at = AppTest.from_file(NEWS, default_timeout=120).run()
    assert not at.exception, at.exception
    assert calls == [1] and len(themes) == 1
    for _ in range(10):
        at = _btn(at, "Làm mới ngay").click().run()
        assert not at.exception, at.exception
    assert calls == [1]                                    # cache DART chung không bị khách xóa
    assert len(themes) == 2                                # lần bấm đầu làm mới Naver, 9 lần sau trong 60 giây bị chặn


def test_owner_refresh_clears_dart(monkeypatch, fake_market):
    monkeypatch.setenv("KCI_PUBLIC", "1")
    calls = _count_dart(monkeypatch)
    at = AppTest.from_file(NEWS, default_timeout=120)
    at.session_state["_owner_ok"] = True
    at.run()
    assert calls == [1]
    at = _btn(at, "Làm mới ngay").click().run()
    assert not at.exception, at.exception
    assert calls == [1, 1]


def test_home_refresh_limited_server_wide(monkeypatch, fake_market):
    import hotnews as H
    seen = []
    real = H.theme_ranking
    monkeypatch.setattr(H, "theme_ranking", lambda max_pages=4: seen.append(1) or real())
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception, at.exception
    for _ in range(5):
        at = _btn(at, "🔄 Làm mới").click().run()
        assert not at.exception, at.exception
    assert len(seen) == 2


# ================================================================ I34 — công bố DART
KINDS_PER_DAY = {          # số công bố giả mỗi ngày theo pblntf_ty (mới nhất trước)
    "I": {"20261117": 180, "20261116": 900, "20261115": 0, "20261114": 0, "20261113": 300},
    "B": {"20261117": 20, "20261116": 150, "20261115": 0, "20261114": 0, "20261113": 30},
    "D": {"20261117": 60, "20261116": 400, "20261115": 0, "20261114": 0, "20261113": 90},
    "A": {"20261117": 500, "20261116": 2000, "20261115": 0, "20261114": 0, "20261113": 400},
}
NAMES = {"I": "단일판매ㆍ공급계약체결", "B": "주요사항보고서(유상증자결정)", "D": "공개매수신고서", "A": "분기보고서"}


def _fake_list(store):
    def get(url, params=None, **k):
        assert "opendart.fss.or.kr/api/list.json" in url
        store.append(dict(params or {}))
        ty = params.get("pblntf_ty")
        days = sorted(KINDS_PER_DAY.get(ty, {}), reverse=True) if ty else []
        rows = []
        for d in days:
            if params["bgn_de"] <= d <= params["end_de"]:
                rows += [{"rcept_no": f"{d}{ty}{i:05d}", "rcept_dt": d, "corp_cls": "Y" if i % 2 else "K",
                          "stock_code": f"{i % 999999:06d}", "corp_name": f"회사{i}",
                          "report_nm": NAMES[ty]} for i in range(KINDS_PER_DAY[ty][d])]
        size, no = int(params.get("page_count", 10)), int(params.get("page_no", 1))
        page = rows[(no - 1) * size: no * size]

        class R:
            def json(self):
                if not rows:
                    return {"status": "013", "message": "조회된 데이타가 없습니다."}
                return {"status": "000", "total_count": len(rows), "total_page": -(-len(rows) // size),
                        "page_no": no, "list": page}
        return R()
    return get


@pytest.fixture
def dart_list(monkeypatch):
    import dart_api as D
    import guard as G
    monkeypatch.setattr(G, "_now_utc", lambda: dt.datetime(2026, 11, 17, 3, 0, tzinfo=UTC))   # 12:00 KST 17/11
    monkeypatch.setenv("DART_API_KEY", "x" * 40)
    monkeypatch.setattr(D, "MIN_GAP", 0.0)
    monkeypatch.setattr(D, "RETRY_WAITS", ())
    sent = []
    monkeypatch.setattr(D.requests, "get", _fake_list(sent))
    return sent


def test_dart_more_days_reads_more_and_goes_through_dart_api(dart_list, monkeypatch):
    import dart_api as D
    import hotnews as H
    thr = []
    real_thr = D._throttle
    monkeypatch.setattr(D, "_throttle", lambda: thr.append(1) or real_thr())
    one, err1, m1 = H.dart_disclosures(1)
    two, err2, m2 = H.dart_disclosures(2)
    five, err5, m5 = H.dart_disclosures(5)
    assert err1 is err2 is err5 is None
    assert (len(one), len(two), len(five)) == (260, 1710, 2130)        # chỉ I/B/D, đủ mọi ngày (trước: 1.500 mục)
    assert m5 == {"total": 2130, "read": 2130, "truncated": False}
    assert not m1["truncated"] and not m2["truncated"]
    assert {p["pblntf_ty"] for p in dart_list} == {"I", "B", "D"}                       # không đọc A (định kỳ)
    assert len(thr) == len(dart_list) and D.STATS["ok"] == len(dart_list)              # qua giãn nhịp + thống kê
    assert all(r["date"] in ("20261117",) for r in one)


def test_dart_truncated_flag_when_cap_reached(dart_list):
    import hotnews as H
    rows, err, meta = H.dart_disclosures(2, max_pages=5)
    assert err is None and meta["truncated"] is True
    assert meta["read"] == 500 and meta["total"] == 1710 and len(rows) == 500    # tổng đủ cả I + B + D
    assert len(dart_list) == 5
    assert [p["pblntf_ty"] for p in dart_list][:3] == ["I", "B", "D"]              # trang 1 mỗi loại trước


def test_dart_cap_does_not_starve_risk_kinds(dart_list, monkeypatch):
    """Mùa nộp báo cáo: loại I một mình vượt trần trang → B (tăng vốn, CB/BW) và D vẫn được đọc trang 1."""
    import hotnews as H
    monkeypatch.setitem(KINDS_PER_DAY["I"], "20261117", 5000)
    rows, err, meta = H.dart_disclosures(1)                # trần 12 trang
    assert err is None and meta["truncated"] is True
    assert meta["total"] == 5000 + 20 + 60 and meta["read"] < meta["total"]
    labels = {r["report"] for r in rows}
    assert NAMES["B"] in labels and NAMES["D"] in labels
    assert len(dart_list) == 12


def test_dart_error_message_has_no_key_and_keeps_partial(dart_list, monkeypatch):
    import dart_api as D
    import hotnews as H
    real = D.requests.get

    def get(url, params=None, **k):
        if params.get("pblntf_ty") == "B":
            class R:
                def json(self):
                    return {"status": "020", "message": "사용한도를 초과하였습니다."}
            return R()
        return real(url, params=params, **k)
    monkeypatch.setattr(D.requests, "get", get)
    rows, err, meta = H.dart_disclosures(1)
    assert len(rows) == 100 and "020" in err and "x" * 40 not in err      # giữ trang 1 của I đã đọc, dừng ở lỗi


def test_dart_missing_key(monkeypatch):
    import hotnews as H
    monkeypatch.delenv("DART_API_KEY", raising=False)
    assert H.dart_disclosures(1) == ([], "thiếu DART_API_KEY", {"total": 0, "read": 0, "truncated": False})


def test_hotnews_has_no_direct_dart_call():
    src = open(os.path.join(ROOT, "hotnews.py"), encoding="utf-8").read()
    assert "opendart.fss.or.kr" not in src and "get_json(" in src


def _news_page(monkeypatch, result):
    import hotnews as H
    calls = []
    monkeypatch.setattr(H, "dart_disclosures", lambda days=1, max_pages=None: calls.append(days) or result)
    return calls


def test_news_page_dart_error_not_blamed_on_key_and_not_cached(monkeypatch, fake_market):
    calls = _news_page(monkeypatch, ([], "DART báo mã 020: đã vượt hạn mức gọi DART trong ngày của khóa", {}))
    at = AppTest.from_file(NEWS, default_timeout=120).run()
    assert not at.exception, at.exception
    w = [x.value for x in at.warning if "DART" in x.value]
    assert w and "020" in w[0] and "DART_API_KEY" not in w[0]
    at.run()
    assert len(calls) == 2                                  # lỗi không bị giữ trong cache 10 phút


def test_news_page_missing_key_hint(monkeypatch, fake_market):
    _news_page(monkeypatch, ([], "thiếu DART_API_KEY", {}))
    at = AppTest.from_file(NEWS, default_timeout=120).run()
    assert any("DART_API_KEY" in x.value for x in at.warning)


def test_news_page_warns_when_truncated(monkeypatch, fake_market):
    _news_page(monkeypatch, ([], None, {"total": 4487, "read": 3000, "truncated": True}))
    at = AppTest.from_file(NEWS, default_timeout=120).run()
    assert any("Chỉ xem được 3,000/4,487" in x.value for x in at.warning)


# ================================================================ I38 — bộ chọn ổn định
ORDER = {"v": [101, 102, 103]}
TNAME = {101: "반도체", 102: "2차전지", 103: "원자력"}


def _themes_by_order(max_pages=4):
    out = []
    for i, no in enumerate(ORDER["v"]):
        out.append({"no": no, "name": TNAME[no], "chg": 5.0 - i, "rise": 7, "fall": 3, "total": 10,
                    "breadth": 70.0})
    return out, "OPEN"


def _detail(no, n=40):
    return {"desc": f"desc {no}", "info": {}, "stocks": [
        {"ticker": f"{no:03d}{k:03d}", "name": f"종목{no}-{k}", "price": 1000.0, "chg": 1.0, "tv_eok": 1.0,
         "cap_eok": 10.0, "reason": "r"} for k in range(2)]}


def test_theme_selectbox_keeps_theme_after_reorder(monkeypatch, fake_market):
    import hotnews as H
    monkeypatch.setattr(H, "theme_ranking", _themes_by_order)
    monkeypatch.setattr(H, "theme_detail", _detail)
    ORDER["v"] = [101, 102, 103]
    at = AppTest.from_file(NEWS, default_timeout=120).run()
    box = at.selectbox(key="news_theme")
    assert any("원자력" in o for o in box.options)
    at = box.set_value(103).run()                            # 원자력 (giá trị lưu = số chủ đề)
    assert any(i.value == "desc 103" for i in at.info)
    ORDER["v"] = [103, 101, 102]                            # dữ liệu realtime đổi thứ tự
    at = _btn(at, "Làm mới ngay").click().run()
    assert not at.exception, at.exception
    assert at.selectbox(key="news_theme").value == 103
    assert any(i.value == "desc 103" for i in at.info)
    assert not any(i.value == "desc 101" for i in at.info)


def test_news_kind_filter_has_fixed_options(monkeypatch, fake_market):
    import hotnews as H
    assert len(H.NEWS_KINDS) == 6
    at = AppTest.from_file(NEWS, default_timeout=120).run()      # tin giả: chỉ có 🟢 (không tin rủi ro)
    ms = at.multiselect(key="news_kinds")
    assert list(ms.options) == H.NEWS_KINDS
    assert "🔴 Rủi ro" in ms.value                                # loại xuất hiện sau vẫn được chọn sẵn
    at = ms.set_value(["🔴 Rủi ro"]).run()
    at.run()
    assert at.multiselect(key="news_kinds").value == ["🔴 Rủi ro"]   # lần không có tin rủi ro: không thành []


def _run_with_selection(at, key, rows):
    from streamlit.proto.WidgetStates_pb2 import WidgetState, WidgetStates
    wid = at.session_state._state._key_id_mapper.get_id_from_key(key)
    ws = at._tree.get_widget_states()
    w = WidgetState(id=wid, string_value=json.dumps({"selection": {"rows": rows, "columns": [], "cells": []}}))
    out = WidgetStates()
    out.widgets.extend([x for x in ws.widgets if x.id != wid] + [w])
    return at._run(out, timeout=120)


def test_megatrend_selection_follows_key_not_position(monkeypatch, fake_market):
    import hotnews as H
    import megatrend as MT
    import streamlit as st
    chg = {m["key"]: 5.0 - i * 0.5 for i, m in enumerate(MT.MEGATRENDS)}

    def ranking(max_pages=4):
        out = [{"no": no, "name": f"T{no}", "chg": chg[m["key"]], "rise": 5, "fall": 5, "total": 10,
                "breadth": 50.0} for m in MT.MEGATRENDS for no in m["themes"]]
        return sorted(out, key=lambda x: -x["chg"]), "OPEN"
    monkeypatch.setattr(H, "theme_ranking", ranking)
    at = AppTest.from_file(APP, default_timeout=120).run()
    df = next(d.value for d in at.dataframe if "Xu hướng" in getattr(d.value, "columns", []))
    row = next(i for i, x in enumerate(df["Xu hướng"]) if "Lưới điện" in x)
    at = _run_with_selection(at, "mt_table", [row])
    head = lambda a: [m.value for m in a.markdown if "cổ phiếu trong nhóm" in m.value]   # noqa: E731
    assert "Lưới điện" in head(at)[0]
    chg.update(ship=9.0, ai_memory=-3.0, grid=-4.0)            # % đổi → thứ hạng đổi
    st.cache_data.clear()
    at = _run_with_selection(at, "mt_table", [row])          # trình duyệt gửi lại dòng đang tô (theo vị trí)
    assert not at.exception, at.exception
    assert "Lưới điện" in head(at)[0]
    df = next(d.value for d in at.dataframe if "Xu hướng" in getattr(d.value, "columns", []))
    assert "Hạng hôm nay" in df.columns
    assert int(df.loc[df["Xu hướng"].str.contains("Đóng tàu"), "Hạng hôm nay"].iloc[0]) == 1


# ================================================================ I69 — Trang chủ ít đợt Naver
def _naver_fake(log, lat=0.2):
    import megatrend as MT
    nos = sorted({n for m in MT.MEGATRENDS for n in m["themes"]})
    nos += [9000 + i for i in range(250 - len(nos))]
    lock = threading.Lock()

    def respond(url, params):
        base = "https://m.stock.naver.com/api/stocks/theme"
        if url == base:
            p = int((params or {}).get("page", 1))
            grp = [{"no": no, "name": f"T{no}", "changeRate": f"{3 - i * 0.01:.2f}", "riseCount": 6,
                    "fallCount": 4, "totalCount": 10} for i, no in enumerate(nos)][(p - 1) * 100: p * 100]
            return {"groups": grp, "totalCount": len(nos), "marketStatus": "CLOSE"}
        if url.startswith(base + "/"):
            no = int(url.rsplit("/", 1)[1])
            return {"themeDescription": f"d{no}", "groupInfo": {}, "themeItemInfoMap": {}, "stocks": [
                {"itemCode": f"{no % 1000:03d}{k:03d}", "stockName": f"S{no}-{k}", "closePrice": "1,000",
                 "fluctuationsRatio": "1.0", "accumulatedTradingValue": "100", "marketValue": "1000",
                 "stockEndType": "stock"} for k in range(2)]}
        if "/api/index/" in url:
            return {"closePrice": "2,500.00", "compareToPreviousClosePrice": "10", "fluctuationsRatio": "0.4",
                    "compareToPreviousPrice": {"name": "RISING"}, "marketStatus": "CLOSE"}
        return None

    def get(*a, **k):
        url = next(x for x in a if isinstance(x, str))
        t0 = time.monotonic()
        time.sleep(lat)
        j = respond(url, k.get("params"))
        with lock:
            log.append((t0, time.monotonic(), url))

        class R:
            def raise_for_status(self):
                if j is None:
                    raise requests.HTTPError("404")

            def json(self):
                return j
        return R()
    return get


def _waves(log, lat):
    n, end = 0, -1.0
    for t0, t1, _ in sorted(log):
        if t0 >= end - lat / 2:
            n += 1
        end = max(end, t1)
    return n


def test_home_naver_calls_in_at_most_three_waves(monkeypatch):
    import screener
    import streamlit as st
    from conftest import FAKE_RF
    monkeypatch.setattr(screener, "rf_for_valuation", lambda: dict(FAKE_RF))
    log = []
    fake = _naver_fake(log)
    monkeypatch.setattr(requests, "get", fake)
    monkeypatch.setattr(requests.Session, "get", fake)
    st.cache_data.clear()
    try:
        at = AppTest.from_file(APP, default_timeout=120).run()
        assert not at.exception, at.exception
        assert not at.error, [e.value for e in at.error]
        assert log, "không có lượt Naver nào"
        assert _waves(log, 0.2) <= 3, sorted((round(a, 2), u) for a, _, u in log)
    finally:
        st.cache_data.clear()


def test_home_bot_panel_is_fragment():
    """Form bot nằm trong @st.fragment: thao tác ở form không chạy lại phần chỉ số/chủ đề (gọi Naver)."""
    import ast
    tree = ast.parse(open(os.path.join(ROOT, "views", "trang_chu.py"), encoding="utf-8").read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_bot_panel")
    assert any(ast.unparse(d) in ("st.fragment", "st.fragment()") for d in fn.decorator_list)
    keys = {k.value.value for n in ast.walk(fn) if isinstance(n, ast.Call)
            for k in n.keywords if k.arg == "key" and isinstance(k.value, ast.Constant)}
    assert {"hb_kind", "hb_presets", "hb_run"} <= keys


def test_hotnews_reuses_one_session(monkeypatch):
    import hotnews as H
    seen = []
    monkeypatch.setattr(requests.Session, "get", lambda self, *a, **k: seen.append(id(self)) or None)
    H._get("https://m.stock.naver.com/a")
    H._get("https://m.stock.naver.com/b")
    assert len(seen) == 2 and len(set(seen)) == 1


def test_home_reselect_fetches_only_selected_trend(monkeypatch):
    """Chọn dòng Mega trend khác: chỉ tải chủ đề của xu hướng đó, nhóm dẫn dắt vẫn lấy từ cache (khóa cache ổn định)."""
    import megatrend as MT
    import screener
    import streamlit as st
    from conftest import FAKE_RF
    monkeypatch.setattr(screener, "rf_for_valuation", lambda: dict(FAKE_RF))
    log = []
    fake = _naver_fake(log, lat=0.0)
    monkeypatch.setattr(requests, "get", fake)
    monkeypatch.setattr(requests.Session, "get", fake)
    st.cache_data.clear()
    try:
        at = AppTest.from_file(APP, default_timeout=120).run()
        assert not at.exception, at.exception
        df = next(d.value for d in at.dataframe if "Xu hướng" in getattr(d.value, "columns", []))
        lead = set(df.sort_values("Hạng hôm nay")["Xu hướng"].head(3))
        lead_nos = {n for m in MT.MEGATRENDS if f"{m['icon']} {m['name']}" in lead for n in m["themes"]}
        row, pick = next((i, m) for i, m in enumerate(MT.MEGATRENDS)
                         if f"{m['icon']} {m['name']}" not in lead and not set(m["themes"]) <= lead_nos)
        assert df["Xu hướng"].iloc[row] == f"{pick['icon']} {pick['name']}"
        log.clear()
        at = _run_with_selection(at, "mt_table", [row])
        assert not at.exception, at.exception
        det = {int(u.rsplit("/", 1)[1]) for _, _, u in log if "/api/stocks/theme/" in u}
        assert det and det <= set(pick["themes"]) - lead_nos, det
    finally:
        st.cache_data.clear()

"""Bộ đối thủ, phép neo peer và danh mục mã (rà soát 18/11/2026: I06, I11, I15, I52, I53, I54, I55) — không cần mạng."""
import glob
import json
import os

import pandas as pd
import pytest
import requests

import peers as PR
import pipeline as P
import screener
import valuation as V

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A = V.Assumptions(rf=0.03, erp=0.05, g_terminal=0.02)


class _Resp:
    def __init__(self, body, status=200):
        self._b, self.status_code = body, status
        self.ok = status < 400

    def json(self):
        return self._b

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


@pytest.fixture
def no_net(monkeypatch):
    """Chặn mọi lượt gọi mạng thật (test nào cần thì tự thay requests.get)."""
    def _blocked(*a, **k):
        raise requests.ConnectionError("test: mạng bị chặn")
    monkeypatch.setattr(requests, "get", _blocked)
    monkeypatch.setattr(requests.Session, "request", _blocked)
    getattr(P, "_IND_NAMES", {}).clear()


def _series(n=5, rev0=1000.0, g=0.05, margin=0.10, eq0=5000.0, dps=500.0, shares=1e7):
    out, eq = [], eq0
    for i in range(n):
        rev = rev0 * (1 + g) ** i
        np_ = rev * margin
        eq += np_ - dps * shares / 1e8
        out.append({"period": f"{2021 + i}12", "is_consensus": False, "revenue": rev, "op_profit": rev * 0.12,
                    "net_profit": np_, "equity": eq, "eps": np_ * 1e8 / shares, "bps": eq * 1e8 / shares,
                    "dps": dps, "roe": np_ / eq * 100, "debt_ratio": 80.0, "_dart": True})
    return out


def _row(series, price=None, shares=1e7, tk="000010"):
    last = series[-1]
    price = price or last["bps"] * 1.0
    return {"ticker": tk, "name": "Test", "price": price, "market_cap": price * shares,
            "eps": last["eps"], "bps": last["bps"], "dps": last["dps"], "industry_kr": "식품", "sector": None}


class _Reg:
    def __init__(self, tickers):
        self.df = pd.DataFrame({"ticker": list(tickers)})


# ============================================================================ I06 — lỗi nguồn không phải "không có"
def test_industry_ranked_http_429_with_json_body_is_error_not_empty(no_net, monkeypatch):
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return _Resp({"code": "TOO_MANY_REQUESTS", "message": "slow down"}, 429)
    monkeypatch.setattr(requests, "get", get)
    assert P._industry_ranked("반도체와반도체장비") is None
    assert len(calls) == 2                                   # thử lại 1 lần
    cache, errs = {}, []
    assert P.industry_peers("반도체와반도체장비", None, 5, cache=cache, errors=errs) == []
    assert "반도체와반도체장비" not in cache and errs           # lỗi không được nhớ, có ghi lỗi


def test_industry_ranked_body_without_groups_or_stocks_is_error(no_net, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp({"unexpected": 1}))
    assert P._industry_ranked("식품") is None


def test_industry_ranked_retries_once_and_drops_preferred(no_net, monkeypatch):
    state = {"n": 0}

    def get(url, params=None, headers=None, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            raise requests.ReadTimeout("thoáng qua")
        if url.endswith("/industry"):
            return _Resp({"groups": [{"no": 7, "name": "식품"}]})
        return _Resp({"totalCount": 3, "stocks": [
            {"itemCode": "000010", "stockEndType": "stock", "marketValueRaw": 10},
            {"itemCode": "000015", "stockEndType": "stock", "marketValueRaw": 50},     # ưu đãi
            {"itemCode": "000020", "stockEndType": "stock", "marketValueRaw": 30}]})
    monkeypatch.setattr(requests, "get", get)
    assert P._industry_ranked("식품") == ["000020", "000010"]


def test_naver_domestic_peers_error_is_none(no_net, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp({"code": "ERR"}, 429))
    assert PR.naver_domestic_peers("005930") is None
    body = {"industryCompareInfo": [{"itemCode": "000660", "stockType": "domestic"}]}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(body))
    assert PR.naver_domestic_peers("005930") == ["000660"]


def test_domestic_candidates_reports_source_errors(no_net, monkeypatch):
    monkeypatch.setattr(PR, "naver_domestic_peers", lambda tk: None)
    monkeypatch.setattr(P, "_industry_ranked", lambda ik: None)
    errs = []
    assert PR.domestic_candidates("000010", "식품", _Reg(["000010", "000020"]), 5, errors=errs) == []
    assert any("Naver" in e for e in errs) and any("ngành" in e for e in errs)


def test_peer_rows_batch_marks_list_errors(no_net, monkeypatch):
    monkeypatch.setattr(PR, "naver_domestic_peers", lambda tk: None)
    monkeypatch.setattr(P, "_industry_ranked", lambda ik: None)
    monkeypatch.setattr(screener, "fetch_many", lambda tks, registry=None, prefer=None: ([], []))
    out = PR.peer_rows_batch([("000010", "식품")], _Reg(["000010"]), {}, n=5)
    assert out["000010"] and all(r.get(PR.LIST_ERROR) for r in out["000010"])
    assert P.peer_failures(out["000010"])


# ---- trang 🧮: bộ đối thủ co vì lỗi không được lưu cache dùng chung; 🔄 lấy lại -----------------------------------
def _fin():
    s = _series()
    return {"annual": s, "quarter": [{"period": "202609", "is_consensus": False, "revenue": 300.0,
                                      "op_profit": 36.0, "net_profit": 30.0}]}


def test_valuation_page_does_not_cache_shrunk_peer_set(no_net, monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import analysis as AN
    import dart_api
    import dossier as DS
    import financials

    st.cache_data.clear()
    reg = screener.TickerRegistry()
    tk = "005930"
    ik = reg.industry_of(tk)
    _d = reg.df[reg.df["industry_kr"] == ik]           # bỏ holding và mã cùng tập đoàn (I21/I57 loại khỏi bộ đối thủ)
    same = [t for t, nm, g in zip(_d["ticker"], _d["name_kr"], _d["group"])
            if t != tk and not V.is_holdco_name(nm, ik) and g != "삼성"][:12]
    state = {"fail": True, "calls": 0}

    def ranked(industry):
        state["calls"] += 1
        return None if state["fail"] else [tk] + same
    monkeypatch.setattr(P, "_industry_ranked", ranked)
    monkeypatch.setattr(PR, "naver_domestic_peers", lambda t: [])
    monkeypatch.setattr(financials, "fetch_financials", lambda t, *a, **k: _fin())
    monkeypatch.setattr(dart_api, "available", lambda: False)
    monkeypatch.setattr(dart_api, "valuation_extras", lambda *a, **k: {})
    monkeypatch.setattr(AN, "fetch_full", lambda *a, **k: None)
    monkeypatch.setattr(AN, "fetch_quarter_reports", lambda *a, **k: [])
    monkeypatch.setattr(DS, "save_section", lambda *a, **k: None)

    def one(t, registry=None, prefer=None, live=True):
        last = _series()[-1]
        return screener.StockRow(ticker=t, name=t, price=last["bps"], market_cap=last["bps"] * 1e7,
                                 per=10.0, pbr=1.0, eps=last["eps"], bps=last["bps"], dps=last["dps"],
                                 industry_kr=ik, source="naver")
    monkeypatch.setattr(screener, "fetch_one", one)
    monkeypatch.setattr(screener, "fetch_many", lambda tks, registry=None, prefer=None, **k: (
        [one(t).to_dict() for t in tks], []))
    monkeypatch.setattr(screener, "fetch_weekly_closes", lambda *a, **k: [])
    monkeypatch.setattr(PR, "fetch_foreign", lambda codes: [])

    page = next(p for p in glob.glob(os.path.join(ROOT, "pages", "*.py")) if "Dinh_gia" in p)
    at = AppTest.from_file(page, default_timeout=120).run()
    at.text_input(key="vp_query_box").set_value(tk)
    at = next(b for b in at.button if "Bắt đầu" in b.label).click().run()
    assert not at.exception, at.exception
    assert len(at.session_state["vp_snap"]["key"][1]) == 0               # lỗi ngành → bộ đối thủ rỗng
    assert any("Bộ đối thủ chưa đầy đủ" in w.value for w in at.warning)
    n1 = state["calls"]

    state["fail"] = False                                                # mạng ổn lại → phiên MỚI phải lấy lại
    b = AppTest.from_file(page, default_timeout=120).run()
    b.text_input(key="vp_query_box").set_value(tk)
    b = next(x for x in b.button if "Bắt đầu" in x.label).click().run()
    assert not b.exception, b.exception
    assert state["calls"] > n1
    assert list(b.session_state["vp_snap"]["key"][1]) == same[:12]
    assert not any("Bộ đối thủ chưa đầy đủ" in w.value for w in b.warning)

    n2 = state["calls"]                                                  # 🔄 lấy lại cả dữ liệu tĩnh
    b = next(x for x in b.button if "Cập nhật" in x.label).click().run()
    assert not b.exception, b.exception
    assert state["calls"] > n2
    assert dict(b.session_state["vp_nonce"]) == {tk: 1}                  # 🔄 chỉ bỏ cache của mã đang xem
    st.cache_data.clear()


# ============================================================================ I11 — đối thủ lỗi không trông như mã lỗ
def _integration_ok(tk):
    return {"stockName": f"n{tk}", "industryCode": "276",
            "totalInfos": [{"code": "per", "value": "10.5배"}, {"code": "pbr", "value": "1.2배"},
                           {"code": "marketValue", "value": "1조"}],
            "dealTrendInfos": [{"closePrice": "10,000"}]}


class _RegFull:
    """Danh mục giả đủ hàm cho screener (resolve, name_of, sector_of, industry_of)."""
    def __init__(self, ind: dict):
        self.ind = ind
        self.df = pd.DataFrame({"ticker": list(ind)})

    def resolve_info(self, q):
        return {"ticker": q if q in self.ind else None}

    def resolve(self, q):
        return q if q in self.ind else None

    def name_of(self, t):
        return None

    def sector_of(self, t):
        return None

    def industry_of(self, t):
        return self.ind.get(t)


def test_failed_peer_row_carries_note_and_fetch_ok_false(no_net, monkeypatch):
    seen = []

    def get(url, params=None, headers=None, timeout=None):
        seen.append(url)
        if "integration" in url:
            raise requests.ReadTimeout("hết giờ")
        return _Resp({})
    monkeypatch.setattr(requests, "get", get)
    r = screener.fetch_one("000020", prefer="naver", live=False)
    assert r.fetch_ok is False and "fail" in r.note and r.per is None
    assert not any("itemSummary" in u for u in seen)                  # đường dự phòng đã chết → bỏ
    monkeypatch.setattr(screener, "RETRY_WAIT", 0)
    rows, _ = screener.fetch_many(["000020"], registry=_RegFull({"000020": None}), live=False)
    assert rows[0]["fetch_ok"] is False and rows[0]["note"]
    monkeypatch.setattr(requests, "get", lambda url, **k: _Resp(_integration_ok("000020")))
    ok = screener.fetch_one("000020", prefer="naver", live=False)
    assert ok.fetch_ok is True and ok.per == pytest.approx(10.5) and ok.note == ""


def _peer(i, pe, pb, ok=True):
    r = {"ticker": f"{100 + i:05d}0", "per": pe, "pbr": pb}
    if not ok:
        r.update(per=None, pbr=None, fetch_ok=False, note="naver integration fail: timeout")
    return r


def _case_peers(n_fail):
    """12 đối thủ, P/E 9–11, P/B ~1; n_fail dòng đầu lấy lỗi (P/E, P/B trống)."""
    return [_peer(i, 9.0 + (i % 3), 0.9 + 0.05 * (i % 3), ok=i >= n_fail) for i in range(12)]


TODAY = pd.Timestamp("2026-11-18").date()


def test_lost_anchor_from_failed_peers_is_not_published():
    s = _series()
    r = _row(s, price=s[-1]["bps"] * 1.2)
    fin = {"annual": [], "quarter": []}
    base = P.assess("000010", r, fin, A, _case_peers(0), 1.0, series=s, today=TODAY)
    assert base["E"]["anchor"] and base["rec"]["fair"]
    bad = P.assess("000010", r, fin, A, _case_peers(10), 1.0, series=s, today=TODAY)
    assert bad["E"]["anchor"] is None
    assert bad["rec"]["fair"] is None and "lỗi nguồn" in bad["rec"]["status"]
    assert bad["dq"]["cap"] == "Low"
    assert any(c["Kiểm tra"] == "Bộ đối thủ" and c["Kết quả"] == P.FAIL for c in bad["dq"]["checks"])
    # cùng số đối thủ trống P/E nhưng vì LỖ thật (không lỗi nguồn) → vẫn được công bố như trước
    loss = [dict(p, per=None, pbr=None) for p in _case_peers(0)[:10]] + _case_peers(0)[10:]
    real = P.assess("000010", r, fin, A, loss, 1.0, series=s, today=TODAY)
    assert real["E"]["anchor"] is None and real["rec"]["fair"]


def test_some_failed_peers_cap_confidence_low():
    s = _series()
    r = _row(s, price=s[-1]["bps"] * 1.2)
    ctx = P.assess("000010", r, {}, A, _case_peers(2), 1.0, series=s, today=TODAY)
    assert ctx["E"]["anchor"] and ctx["rec"]["fair"] and ctx["rec"]["confidence"] == "Low"
    chk = next(c for c in ctx["dq"]["checks"] if c["Kiểm tra"] == "Bộ đối thủ")
    assert "2/12" in chk["Ghi chú"]


def test_list_error_marker_blocks_publication_without_anchor_and_is_not_a_peer():
    s = _series()
    r = _row(s, price=s[-1]["bps"] * 1.2)
    peers = [PR.list_error_row("danh sách ngành 식품 lỗi")]
    assert P.peer_medians(peers)["n"] == 0
    ctx = P.assess("000010", r, {}, A, peers, 1.0, series=s, today=TODAY)
    assert ctx["rec"]["fair"] is None and "lỗi nguồn" in ctx["rec"]["status"]
    ps = PR.build_peer_set(r, s, None, peers, {}, foreign_profiles=[])
    assert ps["domestic"] == []
    # prepare(peer_errors=…) của trang 🧮 cho cùng kết quả
    c2 = P.prepare("000010", r, {}, A, [], 1.0, series=s, today=TODAY, peer_errors=["gợi ý đối thủ Naver lỗi"])
    assert c2["dq"]["cap"] == "Low"
    E = P.evaluate(c2["D"], A, c2["drivers"], c2["idn"], c2["dq"], c2["evidence"])
    assert E["rec"]["fair"] is None


def test_reconcile_anchor_fail_only_bites_without_anchor():
    c = [{"name": "a", "value": 10_000, "role": "primary"}, {"name": "b", "value": 10_500, "role": "primary"}]
    assert P.reconcile(10_000, c, None, [], anchor_fail="3/12 đối thủ không lấy được")["fair"] is None
    assert P.reconcile(10_000, c, 10_000, [], anchor_fail="3/12 đối thủ không lấy được")["fair"]
    assert P.reconcile(10_000, c, None, [])["fair"]


# ============================================================================ I15 — một phép neo cho lõi mid-cycle
_HIST = [
    {"year": 2020, "net_profit": 1000, "equity": 10000, "revenue": 20000, "op_profit": 1500},
    {"year": 2021, "net_profit": 1100, "equity": 10500, "revenue": 21000, "op_profit": 1600},
    {"year": 2022, "net_profit": 900, "equity": 11000, "revenue": 22000, "op_profit": 1300},
    {"year": 2023, "net_profit": 950, "equity": 11500, "revenue": 23000, "op_profit": 1400},
    {"year": 2024, "net_profit": -300, "equity": 11200, "revenue": 21000, "op_profit": -100},
]
_ROW = {"ticker": "000010", "name": "t", "industry_kr": "기계", "eps": -150, "bps": 20000, "price": 15000,
        "fwd_eps": 300}


def test_fallback_midcycle_core_uses_midcycle_anchor():
    res = V.value_stock(_ROW, V.Assumptions(), 10, 0.8, history=_HIST)
    assert "mid-cycle" in res.auto_method and res.anchor_basis == "mid"
    assert res.anchor == pytest.approx(P.peer_anchor(res.methods, True))
    assert res.anchor == pytest.approx(16_260.87, rel=1e-4)
    assert not res.triangulated                                       # lõi 1,27× phép neo chung → không trộn 50/50
    assert res.fair_value == pytest.approx(20_628.89, rel=1e-4)


def test_fallback_midcycle_not_irreconcilable_against_wrong_anchor():
    res = V.value_stock(_ROW, V.Assumptions(), 10, 0.3, history=_HIST)  # P/B peer 0,3
    assert res.anchor_basis == "mid" and res.status != "⚪ nội tại và peer lệch >4×"
    assert 0.25 <= res.fair_raw / res.anchor <= 4.0


def test_trailing_core_keeps_trailing_anchor():
    row = {"ticker": "000010", "industry_kr": "식품", "eps": 1000, "bps": 10000, "price": 12000, "dps": 300}
    res = V.value_stock(row, A, 12, 1.1)
    assert res.anchor_basis == "trailing"
    assert res.anchor == pytest.approx(P.peer_anchor(res.methods, False))


def test_pipeline_uses_the_same_anchor_as_value_stock():
    s = _series()
    r = _row(s, price=s[-1]["bps"] * 1.2)
    ctx = P.assess("000010", r, {}, A, _case_peers(0), 1.0, series=s, today=TODAY)
    assert ctx["E"]["anchor"] == pytest.approx(ctx["E"]["base"].anchor)


# ============================================================================ I52 — mã mới niêm yết, mã rời sàn
def _naver_world(industry_members=None):
    members = industry_members or {}

    def get(url, params=None, headers=None, timeout=None):
        if url.endswith("/api/stocks/industry"):
            return _Resp({"groups": [{"no": 276, "name": "복합기업"}, {"no": 11, "name": "생명보험"}]})
        if "/api/stocks/industry/" in url:
            no = int(url.rsplit("/", 1)[1])
            st = [{"itemCode": t, "stockEndType": "stock", "marketValueRaw": 1000 - i}
                  for i, t in enumerate(members.get(no, []))]
            return _Resp({"totalCount": len(st), "stocks": st})
        if "integration" in url:
            tk = url.split("/stock/")[1].split("/")[0]
            return _Resp(_integration_ok(tk))
        raise requests.ConnectionError(url)
    return get


def test_new_listing_gets_industry_from_naver_code(no_net, monkeypatch):
    monkeypatch.setattr(requests, "get", _naver_world())
    row = screener.fetch_one("0220W0", registry=_RegFull({}), prefer="naver", live=False)
    assert row.industry_code == "276" and row.industry_kr == "복합기업"
    assert P.identify(row.to_dict())["bucket"] == "Holdco"
    assert P.industry_name(276) == "복합기업" and P.industry_name(None) is None


def test_domestic_candidates_accepts_new_listing_in_current_industry(no_net, monkeypatch):
    monkeypatch.setattr(requests, "get", _naver_world({276: ["028260", "0220W0", "000070", "00104K"]}))
    monkeypatch.setattr(PR, "naver_domestic_peers", lambda tk: [])
    reg = _Reg(["028260", "000070", "00104K"])                        # danh mục cũ: chưa có 0220W0
    out = PR.domestic_candidates("028260", "복합기업", reg, 12)
    assert "0220W0" in out and "00104K" not in out                     # ưu đãi (tận cùng K) không vào
    # mã mới chưa có ngành trong danh mục → ngành lấy theo industryCode
    out2 = PR.domestic_candidates("0220W0", None, reg, 12)
    assert "028260" in out2 and "000070" in out2


def test_domestic_candidates_rejects_registry_preferred_from_suggestions(no_net, monkeypatch):
    # 00104K CJ4우(전환) có trong tickers_all.csv — gợi ý Naver không được đưa nó vào bộ đối thủ
    monkeypatch.setattr(requests, "get", _naver_world({276: ["028260", "000070"]}))
    monkeypatch.setattr(PR, "naver_domestic_peers", lambda tk: ["00104K", "005935", "000070"])
    reg = _Reg(["028260", "000070", "00104K", "005935"])
    assert PR.domestic_candidates("028260", "복합기업", reg, 12) == ["000070"]


@pytest.mark.parametrize("fail", ["integration", "industry_list"])
def test_new_listing_industry_lookup_error_is_reported(no_net, monkeypatch, fail):
    # mã mới chưa có trong danh mục: tra ngành lỗi → phải ghi lỗi (trang 🧮 không lưu cache bộ đối thủ co)
    ok = _naver_world({276: ["028260", "000070"]})

    def get(url, params=None, headers=None, timeout=None):
        if (fail == "integration" and "integration" in url) or (fail == "industry_list"
                                                                 and url.endswith("/api/stocks/industry")):
            return _Resp({"message": "too many"}, 429)
        return ok(url, params, headers, timeout)
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(P, "_IND_NAMES", {})
    monkeypatch.setattr(PR, "naver_domestic_peers", lambda tk: [])
    errs: list = []
    out = PR.domestic_candidates("0220W0", None, _Reg(["028260", "000070"]), 12, errors=errs)
    assert out == [] and errs


def test_by_industry_drops_delisted(no_net, monkeypatch):
    import screen as SC
    reg = _RegFull({"082640": "생명보험", "032830": "생명보험", "088350": "생명보험"})
    reg.df["industry_kr"] = ["생명보험"] * 3
    monkeypatch.setattr(requests, "get", _naver_world({11: ["032830", "088350"]}))
    assert SC.by_industry("생명보험", reg) == ["032830", "088350"]
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp({}, 503))   # Naver lỗi → giữ danh mục
    assert SC.by_industry("생명보험", reg) == ["082640", "032830", "088350"]


# ============================================================================ I53 — cp ưu đãi không chiếm chỗ top-n
def test_preferred_share_does_not_take_a_peer_slot(monkeypatch):
    commons = [f"{i:05d}0" for i in range(1, 23)]                     # 22 cp thường
    ranked = ["005930", commons[0], "005935"] + commons[1:]
    monkeypatch.setattr(P, "_industry_ranked", lambda ik: ranked)
    monkeypatch.setattr(PR, "naver_domestic_peers", lambda tk: [])
    out = PR.domestic_candidates("005930", "반도체", _Reg(["005930"] + commons), 12)
    assert len(out) == 12 and "005935" not in out


# ============================================================================ I54 — dữ liệu năm nước ngoài
def _world(cols, rev, ebit, ni):
    def col(vals):
        return {k: {"krw": f"{v:,.2f}"} for k, v in zip(cols, vals)}
    annual = {"trTitleList": [{"isConsensus": "N", "key": k} for k in cols] + [
        {"isConsensus": "Y", "key": "2026.12.31"}],
        "rowList": [{"title": "매출액", "columns": col(rev)}, {"title": "EBIT", "columns": col(ebit)},
                    {"title": "당기순이익", "columns": col(ni)}]}
    basic = {"stockName": "X", "marketValueKrwRaw": "1000000000000",
             "stockItemTotalInfos": [{"code": "per", "value": "20"}, {"code": "pbr", "value": "4"}]}

    def get(url, headers=None, timeout=None):
        if url.endswith("/basic"):
            return _Resp(basic)
        if url.endswith("/finance/annual"):
            return _Resp(annual)
        return _Resp({"trTitleList": [], "rowList": []})
    return get


def test_foreign_profile_drops_duplicated_year(no_net, monkeypatch):
    # TSM: Naver chép năm 2024 vào 2023 → CAGR 14,7% sai
    monkeypatch.setattr(requests, "get", _world(["2023.12.31", "2024.12.31", "2025.12.31"],
                                                [2894636.84, 2894543.76, 3808717.95],
                                                [1322203.29, 1323311.53, 1936703.48],
                                                [1173401.32, 1173363.35, 1717731.43]))
    p = PR.foreign_profile("TSM")
    g = p[PR.GROWTH]
    assert g == pytest.approx((3808717.95 / 2894543.76 - 1) * 100) and abs(g - 14.71) > 5
    assert "trùng" in p.get("Ghi chú", "") and p["Năm BCTC"] == "2025"
    assert "1 năm" in p["Ghi chú"]                                      # còn 2 năm → nói rõ chỉ tăng trưởng 1 năm


def test_foreign_profile_drops_inserted_quarter_column(no_net, monkeypatch):
    # GM: cột quý 2026.03.31 chèn sau FY2025 → 'Năm BCTC' 2026 và CAGR chia sai kỳ
    monkeypatch.setattr(requests, "get", _world(["2024.12.31", "2025.12.31", "2026.03.31"],
                                                [187442.0, 185019.0, 185019.0], [15399.0, 13915.0, 13915.0],
                                                [7189.0, 3180.0, 3180.0]))
    p = PR.foreign_profile("GM")
    assert p["Năm BCTC"] == "2025"
    assert p[PR.GROWTH] == pytest.approx((185019.0 / 187442.0 - 1) * 100)
    assert p["Biên LN ròng %"] == pytest.approx(3180.0 / 185019.0 * 100)


def test_foreign_profile_keeps_real_close_years(no_net, monkeypatch):
    # AT&T: DT 2023/2024 lệch 0,075% nhưng LN khác hẳn → số thật, giữ cả 3 năm
    monkeypatch.setattr(requests, "get", _world(["2023.12.31", "2024.12.31", "2025.12.31"],
                                                [122428.0, 122336.0, 125648.0], [24639.0, 24194.0, 25463.0],
                                                [14192.0, 10746.0, 21889.0]))
    p = PR.foreign_profile("T")
    assert p[PR.GROWTH] == pytest.approx(((125648.0 / 122428.0) ** 0.5 - 1) * 100)
    assert "Ghi chú" not in p


def test_growth_label_is_two_year_cagr_everywhere():
    assert "CAGR DT 2 năm %" in PR.METRICS and not any("3 năm" in m for m in PR.METRICS)
    ann = [{"period": f"{y}12", "revenue": r} for y, r in ((2022, 100.0), (2023, 110.0), (2024, 121.0), (2025, 133.1))]
    d = PR.domestic_profile({"ticker": "x"}, ann)
    assert d[PR.GROWTH] == pytest.approx(10.0)
    ps = {"position": [{"Chỉ số": PR.GROWTH, "Hạng trong nước (%)": 80.0},
                       {"Chỉ số": "ROE %", "Hạng trong nước (%)": 60.0}]}
    ov = P.overall_assessment({"fair": None}, None, None, ps)
    assert ov["pillars"]["Vị thế cạnh tranh"] == pytest.approx(70.0)


# ============================================================================ I55 — lọc SPAC / ưu đãi khi dựng danh mục
@pytest.mark.parametrize("name,code,excluded", [
    ("메리츠제1호스팩", "123450", True),
    ("유진스팩12호", "123460", True),
    ("에스케이증권제14호기업인수목적", "0205H0", True),
    ("아인스팩씨엔에스", "206400", False),
    ("CJ4우(전환)", "00104K", True),
    ("삼성전자우", "005935", True),
    ("성우", "081580", False),
])
def test_registry_spac_and_preferred_filter(name, code, excluded):
    import build_full_registry as B
    assert B._excluded(name, code, "stock") is excluded

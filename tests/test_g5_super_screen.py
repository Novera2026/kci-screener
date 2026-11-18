"""Trang 🚀 Siêu cổ phiếu và 🔍 Sàng lọc (rà soát 18/11/2026: I02, I40, I64, I67) — AppTest kín, dữ liệu giả."""
from __future__ import annotations

import datetime as _dt
import glob
import os
import random
from array import array

import pytest
from streamlit.testing.v1 import AppTest

import growth as G
import guard as GD
import screen as SC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPER = next(p for p in glob.glob(os.path.join(ROOT, "pages", "*.py")) if "Sieu_co_phieu" in p)
SCREEN = next(p for p in glob.glob(os.path.join(ROOT, "pages", "*.py")) if "Sang_loc" in p)


def _detail(items=None):
    m = {"meta": {"in_meta": True, "why": "test"}, "stage": "🌱 Sớm", "stage_why": ["gần nền"],
         "crit": {"Giá > MA50": True}, "chart_pass": 7, "rs": 85, "ext_ma50": 5.0, "pct_from_lo": 40.0,
         "rsi14": 60.0, "tight": 0.1, "near_pivot": False, "price": 10000, "pct_from_hi": -5.0}
    rv = {"score": 70, "grade": "B", "grade_txt": "Đáng cân nhắc", "summary": "x",
          "parts": [{"Phần": "Xu hướng", "Điểm": 20, "Diễn giải": "ok"}], "pros": ["a"], "cons": ["b"]}
    return {"m": m, "g": {}, "gc": {}, "rv": rv, "d": None,
            "nm": {"crit": {}, "n7": 0, "pos": 0, "neg": 0, "items": items or []}}


def _scan(tickers, items=None):
    rows = [{"Mã": tk, "Tên": f"Cty {tk}", "Xếp loại": "B · Đáng cân nhắc", "Điểm rà soát": 70, "Vì sao chọn": "a",
             "Rủi ro chính": "b", "Nhãn": "", "Điểm tiềm năng": 70, "Giai đoạn": "🌱 Sớm", "Meta": "✓ test",
             "Biểu đồ /8": 7, "RS": 85, "Tăng trưởng %": 50, "Tin tức %": 50, "DT quý YoY": "+30%",
             "LN quý YoY": "+30%", "Giá": 10000, "Cách MA50 %": 5.0, "Từ đáy 52T %": 40, "Từ đỉnh 52T %": -5.0,
             "RSI": 60, "Nền siết": 0.1, "Gần điểm mua": "", "Ngành": "", "Vốn hóa (억)": 5000} for tk in tickers]
    return {"rows": rows, "details": {tk: _detail(items) for tk in tickers}, "ext_rows": [], "meta_rows": [],
            "ind_rows": [], "meta_ok": True, "at": "10:00 18/11", "n_uni": len(tickers), "n_trend": len(tickers),
            "n_ext": 0, "n_cand": len(tickers), "n_hidden_meta": 0, "hidden_meta": [], "rs_basis": "toàn sàn"}


@pytest.fixture
def toss_spy(monkeypatch):
    import screener
    import toss_api
    calls = {"init": 0, "overlay": 0}
    real_init = toss_api.TossClient.__init__

    def _init(self, *a, **k):
        calls["init"] += 1
        real_init(self, *a, **k)
    monkeypatch.setattr(toss_api.TossClient, "__init__", _init)
    monkeypatch.setattr(screener, "_TOSS_SINGLETON", None)
    monkeypatch.setenv("TOSS_CLIENT_ID", "fake-id-for-test")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "fake-secret-for-test")
    return calls


# ---------------------------------------------------------------------------- I02
def test_super_page_guest_has_no_toss(monkeypatch, toss_spy):
    monkeypatch.setenv("KCI_PUBLIC", "1")
    at = AppTest.from_file(SUPER, default_timeout=120)
    at.session_state["growth_scan"] = _scan(["100001", "100002"])
    at.run()
    assert not at.exception, at.exception
    assert not [c for c in at.checkbox if "Toss" in c.label]
    assert any("Giá & cảnh báo Toss" in i.value for i in at.info)            # lời giải thích + ô 🔒 Chủ app
    assert any("Chủ app" in e.label for e in at.sidebar.expander)
    assert toss_spy["init"] == 0


def test_super_page_owner_toss_runs_once_per_list(monkeypatch, toss_spy):
    monkeypatch.setenv("KCI_PUBLIC", "1")
    monkeypatch.setattr(GD, "_now_utc", lambda: _dt.datetime(2026, 11, 18, 1, 0, tzinfo=_dt.timezone.utc))
    n = {"overlay": 0}

    def fake_overlay(tks):
        n["overlay"] += 1
        return {t: 12345.0 for t in tks}, {}, None
    monkeypatch.setattr(G, "toss_overlay", fake_overlay)
    at = AppTest.from_file(SUPER, default_timeout=120)
    at.session_state["growth_scan"] = _scan(["100001", "100002", "100003"])
    at.session_state["_owner_ok"] = True
    at.run()
    at = next(c for c in at.checkbox if "Toss" in c.label).check().run()
    assert n["overlay"] == 1
    for i in (1, 2):                                        # đổi ô khác trên trang → không gọi lại Toss
        sb = next(s for s in at.selectbox if s.label == "Chọn mã")
        at = sb.select(sb.options[i]).run()
    assert not at.exception, at.exception
    assert n["overlay"] == 1
    main = next(d.value for d in at.dataframe if "Giá Toss" in d.value.columns)
    assert list(main["Giá Toss"]) == [12345.0] * 3


def test_super_page_toss_error_is_redacted(monkeypatch):
    monkeypatch.setenv("KCI_PUBLIC", "0")
    monkeypatch.setattr(G, "toss_overlay", lambda tks: ({}, {}, "RuntimeError: client_secret=SEKRET98765 bad"))
    at = AppTest.from_file(SUPER, default_timeout=120)
    at.session_state["growth_scan"] = _scan(["100001"])
    at.run()
    at = next(c for c in at.checkbox if "Toss" in c.label).check().run()
    w = [x.value for x in at.warning if "Toss chưa dùng được" in x.value]
    assert w and "SEKRET98765" not in w[0]


def test_toss_overlay_reuses_one_client_and_token(monkeypatch, toss_spy):
    import toss_api

    class _R:
        def __init__(self, p):
            self.p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self.p
    hits = {"token": 0}

    def post(url, **k):
        hits["token"] += 1
        return _R({"access_token": "tok", "expires_in": 3600})

    def get(url, headers=None, params=None, timeout=None):
        if url.endswith("/api/v1/prices"):
            return _R({"result": [{"symbol": s, "lastPrice": 1} for s in params["symbols"].split(",")]})
        return _R({"result": []})
    monkeypatch.setattr(toss_api, "requests", type("R", (), {"post": staticmethod(post), "get": staticmethod(get)}))
    monkeypatch.setenv("KCI_PUBLIC", "0")
    for _ in range(3):
        _, _, err = G.toss_overlay(["005930", "000660"])
        assert err is None
    assert toss_spy["init"] == 1 and hits["token"] == 1


def test_toss_token_error_does_not_echo_response(monkeypatch, toss_spy):
    import toss_api

    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"error": "invalid_client", "client_id": "CID-LEAK-424242"}
    monkeypatch.setattr(toss_api, "requests", type("R", (), {"post": staticmethod(lambda *a, **k: _R())}))
    monkeypatch.setenv("KCI_PUBLIC", "0")
    with pytest.raises(RuntimeError) as e:
        toss_api.TossClient()._get_token()
    assert "CID-LEAK-424242" not in str(e.value) and "invalid_client" in str(e.value)


# ---------------------------------------------------------------------------- I67
def test_super_page_news_titles_are_escaped(monkeypatch):
    monkeypatch.setenv("KCI_PUBLIC", "0")
    items = [{"title": "[특징주] ![x](https://t.example/p.png) $1 2~3% **mua**", "url": "javascript:alert(1)",
              "office": "연합_A", "dt": "20261118", "tag": 1},
             {"title": "수주 $1,400 … $10억", "url": "https://n.news.naver.com/a/1", "office": "뉴스", "dt": "20261117",
              "tag": 0}]
    at = AppTest.from_file(SUPER, default_timeout=120)
    at.session_state["growth_scan"] = _scan(["100001"], items)
    at.run()
    assert not at.exception, at.exception
    md = [m.value for m in at.markdown if "특징주" in m.value or "n.news.naver.com" in m.value]
    assert len(md) == 2
    bad, ok = md
    assert "![x](" not in bad and "\\!\\[x\\]" in bad and "\\~" in bad and "\\$" in bad and "\\*\\*" in bad
    assert "javascript" not in bad and "연합\\_A" in bad                    # URL javascript: bị bỏ, chỉ còn tiêu đề
    assert "](https://n.news.naver.com/a/1)" in ok and "\\$1,400" in ok


# ---------------------------------------------------------------------------- I40
def _synth(n, seed=1, start=_dt.date(2025, 1, 2)):
    rnd = random.Random(seed)
    c, out = 10000.0, []
    for _ in range(n):
        c *= 1 + rnd.uniform(-0.02, 0.025)
        out.append(c)
    return {"date": [(start + _dt.timedelta(days=i)).strftime("%Y%m%d") for i in range(n)],
            "open": out, "high": [x * 1.01 for x in out], "low": [x * 0.985 for x in out], "close": out,
            "volume": [1e6 + rnd.randint(0, 500000) for _ in out]}


def test_packed_daily_gives_identical_chart_metrics():
    for seed in (1, 2, 3):
        d = _synth(380, seed)
        p = G.pack_daily(d)
        assert "open" not in p and isinstance(p["close"], array) and p["close"].typecode == "d"
        assert isinstance(p["date"], array) and p["date"][0] == int(d["date"][0])
        assert G.chart_metrics(p) == G.chart_metrics(d)
    assert G.pack_daily({}) == {} and G.chart_metrics(G.pack_daily(_synth(100))) is None


# ---------------------------------------------------------------------------- I64 (🚀) + nến gọn trong trang
@pytest.fixture
def super_feed(monkeypatch):
    import financials
    mode, calls = {"m": "empty"}, {"d": 0}

    def fake_daily(sym, cal_days=560):
        calls["d"] += 1
        if mode["m"] == "empty":
            return {}
        return _synth(120 if mode["m"] == "short" else 300, seed=int(sym[-2:]))
    monkeypatch.setattr(G, "fetch_daily", fake_daily)
    monkeypatch.setattr(G, "fetch_news", lambda *a, **k: [])
    monkeypatch.setattr(financials, "fetch_financials", lambda *a, **k: {})
    monkeypatch.setenv("KCI_PUBLIC", "0")
    return mode, calls


def _run_manual(tickers):
    at = AppTest.from_file(SUPER, default_timeout=120).run()
    at = at.radio[0].set_value("Nhập tay").run()
    at = at.text_area[0].set_value("\n".join(tickers)).run()
    return next(b for b in at.button if "Tìm siêu cổ phiếu" in b.label).click().run()


def test_super_page_naver_failure_is_reported_not_hidden(super_feed):
    mode, calls = super_feed
    tks = ["900111", "900112", "900113"]
    at = _run_manual(tks)
    assert not at.exception, at.exception
    assert any("Không lấy được nến từ Naver" in e.value for e in at.error)
    assert not any("Không mã nào thỏa" in i.value for i in at.info)       # không khuyên "nới bộ lọc" khi lỗi nguồn
    # Naver hồi phục: cùng mã, cùng ngày → phải tải lại (trước đây {} bị nhớ cả ngày)
    mode["m"], calls["d"] = "ok", 0
    at = _run_manual(tks)
    assert calls["d"] == 3
    assert not any("Không lấy được nến" in e.value for e in at.error)
    res = at.session_state["growth_scan"]
    assert res["n_fail"] == 0
    for dt_ in res["details"].values():                                    # nến giữ ở dạng gọn
        assert isinstance(dt_["d"]["close"], array) and "open" not in dt_["d"]


def test_super_page_short_history_is_explained(super_feed):
    mode, _ = super_feed
    mode["m"] = "short"
    at = _run_manual(["900121", "900122"])
    assert not at.exception, at.exception
    assert any("chưa đủ ~1 năm" in i.value for i in at.info)
    assert at.session_state["growth_scan"]["n_short"] == 2


# ---------------------------------------------------------------------------- I64 (🔍)
def _nodata(tk):
    return {"ticker": tk, "name": tk, "price": None, "per": None, "pbr": None, "eps": None, "bps": None,
            "div_yield": None, "market_cap": None, "industry_kr": "식품", "fetch_ok": False, "note": "err"}


def _good(tk, pe=6.0, pb=0.5):
    return {"ticker": tk, "name": tk, "price": 10000.0, "per": pe, "pbr": pb, "eps": 10000.0 / pe,
            "bps": 10000.0 / pb, "div_yield": 4.0, "market_cap": 5e12, "industry_kr": "식품", "fetch_ok": True}


def test_apply_preset_names_data_failure_instead_of_fake_reasons():
    rows = [_good("A"), _good("B", 7.0, 0.6), _nodata("C"), dict(_good("D"), fetch_ok=False, per=None)]
    for k in SC.PRESETS:
        out = {x["Mã"]: x for x in SC.apply_preset(rows, k)}
        for tk in ("C", "D"):
            assert not out[tk]["Qua lọc"] and out[tk]["Lý do loại"] == "không lấy được dữ liệu", (k, out[tk])


@pytest.fixture
def screen_feed(monkeypatch):
    import screener
    feed = {"rows": []}
    monkeypatch.setattr(screener, "fetch_many", lambda qs, *a, **k: ([dict(r) for r in feed["rows"]], []))
    monkeypatch.setattr(screener, "rf_for_valuation", lambda: {"value": 0.0325, "live": True, "note": ""})
    monkeypatch.setattr(SC, "deep_stage", lambda *a, **k: [])
    monkeypatch.setenv("KCI_PUBLIC", "0")
    return feed


def _run_screen():
    at = AppTest.from_file(SCREEN, default_timeout=120).run()
    at = at.radio[0].set_value("Nhập tay").run()
    return next(b for b in at.button if "Chạy sàng lọc" in b.label).click().run()


def test_screen_page_all_rows_failed_is_an_error(screen_feed):
    screen_feed["rows"] = [_nodata("005930"), _nodata("000660"), _nodata("105560")]
    at = _run_screen()
    assert not at.exception, at.exception
    assert any("Không lấy được dữ liệu thị trường từ Naver" in e.value for e in at.error)
    assert "sc" not in at.session_state
    assert not any(m.label == "Qua tầng 1" for m in at.metric)


def test_screen_page_partial_failure_warns(screen_feed):
    screen_feed["rows"] = [_good("005930"), _nodata("000660"), _nodata("105560")]
    at = _run_screen()
    assert not at.exception, at.exception
    assert any("2/3 mã không lấy được dữ liệu" in w.value for w in at.warning)
    st1 = {r["Mã"]: r for r in at.session_state["sc"]["st1"]}
    assert st1["000660"]["Lý do loại"] == "không lấy được dữ liệu"


def test_screen_deep_cap_is_shared_public_constant():
    assert GD.MAX_DEEP_PUBLIC == 30
    src = open(SCREEN, encoding="utf-8").read()
    assert "MAX_DEEP = GD.MAX_DEEP_PUBLIC" in src

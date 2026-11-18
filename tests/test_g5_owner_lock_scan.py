"""C6 (rà soát 18/11/2026): nguồn riêng của chủ app (Toss, KRX/pykrx, nhật ký định giá) bị chặn ở TẦNG CLIENT, và một
lượt quét mọi trang ở chế độ công khai — tick mọi ô, bấm mọi nút — không được chạm vào các nguồn đó khi chưa mở khóa.

Kín: nguồn thị trường / BCTC / định giá sâu thay bằng bản giả nhanh; TossClient, pykrx, log_append thay bằng bẫy.
"""
from __future__ import annotations

import glob
import os
import sys
import types

import pytest
from streamlit.testing.v1 import AppTest

import guard as GD

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Trang chủ chỉ chạy được qua app.py (st.navigation) — là trang mặc định nên app.py phủ nó
SCREENS = ([os.path.join(ROOT, "app.py")]
           + sorted(p for p in glob.glob(os.path.join(ROOT, "views", "*.py")) if "trang_chu" not in p)
           + sorted(glob.glob(os.path.join(ROOT, "pages", "*.py"))))
SKIP_BUTTONS = {"Mở khóa", "Khóa lại"}           # ô mật khẩu chủ app (không có mật khẩu trong test)


# ---------------------------------------------------------------------------- bẫy
class _Trap:
    def __init__(self):
        self.hits: list[str] = []

    def hit(self, what):
        self.hits.append(what)
        raise RuntimeError(f"BẪY: {what} bị gọi khi chưa mở khóa")


def _fake_pykrx(trap: _Trap):
    mod = types.ModuleType("pykrx")

    class _Stock(types.ModuleType):
        def __getattr__(self, name):
            if name.startswith("__"):             # inspect / pickle dò thuộc tính đặc biệt — không phải gọi pykrx
                raise AttributeError(name)
            trap.hit(f"pykrx.stock.{name}")
    mod.stock = _Stock("pykrx.stock")
    return mod


@pytest.fixture
def trap(monkeypatch):
    import pipeline as P
    import screener
    import toss_api
    t = _Trap()
    fake = _fake_pykrx(t)
    monkeypatch.setitem(sys.modules, "pykrx", fake)
    monkeypatch.setitem(sys.modules, "pykrx.stock", fake.stock)
    monkeypatch.setattr(toss_api.TossClient, "__init__", lambda self, *a, **k: t.hit("TossClient()"))
    monkeypatch.setattr(P, "log_append", lambda *a, **k: t.hit("log_append"))
    monkeypatch.setattr(screener, "_TOSS_SINGLETON", None)
    return t


def _fake_fin(shares=5e8):
    out, eq = [], 60000.0
    for i in range(5):
        rev = 50000.0 * 1.04 ** i
        np_ = rev * 0.125
        eq += np_ * 0.6
        out.append({"period": f"{2021 + i}12", "label": f"{2021 + i}.12.", "is_consensus": False, "revenue": rev,
                    "op_profit": rev * 0.15, "net_profit": np_, "ctrl_net": np_, "equity": eq,
                    "eps": round(np_ * 1e8 / shares), "bps": round(eq * 1e8 / shares), "dps": 400.0,
                    "roe": np_ / eq * 100, "debt_ratio": 80.0})
    qs = [{"period": p, "label": p, "is_consensus": False, "revenue": 13500.0, "op_profit": 2000.0,
           "net_profit": 1700.0, "ctrl_net": 1700.0} for p in ("202512", "202603", "202606", "202609")]
    return {"annual": out, "quarter": qs}


@pytest.fixture
def fast_data(monkeypatch, fake_market):
    """Nguồn nặng → bản giả trả nhanh (không mạng)."""
    import bot_hunt as BH
    import financials
    import growth
    import screen as SC
    import screener

    def rows_for(qs, *a, **k):
        out = []
        for q in qs:
            tk = str(q).zfill(6)[:6]
            out.append({"ticker": tk, "name": tk, "price": 10000.0, "per": 8.0, "pbr": 0.7, "eps": 1250.0,
                        "bps": 14000.0, "div_yield": 3.0, "market_cap": 5e12, "industry_kr": "식품", "fetch_ok": True,
                        "source": "naver", "note": ""})
        return out, []
    monkeypatch.setattr(screener, "fetch_many", rows_for)
    monkeypatch.setattr(growth, "fetch_daily", lambda *a, **k: {})
    monkeypatch.setattr(growth, "universe_by_cap", lambda *a, **k: [])
    monkeypatch.setattr(growth, "fetch_news", lambda *a, **k: [])
    # BCTC giả hợp lệ (không phải {}): luồng 🧮 mở từ Trang chủ phải đi tới kết quả, không dừng ở st.error 'Không lấy
    # được BCTC từ Naver' (rà soát cuối 18/11/2026 — trước đây lượt quét dừng ở đó mà test vẫn xanh)
    monkeypatch.setattr(financials, "fetch_financials", lambda *a, **k: _fake_fin())
    import dart_api
    monkeypatch.setattr(dart_api, "api_key", lambda: None)        # trang có thể nạp .env → không dùng khóa thật
    monkeypatch.setattr(SC, "top_by_cap", lambda *a, **k: ["005930", "000660"])
    monkeypatch.setattr(SC, "deep_stage", lambda *a, **k: [])
    import datetime as _dt
    monkeypatch.setattr(BH, "hunt", lambda presets, **k: {
        "picks": [], "results": {}, "hits": {}, "rows": [], "errored": [],
        "meta": {"at": _dt.datetime(2026, 11, 18, 16, 0), "universe": 0, "scope": k.get("scope", ""),
                 "presets": presets, "n_deep": 0, "rf": 0.03, "rf_live": True, "rf_note": ""}})


# lỗi đã biết của AppTest khi trang gọi st.switch_page (không phải lỗi app)
_KNOWN_EXC = ("url_pathname",)
_KNOWN_ERR = ("Chưa nhập mã nào",)              # bấm 'Phân tích' khi ô danh sách trống — đúng hành vi


def _ok(at: AppTest, step: str) -> AppTest:
    """Sau MỖI lần chạy: trang không được văng ngoại lệ — trang gãy giữa chừng thì các nhánh sau không được quét mà test
    vẫn xanh (rà soát cuối 18/11/2026)."""
    bad = [e.value for e in at.exception if not any(k in str(e.value) for k in _KNOWN_EXC)]
    assert not bad, (step, bad)
    errs = [e.value for e in at.error if not any(k in str(e.value) for k in _KNOWN_ERR)]
    assert not errs, (step, errs)                  # st.error = luồng dừng giữa chừng (dữ liệu giả thiếu / lỗi trang)
    return at


def _sweep(at: AppTest, max_clicks: int = 25) -> AppTest:
    """Tick mọi ô, bấm mọi nút (theo ảnh chụp danh sách nút của lần chạy đầu)."""
    _ok(at, "mở trang")
    for cb in list(at.checkbox):
        if not cb.value:
            at = _ok(cb.check().run(), f"ô {cb.label}")
    seen = []
    for b in list(at.button)[:max_clicks]:
        if b.label in SKIP_BUTTONS:
            continue
        seen.append((b.label, b.key))
    seen.sort(key=lambda x: "Mở trong" in x[0])   # nút chuyển trang bấm sau cùng (sau đó các nút khác không còn)
    for label, key in seen:
        cur = next((x for x in at.button if x.key == key and x.label == label), None) if key else \
            next((x for x in at.button if x.label == label), None)
        if cur is None:
            continue
        at = _ok(cur.click().run(), f"nút {label}")
        for cb in list(at.checkbox):        # nút có thể mở thêm ô mới
            if not cb.value:
                at = _ok(cb.check().run(), f"ô {cb.label}")
    return at


@pytest.mark.parametrize("path", SCREENS, ids=[os.path.relpath(p, ROOT) for p in SCREENS])
def test_public_sweep_never_touches_owner_sources(path, monkeypatch, trap, fast_data):
    from test_g5_super_screen import _scan
    monkeypatch.setenv("KCI_PUBLIC", "1")
    at = AppTest.from_file(path, default_timeout=120)
    at.session_state["growth_scan"] = _scan(["100001", "100002"])     # 🚀 có bảng kết quả → tới chỗ ô Toss
    at = _sweep(at.run())
    assert not trap.hits, trap.hits
    # tùy chọn nguồn riêng (toss / pykrx) không hiện cho khách
    for w in list(at.selectbox) + list(at.radio):
        opts = [str(o).lower() for o in (w.options or [])]
        assert not any(o in ("toss", "pykrx") for o in opts), (w.label, opts)


# ---------------------------------------------------------------------------- tầng client
def test_toss_client_blocks_guest_on_cloud(monkeypatch):
    import screener
    import toss_api
    monkeypatch.setenv("KCI_PUBLIC", "1")
    monkeypatch.setattr(GD, "owner_ok", lambda: False)
    monkeypatch.setattr(screener, "_TOSS_SINGLETON", None)
    with pytest.raises(PermissionError):
        toss_api.TossClient(client_id="fake-id", client_secret="fake-secret")
    with pytest.raises(PermissionError):
        screener._toss_client()
    # bản đã tạo sẵn (phiên chủ app) cũng không cho khách dùng lại
    monkeypatch.setattr(screener, "_TOSS_SINGLETON", object())
    with pytest.raises(PermissionError):
        screener._toss_client()
    monkeypatch.setenv("KCI_PUBLIC", "0")                                  # máy của anh: mở như cũ
    assert toss_api.TossClient(client_id="fake-id", client_secret="fake-secret").client_id == "fake-id"


def test_pykrx_blocked_for_guest_and_fetch_one_falls_back(monkeypatch, trap):
    import screener as S
    monkeypatch.setenv("KCI_PUBLIC", "1")
    monkeypatch.setattr(GD, "owner_ok", lambda: False)
    with pytest.raises(PermissionError):
        S._fetch_pykrx("005930")
    monkeypatch.setitem(S._FETCHERS, "naver", lambda tk: S.StockRow(ticker=tk, source="naver", price=1.0, per=5.0,
                                                                     market_cap=1e12))
    r = S.fetch_one("005930", prefer="pykrx", live=False)
    assert r.price == 1.0 and r.source == "naver" and "PermissionError" in r.note
    assert not trap.hits


def test_krx_name_lookup_skipped_for_guest(monkeypatch, trap):
    import screener as S
    reg = S.TickerRegistry.__new__(S.TickerRegistry)
    reg._krx_loaded, reg._krx_name_map = False, {}
    monkeypatch.setenv("KCI_PUBLIC", "1")
    monkeypatch.setattr(GD, "owner_ok", lambda: False)
    reg._load_krx_names()                                                  # khách: bỏ qua, không lỗi
    assert not trap.hits and reg._krx_name_map == {} and not reg._krx_loaded
    monkeypatch.setenv("KCI_PUBLIC", "0")

    def _once(fn, n=1, w=0):
        try:
            return fn()
        except Exception:
            return None
    monkeypatch.setattr(S, "_retry", _once)                                # không chờ thử lại
    reg._load_krx_names()                                                  # chủ app / máy của anh: có tra
    assert trap.hits and trap.hits[0].startswith("pykrx.stock.")


# ---------------------------------------------------------------------------- quyền chủ app trong luồng phụ
def _owner_fetch_app():
    import streamlit as st
    import screener as S

    class _Reg:
        def resolve_info(self, q):
            return {"query": q, "ticker": q, "how": "ticker", "alts": []}

        def name_of(self, tk):
            return tk

        def sector_of(self, tk):
            return None

        def industry_of(self, tk):
            return "식품"
    rows, _ = S.fetch_many(["005930"], registry=_Reg(), prefer=st.session_state["_prefer"], max_workers=2,
                           retries=0, live=False)
    st.session_state["_out"] = (rows[0]["price"], rows[0].get("note") or "", rows[0].get("source") or "")


@pytest.mark.parametrize("owner,prefer", [(True, "toss"), (False, "toss"), (True, "pykrx"), (False, "pykrx")])
def test_owner_sources_work_in_fetch_many_threads(owner, prefer, monkeypatch):
    """fetch_many chạy mỗi mã trong luồng phụ: chủ app đã mở khóa (KCI_PUBLIC=1) vẫn phải dùng được Toss / KRX —
    trước đây luồng phụ không có phiên Streamlit nên owner_ok() thấy 'khách' và lặng lẽ chuyển sang Naver."""
    import screener as S
    import toss_api
    monkeypatch.setenv("KCI_PUBLIC", "1")
    monkeypatch.setenv("TOSS_CLIENT_ID", "fake-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "fake-secret")
    monkeypatch.setattr(S, "_TOSS_SINGLETON", None)
    monkeypatch.setattr(toss_api.TossClient, "get_price", lambda self, tk: {"price": 99999.0})
    monkeypatch.setitem(S._FETCHERS, "naver",
                        lambda tk: S.StockRow(ticker=tk, source="naver", price=100.0, per=10.0, market_cap=1e12))

    def _krx(tk):                                                        # chỉ thay phần gọi KRX, giữ cổng chủ app
        GD.require_owner("Nguồn KRX (pykrx)")
        return S.StockRow(ticker=tk, source="pykrx", price=77777.0, per=9.0, market_cap=2e12)
    monkeypatch.setitem(S._FETCHERS, "pykrx", _krx)
    at = AppTest.from_function(_owner_fetch_app, default_timeout=30)
    at.session_state["_prefer"] = prefer
    if owner:
        at.session_state["_owner_ok"] = True
    at.run()
    assert not at.exception, at.exception
    price, note, src = at.session_state["_out"]
    if owner:
        assert price == {"toss": 99999.0, "pykrx": 77777.0}[prefer] and "PermissionError" not in note, (price, note)
        assert src.startswith(prefer)
    else:                                                                # khách: bị chặn, rơi về Naver, ghi rõ lý do
        assert price == 100.0 and "PermissionError" in note and src == "naver", (price, note, src)

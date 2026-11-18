"""'Du hành thời gian' và 'mã khó' trên dữ liệu ghi sẵn (rà soát 18/11/2026, cải tiến C4).

Lỗi lịch chỉ lộ vài tuần mỗi năm (I01, I13, I14) và lỗi sửa bảng cần thao tác xóa ô / gõ chữ (I09, I12, I26). Ở đây:
- Dữ liệu Naver thật ghi ngày 18/11/2026 + BCTC năm/quý DART đã lưu (tests/fixtures/g9b) cho 12 mã: thường, ưu đãi,
  BCTC USD (241560), bán dẫn, hàng không, viễn thông, ngân hàng, bio, REIT, holding, mã lỗ, mới niêm yết.
- Đồng hồ (guard) và `today` của định giá đặt lần lượt vào các ngày quanh hạn nộp báo cáo năm (31/3).
- Bảng ④ / ⑦ ở trang 🧮 nhận ô trống, '12,5', '2.000', số cực trị.
"""
from __future__ import annotations

import datetime as dt
import os

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import dart_api as D
import g9b_fakenet as FN
import guard as GD
import pipeline as P
import reporting as R
import valuation as V

PAGES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pages")
VAL_PAGE = next(os.path.join(PAGES, f) for f in sorted(os.listdir(PAGES)) if "Dinh_gia" in f)
TICKERS = FN.TICKERS
BANK = "105560"
# dữ liệu là của 18/11/2026 (BCTC năm thực gần nhất 2025): trước 1/4/2027 không được ❌ 'quá cũ'; từ 1/4/2027 BCTC
# năm 2026 đã quá hạn nộp mà dữ liệu vẫn dừng ở 2025 → PHẢI ❌ (cổng không được nới)
DATES = [dt.date(2026, 12, 30), dt.date(2027, 1, 2), dt.date(2027, 2, 15), dt.date(2027, 3, 20),
         dt.date(2027, 4, 5), dt.date(2027, 5, 20), dt.date(2027, 8, 20)]
_DATA: dict = {}


def _market(tk):
    """Dòng thị trường + BCTC Naver + beta của mã (lấy qua mạng giả, nhớ trong module — dữ liệu ghi sẵn, cố định)."""
    if tk not in _DATA:
        import financials as F
        import screener as S
        row = S.fetch_one(tk, registry=S.TickerRegistry(), prefer="naver").to_dict()
        fin = F.fetch_financials(tk)
        beta = S.beta_vs_market(S.fetch_weekly_closes(tk), S.fetch_weekly_closes("KOSPI"))
        _DATA[tk] = (row, fin, beta)
    row, fin, beta = _DATA[tk]
    return dict(row), {k: [dict(c) for c in v] if isinstance(v, list) else v for k, v in fin.items()}, beta


@pytest.fixture
def world(monkeypatch, tmp_path):
    net = FN.install(monkeypatch)
    FN.dart_on(monkeypatch, tmp_path, year_fixtures=True, q_fixtures=True)
    return net


def _assess(net, monkeypatch, tk, day):
    FN.freeze(monkeypatch, day)
    row, fin, beta = _market(tk)
    net.reset()
    monkeypatch.setattr(D, "_YEAR_CACHE", {})
    ctx = P.assess(tk, row, fin, V.Assumptions(rf=0.0325), [], beta, today=day,
                   dart_quarters=P.cached_dart_quarters(tk))
    return ctx


@pytest.mark.parametrize("day", DATES, ids=[d.isoformat() for d in DATES])
def test_calendar_gates_over_the_year(world, monkeypatch, day):
    exp = R.expected_fy(day)
    for tk in TICKERS:
        ctx = _assess(world, monkeypatch, tk, day)                       # không văng với mọi loại mã
        rec = ctx["rec"]
        assert rec["fair"] is None or rec["fair"] > 0, tk
        chk = next((c for c in ctx["dq"]["checks"] if c["Kiểm tra"] == "Năm BCTC thực gần nhất"), None)
        assert chk is not None, tk
        y_last = int(chk["Giá trị"])
        if y_last >= exp:                                                 # chưa tới hạn nộp năm kế → không ❌
            assert chk["Kết quả"] != P.FAIL, (tk, chk)
        else:                                                             # đã quá hạn mà dữ liệu cũ → ❌ (cổng giữ)
            assert chk["Kết quả"] == P.FAIL and "quá cũ" in chk["Ghi chú"], (tk, chk)
            assert rec["confidence"] == "Low", tk
        # DART: không bao giờ hỏi kỳ chưa kết thúc
        bad = [(c.get("bsns_year"), c.get("reprt_code")) for c in world.dart
               if R.state(int(c["bsns_year"]), c["reprt_code"], day) == "not_ended"]
        assert not bad, (tk, bad)
    if day < dt.date(2027, 4, 1):
        assert exp == 2025
    else:
        assert exp == 2026


@pytest.mark.parametrize("day", DATES, ids=[d.isoformat() for d in DATES])
@pytest.mark.parametrize("tk", ["005930", "000660", BANK, "207940", "030200"])
def test_forecast_starts_after_base_year_from_year_end_bps(world, monkeypatch, tk, day):
    ctx = _assess(world, monkeypatch, tk, day)
    reals = [c for c in ctx["series"] if not c.get("is_consensus")]
    base = reals[-1]
    assert str(base["period"]).endswith("12")                             # năm gốc = năm tài chính, không phải quý
    drivers = ctx["drivers"]
    assert drivers, f"{tk}: không có dự phóng"                            # ngân hàng luôn có dự phóng (I14)
    assert int(str(drivers[0]["Năm"])[:4]) == int(str(base["period"])[:4]) + 1
    fc = ctx["E"]["fc"]
    assert fc
    # B0 = BPS cuối năm tài chính gần nhất, KHÔNG phải BPS cuối quý của Naver (đã gồm LN các quý năm 1)
    assert fc[0]["BPS đầu kỳ"] == pytest.approx(base["bps"]) if base.get("bps") else fc[0]["BPS đầu kỳ"] > 0
    if base.get("bps") and ctx["row"].get("bps"):
        assert fc[0]["BPS đầu kỳ"] != pytest.approx(ctx["row"]["bps"], rel=1e-6)


def test_bank_has_forecast_and_published_value(world, monkeypatch):
    for day in DATES:
        ctx = _assess(world, monkeypatch, BANK, day)
        assert ctx["idn"]["financial"] and ctx["drivers"] and ctx["E"]["fc"], day


def test_usd_statements_not_read_as_krw(world, monkeypatch):
    """241560 nộp BCTC 2023–2025 bằng USD (DART): không trộn vào chuỗi KRW — dùng số Naver (đã quy KRW)."""
    ctx = _assess(world, monkeypatch, "241560", dt.date(2026, 11, 18))
    _, fin, _ = _market("241560")
    nav = {c["period"]: c.get("revenue") for c in fin["annual"]}
    usd = [c for c in ctx["series"] if (c.get("_src") or {}).get("dart") == "currency"]
    assert {c["period"] for c in usd} >= {"202312", "202412", "202512"}
    for c in usd:
        assert c["revenue"] == nav[c["period"]]


# ---------------------------------------------------------------------------- DART theo lịch Hàn Quốc
def test_annual_financials_follows_kst_calendar(world, monkeypatch):
    import time
    y = time.localtime().tm_year
    day = dt.date(y + 1, 5, 20)                     # năm tài chính y đã quá hạn nộp theo lịch guard
    FN.freeze(monkeypatch, day)
    D.annual_financials("005930", years=4)
    asked = {int(c["bsns_year"]) for c in world.dart if c.get("reprt_code") == "11011"}
    assert y in asked                                # phải hỏi BCTC năm y (đã quá hạn nộp)


# ---------------------------------------------------------------------------- sửa bảng ở 🧮
def _num_or(x):
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return None


@pytest.fixture
def page(monkeypatch, tmp_path, fake_market):
    """Trang 🧮 cho 005930 trên dữ liệu ghi sẵn; st.data_editor bọc lại để 'gõ' vào ô (AppTest không lái được)."""
    FN.offline_pages(monkeypatch, tmp_path)
    edits, calls = {}, []
    orig = st.data_editor

    def _de(data, *a, **k):
        calls.append((k.get("key") or "", data))
        out = orig(data, *a, **k)
        for prefix, fn in edits.items():
            if (k.get("key") or "").startswith(prefix):
                out = fn(out.copy())
        return out
    monkeypatch.setattr(st, "data_editor", _de)
    at = AppTest.from_file(VAL_PAGE, default_timeout=120)
    for k in ("vp_ticker", "vp_query", "vp_query_box"):
        at.session_state[k] = "005930"
    at.edits, at.calls = edits, calls
    return at


def _ok(at):
    assert not at.exception, [e.value for e in at.exception]
    return at


def _fair(at):
    return {m.label: m.value for m in at.sidebar.metric}["Giá trị hợp lý"]


def _set(col, val, row=0):
    def fn(df):
        df[col] = df[col].astype(object)
        df.loc[row, col] = val
        return df
    return fn


@pytest.mark.parametrize("val", [None, float("nan"), "", "abc"], ids=["None", "NaN", "rỗng", "chữ"])
def test_forecast_blank_or_text_cell_keeps_default_fair(page, val):
    fair0 = _fair(_ok(page.run()))
    page.edits["drv_"] = _set("Biên LN ròng %", val)
    _ok(page.run())
    assert _fair(page) == fair0                       # ô trống / không phải số → điền lại mặc định, không coi là 0
    assert any("điền lại" in w.value for w in page.warning)


@pytest.mark.parametrize("val", ["12,5", "2.000", 1e9, -1e9, 1e-12], ids=["12,5", "2.000", "1e9", "-1e9", "1e-12"])
def test_forecast_odd_numbers_never_crash_and_gate_holds(page, val):
    _ok(page.run())
    price = _num_or({m.label: m.value for m in page.sidebar.metric}["Giá"])
    page.edits["drv_"] = _set("Biên LN ròng %", val)
    _ok(page.run())
    f = _fair(page)
    if f not in ("—", ""):
        assert 0 < _num_or(f) < 20 * price            # biên cực trị bị cổng số chặn, không ra giá trị phi lý
    if isinstance(val, float) and abs(val) >= 1e9:
        assert any("vi phạm" in m.value for m in page.markdown)       # cổng dự phóng báo ❌


@pytest.mark.parametrize("col,val", [("Xác suất", None), ("× biên LN", float("nan")), ("Δ tăng trưởng (điểm %)", "")])
def test_scenario_blank_cell_uses_default(page, col, val):
    _ok(page.run())
    exp0 = [m.value for m in page.metric if m.label.startswith("Giá trị kỳ vọng")]
    page.edits["sc_"] = _set(col, val, row=1)
    _ok(page.run())
    assert [m.value for m in page.metric if m.label.startswith("Giá trị kỳ vọng")] == exp0
    assert any("ô trống ở bảng kịch bản" in w.value for w in page.warning)


def test_blank_driver_fails_forecast_gate_in_pipeline(world, monkeypatch):
    """Ở tầng pipeline (không qua trang): ô giả định trống → fg['ok'] False; giá trị công bố KHÔNG lấy từ RIM/DDM."""
    ctx = _assess(world, monkeypatch, "005930", dt.date(2026, 11, 18))
    drv = [dict(d) for d in ctx["drivers"]]
    drv[0]["Biên LN ròng %"] = float("nan")
    E = P.evaluate(ctx["D"], V.Assumptions(rf=0.0325), drv, ctx["idn"], ctx["dq"], ctx["evidence"])
    assert ctx["E"]["fg"]["ok"] and not E["fg"]["ok"]
    assert any("Biên LN ròng" in c["Kiểm tra"] and "trống" in str(c["Giá trị"]) and c["Kết quả"] == P.FAIL
               for c in E["fg"]["checks"])
    used = [r["name"] for r in E["rec"]["rows"] if r.get("pass") and r.get("in_fair", True)]
    assert not any(n.startswith(("RIM tường minh", "DDM tường minh")) for n in used) or E["rec"]["fair"] is None
    assert GD.today_kst() == dt.date(2026, 11, 18)

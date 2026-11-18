"""Trang 🧮 với dữ liệu giả (không mạng): ô nhập của người dùng, cơ sở cổ phiếu, hiệu năng, quyền ghi nhật ký."""
import os

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import pipeline as P
from tests.test_pipeline import _row, _series

PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pages", "3_🧮_Dinh_gia.py")
TK = "000001"


class _Reg:
    def resolve(self, q):
        return TK

    def industry_of(self, tk):
        return "식품"

    def sector_of(self, tk):
        return None


class _Row(dict):
    def to_dict(self):
        return dict(self)


@pytest.fixture
def page(monkeypatch, tmp_path):
    """AppTest trang 🧮 cho mã giả 000001 — mọi nguồn Naver/DART/FRED bị thay bằng số cố định."""
    import analysis as AN
    import dart_api
    import dossier as DS
    import financials
    import peers as PR
    import screener

    s = _series()
    row = _row(s)
    row.update(ticker=TK, source="test")
    monkeypatch.setenv("KCI_PUBLIC", "0")
    monkeypatch.setattr(screener, "TickerRegistry", _Reg)
    monkeypatch.setattr(screener, "fetch_one", lambda *a, **k: _Row(row))
    monkeypatch.setattr(screener, "fetch_many", lambda *a, **k: ([], []))
    monkeypatch.setattr(screener, "fetch_weekly_closes", lambda *a, **k: [])
    monkeypatch.setattr(screener, "beta_vs_market", lambda *a, **k: 1.0)
    monkeypatch.setattr(screener, "rf_for_valuation",
                        lambda: {"value": 0.03, "live": True, "source": "test", "asof": "", "note": ""})
    monkeypatch.setattr(financials, "fetch_financials", lambda tk: {"annual": s, "quarter": []})
    monkeypatch.setattr(financials, "build_annual_series", lambda *a, **k: [dict(c) for c in s])
    monkeypatch.setattr(dart_api, "valuation_extras", lambda *a, **k: {})
    monkeypatch.setattr(dart_api, "available", lambda: False)
    monkeypatch.setattr(AN, "fetch_full", lambda *a, **k: None)
    monkeypatch.setattr(AN, "fetch_quarter_reports", lambda *a, **k: [])
    monkeypatch.setattr(PR, "domestic_candidates", lambda *a, **k: [])
    monkeypatch.setattr(PR, "fetch_foreign", lambda *a, **k: [])
    saved = []
    monkeypatch.setattr(DS, "save_section", lambda tk, sec, data, *a, **k: saved.append(sec))
    # st.data_editor: AppTest không lái được → sửa ô bằng hàm trong edits[tiền tố key]; ghi lại tham số mỗi lần gọi
    edits, editor_calls = {}, []
    _orig_de = st.data_editor

    def _de(data, *a, **k):
        editor_calls.append((k.get("key") or "", data, k.get("column_config")))
        out = _orig_de(data, *a, **k)
        for prefix, fn in edits.items():
            if (k.get("key") or "").startswith(prefix):
                out = fn(out.copy())
        return out
    monkeypatch.setattr(st, "data_editor", _de)
    logs = []
    monkeypatch.setattr(P, "log_append", lambda path, entry: logs.append(entry))
    st.cache_data.clear()
    st.cache_resource.clear()
    at = AppTest.from_file(PAGE, default_timeout=120)
    for k in ("vp_ticker", "vp_query", "vp_query_box"):
        at.session_state[k] = TK
    at.g2 = {"saved": saved, "edits": edits, "editor_calls": editor_calls, "logs": logs, "row": row}
    yield at
    st.cache_data.clear()
    st.cache_resource.clear()


def _ok(at):
    assert not at.exception, [e.value for e in at.exception]
    return at


def _fair(at):
    return {m.label: m.value for m in at.sidebar.metric}["Giá trị hợp lý"]


def _hdr(at):
    return [c.value for c in at.sidebar.caption if "giả định mặc định" in c.value or "Đã chỉnh" in c.value][0]


def test_page_runs_offline_with_default_label(page):
    _ok(page.run())
    assert _fair(page) not in ("—", "")
    assert _hdr(page).startswith("✅ Đang dùng giả định mặc định KCI")


# ---------------------------------------------------------------- I12 / I68 bảng dự phóng ④
def _set(col, val, row=0):
    def fn(df):
        df.loc[row, col] = val
        return df
    return fn


def test_blank_forecast_cell_refilled_from_default(page):
    fair0 = _fair(_ok(page.run()))
    page.g2["edits"]["drv_"] = _set("Biên LN ròng %", None)
    _ok(page.run())
    assert _fair(page) == fair0                                   # trước đây EPS 0 → số khác vẫn công bố
    assert any("điền lại" in w.value for w in page.warning)


def test_edited_forecast_cell_is_labelled_everywhere(page):
    _ok(page.run())
    drv = [c for c in page.g2["editor_calls"] if c[0].startswith("drv_")][-1][1]
    page.g2["edits"]["drv_"] = _set("Biên LN ròng %", float(drv.loc[0, "Biên LN ròng %"]) * 0.5)
    _ok(page.run())
    assert _hdr(page).startswith("✏️ Đã chỉnh 1 ô dự phóng")
    assert any("Mặc định KCI" in c.value for c in page.sidebar.caption)
    assert any("Đã sửa tay" in m.value for m in page.markdown)
    reset = [b for b in page.sidebar.button if "Về mặc định KCI" in b.label][0]
    assert not reset.disabled
    [b for b in page.button if "Lưu vào nhật ký" in b.label][0].click().run()
    assert page.g2["logs"] and "1 ô dự phóng" in page.g2["logs"][-1]["Đã sửa tay"]


def test_blank_refilled_cell_is_not_labelled_as_edit(page):
    """Ô trống đã điền lại mặc định → số = chuẩn: không gắn '✏️ Đã chỉnh … khác giả định mặc định'."""
    _ok(page.run())
    page.g2["edits"]["drv_"] = _set("Biên LN ròng %", None)
    _ok(page.run())
    assert _hdr(page).startswith("✅ Đang dùng giả định mặc định KCI")
    assert not any("Đã sửa tay" in m.value for m in page.markdown)
    reset = [b for b in page.sidebar.button if "Về mặc định KCI" in b.label][0]
    assert not reset.disabled                                     # vẫn xóa được trạng thái bảng


def test_scenario_only_edit_caption_not_false(page):
    """Chỉ sửa bảng kịch bản ⑦: GTHL không đổi → không được ghi 'Mặc định KCI: không công bố'."""
    fair0 = _fair(_ok(page.run()))
    page.g2["edits"]["sc_"] = _set("Δ tăng trưởng (điểm %)", -4.0)
    _ok(page.run())
    assert "1 ô kịch bản" in _hdr(page)
    assert _fair(page) == fair0
    assert not any("không công bố" in c.value for c in page.sidebar.caption)


def test_default_fair_error_not_shown_as_unpublished(page, monkeypatch):
    """Lần evaluate phụ (mặc định KCI) lỗi → ghi 'không tính lại được', không phải 'không công bố'."""
    _ok(page.run())
    drv = [c for c in page.g2["editor_calls"] if c[0].startswith("drv_")][-1][1]
    page.g2["edits"]["drv_"] = _set("Biên LN ròng %", float(drv.loc[0, "Biên LN ròng %"]) * 0.5)
    import sys
    _orig = P.default_drivers

    def _dd(*a, **k):
        if sys._getframe(1).f_globals.get("__name__") != "pipeline":   # chỉ lần gọi từ trang (tính lại mặc định)
            raise RuntimeError("x")
        return _orig(*a, **k)
    monkeypatch.setattr(P, "default_drivers", _dd)
    _ok(page.run())
    caps = [c.value for c in page.sidebar.caption if "Mặc định KCI" in c.value]
    assert caps and all("không công bố" not in c for c in caps)


# ---------------------------------------------------------------- I26 bảng kịch bản ⑦
def test_scenario_cells_blank_zero_and_config(page, monkeypatch):
    specs = []
    _orig = P.scenarios
    monkeypatch.setattr(P, "scenarios", lambda *a, **k: specs.append(a[6]) or _orig(*a, **k))

    def fn(df):
        df.loc[0, "× biên LN"] = None                             # Bear: xóa ô
        df.loc[1, "× biên LN"] = 0.0                              # Base: 0 vẫn là 0
        df.loc[2, "Δ tăng trưởng (điểm %)"] = 0.0
        return df
    page.g2["edits"]["sc_"] = fn
    _ok(page.run())
    spec = specs[-1]
    assert spec["Bear"]["mm"] == P.SCENARIOS["Bear"]["mm"]        # trước đây NaN → biên 0
    assert spec["Base"]["mm"] == 0.0                              # trước đây 0 → 1
    assert spec["Bull"]["dg"] == 0.0
    assert any("ô trống ở bảng kịch bản" in w.value for w in page.warning)
    cfg = [c for c in page.g2["editor_calls"] if c[0].startswith("sc_")][-1][2]
    assert cfg and {"Δ tăng trưởng (điểm %)", "× biên LN", "Xác suất"} <= set(cfg)
    assert "ô kịch bản" in _hdr(page)


def test_scenario_probability_25_not_published(page):
    page.g2["edits"]["sc_"] = _set("Xác suất", 25.0)
    _ok(page.run())
    exp = [m.value for m in page.metric if m.label.startswith("Giá trị kỳ vọng")]
    assert exp == ["—"]
    assert any("Σ xác suất" in w.value for w in page.warning)


# ---------------------------------------------------------------- I09 / I46 rNPV, EV/GWh
_ASSET = {"Tên": "A", "Pha": "Pha 3", "Doanh số đỉnh (억)": 7000.0, "Năm ra mắt": 2028, "PoS %": None,
          "Biên LN %": 30.0, "Chi phí PT còn lại (억)": 2000.0}


def _rnpv_page(page, monkeypatch, rows):
    import sector_models as S
    calls = []
    _orig = S.rnpv
    monkeypatch.setattr(S, "rnpv", lambda *a, **k: calls.append(a) or _orig(*a, **k))
    _ok(page.run())
    page.g2["edits"]["rn_"] = lambda df: pd.DataFrame(rows)
    page.selectbox(key=f"spec_mode_{TK}").set_value("rnpv").run()
    return _ok(page), calls


def test_rnpv_table_columns_are_numeric(page, monkeypatch):
    _rnpv_page(page, monkeypatch, [])
    _, data, cfg = [c for c in page.g2["editor_calls"] if c[0].startswith("rn_")][-1]
    for c in ("Doanh số đỉnh (억)", "Năm ra mắt", "PoS %", "Biên LN %", "Chi phí PT còn lại (억)"):
        assert pd.api.types.is_numeric_dtype(data[c]), c          # trước đây object → ô chữ
        assert c in cfg
    for c in ("Doanh số đỉnh (억)", "Năm ra mắt", "Biên LN %", "Chi phí PT còn lại (억)"):
        assert cfg[c]["required"], c


def test_rnpv_far_launch_year_blocked_not_crash(page, monkeypatch):
    at, calls = _rnpv_page(page, monkeypatch, [{**_ASSET, "Năm ra mắt": 12026}])   # trước đây OverflowError
    assert calls
    assert not any("rNPV" in m.value and "KRW/cp" in m.value for m in at.caption)


def test_rnpv_row_without_name_warns(page, monkeypatch):
    at, _ = _rnpv_page(page, monkeypatch, [_ASSET, {**_ASSET, "Tên": None}])
    assert any("chưa có **Tên**" in w.value for w in at.warning)


def test_rnpv_divides_by_share_basis_not_common_shares(page, monkeypatch):
    row = page.g2["row"]
    row["market_cap"] = row["price"] * 0.9e7                      # cp thường 9 triệu; cơ sở EPS 10 triệu
    _, calls = _rnpv_page(page, monkeypatch, [_ASSET])
    assert calls and calls[-1][2] == pytest.approx(1e7)           # trước đây vốn hóa ÷ giá = 9e6


def test_capacity_uses_share_basis(page, monkeypatch):
    import sector_models as S
    row = page.g2["row"]
    row["market_cap"] = row["price"] * 0.9e7
    ev_calls, imp_calls = [], []
    _ev, _imp = S.ev_per_gwh_value, S.implied_ev_per_gwh
    monkeypatch.setattr(S, "ev_per_gwh_value", lambda *a, **k: ev_calls.append(a) or _ev(*a, **k))
    monkeypatch.setattr(S, "implied_ev_per_gwh", lambda *a, **k: imp_calls.append((a, k)) or _imp(*a, **k))
    _ok(page.run())
    page.selectbox(key=f"spec_mode_{TK}").set_value("capacity").run()
    page.number_input(key=f"g0_{TK}").set_value(100.0)
    page.number_input(key=f"gN_{TK}").set_value(120.0)
    page.number_input(key=f"evg_{TK}").set_value(10.0).run()
    _ok(page)
    assert imp_calls and imp_calls[-1][1].get("shares") == pytest.approx(1e7)
    assert ev_calls and ev_calls[-1][3] == pytest.approx(1e7)    # trước đây vốn hóa ÷ giá = 9e6


# ---------------------------------------------------------------- I39 hiệu năng chạy lại
def test_rerun_does_not_build_pdf_or_xlsx(page, monkeypatch):
    import report_pdf
    n = {"pdf": 0, "xlsx": 0}
    _rp = report_pdf.report_pdf
    monkeypatch.setattr(report_pdf, "report_pdf", lambda *a, **k: n.__setitem__("pdf", n["pdf"] + 1) or _rp(*a, **k))
    _ew = pd.ExcelWriter

    def _cnt(*a, **k):
        n["xlsx"] += 1
        return _ew(*a, **k)
    monkeypatch.setattr(pd, "ExcelWriter", _cnt)
    _ok(page.run())
    n_saved = len(page.g2["saved"])
    for _ in range(3):
        _ok(page.run())
    assert n == {"pdf": 0, "xlsx": 0}
    labels = [b.proto.label for b in page.get("download_button")]
    assert any(".pdf" in x for x in labels) and any(".xlsx" in x for x in labels)
    assert len(page.g2["saved"]) == n_saved                       # hồ sơ không ghi lại khi nội dung không đổi


# ---------------------------------------------------------------- I65 quyền ghi nhật ký
def test_log_button_checks_owner_on_server(page, monkeypatch):
    monkeypatch.setenv("KCI_PUBLIC", "1")
    _ok(page.run())
    assert _fair(page) not in ("—", "")
    btn = [b for b in page.button if "Lưu vào nhật ký" in b.label][0]
    assert btn.disabled
    btn.click().run()                                             # AppTest bỏ qua disabled = client tự gửi lệnh bấm
    assert page.g2["logs"] == []
    page.session_state["_owner_ok"] = True
    _ok(page.run())
    [b for b in page.button if "Lưu vào nhật ký" in b.label][0].click().run()
    assert len(page.g2["logs"]) == 1

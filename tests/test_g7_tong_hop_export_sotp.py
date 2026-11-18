"""Định giá tổng hợp: Excel mang đúng kết luận trên màn hình (I25) và bảng SOTP không sập/không đoán mờ (I27).
Chạy offline: không gọi Naver/DART/KRX."""
import ast
import io
import os

import pandas as pd
import pytest

import screener as S
import sotp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEW = os.path.join(ROOT, "views", "tong_hop.py")


# ---- I25: Excel ----
def _export(**kw):
    df = pd.DataFrame([{"ticker": "005930", "name": "Samsung", "price": 190000.0, "per": 12.0}])
    agg = pd.DataFrame([{"sector": "IT", "n": 1}])
    val = pd.DataFrame([{"ticker": "005930", "fair_value": 189809.0, "upside_%": -0.1,
                         "confidence": "Low", "status": "△ tam giác hóa",
                         "fwd_fair": 200000.0, "fwd_upside_%": 5.0, "fair_raw": 189000.0}])
    buf = io.BytesIO()
    S.export_excel(df, agg, buf, val=val, **kw)
    buf.seek(0)
    return pd.read_excel(buf, sheet_name=None)


def test_excel_conclusion_sheet_first_and_matches_screen():
    vsum = pd.DataFrame([{"Mã": "005930", "Tên": "Samsung", "Giá": 190000.0,
                          "Giá trị hợp lý": 170674.0, "Chênh %": -10.1, "Độ tin cậy": "Medium",
                          "Trạng thái": "✓ công bố", "Lõi KCI (tham khảo)": 189809.0}])
    book = _export(conclusion=vsum)
    assert list(book)[0] == "Định giá"
    dg = book["Định giá"]
    assert list(dg.columns) == list(vsum.columns)
    assert dg["Giá trị hợp lý"].tolist() == [170674.0]
    assert dg["Trạng thái"].tolist() == ["✓ công bố"]


def test_excel_valuation_sheet_marks_kci_core_columns():
    book = _export()
    v = book["Valuation"]
    assert "fair_value" not in v.columns and "upside_%" not in v.columns
    assert "confidence" not in v.columns and "status" not in v.columns
    assert v["loi_kci_fair_value"].tolist() == [189809.0]
    assert {"loi_kci_upside_%", "loi_kci_confidence", "loi_kci_status",
            "loi_kci_fwd_fair", "loi_kci_fwd_upside_%", "loi_kci_fair_raw"} <= set(v.columns)
    assert not {"fwd_fair", "fwd_upside_%", "fair_raw"} & set(v.columns)
    assert "Định giá" not in book            # không truyền conclusion → không có sheet rỗng


def test_tong_hop_download_passes_conclusion_table():
    """Nút ⬇️ Tải Excel phải truyền bảng kết luận (vsum) — trước đây chỉ có val=vdf (lõi KCI)."""
    tree = ast.parse(open(VIEW, encoding="utf-8").read())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "export_excel"]
    assert calls
    for c in calls:
        kw = {k.arg: k.value for k in c.keywords}
        assert "conclusion" in kw and getattr(kw["conclusion"], "id", "") == "vsum"


# ---- I27: SOTP ----
def _row(name="", code="", stake=float("nan"), manual=float("nan")):
    return {sotp.COL_NAME: name, sotp.COL_CODE: code, sotp.COL_STAKE: stake, sotp.COL_MANUAL: manual}


def _no_mktcap(q):
    raise AssertionError("không được tra vốn hóa")


@pytest.mark.parametrize("bad", ["5,000", "5000억", "abc"])
def test_sotp_manual_text_warns_instead_of_crash(bad):
    parts, warns = sotp.parse_sotp_rows(pd.DataFrame([_row("Mảng A", manual=bad)]), _no_mktcap)
    assert parts[0]["Giá trị (억)"] is None
    assert len(warns) == 1 and "không đọc được" in warns[0] and bad in warns[0]


def test_sotp_manual_number_and_blank_rows():
    rows = pd.DataFrame([_row("Mảng A", manual=5000.0), _row(), _row("Nợ ròng", manual=-1200.0)])
    parts, warns = sotp.parse_sotp_rows(rows, _no_mktcap)
    assert [p["Giá trị (억)"] for p in parts] == [5000.0, -1200.0]
    assert warns == []


def test_sotp_code_with_blank_stake_warns():
    parts, warns = sotp.parse_sotp_rows(pd.DataFrame([_row("SK hynix", "000660")]), _no_mktcap)
    assert parts[0]["Giá trị (억)"] is None
    assert warns and "% sở hữu trống" in warns[0]


def test_sotp_listed_value_and_matched_column():
    fn = lambda q: {"ticker": "000660", "name": "SK hynix", "how": "ticker", "mc": 1_000_000}
    parts, warns = sotp.parse_sotp_rows(pd.DataFrame([_row("SKH", "000660", 20.0)]), fn)
    assert parts[0]["Giá trị (억)"] == 200_000 and parts[0]["Đã khớp"] == "000660 SK hynix"
    assert warns == []


def test_sotp_unsure_match_is_rejected():
    fn = lambda q: {"ticker": None, "how": "fuzzy", "query": q}
    parts, warns = sotp.parse_sotp_rows(pd.DataFrame([_row("", "SK On", 100.0)]), fn)
    assert parts[0]["Giá trị (억)"] is None
    assert warns and "không nhận diện chắc chắn" in warns[0]


@pytest.fixture(scope="module")
def reg():
    r = S.TickerRegistry()
    r._krx_loaded = True            # không gọi pykrx/KRX trong test offline
    return r


@pytest.mark.parametrize("q", ["SK On", "Samsung Display", "LG Display"])
def test_resolve_sure_rejects_fuzzy_names(reg, q):
    """Trước đây 'SK On' → 000660 (SK hynix), 'Samsung Display' → 000810 … do đoán mờ."""
    info = reg.resolve_info(q)
    assert info["how"] not in sotp.SURE_HOW
    assert sotp.resolve_sure(reg, q)["ticker"] is None


def test_resolve_sure_accepts_ticker_and_exact(reg):
    assert sotp.resolve_sure(reg, "000660")["ticker"] == "000660"
    assert sotp.resolve_sure(reg, "삼성전자")["ticker"] == "005930"


# ---- I27: nối dây trong views/tong_hop.py (chạy đúng code của trang, giả lập st/Naver) ----
def test_sotp_block_uses_parse_sotp_rows():
    tree = ast.parse(open(VIEW, encoding="utf-8").read())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "parse_sotp_rows"]
    assert calls, "khối SOTP phải đọc bảng qua sotp.parse_sotp_rows"
    assert [a.id for a in calls[0].args] == ["edited", "_mktcap_eok"]


@pytest.fixture
def view(reg, monkeypatch):
    """Nạp _memo_retry/_mktcap_eok THẬT từ views/tong_hop.py với st, Naver, fetch_one giả."""
    import time
    from types import SimpleNamespace
    calls = {"naver": 0, "fetch": 0}
    clock = {"t": 1000.0}

    def _nn(code):
        calls["naver"] += 1
        return {"005935": "삼성전자우"}.get(code)

    class _Row:
        def __init__(self, t):
            self.t = t

        def to_dict(self):
            return {"market_cap": {"005935": 5e13, "000660": 7.7e14}.get(self.t), "name": "N"}

    def _fo(tk, registry=None, prefer=None):
        calls["fetch"] += 1
        return _Row(tk)

    monkeypatch.setattr(S, "_naver_name", _nn)
    monkeypatch.setattr(S, "fetch_one", _fo)
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])
    src = open(VIEW, encoding="utf-8").read()
    ns = {"st": SimpleNamespace(session_state={}), "reg": reg}
    for node in ast.parse(src).body:
        if (isinstance(node, ast.FunctionDef) and node.name in ("_run_memo", "_memo_retry", "_mktcap_eok")) or (
                isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_SOTP_RETRY_S"):
            exec(ast.get_source_segment(src, node), ns)
    return SimpleNamespace(fn=ns["_mktcap_eok"], calls=calls, clock=clock)


def test_mktcap_eok_rejects_fuzzy_name_without_fetch(view):
    """'SK On' trước đây → 000660 (SK hynix) và lấy vốn hóa 7,7 triệu 억."""
    out = view.fn("SK On")
    assert out["ticker"] is None and view.calls["fetch"] == 0


def test_mktcap_eok_reruns_do_not_refetch(view):
    """Trang rerun mỗi lần kéo slider: nhận diện mã ưu đãi (Naver) và vốn hóa chỉ tra 1 lần trong lần chạy."""
    for _ in range(5):
        out = view.fn("삼성전자우")
    assert out["ticker"] == "005935" and out["mc"] == 500000
    assert view.calls == {"naver": 1, "fetch": 1}


def test_mktcap_eok_failure_retried_after_delay_not_every_rerun(view):
    for _ in range(5):
        out = view.fn("999999")
    assert out["mc"] is None and view.calls["fetch"] == 1      # lỗi giữ tạm, không gọi lại mỗi rerun
    view.clock["t"] += 61
    view.fn("999999")
    assert view.calls["fetch"] == 2                            # nhưng không giữ lỗi cả lần chạy

"""Backtest point-in-time trên số DART (không cần mạng: DART/giá/Rf được giả lập)."""
import datetime as dt

import pytest

import backtest_dart as B


# ---- đọc BCTC DART ----
def test_statements_parse_ifrs_ids_old_prefix_and_eps_by_name():
    j = {"list": [
        {"sj_div": "CIS", "account_id": "ifrs-full_Revenue", "thstrm_amount": "1,000"},
        {"sj_div": "CIS", "account_id": "dart_OperatingIncomeLoss", "thstrm_amount": "100"},
        {"sj_div": "BS", "account_id": "ifrs_EquityAttributableToOwnersOfParent",
         "thstrm_amount": "5,000"},                                 # mã cũ trước 2018
        {"sj_div": "IS", "account_id": "-표준계정코드 미사용-",
         "account_nm": "희석주당이익", "thstrm_amount": "9"},          # pha loãng → bỏ
        {"sj_div": "IS", "account_id": "-표준계정코드 미사용-",
         "account_nm": "기본주당이익", "thstrm_amount": "10"},
    ]}
    out = B._statements(j)
    assert out == {"revenue": 1000.0, "op_profit": 100.0, "equity_parent": 5000.0, "eps": 10.0}


@pytest.mark.parametrize("rows, expected", [
    ([("의결권 있는\n보통주", "512,759,471", "512,753,119"), ("의결권 없는\n우선주", "-", "-"),
      ("합계", "512,759,471", "512,753,119")], 512_753_119),      # Shinhan: lấy dòng 합계
    ([("의결권 있는 주식", "217,078,554", "205,030,873"), ("합계", "-", "-")], 205_030_873),
    ([("보통주", "100", "90"), ("우선주", "20", "10")], 100),         # không có 합계 → cộng
])
def test_share_count_label_variants(monkeypatch, rows, expected):
    def fake(path, **kw):
        if path.startswith("stockTotqy"):
            return {"status": "000", "list": [{"se": se, "istc_totqy": a, "distb_stock_co": b}
                                              for se, a, b in rows]}
        return {"status": "000", "list": [{"se": "주당 현금배당금(원)", "stock_knd": "보통주",
                                            "thstrm": "1,000"}]}
    monkeypatch.setattr(B, "_dart_get", fake)
    rec = B._add_shares({}, "X", 2023)
    assert rec["shares_out"] == expected and rec["dps"] == 1000.0


def test_split_factor():
    assert B.split_factor(1e6, 1.2e6) == 1.0          # mua lại/phát hành nhỏ
    assert B.split_factor(5e6, 1e6) == 5.0            # chia tách 1:5
    assert B.split_factor(1e6, 5e6) == pytest.approx(0.2)
    assert B.split_factor(3.3e6, 1e6) is None         # đổi mạnh, không tròn → bỏ điểm


def test_market_helpers():
    s = [("20240412", 100.0), ("20240415", 101.0), ("20240416", 102.0)]
    assert B.close_on_or_after(s, "20240413") == ("20240415", 101.0)
    assert B.close_on_or_after(s, "20240501") is None
    assert B.add_months("20240131", 1) == "20240228"
    assert B.rf_at([("2024-03-01", 0.03), ("2024-04-01", 0.035)], "20240415") == 0.03
    daily = [(d.strftime("%Y%m%d"), float(i)) for i, d in
             enumerate(dt.date(2024, 1, 1) + dt.timedelta(days=k) for k in range(30))]
    assert B.weekly_before(daily, "20240115") == [6.0, 13.0]   # CN 7/1 và 14/1


def test_evaluate_perfect_signal():
    vals = [{"year": y, "upside": i / 10, "excess": i / 100, "ret": i / 100 - 0.4,
             "method_upsides": {}} for y in (2022, 2023) for i in range(10)]
    e = B.evaluate(vals)
    assert e["ic_by_year"] == {2022: 1.0, 2023: 1.0} and e["ic_pooled"] == pytest.approx(1.0)
    q = {r["q"]: r for r in e["quintiles"]}
    assert q[1]["median_excess"] > q[5]["median_excess"] and q[5]["loss30"] == 1.0


# ---- chạy end-to-end với dữ liệu giả lập ----
def _fs(i):
    out = {}
    for y in range(2015, 2024):
        rev = 1e12 * (1.05 + 0.01 * i) ** (y - 2015)
        ni = rev * (0.05 + 0.005 * i)
        out[y] = {"year": y, "fs_div": "CFS", "revenue": rev, "op_profit": rev * 0.1,
                  "ni": ni, "ni_parent": ni, "equity_parent": 8e12 + ni,
                  "eps": ni / 1e8, "shares_out": 1e8, "dps": 300.0}
    return out


def _prices(g):
    d0 = dt.date(2019, 1, 1)
    return [((d0 + dt.timedelta(days=k)).strftime("%Y%m%d"), 50_000 * (1 + g) ** (k / 365))
            for k in range(0, 365 * 6)]


def test_run_backtest_offline(monkeypatch):
    tks = [f"{i:06d}" for i in range(1, 11)]
    fss = {t: _fs(i) for i, t in enumerate(tks)}
    monkeypatch.setattr(B, "annual", lambda t, y, full=True, cache_dir=None:
                        fss[t].get(y, {"year": y, "status": "013"}))
    monkeypatch.setattr(B, "price_history", lambda sym, start="", cache_dir=None:
                        _prices(0.0) if sym == "KOSPI" else _prices(0.02 * int(sym)))
    monkeypatch.setattr(B, "fred_rf", lambda cache_dir=None: [("2020-01-01", 0.02)])
    steps = []
    df, res = B.run_backtest_dart(tks, years=(2021, 2023), progress=lambda s, x: steps.append(x))
    m = res["meta"]
    assert m["points"] == 30 and m["points_with_return"] == 30 and not m["no_points"]
    assert set(df["year"]) == {2021, 2022, 2023} and df["rf"].eq(0.02).all()
    assert res["auto"]["n"] == 30 and res["forecast"]["table"]
    assert steps[-1] == 1.0
    assert "Backtest point-in-time" in B.report_markdown(res)

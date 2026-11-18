"""Parser số liệu gốc chạy trên phản hồi THẬT đã ghi sẵn (rà soát 18/11/2026, I43).

Trước đây mọi test thay _fetch_naver, financials._parse, fetch_weekly_closes, corp_code_of, valuation_extras,
_industry_ranked… bằng bản giả → 0% dòng của chúng chạy trong CI, đột biến phá số (net_debt đảo dấu, EPS↔BPS,
is_consensus đảo, corp_code thành tên, giá tuần đảo thứ tự, xếp ngành đảo chiều) vẫn xanh. Ở đây chỉ thay
requests (tests/g9b_fakenet.py): fixture Naver 18/11/2026 và DART fnlttSinglAcntAll năm 2025 (không chứa khóa).
"""
from __future__ import annotations

import json
import os
import re

import pytest

import dart_api as D
import financials as F
import g9b_fakenet as FN
import peers as PR
import pipeline as P
import screener as S

NAVER = FN.NAVER


def _j(name):
    with open(os.path.join(NAVER, name), encoding="utf-8") as f:
        return json.load(f)


def _won(s: str) -> float:
    """'1,112조 5,468억' → won (cách đọc độc lập với screener._parse_kr_won)."""
    units = {"조": 1e12, "억": 1e8}
    return sum(float(n.replace(",", "")) * units[u] for n, u in re.findall(r"([\d,]+)(조|억)", s))


def _num(s) -> float:
    return float(re.sub(r"[^\d.\-]", "", str(s)))


@pytest.fixture
def net(monkeypatch, tmp_path):
    n = FN.install(monkeypatch)
    FN.freeze(monkeypatch, __import__("datetime").date(2026, 11, 18))
    return n


@pytest.fixture
def dart(net, monkeypatch, tmp_path):
    """DART 'có khóa' (khóa giả, không phải hex) + danh mục corp_code mẫu; lượt gọi đi qua FakeNet."""
    FN.dart_on(monkeypatch, tmp_path)
    return net


# ---------------------------------------------------------------------------- Naver integration
def test_fetch_naver_units_and_fields(net):
    raw = _j("integration_005930.json")
    tot = {d["code"]: d["value"] for d in raw["totalInfos"]}
    row = S._fetch_naver("005930")
    assert row.fetch_ok and row.name == "삼성전자" and row.industry_code == raw["industryCode"]
    assert row.price == _num(raw["dealTrendInfos"][0]["closePrice"])          # đóng cửa phiên trước (tạm)
    assert row.eps == _num(tot["eps"]) and row.bps == _num(tot["bps"])
    assert row.per == _num(tot["per"]) and row.pbr == _num(tot["pbr"])
    assert row.market_cap == pytest.approx(_won(tot["marketValue"]))          # KRW, không phải 억
    px = _num(tot["lastClosePrice"])
    # chéo: EPS × P/E ≈ giá, BPS × P/B ≈ giá (bắt EPS↔BPS đổi chỗ, sai đơn vị)
    assert row.eps * row.per == pytest.approx(px, rel=0.03)
    assert row.bps * row.pbr == pytest.approx(px, rel=0.03)
    assert 5e9 < row.market_cap / px < 7e9                                      # ~5,9 tỷ cp Samsung
    assert row.net_income == pytest.approx(row.market_cap / row.per)


def test_fetch_one_overlays_realtime_price(net):
    rt = {d["itemCode"]: _num(d["closePrice"]) for d in _j("realtime.json")["datas"]}
    row = S.fetch_one("005930", prefer="naver")
    assert row.price == rt["005930"] and row.source == "naver+rt"
    rows, bad = S.fetch_many(["005930", "105560", "330590"], prefer="naver")
    assert not bad and [r["ticker"] for r in rows] == ["005930", "105560", "330590"]      # đúng thứ tự nhập
    assert [r["price"] for r in rows] == [rt["005930"], rt["105560"], rt["330590"]]
    assert all(r["fetch_ok"] for r in rows)


def test_naver_error_is_marked_not_loss(net, monkeypatch):
    monkeypatch.setattr(net, "route", lambda m, u, p: (503, {"err": 1}))
    row = S._fetch_naver("005930")
    assert not row.fetch_ok and row.per is None and "fail" in row.note


# ---------------------------------------------------------------------------- BCTC Naver
@pytest.mark.parametrize("tk", ["005930", "105560", "462870"])
def test_financials_parse_consensus_and_order(net, tk):
    out = F.fetch_financials(tk)
    assert "_error" not in out
    for ptype in ("annual", "quarter"):
        raw = _j(f"finance_{ptype}_{tk}.json")["financeInfo"]
        titles = raw["trTitleList"]
        cols = out[ptype]
        assert [c["period"] for c in cols] == [t["key"] for t in titles]
        assert [c["period"] for c in cols] == sorted(c["period"] for c in cols)       # cũ → mới
        assert [c["is_consensus"] for c in cols] == [t["isConsensus"] == "Y" for t in titles]
        eps_row = next(r["columns"] for r in raw["rowList"] if r["title"] == "EPS")
        for c in cols:
            v = (eps_row.get(c["period"]) or {}).get("value")
            assert c["eps"] == (None if v in (None, "", "-") else _num(v))
        la = F.latest_actual(cols)
        assert la["period"] == [t["key"] for t in titles if t["isConsensus"] == "N"][-1]
        assert not la["is_consensus"]


def test_financials_5930_values(net):
    a = {c["period"]: c for c in F.fetch_financials("005930")["annual"]}
    assert a["202512"]["eps"] == 6564 and a["202412"]["eps"] == 4950
    assert a["202612"]["is_consensus"] and not a["202512"]["is_consensus"]
    assert F.latest_actual(list(a.values()))["period"] == "202512"
    assert a["202512"]["bps"] > a["202512"]["eps"] > 0                       # BPS, EPS không đổi chỗ


# ---------------------------------------------------------------------------- giá tuần, Rf, chỉ số
def test_weekly_closes_old_to_new(net):
    rows = json.loads(open(os.path.join(NAVER, "sise_week_005930.txt"), encoding="utf-8").read().replace("'", '"'))
    closes = S.fetch_weekly_closes("005930")
    assert closes == [float(r[4]) for r in rows[1:]]
    assert rows[1][0] < rows[-1][0]                                          # file: ngày tăng dần
    assert closes[0] == float(rows[1][4]) and closes[-1] == float(rows[-1][4])
    k = S.fetch_weekly_closes("KOSPI")
    assert len(k) >= 30 and S.beta_vs_market(closes, k) is not None


def test_rf_from_naver_bond(net):
    raw = _j("bond_KR10YT.json")
    d = S.fetch_kgb10y_detail()
    assert d["live"] and d["value"] == pytest.approx(_num(raw["closePrice"]) / 100)
    assert 0.01 < d["value"] < 0.08


def test_market_indices_parse(net):
    import megatrend as MT
    raw = _j("index_KOSPI.json")
    got = MT.market_indices(("KOSPI",))
    assert got and got[0]["price"] == _num(raw["closePrice"])
    sign = -1 if (raw.get("compareToPreviousPrice") or {}).get("name") in ("FALLING", "LOWER_LIMIT") else 1
    assert got[0]["chg"] == pytest.approx(sign * abs(_num(raw["fluctuationsRatio"])))


# ---------------------------------------------------------------------------- ngành, đối thủ
def test_industry_ranked_by_market_cap_desc(net):
    P._IND_NAMES.clear()
    ranked = P._industry_ranked("반도체와반도체장비")
    raw = [s for s in _j("industry_278.json")["stocks"] if s.get("stockEndType") == "stock"]
    cap = {str(s["itemCode"]).zfill(6): float(s.get("marketValueRaw") or 0) for s in raw}
    assert ranked and ranked[0] == max((t for t in cap if t.endswith("0")), key=cap.get)
    caps = [cap[t] for t in ranked]
    assert caps == sorted(caps, reverse=True)
    assert all(t.endswith("0") for t in ranked)                              # bỏ cổ phiếu ưu đãi
    assert {"005930", "000660"} <= set(ranked[:3])
    assert P._industry_ranked("ngành không có") == []
    assert P.industry_name("278") == "반도체와반도체장비"


def test_industry_ranked_error_is_none(net, monkeypatch):
    monkeypatch.setattr(net, "route", lambda m, u, p: (503, {"groups": []}))
    assert P._industry_ranked("은행") is None                                # lỗi ≠ 'ngành không có'


def test_naver_domestic_peers(net):
    raw = _j("integration_005930.json")
    want = [str(x["itemCode"]).zfill(6) for x in raw["industryCompareInfo"]
            if x.get("stockType") == "domestic" and str(x["itemCode"]).zfill(6) != "005930"]
    assert PR.naver_domestic_peers("005930") == want and "000660" in want


def test_foreign_profile_krw(net):
    b, a = _j("world_basic_TSM.json"), _j("world_annual_TSM.json")
    p = PR.foreign_profile("TSM")
    assert p["Tên"] == b["stockName"]
    assert p["Vốn hóa (tỷ KRW)"] == pytest.approx(float(b["marketValueKrwRaw"]) / 1e9)
    info = {t["code"]: t["value"] for t in b["stockItemTotalInfos"]}
    assert p["P/E"] == pytest.approx(_num(info["per"]))
    years = [t["key"] for t in a["trTitleList"] if t.get("isConsensus") == "N"]
    assert p["Năm BCTC"] == years[-1][:4]
    assert PR.foreign_profile("XXXX") is None                                 # Naver không có (404)


# ---------------------------------------------------------------------------- DART
def test_corp_code_of_reads_code_not_name(dart):
    m = D._read_corp_csv(os.path.join(FN.FIX, "dart", "corp_sample.csv"))
    assert m["005930"] == "00126380" and m["105560"] == "00688996"
    assert D.corp_code_of("5930") == "00126380"
    assert D.corp_code_of("105560") == "00688996"
    assert D.corp_code_of("999990") is None


def test_valuation_extras_net_debt_is_debt_minus_cash(dart):
    raw = json.load(open(os.path.join(FN.FIX, "dart", "fnltt_00126380_2025_11011_CFS.json"), encoding="utf-8"))
    items = raw["list"]
    cash = next(float(i["thstrm_amount"]) for i in items if i["account_id"] == "ifrs-full_CashAndCashEquivalents")
    ebit = next(float(i["thstrm_amount"]) for i in items if i["account_id"] == "dart_OperatingIncomeLoss")
    ex = D.valuation_extras("005930", year=2025)
    assert ex["year"] == 2025 and ex["fs_div"] == "CFS" and ex["currency"] == "KRW"
    assert ex["cash"] == pytest.approx(cash / 1e8) and ex["ebit"] == pytest.approx(ebit / 1e8)     # đơn vị 억
    assert ex["debt"] > 0 and ex["cash"] > ex["debt"]                         # Samsung tiền ròng
    assert ex["net_debt"] < 0 and abs(ex["net_debt"] - (ex["debt"] - ex["cash"])) <= 1
    assert 0 <= ex["tax_rate"] <= 0.4
    n = dart.count("opendart")
    assert D.valuation_extras("005930", year=2025) == ex                      # lần 2 từ cache đĩa
    assert dart.count("opendart") == n


def test_annual_report_parse_bank(dart):
    raw = json.load(open(os.path.join(FN.FIX, "dart", "fnltt_00688996_2025_11011_CFS.json"), encoding="utf-8"))
    eq = next(float(i["thstrm_amount"]) for i in raw["list"]
              if i["sj_div"] == "BS" and i["account_id"] == "ifrs-full_Equity")
    rec = D._fetch_year_raw("00688996", 2025)
    assert rec["period"] == "202512" and rec["fs_div"] == "CFS" and rec["currency"] == "KRW"
    assert rec["equity"] == pytest.approx(eq / 1e8)
    assert rec["assets"] > rec["liabilities"] > rec["equity"] > 0             # ngân hàng: đòn bẩy cao
    assert rec["net_profit"] > 0
    st = D.fetch_statements("00688996", 2024, "11011", "CFS")                  # không có fixture → 013
    assert st is None


def test_quarterly_financials_order_and_no_open_period(dart, monkeypatch):
    import datetime as dt

    import reporting as R
    route = dart.route
    fn = os.path.join(FN.FIX, "dart", "fnltt_00126380_2025_11011_CFS.json")

    def route2(m, u, p):          # quý 2026 (Q3, bán niên) của 005930: dùng lại danh sách tài khoản năm 2025
        if "fnlttSinglAcntAll" in u and p.get("bsns_year") == "2026" and p.get("reprt_code") in ("11014", "11012"):
            route(m, u, p)                                                   # vẫn ghi lượt gọi
            return 200, open(fn, encoding="utf-8").read()
        return route(m, u, p)
    monkeypatch.setattr(dart, "route", route2)
    got = D.quarterly_financials("005930", max_reports=2)
    assert [g["period"] for g in got] == ["2026-반기", "2026-Q3"]                # cũ → mới
    assert got[0]["revenue"] > 0 and not got[0]["is_consensus"]
    asked = [(int(c["bsns_year"]), c["reprt_code"]) for c in dart.dart]
    assert asked and all(R.state(y, rc, dt.date(2026, 11, 18)) != "not_ended" for y, rc in asked)
    assert ("2026", "11011") not in {(c["bsns_year"], c["reprt_code"]) for c in dart.dart}   # năm 2026 chưa hết

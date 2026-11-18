"""Bộ đối thủ không chứa công ty cùng tập đoàn / holding (I21) và holding phi tài chính vào nhóm Holdco (I57) —
rà soát 18/11/2026. Không cần mạng: danh sách ngành và gợi ý Naver được thay bằng số giả."""
import pandas as pd
import pytest
import requests

import peers as PR
import pipeline as P
import screener
import valuation as V

A = V.Assumptions(rf=0.03, erp=0.05, g_terminal=0.02)
TODAY = pd.Timestamp("2026-11-18").date()


@pytest.fixture
def no_net(monkeypatch):
    def _blocked(*a, **k):
        raise requests.ConnectionError("test: mạng bị chặn")
    monkeypatch.setattr(requests, "get", _blocked)
    monkeypatch.setattr(requests.Session, "request", _blocked)


@pytest.fixture(scope="module")
def reg():
    return screener.TickerRegistry()          # danh mục thật (tickers.csv + tickers_all.csv), đọc file, không mạng


class _Reg:
    def __init__(self, rows):
        self.df = pd.DataFrame(rows, columns=["ticker", "name_kr", "sector", "industry_kr", "group"])


def _world(monkeypatch, ranked, sug=()):
    monkeypatch.setattr(P, "_industry_ranked", lambda ik: list(ranked))
    monkeypatch.setattr(PR, "naver_domestic_peers", lambda tk: list(sug))


# ============================================================================ I21 — cùng tập đoàn
def test_same_group_peer_is_dropped_and_set_stays_full(no_net, monkeypatch):
    others = [f"1{i:04d}0" for i in range(20)]
    rows = [("000660", "SK하이닉스", "Semiconductor", "반도체와반도체장비", "SK"),
            ("402340", "SK스퀘어", None, "반도체와반도체장비", "SK")] + \
           [(t, f"회사{t}", None, "반도체와반도체장비", None) for t in others]
    _world(monkeypatch, ["000660", "402340"] + others, sug=["402340"])
    dropped: list = []
    out = PR.domestic_candidates("000660", "반도체와반도체장비", _Reg(rows), 12, dropped=dropped)
    assert "402340" not in out and len(out) == 12
    assert dropped == [{"Mã": "402340", "Tên": "SK스퀘어", "Lý do": "cùng tập đoàn SK"}]
    # không truyền dropped vẫn chạy (chữ ký cũ)
    assert PR.domestic_candidates("000660", "반도체와반도체장비", _Reg(rows), 12) == out


@pytest.mark.parametrize("tk,ik,rel", [
    ("000660", "반도체와반도체장비", "402340"),       # SK하이닉스 ← SK스퀘어
    ("090430", "화장품", "002790"),                    # 아모레퍼시픽 ← 아모레퍼시픽홀딩스
    ("012450", "우주항공과국방", "272210"),            # 한화에어로스페이스 ← 한화시스템
    ("068270", "제약", "068760"),                      # 셀트리온 ← 셀트리온제약
    ("329180", "조선", "009540"),                      # HD현대중공업 ← HD한국조선해양
    ("329180", "조선", "267250"),                      # HD현대중공업 ← HD현대
])
def test_real_registry_drops_related_companies(no_net, monkeypatch, reg, tk, ik, rel):
    unrelated = ["010140", "042660", "005930", "000990", "207940", "128940", "051900", "161890", "000080"]
    _world(monkeypatch, [rel] + unrelated, sug=[rel])
    out = PR.domestic_candidates(tk, ik, reg, 5)
    assert rel not in out and len(out) == 5


def test_coway_drops_cuckoo_holdco_but_keeps_cuckoo_homesys(no_net, monkeypatch, reg):
    # 코웨이 không thuộc 쿠쿠: 쿠쿠홀딩스 bị loại vì là holding, 쿠쿠홈시스 (DN vận hành) vẫn là đối thủ
    _world(monkeypatch, ["192400", "284740", "066570"])
    dropped: list = []
    out = PR.domestic_candidates("021240", "가정용기기와용품", reg, 5, dropped=dropped)
    assert "192400" not in out and "284740" in out
    assert [d["Mã"] for d in dropped] == ["192400"] and "holding" in dropped[0]["Lý do"]


def test_registry_carries_group_column(reg):
    g = dict(zip(reg.df["ticker"], reg.df["group"]))
    assert g["000660"] == g["402340"] == "SK"
    assert g["003490"] == g["180640"] == "한진"
    assert pd.isna(g["000020"])                          # mã không chắc → để trống


# ============================================================================ I57 — holding phi tài chính
@pytest.mark.parametrize("tk", ["009970", "078930", "006260", "180640", "012630", "000240"])
def test_nonfinancial_holdcos_are_holdco_with_medium_cap(reg, tk):
    idn = P.identify({"sector": reg.sector_of(tk), "industry_kr": reg.industry_of(tk), "name": reg.name_of(tk)})
    assert idn["bucket"] == "Holdco" and idn["data_ok"] is False
    assert "NAV" in idn["primary"]


@pytest.mark.parametrize("tk,bucket", [("105560", "Bank"), ("055550", "Bank"), ("071050", "Brokerage"),
                                       ("138040", "Brokerage"), ("139130", "Bank"), ("005490", "Steel/Chemicals")])
def test_financial_and_curated_holdings_keep_their_bucket(reg, tk, bucket):
    idn = P.identify({"sector": reg.sector_of(tk), "industry_kr": reg.industry_of(tk), "name": reg.name_of(tk)})
    assert idn["bucket"] == bucket


def test_holdco_name_rule():
    assert V.effective_sector(None, "섬유,의류,신발,호화품", "영원무역홀딩스") == "Holdco"
    assert V.effective_sector(None, "식품", "하림지주") == "Holdco"
    assert V.effective_sector(None, "은행", "BNK금융지주") == "Bank"
    assert V.effective_sector(None, "식품과기본식료품소매", "사토시홀딩스") == "Consumer/Retail"   # DN vận hành
    assert V.effective_sector(None, "섬유,의류,신발,호화품", "영원무역") == "Consumer/Retail"
    assert V.effective_sector("Steel/Chemicals", "철강", "POSCO홀딩스") == "Steel/Chemicals"     # curated ưu tiên
    assert V.recommend_method(None, "섬유,의류,신발,호화품", "영원무역홀딩스")["auto"] == "P/B-relative"
    # chữ ký cũ (không có tên) giữ nguyên kết quả
    assert V.effective_sector(None, "섬유,의류,신발,호화품") == "Consumer/Retail"


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


def test_holdco_conclusion_is_capped_medium():
    s = _series()
    last = s[-1]
    price = last["bps"] * 1.2

    def row(name):
        return {"ticker": "009970", "name": name, "price": price, "market_cap": price * 1e7, "eps": last["eps"],
                "bps": last["bps"], "dps": last["dps"], "industry_kr": "섬유,의류,신발,호화품", "sector": None}
    peers = [{"ticker": f"9{i:05d}", "per": 9.0 + (i % 3), "pbr": 0.9 + 0.05 * (i % 3), "fetch_ok": True}
             for i in range(12)]
    ctx = P.assess("009970", row("영원무역홀딩스"), {}, A, peers, 1.0, series=s, today=TODAY)
    assert ctx["idn"]["bucket"] == "Holdco"
    assert ctx["rec"]["confidence"] in ("Medium", "Low")


def test_holdco_name_turns_high_into_medium(reg):
    # cùng đầu vào đạt High khi là DN vận hành (2 mô hình chính lệch ≤20%, fair trong [0,67;1,5]× neo);
    # tên holding → Holdco, data_ok=False → trần Medium (cách assess dựng caps)
    price = 174600.0
    cands = [{"name": "DCF", "value": 360000, "role": "primary"}, {"name": "RIM", "value": 350000, "role": "primary"}]
    conf = {}
    for nm in ("영원무역", "영원무역홀딩스"):
        idn = P.identify({"sector": reg.sector_of("009970"), "industry_kr": reg.industry_of("009970"), "name": nm})
        conf[nm] = P.reconcile(price, cands, 340000, [None, None if idn["data_ok"] else "Medium"])["confidence"]
    assert conf == {"영원무역": "High", "영원무역홀딩스": "Medium"}


def test_sk_square_is_holdco_and_leaves_semiconductor_peer_sets(no_net, monkeypatch, reg):
    # SK스퀘어: P/E thực chất là P/E của SK하이닉스 sau chiết khấu holding → không làm đối thủ của 삼성전자 & cả ngành
    idn = P.identify({"sector": reg.sector_of("402340"), "industry_kr": reg.industry_of("402340"),
                      "name": reg.name_of("402340")})
    assert idn["bucket"] == "Holdco" and idn["data_ok"] is False
    _world(monkeypatch, ["005930", "000660", "402340", "042700", "000990", "357780", "240810", "058470"],
           sug=["000660", "402340"])
    for tk in ("005930", "042700"):
        dropped: list = []
        out = PR.domestic_candidates(tk, "반도체와반도체장비", reg, 5, dropped=dropped)
        assert "402340" not in out and "000660" in out and len(out) == 5
        assert any(d["Mã"] == "402340" and "holding" in d["Lý do"] for d in dropped)


def test_airline_peer_set_has_no_hanjin_kal(no_net, monkeypatch, reg):
    _world(monkeypatch, ["180640", "020560", "272450", "089590", "091810", "298690"], sug=["180640"])
    out = PR.domestic_candidates("003490", "항공사", reg, 12)
    assert "180640" not in out and "089590" in out

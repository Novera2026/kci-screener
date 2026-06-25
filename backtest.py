"""
KCI Backtest — đo SAI SỐ & TÍN HIỆU của hệ thống định giá (mảnh thứ 3 nâng tin cậy).

Ý tưởng: tại một thời điểm QUÁ KHỨ (as-of), định giá bằng dữ liệu có-tại-thời-điểm-đó,
rồi so fair value / upside với GIÁ THỰC sau `horizon` tháng. Trả lời 3 câu:

  1) Sai số định giá bao lớn?         -> median |fair − price| / price
  2) Upside model có DỰ BÁO được return tương lai không?  -> IC (rank corr) + chênh
     return giữa nhóm "rẻ nhất" và "đắt nhất" theo model.
  3) Confidence có ý nghĩa không?     -> sai số / hiệu quả theo từng mức confidence.

Nguyên tắc point-in-time: dùng EPS/BPS/PER/PBR/DPS có-tại-as-of (pykrx fundamental),
giá đóng cửa tại as-of và tại as-of+horizon (pykrx OHLCV). KHÔNG dùng số tương lai.

Chạy trên MÁY BẠN (pykrx fundamental cần mạng KRX, một số sandbox chặn).
Phụ thuộc: pip install pykrx pandas
"""
from __future__ import annotations
import os
import csv
import datetime as _dt
from typing import Optional

import pandas as pd

import valuation
from valuation import Assumptions, value_stock, effective_sector

_HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Sector lookup (đồng bộ app): tickers.csv (EN) → fallback tickers_all.csv (업종 KRV)
# ---------------------------------------------------------------------------
def _load_sector_map() -> dict[str, dict]:
    out: dict[str, dict] = {}
    p2 = os.path.join(_HERE, "tickers_all.csv")
    if os.path.exists(p2):
        with open(p2, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                tk = str(r.get("ticker", "")).zfill(6)
                if tk:
                    out[tk] = {"industry_kr": r.get("industry_kr"), "sector": None,
                               "name": r.get("name_kr")}
    p1 = os.path.join(_HERE, "tickers.csv")
    if os.path.exists(p1):
        with open(p1, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                tk = str(r.get("ticker", "")).zfill(6)
                if tk:
                    out.setdefault(tk, {})
                    out[tk]["sector"] = r.get("sector")
                    out[tk].setdefault("industry_kr", None)
                    out[tk].setdefault("name", r.get("name_en") or r.get("name_kr"))
    return out


# ---------------------------------------------------------------------------
# Fetch point-in-time (pykrx) — phần cần mạng, tách riêng để dễ test phần còn lại
# ---------------------------------------------------------------------------
def _d(date: str) -> str:
    return date.replace("-", "")


def _asof_fundamental(ticker: str, asof: str) -> Optional[dict]:
    """EPS/BPS/PER/PBR/DPS/DIV có tại as-of (lấy bản ghi gần nhất ≤ as-of)."""
    from pykrx import stock
    end = _d(asof)
    start = _d((_dt.date.fromisoformat(asof) - _dt.timedelta(days=14)).isoformat())
    try:
        df = stock.get_market_fundamental_by_date(start, end, ticker)
        if df is None or df.empty:
            return None
        last = df.iloc[-1]
        return {"bps": float(last.get("BPS") or 0) or None,
                "eps": float(last.get("EPS") or 0) or None,
                "per": float(last.get("PER") or 0) or None,
                "pbr": float(last.get("PBR") or 0) or None,
                "dps": float(last.get("DPS") or 0) or None,
                "div": float(last.get("DIV") or 0) or None}
    except Exception:
        return None


def _price_on(ticker: str, date: str, window: int = 10) -> Optional[float]:
    """Giá đóng cửa gần nhất ≤ date."""
    from pykrx import stock
    end = _d(date)
    start = _d((_dt.date.fromisoformat(date) - _dt.timedelta(days=window)).isoformat())
    try:
        df = stock.get_market_ohlcv_by_date(start, end, ticker)
        if df is None or df.empty:
            return None
        return float(df["종가"].iloc[-1])
    except Exception:
        return None


def _add_months(date: str, months: int) -> str:
    d = _dt.date.fromisoformat(date)
    m = d.month - 1 + months
    y = d.year + m // 12
    return _dt.date(y, m % 12 + 1, min(d.day, 28)).isoformat()


# ---------------------------------------------------------------------------
# Thu thập 1 (ticker, as-of) → bản ghi thô (chưa định giá)
# ---------------------------------------------------------------------------
def collect_point(ticker: str, asof: str, horizon_months: int,
                  smap: dict) -> Optional[dict]:
    f = _asof_fundamental(ticker, asof)
    if not f:
        return None
    p0 = _price_on(ticker, asof)
    pH = _price_on(ticker, _add_months(asof, horizon_months))
    if not p0 or not pH:
        return None
    meta = smap.get(str(ticker).zfill(6), {})
    return {"ticker": ticker, "asof": asof, "price0": p0, "priceH": pH,
            "fwd_ret": pH / p0 - 1,
            "sector": meta.get("sector"), "industry_kr": meta.get("industry_kr"),
            "name": meta.get("name"), **f}


# ---------------------------------------------------------------------------
# Định giá điểm đã thu thập (phần THUẦN — test được không cần mạng)
# ---------------------------------------------------------------------------
def value_points(points: list[dict], a: Optional[Assumptions] = None) -> pd.DataFrame:
    """Nhận list bản ghi thô (từ collect_point hoặc giả lập) → DataFrame có
    fair_value, upside, confidence. Median ngành tính từ chính cross-section mỗi as-of."""
    a = a or Assumptions()
    df = pd.DataFrame(points)
    if df.empty:
        return df
    # median PER/PBR theo (as-of, sector hiệu dụng)
    df["sec_eff"] = [effective_sector(r.get("sector"), r.get("industry_kr"))
                     for _, r in df.iterrows()]
    med = (df.groupby(["asof", "sec_eff"])
             .agg(med_pe=("per", "median"), med_pb=("pbr", "median")).reset_index())
    df = df.merge(med, on=["asof", "sec_eff"], how="left")

    fair, ups, conf, method = [], [], [], []
    for _, r in df.iterrows():
        row = {"ticker": r["ticker"], "name": r.get("name"), "sector": r.get("sector"),
               "industry_kr": r.get("industry_kr"), "eps": r.get("eps"),
               "bps": r.get("bps"), "dps": r.get("dps"), "price": r["price0"]}
        res = value_stock(row, a, r.get("med_pe"), r.get("med_pb"))
        fair.append(res.fair_value)
        ups.append(res.upside_pct / 100 if res.upside_pct is not None else None)
        conf.append(res.confidence)
        method.append(res.auto_method)
    df["fair_value"] = fair
    df["upside"] = ups
    df["confidence"] = conf
    df["method"] = method
    df["abs_err"] = [(abs(fv - p0) / p0) if (fv and p0) else None
                     for fv, p0 in zip(df["fair_value"], df["price0"])]
    return df


# ---------------------------------------------------------------------------
# Đánh giá: sai số + tín hiệu (IC, chênh nhóm) + theo confidence
# ---------------------------------------------------------------------------
def _spearman(x: pd.Series, y: pd.Series) -> Optional[float]:
    d = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(d) < 5:
        return None
    return round(d["x"].rank().corr(d["y"].rank()), 3)


def evaluate(df: pd.DataFrame) -> dict:
    """Trả metrics tổng + theo confidence."""
    d = df.dropna(subset=["upside", "fwd_ret"]).copy()
    n = len(d)
    out = {"n": n}
    if n == 0:
        return out
    out["median_abs_error"] = round(d["abs_err"].median(), 3) if d["abs_err"].notna().any() else None
    out["IC_spearman"] = _spearman(d["upside"], d["fwd_ret"])
    # chênh return: nhóm "rẻ nhất" (upside cao) − nhóm "đắt nhất" theo tercile
    if n >= 9:
        d["bucket"] = pd.qcut(d["upside"].rank(method="first"), 3,
                              labels=["đắt", "giữa", "rẻ"])
        top = d[d["bucket"] == "rẻ"]["fwd_ret"].mean()
        bot = d[d["bucket"] == "đắt"]["fwd_ret"].mean()
        out["spread_rẻ_trừ_đắt"] = round(top - bot, 3)
        out["return_nhóm_rẻ"] = round(top, 3)
        out["return_nhóm_đắt"] = round(bot, 3)
    # directional hit-rate: model bảo undervalue (upside>10%) → có lên thật không
    und = d[d["upside"] > 0.10]
    out["hit_rate_undervalue"] = round((und["fwd_ret"] > 0).mean(), 3) if len(und) else None
    out["n_undervalue"] = int(len(und))
    # theo confidence
    byc = {}
    for c, g in d.groupby("confidence"):
        byc[c] = {"n": int(len(g)),
                  "median_abs_error": round(g["abs_err"].median(), 3) if g["abs_err"].notna().any() else None,
                  "IC": _spearman(g["upside"], g["fwd_ret"]),
                  "mean_fwd_ret": round(g["fwd_ret"].mean(), 3)}
    out["by_confidence"] = byc
    return out


# ---------------------------------------------------------------------------
# Runner end-to-end
# ---------------------------------------------------------------------------
def run_backtest(tickers: list[str], asof_dates: list[str], horizon_months: int = 12,
                 a: Optional[Assumptions] = None, verbose: bool = True
                 ) -> tuple[pd.DataFrame, dict]:
    smap = _load_sector_map()
    points = []
    total = len(tickers) * len(asof_dates)
    i = 0
    for asof in asof_dates:
        for tk in tickers:
            i += 1
            pt = collect_point(tk, asof, horizon_months, smap)
            if pt:
                points.append(pt)
            if verbose and i % 10 == 0:
                print(f"  {i}/{total} điểm...")
    df = value_points(points, a)
    metrics = evaluate(df)
    return df, metrics


def report_markdown(df: pd.DataFrame, m: dict, horizon_months: int) -> str:
    L = [f"# KCI Backtest — kết quả ({m.get('n', 0)} điểm, horizon {horizon_months} tháng)\n"]
    L.append(f"- **Sai số định giá** (median |fair−price|/price): "
             f"**{m.get('median_abs_error')}**")
    L.append(f"- **IC (Spearman upside vs return tương lai):** **{m.get('IC_spearman')}** "
             "— >0 nghĩa là model có tín hiệu; ~0 là không.")
    if "spread_rẻ_trừ_đắt" in m:
        L.append(f"- **Chênh return nhóm 'rẻ' − 'đắt' theo model:** "
                 f"**{m['spread_rẻ_trừ_đắt']:+.1%}** "
                 f"(rẻ {m['return_nhóm_rẻ']:+.1%} vs đắt {m['return_nhóm_đắt']:+.1%})")
    if m.get("hit_rate_undervalue") is not None:
        L.append(f"- **Hit-rate khi model bảo 'undervalue >10%':** "
                 f"{m['hit_rate_undervalue']:.0%} ({m['n_undervalue']} lần)")
    L.append("\n## Theo mức confidence\n")
    L.append("| Confidence | n | Sai số median | IC | Return TB |")
    L.append("|---|---|---|---|---|")
    for c, s in (m.get("by_confidence") or {}).items():
        L.append(f"| {c} | {s['n']} | {s['median_abs_error']} | {s['IC']} | "
                 f"{s['mean_fwd_ret']:+.1%} |")
    L.append("\n> Diễn giải đúng: nếu IC nhóm High > nhóm Low, confidence có ý nghĩa. "
             "Nếu sai số lớn ở mọi nhóm → cảnh báo người dùng đây là công cụ tham khảo, "
             "không phải định giá chính xác (đúng tinh thần SOP).")
    return "\n".join(L)


if __name__ == "__main__":
    # Universe demo: trải nhiều ngành. As-of cách nhau 6 tháng, horizon 12 tháng.
    UNIVERSE = ["005930", "000660", "035420", "035720", "105560", "055550",
                "005380", "000270", "005490", "051910", "207940", "068270",
                "012450", "042660", "015760", "017670", "090430", "352820"]
    ASOF = ["2023-06-30", "2023-12-29", "2024-06-28"]
    a = Assumptions()
    df, m = run_backtest(UNIVERSE, ASOF, horizon_months=12, a=a)
    out_csv = os.path.join(_HERE, "backtest_results.csv")
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    rep = report_markdown(df, m, 12)
    with open(os.path.join(_HERE, "backtest_report.md"), "w", encoding="utf-8") as f:
        f.write(rep)
    print(rep)
    print(f"\n[OK] Chi tiết: {out_csv}")

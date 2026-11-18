"""
🤖 KCI Bot săn cổ phiếu — chạy quy trình SÀNG LỌC + ĐỊNH GIÁ của App rồi gửi báo cáo Telegram.

Mỗi lần chạy (mặc định sau giờ đóng cửa ngày giao dịch):
  1. Danh sách gốc: top N vốn hóa cả hai sàn — giá, P/E, P/B lấy MỚI (không dùng lại số cũ).
  2. Áp các BỘ TIÊU CHUẨN của trang 🔍 (screen.PRESETS) — tầng 1 trên dữ liệu thị trường.
  3. Định giá sâu (cùng quy trình trang 🧮, screen.deep_stage) các mã qua tầng 1, rồi kiểm tầng 2 của từng bộ.
  4. Danh sách săn = mã đạt tầng 2 của ≥ 1 bộ, có giá trị hợp lý qua cổng số và rẻ hơn giá trị ước tính.
  5. So với lần chạy trước (bot_state.json): mã MỚI lọt vào, mã RỚT ra, kết luận thay đổi.
  6. Gửi Telegram (tin nhắn + file Excel). Ngày sàn nghỉ (KOSPI không có phiên hôm nay) → không gửi.

Chạy:
    python bot_hunt.py --dry-run     # quét và IN tin nhắn, không gửi
    python bot_hunt.py               # quét và gửi Telegram
    python bot_hunt.py --chat-id     # lấy chat id sau khi anh nhắn /start cho bot
    python bot_hunt.py --export out.json --force   # quét, ghi JSON (cho tác vụ Claude ghi Google Sheet), không gửi
Biến môi trường (.env khi chạy máy, GitHub Secrets khi chạy tự động):
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DART_API_KEY
    BOT_PRESETS (mặc định "kci,valueup,magic,dividend,graham"), BOT_UNIVERSE (200), BOT_MAX_DEEP (15/bộ)

Danh sách là để nghiên cứu tiếp, không phải khuyến nghị đầu tư.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import html
import io
import json
import os
import sys
from typing import Optional

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(ROOT, "bot_state.json")
KST = _dt.timezone(_dt.timedelta(hours=9))
DEFAULT_PRESETS = ["kci", "valueup", "magic", "dividend", "graham"]
APP_URL = "https://kci-screener-7j3nnaqzjydhcsdvzvy37q.streamlit.app"
TG_MAX = 4000                      # Telegram giới hạn 4096 ký tự/tin


def _env():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
    except Exception:
        pass


# ============================================================================ logic thuần (có test)
def aggregate(results: dict, hits: dict) -> list[dict]:
    """results: {mã: dòng deep_stage}; hits: {bộ: [mã đạt tầng 2]} → danh sách săn đã xếp hạng.

    Vào danh sách khi: đạt tầng 2 của ≥ 1 bộ, có giá trị hợp lý (qua cổng số) và chênh lệch > 0.
    Xếp: chênh lệch ↓ (tín hiệu định giá — phần duy nhất có IC dương). 10/11/2026: mô phỏng PIT 2021–25 top 200, 5 bộ
    mặc định: top 12 xếp theo SỐ BỘ ĐẠT thua TB danh sách gốc −14,1%/năm (t −2,29); xếp theo chênh lệch +0,4%/năm (t 0,11).
    Xếp theo độ tin cậy trước cũng kém (−11%/năm) — không dùng. Số bộ đạt vẫn hiện để đọc, không dùng để xếp."""
    import screen as SC
    by_tk: dict = {}
    for key, tks in hits.items():
        for tk in tks:
            by_tk.setdefault(tk, []).append(key)
    picks = []
    for tk, presets in by_tk.items():
        r = results.get(tk) or {}
        up = r.get("Chênh %")
        if not r.get("Giá trị hợp lý") or up is None or up <= 0:
            continue
        picks.append({"ticker": tk, "name": r.get("Tên") or tk, "industry": r.get("Ngành"),
                      "price": r.get("Giá"), "fair": r.get("Giá trị hợp lý"), "upside": up,
                      "quality": r.get("Chất lượng TS"), "verdict": r.get("Kết luận") or "",
                      "confidence": r.get("Độ tin cậy"), "data_warn": r.get("Cảnh báo dữ liệu") or "",
                      "sources": r.get("Nguồn dữ liệu") or "",            # một dòng chung với 🧮 / Sàng lọc (C8)
                      "presets": sorted(presets, key=lambda k: list(SC.PRESETS).index(k))})
    picks.sort(key=lambda p: -p["upside"])
    return picks


def diff(prev: dict, picks: list[dict], errored=None) -> dict:
    """So với lần trước: mã mới, mã rớt, mã đổi kết luận (🟢 ↔ 🟡 …).

    errored: mã lần này lỗi dữ liệu / định giá lỗi — mã cũ thuộc nhóm này KHÔNG tính là rớt (chưa biết), đưa vào
    khóa 'errored' (rà soát 18/11/2026: Naver lỗi một phần làm mã cũ hiện 'Rớt')."""
    old = (prev or {}).get("picks", {})
    cur = {p["ticker"]: p for p in picks}
    err = set(errored or ())
    new = [p for p in picks if p["ticker"] not in old]
    dropped = [dict(ticker=t, **v) for t, v in old.items() if t not in cur and t not in err]
    unknown = [dict(ticker=t, **v) for t, v in old.items() if t not in cur and t in err]
    changed = [(p, old[p["ticker"]]) for p in picks if p["ticker"] in old
               and (old[p["ticker"]].get("verdict") or "")[:1] != (p["verdict"] or "")[:1]]
    return {"new": new, "dropped": dropped, "changed": changed, "first_run": not old, "errored": unknown}


def _short(p: dict) -> str:
    return p["verdict"][:1] if p.get("verdict") else "•"


def _line(p: dict, preset_names: dict) -> str:
    sets = ", ".join(preset_names.get(k, k) for k in p["presets"])
    # ⚠️: kết luận tin cậy Thấp (chỉ một mô hình chính, hoặc lỗi dữ liệu) — phải thấy ngay trong danh sách (15/11/2026)
    low = ""
    if p.get("data_warn"):
        low = f" · ⚠️ lỗi dữ liệu: {html.escape(str(p['data_warn']))}"
        if p.get("sources"):
            low += f" ({html.escape(str(p['sources']))})"
    elif p.get("confidence") == "Low":
        low = " · ⚠️ tin cậy Thấp"
    return (f"{_short(p)} <b>{html.escape(str(p['name']))}</b> ({p['ticker']}) · "
            f"{p['price']:,.0f} → <b>{p['fair']:,.0f}</b> ({p['upside']:+.0f}%) · TS {p.get('quality') or '—'} · "
            f"{len(p['presets'])} bộ: {html.escape(sets)}{low}")


def format_message(picks: list[dict], d: dict, meta: dict, top: int = 12) -> list[str]:
    """Tin nhắn Telegram (HTML), tách nhiều tin nếu dài."""
    import screen as SC
    names = {k: SC.PRESETS[k]["name"].split(" — ")[0].split(" (")[0] for k in SC.PRESETS}
    L = [f"🤖 <b>KCI săn cổ phiếu</b> · {meta['at']:%d/%m/%Y %H:%M} KST",
         f"Danh sách gốc top {meta['universe']} · {len(meta['presets'])} bộ tiêu chuẩn · định giá sâu {meta['n_deep']} mã · "
         f"Rf {meta['rf'] * 100:.2f}%" if meta.get("rf") else
         f"Danh sách gốc top {meta['universe']} · {len(meta['presets'])} bộ tiêu chuẩn · định giá sâu {meta['n_deep']} mã", ""]
    if meta.get("rf_live") is False:
        L[-1:-1] = [f"⚠️ {html.escape(str(meta.get('rf_note') or 'Không lấy được Rf trực tiếp'))} — kết quả lần này tin cậy Thấp"]
    if d["first_run"]:
        L.append("Lần chạy đầu — chưa có lần trước để so.")
    else:
        L.append(f"🆕 <b>Mới vào ({len(d['new'])})</b>")
        L += [_line(p, names) for p in d["new"]] or ["— không có"]
        if d["changed"]:
            L.append(f"🔁 <b>Đổi kết luận ({len(d['changed'])})</b>")
            L += [f"{o.get('verdict', '')[:1]}→{_short(p)} {html.escape(str(p['name']))} ({p['ticker']}) "
                  f"{p['upside']:+.0f}%" for p, o in d["changed"]]
        if d["dropped"]:
            L.append(f"❌ <b>Rớt khỏi danh sách ({len(d['dropped'])})</b>")
            L += [f"{html.escape(str(x.get('name', x['ticker'])))} ({x['ticker']})" for x in d["dropped"]]
        if d.get("errored"):
            L.append(f"⚪ <b>Chưa kiểm được lần này — lỗi dữ liệu ({len(d['errored'])})</b>")
            L += [f"{html.escape(str(x.get('name', x['ticker'])))} ({x['ticker']})" for x in d["errored"]]
    L += ["", f"📋 <b>Danh sách săn ({len(picks)} mã) — top {min(top, len(picks))}</b>"]
    L += [_line(p, names) for p in picks[:top]] or ["— không mã nào đạt chuẩn hôm nay"]
    L += ["", "🟢 rẻ & chất lượng tốt · 🟡 rẻ nhưng cần kiểm (chất lượng/ngành tài chính) · TS = hạng chất lượng tài sản · "
                "⚠️ = kết luận tin cậy Thấp (chỉ một mô hình chính hoặc lỗi dữ liệu).",
          "Xếp theo chênh lệch định giá. Backtest 2021–25 (top 200): định giá xếp hạng có tín hiệu yếu (IC +0,08); "
          "danh sách kiểu này CHƯA thắng trung bình thị trường (top 12 ≈ +0,4%/năm, không ý nghĩa thống kê). "
          "Để nghiên cứu tiếp, không phải danh mục mua.",
          f'Định giá đủ 5 bước: <a href="{APP_URL}/Dinh_gia">KCI · Định giá</a>']
    msgs, cur = [], ""
    for ln in L:
        if len(cur) + len(ln) + 1 > TG_MAX:
            msgs.append(cur)
            cur = ""
        cur += ln + "\n"
    if cur.strip():
        msgs.append(cur)
    return msgs


def state_of(picks: list[dict], at: _dt.datetime, d: Optional[dict] = None) -> dict:
    """Trạng thái lưu cho lần sau. d = kết quả diff(): mã cũ lần này lỗi dữ liệu ('errored') được giữ nguyên mục cũ."""
    out = {"at": at.isoformat(timespec="minutes"),
           "picks": {p["ticker"]: {"name": p["name"], "upside": p["upside"], "verdict": p["verdict"],
                                   "presets": p["presets"]} for p in picks}}
    for x in (d or {}).get("errored") or []:
        out["picks"].setdefault(x["ticker"], {k: v for k, v in x.items() if k != "ticker"})
    return out


# mục ❌ trong 'Cảnh báo dữ liệu' (screen.deep_stage) do LỖI LẤY DART — 'Đối chiếu DART ↔ Naver' là lệch nguồn, không tính
_DART_ERR_CHECK = "Số năm có số gốc DART"          # pipeline: ❌ chỉ khi DART trả lỗi
_DART_FS_ERR = "BCTC đầy đủ DART:"                 # screen.deep_stage: lỗi lấy BCTC đầy đủ


def _errored(r: dict) -> bool:
    """Dòng định giá sâu lỗi: không định giá được, hoặc BCTC DART lỗi (thiếu F-score… → tầng 2 trượt vì lỗi)."""
    warns = [w.strip() for w in str(r.get("Cảnh báo dữ liệu") or "").split(";")]
    return (str(r.get("Kết luận") or "").startswith("⚪ Chưa định giá được")
            or any(w == _DART_ERR_CHECK or w.startswith(_DART_FS_ERR) for w in warns))


MIN_UNIVERSE_SHARE = 0.8        # danh sách gốc có dữ liệu < 80% số mã yêu cầu → lượt chạy hỏng
MAX_ERROR_SHARE = 0.2           # > 20% mã định giá sâu lỗi → lượt chạy hỏng


def _health(out: dict, n_req: Optional[int] = None) -> tuple[bool, list[str]]:
    """Lượt săn có đủ dữ liệu để ghi / gửi không. n_req: số mã yêu cầu ở chế độ top vốn hóa (None = danh sách riêng)."""
    import screen as SC
    m, why = out["meta"], []
    n_data = sum(1 for r in out.get("rows") or [] if not SC.no_market_data(r))
    if n_req and n_data < MIN_UNIVERSE_SHARE * n_req:
        why.append(f"danh sách gốc chỉ có {n_data}/{n_req} mã có dữ liệu thị trường (Naver lỗi?)")
    if not m.get("n_deep"):
        why.append("không mã nào được định giá sâu")
    res = list((out.get("results") or {}).values())
    n_err = sum(1 for r in res if _errored(r))
    if res and n_err > MAX_ERROR_SHARE * len(res):
        why.append(f"{n_err}/{len(res)} mã định giá sâu bị lỗi (DART / dữ liệu)")
    return (not why, why)


def _write_json_atomic(path: str, obj, **kw) -> None:
    """Ghi JSON qua file tạm rồi đổi tên — lỗi giữa chừng không để lại file hỏng / nửa vời."""
    import tempfile
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, **kw)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ============================================================================ mạng
def session_is_fresh(last_trade: str, now: _dt.datetime) -> bool:
    """Phiên gần nhất có phải 'phiên của lần chạy này' không: là hôm nay, hoặc là hôm qua khi lần chạy rơi vào trước
    09:00 giờ Hàn (lịch chạy bị GitHub kích hoạt trễ qua nửa đêm — rà soát 15/11: 4/4 lần chạy rơi sau 0 giờ nên bot
    tưởng sàn nghỉ và thoát)."""
    today = now.date()
    if last_trade == today.isoformat():
        return True
    return now.hour < 9 and last_trade == (today - _dt.timedelta(days=1)).isoformat()


def market_open_today(now: Optional[_dt.datetime] = None) -> Optional[bool]:
    """KOSPI có phiên mới để quét không (session_is_fresh) — theo ngày giao dịch gần nhất của Naver. None nếu không
    kiểm được."""
    import requests
    now = now or _dt.datetime.now(KST)
    try:
        j = requests.get("https://m.stock.naver.com/api/index/KOSPI/price?pageSize=1&page=1",
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}, timeout=10).json()
        return session_is_fresh(str(j[0]["localTradedAt"])[:10], now)
    except Exception:
        return None


def _round_robin(stage1: dict, presets: list[str]) -> list[str]:
    """Gộp mã qua tầng 1 của các bộ kiểu xen kẽ (mã thứ 1 của mọi bộ, rồi mã thứ 2…) — cắt bớt thì bộ nào cũng còn mã."""
    out: dict = {}
    for i in range(max((len(stage1[k]) for k in presets), default=0)):
        for k in presets:
            if i < len(stage1[k]):
                out.setdefault(stage1[k][i], None)
    return list(out)


def hunt(presets: list[str], universe: int = 200, max_deep: int = 15, progress=print,
         tickers: Optional[list[str]] = None, market: str = "ALL", scope: str = "",
         max_total: Optional[int] = None) -> dict:
    """Chạy sàng lọc + định giá sâu. Trả {picks, results, hits, rows, errored, meta}.

    Mặc định danh sách gốc = top `universe` vốn hóa của `market` (ALL / KOSPI / KOSDAQ).
    Truyền `tickers` để săn trong danh sách riêng (ngành, chủ đề, mega trend…), tối đa 400 mã.
    max_total: trần TỔNG số mã định giá sâu (khách trên bản công khai — guard.MAX_DEEP_PUBLIC); None = không trần."""
    import screen as SC
    import valuation as V
    from screener import TickerRegistry, fetch_many, rf_for_valuation
    reg = TickerRegistry()
    at = _dt.datetime.now(KST)
    if tickers:
        tks = list(dict.fromkeys(str(t).zfill(6) for t in tickers))[:400]
        scope = scope or f"{len(tks)} mã tự chọn"
    else:
        scope = scope or f"top {universe} vốn hóa " + ("cả hai sàn" if market == "ALL" else market)
        progress(f"① Danh sách gốc {scope}…")
        tks = SC.top_by_cap(market, universe, reg)
    progress(f"① Lấy chỉ số {len(tks)} mã ({scope})…")
    rows, _ = fetch_many(tks, registry=reg, prefer="naver")
    stage1 = {k: [r["Mã"] for r in SC.apply_preset(rows, k) if r["Qua lọc"]][:max_deep] for k in presets}
    union = list(dict.fromkeys(t for k in presets for t in stage1[k]))
    n_cut = 0
    if max_total is not None and len(union) > max_total:
        union = _round_robin(stage1, presets)[:max_total]
        n_cut = len(dict.fromkeys(t for k in presets for t in stage1[k])) - len(union)
        progress(f"   Đã cắt còn {len(union)} mã (trần {max_total} mã định giá sâu mỗi lần trên bản công khai)…")
    progress(f"② Tầng 1: {', '.join(f'{k} {len(v)}' for k, v in stage1.items())} → định giá sâu {len(union)} mã…")
    # Rf không lấy được trực tiếp → vẫn tính trên giá trị dự phòng nhưng nói rõ và mọi kết quả bị hạ xuống Low
    # (trước 15/11/2026: lặng lẽ dùng 3,2%)
    rf = rf_for_valuation()
    a = V.Assumptions(rf=rf["value"])
    feeds = {} if rf["live"] else {"rf": {"ok": False, "note": rf["note"]}}
    if not rf["live"]:
        progress(f"⚠️ {rf['note']} — độ tin cậy của mọi kết quả lần này bị hạ xuống Low")
    res = SC.deep_stage(union, {r["ticker"]: r for r in rows}, rows, a, reg, feeds=feeds,
                        progress=lambda d, n: progress(f"   định giá {d}/{n}") if d % 5 == 0 or d == n else None)
    results = {r["Mã"]: r for r in res}
    hits = {k: [t for t in stage1[k] if t in results and SC.stage2_check(results[t], k)[0]] for k in presets}
    progress("③ Tầng 2: " + ", ".join(f"{k} {len(v)}" for k, v in hits.items()))
    picks = aggregate(results, hits)
    # mã lỗi dữ liệu lần này (Naver không trả số / định giá lỗi) — không được coi là "rớt" khi so lần trước
    errored = sorted({r["ticker"] for r in rows if SC.no_market_data(r)}
                     | {t for t, r in results.items() if _errored(r)})
    return {"picks": picks, "results": results, "hits": hits, "rows": rows, "errored": errored,
            "meta": {"at": at, "universe": len(rows), "scope": scope, "presets": presets, "n_deep": len(union),
                     "n_cut": n_cut, "rf": rf["value"], "rf_live": rf["live"], "rf_note": rf["note"]}}


def excel_bytes(out: dict) -> bytes:
    import pandas as pd
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame([{**p, "presets": ", ".join(p["presets"])} for p in out["picks"]] or [{}]) \
            .to_excel(xw, sheet_name="Danh sách săn", index=False)
        pd.DataFrame(list(out["results"].values())).to_excel(xw, sheet_name="Định giá sâu", index=False)
    return buf.getvalue()


def tg_send(token: str, chat_id: str, msgs: list[str], doc: Optional[bytes] = None, doc_name: str = "") -> None:
    import requests
    base = f"https://api.telegram.org/bot{token}"
    for m in msgs:
        r = requests.post(f"{base}/sendMessage", data={"chat_id": chat_id, "text": m, "parse_mode": "HTML",
                                                       "disable_web_page_preview": "true"}, timeout=20)
        if not r.json().get("ok"):
            raise RuntimeError(f"Telegram sendMessage lỗi: {r.text[:200]}")
    if doc:
        r = requests.post(f"{base}/sendDocument", data={"chat_id": chat_id},
                          files={"document": (doc_name, doc)}, timeout=60)
        if not r.json().get("ok"):
            raise RuntimeError(f"Telegram sendDocument lỗi: {r.text[:200]}")


def tg_chat_ids(token: str) -> list[dict]:
    """Các cuộc trò chuyện đã nhắn cho bot (sau khi anh bấm Start / gửi /start)."""
    import requests
    j = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20).json()
    seen = {}
    for u in j.get("result", []):
        c = (u.get("message") or u.get("channel_post") or {}).get("chat") or {}
        if c.get("id"):
            seen[c["id"]] = {"chat_id": c["id"], "name": c.get("title") or c.get("first_name") or c.get("username")}
    return list(seen.values())


_EXPORT_FIELDS = ("Điểm chất lượng", "F-score", "Nợ/VCSH", "Thanh toán hiện hành", "Có năm lỗ", "Tổng quan",
                  "Độ tin cậy", "Trạng thái", "Nhóm", "Cảnh báo dữ liệu", "Nguồn dữ liệu")


def export_payload(out: dict, d: dict, registry=None) -> dict:
    """Kết quả 1 lần săn → JSON cho tác vụ Claude (ghi Google Sheet + nhận định AI). Chỉ số liệu của App."""
    import screen as SC
    names = {k: SC.PRESETS[k]["name"].split(" — ")[0].split(" (")[0] for k in SC.PRESETS}
    mk = {}
    if registry is not None:
        mk = dict(zip(registry.df["ticker"].astype(str).str.zfill(6), registry.df["market"]))
    rows = []
    for i, p in enumerate(out["picks"], 1):
        r = out["results"].get(p["ticker"], {})
        rows.append({"rank": i, "ticker": p["ticker"], "name": p["name"], "market": mk.get(p["ticker"], ""),
                     "industry": p.get("industry") or "", "price": p["price"], "fair": round(p["fair"]),
                     "upside_pct": round(p["upside"], 1), "verdict": p["verdict"], "quality": p.get("quality") or "",
                     "presets": [names.get(k, k) for k in p["presets"]],
                     **{k: r.get(k) for k in _EXPORT_FIELDS}})
    m = out["meta"]
    # run_status: chỉ "ok" mới được ghi Google Sheet (main() không ghi file khi lượt chạy hỏng — rà soát 18/11/2026)
    return {"run_status": m.get("run_status", "ok"), "run_problems": list(m.get("run_problems") or []),
            "at": m["at"].strftime("%d/%m/%Y %H:%M"), "scope": m.get("scope"), "universe": m["universe"],
            "n_deep": m["n_deep"], "rf": m.get("rf"), "rf_live": m.get("rf_live", True), "rf_note": m.get("rf_note") or "",
            "presets": [names.get(k, k) for k in m["presets"]],
            "picks": rows,
            "vs_previous": {"first_run": d["first_run"], "new": [p["ticker"] for p in d["new"]],
                            "dropped": [{"ticker": x["ticker"], "name": x.get("name")} for x in d["dropped"]],
                            "errored": [{"ticker": x["ticker"], "name": x.get("name")} for x in d.get("errored") or []],
                            "changed": [{"ticker": p["ticker"], "from": (o.get("verdict") or "")[:1],
                                         "to": (p["verdict"] or "")[:1]} for p, o in d["changed"]]}}


def main(argv=None) -> int:
    _env()
    ap = argparse.ArgumentParser(description="KCI bot săn cổ phiếu")
    ap.add_argument("--dry-run", action="store_true", help="quét và in tin nhắn, không gửi")
    ap.add_argument("--chat-id", action="store_true", help="in chat id (sau khi nhắn /start cho bot)")
    ap.add_argument("--force", action="store_true", help="chạy cả ngày sàn nghỉ")
    ap.add_argument("--export", metavar="PATH", help="ghi kết quả ra JSON (cho tác vụ Claude) thay vì gửi Telegram")
    args = ap.parse_args(argv)
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if args.chat_id:
        if not token:
            print("Thiếu TELEGRAM_BOT_TOKEN trong .env"); return 1
        ids = tg_chat_ids(token)
        print("\n".join(f"chat_id = {c['chat_id']}  ({c['name']})" for c in ids)
              or "Chưa thấy tin nhắn nào — mở bot trên Telegram, bấm Start hoặc gửi /start rồi chạy lại.")
        return 0
    if not args.force:
        mo = market_open_today()
        if mo is False:
            print("Hôm nay sàn nghỉ (KOSPI không có phiên) — không quét."); return 0
        if mo is None:
            print("⚠️ Không kiểm được lịch phiên KOSPI (Naver lỗi?) — vẫn quét.", file=sys.stderr)
    presets = [k.strip() for k in os.environ.get("BOT_PRESETS", ",".join(DEFAULT_PRESETS)).split(",") if k.strip()]
    n_req = int(os.environ.get("BOT_UNIVERSE", "200"))
    out = hunt(presets, n_req, int(os.environ.get("BOT_MAX_DEEP", "15")))
    # cổng sức khỏe (rà soát 18/11/2026): mạng lỗi trước đây vẫn thoát 0, xuất 0 mã, báo mọi mã "Rớt", xóa bot_state
    ok, why = _health(out, n_req)
    if not ok:
        print("LƯỢT CHẠY HỎNG — không ghi export, không ghi bot_state.json, không gửi Telegram: " + "; ".join(why),
              file=sys.stderr)
        if args.export and os.path.exists(args.export):
            os.remove(args.export)       # không để file của lần trước bị đọc nhầm là kết quả lần này
        return 2
    prev = json.load(open(STATE_PATH, encoding="utf-8")) if os.path.exists(STATE_PATH) else {}
    d = diff(prev, out["picks"], out.get("errored"))
    msgs = format_message(out["picks"], d, out["meta"])
    if args.export:
        from screener import TickerRegistry
        _write_json_atomic(args.export, export_payload(out, d, TickerRegistry()), indent=1, default=str)
        _write_json_atomic(STATE_PATH, state_of(out["picks"], out["meta"]["at"], d))
        prev_txt = "chưa có" if d["first_run"] else f"{len(d['new'])} mới, {len(d['dropped'])} rớt"
        print(f"EXPORT {args.export} · {len(out['picks'])} mã · so lần trước: {prev_txt}")
        return 0
    if args.dry_run:
        print("\n---\n".join(msgs)); return 0
    if not token or not chat:
        print("Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID — chạy --dry-run hoặc cấu hình .env"); return 1
    tg_send(token, chat, msgs, excel_bytes(out), f"kci_san_co_phieu_{out['meta']['at']:%Y%m%d}.xlsx")
    _write_json_atomic(STATE_PATH, state_of(out["picks"], out["meta"]["at"], d))
    print(f"Đã gửi {len(msgs)} tin + Excel · {len(out['picks'])} mã trong danh sách săn.")
    return 0


def _main_safe(argv=None) -> int:
    """Chạy main(); lỗi không mong đợi được in ra SAU KHI che khóa / token (log GitHub Actions của repo công khai ai
    cũng đọc được; URL lỗi của requests chứa khóa DART, đường dẫn Telegram chứa token)."""
    try:
        return main(argv)
    except Exception as e:
        import guard
        import traceback
        print(guard.redact("".join(traceback.format_exception(type(e), e, e.__traceback__))), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_main_safe())

"""
🤖 KCI Bot săn cổ phiếu — chạy quy trình SÀNG LỌC + ĐỊNH GIÁ của App rồi gửi báo cáo Telegram.

Mỗi lần chạy (mặc định sau giờ đóng cửa ngày giao dịch):
  1. Vũ trụ: top N vốn hóa cả hai sàn — giá, P/E, P/B lấy MỚI (không dùng lại số cũ).
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
    mặc định: top 12 xếp theo SỐ BỘ ĐẠT thua TB vũ trụ −14,1%/năm (t −2,29); xếp theo chênh lệch +0,4%/năm (t 0,11).
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
                      "presets": sorted(presets, key=lambda k: list(SC.PRESETS).index(k))})
    picks.sort(key=lambda p: -p["upside"])
    return picks


def diff(prev: dict, picks: list[dict]) -> dict:
    """So với lần trước: mã mới, mã rớt, mã đổi kết luận (🟢 ↔ 🟡 …)."""
    old = (prev or {}).get("picks", {})
    cur = {p["ticker"]: p for p in picks}
    new = [p for p in picks if p["ticker"] not in old]
    dropped = [dict(ticker=t, **v) for t, v in old.items() if t not in cur]
    changed = [(p, old[p["ticker"]]) for p in picks if p["ticker"] in old
               and (old[p["ticker"]].get("verdict") or "")[:1] != (p["verdict"] or "")[:1]]
    return {"new": new, "dropped": dropped, "changed": changed, "first_run": not old}


def _short(p: dict) -> str:
    return p["verdict"][:1] if p.get("verdict") else "•"


def _line(p: dict, preset_names: dict) -> str:
    sets = ", ".join(preset_names.get(k, k) for k in p["presets"])
    return (f"{_short(p)} <b>{html.escape(str(p['name']))}</b> ({p['ticker']}) · "
            f"{p['price']:,.0f} → <b>{p['fair']:,.0f}</b> ({p['upside']:+.0f}%) · TS {p.get('quality') or '—'} · "
            f"{len(p['presets'])} bộ: {html.escape(sets)}")


def format_message(picks: list[dict], d: dict, meta: dict, top: int = 12) -> list[str]:
    """Tin nhắn Telegram (HTML), tách nhiều tin nếu dài."""
    import screen as SC
    names = {k: SC.PRESETS[k]["name"].split(" — ")[0].split(" (")[0] for k in SC.PRESETS}
    L = [f"🤖 <b>KCI săn cổ phiếu</b> · {meta['at']:%d/%m/%Y %H:%M} KST",
         f"Vũ trụ top {meta['universe']} · {len(meta['presets'])} bộ tiêu chuẩn · định giá sâu {meta['n_deep']} mã · "
         f"Rf {meta['rf'] * 100:.2f}%" if meta.get("rf") else
         f"Vũ trụ top {meta['universe']} · {len(meta['presets'])} bộ tiêu chuẩn · định giá sâu {meta['n_deep']} mã", ""]
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
    L += ["", f"📋 <b>Danh sách săn ({len(picks)} mã) — top {min(top, len(picks))}</b>"]
    L += [_line(p, names) for p in picks[:top]] or ["— không mã nào đạt chuẩn hôm nay"]
    L += ["", "🟢 rẻ & chất lượng tốt · 🟡 rẻ nhưng cần kiểm (chất lượng/ngành tài chính) · TS = hạng chất lượng tài sản.",
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


def state_of(picks: list[dict], at: _dt.datetime) -> dict:
    return {"at": at.isoformat(timespec="minutes"),
            "picks": {p["ticker"]: {"name": p["name"], "upside": p["upside"], "verdict": p["verdict"],
                                    "presets": p["presets"]} for p in picks}}


# ============================================================================ mạng
def market_open_today(now: Optional[_dt.datetime] = None) -> Optional[bool]:
    """KOSPI có phiên HÔM NAY (giờ Hàn) không — theo ngày giao dịch gần nhất của Naver. None nếu không kiểm được."""
    import requests
    now = now or _dt.datetime.now(KST)
    try:
        j = requests.get("https://m.stock.naver.com/api/index/KOSPI/price?pageSize=1&page=1",
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}, timeout=10).json()
        return str(j[0]["localTradedAt"])[:10] == now.strftime("%Y-%m-%d")
    except Exception:
        return None


def hunt(presets: list[str], universe: int = 200, max_deep: int = 15, progress=print,
         tickers: Optional[list[str]] = None, market: str = "ALL", scope: str = "") -> dict:
    """Chạy sàng lọc + định giá sâu. Trả {picks, results, hits, rows, meta}.

    Mặc định vũ trụ = top `universe` vốn hóa của `market` (ALL / KOSPI / KOSDAQ).
    Truyền `tickers` để săn trong danh sách riêng (ngành, chủ đề, mega trend…), tối đa 400 mã."""
    import screen as SC
    import valuation as V
    from screener import TickerRegistry, fetch_kgb10y_detail, fetch_many
    reg = TickerRegistry()
    at = _dt.datetime.now(KST)
    if tickers:
        tks = list(dict.fromkeys(str(t).zfill(6) for t in tickers))[:400]
        scope = scope or f"{len(tks)} mã tự chọn"
    else:
        scope = scope or f"top {universe} vốn hóa " + ("cả hai sàn" if market == "ALL" else market)
        progress(f"① Vũ trụ {scope}…")
        tks = SC.top_by_cap(market, universe, reg)
    progress(f"① Lấy chỉ số {len(tks)} mã ({scope})…")
    rows, _ = fetch_many(tks, registry=reg, prefer="naver")
    stage1 = {k: [r["Mã"] for r in SC.apply_preset(rows, k) if r["Qua lọc"]][:max_deep] for k in presets}
    union = list(dict.fromkeys(t for k in presets for t in stage1[k]))
    progress(f"② Tầng 1: {', '.join(f'{k} {len(v)}' for k, v in stage1.items())} → định giá sâu {len(union)} mã…")
    rf = fetch_kgb10y_detail()
    a = V.Assumptions(rf=rf.get("value") or 0.032)
    res = SC.deep_stage(union, {r["ticker"]: r for r in rows}, rows, a, reg,
                        progress=lambda d, n: progress(f"   định giá {d}/{n}") if d % 5 == 0 or d == n else None)
    results = {r["Mã"]: r for r in res}
    hits = {k: [t for t in stage1[k] if t in results and SC.stage2_check(results[t], k)[0]] for k in presets}
    progress("③ Tầng 2: " + ", ".join(f"{k} {len(v)}" for k, v in hits.items()))
    picks = aggregate(results, hits)
    return {"picks": picks, "results": results, "hits": hits, "rows": rows,
            "meta": {"at": at, "universe": len(rows), "scope": scope, "presets": presets, "n_deep": len(union), "rf": rf.get("value")}}


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
                  "Độ tin cậy", "Trạng thái", "Nhóm")


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
    return {"at": m["at"].strftime("%d/%m/%Y %H:%M"), "scope": m.get("scope"), "universe": m["universe"],
            "n_deep": m["n_deep"], "rf": m.get("rf"), "presets": [names.get(k, k) for k in m["presets"]],
            "picks": rows,
            "vs_previous": {"first_run": d["first_run"], "new": [p["ticker"] for p in d["new"]],
                            "dropped": [{"ticker": x["ticker"], "name": x.get("name")} for x in d["dropped"]],
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
    if not args.force and market_open_today() is False:
        print("Hôm nay sàn nghỉ (KOSPI không có phiên) — không quét."); return 0
    presets = [k.strip() for k in os.environ.get("BOT_PRESETS", ",".join(DEFAULT_PRESETS)).split(",") if k.strip()]
    out = hunt(presets, int(os.environ.get("BOT_UNIVERSE", "200")), int(os.environ.get("BOT_MAX_DEEP", "15")))
    prev = json.load(open(STATE_PATH, encoding="utf-8")) if os.path.exists(STATE_PATH) else {}
    d = diff(prev, out["picks"])
    msgs = format_message(out["picks"], d, out["meta"])
    if args.export:
        from screener import TickerRegistry
        with open(args.export, "w", encoding="utf-8") as f:
            json.dump(export_payload(out, d, TickerRegistry()), f, ensure_ascii=False, indent=1, default=str)
        json.dump(state_of(out["picks"], out["meta"]["at"]), open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False)
        prev_txt = "chưa có" if d["first_run"] else f"{len(d['new'])} mới, {len(d['dropped'])} rớt"
        print(f"EXPORT {args.export} · {len(out['picks'])} mã · so lần trước: {prev_txt}")
        return 0
    if args.dry_run:
        print("\n---\n".join(msgs)); return 0
    if not token or not chat:
        print("Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID — chạy --dry-run hoặc cấu hình .env"); return 1
    tg_send(token, chat, msgs, excel_bytes(out), f"kci_san_co_phieu_{out['meta']['at']:%Y%m%d}.xlsx")
    json.dump(state_of(out["picks"], out["meta"]["at"]), open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"Đã gửi {len(msgs)} tin + Excel · {len(out['picks'])} mã trong danh sách săn.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Xuất báo cáo định giá ra PDF thật (fpdf2, thuần Python — chạy được trên Streamlit Cloud).

Đầu vào là Markdown của pipeline.report_markdown (tập con: #, -, bảng |, **đậm**, _nghiêng_) → PDF A4 có
trang bìa tóm tắt, tiêu đề/chân trang, đánh số trang.

Font: DejaVu Sans (đi kèm matplotlib) cho tiếng Việt; chữ Hàn dùng font dự phòng tìm theo thứ tự trong
KR_FONTS (Cloud: gói apt fonts-nanum khai báo ở packages.txt; macOS: Apple SD Gothic Neo). Không có font Hàn →
vẫn xuất PDF, chữ Hàn bị bỏ trống và có ghi chú ở chân trang.
Biểu tượng emoji (✅ ⚠️ 🟢 📈 …) không có trong font PDF → đổi sang ký hiệu tương đương.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from fpdf import FPDF
from fpdf.fonts import FontFace

KR_FONTS = [
    os.environ.get("KCI_KR_FONT", ""),
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",              # Debian/Ubuntu: fonts-nanum (Cloud)
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",       # fonts-noto-cjk
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",                   # macOS
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "C:/Windows/Fonts/malgun.ttf",                                  # Windows
]

# emoji / ký hiệu → ký tự có trong DejaVu Sans
SYMBOLS = {
    "✅": "✓", "❌": "✗", "⚠️": "⚠", "⚠": "⚠", "🟢": "●", "🟡": "●", "🔴": "●", "⚪": "○", "📈": "▲", "📉": "▼",
    "🆕": "+", "🔁": "↻", "📌": "•", "🕒": "", "💰": "", "🧮": "", "🤖": "", "📋": "", "🔍": "", "✓": "✓", "△": "△",
    "○": "○", "—": "—", "→": "→", "×": "×", "≥": "≥", "≤": "≤", "−": "−",
}
_SYM_COLOR = {"🟢": (30, 127, 52), "🟡": (196, 140, 0), "🔴": (215, 0, 21), "✅": (30, 127, 52),
              "❌": (215, 0, 21), "⚠️": (196, 140, 0)}
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")
_KEEP = set("✓✗⚠●○▲▼↻△")                                     # ký hiệu đã đổi sang — có trong DejaVu Sans

INK, MUTED, LINE, ACCENT, SOFT = (29, 29, 31), (110, 110, 115), (229, 229, 234), (0, 113, 227), (245, 245, 247)


def _font_paths() -> tuple[str, str, Optional[str]]:
    import matplotlib
    d = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
    kr = next((p for p in KR_FONTS if p and os.path.exists(p)), None)
    return os.path.join(d, "DejaVuSans.ttf"), os.path.join(d, "DejaVuSans-Bold.ttf"), kr


def clean(s: str) -> str:
    """Đổi emoji sang ký hiệu PDF hiển thị được; bỏ emoji còn lại."""
    for k, v in SYMBOLS.items():
        s = s.replace(k, v)
    return _EMOJI_RE.sub(lambda m: m.group(0) if m.group(0) in _KEEP else "", s).replace("\u00a0", " ")


def _inline(s: str) -> str:
    """Markdown nội dòng của báo cáo → markdown của fpdf2 (**đậm**, __nghiêng__)."""
    s = clean(s)
    s = re.sub(r"(?<![\w*])_(.+?)_(?![\w*])", r"__\1__", s)     # _nghiêng_ → __nghiêng__
    return s.replace("`", "")


class _PDF(FPDF):
    title_txt = ""
    has_kr = True

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("dv", size=7.5)
        self.set_text_color(*MUTED)
        self.cell(0, 6, self.title_txt, align="L")
        self.cell(0, 6, "KCI · Báo cáo định giá", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*LINE)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3)

    def footer(self):
        self.set_y(-12)
        self.set_font("dv", size=7)
        self.set_text_color(*MUTED)
        note = "Ước lượng có kỷ luật cho nghiên cứu — không phải khuyến nghị đầu tư."
        if not self.has_kr:
            note += " (Thiếu font tiếng Hàn: tên tiếng Hàn bị lược.)"
        self.cell(0, 5, note, align="L")
        self.cell(0, 5, f"{self.page_no()}/{{nb}}", align="R")


def _table(pdf: _PDF, rows: list[list[str]]):
    head = rows[0]
    body = [r for r in rows[1:] if not all(set(c) <= set("-: ") for c in r)]
    ncol = len(head)
    body = [(r + [""] * ncol)[:ncol] for r in body]
    size = 8 if ncol <= 5 else 7 if ncol <= 8 else 6.2
    pdf.set_font("dv", size=size)
    pdf.set_text_color(*INK)
    num = re.compile(r"^[\s+\-−]?[\d,.]+\s*(%|×|억|KRW)?$")
    is_num = [j > 0 and all(num.match(clean(r[j]).replace("**", "")) or clean(r[j]) in ("—", "") for r in body)
              for j in range(ncol)]
    aligns = ["RIGHT" if is_num[j] else "LEFT" for j in range(ncol)]
    # độ rộng cột theo bề rộng chữ thật: cột số giữ đủ rộng (không ngắt số), cột chữ chia phần còn lại
    pad = 2 * 1.2 + 1.5
    nat = []
    for j in range(ncol):
        ws = [pdf.get_string_width(clean(r[j]).replace("**", "")) for r in body]
        pdf.set_font("dv", "B", size)
        ws.append(pdf.get_string_width(clean(head[j]).replace("**", "")) * (1.0 if is_num[j] else 0.6))
        pdf.set_font("dv", size=size)
        nat.append(max(ws + [6]) + pad)
    avail = pdf.w - pdf.l_margin - pdf.r_margin
    fixed = sum(nat[j] for j in range(ncol) if is_num[j])
    txt = [j for j in range(ncol) if not is_num[j]]
    if sum(nat) <= avail:
        w = nat[:]
        extra = avail - sum(nat)
        tot = sum(nat[j] for j in txt) or 1
        for j in txt:
            w[j] += extra * nat[j] / tot
    else:
        rest = max(avail - fixed, 18 * len(txt))
        tot = sum(nat[j] for j in txt) or 1
        w = [nat[j] if is_num[j] else max(rest * nat[j] / tot, 18) for j in range(ncol)]
    with pdf.table(col_widths=w, text_align=aligns, markdown=True, line_height=size * 0.55,
                   headings_style=FontFace(emphasis="BOLD", color=INK, fill_color=SOFT),
                   borders_layout="HORIZONTAL_LINES", padding=1.2, first_row_as_headings=True) as t:
        for r in [head] + body:
            row = t.row()
            for c in r:
                row.cell(_inline(c))
    pdf.ln(2.5)


def report_pdf(md: str, title: str, summary: Optional[dict] = None) -> bytes:
    """Markdown báo cáo → PDF (bytes). `summary` (tùy chọn) = {price, fair, low, high, upside, verdict, confidence,
    date, ticker, name} để dựng ô tóm tắt trên trang 1."""
    reg, bold, kr = _font_paths()
    pdf = _PDF(format="A4")
    pdf.title_txt = clean(title)
    pdf.set_title(clean(title))
    pdf.set_author("KCI Screener")
    pdf.set_margins(16, 16, 16)
    pdf.set_auto_page_break(True, margin=16)
    pdf.add_font("dv", "", reg)
    pdf.add_font("dv", "B", bold)
    pdf.add_font("dv", "I", reg)
    pdf.add_font("dv", "BI", bold)
    fallbacks = []
    if kr:
        try:
            kw = {"collection_font_number": 0} if kr.lower().endswith(".ttc") else {}
            pdf.add_font("kr", "", kr, **kw)
            pdf.add_font("kr", "B", kr, **kw)
            fallbacks = ["kr"]
        except Exception:
            fallbacks = []
    pdf.has_kr = bool(fallbacks)
    if fallbacks:
        pdf.set_fallback_fonts(fallbacks, exact_match=False)
    pdf.alias_nb_pages()
    pdf.add_page()

    lines = md.splitlines()
    i = 0
    # ---- trang 1: tiêu đề + dòng phụ + ô tóm tắt
    if lines and lines[0].startswith("# "):
        pdf.set_font("dv", "B", 19)
        pdf.set_text_color(*INK)
        pdf.multi_cell(0, 9, clean(lines[0][2:]), align="L", new_x="LMARGIN", new_y="NEXT")
        i = 1
        if i < len(lines) and lines[i].startswith("_"):
            pdf.set_font("dv", size=8.5)
            pdf.set_text_color(*MUTED)
            pdf.multi_cell(0, 5, clean(lines[i].strip("_")), align="L", new_x="LMARGIN", new_y="NEXT")
            i += 1
        pdf.ln(3)
    if summary and summary.get("fair"):
        _summary_box(pdf, summary)

    while i < len(lines):
        ln = lines[i]
        if ln.startswith("|"):
            tbl = []
            while i < len(lines) and lines[i].startswith("|"):
                tbl.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            _table(pdf, tbl)
            continue
        if ln.startswith("- "):
            pdf.set_font("dv", size=9)
            pdf.set_text_color(*INK)
            while i < len(lines) and lines[i].startswith("- "):
                raw = lines[i][2:]
                col = next((c for e, c in _SYM_COLOR.items() if raw.startswith(e)), None)
                x0 = pdf.get_x()
                pdf.set_text_color(*(col or ACCENT))
                pdf.cell(4, 5, "•")
                pdf.set_text_color(*INK)
                pdf.multi_cell(0, 5, _inline(raw), markdown=True, align="L", new_x="LMARGIN", new_y="NEXT")
                pdf.set_x(x0)
                i += 1
            pdf.ln(1.5)
            continue
        m = len(ln) - len(ln.lstrip("#"))
        if 1 <= m <= 4 and ln[m:m + 1] == " ":
            txt = clean(ln[m + 1:])
            if pdf.get_y() > pdf.h - 45:                     # tiêu đề không đứng lẻ cuối trang
                pdf.add_page()
            pdf.ln(3 if m > 2 else 4)
            pdf.set_font("dv", "B", {1: 16, 2: 12.5, 3: 11, 4: 10}[m])
            pdf.set_text_color(*INK)
            pdf.multi_cell(0, 6.5, txt.replace("**", ""), align="L", new_x="LMARGIN", new_y="NEXT")
            if m <= 2:
                pdf.set_draw_color(*LINE)
                pdf.line(pdf.l_margin, pdf.get_y() + 0.5, pdf.w - pdf.r_margin, pdf.get_y() + 0.5)
                pdf.ln(2.5)
            i += 1
            continue
        if ln.strip():
            italic = ln.strip().startswith("_") and ln.strip().endswith("_")
            pdf.set_font("dv", size=8.5 if italic else 9)
            pdf.set_text_color(*(MUTED if italic else INK))
            pdf.multi_cell(0, 5, _inline(ln.strip("_") if italic else ln), markdown=True, align="L",
                           new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        i += 1
    return bytes(pdf.output())


def _summary_box(pdf: _PDF, s: dict):
    """Ô tóm tắt kiểu thẻ: giá · giá trị hợp lý · chênh lệch · kết luận."""
    x, y, w, h = pdf.l_margin, pdf.get_y(), pdf.w - pdf.l_margin - pdf.r_margin, 30
    pdf.set_fill_color(*SOFT)
    pdf.set_draw_color(*LINE)
    pdf.rect(x, y, w, h, style="DF", round_corners=True, corner_radius=4)
    up = s.get("upside")
    items = [("Giá hiện tại", f"{s['price']:,.0f}" if s.get("price") else "—", INK),
             ("Giá trị hợp lý", f"{s['fair']:,.0f}", ACCENT),
             ("Chênh lệch", f"{up:+.1f}%" if up is not None else "—",
              (30, 127, 52) if (up or 0) > 0 else (215, 0, 21) if (up or 0) < 0 else INK),
             ("Khoảng", f"{s['low']:,.0f} – {s['high']:,.0f}" if s.get("low") and s.get("high") else "—", INK)]
    cw = w / len(items)
    for k, (lab, val, col) in enumerate(items):
        pdf.set_xy(x + k * cw + 5, y + 4)
        pdf.set_font("dv", size=7.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(cw - 6, 4, lab)
        pdf.set_xy(x + k * cw + 5, y + 9)
        pdf.set_font("dv", "B", 13 if k < 3 else 9.5)
        pdf.set_text_color(*col)
        pdf.cell(cw - 6, 7, val)
    pdf.set_xy(x + 5, y + 20)
    pdf.set_font("dv", size=8.5)
    pdf.set_text_color(*INK)
    pdf.cell(w - 10, 6, clean(f"{s.get('verdict') or ''} · Độ tin cậy {s.get('confidence') or '—'} · đơn vị KRW"))
    pdf.set_xy(x, y + h + 4)

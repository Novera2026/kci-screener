"""Báo cáo PDF: dựng được từ Markdown báo cáo, có tiếng Việt, ký hiệu và bảng — không cần mạng."""
import report_pdf as R

MD = """# Báo cáo định giá — 삼성전자 (005930)
_Ngày 2026-11-10 · KCI Pipeline_

## Kết luận
- Giá hiện tại: **199,800 KRW**
- ✅ Chất lượng tốt · ⚠️ cần soi · ❌ rủi ro · 🟢 rẻ

## Bóc tách theo quý (Q1.26 → Q2.26)
| Chỉ tiêu | Q1.26 | Q2.26 |
|---|---:|---:|
| Doanh thu (억) | 1,338,734 | 1,714,995 |
| DT YoY % | +69.2% | +130.0% |

_Đây là ước lượng có kỷ luật cho nghiên cứu._
"""


def test_clean_maps_emoji_to_pdf_symbols():
    assert R.clean("✅ a ⚠️ b ❌ c 🟢 d 📈 e 🤖") == "✓ a ⚠ b ✗ c ● d ▲ e "


def test_report_pdf_builds_valid_pdf():
    b = R.report_pdf(MD, "Định giá 삼성전자 005930",
                     {"price": 199800, "fair": 175440, "low": 157591, "high": 461744, "upside": -12.2,
                      "verdict": "⚪ Giá gần giá trị", "confidence": "Medium"})
    assert b[:5] == b"%PDF-" and len(b) > 5000
    assert b.rstrip().endswith(b"%%EOF")


def test_report_pdf_without_summary_or_korean_font(monkeypatch):
    monkeypatch.setattr(R, "KR_FONTS", [])
    b = R.report_pdf(MD, "Định giá 005930")
    assert b[:5] == b"%PDF-"

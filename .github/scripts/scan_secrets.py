"""Quét bí mật trong phần THÊM MỚI của git diff (rà soát 18/11/2026, C1). Không cần secret nào.

Đỏ khi dòng thêm mới có:
  • chuỗi 40 ký tự hex (dạng khóa OpenDART) — trừ mã commit ghim action ở dòng `uses: …@<sha>`;
  • token bot Telegram (`<số>:<35 ký tự>`).
Không bao giờ in nguyên giá trị: chỉ in file:dòng và vài ký tự đầu.

Quét TỪNG commit trong base..head (khóa thêm ở commit 1 rồi xóa ở commit 2 vẫn nằm trong lịch sử công khai → vẫn đỏ).
Dùng:  python .github/scripts/scan_secrets.py <base> [<head>]     (base rỗng / toàn số 0 / lạ → chỉ quét commit head)
"""
from __future__ import annotations

import re
import subprocess
import sys

PATTERNS = {
    "chuỗi 40 hex (khóa DART?)": re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{40}(?![0-9A-Fa-f])"),
    "token bot Telegram": re.compile(r"(?<![0-9])[0-9]{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"),
}
_PINNED_ACTION = re.compile(r"^\s*(-\s*)?uses:\s*[\w./-]+@[0-9a-f]{40}\b")


def _mask(s: str) -> str:
    return s[:4] + "…" + f"({len(s)} ký tự)"


def scan_diff(diff: str) -> list[str]:
    """Văn bản `git diff -U0` → danh sách phát hiện 'file:dòng: loại (giá trị đã che)'."""
    hits, path, line, header = [], "?", 0, False
    for raw in diff.splitlines():
        if raw.startswith("diff "):                     # tiêu đề file: từ 'diff …' tới hunk '@@' đầu tiên
            header = True
            continue
        if header and raw.startswith("+++ "):           # ngoài tiêu đề, dòng thêm có nội dung '++ …' vẫn được quét
            path = raw[6:] if raw.startswith("+++ b/") else raw[4:]
            continue
        if raw.startswith("@@"):
            header = False
            m = re.search(r"\+(\d+)", raw)
            line = int(m.group(1)) if m else 0
            continue
        if not raw.startswith("+"):
            continue
        text = raw[1:]
        if not _PINNED_ACTION.match(text):
            for kind, rx in PATTERNS.items():
                for m in rx.finditer(text):
                    hits.append(f"{path}:{line}: {kind} ({_mask(m.group(0))})")
        line += 1
    return hits


def _known(rev: str) -> bool:
    return subprocess.run(["git", "rev-parse", "-q", "--verify", f"{rev}^{{commit}}"], capture_output=True).returncode == 0


def _git(*a: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=off", *a], capture_output=True, text=True, check=True).stdout


def _commits(base: str, head: str) -> list[str]:
    if not base or set(base) == {"0"} or not _known(base):      # nhánh mới / chạy tay / commit cũ đã bị ghi đè
        return [head]
    return _git("rev-list", "--reverse", f"{base}..{head}").split()


def _diff(base: str, head: str) -> str:
    """Nối diff -U0 của từng commit (commit đầu tiên so với cây rỗng; merge so với cha thứ nhất)."""
    return "".join(_git("diff-tree", "-p", "-U0", "--no-color", "--no-commit-id", "--root", "-m", "--first-parent", c)
                   for c in _commits(base, head))


def main(argv: list[str]) -> int:
    base = argv[1] if len(argv) > 1 else ""
    head = argv[2] if len(argv) > 2 else "HEAD"
    hits = scan_diff(_diff(base, head))
    for h in hits:
        print(f"::error::Có thể lộ bí mật — {h}")
    print(f"Quét bí mật: {len(hits)} phát hiện.")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

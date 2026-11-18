"""CI (rà soát 18/11/2026): nhóm concurrency tách theo sự kiện (I72), quét bí mật trong diff (C1).
Chuỗi giả trong file này được ghép lúc chạy để chính bước quét không bắt nhầm file test."""
import importlib.util
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, ".github", "workflows", "test.yml")
SCAN = os.path.join(ROOT, ".github", "scripts", "scan_secrets.py")

FAKE_HEX = "ab12" * 10                          # 40 ký tự hex (giống khóa DART)
FAKE_TG = "1234567890" + ":" + "AAbb_-" * 5 + "Zz9yX"   # token Telegram giả (35 ký tự sau dấu :)


def _wf() -> str:
    with open(WF, encoding="utf-8") as f:
        return f.read()


def _top_concurrency_group(text: str) -> str:
    m = re.search(r"^concurrency:\n\s+group:\s*(.+)$", text, re.M)
    assert m, "thiếu concurrency cấp workflow"
    return m.group(1).strip()


def _render(expr: str, **ctx) -> str:
    return re.sub(r"\$\{\{\s*github\.(\w+)\s*\}\}", lambda m: ctx[m.group(1)], expr)


def test_manual_run_does_not_share_group_with_push():
    """Bấm Run workflow trên main không được rơi vào cùng nhóm (cancel-in-progress) với lượt push đang chạy."""
    g = _top_concurrency_group(_wf())
    push = _render(g, event_name="push", ref="refs/heads/main")
    manual = _render(g, event_name="workflow_dispatch", ref="refs/heads/main")
    push2 = _render(g, event_name="push", ref="refs/heads/main")
    assert push != manual
    assert push == push2                           # hai lượt push liền nhau vẫn hủy lượt cũ như trước


def test_promote_waits_for_secret_scan():
    text = _wf()
    promote = text[text.index("\n  promote:"):]
    assert re.search(r"needs:\s*\[\s*test\s*,\s*secrets\s*\]", promote)
    assert "\n  secrets:" in text and "scan_secrets.py" in text
    sec = text[text.index("\n  secrets:"):text.index("\n  promote:")]
    assert "secrets." not in sec.replace("scan_secrets", "")    # bước quét không dùng secret nào


@pytest.fixture(scope="module")
def scan():
    spec = importlib.util.spec_from_file_location("scan_secrets", SCAN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _diff(*added: str, path: str = "x.py") -> str:
    body = "".join(f"+{ln}\n" for ln in added)
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1,{len(added)} @@\n{body}"


def test_scan_flags_dart_like_key_and_masks_it(scan):
    hits = scan.scan_diff(_diff("x = 1", f'KEY = "{FAKE_HEX}"'))
    assert len(hits) == 1 and hits[0].startswith("x.py:2:")
    assert FAKE_HEX not in hits[0]                 # không in nguyên giá trị


def test_scan_flags_telegram_token(scan):
    hits = scan.scan_diff(_diff(f"TELEGRAM_BOT_TOKEN={FAKE_TG}", path=".env.example"))
    assert len(hits) == 1 and "Telegram" in hits[0] and FAKE_TG not in hits[0]


def test_scan_ignores_pinned_actions_removed_lines_and_short_hex(scan):
    diff = _diff(f"      - uses: actions/checkout@{'0f' * 20}      # v4", "sha = 'abc123'",
                 "x = '" + "a" * 39 + "'", "y = '" + "a" * 41 + "'")
    diff += f"@@ -5 +0,0 @@\n-OLD = '{FAKE_HEX}'\n"
    assert scan.scan_diff(diff) == []


def test_scan_reads_added_line_starting_with_plus_plus(scan):
    """Dòng thêm có nội dung '++ …' hiện thành '+++ …' trong diff — không được coi là tiêu đề file."""
    hits = scan.scan_diff(_diff("x = 1", f"++ {FAKE_HEX}"))
    assert len(hits) == 1 and hits[0].startswith("x.py:2:")


def test_repo_sources_have_no_secret_like_strings(scan):
    """Toàn bộ file đang theo dõi (coi như thêm mới hết) không có chuỗi giống khóa — bước CI không đỏ oan."""
    if not shutil.which("git") or not os.path.exists(os.path.join(ROOT, ".git")):
        pytest.skip("không có git")
    files = [f for f in subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                                       text=True).stdout.split("\0") if f]   # -z: tên có dấu/emoji không bị bọc ngoặc
    assert any(f.startswith("pages/") and f.endswith(".py") for f in files)     # các trang (tên emoji) được quét
    hits = []
    for rel in files:
        if not rel.endswith((".py", ".yml", ".yaml", ".toml", ".md", ".txt", ".example", ".ini", ".json")):
            continue
        try:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                lines = f.read().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        hits += scan.scan_diff(_diff(*lines, path=rel))
    assert hits == [], hits


def test_scan_cli_on_real_git_diff(tmp_path):
    if not shutil.which("git"):
        pytest.skip("không có git")
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def run(*a):
        return subprocess.run([*g, *a], cwd=tmp_path, capture_output=True, text=True, env=env, check=True).stdout

    run("init", "-q")
    (tmp_path / "a.py").write_text("x = 1\n")
    run("add", "a.py")
    run("commit", "-qm", "dau")
    base = run("rev-parse", "HEAD").strip()
    (tmp_path / "a.py").write_text(f"x = 1\nkey = '{FAKE_HEX}'\n")
    run("commit", "-qam", "lo khoa")
    p = subprocess.run([sys.executable, SCAN, base, "HEAD"], cwd=tmp_path, capture_output=True, text=True, env=env)
    assert p.returncode == 1 and "a.py:2" in p.stdout and FAKE_HEX not in p.stdout
    p = subprocess.run([sys.executable, SCAN, "0" * 40, "HEAD"], cwd=tmp_path, capture_output=True, text=True,
                       env=env)                       # nhánh mới (before = 000…) → so với commit cha
    assert p.returncode == 1
    (tmp_path / "a.py").write_text("x = 2\n")
    run("commit", "-qam", "go khoa")
    p = subprocess.run([sys.executable, SCAN, "", "HEAD"], cwd=tmp_path, capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout
    p = subprocess.run([sys.executable, SCAN, base, "HEAD"], cwd=tmp_path, capture_output=True, text=True, env=env)
    assert p.returncode == 1 and FAKE_HEX not in p.stdout, p.stdout    # thêm rồi xóa trong cùng lượt push: vẫn đỏ

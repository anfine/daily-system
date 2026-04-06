from pathlib import Path
import re

DATE_RE = re.compile(r'^\d{4}-\d{1,2}-\d{1,2}$')
USER_HEADER_RE = re.compile(r"^#{2,3}\s+User$")
ASSISTANT_HEADER_RE = re.compile(r"^#{2,3}\s+Assistant$")

def segment(md_path: Path) -> list[list[str]]:
    with open(md_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    blocks = []
    i = 0
    n = len(lines)

    while i < n:
        # 找到一段 User 开始
        if not USER_HEADER_RE.match(lines[i].strip()):
            i += 1
            continue

        i += 1  # 指向日期行（或空行）
        # 跳过可能的空行
        while i < n and lines[i].strip() == "":
            i += 1

        if i >= n or not DATE_RE.match(lines[i].strip()):
            # 这段不是你预期结构，直接跳过，避免崩
            continue

        # 收集：日期 + 内容，直到 ### Assistant
        block = [lines[i].strip()]
        i += 1

        while i < n and not ASSISTANT_HEADER_RE.match(lines[i].strip()):
            block.append(lines[i])  # 保留原样（含空行）
            i += 1

        # 如果遇到 Assistant，就结算这个 block
        if i < n and ASSISTANT_HEADER_RE.match(lines[i].strip()):
            blocks.append(block)

        i += 1  # 越过 ### Assistant，继续找下一段

    return blocks
            

def archive(content: list[str]) -> tuple[str, Path, str]:
    timestamp = content[0]
    year, month, day = map(int, timestamp.split("-"))
    daily_md = "\n".join(content).strip()

    BASE_DIR = Path(__file__).resolve().parent
    ARCHIVE_ROOT = BASE_DIR / 'daily_logs'

    archive_path = ARCHIVE_ROOT  / f"{year:02d}"/ f"{month:02d}" / f"{day:02d}.md"
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    if archive_path.exists():
        old = archive_path.read_text(encoding="utf-8")
        if old == daily_md:
            return timestamp, archive_path, "skipped_same"  # 完全重复，不写

    archive_path.write_text(daily_md, encoding="utf-8")
    return timestamp, archive_path, "written"

if __name__ == "__main__":
    path = Path("/home/anfine/projects/daily-system/inbox/2026-04-05T07-25-27-459Z.md")
    blocks = segment(path)
    stats = {"written": 0, "skipped_same": 0, "failed": 0}
    failed = []

    for block in blocks:
        ts = block[0].strip()
        status = None
        try:
            _, path, status = archive(block)
            stats[status] += 1
        except Exception as e:
            stats["failed"] += 1
            failed.append((ts, str(e)))
        if status == "written":
            print("REWRITTEN:", ts, "->", path)

    print(stats)
    if failed:
        print("Failed samples:", failed[:3])

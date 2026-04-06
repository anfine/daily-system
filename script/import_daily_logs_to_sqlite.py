from pathlib import Path

from build_daily_char_meta_map import (
    META_LINE_RE,
    count_chars_from_text,
    date_from_path,
    iter_md_files,
    parse_meta_from_text,
    patch_done_before_cutover,
)
from db import get_conn
from meta_keys import build_meta_key


BASE_DIR = Path(__file__).resolve().parent
ARCHIVE_ROOT = BASE_DIR / "daily_logs"
CUTOVER_DATE = "2025-12-24"


def extract_entry_content(text: str) -> str:
    lines = text.splitlines()
    if len(lines) <= 1:
        return ""

    body_lines = []
    for line in lines[1:]:
        if line.strip() == "---" or META_LINE_RE.match(line):
            break
        body_lines.append(line)

    return "\n".join(body_lines).strip()


def ensure_meta(conn, label: str) -> str:
    row = conn.execute(
        "SELECT meta_key FROM metas WHERE label = ?",
        (label,),
    ).fetchone()
    if row:
        return row["meta_key"]

    meta_key = build_meta_key(label)
    conn.execute(
        """
        INSERT INTO metas (meta_key, label)
        VALUES (?, ?)
        ON CONFLICT(meta_key) DO UPDATE SET label = excluded.label
        """,
        (meta_key, label),
    )
    return meta_key


def load_archive_records(root: Path) -> list[dict]:
    records = []

    for md in sorted(iter_md_files(root)):
        text = md.read_text(encoding="utf-8")
        entry_date = date_from_path(md, root)
        meta_payload = parse_meta_from_text(text)

        records.append(
            {
                "entry_date": entry_date,
                "content": extract_entry_content(text),
                "char_count": count_chars_from_text(text),
                "meta_notes": meta_payload.get("notes", "").strip(),
                "metas": meta_payload.get("metas", {}),
            }
        )

    return records


def patch_historical_done(records: list[dict]) -> None:
    daily_meta_map = {
        record["entry_date"]: {
            "metas": record["metas"],
            "notes": record["meta_notes"],
        }
        for record in records
    }
    patch_done_before_cutover(daily_meta_map, cutover_date=CUTOVER_DATE)


def persist_records(records: list[dict]) -> None:
    with get_conn() as conn:
        for record in records:
            conn.execute(
                """
                INSERT INTO daily_entries (entry_date, content, char_count, meta_notes)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(entry_date) DO UPDATE SET
                    content = excluded.content,
                    char_count = excluded.char_count,
                    meta_notes = excluded.meta_notes,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    record["entry_date"],
                    record["content"],
                    record["char_count"],
                    record["meta_notes"],
                ),
            )

            conn.execute(
                "DELETE FROM daily_meta_status WHERE entry_date = ?",
                (record["entry_date"],),
            )

            for label, meta in record["metas"].items():
                meta_key = ensure_meta(conn, label)
                conn.execute(
                    """
                    INSERT INTO daily_meta_status (entry_date, meta_key, count, done)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(entry_date, meta_key) DO UPDATE SET
                        count = excluded.count,
                        done = excluded.done,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        record["entry_date"],
                        meta_key,
                        int(meta.get("count", 0)),
                        int(bool(meta.get("done"))),
                    ),
                )


def main() -> None:
    records = load_archive_records(ARCHIVE_ROOT)
    patch_historical_done(records)
    persist_records(records)

    print(
        {
            "ok": True,
            "imported_entries": len(records),
            "archive_root": str(ARCHIVE_ROOT),
            "cutover_date": CUTOVER_DATE,
        }
    )


if __name__ == "__main__":
    main()

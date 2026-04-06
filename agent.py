from flask import Flask, request, jsonify
from pathlib import Path
import subprocess
import datetime
import json
from archive_daily import archive
from build_daily_char_meta_map import (
    META_LINE_RE,
    parse_meta_from_text,
    count_chars_from_text,
)
from db import get_conn
from meta_keys import build_meta_key


app = Flask(__name__)

# ===== 路径统一从这里出发，避免写错 =====
BASE_DIR = Path(__file__).resolve().parent
DEBUG_FILE = BASE_DIR / "_agent_debug.json"
INBOX_DIR = BASE_DIR / "agent_inbox"


def get_db_health() -> dict:
    """
    最小数据库健康检查：
    - 验证能连上 SQLite
    - 验证 foreign_keys 已开启
    - 返回当前已存在的业务表
    """
    with get_conn() as conn:
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()

    return {
        "ok": True,
        "foreign_keys": bool(foreign_keys),
        "tables": [row["name"] for row in rows],
    }
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


def persist_text_to_db(log_date: str, text: str, meta_payload: dict, char_count: int) -> None:
    content = extract_entry_content(text)
    notes = meta_payload.get("notes", "").strip()
    metas = meta_payload.get("metas", {})

    with get_conn() as conn:
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
            (log_date, content, char_count, notes),
        )

        conn.execute(
            "DELETE FROM daily_meta_status WHERE entry_date = ?",
            (log_date,),
        )

        for label, item in metas.items():
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
                (log_date, meta_key, int(item.get("count", 0)), int(bool(item.get("done")))),
            )

def scp_to_server(*paths):
    """
    把指定文件 scp 到服务器
    """
    remote = "root@139.224.80.186:/var/www/html/calendar"

    cmd = ["scp", *map(str, paths), remote]

    # 最多只影响远程同步，不应该让本地保存直接 500
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "unknown scp error").strip()
        raise RuntimeError(err)


@app.after_request
def add_cors_headers(resp):
    # 允许你的网页来源访问（先用 * 省事；后面想收紧再改成具体 origin）
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.get("/ping")
def ping():
    """
    用来测试 agent 是否存活
    浏览器访问 http://127.0.0.1:8787/ping
    """
    db = get_db_health()
    return {"ok": True, "msg": "agent alive", "db": db}


@app.get("/db_health")
def db_health():
    """
    验证 agent 能否成功接入 SQLite
    """
    return jsonify(get_db_health())


@app.post("/echo")
def echo():
    """
    最小 POST 测试接口：
    - 接收 JSON
    - 打印
    - 顺手写到一个 debug 文件
    """
    data = request.get_json(force=True)

    payload = {
        "received_at": datetime.datetime.now().isoformat(),
        "data": data,
    }

    # 写一个本地文件，确认 agent 真能落盘
    DEBUG_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print("=== RECEIVED FROM WEB ===")
    print(payload)

    return jsonify({"ok": True})


@app.post("/save")
def save():
    """
    保存传入文本到 agent_inbox 目录
    """
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    if text is None or str(text).strip() == "":
        return jsonify({"error": "empty text"}), 400

    INBOX_DIR.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = INBOX_DIR / f"{ts}.txt"
    file_path.write_text(str(text), encoding="utf-8")

    # 直接消费收到的文本，完成 meta/char map patch
    meta_map_path = BASE_DIR / "daily_meta_map.json"
    char_map_path = BASE_DIR / "daily_char_map.json"

    if meta_map_path.exists():
        daily_meta_map = json.loads(meta_map_path.read_text(encoding="utf-8"))
    else:
        daily_meta_map = {}

    if char_map_path.exists():
        daily_char_map = json.loads(char_map_path.read_text(encoding="utf-8"))
    else:
        daily_char_map = {}

    content = str(text).splitlines()
    if not content:
        return jsonify({"error": "empty text"}), 400

    log_date = content[0].strip()
    archive(content)

    meta = parse_meta_from_text(str(text))
    char_count = count_chars_from_text(str(text))
    persist_text_to_db(log_date, str(text), meta, char_count)

    daily_meta_map[log_date] = meta
    daily_char_map[log_date] = char_count

    meta_map_path.write_text(
        json.dumps(daily_meta_map, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    char_map_path.write_text(
        json.dumps(daily_char_map, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # === 自动 scp 到服务器（失败不影响本地保存） ===
    sync_warning = None
    try:
        scp_to_server(meta_map_path, char_map_path)
    except Exception as e:
        sync_warning = f"remote sync failed: {e}"

    resp = {
        "ok": True,
        "message": "saved locally",
        "date": log_date,
    }
    if sync_warning:
        resp["warning"] = sync_warning

    return jsonify(resp)


@app.post("/consume_inbox")
def consume_inbox():
    """
    消费 agent_inbox 中的 txt：
    - 解析 meta
    - 统计字符数
    - patch 到 daily_meta_map.json / daily_char_map.json
    """
    INBOX_DIR.mkdir(exist_ok=True)

    meta_map_path = BASE_DIR / "daily_meta_map.json"
    char_map_path = BASE_DIR / "daily_char_map.json"

    # 读取已有 map（不存在就初始化）
    if meta_map_path.exists():
        daily_meta_map = json.loads(meta_map_path.read_text(encoding="utf-8"))
    else:
        daily_meta_map = {}

    if char_map_path.exists():
        daily_char_map = json.loads(char_map_path.read_text(encoding="utf-8"))
    else:
        daily_char_map = {}

    processed = []
    errors = []

    for txt in sorted(INBOX_DIR.glob("*.txt")):
        try:
            text = txt.read_text(encoding="utf-8")

            # 用“今天”作为日期（你之后想改成从网页传，也很容易）
            today = datetime.date.today().isoformat()

            meta = parse_meta_from_text(text)
            char_count = count_chars_from_text(text)

            daily_meta_map[today] = meta
            daily_char_map[today] = char_count

            processed.append(txt.name)

            # 处理成功就删除（或你也可以 move 到 processed/）
            txt.unlink()

        except Exception as e:
            errors.append({
                "file": txt.name,
                "error": str(e),
            })

    # 写回 map
    meta_map_path.write_text(
        json.dumps(daily_meta_map, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    char_map_path.write_text(
        json.dumps(daily_char_map, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    return jsonify({
        "ok": True,
        "processed": processed,
        "errors": errors,
    })



if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8787, debug=False)

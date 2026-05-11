from flask import Flask, request, jsonify, session
from pathlib import Path
import subprocess
import datetime
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from archive_daily import archive
from build_daily_char_meta_map import (
    META_LINE_RE,
    parse_meta_from_text,
    count_chars_from_text,
)
from db import get_conn
from meta_keys import build_meta_key


# ===== 路径统一从这里出发，避免写错 =====
BASE_DIR = Path(__file__).resolve().parent


def load_local_env() -> None:
    """
    Load local .env for direct `python agent.py` runs.
    Docker Compose already reads .env and passes values through environment.
    """
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


load_local_env()

app = Flask(__name__)
app.json.ensure_ascii = False
app.secret_key = os.environ.get("ADMIN_SESSION_SECRET", "dev-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

DEBUG_FILE = BASE_DIR / "_agent_debug.json"
INBOX_DIR = BASE_DIR / "agent_inbox"
QUEUE_DIR = BASE_DIR / "server_queue"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
INTERNAL_AGENT_TOKEN = os.environ.get("INTERNAL_AGENT_TOKEN", "dev-internal-token")
CLOUD_API_URL = os.environ.get("CLOUD_API_URL", "").rstrip("/")
AGENT_SYNC_TOKEN = os.environ.get("AGENT_SYNC_TOKEN", "")
CLOUD_SYNC_INTERVAL_SECONDS = int(os.environ.get("CLOUD_SYNC_INTERVAL_SECONDS", "30"))
LEGACY_SCP_REMOTE = os.environ.get("LEGACY_SCP_REMOTE", "").strip()
AUTH_EXEMPT_PATHS = {"/auth/login", "/auth/logout", "/auth/me"}
ALLOWED_ORIGINS = {
    origin.strip().rstrip("/")
    for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
}
LOGIN_FAILURE_WINDOW_SECONDS = 60
LOGIN_FAILURE_LIMIT = 5
LOGIN_LOCK_SECONDS = 600
login_failures: dict[str, dict] = {}


def get_client_ip() -> str:
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"


def is_logged_in() -> bool:
    return bool(session.get("admin_logged_in"))


def is_internal_request() -> bool:
    token = request.headers.get("X-Internal-Agent-Token", "")
    return bool(INTERNAL_AGENT_TOKEN) and hmac.compare_digest(token, INTERNAL_AGENT_TOKEN)


def get_login_lock_remaining(client_ip: str) -> int:
    state = login_failures.get(client_ip)
    if not state:
        return 0

    locked_until = float(state.get("locked_until", 0))
    remaining = int(locked_until - time.time())
    if remaining <= 0 and locked_until:
        login_failures.pop(client_ip, None)
        return 0
    return max(0, remaining)


def record_login_failure(client_ip: str) -> int:
    now = time.time()
    state = login_failures.get(client_ip)
    if not state or now - float(state.get("first_failed_at", 0)) > LOGIN_FAILURE_WINDOW_SECONDS:
        state = {"count": 0, "first_failed_at": now, "locked_until": 0}

    state["count"] = int(state.get("count", 0)) + 1
    if state["count"] >= LOGIN_FAILURE_LIMIT:
        state["locked_until"] = now + LOGIN_LOCK_SECONDS

    login_failures[client_ip] = state
    return get_login_lock_remaining(client_ip)


def clear_login_failures(client_ip: str) -> None:
    login_failures.pop(client_ip, None)


@app.before_request
def require_admin_login():
    if request.method == "OPTIONS":
        return "", 204

    if is_internal_request():
        return None

    if request.path in AUTH_EXEMPT_PATHS:
        return None

    if is_logged_in():
        return None

    return jsonify({"ok": False, "error": "login required"}), 401


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


def validate_date_arg(raw_date: str | None) -> tuple[str | None, str | None]:
    if raw_date is None or raw_date.strip() == "":
        return None, "missing required query param: date"

    date_str = raw_date.strip()
    try:
        datetime.date.fromisoformat(date_str)
    except ValueError:
        return None, "invalid date format, expected YYYY-MM-DD"

    return date_str, None


def get_entry_date_from_text(text: str) -> str:
    first_line = str(text or "").splitlines()[0].strip() if str(text or "").splitlines() else ""
    try:
        datetime.date.fromisoformat(first_line)
        return first_line
    except ValueError:
        return datetime.date.today().isoformat()


def build_queue_item_id(text: str) -> str:
    entry_date = get_entry_date_from_text(text)
    return f"{entry_date}_{datetime.datetime.now().strftime('%H%M%S')}_{uuid.uuid4().hex[:8]}.txt"


def get_queue_item_path(item_id: str) -> Path:
    if "/" in item_id or "\\" in item_id or item_id.startswith(".") or not item_id.endswith(".txt"):
        raise ValueError("invalid queue item id")
    return QUEUE_DIR / item_id


def queue_text(text: str) -> dict:
    if str(text).strip() == "":
        raise ValueError("empty text")

    QUEUE_DIR.mkdir(exist_ok=True)
    item_id = build_queue_item_id(text)
    item_path = QUEUE_DIR / item_id
    item_path.write_text(str(text), encoding="utf-8")
    return build_queue_item(item_path)


def build_queue_item(item_path: Path) -> dict:
    text = item_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    stat = item_path.stat()
    return {
        "id": item_path.name,
        "entry_date": get_entry_date_from_text(text),
        "created_at": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "preview": "\n".join(lines[:8]),
        "size": stat.st_size,
    }


def list_queue_items() -> list[dict]:
    if not QUEUE_DIR.exists():
        return []
    return [build_queue_item(path) for path in sorted(QUEUE_DIR.glob("*.txt"))]


def fetch_entry_payload(entry_date: str) -> dict | None:
    with get_conn() as conn:
        entry_row = conn.execute(
            """
            SELECT entry_date, content, char_count, meta_notes, created_at, updated_at
            FROM daily_entries
            WHERE entry_date = ?
            """,
            (entry_date,),
        ).fetchone()

        if not entry_row:
            return None

        meta_rows = conn.execute(
            """
            SELECT
                m.meta_key,
                m.label,
                m.category,
                m.unit,
                m.enabled,
                m.sort_order,
                COALESCE(dms.count, 0) AS count,
                COALESCE(dms.done, 0) AS done
            FROM metas AS m
            LEFT JOIN daily_meta_status AS dms
                ON dms.meta_key = m.meta_key
               AND dms.entry_date = ?
            ORDER BY m.sort_order ASC, m.id ASC
            """,
            (entry_date,),
        ).fetchall()

    return {
        "entry": {
            "entry_date": entry_row["entry_date"],
            "content": entry_row["content"],
            "char_count": entry_row["char_count"],
            "meta_notes": entry_row["meta_notes"],
            "created_at": entry_row["created_at"],
            "updated_at": entry_row["updated_at"],
        },
        "metas": [
            {
                "meta_key": row["meta_key"],
                "label": row["label"],
                "category": row["category"],
                "unit": row["unit"],
                "enabled": bool(row["enabled"]),
                "sort_order": row["sort_order"],
                "count": row["count"],
                "done": bool(row["done"]),
            }
            for row in meta_rows
        ],
    }


def fetch_metas_payload() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                m.meta_key,
                m.label,
                m.category,
                m.unit,
                m.enabled,
                m.sort_order,
                m.created_at,
                latest.entry_date AS latest_entry_date,
                COALESCE(latest.count, 0) AS count,
                COALESCE(latest.done, 0) AS done
            FROM metas AS m
            LEFT JOIN daily_meta_status AS latest
                ON latest.meta_key = m.meta_key
               AND latest.entry_date = (
                   SELECT MAX(dms.entry_date)
                   FROM daily_meta_status AS dms
                   WHERE dms.meta_key = m.meta_key
               )
            ORDER BY m.enabled DESC, m.sort_order ASC, m.id ASC
            """
        ).fetchall()

    return [
        {
            "meta_key": row["meta_key"],
            "label": row["label"],
            "category": row["category"],
            "unit": row["unit"],
            "enabled": bool(row["enabled"]),
            "sort_order": row["sort_order"],
            "created_at": row["created_at"],
            "latest_entry_date": row["latest_entry_date"],
            "count": row["count"],
            "done": bool(row["done"]),
        }
        for row in rows
    ]


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
    if not LEGACY_SCP_REMOTE:
        return

    remote = LEGACY_SCP_REMOTE

    cmd = ["scp", *map(str, paths), remote]

    # 最多只影响远程同步，不应该让本地保存直接 500
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "unknown scp error").strip()
        raise RuntimeError(err)


def save_text_to_agent(text: str) -> dict:
    if text is None or str(text).strip() == "":
        raise ValueError("empty text")

    INBOX_DIR.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = INBOX_DIR / f"{ts}.txt"
    file_path.write_text(str(text), encoding="utf-8")

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
        raise ValueError("empty text")

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

    return resp


class CloudSyncError(Exception):
    pass


def request_cloud(path: str, method: str = "GET", payload: dict | None = None, timeout: float = 10) -> tuple[dict, int]:
    if not CLOUD_API_URL:
        raise CloudSyncError("CLOUD_API_URL is not configured")
    if not AGENT_SYNC_TOKEN:
        raise CloudSyncError("AGENT_SYNC_TOKEN is not configured")

    data = None
    headers = {
        "Accept": "application/json",
        "X-Agent-Sync-Token": AGENT_SYNC_TOKEN,
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(f"{CLOUD_API_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}, resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"error": raw or e.reason}
        raise CloudSyncError(f"cloud api HTTP {e.code}: {payload.get('error') or payload}") from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise CloudSyncError(str(e)) from e


def checkin_cloud(processed: int = 0, last_error: str | None = None) -> dict:
    payload = {
        "processed": processed,
        "last_sync": datetime.datetime.now().isoformat(),
        "last_error": last_error,
    }
    data, _status = request_cloud("/agent/sync/checkin", method="POST", payload=payload)
    return data


def upload_display_json_to_cloud() -> dict:
    payload = {}
    for key, filename in (
        ("daily_char_map", "daily_char_map.json"),
        ("daily_meta_map", "daily_meta_map.json"),
    ):
        path = BASE_DIR / filename
        if path.exists():
            payload[key] = json.loads(path.read_text(encoding="utf-8"))

    if not payload:
        return {"ok": True, "written": []}

    data, _status = request_cloud("/agent/sync/display-json", method="POST", payload=payload)
    return data


def upload_metas_to_cloud() -> dict:
    data, _status = request_cloud("/agent/sync/metas", method="POST", payload={"metas": fetch_metas_payload()})
    return data


def sync_cloud_once() -> dict:
    checkin_cloud()
    queue_payload, _status = request_cloud("/agent/sync/queue")
    items = queue_payload.get("items", [])
    processed = []
    errors = []

    for item in items:
        item_id = item.get("id")
        text = item.get("text")
        if not item_id:
            errors.append({"id": None, "error": "missing queue item id"})
            continue
        try:
            save_text_to_agent(str(text or ""))
            request_cloud(f"/agent/sync/queue/{item_id}", method="DELETE")
            processed.append(item_id)
        except Exception as e:
            errors.append({"id": item_id, "error": str(e)})

    upload_result = {}
    if processed:
        upload_result = upload_display_json_to_cloud()
    meta_upload_result = upload_metas_to_cloud()

    last_error = "; ".join(f"{e['id']}: {e['error']}" for e in errors) if errors else None
    checkin_cloud(processed=len(processed), last_error=last_error)

    return {
        "ok": not errors,
        "processed": processed,
        "errors": errors,
        "uploaded": upload_result,
        "metas_uploaded": meta_upload_result,
    }


def sync_cloud_loop() -> None:
    while True:
        try:
            result = sync_cloud_once()
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), flush=True)
            try:
                checkin_cloud(last_error=str(e))
            except Exception:
                pass
        time.sleep(max(5, CLOUD_SYNC_INTERVAL_SECONDS))


@app.after_request
def add_cors_headers(resp):
    origin = request.headers.get("Origin")
    if origin and (not ALLOWED_ORIGINS or origin.rstrip("/") in ALLOWED_ORIGINS):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,DELETE,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Credentials"] = "true"
    return resp


@app.errorhandler(500)
def handle_internal_server_error(err):
    app.logger.exception("Unhandled server error: %s", err)
    return jsonify({"ok": False, "error": "internal server error"}), 500


@app.post("/auth/login")
def auth_login():
    client_ip = get_client_ip()
    lock_remaining = get_login_lock_remaining(client_ip)
    if lock_remaining > 0:
        return jsonify({
            "ok": False,
            "error": "too many failed login attempts",
            "retry_after": lock_remaining,
        }), 429

    if not ADMIN_PASSWORD:
        return jsonify({"ok": False, "error": "admin password is not configured"}), 500

    data = request.get_json(silent=True) or {}
    password = str(data.get("password", ""))
    if not hmac.compare_digest(password, ADMIN_PASSWORD):
        lock_remaining = record_login_failure(client_ip)
        if lock_remaining > 0:
            return jsonify({
                "ok": False,
                "error": "too many failed login attempts",
                "retry_after": lock_remaining,
            }), 429
        return jsonify({"ok": False, "error": "invalid password"}), 401

    clear_login_failures(client_ip)
    session["admin_logged_in"] = True
    return jsonify({"ok": True, "authenticated": True})


@app.post("/auth/logout")
def auth_logout():
    session.clear()
    return jsonify({"ok": True, "authenticated": False})


@app.get("/auth/me")
def auth_me():
    return jsonify({"ok": True, "authenticated": is_logged_in()})


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


@app.get("/entry")
def get_entry():
    """
    按日期读取单天 entry，并带上当天 meta 状态。
    """
    entry_date, error = validate_date_arg(request.args.get("date"))
    if error:
        return jsonify({"ok": False, "error": error}), 400

    payload = fetch_entry_payload(entry_date)
    if payload is None:
        return jsonify({"ok": False, "error": "entry not found", "date": entry_date}), 404

    return jsonify({"ok": True, **payload})


@app.get("/metas")
def get_metas():
    """
    读取所有 meta 定义，供前端初始化配置使用。
    """
    return jsonify({"ok": True, "metas": fetch_metas_payload()})


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
    try:
        return jsonify(save_text_to_agent(str(text)))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.post("/queue")
def create_queue_item():
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    try:
        item = queue_text(str(text))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "item": item})


@app.get("/queue")
def get_queue_items():
    return jsonify({"ok": True, "items": list_queue_items()})


@app.get("/queue/<item_id>")
def get_queue_item(item_id):
    try:
        item_path = get_queue_item_path(item_id)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if not item_path.exists():
        return jsonify({"ok": False, "error": "queue item not found"}), 404
    return jsonify({
        "ok": True,
        "item": build_queue_item(item_path),
        "text": item_path.read_text(encoding="utf-8"),
    })


@app.delete("/queue/<item_id>")
def delete_queue_item(item_id):
    try:
        item_path = get_queue_item_path(item_id)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if not item_path.exists():
        return jsonify({"ok": False, "error": "queue item not found"}), 404
    item_path.unlink()
    return jsonify({"ok": True})


@app.post("/queue/<item_id>/save")
def save_queue_item(item_id):
    try:
        item_path = get_queue_item_path(item_id)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if not item_path.exists():
        return jsonify({"ok": False, "error": "queue item not found"}), 404

    text = item_path.read_text(encoding="utf-8")
    try:
        resp = save_text_to_agent(text)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    item_path.unlink()
    resp["queue_item_id"] = item_id
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
    if len(sys.argv) > 1 and sys.argv[1] == "cloud-sync-once":
        print(json.dumps(sync_cloud_once(), ensure_ascii=False, indent=2))
        raise SystemExit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "cloud-sync-loop":
        sync_cloud_loop()
        raise SystemExit(0)

    app.run(host="0.0.0.0", port=8787, debug=False)

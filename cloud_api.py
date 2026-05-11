from flask import Flask, request, jsonify, session
from pathlib import Path
import datetime
import hmac
import json
import os
import time
import uuid


BASE_DIR = Path(__file__).resolve().parent


def load_local_env() -> None:
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

QUEUE_DIR = BASE_DIR / "server_queue"
DISPLAY_DATA_DIR = Path(os.environ.get("DISPLAY_DATA_DIR", BASE_DIR / "web/site/data"))
AGENT_STATE_FILE = Path(os.environ.get("AGENT_STATE_FILE", BASE_DIR / "agent_state.json"))
META_SNAPSHOT_FILE = Path(os.environ.get("META_SNAPSHOT_FILE", BASE_DIR / "meta_snapshot.json"))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
AGENT_SYNC_TOKEN = os.environ.get("AGENT_SYNC_TOKEN", "")
AGENT_ONLINE_WINDOW_SECONDS = int(os.environ.get("AGENT_ONLINE_WINDOW_SECONDS", "120"))
AUTH_EXEMPT_PATHS = {"/auth/login", "/auth/logout", "/auth/me"}
AGENT_SYNC_PATH_PREFIX = "/agent/sync/"
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


def is_agent_sync_request() -> bool:
    token = request.headers.get("X-Agent-Sync-Token", "")
    return bool(AGENT_SYNC_TOKEN) and hmac.compare_digest(token, AGENT_SYNC_TOKEN)


def read_agent_state() -> dict:
    if not AGENT_STATE_FILE.exists():
        return {}
    try:
        return json.loads(AGENT_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_agent_state(state: dict) -> None:
    AGENT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = AGENT_STATE_FILE.with_suffix(f"{AGENT_STATE_FILE.suffix}.tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(AGENT_STATE_FILE)


def build_agent_status() -> dict:
    state = read_agent_state()
    last_seen_ts = state.get("last_seen_ts")
    now = time.time()
    age_seconds = None
    if isinstance(last_seen_ts, (int, float)):
        age_seconds = max(0, int(now - last_seen_ts))

    return {
        "ok": True,
        "last_seen": state.get("last_seen"),
        "last_seen_ts": last_seen_ts,
        "age_seconds": age_seconds,
        "online": age_seconds is not None and age_seconds <= AGENT_ONLINE_WINDOW_SECONDS,
        "processed_total": int(state.get("processed_total", 0)),
        "last_sync": state.get("last_sync"),
        "last_error": state.get("last_error"),
        "queue_count": len(list_queue_items()),
    }


def update_agent_checkin(payload: dict | None = None) -> dict:
    now = time.time()
    state = read_agent_state()
    state.update({
        "last_seen": datetime.datetime.fromtimestamp(now).isoformat(),
        "last_seen_ts": now,
    })
    if payload:
        for key in ("last_sync", "last_error"):
            if key in payload:
                state[key] = payload.get(key)
        if "processed" in payload:
            state["processed_total"] = int(state.get("processed_total", 0)) + int(payload.get("processed") or 0)
    write_agent_state(state)
    return build_agent_status()


def write_display_json(name: str, value: dict) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")

    DISPLAY_DATA_DIR.mkdir(parents=True, exist_ok=True)
    target = DISPLAY_DATA_DIR / f"{name}.json"
    tmp_path = target.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(target)


def read_meta_snapshot() -> dict | None:
    if not META_SNAPSHOT_FILE.exists():
        return None
    try:
        return json.loads(META_SNAPSHOT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_meta_snapshot(metas: list[dict]) -> dict:
    payload = {
        "ok": True,
        "metas": metas,
        "updated_at": datetime.datetime.now().isoformat(),
    }
    META_SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = META_SNAPSHOT_FILE.with_suffix(f"{META_SNAPSHOT_FILE.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(META_SNAPSHOT_FILE)
    return payload


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


def get_entry_date_from_text(text: str) -> str:
    lines = str(text or "").splitlines()
    first_line = lines[0].strip() if lines else ""
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


def queue_text(text: str) -> dict:
    if str(text).strip() == "":
        raise ValueError("empty text")

    QUEUE_DIR.mkdir(exist_ok=True)
    item_path = QUEUE_DIR / build_queue_item_id(text)
    item_path.write_text(str(text), encoding="utf-8")
    return build_queue_item(item_path)


def list_queue_items() -> list[dict]:
    if not QUEUE_DIR.exists():
        return []
    return [build_queue_item(path) for path in sorted(QUEUE_DIR.glob("*.txt"))]


@app.before_request
def require_admin_login():
    if request.method == "OPTIONS":
        return "", 204

    if request.path.startswith(AGENT_SYNC_PATH_PREFIX):
        if is_agent_sync_request():
            return None
        return jsonify({"ok": False, "error": "invalid agent sync token"}), 403

    if request.path in AUTH_EXEMPT_PATHS:
        return None

    if is_logged_in():
        return None

    return jsonify({"ok": False, "error": "login required"}), 401


@app.after_request
def add_cors_headers(resp):
    origin = request.headers.get("Origin")
    if origin and (not ALLOWED_ORIGINS or origin.rstrip("/") in ALLOWED_ORIGINS):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,DELETE,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type,X-Agent-Sync-Token"
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
    return jsonify({"ok": True, "msg": "cloud api alive", "agent": build_agent_status()})


@app.get("/agent/status")
def agent_status():
    return jsonify(build_agent_status())


@app.post("/agent/sync/checkin")
def agent_sync_checkin():
    data = request.get_json(silent=True) or {}
    return jsonify(update_agent_checkin(data))


@app.get("/agent/sync/queue")
def agent_sync_get_queue():
    items = []
    for item in list_queue_items():
        item_path = get_queue_item_path(item["id"])
        items.append({**item, "text": item_path.read_text(encoding="utf-8")})
    return jsonify({"ok": True, "items": items})


@app.delete("/agent/sync/queue/<item_id>")
def agent_sync_delete_queue_item(item_id):
    try:
        item_path = get_queue_item_path(item_id)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if not item_path.exists():
        return jsonify({"ok": False, "error": "queue item not found"}), 404
    item_path.unlink()
    return jsonify({"ok": True})


@app.post("/agent/sync/display-json")
def agent_sync_display_json():
    data = request.get_json(silent=True) or {}
    written = []
    try:
        if "daily_char_map" in data:
            write_display_json("daily_char_map", data["daily_char_map"])
            written.append("daily_char_map.json")
        if "daily_meta_map" in data:
            write_display_json("daily_meta_map", data["daily_meta_map"])
            written.append("daily_meta_map.json")
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    if not written:
        return jsonify({"ok": False, "error": "no display json provided"}), 400

    update_agent_checkin({"last_sync": datetime.datetime.now().isoformat(), "last_error": None})
    return jsonify({"ok": True, "written": written})


@app.post("/agent/sync/metas")
def agent_sync_metas():
    data = request.get_json(silent=True) or {}
    metas = data.get("metas")
    if not isinstance(metas, list):
        return jsonify({"ok": False, "error": "metas must be a list"}), 400

    payload = write_meta_snapshot(metas)
    update_agent_checkin({"last_sync": datetime.datetime.now().isoformat(), "last_error": None})
    return jsonify({"ok": True, "count": len(payload["metas"]), "updated_at": payload["updated_at"]})


@app.get("/db_health")
def db_health():
    return jsonify({"ok": False, "error": "local agent data is not reachable from cloud-api"}), 503


@app.get("/metas")
def get_metas():
    snapshot = read_meta_snapshot()
    if snapshot is None:
        return jsonify({"ok": False, "error": "meta snapshot is not available yet"}), 503
    return jsonify(snapshot)


@app.get("/entry")
def get_entry():
    return jsonify({"ok": False, "error": "local agent data is not reachable from cloud-api"}), 503


@app.post("/save")
def save():
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    if str(text).strip() == "":
        return jsonify({"ok": False, "error": "empty text"}), 400

    try:
        item = queue_text(str(text))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({
        "ok": True,
        "queued": True,
        "message": "queued on server",
        "item": item,
    })


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

    return jsonify({
        "ok": False,
        "error": "queue items are saved by local agent sync; wait for agent check-in",
        "queue_item_id": item_id,
    }), 409


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8788, debug=False)

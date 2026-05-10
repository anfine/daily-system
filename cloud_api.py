from flask import Flask, request, jsonify, session
from pathlib import Path
import datetime
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
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
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
LOCAL_AGENT_URL = os.environ.get("LOCAL_AGENT_URL", "http://agent:8787").rstrip("/")
INTERNAL_AGENT_TOKEN = os.environ.get("INTERNAL_AGENT_TOKEN", "dev-internal-token")
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


class AgentRequestError(Exception):
    def __init__(self, status: int, payload: dict | None = None, message: str = "agent request failed"):
        super().__init__(message)
        self.status = status
        self.payload = payload or {"ok": False, "error": message}


def request_agent(path: str, method: str = "GET", payload: dict | None = None, timeout: float = 1.2) -> tuple[dict, int]:
    url = f"{LOCAL_AGENT_URL}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if INTERNAL_AGENT_TOKEN:
        headers["X-Internal-Agent-Token"] = INTERNAL_AGENT_TOKEN
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}, resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            error_payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            error_payload = {"ok": False, "error": raw or e.reason}
        raise AgentRequestError(e.code, error_payload, str(e)) from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise AgentRequestError(503, {"ok": False, "error": "agent unavailable"}, str(e)) from e


def proxy_agent_json(path: str, method: str = "GET", payload: dict | None = None):
    try:
        data, status = request_agent(path, method=method, payload=payload)
    except AgentRequestError as e:
        return jsonify(e.payload), e.status
    return jsonify(data), status


@app.before_request
def require_admin_login():
    if request.method == "OPTIONS":
        return "", 204

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
    try:
        agent, _status = request_agent("/ping", timeout=1.0)
    except AgentRequestError:
        agent = {"ok": False, "error": "agent unavailable"}

    return jsonify({"ok": True, "msg": "cloud api alive", "agent": agent})


@app.get("/db_health")
def db_health():
    return proxy_agent_json("/db_health")


@app.get("/metas")
def get_metas():
    return proxy_agent_json("/metas")


@app.get("/entry")
def get_entry():
    query = urllib.parse.urlencode(request.args)
    path = f"/entry?{query}" if query else "/entry"
    return proxy_agent_json(path)


@app.post("/save")
def save():
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    if str(text).strip() == "":
        return jsonify({"ok": False, "error": "empty text"}), 400

    try:
        payload, status = request_agent("/save", method="POST", payload={"text": str(text)}, timeout=1.2)
        return jsonify(payload), status
    except AgentRequestError as e:
        if e.status < 500:
            return jsonify(e.payload), e.status

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

    text = item_path.read_text(encoding="utf-8")
    try:
        payload, status = request_agent("/save", method="POST", payload={"text": text}, timeout=1.2)
    except AgentRequestError as e:
        return jsonify({**e.payload, "queue_item_id": item_id}), e.status

    if 200 <= status < 300 and payload.get("ok"):
        item_path.unlink()
        payload["queue_item_id"] = item_id
        payload["queue_removed"] = True

    return jsonify(payload), status


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8788, debug=False)

"""
src/auth/models.py

Auth database: users, API keys, and access logs.
"""

import os, secrets, sqlite3, hashlib
from datetime import datetime, timezone
from pathlib import Path

from config.settings import ROOT_DIR

try:
    from werkzeug.security import check_password_hash, generate_password_hash
    _WERKZEUG = True
except ImportError:
    _WERKZEUG = False

AUTH_DB_PATH: Path = ROOT_DIR / os.getenv("AUTH_DB_PATH", "data/auth.sqlite")


def get_auth_db() -> sqlite3.Connection:
    AUTH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(AUTH_DB_PATH), timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_login TEXT
);
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    key_prefix TEXT NOT NULL,
    rate_limit_hour INTEGER NOT NULL DEFAULT 1000,
    scopes TEXT NOT NULL DEFAULT 'jobs:read,exports:read,analytics:read,sources:read,markets:read',
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_used TEXT,
    usage_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS access_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    api_key_id INTEGER REFERENCES api_keys(id) ON DELETE SET NULL,
    auth_type TEXT,
    endpoint TEXT,
    method TEXT,
    ip TEXT,
    status_code INTEGER,
    response_ms INTEGER,
    ts TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE,
    ip TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 0,
    ts TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_access_logs_ts ON access_logs(ts);
CREATE INDEX IF NOT EXISTS idx_access_logs_user ON access_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_access_logs_key ON access_logs(api_key_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_login_attempts_user_ip_ts ON login_attempts(username, ip, ts);
"""


def init_auth_db():
    conn = get_auth_db()
    try:
        conn.executescript(_SCHEMA)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(api_keys)")}
        if "scopes" not in cols:
            conn.execute("ALTER TABLE api_keys ADD COLUMN scopes TEXT NOT NULL DEFAULT 'jobs:read,exports:read,analytics:read,sources:read,markets:read'")
        user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        if "google_id" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN google_id TEXT")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id)")
        if "auth_provider" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'local'")
        retention = max(1, int(os.getenv("ACCESS_LOG_RETENTION_DAYS", "90")))
        conn.execute("DELETE FROM access_logs WHERE ts < datetime('now', ?)", (f"-{retention} days",))
        conn.execute("DELETE FROM login_attempts WHERE ts < datetime('now', '-1 day')")
        conn.commit()
        if conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] == 0:
            pw = os.getenv("ADMIN_PASSWORD", "change-me-now")
            _create_user(conn, "admin", "admin@localhost", pw, role="admin")
            conn.commit()
            import logging; logging.getLogger(__name__).warning("Default admin created. Change password immediately.")
    finally:
        conn.close()


def _hash_password(password: str) -> str:
    if _WERKZEUG:
        return generate_password_hash(password, method="scrypt")
    salt = os.urandom(16).hex()
    return salt + ":" + hashlib.sha256((salt + password).encode()).hexdigest()


def _check_password(password: str, stored: str) -> bool:
    if _WERKZEUG and stored.startswith(("scrypt:", "pbkdf2:")):
        return check_password_hash(stored, password)
    try:
        salt, h = stored.split(":", 1)
        return secrets.compare_digest(hashlib.sha256((salt + password).encode()).hexdigest(), h)
    except Exception:
        return False


def _create_user(conn, username, email, password, role="viewer")-> int:
    if role not in {"admin", "viewer"}: raise ValueError("Invalid role")
    cur = conn.execute(
        "INSERT INTO users (username, email, password_hash, role) VALUES (?,?,?,?)",
        (username.strip(), email.strip().lower(), _hash_password(password), role),
    )
    return cur.lastrowid


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def authenticate_user(username: str, password: str):
    conn = get_auth_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
        if row and _check_password(password, row["password_hash"]):
            conn.execute("UPDATE users SET last_login=datetime('now') WHERE id=?", (row["id"],))
            conn.commit()
            return dict(row)
        return None
    finally:
        conn.close()


def login_is_rate_limited(username, ip, max_failures=5, window_minutes=15) -> bool:
    conn = get_auth_db()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE username=? AND ip=? AND success=0 AND ts > datetime('now', ?)",
            (username.strip(), ip, f"-{max(1,int(window_minutes))} minutes"),
        ).fetchone()[0]
        return n >= max(1, int(max_failures))
    finally:
        conn.close()


def record_login_attempt(username, ip, success: bool) -> None:
    conn = get_auth_db()
    try:
        conn.execute("INSERT INTO login_attempts (username, ip, success) VALUES (?,?,?)", (username.strip(), ip, int(success)))
        if success:
            conn.execute("DELETE FROM login_attempts WHERE username=? AND ip=? AND success=0", (username.strip(), ip))
        conn.commit()
    finally:
        conn.close()


def get_user_by_id(user_id: int):
    conn = get_auth_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_users() -> list:
    conn = get_auth_db()
    try:
        rows = conn.execute(
            "SELECT u.*, (SELECT COUNT(*) FROM api_keys k WHERE k.user_id=u.id AND k.revoked=0) as active_keys FROM users u ORDER BY u.created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_user(username, email, password, role="viewer") -> dict:
    conn = get_auth_db()
    try:
        uid = _create_user(conn, username, email, password, role)
        conn.commit()
        return dict(conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
    except sqlite3.IntegrityError:
        raise ValueError("Username or email already exists.")
    finally:
        conn.close()


def get_user_by_google_id(google_id: str):
    conn = get_auth_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE google_id=?", (google_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_email(email: str):
    conn = get_auth_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_google_user(google_id: str, email: str, name: str) -> dict:
    """First-time 'Continue with Google' sign-in — auto-creates a viewer account.

    password_hash is filled with a random, never-shared value so the NOT NULL
    constraint is satisfied but username/password login can never match it.
    """
    conn = get_auth_db()
    try:
        username = _unique_username_from(conn, name or email.split("@")[0])
        cur = conn.execute(
            "INSERT INTO users (username, email, password_hash, role, google_id, auth_provider) "
            "VALUES (?,?,?,?,?,?)",
            (username, email.strip().lower(), _hash_password(secrets.token_urlsafe(32)), "viewer", google_id, "google"),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone())
    except sqlite3.IntegrityError:
        raise ValueError("An account with that email already exists.")
    finally:
        conn.close()


def link_google_account(user_id: int, google_id: str) -> None:
    """Attach a Google identity to an existing local account (matched by email)."""
    conn = get_auth_db()
    try:
        conn.execute("UPDATE users SET google_id=? WHERE id=?", (google_id, user_id))
        conn.commit()
    finally:
        conn.close()


def touch_last_login(user_id: int) -> None:
    conn = get_auth_db()
    try:
        conn.execute("UPDATE users SET last_login=datetime('now') WHERE id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def _unique_username_from(conn, seed: str) -> str:
    base = "".join(c for c in seed.strip().lower().replace(" ", "_") if c.isalnum() or c == "_") or "user"
    base = base[:40]
    candidate = base
    n = 1
    while conn.execute("SELECT 1 FROM users WHERE username=?", (candidate,)).fetchone():
        n += 1
        candidate = f"{base}{n}"
    return candidate


def update_user(user_id: int, **fields) -> bool:
    allowed = {"username", "email", "role", "active"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates: return False
    if "role" in updates and updates["role"] not in {"admin", "viewer"}: raise ValueError("Invalid role")
    conn = get_auth_db()
    conn.isolation_level = None  # manual transaction control
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("SELECT role, active FROM users WHERE id=?", (user_id,)).fetchone()
        if not cur:
            conn.execute("ROLLBACK")
            return False
        removing = cur["role"]=="admin" and (updates.get("role","admin")!="admin" or int(updates.get("active",1))==0)
        if removing and conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0] <= 1:
            conn.execute("ROLLBACK")
            raise ValueError("Cannot disable or demote the last active admin")
        sql = "UPDATE users SET " + ", ".join(f"{k}=?" for k in updates) + " WHERE id=?"
        conn.execute(sql, list(updates.values()) + [user_id])
        conn.execute("COMMIT")
        return True
    except Exception:
        try: conn.execute("ROLLBACK")
        except Exception: pass
        raise
    finally:
        conn.close()


def change_password(user_id: int, new_password: str) -> bool:
    conn = get_auth_db()
    try:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (_hash_password(new_password), user_id))
        conn.commit(); return True
    finally:
        conn.close()


def generate_api_key(user_id, name, rate_limit_hour=1000, scopes="jobs:read,exports:read,analytics:read,sources:read,markets:read") -> dict:
    raw = "jmi_" + secrets.token_urlsafe(32)
    key_hash = _hash_key(raw)
    conn = get_auth_db()
    try:
        safe_rate = max(1, min(int(rate_limit_hour), 10000))
        ok_scopes = {"jobs:read","exports:read","analytics:read","sources:read","markets:read"}
        safe_scopes = ",".join(s.strip() for s in scopes.split(",") if s.strip() in ok_scopes) or "jobs:read"
        owner = conn.execute("SELECT active FROM users WHERE id=?", (user_id,)).fetchone()
        if not owner or not owner["active"]: raise ValueError("Owner must be active")
        cur = conn.execute(
            "INSERT INTO api_keys (user_id, name, key_hash, key_prefix, rate_limit_hour, scopes) VALUES (?,?,?,?,?,?)",
            (user_id, name, key_hash, raw[:12], safe_rate, safe_scopes),
        )
        conn.commit()
        row = dict(conn.execute("SELECT * FROM api_keys WHERE id=?", (cur.lastrowid,)).fetchone())
        row["key"] = raw
        return row
    finally:
        conn.close()


def authenticate_api_key(raw_key: str):
    key_hash = _hash_key(raw_key)
    conn = get_auth_db()
    try:
        row = conn.execute(
            "SELECT k.*, u.username, u.role, u.active as user_active FROM api_keys k JOIN users u ON k.user_id=u.id WHERE k.key_hash=? AND k.revoked=0 AND u.active=1",
            (key_hash,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def check_rate_limit(api_key_id: int, limit_per_hour: int) -> bool:
    conn = get_auth_db()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM access_logs WHERE api_key_id=? AND ts > datetime('now', '-1 hour')",
            (api_key_id,),
        ).fetchone()[0]
        return n < limit_per_hour
    finally:
        conn.close()


def mark_api_key_used(api_key_id: int) -> None:
    conn = get_auth_db()
    try:
        conn.execute("UPDATE api_keys SET last_used=datetime('now'), usage_count=usage_count+1 WHERE id=?", (api_key_id,))
        conn.commit()
    finally:
        conn.close()


def list_api_keys(user_id=None) -> list:
    conn = get_auth_db()
    try:
        if user_id:
            rows = conn.execute("SELECT k.*, u.username FROM api_keys k JOIN users u ON k.user_id=u.id WHERE k.user_id=? ORDER BY k.created_at DESC", (user_id,)).fetchall()
        else:
            rows = conn.execute("SELECT k.*, u.username FROM api_keys k JOIN users u ON k.user_id=u.id ORDER BY k.created_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def revoke_api_key(key_id: int, user_id=None) -> bool:
    conn = get_auth_db()
    try:
        if user_id is None:
            cur = conn.execute("UPDATE api_keys SET revoked=1 WHERE id=?", (key_id,))
        else:
            cur = conn.execute("UPDATE api_keys SET revoked=1 WHERE id=? AND user_id=?", (key_id, user_id))
        conn.commit(); return cur.rowcount > 0
    finally:
        conn.close()


def update_api_key_limit(key_id: int, rate_limit_hour: int) -> bool:
    conn = get_auth_db()
    try:
        conn.execute("UPDATE api_keys SET rate_limit_hour=? WHERE id=?", (max(1, min(int(rate_limit_hour), 10000)), key_id))
        conn.commit(); return True
    finally:
        conn.close()


def log_access(endpoint, method, ip, status_code, response_ms, user_id=None, api_key_id=None, auth_type=None):
    conn = get_auth_db()
    try:
        conn.execute(
            "INSERT INTO access_logs (user_id, api_key_id, auth_type, endpoint, method, ip, status_code, response_ms) VALUES (?,?,?,?,?,?,?,?)",
            (user_id, api_key_id, auth_type, endpoint, method, ip, status_code, response_ms),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def get_access_logs(limit=500, offset=0, user_id=None) -> list:
    conn = get_auth_db()
    try:
        base = "SELECT l.*, u.username, k.key_prefix, k.name as key_name FROM access_logs l LEFT JOIN users u ON l.user_id=u.id LEFT JOIN api_keys k ON l.api_key_id=k.id"
        if user_id:
            rows = conn.execute(base + " WHERE l.user_id=? ORDER BY l.ts DESC LIMIT ? OFFSET ?", (user_id, limit, offset)).fetchall()
        else:
            rows = conn.execute(base + " ORDER BY l.ts DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_access_stats() -> dict:
    conn = get_auth_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM access_logs").fetchone()[0]
        today = conn.execute("SELECT COUNT(*) FROM access_logs WHERE ts > datetime('now','start of day')").fetchone()[0]
        hour = conn.execute("SELECT COUNT(*) FROM access_logs WHERE ts > datetime('now','-1 hour')").fetchone()[0]
        top = [dict(r) for r in conn.execute("SELECT endpoint, COUNT(*) as cnt FROM access_logs GROUP BY endpoint ORDER BY cnt DESC LIMIT 10").fetchall()]
        return {"total": total, "today": today, "last_hour": hour, "top_endpoints": top}
    finally:
        conn.close()

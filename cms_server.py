import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from cms_content import BASE, Invalid, galleries, render, schema, validate
from cms_store import Conflict, StorageError, encode, initial, storage, update


MAX_BODY = 480_000
SESSION_TTL = 8 * 3600
COOKIE = "joo_admin_session"
PUBLIC_ASSETS = {
    "/admin": ("admin.html", "text/html"),
    "/admin.js": ("admin.js", "text/javascript"),
    "/admin.css": ("admin.css", "text/css"),
    "/cms-public.js": ("cms-public.js", "text/javascript"),
    "/cms-public.css": ("cms-public.css", "text/css"),
    "/cms-hero.js": ("cms-hero.js", "text/javascript"),
}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: https:; "
       "connect-src 'self'; frame-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")


def now():
    return datetime.now(timezone.utc)


def enabled():
    return os.getenv("VERCEL_ENV") != "production" or os.getenv("CMS_ENABLE_PRODUCTION") == "yes"


def read_documents(store):
    raw = store.get("documents")
    return json.loads(raw) if raw else initial()


def summary(state):
    return {"revision": state["revision"], "draft": state["draft"], "publishedAt": state["publishedAt"],
            "unpublished": state["draft"] != state["live"],
            "backups": [{k: v for k, v in backup.items() if k != "content"} for backup in state["backups"]]}


def backup(state, content, name):
    state["backups"].insert(0, {"id": secrets.token_hex(12), "label": name, "date": now().isoformat(),
                                "content": content})
    del state["backups"][25:]


def login_secret():
    secret = os.getenv("CMS_ADMIN_PASSWORD", "")
    if not secret:
        raise StorageError("CMS_ADMIN_PASSWORD is missing from this deployment")
    if len(secret) < 20:
        raise StorageError("The configured CMS_ADMIN_PASSWORD is shorter than 20 characters")
    return secret


def session_key(token):
    return "session:" + hmac.new(login_secret().encode(), token.encode(), hashlib.sha256).hexdigest()


class StudioHandler(BaseHTTPRequestHandler):
    def log_request(self, code="-", size="-"):
        # Query strings, session details and preview routes stay out of access logs.
        self.log_message("%s %s", self.command, code)

    def send(self, code, body=b"", mime="application/json", *, headers=None, private=True):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Cache-Control", "private, no-store" if private else "public, max-age=0, must-revalidate")
        for name, val in (headers or {}).items():
            self.send_header(name, val)
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def json(self, code, body, **kwargs):
        self.send(code, encode(body), **kwargs)

    def origin(self):
        https = bool(os.getenv("VERCEL"))
        host = self.headers.get("Host", "")
        configured = os.getenv("CMS_ORIGIN")
        return configured or (("https://" if https else "http://") + host)

    def check_origin(self):
        if self.headers.get("Origin") != self.origin() or self.headers.get("Sec-Fetch-Site") not in (None, "same-origin"):
            raise PermissionError("Request must originate from this website")

    def read_json(self):
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise Invalid("Use application/json")
        try:
            size = int(self.headers.get("Content-Length", ""))
            if not 0 < size <= MAX_BODY:
                raise Invalid("Request exceeds the content limit")
            value = json.loads(self.rfile.read(size))
        except (ValueError, UnicodeError, RecursionError) as exc:
            if isinstance(exc, Invalid):
                raise
            raise Invalid("Invalid JSON request") from exc
        if not isinstance(value, dict):
            raise Invalid("Request must be an object")
        return value

    def cookie(self, token="", ttl=0):
        secure = "; Secure" if self.origin().startswith("https://") else ""
        return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={ttl}{secure}"

    def token(self):
        try:
            jar = SimpleCookie(self.headers.get("Cookie", ""))
            token = jar[COOKIE].value if COOKIE in jar else ""
            return token if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) else ""
        except CookieError:
            return ""

    def authenticated(self, store):
        token = self.token()
        return bool(token and store.get(session_key(token)))

    def route(self):
        request = urlsplit(self.path)
        params = parse_qs(request.query, keep_blank_values=True)
        path = request.path
        if path == "/api/cms" and self.command in {"GET", "HEAD"}:
            if params.get("route") == ["site"]:
                path = "/"
            elif params.get("route") == ["admin"]:
                path = "/admin"
            elif params.get("route") == ["page"] and re.fullmatch(r"[a-z0-9-]{1,60}", params.get("slug", [""])[0]):
                path = "/pages/" + params["slug"][0]
        return path, params

    def asset(self, path):
        name, mime = PUBLIC_ASSETS[path]
        headers = {}
        if path == "/admin":
            headers = {"Content-Security-Policy": CSP, "X-Frame-Options": "DENY", "X-Robots-Tag": "noindex, nofollow"}
        path_on_disk = Path(__file__).resolve().parent / name
        self.send(200, path_on_disk.read_bytes(), mime, headers=headers)

    def public_page(self, path):
        headers = {"X-Robots-Tag": "noindex, nofollow"} if os.getenv("VERCEL_ENV") == "preview" else {}
        if enabled():
            try:
                state = read_documents(storage())
            except StorageError:
                if any(os.getenv(name) for name in ("UPSTASH_REDIS_REST_URL", "KV_REST_API_URL", "CMS_LOCAL_DB")):
                    self.log_error("CMS storage unavailable while serving public content")
                    if path == "/":
                        self.send(200, BASE, "text/html", headers=headers)
                        return
                    self.send(503, "The portfolio is temporarily unavailable.", "text/plain",
                              headers={**headers, "Retry-After": "30"})
                    return
            else:
                body = render(state["live"], path, analytics=True)
                self.send(200 if body else 404, body or "Page not found", "text/html", headers=headers)
                return
        self.send(200 if path == "/" else 404, BASE if path == "/" else "Page not found",
                  "text/html", private=False, headers=headers)

    def do_GET(self):
        self.process_cms_request()

    do_HEAD = do_GET

    def do_POST(self):
        self.process_cms_request()

    def process_cms_request(self):
        try:
            self.dispatch()
        except PermissionError as exc:
            self.json(403, {"error": str(exc)})
        except Invalid as exc:
            self.json(400, {"error": str(exc)})
        except Conflict as exc:
            self.json(409, {"error": str(exc)})
        except StorageError as exc:
            self.json(503, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            self.log_error("CMS error: %s", type(exc).__name__)
            self.json(500, {"error": "An unexpected error occurred"})

    def dispatch(self):
        path, params = self.route()
        read = self.command in {"GET", "HEAD"}
        if read and path in PUBLIC_ASSETS:
            return self.asset(path)
        if read and (path in {"/", "/index.html"} or path.startswith("/pages/")):
            return self.public_page("/" if path == "/index.html" else path)
        if path != "/api/cms":
            return self.json(404, {"error": "Route not found"})
        if not enabled():
            return self.json(503, {"error": "Dashboard access is limited to staging."})
        action = params.get("action", [""])[0]
        if not read:
            self.check_origin()
            if self.headers.get("X-Joo-CMS") != "1":
                raise PermissionError("Required request header is missing")
        if action == "session" and read:
            try:
                return self.json(200, {"authenticated": self.authenticated(storage()), "configured": bool(login_secret())})
            except StorageError as exc:
                return self.json(200, {"authenticated": False, "configured": False, "error": str(exc)})
        store = storage()
        if action == "event" and not read:
            return self.event(store)
        if action == "login" and not read:
            return self.login(store)
        if not self.authenticated(store):
            return self.json(401, {"error": "Sign in to continue"})
        if read:
            return self.admin_read(store, action, params)
        return self.admin_write(store, action, self.read_json())

    def login(self, store):
        data = self.read_json()
        candidate = data.get("password")
        if not isinstance(candidate, str) or len(candidate) > 512:
            raise Invalid("Enter the admin password")
        secret = login_secret()
        ip = (self.headers.get("x-vercel-forwarded-for", "").split(",")[0].strip()
              if os.getenv("VERCEL") else self.client_address[0])
        bucket = int(time.time()) // 900
        address = hmac.new(secret.encode(), ip.encode(), hashlib.sha256).hexdigest()[:20]
        client = store.increment("login:" + str(bucket), address, 901)
        total = store.increment("login:" + str(bucket), "total", 901)
        if client > 10 or total > 80:
            return self.json(429, {"error": "Too many attempts. Try again in a few minutes."},
                             headers={"Retry-After": "900"})
        if not hmac.compare_digest(hashlib.sha256(candidate.encode()).digest(),
                                   hashlib.sha256(secret.encode()).digest()):
            return self.json(401, {"error": "Incorrect password"})
        old_token = self.token()
        if old_token:
            store.delete(session_key(old_token))
        token = secrets.token_urlsafe(32)
        store.set_expiring(session_key(token), "1", SESSION_TTL)
        return self.json(200, {"authenticated": True}, headers={"Set-Cookie": self.cookie(token, SESSION_TTL)})

    def admin_read(self, store, action, params):
        if action == "analytics":
            dates = [(now().date() - timedelta(days=offset)).isoformat() for offset in reversed(range(30))]
            return self.json(200, {"days": store.analytics(dates)})
        state = read_documents(store)
        if action == "content":
            return self.json(200, {"schema": {**schema(), "galleries": galleries()}, **summary(state)})
        if action == "preview":
            body = render(state["draft"], params.get("path", ["/"])[0], preview=True)
            return self.send(200 if body else 404, body or "Save this page in the draft to preview it", "text/html",
                             headers={"X-Frame-Options": "SAMEORIGIN", "X-Robots-Tag": "noindex, nofollow"})
        if action == "export":
            backup_id = params.get("id", [""])[0]
            content = state["draft"]
            if backup_id:
                item = next((item for item in state["backups"] if item["id"] == backup_id), None)
                if item is None:
                    return self.json(404, {"error": "Backup not found"})
                content = item["content"]
            return self.json(200, {"format": "joo-films-cms/v1", "base": schema()["base"], "content": content},
                             headers={"Content-Disposition": 'attachment; filename="joo-films-backup.json"'})
        return self.json(404, {"error": "Unknown dashboard action"})

    def admin_write(self, store, action, data):
        if action == "logout":
            store.delete(session_key(self.token()))
            return self.json(200, {"authenticated": False}, headers={"Set-Cookie": self.cookie()})
        if action not in {"save", "publish", "backup", "restore", "import"}:
            return self.json(404, {"error": "Unknown dashboard action"})
        revision = data.get("revision")
        if type(revision) is not int:
            raise Invalid("Missing revision")
        content = None
        if action in {"save", "import"}:
            if action == "import":
                bundle = data.get("bundle")
                if not isinstance(bundle, dict) or bundle.get("format") != "joo-films-cms/v1" or bundle.get("base") != schema()["base"]:
                    raise Invalid("Backup format or portfolio baseline does not match")
                content = validate(bundle.get("content"))
            else:
                content = validate(data.get("draft"))
            render(content)

        def mutate(state):
            if action == "save":
                state["draft"] = content
            if action == "publish":
                if state["draft"] != state["live"]:
                    backup(state, state["live"], "Before publish")
                    state["live"] = state["draft"]
                    state["publishedAt"] = now().isoformat()
            if action == "backup":
                backup(state, state["draft"], "Draft snapshot")
            if action in {"restore", "import"}:
                restored = content
                if action == "restore":
                    item = next((item for item in state["backups"] if item["id"] == data.get("id")), None)
                    if not item:
                        raise Invalid("Backup not found")
                    restored = item["content"]
                backup(state, state["draft"], "Before restore" if action == "restore" else "Before import")
                state["draft"] = restored
        return self.json(200, summary(update(store, revision, mutate)))

    def event(self, store):
        value = self.read_json()
        path, kind = value.get("path"), value.get("event")
        if not isinstance(path, str) or not (path in {"/", "/index.html"} or re.fullmatch(r"/pages/[a-z0-9-]{1,60}", path)):
            raise Invalid("Unknown analytics page")
        if not isinstance(kind, str) or kind not in {"view", "email", "whatsapp", "instagram", "video"}:
            raise Invalid("Unknown analytics event")
        if path.startswith("/pages/") and not any(path == "/pages/" + page["slug"] for page in read_documents(store)["live"]["pages"]):
            raise Invalid("Unknown analytics page")
        store.increment("analytics:" + now().date().isoformat(), kind + "|" + ("/" if path == "/index.html" else path), 31 * 86400)
        self.send(204)


def serve():
    port = int(os.getenv("PORT", "8765"))
    ThreadingHTTPServer(("127.0.0.1", port), StudioHandler).serve_forever()

import json
import os
import sqlite3
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

from cms_content import empty


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def initial():
    return {"revision": 0, "draft": empty(), "live": empty(), "backups": [], "publishedAt": None}


CAS = """
local current = redis.call('GET', KEYS[1])
if (current or '') ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2])
return 1
"""
COUNT = """
local count = redis.call('HINCRBY', KEYS[1], ARGV[1], 1)
if redis.call('TTL', KEYS[1]) < 0 then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end
return count
"""


class StorageError(Exception):
    pass


class Conflict(Exception):
    pass


class Redis:
    def __init__(self, url, token, prefix):
        if not url.startswith("https://"):
            raise StorageError("Redis URL must use HTTPS")
        self.url, self.token, self.prefix = url.rstrip("/"), token, prefix

    def run(self, *args):
        request = Request(self.url, data=encode(args).encode(), method="POST", headers={
            "Authorization": "Bearer " + self.token, "Content-Type": "application/json",
            "User-Agent": "JooFilms-CMS/1.0",
        })
        try:
            with urlopen(request, timeout=4) as response:
                payload = json.load(response)
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise StorageError("Storage is temporarily unavailable") from exc
        if "error" in payload or "result" not in payload:
            raise StorageError("Storage command failed")
        return payload["result"]

    def key(self, key):
        return self.prefix + ":" + key

    def get(self, key):
        return self.run("GET", self.key(key))

    def set_expiring(self, key, value, ttl):
        self.run("SET", self.key(key), value, "EX", ttl)

    def delete(self, key):
        self.run("DEL", self.key(key))

    def cas(self, key, previous, next_value):
        return self.run("EVAL", CAS, 1, self.key(key), previous or "", next_value) == 1

    def increment(self, key, field, ttl):
        return self.run("EVAL", COUNT, 1, self.key(key), field, ttl)

    def counts(self, key):
        values = self.run("HGETALL", self.key(key)) or []
        if isinstance(values, dict):
            return {k: int(v) for k, v in values.items()}
        return {key: int(val) for key, val in zip(values[::2], values[1::2])}

    def analytics(self, days):
        # One pipeline request keeps report latency independent of the date range.
        request = Request(self.url + "/pipeline", method="POST",
                          data=encode([["HGETALL", self.key("analytics:" + day)] for day in days]).encode(),
                          headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json",
                                   "User-Agent": "JooFilms-CMS/1.0"})
        try:
            with urlopen(request, timeout=4) as response:
                entries = json.load(response)
            result = {}
            for day, entry in zip(days, entries):
                values = entry["result"] or []
                result[day] = ({k: int(v) for k, v in values.items()} if isinstance(values, dict)
                               else {k: int(v) for k, v in zip(values[::2], values[1::2])})
            if len(result) != len(days):
                raise ValueError("Incomplete report")
            return result
        except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as exc:
            raise StorageError("Analytics storage is temporarily unavailable") from exc


class Local:
    def __init__(self, file):
        self.file = file
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT, expires INTEGER);
                CREATE TABLE IF NOT EXISTS counters (
                    key TEXT, field TEXT, value INTEGER, expires INTEGER, PRIMARY KEY (key, field)
                );
            """)

    def connect(self):
        return sqlite3.connect(self.file, timeout=7)

    def get(self, key):
        with self.connect() as db:
            row = db.execute("SELECT value FROM kv WHERE key=? AND (expires IS NULL OR expires>?)",
                             (key, time.time())).fetchone()
            return row[0] if row else None

    def set_expiring(self, key, value, ttl):
        with self.connect() as db:
            db.execute("DELETE FROM kv WHERE expires <= ?", (time.time(),))
            db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?, ?)", (key, value, time.time() + ttl))

    def delete(self, key):
        with self.connect() as db:
            db.execute("DELETE FROM kv WHERE key=?", (key,))

    def cas(self, key, previous, next_value):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            if (row[0] if row else None) != previous:
                return False
            db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?, NULL)", (key, next_value))
            return True

    def increment(self, key, field, ttl):
        with self.connect() as db:
            now = time.time()
            db.execute("DELETE FROM counters WHERE expires <= ?", (now,))
            db.execute("""INSERT INTO counters VALUES (?, ?, 1, ?)
                        ON CONFLICT(key, field) DO UPDATE SET value=value+1""", (key, field, now + ttl))
            return db.execute("SELECT value FROM counters WHERE key=? AND field=?", (key, field)).fetchone()[0]

    def analytics(self, days):
        with self.connect() as db:
            return {day: dict(db.execute("SELECT field, value FROM counters WHERE key=? AND expires>?",
                                         ("analytics:" + day, time.time()))) for day in days}


def storage():
    url, token = os.getenv("UPSTASH_REDIS_REST_URL"), os.getenv("UPSTASH_REDIS_REST_TOKEN")
    if url and token:
        environment = os.getenv("VERCEL_ENV", "development")
        branch = os.getenv("VERCEL_GIT_COMMIT_REF", "standalone") if environment == "preview" else ""
        return Redis(url, token, ":".join(("joofilms", os.getenv("CMS_NAMESPACE", "portfolio"), environment, branch)))
    if os.getenv("CMS_LOCAL_DB") and not os.getenv("VERCEL"):
        return Local(os.environ["CMS_LOCAL_DB"])
    raise StorageError("Set up persistent CMS storage to enable the dashboard")


def update(store, revision, mutate):
    raw = store.get("documents")
    state = json.loads(raw) if raw else initial()
    if revision != state["revision"]:
        raise Conflict("A newer edit is available. Reload the dashboard before saving.")
    mutate(state)
    state["revision"] += 1
    encoded = encode(state)
    if len(encoded.encode()) > 8_000_000:
        raise StorageError("Storage limit reached. Export and remove older backups.")
    if not store.cas("documents", raw, encoded):
        raise Conflict("A newer edit is available. Reload the dashboard before saving.")
    return state

import copy
import io
import json
import os
import secrets
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from api.cms import handler
from cms_content import BASE, DEFAULT_HERO, empty, render, schema


class Request:
    def __init__(self, data):
        self.input = io.BytesIO(data)
        self.output = io.BytesIO()

    def makefile(self, mode, *args):
        class Buffer(io.BytesIO):
            def close(self):
                pass
        return self.input if "r" in mode else Buffer()

    def sendall(self, data):
        self.output.write(data)


class QuietHandler(handler):
    def log_message(self, *args):
        pass


class Studio(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.secret = secrets.token_urlsafe(32)
        self.config = {
            "CMS_LOCAL_DB": str(Path(self.temp.name) / "browser-test.sqlite"),
            "CMS_ADMIN_PASSWORD": self.secret,
        }
        env = patch.dict(os.environ, self.config, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.token = None

    def request(self, method, path, payload=None, *, auth=True, origin="http://cms.test"):
        headers = ["Host: cms.test"]
        if auth and self.token:
            headers.append("Cookie: " + self.token)
        if payload is not None:
            body = json.dumps(payload).encode()
            headers += ["Content-Type: application/json", "Content-Length: " + str(len(body)),
                        "Origin: " + origin, "X-Joo-CMS: 1"]
        else:
            body = b""
        data = ("\r\n".join([f"{method} {path} HTTP/1.0", *headers]) + "\r\n\r\n").encode() + body
        mock = Request(data)
        QuietHandler(mock, ("127.0.0.1", 1000), None)
        head, encoded = mock.output.getvalue().split(b"\r\n\r\n", 1)
        lines = head.decode().split("\r\n")
        status = int(lines[0].split()[1])
        headers = dict(line.split(": ", 1) for line in lines[1:])
        response = json.loads(encoded) if encoded and headers["Content-Type"].startswith("application/json") else encoded.decode()
        return status, headers, response

    def action(self, name, payload=None, **kwargs):
        return self.request("GET" if payload is None else "POST", "/api/cms?action=" + name, payload, **kwargs)

    def sign_in(self):
        status, headers, _ = self.action("login", {"password": self.secret})
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly; SameSite=Strict", headers["Set-Cookie"])
        self.token = headers["Set-Cookie"].split(";", 1)[0]

    def test_baseline_is_unchanged(self):
        self.assertEqual(render(empty()), BASE)
        self.assertEqual(len([f for f in schema()["fields"] if f["kind"] == "video"]), 80)
        self.assertEqual(len([i for i in schema()["items"] if i["group"] == "clients"]), 105)
        self.assertEqual(self.request("GET", "/api/cms?route=site")[2].replace(
            '<script src="/cms-public.js" data-mode="analytics"></script>', ""), BASE)

    def test_access_and_origin(self):
        self.assertEqual(self.action("content")[0], 401)
        self.assertEqual(self.action("preview")[0], 401)
        self.assertEqual(self.action("login", {"password": "wrong"})[0], 401)
        self.assertEqual(self.action("login", {"password": self.secret}, origin="https://cross.test")[0], 403)
        self.sign_in()
        self.assertEqual(self.request("GET", "/api/cms?route=admin")[0], 200)
        self.assertIn("frame-ancestors 'none'", self.request("GET", "/admin")[1]["Content-Security-Policy"])
        self.assertEqual(self.action("backup", {"revision": 0}, origin="https://cross.test")[0], 403)
        self.assertEqual(self.action("logout", {})[0], 200)
        self.assertEqual(self.action("content")[0], 401)

    def test_draft_preview_publish_restore_import_and_analytics(self):
        self.sign_in()
        state = self.action("content")[2]
        draft = state["draft"]
        field = next(f for f in state["schema"]["fields"] if f["group"] == "services" and f["value"] == "Services")
        draft["fields"][field["id"]] = "Studio Test <script>never execute</script>"
        draft["seo"]["description"] = "Joo test search snippet"
        draft["home"].append({"id": "logo-1", "type": "logo", "title": "Test logo", "text": "",
                              "url": "https://example.com/logo.png", "layout": "landscape", "target": "clients"})
        draft["hero"] = copy.deepcopy(DEFAULT_HERO)
        draft["hero"]["clips"] = [{"url": "https://vimeo.com/1186982350/testHash", "start": 3, "seconds": 7}]
        draft["hero"]["overlay"] = 40
        draft["pages"].append({"slug": "color", "title": "Color studio", "menu": True, "seo": {},
                               "blocks": [{"id": "video-test", "type": "video", "layout": "portrait",
                                           "text": "", "title": "Portrait sample", "url": "https://youtu.be/FE21pT6DPwg"}]})
        self.assertEqual(self.action("save", {"revision": 0, "draft": draft})[0], 200)
        self.assertNotIn("Studio Test", self.request("GET", "/")[2])
        self.assertEqual(self.request("GET", "/pages/color")[0], 404)
        status, headers, preview = self.action("preview")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("Studio Test &lt;script&gt;never execute&lt;/script&gt;", preview)
        self.assertIn("Joo test search snippet", preview)
        self.assertIn('alt="Test logo"', preview)
        self.assertIn('"access":"testHash","seconds":7,"start":3', preview)
        self.assertIn("rgba(0,0,0,0.40)", preview)
        self.assertNotIn('data-mode="analytics"', preview)
        published = self.action("publish", {"revision": 1})[2]
        self.assertIn("Studio Test", self.request("GET", "/")[2])
        path = "/api/cms?route=page&slug=color"
        page = self.request("GET", path)[2]
        self.assertIn("Color studio", page)
        self.assertIn("films films-portrait", page)
        self.assertIn("https://www.youtube.com/embed/FE21pT6DPwg", page)
        self.assertNotIn('id="hero"', page)
        self.assertNotIn("Joo test search snippet", page)
        backup = published["backups"][0]["id"]
        self.assertEqual(self.action("backup", {"revision": 2})[0], 200)
        exported = self.action("export")[2]
        self.assertEqual(exported["content"], draft)
        self.assertEqual(self.action("restore", {"revision": 3, "id": backup})[0], 200)
        self.assertNotIn("Studio Test", self.action("preview")[2])
        self.assertIn("Studio Test", self.request("GET", "/")[2])
        self.assertEqual(self.action("import", {"revision": 4, "bundle": exported})[2]["draft"], draft)
        self.assertEqual(self.action("event", {"path": "/pages/color", "event": "view"}, auth=False)[0], 204)
        self.assertEqual(self.action("event", {"path": "/pages/unknown", "event": "view"}, auth=False)[0], 400)
        result = self.action("analytics")[2]["days"]
        self.assertEqual(sum(day.get("view|/pages/color", 0) for day in result.values()), 1)

    def test_validation_concurrent_edit_and_production_gate(self):
        self.sign_in()
        draft = empty()
        field = next(f for f in schema()["fields"] if f["kind"] == "video")
        for invalid in ("javascript:alert(1)", "https://evil.test/embed/FE21pT6DPwg", "https://youtu.be/notanid"):
            draft["fields"][field["id"]] = invalid
            self.assertEqual(self.action("save", {"revision": 0, "draft": draft})[0], 400)
        draft["fields"][field["id"]] = "https://youtu.be/FE21pT6DPwg"
        with ThreadPoolExecutor(max_workers=2) as workers:
            statuses = list(workers.map(lambda _: self.action("save", {"revision": 0, "draft": draft})[0], range(2)))
        self.assertEqual(sorted(statuses), [200, 409])
        with patch.dict(os.environ, {"VERCEL_ENV": "production"}):
            self.assertEqual(self.action("content")[0], 503)
            self.assertEqual(self.request("GET", "/")[2], BASE)

    def test_reorder_hide_and_stage_headers(self):
        draft = empty()
        draft["order"] = ["services", "hero", "work"]
        draft["hidden"] = ["clients", "whatsapp"]
        logos = [i for i in schema()["items"] if i["group"] == "clients"]
        draft["items"] = {logos[0]["id"]: True}
        draft["itemOrder"] = [item["id"] for item in reversed(logos)]
        doc = render(draft, preview=True)
        self.assertLess(doc.index('id="services"'), doc.index('id="hero"'))
        self.assertNotIn('id="clients"', doc)
        self.assertNotIn("wa.classList", doc)
        self.assertGreater(doc.index("const ids ="), doc.index('id="hero"'))
        with patch.dict(os.environ, {"VERCEL_ENV": "preview"}):
            self.assertEqual(self.request("GET", "/")[1]["X-Robots-Tag"], "noindex, nofollow")


if __name__ == "__main__":
    unittest.main()

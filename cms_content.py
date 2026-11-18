import html
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit


ROOT = Path(__file__).resolve().parent
BASE = (ROOT / "index.html").read_text(encoding="utf-8")
VOID = set("area base br col embed hr img input link meta param source track wbr".split())
SKIP = {"script", "style", "svg", "noscript"}
CLIP_IDS = re.search(r"const ids = \[(.*?)\];", BASE).group(1)
DEFAULT_HERO = {
    "enabled": True, "shuffle": True, "overlay": None,
    "clips": [{"url": "https://youtu.be/" + video_id, "seconds": None, "start": 0}
              for video_id in re.findall(r"'([^']+)'", CLIP_IDS)],
}
SEO = {
    "title": ("title", ""), "description": ("name", "description"),
    "keywords": ("name", "keywords"), "robots": ("name", "robots"),
    "og:title": ("property", "og:title"), "og:description": ("property", "og:description"),
    "og:image": ("property", "og:image"), "canonical": ("rel", "canonical"),
}
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
KEY = re.compile(r"[a-zA-Z0-9_-]{1,70}")
ALLOWED_ROBOTS = {"index, follow", "noindex, follow", "noindex, nofollow"}


class Invalid(ValueError):
    pass


@dataclass
class Node:
    tag: str
    attrs: dict
    start: int
    inner: int
    end: int = 0
    children: list = field(default_factory=list)


@dataclass
class Text:
    start: int
    end: int
    value: str


class SourceMap(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.lines = [0] + [m.end() for m in re.finditer("\n", BASE)]
        self.stack = [Node("", {}, 0, 0, len(BASE))]
        self.root = self.stack[0]
        self.feed(BASE)
        self.close()

    def source_offset(self):
        line, col = self.getpos()
        return self.lines[line - 1] + col

    def handle_starttag(self, tag, attrs):
        start = self.source_offset()
        raw = self.get_starttag_text()
        node = Node(tag, dict(attrs), start, start + len(raw))
        self.stack[-1].children.append(node)
        if tag in VOID or raw.rstrip().endswith("/>"):
            node.end = node.inner
        else:
            self.stack.append(node)

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        off = self.source_offset()
        for pos in range(len(self.stack) - 1, 0, -1):
            if self.stack[pos].tag == tag:
                self.stack[pos].end = BASE.index(">", off) + 1
                for unclosed in self.stack[pos + 1:]:
                    unclosed.end = off
                del self.stack[pos:]
                break

    def append_text(self, raw):
        off = self.source_offset()
        end = off + len(raw)
        children = self.stack[-1].children
        if children and isinstance(children[-1], Text) and children[-1].end == off:
            children[-1].end = end
            children[-1].value += raw
        else:
            children.append(Text(off, end, raw))

    handle_data = append_text

    def handle_entityref(self, name):
        raw = "&" + name
        self.append_text(raw + (";" if BASE.startswith(raw + ";", self.source_offset()) else ""))

    def handle_charref(self, name):
        self.handle_entityref("#" + name)


def classes(node):
    return set(node.attrs.get("class", "").split())


def walk(node):
    yield node
    for child in node.children:
        if isinstance(child, Node):
            yield from walk(child)


def label(node):
    for key in ("title", "alt", "aria-label"):
        if node.attrs.get(key):
            return node.attrs[key]
    for child in node.children:
        if isinstance(child, Text) and child.value.strip():
            return html.unescape(child.value.strip())[:75]
        if isinstance(child, Node) and child.tag in {"summary", "h2", "h3", "h4"}:
            return label(child)
    return next(iter(node.attrs.get("class", "").split()), node.tag).replace("-", " ").capitalize()


@lru_cache(maxsize=1)
def catalog():
    tree = SourceMap().root
    body = next(node for node in walk(tree) if node.tag == "body")
    head = next(node for node in walk(tree) if node.tag == "head")
    groups = {}
    fields = {}
    items = {}
    positions = {}
    group_nodes = {}
    for node in body.children:
        if not isinstance(node, Node):
            continue
        name = node.attrs.get("id") if node.tag == "section" else None
        name = name or {"nav": "navigation", "footer": "footer"}.get(node.tag)
        if "portfolio-more" in classes(node):
            name = "portfolio-link"
        if node.attrs.get("id") == "waWrap":
            name = "whatsapp"
        if not name:
            continue
        group_nodes[name] = node
        groups[name] = {
            "id": name, "label": name.replace("-", " ").capitalize(),
            "movable": node.tag == "section" or name == "portfolio-link",
        }

        def add_field(owner, pos, prop, value, kind, item):
            field_id = f"{name}-{pos}-{prop}"
            fields[field_id] = {
                "id": field_id, "group": name, "item": item, "label": label(owner),
                "kind": kind, "value": "" if value.startswith("data:") else value,
                "embedded": value.startswith("data:"),
            }
            positions[field_id] = (owner, prop)

        def visit(child, path, item=None, container=None):
            if child.tag in SKIP:
                return
            item_classes = {"work-card", "film", "client-tile", "svc", "tag", "clink"}
            if classes(child) & item_classes or child.tag == "li":
                item = f"{name}-{path}"
                items[item] = {"id": item, "group": name, "label": label(child),
                               "parent": parent_items[-1] if parent_items else None, "container": f"{name}-{container}"}
                positions[item] = (child, "item")
                parent_items.append(item)
                added = True
            else:
                added = False
            for attr in ("src", "alt", "href", "title", "aria-label"):
                if attr in child.attrs and child.attrs[attr] is not None:
                    kind = {"href": "link", "src": "video" if child.tag == "iframe" else "image"}.get(attr, "text")
                    add_field(child, path, attr, child.attrs[attr], kind, item)
            for idx, part in enumerate(child.children):
                child_path = f"{path}-{idx}"
                if isinstance(part, Node):
                    visit(part, child_path, item, path)
                elif part.value.strip():
                    add_field(child, child_path, "text", html.unescape(part.value.strip()), "text", item)
                    positions[f"{name}-{child_path}-text"] = (part, "text")
            if added:
                parent_items.pop()

        parent_items = []
        visit(node, "0")

    seo_nodes = {}
    seo_values = {}
    for key, (attr, value) in SEO.items():
        node = next((n for n in walk(head) if n.tag == "title" and attr == "title"
                     or n.attrs.get(attr) == value), None)
        if node:
            seo_nodes[key] = node
            seo_values[key] = (html.unescape("".join(t.value for t in node.children if isinstance(t, Text)))
                               if attr == "title" else node.attrs.get("href" if attr == "rel" else "content", ""))
        else:
            seo_values[key] = ""
    return groups, fields, items, positions, group_nodes, seo_nodes, seo_values


def schema():
    groups, fields, items, _, _, _, seo_values = catalog()
    return {"groups": list(groups.values()), "fields": list(fields.values()),
            "items": list(items.values()), "seo": seo_values, "hero": DEFAULT_HERO,
            "base": "b2027c3610ac66dd241939d261be2e9d194edf19"}


def empty():
    return {"fields": {}, "hidden": [], "order": [], "items": {}, "itemOrder": [],
            "home": [], "pages": [], "seo": {}, "hero": None, "logo": ""}


def require(condition, message):
    if not condition:
        raise Invalid(message)


def string(value, limit=3000):
    require(isinstance(value, str) and len(value) <= limit and "\x00" not in value, "Invalid text field")
    return value


def keys(value, limit):
    require(isinstance(value, list) and len(value) <= limit, "Invalid list")
    require(all(isinstance(k, str) and KEY.fullmatch(k) for k in value), "Invalid ID")
    require(len(set(value)) == len(value), "Duplicate ID")
    return value


def url(value, kind):
    value = string(value, 2048).strip()
    if not value:
        return value
    require(not re.search(r"[\x00-\x20\\\x7f]", value), "URL contains invalid characters")
    if kind == "link" and value.startswith("#") and re.fullmatch(r"#[\w-]+", value):
        return value
    if kind in {"link", "image"} and value.startswith("/") and not value.startswith("//"):
        return value
    if kind == "link" and re.fullmatch(r"(mailto:[^@]+@[^@]+|tel:\+?[0-9-]+)", value):
        return value
    try:
        parsed = urlsplit(value)
        require(parsed.scheme == "https" and parsed.hostname and not parsed.username
                and not parsed.password and parsed.port in (None, 443), "Use a valid HTTPS URL")
    except ValueError as exc:
        raise Invalid("Use a valid HTTPS URL") from exc
    if kind == "video":
        video(value)
    return value


def video(value):
    parsed = urlsplit(value.strip())
    query = parse_qs(parsed.query)
    parts = parsed.path.strip("/").split("/")
    host = parsed.hostname
    if host in {"youtu.be", "www.youtube.com", "youtube.com", "m.youtube.com", "www.youtube-nocookie.com"}:
        clip = query.get("v", [""])[0] if parts[0] == "watch" else parts[-1]
        require(re.fullmatch(r"[A-Za-z0-9_-]{11}", clip), "Invalid YouTube video link")
        return "youtube", clip, ""
    if host in {"vimeo.com", "www.vimeo.com", "player.vimeo.com"}:
        clip, access = (parts[-2], parts[-1]) if len(parts) >= 2 and parts[-2].isdigit() else (parts[-1], "")
        access = query.get("h", [access])[0]
        require(re.fullmatch(r"\d{5,15}", clip) and re.fullmatch(r"[a-zA-Z0-9]{0,32}", access),
                "Invalid Vimeo video link")
        return "vimeo", clip, access
    raise Invalid("Use a YouTube or Vimeo video link")


def embed(value):
    provider, clip, access = video(value)
    if provider == "youtube":
        return f"https://www.youtube.com/embed/{clip}?playsinline=1&rel=0"
    query = urlencode({"dnt": 1, **({"h": access} if access else {})})
    return f"https://player.vimeo.com/video/{clip}?{query}"


def integer(value, lo, hi):
    require(type(value) is int and lo <= value <= hi, "Number outside the allowed range")
    return value


def seo(value):
    require(isinstance(value, dict) and set(value) <= set(SEO), "Invalid SEO fields")
    result = {}
    for key, val in value.items():
        if key in {"canonical", "og:image"}:
            result[key] = url(val, "canonical")
        else:
            result[key] = string(val, 1000)
        if key == "robots":
            require(val in ALLOWED_ROBOTS or not val, "Invalid robots directive")
    return result


def hero(value):
    require(isinstance(value, dict) and set(value) == set(DEFAULT_HERO), "Invalid montage")
    require(type(value["enabled"]) is bool and type(value["shuffle"]) is bool, "Invalid montage settings")
    if value["overlay"] is not None:
        integer(value["overlay"], 0, 100)
    require(isinstance(value["clips"], list) and len(value["clips"]) <= 80, "Too many montage clips")
    for clip in value["clips"]:
        require(isinstance(clip, dict) and set(clip) == {"url", "seconds", "start"}, "Invalid montage clip")
        require(url(clip["url"], "video"), "Montage clip needs a video link")
        if clip["seconds"] is not None:
            integer(clip["seconds"], 2, 120)
        integer(clip["start"], 0, 14400)
    return value


def blocks(value, insertion=False):
    require(isinstance(value, list) and len(value) <= 80, "Too many sections")
    ids = []
    for block in value:
        require(isinstance(block, dict) and set(block) ==
                ({"id", "type", "title", "text", "url", "layout", "target"} if insertion
                 else {"id", "type", "title", "text", "url", "layout"}), "Invalid section")
        ids.append(block["id"])
        require(isinstance(block["type"], str) and block["type"] in {"text", "image", "video", "logo"}, "Invalid section type")
        string(block["title"], 150)
        string(block["text"])
        require(isinstance(block["layout"], str) and block["layout"] in {"landscape", "portrait"}, "Invalid layout")
        if block["type"] != "text":
            url(block["url"], "video" if block["type"] == "video" else "image")
        else:
            require(block["url"] == "", "Text sections cannot contain media URLs")
        if insertion:
            require(isinstance(block["target"], str) and block["target"] in {"", "clients", *gallery_targets()}, "Invalid destination")
            if block["target"]:
                require((block["target"] == "clients" and block["type"] == "logo") or
                        (block["target"] != "clients" and block["type"] == "video"), "Wrong media type for destination")
    keys(ids, 80)
    return value


@lru_cache(maxsize=1)
def gallery_targets():
    _, _, _, _, nodes, _, _ = catalog()
    return {f"gallery-{idx}": node for idx, node in enumerate(
        n for n in walk(nodes["work"]) if "films" in classes(n))}


def galleries():
    _, _, _, _, nodes, _, _ = catalog()
    result = []
    for card in (n for n in walk(nodes["work"]) if "work-card" in classes(n)):
        title = next((label(n) for n in walk(card) if n.tag == "h3"), "Work")
        for gallery_id, target in gallery_targets().items():
            if any(n is target for n in walk(card)):
                portrait = "films-portrait" in classes(target)
                result.append({"id": gallery_id, "label": title + (" · Portrait" if portrait else " · Landscape")})
    return result


def validate(data):
    require(isinstance(data, dict) and set(data) == set(empty()), "Unsupported document format")
    groups, fields, items, _, _, _, _ = catalog()
    require(isinstance(data["fields"], dict) and set(data["fields"]) <= set(fields), "Unknown content field")
    for field_id, value in data["fields"].items():
        kind = fields[field_id]["kind"]
        if kind == "text":
            string(value)
        else:
            require(url(value, kind), "Use Hide or Remove to remove a media item")
    require(set(keys(data["hidden"], 20)) <= set(groups), "Unknown section")
    require(isinstance(data["items"], dict) and set(data["items"]) <= set(items)
            and all(v is True for v in data["items"].values()), "Unknown hidden item")
    require(set(keys(data["itemOrder"], len(items))) <= set(items), "Unknown ordered item")
    blocks(data["home"], insertion=True)
    movable = {key for key, group in groups.items() if group["movable"]}
    require(set(keys(data["order"], 100)) <= movable | {b["id"] for b in data["home"] if not b["target"]},
            "Unknown ordered section")
    require(not ({b["id"] for b in data["home"]} & set(groups)), "Section ID conflicts with an existing section")
    seo(data["seo"])
    if data["hero"] is not None:
        hero(data["hero"])
    url(data["logo"], "image")
    require(isinstance(data["pages"], list) and len(data["pages"]) <= 24, "Too many pages")
    slugs = set()
    for page in data["pages"]:
        require(isinstance(page, dict) and set(page) == {"slug", "title", "menu", "blocks", "seo"}, "Invalid page")
        slug = string(page["slug"], 60)
        require(SLUG.fullmatch(slug) and slug not in slugs, "Page addresses must be unique")
        slugs.add(slug)
        require(string(page["title"], 120).strip() and type(page["menu"]) is bool, "Page needs a title")
        blocks(page["blocks"])
        seo(page["seo"])
    return data


def esc(value):
    return html.escape(str(value), quote=True)


def opening(node, attrs):
    original = BASE[node.start:node.inner]
    changed = dict(attrs)
    for attr, value in list(changed.items()):
        pattern = rf'(\s{re.escape(attr)}\s*=\s*)(?:"[^"]*"|\x27[^\x27]*\x27|[^\s>]+)'
        original, count = re.subn(pattern, lambda hit: hit[1] + f'"{esc(value)}"', original, count=1, flags=re.I)
        if count:
            del changed[attr]
    if changed:
        end = "/>" if original.endswith("/>") else ">"
        original = original[:-len(end)] + "".join(f' {key}="{esc(val)}"' for key, val in changed.items()) + end
    return original


def modify(edits, start=0, end=len(BASE)):
    output = []
    cursor = start
    for left, right, text in sorted(edits, key=lambda edit: edit[:2]):
        if left < start or right > end:
            continue
        require(left >= cursor, "Overlapping content edits")
        output.extend((BASE[cursor:left], text))
        cursor = right
    output.append(BASE[cursor:end])
    return "".join(output)


@dataclass
class Edits:
    changes: list = field(default_factory=list)

    def replace(self, start, end, value):
        self.changes.append((start, end, value))

    def take(self, node, value=None):
        inner = [edit for edit in self.changes if node.start <= edit[0] < node.end and edit[1] <= node.end]
        self.changes = [edit for edit in self.changes if edit not in inner]
        if value is not None:
            self.replace(node.start, node.end, value)
        return modify(inner, node.start, node.end) if value is None else ""


def film(block, preview=False):
    if not block["url"]:
        return ""
    value = esc(embed(block["url"]))
    title = esc(block["title"] or "Portfolio video")
    return (f'<article class="film"{selection(block, preview)}><h3>{title}</h3><div class="film-player">'
            f'<iframe src="{value}" title="{title}" loading="lazy" allow="fullscreen; picture-in-picture; encrypted-media"'
            f' allowfullscreen></iframe></div>'
            f'<a class="film-link" href="{esc(block["url"])}" target="_blank" rel="noopener noreferrer">Watch ↗</a></article>')


def selection(block, preview):
    return f' data-cms-select="{esc(block["id"])}"' if preview else ""


def logo(block, preview=False):
    if not block["url"]:
        return ""
    return (f'<div class="client-tile"{selection(block, preview)}><img src="{esc(block["url"])}" '
            f'alt="{esc(block["title"])}" loading="lazy" decoding="async"></div>')


def section(block, preview=False):
    title = f'<h2 class="big-title">{esc(block["title"])}</h2>' if block["title"] else ""
    description = f'<p class="cms-copy">{esc(block["text"])}</p>' if block["text"] else ""
    media = ""
    if block["type"] == "video":
        css = "films films-portrait" if block["layout"] == "portrait" else "films"
        media = f'<div class="{css}">{film(block, preview)}</div>'
    elif block["type"] == "logo":
        media = logo(block, preview)
    elif block["type"] == "image" and block["url"]:
        media = f'<img loading="lazy" src="{esc(block["url"])}" alt="{esc(block["title"])}">'
    return (f'<section class="cms-section" id="{esc(block["id"])}"{selection(block, preview)}>'
            f'{title}{description}{media}</section>')


def meta_edits(edits, values, preview):
    _, _, _, _, _, nodes, defaults = catalog()
    values = dict(values)
    if preview:
        values["robots"] = "noindex, nofollow"
    for key, value in values.items():
        if value == defaults[key]:
            continue
        node = nodes.get(key)
        attr, name = SEO[key]
        if node is not None:
            if key == "title":
                edits.replace(node.start, node.end, f"<title>{esc(value)}</title>")
            else:
                attr = "href" if attr == "rel" else "content"
                edits.replace(node.start, node.end, opening(node, {attr: value}) if value else "")
        elif value:
            pos = BASE.index("</head>")
            tag = (f'<link rel="canonical" href="{esc(value)}">' if key == "canonical"
                   else f'<meta {attr}="{name}" content="{esc(value)}">')
            edits.replace(pos, pos, tag)


def render(data, path="/", preview=False, analytics=False):
    groups, fields, items, positions, nodes, _, _ = catalog()
    page = next((p for p in data["pages"] if path == "/pages/" + p["slug"]), None)
    if path != "/" and not page:
        return None
    edits = Edits()
    attributes = {}
    for field_id, value in data["fields"].items():
        if value == fields[field_id]["value"]:
            continue
        node, prop = positions[field_id]
        if prop == "text":
            raw = node.value
            prefix = raw[:len(raw) - len(raw.lstrip())]
            suffix = raw[len(raw.rstrip()):]
            edits.replace(node.start, node.end, prefix + esc(value) + suffix)
            continue
        if fields[field_id]["kind"] == "video":
            value = embed(value)
        attributes.setdefault(node.start, (node, {}))[1][prop] = value
        if node.tag == "iframe" and prop == "src":
            link = next((n for n in walk(positions[fields[field_id]["item"]][0])
                         if "film-link" in classes(n)), None)
            if link:
                href = next((fid for fid, (target, attr) in positions.items()
                             if target is link and attr == "href"), None)
                if href not in data["fields"]:
                    attributes.setdefault(link.start, (link, {}))[1]["href"] = data["fields"][field_id]
                text = next((t for t in link.children if isinstance(t, Text) and t.value.strip()), None)
                text_key = next((fid for fid, (target, attr) in positions.items()
                                 if target is text and attr == "text"), None)
                if text and text_key not in data["fields"]:
                    provider = "YouTube" if video(value)[0] == "youtube" else "Vimeo"
                    replacement = re.sub(r"(?<=Watch on )(?:YouTube|Vimeo)", provider, text.value)
                    if replacement != text.value:
                        edits.replace(text.start, text.end, replacement)

    if data["logo"]:
        node = next(n for n in walk(nodes["navigation"]) if "nav-logo" in classes(n))
        edits.changes = [e for e in edits.changes if not node.inner <= e[0] < node.end]
        edits.replace(node.inner, BASE.rfind("</a>", node.inner, node.end),
                      f'<img class="cms-brand" src="{esc(data["logo"])}" alt="Joo Films">')
        footer = next(n for n in walk(nodes["footer"]) if "footer-logo" in classes(n))
        edits.changes = [e for e in edits.changes if not footer.inner <= e[0] < footer.end]
        edits.replace(footer.inner, BASE.rfind("</span>", footer.inner, footer.end),
                      f'<img class="cms-brand" src="{esc(data["logo"])}" alt="Joo Films">')
    if preview:
        for key, node in nodes.items():
            attributes.setdefault(node.start, (node, {}))[1]["data-cms-select"] = key
        for key in items:
            node = positions[key][0]
            attributes.setdefault(node.start, (node, {}))[1]["data-cms-select"] = key
    if page:
        for node in walk(nodes["navigation"]):
            if node.attrs.get("href", "").startswith("#"):
                attributes.setdefault(node.start, (node, {}))[1]["href"] = "/" + node.attrs["href"]
    for node, attrs in attributes.values():
        edits.replace(node.start, node.inner, opening(node, attrs))

    menu = [p for p in data["pages"] if p["menu"]]
    if menu:
        menu_pos = BASE.index("</ul>", nodes["navigation"].start)
        edits.replace(menu_pos, menu_pos, "".join(
            f'<li><a href="/pages/{esc(p["slug"])}">{esc(p["title"])}</a></li>' for p in menu))

    if page:
        meta = {key: "" for key in SEO if key != "robots"}
        meta.update({"title": page["title"], "og:title": page["title"], **page["seo"]})
        script = re.search(r'<script type="application/ld\+json">.*?</script>', BASE, flags=re.S)
        if script:
            edits.replace(script.start(), script.end(), "")
    else:
        meta = data["seo"]
    meta_edits(edits, meta, preview)
    additions = {}
    for block in data["home"]:
        if block["target"]:
            additions.setdefault(block["target"], []).append(block)
    if not page:
        for gallery_id, collection in gallery_targets().items():
            items_in_gallery = {id(node) for node in collection.children if isinstance(node, Node)}
            removed = sum(id(positions[item_id][0]) in items_in_gallery for item_id in data["items"])
            added = sum(bool(entry["url"]) for entry in additions.get(gallery_id, []))
            if not removed and not added:
                continue
            parent = next(n for n in walk(nodes["work"])
                          if n.tag == "details" and any(child is collection for child in n.children))
            summary = next(n for n in parent.children if isinstance(n, Node) and n.tag == "summary")
            count = len(items_in_gallery) - removed + added
            for piece in summary.children:
                if not isinstance(piece, Text):
                    continue
                text_id = next((fid for fid, (node, kind) in positions.items() if node is piece and kind == "text"), None)
                if text_id in data["fields"]:
                    continue
                adjusted = re.sub(r"\b\d+(\s+(?:vertical\s+)?)videos?\b",
                                  lambda hit: f"{count}{hit.group(1)}video{'s' if count != 1 else ''}",
                                  piece.value)
                if adjusted != piece.value:
                    edits.replace(piece.start, piece.end, adjusted)
    children = {}
    for key, item in items.items():
        if page and item["group"] != "navigation":
            continue
        children.setdefault(item["container"], []).append(key)
    rank = {key: idx for idx, key in enumerate(data["itemOrder"])}
    for keys_in_container in sorted(children.values(), key=lambda keys_: positions[keys_[0]][0].start, reverse=True):
        if not any(key in rank or key in data["items"] for key in keys_in_container):
            continue
        original = [positions[key][0] for key in keys_in_container]
        rendered = {key: edits.take(node) for key, node in zip(keys_in_container, original)}
        active = [key for key in sorted(keys_in_container, key=lambda key: rank.get(key, len(rank)))
                  if key not in data["items"]]
        for idx, node in enumerate(original):
            edits.replace(node.start, node.end, rendered[active[idx]] if idx < len(active) else "")
    if page:
        content = f'<main class="cms-page"><h1 class="big-title">{esc(page["title"])}</h1>'
        content += "".join(section(block, preview) for block in page["blocks"]) + "</main>"
        start, end = nodes["hero"].start, nodes["contact"].end
        edits.changes = [change for change in edits.changes if not start <= change[0] < end]
        edits.replace(start, end, content)
    else:
        for target, values in additions.items():
            collection = (next(n for n in walk(nodes["clients"]) if "clients-grid" in classes(n))
                          if target == "clients" else gallery_targets()[target])
            end = BASE.rfind("</div>", collection.inner, collection.end)
            edits.replace(end, end, "".join((logo if target == "clients" else film)(b, preview) for b in values))

        if data["hero"] is not None:
            start = BASE.index("<script>", nodes["hero"].end)
            end = BASE.index("</script>", start) + len("</script>")
            cfg = data["hero"]
            clips = []
            for clip in cfg["clips"]:
                provider, clip_id, access = video(clip["url"])
                clips.append({"provider": provider, "id": clip_id, "access": access,
                              "seconds": clip["seconds"], "start": clip["start"]})
            if cfg["overlay"] is not None:
                edits.replace(BASE.index("</head>"), BASE.index("</head>"),
                              f'<style>.hero-overlay{{background:rgba(0,0,0,{cfg["overlay"] / 100:.2f})}}</style>')
            markup = ""
            if cfg["enabled"] and clips and "hero" not in data["hidden"]:
                script_data = json.dumps({"shuffle": cfg["shuffle"], "clips": clips}, separators=(",", ":"))
                markup = f'<script type="application/json" id="cms-montage">{script_data}</script><script defer src="/cms-hero.js"></script>'
            edits.replace(start, end, markup)

        movable = [key for key, group in groups.items() if group["movable"]]
        custom = {b["id"]: section(b, preview) for b in data["home"] if not b["target"]}
        if data["order"] or custom or set(movable) & set(data["hidden"]):
            hero_node = nodes["hero"]
            script_start = BASE.index("<script>", hero_node.end)
            script_end = BASE.index("</script>", script_start) + len("</script>")
            # The baseline player runs after all sections if the hero is moved.
            existing = next((change for change in edits.changes if change[0] == script_start), None)
            if existing:
                edits.changes.remove(existing)
                script = existing[2]
            else:
                script = BASE[script_start:script_end]
            edits.replace(script_start, script_end, "")
            if "hero" not in data["hidden"]:
                pos = BASE.index("</body>")
                edits.replace(pos, pos, script)
            rendered = {key: edits.take(nodes[key]) for key in movable}
            rendered.update(custom)
            order = list(dict.fromkeys(data["order"] + movable + list(custom)))
            visible = [rendered[key] for key in order if key not in data["hidden"]]
            for idx, key in enumerate(movable):
                node = nodes[key]
                chunk = (visible[idx:idx+1] if idx < len(movable)-1 else visible[idx:])
                edits.replace(node.start, node.end, "\n".join(chunk))
    for key in ("navigation", "footer", "whatsapp"):
        if key in data["hidden"]:
            edits.take(nodes[key], "")
    if "whatsapp" in data["hidden"]:
        marker = BASE.index("<script>", nodes["whatsapp"].end)
        edits.replace(marker, BASE.index("</script>", marker) + 9, "")
    if data["logo"] or page or data["home"] or preview:
        pos = BASE.index("</head>")
        edits.replace(pos, pos, '<link rel="stylesheet" href="/cms-public.css">')
    if preview or analytics:
        pos = BASE.index("</body>")
        edits.replace(pos, pos, f'<script src="/cms-public.js" data-mode="{"preview" if preview else "analytics"}"></script>')
    return modify(edits.changes)

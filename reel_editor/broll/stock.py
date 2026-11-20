"""Free stock B-roll: Pexels + Pixabay, searched IN PARALLEL, merged, vertical/HD preferred, cached locally.

Keys (free): PEXELS_API_KEY (https://www.pexels.com/api/new/) and PIXABAY_API_KEY (https://pixabay.com/api/docs/),
stored in the repo's .env. Either one alone works; both together give more choice.

Licences (check the current terms yourself): both sites allow free commercial use of their videos without
attribution, but not reselling the clips unaltered. Pixabay forbids hotlinking, so every clip and thumbnail is
downloaded once and served from the local cache. A credits file is written next to each finished video.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = "ai-reel-editor/1.0 (+https://github.com/)"
PEXELS_URL = "https://api.pexels.com/videos/search"
PIXABAY_URL = "https://pixabay.com/api/videos/"


def http_get(url, headers=None, timeout=30):
    """GET -> bytes. Tests replace this function to mock the network."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_json(url, headers=None):
    return json.loads(http_get(url, headers).decode("utf-8"))


def _score(c, want_vertical=True):
    """higher = better: vertical first, then a resolution near 1080x1920, then a usable length"""
    w, h = c.get("width") or 0, c.get("height") or 0
    s = 0.0
    if want_vertical and h > w:
        s += 3
    short = min(w, h)
    s += 2 if short >= 1080 else (1 if short >= 720 else 0)
    d = c.get("duration_sec") or 0
    s += 1 if 4 <= d <= 30 else 0
    return s


class Pexels:
    name = "pexels"

    def __init__(self, key):
        self.key = key

    def search(self, query, orientation="portrait", limit=15):
        q = {"query": query, "per_page": str(limit), "size": "medium"}
        if orientation in ("portrait", "landscape"):
            q["orientation"] = orientation
        d = http_json(PEXELS_URL + "?" + urllib.parse.urlencode(q), {"Authorization": self.key})
        out = []
        for v in d.get("videos", []):
            files = [f for f in v.get("video_files", []) if f.get("link") and (f.get("file_type") or "").endswith("mp4")]
            f = pick_file(files)
            if not f:
                continue
            out.append({"id": f"pexels-{v['id']}", "source": "pexels", "title": _pexels_title(v),
                        "duration_sec": v.get("duration"), "width": f.get("width") or v.get("width"),
                        "height": f.get("height") or v.get("height"), "thumbUrl": v.get("image"),
                        "download_url": f["link"], "page_url": v.get("url"),
                        "author": (v.get("user") or {}).get("name")})
        return out


def _pexels_title(v):
    # Pexels has no title field; the page URL slug describes the clip ("/video/woman-counting-money-12345/")
    m = re.search(r"/video/([a-z0-9-]+?)-\d+/?$", v.get("url") or "")
    return m.group(1).replace("-", " ") if m else f"pexels video {v.get('id')}"


def pick_file(files):
    """the rendition closest to 1080p on its short side (never 4K when HD exists: smaller, faster)"""
    def short(f):
        return min(f.get("width") or 0, f.get("height") or 0)
    hd = [f for f in files if 1000 <= short(f) <= 1300]
    if hd:
        return max(hd, key=short)
    lower = [f for f in files if short(f) < 1000]
    if lower:
        return max(lower, key=short)
    return min(files, key=short) if files else None


class Pixabay:
    name = "pixabay"

    def __init__(self, key):
        self.key = key

    def search(self, query, orientation="portrait", limit=15):
        q = {"key": self.key, "q": query[:100], "per_page": str(max(3, min(200, limit))), "safesearch": "true",
             "video_type": "film"}
        d = http_json(PIXABAY_URL + "?" + urllib.parse.urlencode(q))
        out = []
        for h in d.get("hits", []):
            vids = h.get("videos") or {}
            files = [dict(v, link=v.get("url")) for k, v in vids.items() if isinstance(v, dict) and v.get("url")]
            f = pick_file(files)
            if not f:
                continue
            thumb = f.get("thumbnail") or (vids.get("medium") or {}).get("thumbnail")
            if not thumb and h.get("picture_id"):
                thumb = f"https://i.vimeocdn.com/video/{h['picture_id']}_640x360.jpg"
            out.append({"id": f"pixabay-{h['id']}", "source": "pixabay", "title": h.get("tags") or "",
                        "duration_sec": h.get("duration"), "width": f.get("width"), "height": f.get("height"),
                        "thumbUrl": thumb, "download_url": f["link"], "page_url": h.get("pageURL"),
                        "author": h.get("user")})
        return out


class StockProvider:
    """Pexels + Pixabay together (whichever keys are set)."""
    name = "stock"

    def __init__(self, pexels_key=None, pixabay_key=None):
        self.sources = []
        if pexels_key:
            self.sources.append(Pexels(pexels_key))
        if pixabay_key:
            self.sources.append(Pixabay(pixabay_key))

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("PEXELS_API_KEY"), os.environ.get("PIXABAY_API_KEY"))

    def ok(self):
        return bool(self.sources)

    def search(self, query, orientation="vertical", limit=12, log=print):
        """both sources in parallel -> merged, de-duplicated, best first (alternating sources for variety)"""
        orient = {"vertical": "portrait", "horizontal": "landscape"}.get(orientation, "any")

        def one(src):
            try:
                return src.search(query, orient, limit)
            except urllib.error.HTTPError as e:
                log(f"{src.name} search failed: HTTP {e.code}")
            except Exception as e:
                log(f"{src.name} search failed: {str(e)[:100]}")
            return []
        with ThreadPoolExecutor(max(1, len(self.sources))) as ex:
            lists = list(ex.map(one, self.sources))
        for lst in lists:
            lst.sort(key=lambda c: -_score(c, orientation == "vertical"))
        merged, seen = [], set()
        for k in range(max([len(x) for x in lists] or [0])):
            for lst in lists:
                if k < len(lst) and lst[k]["id"] not in seen:
                    seen.add(lst[k]["id"])
                    merged.append(lst[k])
        return merged[:limit]

    def fetch_thumb(self, cand, path):
        if not cand.get("thumbUrl"):
            return None
        if not os.path.exists(path):
            data = http_get(cand["thumbUrl"])
            with open(path, "wb") as fh:
                fh.write(data)
        return path

    def download(self, cand, dest_dir):
        """download once into the local cache (no hotlinking), return the file path"""
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, f"{cand['id']}.mp4")
        if os.path.exists(path) and os.path.getsize(path) > 10000:
            return path
        tmp = path + ".part"
        req = urllib.request.Request(cand["download_url"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as fh:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                fh.write(b)
        os.replace(tmp, path)
        meta = {k: cand.get(k) for k in ("id", "source", "title", "page_url", "author")}
        with open(path + ".json", "w") as fh:
            json.dump(meta, fh)
        return path


def test_keys(log=print):
    """real one-result search on each configured source -> {name: True/False/None (no key)}"""
    res = {}
    for name, cls, key in (("pexels", Pexels, os.environ.get("PEXELS_API_KEY")),
                           ("pixabay", Pixabay, os.environ.get("PIXABAY_API_KEY"))):
        if not key:
            res[name] = None
            continue
        try:
            hits = cls(key).search("ocean waves", "portrait", 3)
            res[name] = len(hits) > 0
            log(f"{name}: OK ({len(hits)} results)" if hits else f"{name}: key works but no results")
        except urllib.error.HTTPError as e:
            res[name] = False
            log(f"{name}: FAILED (HTTP {e.code} - key wrong or not activated yet)")
        except Exception as e:
            res[name] = False
            log(f"{name}: FAILED ({str(e)[:80]})")
    return res

"""B-roll providers. All share one small interface:

  provider.search(query, orientation="vertical"|"any", limit=12, log=print) -> [candidate dict]
      candidate: id, source, title, duration_sec, width, height, thumbUrl, download_url/page_url, author
  provider.fetch_thumb(candidate, path) -> path or None
  provider.download(candidate, dest_dir) -> local file path (cached)

BROLL_SOURCE=stock  -> Pexels + Pixabay in parallel (free API keys)        [default, recommended]
BROLL_SOURCE=envato -> your own logged-in Envato session (optional, paid)
BROLL_SOURCE=none   -> no stock footage; the editor uses stickers, text cards and punch-ins instead
"""
from .. import config as C


def get_provider(name=None, log=print):
    name = (name or C.BROLL_SOURCE or "none").lower()
    if name in ("stock", "pexels", "pixabay", "pexels+pixabay"):
        from .stock import StockProvider
        p = StockProvider.from_env()
        if not p.ok():
            log("B-roll: no PEXELS_API_KEY / PIXABAY_API_KEY in .env -> editing without stock B-roll")
            return None
        return p
    if name == "envato":
        from .envato import EnvatoProvider
        return EnvatoProvider()
    return None

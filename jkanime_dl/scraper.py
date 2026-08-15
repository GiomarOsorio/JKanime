import base64
import json
import random
import re
import time
import urllib.parse

import cloudscraper
import requests
from bs4 import BeautifulSoup

# Transient network failures worth retrying — a Cloudflare/origin connection
# reset under request bursts, not a real error like a 404.
_RETRYABLE_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)

_ROMAN_SEASON = {
    "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6,
    "VII": 7, "VIII": 8, "IX": 9, "X": 10,
}

_SPANISH_ORDINAL_SEASON = {
    "primera": 1, "segunda": 2, "tercera": 3, "cuarta": 4, "quinta": 5,
    "sexta": 6, "septima": 7, "séptima": 7, "octava": 8, "novena": 9,
    "decima": 10, "décima": 10,
}

# Title explicitly says this is the last season, just not which number it is.
_FINAL_SEASON_MARKERS = [r"final season", r"temporada final", r"last season"]

# Sequel implied but no season number (and no "this is the final one" wording either).
_AMBIGUOUS_SEASON_MARKERS = [r"\bkanketsu\b", r"\bzoku\b"]


def _season_match(title: str) -> tuple[str, tuple[int, int]] | tuple[None, None]:
    """Find a season marker in a single title. Returns (label, (start, end))
    of the matched span in `title`, or (None, None) if nothing matched."""
    tl = title.lower()

    for pattern in (
        r"(\d+)(?:st|nd|rd|th)\s+season\b",
        r"\bseason\s+(\d+)\b",
        r"\btemporada\s+(\d+)\b",
        r"\bpart(?:e)?\s+(\d+)\b",
    ):
        m = re.search(pattern, tl)
        if m:
            return f"Temporada {int(m.group(1))}", m.span()

    for word, num in _SPANISH_ORDINAL_SEASON.items():
        m = re.search(rf"\b{word}\s+temporada\b", tl)
        if m:
            return f"Temporada {num}", m.span()

    m = re.search(r"\b([IVX]{2,5})\b", title)
    if m and m.group(1) in _ROMAN_SEASON:
        return f"Temporada {_ROMAN_SEASON[m.group(1)]}", m.span()

    m = re.search(r"\s([2-9])\s*$", title)
    if m:
        return f"Temporada {int(m.group(1))}", m.span()

    for pattern in _FINAL_SEASON_MARKERS:
        m = re.search(pattern, tl)
        if m:
            return "Temporada Final", m.span()

    for pattern in _AMBIGUOUS_SEASON_MARKERS:
        m = re.search(pattern, tl)
        if m:
            return "Temporada X", m.span()

    return None, None


def split_title_and_season(*titles: str) -> tuple[str, str]:
    """Best-effort (base_title, season_label) from anime title text.

    JKAnime gives each season its own page/title (e.g. a title ending in
    "2nd Season"), so the season wording is stripped from whichever title
    matched to get a stable base name that all seasons of the same show can
    share as their parent folder. Falls back to the first non-empty title
    unchanged, paired with "Temporada 1", when no season marker is found —
    this runs unattended, so it must never block on a guess. See
    `determine_season` for the season-label-only rules.
    """
    fallback = ""
    for title in titles:
        if not title:
            continue
        t = title.strip()
        if not fallback:
            fallback = t
        label, span = _season_match(t)
        if label:
            base = t[: span[0]].rstrip(" :-–—.,").strip()
            base = re.sub(r"\s+(?:the|el|la|los|las)$", "", base, flags=re.IGNORECASE).strip()
            base = base.rstrip(" :-–—.,").strip()
            if base:
                return base, label
    return fallback, "Temporada 1"


def determine_season(*titles: str) -> str:
    """Best-effort anime season label from title text.

    Returns "Temporada N" when a season number can be inferred, "Temporada
    Final" when the title explicitly says "Final Season"/"Temporada Final"
    (last one, number just isn't stated), "Temporada X" when a sequel is
    implied some other ambiguous way, or "Temporada 1" as the default when
    nothing suggests otherwise.
    """
    return split_title_and_season(*titles)[1]


class JKAnimeClient:
    BASE_URL = "https://jkanime.net"
    JKPLAYER_URL = "https://jkanime.net/jkplayer/c1"
    SERVERS_PATTERN = r"var\s+servers\s*=\s*(\[.*?\]);"

    # Servers we can extract direct video URLs from, in priority order
    SUPPORTED_SERVERS = ["mp4upload", "streamwish", "filemoon", "voe", "vidhide"]

    def __init__(self):
        self.scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True}
        )
        self.headers = {
            "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
            "Referer": self.BASE_URL,
        }

    def _request(self, method, *args, retries: int = 3, **kwargs):
        """Run a cloudscraper request, retrying transient connection failures
        with backoff (+ jitter so a batch of anime doesn't retry in lockstep).
        """
        delay = 1.0
        for attempt in range(retries):
            try:
                return method(*args, **kwargs)
            except _RETRYABLE_EXCEPTIONS:
                if attempt == retries - 1:
                    raise
                time.sleep(delay + random.uniform(0, 0.5))
                delay *= 2

    def get_anime_details(self, anime_url: str) -> dict:
        """Fetch anime title, metadata and episode list from an anime page URL."""
        if not anime_url.endswith("/"):
            anime_url += "/"

        r = self._request(self.scraper.get, anime_url, headers=self.headers)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        details = soup.find("div", class_="anime__details__content")
        info_div = details.find("div", class_="anime_info")

        h3 = info_div.find("h3")
        title = h3.text.strip()
        alt_title_tag = h3.find_next_sibling("span")
        alt_title = alt_title_tag.get_text(strip=True) if alt_title_tag else ""

        synopsis_tag = info_div.find("p", class_="scroll")
        synopsis = synopsis_tag.get_text(strip=True) if synopsis_tag else ""

        poster = ""
        img_tag = info_div.select_one(".movpic img")
        if img_tag and img_tag.get("src"):
            poster = img_tag["src"]
        else:
            og_image = soup.find("meta", attrs={"property": "og:image"})
            if og_image:
                poster = og_image.get("content", "")

        fields = self._parse_info_list(details)
        status = fields.get("Estado", "")

        last_episode = self._get_episode_count(r, anime_url)

        episodes = [
            {"number": i, "name": f"EP{i:02d}", "url": f"{anime_url}{i}/"}
            for i in range(1, last_episode + 1)
        ]

        folder_title, season = split_title_and_season(title, alt_title)

        metadata = {
            "titulo": title,
            "titulo_alternativo": alt_title,
            "sinopsis": synopsis,
            "imagen": poster,
            "url": anime_url,
            "tipo": fields.get("Tipo", ""),
            "generos": self._split_list(fields.get("Generos", "")),
            "studios": self._split_list(fields.get("Studios", "")),
            "temporada_emision": fields.get("Temporada", ""),
            "idiomas": self._split_list(fields.get("Idiomas", "")),
            "episodios": self._to_int(fields.get("Episodios", "")) or last_episode,
            "duracion": fields.get("Duracion", ""),
            "emitido": fields.get("Emitido", ""),
            "estado": status,
            "calidad": fields.get("Calidad", ""),
            "temporada": season,
        }

        return {
            "title": title,
            "folder_title": folder_title,
            "status": status,
            "episodes": episodes,
            "season": season,
            "metadata": metadata,
        }

    def _get_episode_count(self, response, anime_url: str) -> int:
        """Fetch the true episode count from the AJAX endpoint the site uses to
        lazily load the episode list (the old static "next episode" link the
        count used to be scraped from no longer exists in the page markup, and
        the "Episodios" info field reads 0 for ongoing/en emision anime).
        """
        anime_id_match = re.search(r'data-anime="(\d+)"', response.text)
        if not anime_id_match:
            return 0

        # Laravel reissues XSRF-TOKEN on every response, so the token that
        # matches *this* page is the one this response's own Set-Cookie set —
        # not whatever happens to be last in the whole session's cookie jar,
        # which in a batch run holds one stale/mismatched entry per anime
        # already fetched and gets a 419 (CSRF mismatch) if picked wrong.
        # Session jar is only a fallback for the rare response that sets none.
        xsrf_token = self._pick_cookie(response.cookies, "XSRF-TOKEN") or self._pick_cookie(
            self.scraper.cookies, "XSRF-TOKEN"
        )
        if not xsrf_token:
            return 0

        headers = {
            **self.headers,
            "X-Requested-With": "XMLHttpRequest",
            "X-XSRF-TOKEN": urllib.parse.unquote(xsrf_token),
        }
        r = self._request(
            self.scraper.post, f"{self.BASE_URL}/ajax/episodes/{anime_id_match.group(1)}/1", headers=headers
        )
        r.raise_for_status()
        return r.json().get("total", 0)

    @staticmethod
    def _pick_cookie(jar, name: str) -> str | None:
        """Like jar.get(name), but never raises on duplicate names — picks the
        last matching cookie in the jar instead of erroring."""
        values = [c.value for c in jar if c.name == name]
        return values[-1] if values else None

    @staticmethod
    def _parse_info_list(details_soup) -> dict:
        """Parse the Tipo/Generos/Studios/... info list into a label -> value dict."""
        fields = {}
        card = details_soup.find("div", class_="card-bod")
        if not card or not card.find("ul"):
            return fields
        for li in card.find("ul").find_all("li"):
            span = li.find("span")
            if not span:
                continue
            label = span.get_text(strip=True).rstrip(":").strip()
            span.extract()
            links = li.find_all("a")
            if links:
                value = ", ".join(a.get_text(strip=True) for a in links)
            else:
                value = li.get_text(" ", strip=True)
            fields[label] = value
        return fields

    @staticmethod
    def _split_list(value: str) -> list[str]:
        return [v.strip() for v in value.split(",") if v.strip()]

    @staticmethod
    def _to_int(value: str) -> int | None:
        m = re.search(r"\d+", value)
        return int(m.group()) if m else None

    def _extract_servers(self, html: str) -> list[dict]:
        """Extract the servers array from episode page JavaScript."""
        match = re.search(self.SERVERS_PATTERN, html, re.DOTALL)
        if not match:
            return []
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return []

    def _decode_remote(self, remote: str) -> str:
        """Decode the base64-encoded remote URL."""
        # Add padding if needed
        padding = 4 - len(remote) % 4
        if padding != 4:
            remote += "=" * padding
        return base64.b64decode(remote).decode().strip()

    def _extract_from_embed(self, embed_url: str, server_name: str) -> str | None:
        """Extract the direct video URL from a third-party embed page."""
        try:
            r = self._request(self.scraper.get, embed_url, headers={**self.headers, "Referer": self.BASE_URL}, retries=2)
        except Exception:
            return None

        text = r.text

        # Mp4upload: direct MP4 in player.src or src: "..."
        if server_name == "mp4upload":
            m = re.search(r'src:\s*"(https?://[^"]+\.mp4[^"]*)"', text)
            if m:
                return m.group(1)

        # Streamwish/Filemoon: packed JS containing m3u8 URL
        if server_name in ("streamwish", "filemoon"):
            packed = re.search(
                r"eval\(function\(p,a,c,k,e,d\)\{.*?\.split\('\|'\)\)\)",
                text,
                re.DOTALL,
            )
            if packed:
                unpacked = self._unpack_js(packed.group())
                if unpacked:
                    # Try file:"url" or links object with hls/m3u8 URLs
                    for p in [
                        r'file:"(https?://[^"]+)"',
                        r'"hls\d*":"(https?://[^"]+\.m3u8[^"]*)"',
                        r'"(https?://[^"]+master\.m3u8[^"]*)"',
                        r'"(/stream/[^"]+\.m3u8[^"]*)"',
                    ]:
                        m = re.search(p, unpacked)
                        if m:
                            return m.group(1)

        # Generic: look for common video URL patterns
        for pattern in [
            r'src:\s*"(https?://[^"]+\.mp4[^"]*)"',
            r"src:\s*'(https?://[^']+\.mp4[^']*)'",
            r'file:\s*"(https?://[^"]+)"',
            r"file:\s*'(https?://[^']+)'",
            r'"(https?://[^"]+\.m3u8[^"]*)"',
            r"'(https?://[^']+\.m3u8[^']*)'",
        ]:
            m = re.search(pattern, text)
            if m:
                return m.group(1)

        return None

    @staticmethod
    def _unpack_js(packed: str) -> str | None:
        """Unpack p,a,c,k,e,d packed JavaScript."""
        try:
            # Extract the arguments from eval(function(p,a,c,k,e,d){...}('payload',base,count,'words'.split('|')))
            match = re.search(
                r"}\('(.*)',\s*(\d+),\s*(\d+),\s*'([^']*)'\s*\.split\('\|'\)",
                packed,
                re.DOTALL,
            )
            if not match:
                return None

            payload, base, count, words_str = match.groups()
            base = int(base)
            count = int(count)
            words = words_str.split("|")

            def base_n(num: int, b: int) -> str:
                chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                if num < b:
                    return chars[num]
                return base_n(num // b, b) + chars[num % b]

            lookup = {}
            for i in range(count):
                key = base_n(i, base)
                lookup[key] = words[i] if words[i] else key

            result = re.sub(r'\b(\w+)\b', lambda m: lookup.get(m.group(1), m.group(1)), payload)
            return result
        except Exception:
            return None

    def get_episode_streams(self, episode_url: str) -> list[dict]:
        """Extract stream URLs from an episode page.

        Parses the JS `servers` array, decodes the base64 remote URLs,
        then fetches the third-party embed pages to extract direct video URLs.
        """
        seen = set()
        streams = []

        r = self._request(self.scraper.get, episode_url, headers=self.headers)
        r.raise_for_status()

        servers = self._extract_servers(r.text)
        if not servers:
            return []

        # Sort servers by priority
        def server_priority(s):
            name = s.get("server", "").lower()
            if name in self.SUPPORTED_SERVERS:
                return self.SUPPORTED_SERVERS.index(name)
            return 999

        sorted_servers = sorted(servers, key=server_priority)

        first = True
        for server in sorted_servers:
            remote = server.get("remote", "")
            server_name = server.get("server", "").lower()
            if not remote or server_name not in self.SUPPORTED_SERVERS:
                continue

            embed_url = self._decode_remote(remote)
            if not embed_url:
                continue

            # Small pause between third-party embed hosts — probing all 5
            # back to back is the same bursty pattern that gets anime pages
            # rate-limited, just aimed at different domains.
            if first:
                first = False
            else:
                time.sleep(random.uniform(0.3, 0.8))

            src = self._extract_from_embed(embed_url, server_name)
            if src and src not in seen:
                seen.add(src)
                stream_type = "hls" if ".m3u8" in src else "mp4"
                streams.append({
                    "index": len(streams),
                    "src": src,
                    "type": stream_type,
                    "server": server.get("server", ""),
                    "referer": embed_url,
                })

        return streams

import base64
import json
import re

import cloudscraper
from bs4 import BeautifulSoup


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

    def get_anime_details(self, anime_url: str) -> dict:
        """Fetch anime title and episode list from an anime page URL."""
        if not anime_url.endswith("/"):
            anime_url += "/"

        r = self.scraper.get(anime_url, headers=self.headers)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        details = soup.find("div", class_="anime__details__content")
        title = details.find("div", class_="anime_info").find("h3").text.strip()
        status = details.find("div", class_="card-bod").find("ul").find_all("li")[-2].find("div").text.strip()

        last_ep_tag = details.find("a", id="uep")
        last_episode = int(last_ep_tag.text.strip().split("-")[1].strip().split(" ")[0])

        episodes = [
            {"number": i, "name": f"EP{i:02d}", "url": f"{anime_url}{i}/"}
            for i in range(1, last_episode + 1)
        ]

        return {"title": title, "status": status, "episodes": episodes}

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
            r = self.scraper.get(embed_url, headers={**self.headers, "Referer": self.BASE_URL})
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

        r = self.scraper.get(episode_url, headers=self.headers)
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

        for server in sorted_servers:
            remote = server.get("remote", "")
            server_name = server.get("server", "").lower()
            if not remote or server_name not in self.SUPPORTED_SERVERS:
                continue

            embed_url = self._decode_remote(remote)
            if not embed_url:
                continue

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

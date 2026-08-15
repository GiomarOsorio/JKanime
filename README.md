# jkanime-dl

CLI tool to download anime episodes from [jkanime.net](https://jkanime.net) with a Rich-based TUI showing progress bars, episode tracking, and download summaries.

## Features

- Cloudflare bypass via cloudscraper
- Auto-extracts video streams from multiple servers (Mp4upload, Streamwish, Filemoon, VOE, Vidhide)
- Direct MP4 download with progress bars, HLS/ffmpeg fallback
- Concurrent downloads (configurable)
- Skips already-downloaded episodes automatically
- Batch mode: pass a file with multiple anime URLs
- Episode range selection (`-e 1-12`, `-e 5`, `-e 3-`)
- Writes a `metadata.json` per season (synopsis, genres, studios, air date, status, quality, source URL...)
- Detects season number from the title and organizes output as `Anime/Temporada N/`
- Update mode: rescan a folder for ongoing anime and grab newly aired episodes, no URLs needed

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (package manager)
- [ffmpeg](https://ffmpeg.org/) (for HLS streams)

```bash
# Install ffmpeg (macOS)
brew install ffmpeg

# Install ffmpeg (Debian/Ubuntu)
sudo apt install ffmpeg
```

## Installation

```bash
git clone git@github.com:GiomarOsorio/JKanime.git
cd JKanime
uv sync
```

To get a global `jkanime-dl` command (no `uv run` / no `cd` into the repo needed), install it as a uv tool instead:

```bash
uv tool install .
```

## Usage

### Single anime

```bash
uv run jkanime-dl https://jkanime.net/anime-name/
```

### With options

```bash
# Download episodes 1-12, 2 concurrent, auto-confirm
uv run jkanime-dl https://jkanime.net/anime-name/ -e 1-12 -c 2 -y

# Debug mode (shows streams, errors, tracebacks)
uv run jkanime-dl https://jkanime.net/anime-name/ --debug
```

### Batch mode (file with URLs)

Create a text file with one URL per line:

```
# animes.txt
https://jkanime.net/anime-one/
https://jkanime.net/anime-two/
https://jkanime.net/anime-three/
```

```bash
uv run jkanime-dl animes.txt -y
```

### Update mode (check ongoing anime for new episodes)

Every download writes a `metadata.json` per season with its source URL and status. Run
`jkanime-dl` with no URL (or with a folder path) to rescan for anime still marked "En
emision" and grab whatever's new — nothing to type per-show, so it's cron-friendly:

```bash
# Rescans -o/--output (default ~/Videos)
uv run jkanime-dl -y

# Rescans a specific folder instead
uv run jkanime-dl /path/to/library -y
```

### All options

```
usage: jkanime-dl [-h] [-o OUTPUT] [-c CONCURRENT] [-e EPISODES] [-y] [--debug] [url]

positional arguments:
  url                   JKAnime URL, path to a file with URLs, or a folder to rescan
                         for ongoing anime. Omit to rescan -o/--output the same way.

options:
  -o, --output OUTPUT   Output directory (default: ~/Videos)
  -c, --concurrent N    Concurrent downloads (default: 3)
  -e, --episodes RANGE  Episode range (e.g. 1-12, 5, 3-)
  -y, --yes             Skip confirmation prompt
  --debug               Show debug info
```

## Output structure

```
~/Videos/
  Anime Title/
    Temporada 1/
      metadata.json
      EP01.mp4
      EP02.mp4
      ...
    Temporada 2/
      metadata.json
      EP01.mp4
      ...
```

The season number is inferred from the anime's title (`2nd Season`, `III`, `Temporada 2`,
etc.); when a title implies a sequel with no number ("Final Season") it becomes `Temporada
Final`, and `Temporada X` when it's ambiguous some other way. All seasons of the same show
share one anime folder even though JKAnime gives each its own page/title.

## How it works

1. Fetches the anime page, extracts title, metadata (synopsis, genres, studios...) and season
2. Gets the real episode count from JKAnime's episodes AJAX endpoint (needs an XSRF token from the page's cookies)
3. For each episode, parses the `servers` JS array with base64-encoded stream URLs
4. Fetches third-party embed pages (Mp4upload, Streamwish, etc.) and extracts direct video URLs
5. Downloads via httpx (MP4) or ffmpeg (HLS/m3u8), with proper `Referer` headers
6. Skips episodes that already exist on disk

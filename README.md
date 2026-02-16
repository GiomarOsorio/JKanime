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

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (package manager)
- [ffmpeg](https://ffmpeg.org/) (for HLS streams)

```bash
# Install ffmpeg (macOS)
brew install ffmpeg
```

## Installation

```bash
git clone https://github.com/gio-garbern/JKanime.git
cd JKanime
uv sync
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

### All options

```
usage: jkanime-dl [-h] [-o OUTPUT] [-c CONCURRENT] [-e EPISODES] [-y] [--debug] url

positional arguments:
  url                   JKAnime URL or path to file with URLs (one per line)

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
    EP01.mp4
    EP02.mp4
    ...
```

## How it works

1. Fetches the anime page, extracts title and episode list
2. For each episode, parses the `servers` JS array with base64-encoded stream URLs
3. Fetches third-party embed pages (Mp4upload, Streamwish, etc.) and extracts direct video URLs
4. Downloads via httpx (MP4) or ffmpeg (HLS/m3u8), with proper `Referer` headers
5. Skips episodes that already exist on disk

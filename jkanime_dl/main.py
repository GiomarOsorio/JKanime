import argparse
import asyncio
import json
import logging
import re
import shutil
import sys
import traceback
from pathlib import Path

from rich.prompt import Confirm

from jkanime_dl.downloader import download_episode
from jkanime_dl.scraper import JKAnimeClient
from jkanime_dl.ui import (
    console,
    create_progress,
    show_anime_info,
    show_banner,
    show_episode_result,
    show_episode_table,
    show_summary,
)

logger = logging.getLogger("jkanime-dl")


def parse_episode_range(range_str: str, max_ep: int) -> tuple[int, int]:
    """Parse episode range like '1-12', '5', or '3-'."""
    if "-" in range_str:
        parts = range_str.split("-", 1)
        start = int(parts[0]) if parts[0] else 1
        end = int(parts[1]) if parts[1] else max_ep
    else:
        start = end = int(range_str)
    return max(1, start), min(max_ep, end)


def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def find_ongoing_anime(output_dir: Path) -> list[str]:
    """Scan output_dir for metadata.json files left by previous runs and
    return the anime URLs still marked "En emision" (ongoing), so a bare
    run can recheck those for new episodes without the URLs being passed in
    again. Each season has its own metadata.json (see download_anime), so
    this naturally rechecks per season rather than just per show.
    """
    urls = []
    seen = set()
    for meta_path in sorted(output_dir.rglob("metadata.json")):
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        url = data.get("url", "")
        estado = data.get("estado", "")
        if url and url not in seen and "emision" in estado.lower():
            seen.add(url)
            urls.append(url)
    return urls


async def download_anime(
    client: JKAnimeClient,
    url: str,
    output: str,
    concurrent: int,
    episodes: str | None,
    yes: bool,
    debug: bool,
):
    """Download all (or selected) episodes for a single anime URL."""
    # Fetch anime details
    with console.status("[bold cyan]Fetching anime details..."):
        try:
            details = client.get_anime_details(url)
        except Exception as e:
            console.print(f"[red]Error fetching anime details for {url}:[/red] {e}")
            if debug:
                console.print(f"[dim]{traceback.format_exc()}[/dim]")
            return

    title = details["title"]
    folder_title = details["folder_title"]
    season = details["season"]
    all_episodes = details["episodes"]
    show_anime_info(title, details["status"], len(all_episodes), season)

    # Determine episode range
    if episodes:
        start, end = parse_episode_range(episodes, len(all_episodes))
    else:
        start, end = 1, len(all_episodes)

    selected = [ep for ep in all_episodes if start <= ep["number"] <= end]
    show_episode_table(all_episodes, (start, end))

    # Prepare output directory: <output>/<anime base title>/<Temporada N|X>/
    # folder_title has season wording stripped so every season of the same show
    # (each a separate JKAnime page/title) lands under one shared anime folder.
    anime_dir = Path(output).expanduser() / sanitize_filename(folder_title)
    output_dir = anime_dir / sanitize_filename(season)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Metadata lives per season, since each JKAnime page has its own synopsis,
    # genres and episode count even when seasons share the anime folder above.
    # "descargados" (episode name -> byte size at the moment it finished) is
    # the one field we own rather than the scraper, so carry it over from
    # whatever was already on disk instead of clobbering it with the fresh scrape.
    metadata_path = output_dir / "metadata.json"
    downloaded_sizes: dict[str, int] = {}
    if metadata_path.exists():
        try:
            downloaded_sizes = json.loads(metadata_path.read_text(encoding="utf-8")).get("descargados", {})
        except (json.JSONDecodeError, OSError):
            pass
    metadata = {**details["metadata"], "descargados": downloaded_sizes}
    metadata_lock = asyncio.Lock()

    async def save_metadata():
        async with metadata_lock:
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    await save_metadata()

    # Check which episodes already exist. A file only counts as "already
    # downloaded" if its size on disk still matches what was recorded when it
    # finished — a size mismatch means a previous run was interrupted mid
    # download and left a partial file that looked complete just because it
    # existed. No recorded size at all (episode predates this check, or was
    # never confirmed) is trusted as-is rather than forcing a mass re-download.
    existing = []
    pending = []
    for ep in selected:
        dest = output_dir / f"{ep['name']}.mp4"
        recorded_size = downloaded_sizes.get(ep["name"])
        if dest.exists() and dest.stat().st_size > 0 and (
            recorded_size is None or dest.stat().st_size == recorded_size
        ):
            existing.append(ep)
        else:
            pending.append(ep)

    if existing:
        console.print(f"\n[yellow]Already downloaded ({len(existing)}):[/yellow] {', '.join(ep['name'] for ep in existing)}")

    if not pending:
        console.print("\n[green]All episodes already downloaded![/green]")
        return

    console.print(f"[bold]To download ({len(pending)}):[/bold] {', '.join(ep['name'] for ep in pending)}")
    console.print(f"[dim]Saving to: {output_dir}[/dim]\n")

    # Confirm
    if not yes:
        if not Confirm.ask(f"Download {len(pending)} episodes?", default=True):
            console.print("[yellow]Cancelled.[/yellow]")
            return

    # Download episodes
    semaphore = asyncio.Semaphore(concurrent)
    success_count = 0
    failed_count = 0
    skipped_count = len(existing)

    progress = create_progress()

    async def process_episode(episode: dict):
        nonlocal success_count, failed_count

        ep_name = episode["name"]

        async with semaphore:
            try:
                streams = await asyncio.to_thread(
                    client.get_episode_streams, episode["url"]
                )
            except Exception as e:
                console.print(f"  [red]✗[/red] {ep_name} [red]Stream fetch failed: {e}[/red]")
                if debug:
                    console.print(f"[dim]{traceback.format_exc()}[/dim]")
                failed_count += 1
                return

            if not streams:
                console.print(f"  [red]✗[/red] {ep_name} [red]No streams found[/red]")
                failed_count += 1
                return

            if debug:
                for s in streams:
                    console.print(f"  [dim]{ep_name} stream: {s['server']} ({s['type']}) → {s['src'][:80]}...[/dim]")

            task_id = progress.add_task(ep_name, total=None)

            def progress_cb(event: str, value: int | None):
                if event == "start":
                    progress.update(task_id, total=value)
                elif event == "update":
                    progress.advance(task_id, value)
                elif event == "hls":
                    progress.update(task_id, description=f"{ep_name} [dim](ffmpeg)[/dim]")

            result = await download_episode(streams, output_dir, ep_name, progress_cb, debug=debug)
            progress.remove_task(task_id)

            if result:
                success_count += 1
                downloaded_sizes[ep_name] = Path(result).stat().st_size
                await save_metadata()
                show_episode_result(ep_name, True, result)
            else:
                failed_count += 1
                show_episode_result(ep_name, False)

    with progress:
        tasks = [process_episode(ep) for ep in pending]
        await asyncio.gather(*tasks)

    show_summary(len(selected), success_count, failed_count, skipped_count)


async def run(args: argparse.Namespace):
    debug = args.debug
    if debug:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s")

    show_banner()

    if not shutil.which("ffmpeg"):
        console.print("[red]Error: ffmpeg not found. Required for downloading streams.[/red]")
        console.print("[dim]Install: brew install ffmpeg[/dim]")
        sys.exit(1)

    client = JKAnimeClient()

    async def scan_and_update(scan_dir: Path):
        # Rescan scan_dir for anime still airing and recheck each for new
        # episodes, using the URL saved in its metadata.json. Downloads land
        # back in scan_dir too, so this is a self-contained "update" pass.
        console.print(f"[bold]Scanning {scan_dir} for ongoing anime...[/bold]\n")
        urls = find_ongoing_anime(scan_dir)
        if not urls:
            console.print("[yellow]No ongoing anime found under that folder.[/yellow]")
            return
        console.print(f"[bold]Found {len(urls)} ongoing anime, checking for new episodes:[/bold]\n")
        for i, url in enumerate(urls, 1):
            console.rule(f"[bold cyan]{i}/{len(urls)}[/bold cyan]")
            await download_anime(client, url, str(scan_dir), args.concurrent, args.episodes, args.yes, debug)
            console.print()

    if args.url is None:
        # No URL given: fall back to -o/--output as the folder to rescan.
        await scan_and_update(Path(args.output).expanduser())
        return

    # Determine if input is a directory to rescan, a file of URLs, or a single URL
    input_path = Path(args.url).expanduser()
    if input_path.is_dir():
        await scan_and_update(input_path)
    elif input_path.is_file():
        urls = [line.strip() for line in input_path.read_text().splitlines() if line.strip() and not line.strip().startswith("#")]
        console.print(f"[bold]Loaded {len(urls)} anime URLs from {input_path}[/bold]\n")
        for i, url in enumerate(urls, 1):
            console.rule(f"[bold cyan]{i}/{len(urls)}[/bold cyan]")
            await download_anime(client, url, args.output, args.concurrent, args.episodes, args.yes, debug)
            console.print()
    else:
        await download_anime(client, args.url, args.output, args.concurrent, args.episodes, args.yes, debug)


def main():
    parser = argparse.ArgumentParser(
        prog="jkanime-dl",
        description="Download anime episodes from jkanime.net",
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=None,
        help="JKAnime anime page URL, a path to a file with URLs (one per line), or a "
        "folder to rescan for ongoing anime and check for new episodes. Omit entirely "
        "to rescan -o/--output the same way.",
    )
    parser.add_argument("-o", "--output", default="~/Videos", help="Output directory (default: ~/Videos)")
    parser.add_argument("-c", "--concurrent", type=int, default=3, help="Concurrent downloads (default: 3)")
    parser.add_argument("-e", "--episodes", help="Episode range (e.g. 1-12, 5, 3-)")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--debug", action="store_true", help="Show debug info (streams, errors, tracebacks)")

    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()

import asyncio
import traceback
from pathlib import Path

import httpx
from rich.console import Console

console = Console()


async def download_mp4(url: str, dest: Path, referer: str, progress_callback=None) -> bool:
    """Download an MP4 file with streaming and progress reporting."""
    headers = {"Referer": referer}
    async with httpx.AsyncClient(follow_redirects=True, timeout=30, verify=False, headers=headers) as client:
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                return False

            content_length = response.headers.get("content-length")
            total = int(content_length) if content_length else None
            if progress_callback:
                progress_callback("start", total)

            with open(dest, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)
                    if progress_callback:
                        progress_callback("update", len(chunk))

    return True


async def download_hls(m3u8_url: str, dest: Path, referer: str, progress_callback=None) -> tuple[bool, str]:
    """Download an HLS stream using ffmpeg.

    Total size isn't known upfront (HLS has no content-length), so progress is
    reported as bytes-written deltas parsed from ffmpeg's own `-progress`
    output rather than a percentage — that keeps the UI honest instead of
    showing a fake/stuck number.
    """
    cmd = [
        "ffmpeg", "-y",
        "-headers", f"Referer: {referer}\r\n",
        "-i", m3u8_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-progress", "pipe:1",
        "-nostats",
        str(dest),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    async def read_progress():
        last_size = 0
        async for raw_line in proc.stdout:
            key, _, value = raw_line.decode(errors="replace").strip().partition("=")
            if key != "total_size" or not progress_callback:
                continue
            try:
                size = int(value)
            except ValueError:
                continue
            if size > last_size:
                progress_callback("update", size - last_size)
                last_size = size

    stderr_chunks = []

    async def read_stderr():
        async for line in proc.stderr:
            stderr_chunks.append(line)

    await asyncio.gather(read_progress(), read_stderr())
    returncode = await proc.wait()
    stderr = b"".join(stderr_chunks).decode(errors="replace")
    return returncode == 0, stderr


async def download_episode(
    streams: list[dict],
    dest_dir: Path,
    episode_name: str,
    progress_callback=None,
    debug: bool = False,
) -> str | None:
    """Try downloading an episode: prefer MP4, fallback to HLS.

    Returns the file path on success, None on failure.
    """
    dest = dest_dir / f"{episode_name}.mp4"
    if dest.exists() and dest.stat().st_size > 0:
        return str(dest)

    # Sort: prefer mp4 over hls
    sorted_streams = sorted(streams, key=lambda s: 0 if s["type"] == "mp4" else 1)

    for stream in sorted_streams:
        server = stream.get("server", "unknown")
        referer = stream.get("referer", "")
        if stream["type"] == "mp4":
            try:
                if debug:
                    console.print(f"  [dim]{episode_name}: Trying MP4 from {server}: {stream['src'][:80]}[/dim]")
                ok = await download_mp4(stream["src"], dest, referer, progress_callback)
                if ok and dest.exists() and dest.stat().st_size > 0:
                    return str(dest)
                if debug:
                    console.print(f"  [dim]{episode_name}: MP4 from {server} failed (ok={ok}, exists={dest.exists()}, size={dest.stat().st_size if dest.exists() else 0})[/dim]")
            except Exception as e:
                if debug:
                    console.print(f"  [dim red]{episode_name}: MP4 error from {server}: {e}[/dim red]")
                    console.print(f"  [dim]{traceback.format_exc()}[/dim]")
                dest.unlink(missing_ok=True)
                continue
        elif stream["type"] == "hls":
            try:
                if debug:
                    console.print(f"  [dim]{episode_name}: Trying HLS from {server}: {stream['src'][:80]}[/dim]")
                if progress_callback:
                    progress_callback("hls", 0)
                ok, stderr_out = await download_hls(stream["src"], dest, referer, progress_callback)
                if ok and dest.exists() and dest.stat().st_size > 0:
                    return str(dest)
                if debug:
                    console.print(f"  [dim red]{episode_name}: HLS from {server} failed (returncode ok={ok})[/dim red]")
                    if stderr_out:
                        lines = stderr_out.strip().split("\n")
                        for line in lines[-5:]:
                            console.print(f"  [dim]  ffmpeg: {line}[/dim]")
            except Exception as e:
                if debug:
                    console.print(f"  [dim red]{episode_name}: HLS error from {server}: {e}[/dim red]")
                    console.print(f"  [dim]{traceback.format_exc()}[/dim]")
                dest.unlink(missing_ok=True)
                continue

    return None

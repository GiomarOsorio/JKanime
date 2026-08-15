from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

console = Console()


def show_banner():
    console.print(
        Panel(
            "[bold cyan]JKAnime Downloader[/bold cyan]\n"
            "[dim]Download anime episodes from jkanime.net[/dim]",
            border_style="cyan",
        )
    )


def show_anime_info(title: str, status: str, episode_count: int, season: str | None = None):
    table = Table(show_header=False, border_style="cyan", padding=(0, 2))
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Title", f"[bold white]{title}[/bold white]")
    if season:
        table.add_row("Season", season)
    table.add_row("Status", status)
    table.add_row("Episodes", str(episode_count))
    console.print(Panel(table, title="[bold]Anime Info[/bold]", border_style="cyan"))


def show_episode_table(episodes: list[dict], selected_range: tuple[int, int] | None = None):
    start = selected_range[0] if selected_range else 1
    end = selected_range[1] if selected_range else len(episodes)
    console.print(f"\n[bold]Downloading episodes {start} to {end}[/bold] ({end - start + 1} episodes)\n")


def create_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        "[progress.percentage]{task.percentage:>3.0f}%",
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )


def show_episode_result(episode_name: str, success: bool, path: str | None = None):
    if success:
        console.print(f"  [green]✓[/green] {episode_name} → {path}")
    else:
        console.print(f"  [red]✗[/red] {episode_name} [red]FAILED[/red]")


def show_summary(total: int, success: int, failed: int, skipped: int):
    table = Table(title="Download Summary", border_style="cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Total", str(total))
    table.add_row("Downloaded", f"[green]{success}[/green]")
    table.add_row("Skipped (existing)", f"[yellow]{skipped}[/yellow]")
    table.add_row("Failed", f"[red]{failed}[/red]")
    console.print(f"\n")
    console.print(table)

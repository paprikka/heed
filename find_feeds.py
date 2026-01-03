#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27",
#     "beautifulsoup4>=4.12",
#     "lxml>=5.0",
#     "rich>=13.0",
# ]
# ///
"""
RSS Feed Finder - Discover RSS/Atom feeds for a list of domains.

Usage:
    uv run find_feeds.py

Input: domains.json (array of domain strings)
Output: LIST.md (Markdown table: url | feed url | feed online?)
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TaskProgressColumn, TimeElapsedColumn

# Configuration
TIMEOUT_SECONDS = 10
MAX_CONCURRENT = 50
USER_AGENT = "RSSFeedFinder/1.0"

# Feed URL patterns to try (in order of likelihood)
FEED_PATHS = [
    "/feed",
    "/feed.xml",
    "/feed/",
    "/rss",
    "/rss.xml",
    "/rss/",
    "/atom.xml",
    "/atom",
    "/index.xml",
    "/blog/feed",
    "/blog/feed.xml",
    "/blog/rss",
    "/blog/rss.xml",
    "/blog/atom.xml",
    "/blog/index.xml",
    "/?feed=rss2",
    "/?feed=rss",
    "/?feed=atom",
    "/feeds/posts/default",
    "/posts.atom",
    "/news/feed",
    "/news/rss",
    "/articles.xml",
    "/latest.rss",
]

# Platform-specific feed patterns
PLATFORM_PATTERNS: dict[str, str] = {
    "substack.com": "/feed",
    "medium.com": "/feed",
    "buttondown.com": "/rss",
    "buttondown.email": "/rss",
    "github.io": "/feed.xml",
    "wordpress.com": "/feed/",
    "tumblr.com": "/rss",
    "blogspot.com": "/feeds/posts/default",
    "ghost.io": "/rss/",
}

# Valid content types for feeds
VALID_CONTENT_TYPES = [
    "application/rss+xml",
    "application/atom+xml",
    "application/xml",
    "text/xml",
    "application/rdf+xml",
]

# XML markers to detect feeds by content
XML_FEED_MARKERS = [
    b"<?xml",
    b"<rss",
    b"<feed",
    b"<rdf:RDF",
    b"<!DOCTYPE rss",
]


def load_domains(filepath: str) -> list[str]:
    """Load domains from JSON file."""
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)


def normalize_domain_to_url(domain: str) -> str:
    """Convert domain string to full URL."""
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain
    return f"https://{domain}"


def get_platform_feed_path(domain: str) -> str | None:
    """Get feed path for known platforms."""
    for platform, feed_path in PLATFORM_PATTERNS.items():
        if platform in domain:
            return feed_path
    return None


async def safe_request(
    client: httpx.AsyncClient,
    url: str,
    method: str = "GET",
) -> httpx.Response | None:
    """Make HTTP request with error handling."""
    try:
        if method == "HEAD":
            return await client.head(url, follow_redirects=True)
        return await client.get(url, follow_redirects=True)
    except (
        httpx.TimeoutException,
        httpx.ConnectError,
        httpx.TooManyRedirects,
        httpx.HTTPStatusError,
        httpx.UnsupportedProtocol,
        Exception,
    ):
        return None


async def validate_feed(client: httpx.AsyncClient, url: str) -> bool:
    """Validate that URL returns a valid RSS/Atom feed."""
    response = await safe_request(client, url)
    if not response or response.status_code != 200:
        return False

    content_type = response.headers.get("content-type", "").lower()

    # Check content-type header
    if any(ct in content_type for ct in VALID_CONTENT_TYPES):
        return True

    # Fallback: check content starts with XML/RSS/Atom markers
    content_start = response.content[:500].strip()
    return any(content_start.startswith(marker) for marker in XML_FEED_MARKERS)


async def try_html_discovery(
    client: httpx.AsyncClient,
    base_url: str,
) -> str | None:
    """Parse homepage HTML for RSS/Atom autodiscovery links."""
    response = await safe_request(client, base_url)
    if not response or response.status_code != 200:
        return None

    try:
        soup = BeautifulSoup(response.content, "lxml")

        for link in soup.find_all("link", rel="alternate"):
            link_type = link.get("type", "").lower()
            href = link.get("href")

            if not href:
                continue

            if "rss" in link_type or "atom" in link_type:
                # Resolve relative URLs
                full_url = urljoin(base_url, href)

                if await validate_feed(client, full_url):
                    return full_url

    except Exception:
        pass

    return None


async def try_common_paths(
    client: httpx.AsyncClient,
    base_url: str,
) -> str | None:
    """Try common feed paths until one validates."""
    parsed = urlparse(base_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    for path in FEED_PATHS:
        url = base + path
        if await validate_feed(client, url):
            return url

    return None


async def try_platform_pattern(
    client: httpx.AsyncClient,
    base_url: str,
    domain: str,
) -> str | None:
    """Try platform-specific feed pattern."""
    feed_path = get_platform_feed_path(domain)
    if not feed_path:
        return None

    parsed = urlparse(base_url)
    url = f"{parsed.scheme}://{parsed.netloc}{feed_path}"

    if await validate_feed(client, url):
        return url

    return None


async def _discover_feed_impl(
    client: httpx.AsyncClient,
    domain: str,
) -> tuple[str, str | None, bool]:
    """Inner implementation for feed discovery."""
    base_url = normalize_domain_to_url(domain)

    # Strategy 1: Platform-specific patterns
    feed_url = await try_platform_pattern(client, base_url, domain)
    if feed_url:
        return (domain, feed_url, True)

    # Strategy 2: HTML autodiscovery
    feed_url = await try_html_discovery(client, base_url)
    if feed_url:
        return (domain, feed_url, True)

    # Strategy 3: Common path probing
    feed_url = await try_common_paths(client, base_url)
    if feed_url:
        return (domain, feed_url, True)

    return (domain, None, False)


async def discover_feed_for_domain(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    domain: str,
    timeout: int = 30,
) -> tuple[str, str | None, bool]:
    """
    Discover RSS feed for a single domain with hard timeout.
    Returns: (domain, feed_url or None, feed_online: bool)
    """
    async with semaphore:
        try:
            return await asyncio.wait_for(
                _discover_feed_impl(client, domain),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return (domain, None, False)


def write_markdown_table(
    results: list[tuple[str, str | None, bool]],
    filepath: str,
) -> None:
    """Write results as Markdown table."""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("| url | feed url | feed online? |\n")
        f.write("|-----|----------|-------------|\n")

        for url, feed_url, is_online in results:
            feed_col = feed_url or ""
            online_col = "yes" if is_online else "no"
            f.write(f"| {url} | {feed_col} | {online_col} |\n")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Discover RSS feeds for domains")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of domains to process (default: all)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=MAX_CONCURRENT,
        help=f"Number of concurrent requests (default: {MAX_CONCURRENT})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout per domain in seconds (default: 30)",
    )
    return parser.parse_args()


async def main() -> None:
    """Entry point."""
    args = parse_args()
    console = Console()

    input_file = "domains.json"
    output_file = "LIST.md"

    # Load domains
    if not Path(input_file).exists():
        console.print(f"[red]Error:[/red] {input_file} not found")
        sys.exit(1)

    domains = load_domains(input_file)

    if args.limit:
        domains = domains[:args.limit]

    total = len(domains)
    console.print(f"[bold]Processing {total} domains[/bold] (concurrency: {args.concurrency}, timeout: {args.timeout}s)\n")

    # Process all domains
    semaphore = asyncio.Semaphore(args.concurrency)
    found_count = 0

    results: list[tuple[str, str | None, bool]] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("• [green]{task.fields[found]} feeds found[/green]"),
        console=console,
        refresh_per_second=10,
    ) as progress:
        task = progress.add_task("Scanning domains", total=total, found=0)

        async def process_with_progress(domain: str) -> tuple[str, str | None, bool]:
            nonlocal found_count
            result = await discover_feed_for_domain(client, semaphore, domain, args.timeout)
            if result[1]:
                found_count += 1
            progress.update(task, advance=1, found=found_count)
            return result

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(TIMEOUT_SECONDS),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=50),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            tasks = [process_with_progress(domain) for domain in domains]
            results = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter out exceptions
    valid_results = [r for r in results if isinstance(r, tuple)]

    # Write Markdown table
    write_markdown_table(valid_results, output_file)

    # Summary
    found = sum(1 for _, feed, _ in valid_results if feed)
    console.print(f"\n[bold green]Complete![/bold green] Found {found} feeds out of {total} domains")
    console.print(f"Results saved to [cyan]{output_file}[/cyan]")


if __name__ == "__main__":
    asyncio.run(main())

"""Render HTML ads in Chromium and archive their original assets."""

import argparse
import asyncio
from contextlib import contextmanager
from functools import partial
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import math
import os
from pathlib import Path
import re
from shutil import rmtree
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile


SIZE_PATTERN = re.compile(r"([1-9][0-9]*)x([1-9][0-9]*)")
MAX_CONCURRENT_ADS = 12


class AdSizeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.dimensions: tuple[int, int] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta" or self.dimensions is not None:
            return
        attributes = dict(attrs)
        if (attributes.get("name") or "").strip().lower() != "ad.size":
            return
        values = {}
        for field in (attributes.get("content") or "").split(","):
            key, separator, value = field.partition("=")
            key, value = key.strip().lower(), value.strip()
            if not separator or key in values or not re.fullmatch(r"[1-9][0-9]*", value):
                return
            values[key] = value
        if set(values) == {"width", "height"}:
            self.dimensions = (int(values["width"]), int(values["height"]))


def parse_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("SECONDS must be a non-negative number") from error
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError("SECONDS must be a finite, non-negative number")
    return seconds


def find_ads(root: Path) -> list[tuple[Path, int, int]]:
    ads = []
    for folder in sorted(root.iterdir(), key=lambda entry: entry.name):
        if not folder.is_dir():
            continue
        dimensions = None
        index = folder / "index.html"
        if index.is_file():
            try:
                parser = AdSizeParser()
                parser.feed(index.read_text(encoding="utf-8", errors="replace"))
                dimensions = parser.dimensions
            except OSError as error:
                print(f"Warning: Could not read {index}: {error}", file=sys.stderr)
        match = SIZE_PATTERN.fullmatch(folder.name)
        if dimensions is None and match:
            dimensions = (int(match[1]), int(match[2]))
        if dimensions is not None:
            ads.append((folder, *dimensions))
    return ads


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@contextmanager
def serve_ads(root: Path):
    handler = partial(QuietHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


async def capture_ad(browser, folder: Path, width: int, height: int,
                     base_url: str, seconds: float, destination: Path) -> None:
    if not (folder / "index.html").is_file():
        raise FileNotFoundError(f"{folder.name}/index.html is missing")
    context = await browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=1,
    )
    try:
        page = await context.new_page()
        warnings: set[str] = set()

        def warn(message: str) -> None:
            if message not in warnings:
                warnings.add(message)
                print(f"    [{folder.name}] Warning: {message}", flush=True)

        page.on("pageerror", lambda error: warn(f"JavaScript: {error}"))
        page.on("requestfailed", lambda request: warn(
            f"Asset request failed: {request.url} ({request.failure})"))
        page.on("response", lambda response: warn(
            f"HTTP {response.status}: {response.url}") if response.status >= 400 else None)
        print(f"    [{folder.name}] Loading ad...", flush=True)
        response = await page.goto(
            f"{base_url}/{quote(folder.name)}/index.html",
            wait_until="load", timeout=30_000,
        )
        if response is not None and response.status >= 400:
            raise RuntimeError(f"index.html returned HTTP {response.status}")
        print(f"    [{folder.name}] Waiting {seconds:g} seconds...", flush=True)
        await asyncio.sleep(seconds)
        await page.screenshot(
            path=str(destination), type="jpeg", quality=90,
            full_page=False, animations="allow",
            clip={"x": 0, "y": 0, "width": width, "height": height},
            timeout=30_000,
        )
    finally:
        await context.close()


def archive_ad(folder: Path, destination: Path) -> None:
    if not folder.is_dir():
        raise FileNotFoundError(f"Source folder disappeared: {folder}")
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for asset in sorted(folder.rglob("*")):
            archive.write(asset, asset.relative_to(folder).as_posix())


async def export_ads(browser, ads: list[tuple[Path, int, int]],
                     base_url: str, seconds: float) -> int:
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_ADS)

    async def export_one(position: int, folder: Path, width: int, height: int) -> bool:
        async with semaphore:
            try:
                return await export_ad(browser, folder, width, height, base_url, seconds,
                                       position, len(ads))
            except Exception as error:
                print(f"    [{folder.name}] Error: {error}", file=sys.stderr, flush=True)
                return True

    results = await asyncio.gather(*(
        export_one(position, folder, width, height)
        for position, (folder, width, height) in enumerate(ads, start=1)
    ))
    return sum(results)


async def export_ad(browser, folder: Path, width: int, height: int,
                    base_url: str, seconds: float, position: int, total: int) -> bool:
    print(f"\n[{position}/{total}] {folder.name}", flush=True)
    errors = []
    zip_created = False
    with TemporaryDirectory(prefix=".ad-fallback-", dir=folder.parent) as temporary:
        staging = Path(temporary)
        for suffix, label in (("jpg", "Screenshot"), ("zip", "ZIP")):
            filename = f"{folder.name}.{suffix}"
            output = staging / filename
            try:
                if suffix == "jpg":
                    await capture_ad(browser, folder, width, height, base_url, seconds, output)
                else:
                    archive_ad(folder, output)
                os.replace(output, folder.parent / filename)
                if suffix == "zip":
                    zip_created = True
                print(f"    [{folder.name}] {label}: {filename}", flush=True)
            except Exception as error:
                errors.append(f"{label}: {error}")
                print(f"    [{folder.name}] Error: {label}: {error}", file=sys.stderr, flush=True)
    if zip_created:
        try:
            rmtree(folder)
            print(f"    [{folder.name}] Removed folder: {folder.name}", flush=True)
        except Exception as error:
            errors.append(f"Cleanup: {error}")
            print(f"    [{folder.name}] Error: Cleanup: {error}", file=sys.stderr, flush=True)
    if errors:
        print(f"    [{folder.name}] Failed (continuing)", flush=True)
    else:
        print(f"    [{folder.name}] Done", flush=True)
    return bool(errors)


async def run_exports(root: Path, ads: list[tuple[Path, int, int]], seconds: float) -> int:
    from playwright.async_api import async_playwright

    with serve_ads(root) as base_url:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                return await export_ads(browser, ads, base_url, seconds)
            finally:
                await browser.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ad-fallback",
        description="Export ad folders as JPG and ZIP, preferring index.html ad.size metadata "
                "over WIDTHxHEIGHT folder names.",
    )
    parser.add_argument("seconds", type=parse_seconds, metavar="SECONDS",
                        help="seconds to wait after each ad's load event (zero or greater)")
    args = parser.parse_args(argv)
    try:
        root = Path.cwd()
        ads = find_ads(root)
        print(f"Found {len(ads)} ads.", flush=True)
        if not ads:
            print("No ad folders found: use ad.size metadata in index.html or "
                "WIDTHxHEIGHT folder names in the current directory.", file=sys.stderr)
            return 1
        failed = asyncio.run(run_exports(root, ads, args.seconds))
        print(f"\nFinished: {len(ads) - failed} succeeded, {failed} failed.", flush=True)
        return 1 if failed else 0
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        print("Install dependencies and Chromium as described in the README.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
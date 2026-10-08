from contextlib import redirect_stderr, redirect_stdout
import asyncio
import io
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.request import urlopen
from zipfile import ZipFile

import ad_fallback


class AdFallbackTests(unittest.IsolatedAsyncioTestCase):
    def test_seconds_validation(self):
        self.assertEqual(ad_fallback.parse_seconds("10"), 10)
        self.assertEqual(ad_fallback.parse_seconds("0.5"), 0.5)
        self.assertEqual(ad_fallback.parse_seconds("0"), 0)
        for value in ("abc", "-1", "nan", "inf", "-inf"):
            with self.subTest(value=value), self.assertRaises(Exception):
                ad_fallback.parse_seconds(value)
        for arguments in ([], ["abc"]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                ad_fallback.main(arguments)
            self.assertEqual(caught.exception.code, 2)

    def test_discovery_and_archive(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "300x250"
            (folder / "assets" / "empty").mkdir(parents=True)
            (folder / "index.html").write_text("<html></html>")
            (folder / "assets" / "logo.svg").write_text("<svg/>")
            (folder / ".hidden").write_bytes(b"hidden")
            (root / "not-an-ad").mkdir()
            (root / "0x250").mkdir()
            (root / "728x90").write_text("not a folder")
            self.assertEqual(ad_fallback.find_ads(root), [(folder, 300, 250)])
            archive_path = root / "300x250.zip"
            ad_fallback.archive_ad(folder, archive_path)
            with ZipFile(archive_path) as archive:
                self.assertEqual(archive.read("assets/logo.svg"), b"<svg/>")
                self.assertEqual(archive.read(".hidden"), b"hidden")
                self.assertIn("assets/empty/", archive.namelist())
                self.assertIn("index.html", archive.namelist())
                self.assertNotIn("300x250/index.html", archive.namelist())

    def test_discovery_prefers_metadata_and_accepts_named_folders(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("300x250", "campaign-banner"):
                folder = root / name
                folder.mkdir()
                (folder / "index.html").write_text(
                    '<META content=" height = 600, width = 300 " NAME="ad.size"/>',
                    encoding="utf-8",
                )
            self.assertEqual(ad_fallback.find_ads(root), [
                (root / "300x250", 300, 600),
                (root / "campaign-banner", 300, 600),
            ])

    def test_invalid_metadata_falls_back_to_folder_dimensions(self):
        for content in ("", "width=0,height=600", "width=-1,height=600",
                        "width=abc,height=600", "width=300", "width=300,height=nan",
                        "width=300,width=400,height=600", "width=300.5,height=600"):
            with self.subTest(content=content), TemporaryDirectory() as temporary:
                root = Path(temporary)
                for name in ("300x250", "campaign-banner"):
                    folder = root / name
                    folder.mkdir()
                    (folder / "index.html").write_text(
                        f'<meta name="ad.size" content="{content}">', encoding="utf-8")
                self.assertEqual(ad_fallback.find_ads(root), [(root / "300x250", 300, 250)])

    def test_archive_missing_source_is_an_error(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "300x250.zip"
            with self.assertRaises(FileNotFoundError):
                ad_fallback.archive_ad(root / "300x250", destination)
            self.assertFalse(destination.exists())

    async def test_capture_waits_after_load_and_uses_exact_jpeg_size(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary) / "300x250"
            folder.mkdir()
            (folder / "index.html").touch()
            browser = Mock()
            context = Mock()
            page = Mock()
            browser.new_context = AsyncMock(return_value=context)
            context.new_page = AsyncMock(return_value=page)
            context.close = AsyncMock()
            page.goto = AsyncMock()
            page.screenshot = AsyncMock()
            page.goto.return_value.status = 200
            events = []
            page.goto.side_effect = lambda *args, **kwargs: events.append("loaded")
            page.screenshot.side_effect = lambda **kwargs: events.append("screenshot")
            with patch("ad_fallback.asyncio.sleep", side_effect=lambda seconds: events.append(seconds)):
                await ad_fallback.capture_ad(browser, folder, 300, 250, "http://localhost:1234", 10,
                                             folder.parent / "300x250.jpg")
            self.assertEqual(events, ["loaded", 10, "screenshot"])
            browser.new_context.assert_called_once_with(
                viewport={"width": 300, "height": 250}, device_scale_factor=1)
            self.assertEqual(page.goto.call_args.kwargs["wait_until"], "load")
            self.assertEqual(page.screenshot.call_args.kwargs["type"], "jpeg")
            self.assertEqual(page.screenshot.call_args.kwargs["quality"], 90)
            self.assertEqual(page.screenshot.call_args.kwargs["clip"],
                             {"x": 0, "y": 0, "width": 300, "height": 250})
            context.close.assert_awaited_once()

    async def test_failure_continuation_overwrite_and_cleanup(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            broken = root / "300x250"
            good = root / "728x90"
            broken.mkdir()
            good.mkdir()
            (good / "index.html").write_bytes(b"original")
            (root / "728x90.jpg").write_bytes(b"old")
            (root / "728x90.zip").write_bytes(b"old")

            async def capture(browser, folder, width, height, url, seconds, output):
                if folder == broken:
                    raise RuntimeError("failed render")
                output.write_bytes(b"new jpeg")

            with patch("ad_fallback.capture_ad", side_effect=capture), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                failed = await ad_fallback.export_ads(Mock(), ad_fallback.find_ads(root), "http://localhost", 0)
            self.assertEqual(failed, 1)
            self.assertEqual((root / "728x90.jpg").read_bytes(), b"new jpeg")
            self.assertTrue((root / "300x250.zip").is_file())
            with ZipFile(root / "728x90.zip") as archive:
                self.assertEqual(archive.read("index.html"), b"original")
            self.assertFalse(good.exists())
            self.assertFalse(broken.exists())
            self.assertEqual(list(root.glob(".ad-fallback-*")), [])

    async def test_zip_failure_preserves_source_and_existing_archive(self):
        for stage in ("archive", "replace"):
            with self.subTest(stage=stage), TemporaryDirectory() as temporary:
                root = Path(temporary)
                folder = root / "300x250"
                folder.mkdir()
                (folder / "index.html").write_bytes(b"original")
                (root / "300x250.zip").write_bytes(b"old archive")
                replace = os.replace

                def replace_output(source, destination):
                    if Path(destination).suffix == ".zip":
                        raise OSError("ZIP replacement failed")
                    replace(source, destination)

                with patch("ad_fallback.capture_ad",
                           side_effect=lambda *args: args[-1].write_bytes(b"jpeg")), \
                        patch("ad_fallback.archive_ad", wraps=ad_fallback.archive_ad,
                              side_effect=OSError("ZIP failed") if stage == "archive" else None), \
                        patch("ad_fallback.os.replace",
                              side_effect=replace_output if stage == "replace" else replace), \
                        redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    failed = await ad_fallback.export_ads(
                        Mock(), ad_fallback.find_ads(root), "http://localhost", 0)
                self.assertEqual(failed, 1)
                self.assertEqual((folder / "index.html").read_bytes(), b"original")
                self.assertEqual((root / "300x250.zip").read_bytes(), b"old archive")
                self.assertEqual(list(root.glob(".ad-fallback-*")), [])

    async def test_cleanup_failure_keeps_zip_and_continues(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("300x250", "728x90"):
                (root / name).mkdir()
                (root / name / "index.html").write_bytes(b"original")
            remove = ad_fallback.rmtree

            def remove_folder(folder):
                if folder.name == "300x250":
                    raise OSError("Permission denied")
                remove(folder)

            with patch("ad_fallback.capture_ad",
                       side_effect=lambda *args: args[-1].write_bytes(b"jpeg")), \
                    patch("ad_fallback.rmtree", side_effect=remove_folder), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
                failed = await ad_fallback.export_ads(
                    Mock(), ad_fallback.find_ads(root), "http://localhost", 0)
            self.assertEqual(failed, 1)
            self.assertIn("Error: Cleanup: Permission denied", stderr.getvalue())
            self.assertTrue((root / "300x250").is_dir())
            self.assertFalse((root / "728x90").exists())
            for name in ("300x250", "728x90"):
                with ZipFile(root / f"{name}.zip") as archive:
                    self.assertEqual(archive.read("index.html"), b"original")

    async def test_concurrent_exports_are_limited_to_six(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for width in range(100, 108):
                folder = root / f"{width}x100"
                folder.mkdir()
                (folder / "index.html").write_bytes(b"original")
            active = 0
            peak = 0
            started = []
            first_batch = asyncio.Event()
            release = asyncio.Event()

            async def capture(browser, folder, width, height, url, seconds, output):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                started.append(folder.name)
                if len(started) == 6:
                    first_batch.set()
                try:
                    await release.wait()
                    if folder.name == "100x100":
                        raise RuntimeError("failed render")
                    output.write_bytes(b"jpeg")
                finally:
                    active -= 1

            with patch("ad_fallback.capture_ad", side_effect=capture), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                task = asyncio.create_task(ad_fallback.export_ads(
                    Mock(), ad_fallback.find_ads(root), "http://localhost", 0))
                try:
                    await asyncio.wait_for(first_batch.wait(), timeout=2)
                    self.assertEqual(active, 6)
                    self.assertEqual(len(started), 6)
                finally:
                    release.set()
                    failed = await task
            self.assertEqual(failed, 1)
            self.assertEqual(peak, 6)
            self.assertEqual(len(started), 8)
            self.assertEqual(len(list(root.glob("*.zip"))), 8)
            self.assertEqual(len(list(root.glob("*.jpg"))), 7)
            self.assertFalse(any(path.is_dir() for path in root.iterdir()))

    async def test_cancelling_exports_cleans_staging_and_preserves_sources(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for width in range(100, 107):
                folder = root / f"{width}x100"
                folder.mkdir()
                (folder / "index.html").write_bytes(b"original")
            started = []
            cancelled = []
            first_batch = asyncio.Event()

            async def capture(browser, folder, width, height, url, seconds, output):
                started.append(folder.name)
                if len(started) == 6:
                    first_batch.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.append(folder.name)

            with patch("ad_fallback.capture_ad", side_effect=capture), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                task = asyncio.create_task(ad_fallback.export_ads(
                    Mock(), ad_fallback.find_ads(root), "http://localhost", 10))
                try:
                    await asyncio.wait_for(first_batch.wait(), timeout=2)
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
            self.assertEqual(len(started), 6)
            self.assertCountEqual(cancelled, started)
            self.assertEqual(list(root.glob(".ad-fallback-*")), [])
            self.assertEqual(len(list(root.glob("*/index.html"))), 7)
            self.assertEqual(list(root.glob("*.zip")), [])

    def test_http_serves_relative_assets(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "300x250"
            folder.mkdir()
            (folder / "index.html").write_bytes(b'<script src="asset.js"></script>')
            (folder / "asset.js").write_bytes(b"window.loaded = true;")
            with ad_fallback.serve_ads(root) as base_url:
                with urlopen(f"{base_url}/300x250/index.html") as response:
                    self.assertIn(b"asset.js", response.read())
                with urlopen(f"{base_url}/300x250/asset.js") as response:
                    self.assertEqual(response.read(), b"window.loaded = true;")


@unittest.skipUnless(os.environ.get("AD_FALLBACK_BROWSER_TESTS") == "1",
                     "Set AD_FALLBACK_BROWSER_TESTS=1 after installing Chromium")
class BrowserIntegrationTests(unittest.TestCase):
    def test_real_cli_jpeg_assets_errors_and_source_removal(self):
        from playwright.sync_api import sync_playwright

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "100x100").mkdir()
            originals = {}
            names = ("300x250", "728x90", "160x600", "300x600", "320x50", "970x250", "468x60")
            for name in names:
                folder = root / name
                (folder / "assets").mkdir(parents=True)
                html = (
                    '<body style="margin:0;background:rgb(255,0,0)">'
                    '<script src="assets/animation.js"></script>'
                    '<script src="missing.js"></script>'
                    '<script>throw new Error("test warning")</script></body>'
                ).encode()
                script = (
                    'window.addEventListener("load", () => setTimeout(() => '
                    'document.body.style.background = "rgb(0,200,0)", 100));'
                ).encode()
                for relative, content in (("index.html", html), ("assets/animation.js", script)):
                    path = folder / relative
                    path.write_bytes(content)
                    originals[path] = content
                (root / f"{name}.jpg").write_bytes(b"old")
                (root / f"{name}.zip").write_bytes(b"old")
            result = subprocess.run(
                [sys.executable, "-m", "ad_fallback", "0.25"], cwd=root,
                env={**os.environ, "PYTHONPATH": str(Path(ad_fallback.__file__).resolve().parent)},
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("Finished: 7 succeeded, 1 failed.", result.stdout)
            self.assertIn("JavaScript: test warning", result.stdout)
            self.assertIn("HTTP 404:", result.stdout)
            self.assertIn("index.html is missing", result.stderr)
            with ad_fallback.serve_ads(root) as base_url, sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page()
                    page.goto(base_url)
                    for name in names:
                        self.assertTrue((root / f"{name}.jpg").read_bytes().startswith(b"\xff\xd8"))
                        image = page.evaluate("""async (url) => {
                            const image = await createImageBitmap(await (await fetch(url)).blob());
                            const canvas = document.createElement('canvas');
                            canvas.width = image.width;
                            canvas.height = image.height;
                            const context = canvas.getContext('2d');
                            context.drawImage(image, 0, 0);
                            return {width: image.width, height: image.height,
                                    pixel: Array.from(context.getImageData(10, 10, 1, 1).data)};
                        }""", f"{base_url}/{name}.jpg")
                        width, height = map(int, name.split("x"))
                        self.assertEqual((image["width"], image["height"]), (width, height))
                        self.assertLess(image["pixel"][0], 10)
                        self.assertGreater(image["pixel"][1], 190)
                        with ZipFile(root / f"{name}.zip") as archive:
                            self.assertEqual(archive.read("index.html"), originals[root / name / "index.html"])
                            self.assertIn("assets/animation.js", archive.namelist())
                finally:
                    browser.close()
            for path, content in originals.items():
                self.assertFalse(path.parent.exists())
                folder = path.relative_to(root).parts[0]
                with ZipFile(root / f"{folder}.zip") as archive:
                    self.assertEqual(archive.read(path.relative_to(root / folder).as_posix()), content)
            self.assertFalse((root / "100x100").exists())
            self.assertEqual(list(root.glob(".ad-fallback-*")), [])


if __name__ == "__main__":
    unittest.main()
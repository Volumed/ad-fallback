# ad-fallback

Render HTML ads with Playwright Chromium, save exact-size JPEGs at quality 90,
and archive their original files. Python 3.10+; macOS and Linux.

## Global installation on macOS

Use pipx to install the command in an isolated environment, without changing
Homebrew's managed Python installation:

```sh
brew install python pipx
pipx ensurepath
```

Open a new terminal (zsh picks up the PATH change), then install from this project:

```sh
cd ad-fallback
pipx install .
"$(pipx environment --value PIPX_LOCAL_VENVS)/ad-fallback/bin/python" -m playwright install chromium
```

The browser command uses the tool's own Python environment, ensuring Chromium
matches its installed Playwright version.
Verify global availability with `command -v ad-fallback` and `ad-fallback --help`.
After changing this project's source, reinstall with `pipx reinstall ad-fallback`.
After upgrading Playwright, rerun its browser installation command.

## Linux installation

Install Python 3, Python's venv support, and pipx using your distribution's package
manager (for example, `sudo apt install python3 python3-venv pipx` on Ubuntu).
Then:

```sh
pipx ensurepath
# Open a new terminal, then:
cd /path/to/ad-fallback
pipx install .
"$(pipx environment --value PIPX_LOCAL_VENVS)/ad-fallback/bin/python" -m playwright install --with-deps chromium
```

Linux browser system dependencies may require administrator privileges.
Use a Linux distribution supported by Playwright.

## Usage

```text
ads/
├── 300x250/
│   ├── index.html
│   ├── animation.js
│   └── images/
│       └── logo.png
├── 300x600/
│   └── index.html
└── 728x90/
    └── index.html
```

From any directory containing ad-size folders:

```sh
cd /path/to/ads
ad-fallback 10
```

```text
Found 3 ads.

[1/3] 300x250
    Loading ad...
    Waiting 10 seconds...
    Screenshot: 300x250.jpg
    ZIP: 300x250.zip
    Removed folder: 300x250
    Done
...
Finished: 3 succeeded, 0 failed.
```

Each original folder is removed after its ZIP is successfully saved, leaving
`300x250.jpg` and `300x250.zip` in its parent directory. ZIP entries start with
`index.html` and assets, not an enclosing `300x250/` directory. Nested files,
hidden files, and empty directories are included.

Ad dimensions are preferably read from the folder's `index.html`:

```html
<meta name="ad.size" content="width=300,height=600" />
```

This takes priority over dimensions in the folder name. A folder named
`campaign-banner` with this metadata produces a 300x600 screenshot named
`campaign-banner.jpg` and an archive named `campaign-banner.zip`.

## Behavior and limitations

- Only immediate subdirectories are considered, in name order. The first valid
  `ad.size` meta tag in `index.html` supplies positive integer width and height.
  Attribute order, dimension order, and surrounding whitespace do not matter.
  Missing or invalid metadata falls back to a folder name of exactly
  `WIDTHxHEIGHT` (positive integers, lowercase `x`). Folders with neither are ignored.
- `index.html` must be directly inside each matching folder. Missing HTML is a
  per-ad error for dimension-named folders; their ZIP is still attempted.
- SECONDS is required, finite, and non-negative; decimals and zero are allowed.
  Missing or invalid arguments show usage and exit with status 2.
- Every ad gets an isolated browser context, a viewport equal to its dimensions,
  and device scale factor 1. The screenshot is clipped to that viewport, not the
  full document. Oversized content is clipped; ads must fit their declared size.
- The delay starts after the browser's `load` event, not after network idle.
  Animations continue during the delay. Async requests, dynamically loaded fonts,
  and images may need a longer delay. Screenshot capture itself may take extra
  time; the delay is not an exact animation-frame timestamp.
- A temporary HTTP server listens only on `127.0.0.1`, on an automatically chosen
  port, and serves the current directory. Relative assets work without `file://`
  restrictions. The server closes on completion or interruption.
- External assets require network access. Browser CORS, mixed-content, and other
  security rules still apply. Console JavaScript exceptions, failed requests, and
  HTTP errors are warnings; a screenshot may be incomplete but is still saved.
  Navigation and screenshots each have a 30-second timeout.
- Screenshot and ZIP failures are reported separately, and remaining ads are
  processed. Exit status is 1 for any failed ad, startup failure, or no ads; 0
  means all exports succeeded; interruption returns 130. Warnings alone do not
  mark an ad failed.
- Existing JPG/ZIP files are replaced only after each new file is successfully
  generated. If an export fails, an older output may remain. Temporary staging
  files are cleaned up on normal completion, handled errors, and interruption.
- Source folders are deleted only after their new ZIP is successfully written
  and moved into place, even if the screenshot failed. ZIP creation or replacement
  failures leave the source folder untouched. Deletion failures are reported,
  keep the completed ZIP, and do not stop remaining ads; partial deletion is possible.
- Only render trusted ads: JavaScript executes in a real browser. The localhost
  server exposes the current directory to local processes while running. Avoid
  symlinks in ad folders: Python's server and ZIP writer follow file symlinks;
  symlinked directories are not recursively archived as external trees.
- The tool removes original ad folders after archiving. Do not edit assets while an
  export is running. The bundled sample currently references missing images,
  so expect asset warnings until those original assets are supplied.

## Local development

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m playwright install chromium
.venv/bin/python -m unittest discover -s tests -v
AD_FALLBACK_BROWSER_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

The final command also runs an end-to-end Chromium test that checks decoded JPEG
dimensions, rendered pixels, asset loading, overwrites, and failure continuation.

Run `.venv/bin/ad-fallback 10` from an ads directory, using an absolute path to
the executable when necessary. For a global install without pipx, install into
a dedicated venv and add that venv's `bin` directory to your shell's PATH.

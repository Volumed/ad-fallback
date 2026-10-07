### Goal

I have a folder containing multiple folders named after ad dimensions, for example:

```text
ads/
├── 300x250/
│   └── index.html
├── 300x600/
│   └── index.html
├── 728x90/
│   └── index.html
└── 970x250/
    └── index.html
```

I want to be able to `cd` into this folder and run:

```bash
ad-fallback 10
```

The command should be available globally, regardless of which directory I'm currently in.

The `10` argument is the number of seconds to wait before taking the screenshot.

### What the script should do

For every ad-size folder:

1. Find the `index.html` inside the folder.
2. Determine the ad dimensions from the folder name, e.g.:

   - `300x250` → width `300`, height `250`
   - `300x600` → width `300`, height `600`

3. Open/render the `index.html` in a real browser, preferably using Playwright.
4. Set the browser viewport to exactly the ad dimensions.
5. Load the ad and wait for the specified number of seconds.

   - For example, `ad-fallback 10` means **wait 10 seconds after the ad has loaded before taking the screenshot**.

6. Take a screenshot at exactly the ad dimensions.
7. Save the screenshot next to the original folder as:

   ```text
   300x250.jpg
   ```

8. Zip the entire ad folder into:

   ```text
   300x250.zip
   ```

9. The ZIP should contain the `index.html` and all other files/assets inside the original `300x250` folder.
10. The original ad folder should remain untouched.

### Expected result

After running:

```bash
cd ads
ad-fallback 10
```

I should end up with:

```text
ads/
├── 300x250.jpg
├── 300x250.zip
├── 300x600.jpg
├── 300x600.zip
├── 728x90.jpg
├── 728x90.zip
...
```

### JPG requirements

The screenshot must be saved as a JPG, not PNG.

The output image must be exactly the dimensions of the ad, so a `300x250` ad produces a `300x250` JPG.

Use a sensible JPEG quality, preferably around 90.

### CLI requirements

The command should:

```bash
ad-fallback SECONDS
```

and should show useful progress, for example:

```text
Found 4 ads.

[1/4] 300x250
      Loading ad...
      Waiting 10 seconds...
      Screenshot: 300x250.jpg
      ZIP: 300x250.zip
      Done

[2/4] 300x600
      ...
```

It should also validate the argument and show a useful error if the user runs:

```bash
ad-fallback
```

or:

```bash
ad-fallback abc
```

### Important details

- The tool should work on macOS and Linux.
- It should use Python 3.
- Prefer Playwright for browser rendering because the ads may contain JavaScript, animations, external assets, etc.
- Make sure local `index.html` files can load their relative assets correctly.
- Handle JavaScript errors or missing assets gracefully where possible.
- If one ad fails, report the error and continue processing the remaining ads instead of stopping the entire export.
- If the JPG/ZIP already exists, overwrite it.
- Do not modify the original ad folders.
- Use a temporary working directory if necessary.
- Clean up temporary files after completion.

### Global installation

Please also provide clear instructions for installing the command globally so that I can run:

```bash
ad-fallback 10
```

from any directory.

I use macOS, so ideally this should work cleanly with Homebrew/Python and my normal shell (`zsh`), but keep the implementation portable to Linux as well.

Please provide:

1. The complete Python implementation.
2. A `pyproject.toml` or another clean packaging approach so `ad-fallback` becomes a globally available CLI command.
3. Installation instructions.
4. Playwright browser installation instructions.
5. An example directory structure.
6. An example of running the command.
7. Any important assumptions or limitations.

Keep the implementation simple and production-ready rather than overengineering it.

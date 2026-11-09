# CK Text Artist

Style text with any installed font, fills, outlines and shadows, then export it as PNG, JPG or SVG, or drag it straight into another app.

Part of the **CK Suite**, a set of small, free Windows tools.

![CK Text Artist](docs/screenshot.png)

## Features

- Every installed font, with search and favourites
- Fills and outlines in a colour, gradient or image texture (with texture filters)
- Character / line spacing, squash and stretch, rotation, drop shadow or glow
- Export PNG, JPG or SVG at up to 4x quality, or drag the preview into a browser, Canva or a folder
- Presets with thumbnails, undo / redo


## Download

**[Download the latest version](https://github.com/project-ceekay/ck-text-artist/releases/latest)**:
get the `…-Setup.exe` file under *Assets* and run it.

- Windows 10 or 11 (64-bit). Installs for your user account only, so no administrator rights are needed.
- Windows may show **"Windows protected your PC"** because the installer isn't code-signed.
  Click **More info → Run anyway**.
- Uninstall any time from **Settings → Apps**.

## How to use it

1. Type your text and pick a font on the **Text** tab.
2. Style it on the **Layout**, **Fill**, **Outline** and **Effects** tabs.
3. Click **Export…** (Ctrl+E), or drag the preview out of the window.

## Updates

The program checks GitHub for a new version once a day. When one is available, a link appears at
the bottom of the window: click it to read what's new and update in one click. Your settings are
kept. You can turn the automatic check off under **About**.

Settings are stored in `%APPDATA%\CK Suite\Text Artist`.

## Run or build from source

Requires Python 3.10 or newer.

```
pip install -r requirements.txt
python program.py
```

Releases are built automatically by GitHub Actions when a version tag (`v1.2.3`) is pushed; see
[`.github/workflows/release.yml`](.github/workflows/release.yml) and [`installer/setup.iss`](installer/setup.iss).

## License

[MIT](LICENSE) © CK Software. Bundled third-party components are listed in
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

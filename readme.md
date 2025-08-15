# OnlyFans Downloader

Brutally simple, lighting fast. One script that gets everything you paid for (DRM included).

## Usage

1. Install this package. Use editable mode (`-e`) in case any troubleshooting is required. Python 3.10 or higher recommended.

   ```
   python3.10 -m venv .venv
   source .venv/bin/activate
   pip install -c requirements.txt -e .
   ```

1. Make a copy of `config.json.tpl` and name it `config.json`.

   ```
   cp config.json.tpl config.json
   ```

1. Follow [instructions](https://of-scraper.gitbook.io/of-scraper/getting-started/auth#manual-methods) and borrow your OnlyFans browser session. Fill out `cookies`, `x_bc`, and `user_agent` fields in `config.json`. Avoid enabling 2FA.

   Session expires, so you may need to repeat this step eventually.

1. (Optional) Follow [instructions](https://forum.videohelp.com/threads/408031-Dumping-Your-own-L3-CDM-with-Android-Studio/page26#post2766668) and acquire `client_id.bin` and `private_key.pem`. Fill out `client_id_path` and `private_key_path` in `config.json` with paths to these files.

   DRM protection is opt in by the performer. You don't need this to download unprotected content.

1. Run the script. Your content will be waiting for you in `downloads/`.

   ```
   ofd
   ```

## About

This project is heavily inspired by [OF-Scraper](https://github.com/datawhores/OF-Scraper) and [UltimaScraper](https://github.com/UltimaHoarder/UltimaScraper). It rewrites the core functionality for extremely fast content downloading, while simplifying and compressing everything into a single source file.

Its simplicity makes it easy to understand, troubleshoot, and extend, especially with AI assistance.

Enjoy.

#!/usr/bin/env python3
"""Download parquet files from the ITk pixel module reports page to data/.

Usage:
    python3 download_parquets.py

Reads your existing CERN SSO session from Chrome — make sure you are already
logged into the CERN website in Chrome before running.
"""

import re
import sys
from pathlib import Path

import browser_cookie3
import requests

BASE_URL  = "https://itk-pixel-modules-reports-test.web.cern.ch/parquets/"
INDEX_URL = BASE_URL + "index.html"
DATA_DIR  = Path("data")


def download_parquets():
    DATA_DIR.mkdir(exist_ok=True)

    cookies = browser_cookie3.chrome(domain_name=".cern.ch")
    session = requests.Session()
    session.cookies.update(cookies)

    print("Fetching index ...", flush=True)
    resp = session.get(INDEX_URL)
    if resp.status_code == 401 or "auth.cern.ch" in resp.url:
        print("Not authenticated — make sure you are logged into CERN in Chrome.")
        sys.exit(1)
    resp.raise_for_status()

    links = re.findall(r'href="([^"]+\.parquet)"', resp.text)
    if not links:
        print("No parquet files found on index page.")
        sys.exit(1)

    print(f"Found {len(links)} file(s).\n")
    for link in links:
        filename = Path(link).name
        url = link if link.startswith("http") else BASE_URL + link.lstrip("/")
        dest = DATA_DIR / filename

        print(f"  {filename} ... ", end="", flush=True)
        r = session.get(url, stream=True)
        r.raise_for_status()

        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)

        print(f"{dest.stat().st_size / 1e6:.1f} MB")

    print("\nDone.")


if __name__ == "__main__":
    download_parquets()

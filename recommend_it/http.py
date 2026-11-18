"""Tiny stdlib HTTP helpers (no third-party dependency needed)."""

from __future__ import annotations

import json
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "recommend-it/2.0 (+https://github.com/sushmoydatta/Recommend-It)"


class HTTPError(RuntimeError):
    """Raised when a request fails (network error or non-2xx status)."""


def get_json(url: str, params: dict | None = None, headers: dict | None = None, timeout: float = 15) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # urllib raises a zoo of exception types
        raise HTTPError(f"GET {url.split('?')[0]} failed: {e}") from e


def download(url: str, dest: Path, timeout: float = 60, quiet: bool = False) -> Path:
    """Stream ``url`` to ``dest`` (atomically, via a .part file)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if not quiet and total:
                    print(f"\r  {dest.name}: {done / total:6.1%} of {total / 1e6:,.0f} MB", end="", file=sys.stderr)
        if not quiet:
            print(file=sys.stderr)
        shutil.move(tmp, dest)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise HTTPError(f"Download of {url} failed: {e}") from e
    return dest

import time
import requests
import random
import json
import os
import logging
import sys
from pathlib import Path
from typing import Iterator
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

BASE_URL = "https://ws.audioscrobbler.com/2.0/"
SCOPES = {
    "ID": {"method": "geo.gettoptracks", "country": "Indonesia"},
    "GLOBAL": {"method": "chart.gettoptracks"},
}
MIN_REQUEST_SECONDS = 1.0
TIMEOUT_SECONDS = 30
RETRYABLE_ERRORS = {8, 11, 16, 29}
MAX_ATTEMPTS = 3
BASE_DELAY_SECONDS = 1
MAX_PAGE = 4
ROOT_DIR = Path(__file__).resolve().parents[1]
RAW = ROOT_DIR / "data" / "raw"
LIMIT = 50
wib = timezone(timedelta(hours=7))

logger = logging.getLogger("extract")


class LastFMRetryableError(Exception):
    pass


class LastFMFatalError(Exception):
    pass


def _as_list(x) -> list:
    if x is None:
        return []
    return list(x) if isinstance(x, list) else [x]


_last_request = 0.0


def _throttle() -> None:
    global _last_request
    wait = MIN_REQUEST_SECONDS - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def fetch_page(params: dict) -> dict:

    _throttle()

    try:
        res = requests.get(BASE_URL, params=params, timeout=TIMEOUT_SECONDS)
    except (requests.Timeout, requests.ConnectionError) as e:
        raise LastFMRetryableError(f"Http error : {type(e).__name__}") from e

    if res.status_code >= 500 or res.status_code == 429:
        raise LastFMRetryableError(f"Http status code : {res.status_code}")

    try:
        data = res.json()
    except ValueError as e:
        if res.status_code >= 400:
            raise LastFMFatalError("Response body bukan json") from e
        raise LastFMRetryableError("Response body bukan json") from e

    if "error" in data:
        code = data["error"]
        msg = data.get("message")
        if code in RETRYABLE_ERRORS:
            raise LastFMRetryableError(f"LastFm Error : {code}, message : {msg}")
        raise LastFMFatalError(f"LastFm Error : {code}, message : {msg}")

    if res.status_code >= 400:
        raise LastFMFatalError(f"Http {res.status_code} tanpa key error")

    track_obj = data.get("tracks")
    if not track_obj or "track" not in track_obj or "@attr" not in track_obj:
        raise LastFMFatalError(
            f"Struktur object tidak valid. key yang ada {list(data.keys())}"
        )

    attr = track_obj["@attr"]
    try:
        curr_page = int(attr["page"])
        total_pages = int(attr["totalPages"])
        int(attr["total"])
        int(attr["perPage"])
    except (ValueError, KeyError, TypeError) as e:
        raise LastFMFatalError(f"struktur attr tidak valid") from e

    if curr_page != params["page"]:
        raise LastFMFatalError(
            f"Page tidak sesuai, request {params["page"]} dapetnya {curr_page}"
        )

    if len(_as_list(track_obj["track"])) == 0 and curr_page <= total_pages:
        raise LastFMFatalError(
            f"chart kosong padahal baru page {curr_page} dan {total_pages}"
        )

    return data


def fetch_with_retry(params: dict) -> dict:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return fetch_page(params)
        except LastFMRetryableError as e:
            if attempt == MAX_ATTEMPTS:
                raise LastFMFatalError(
                    f"halaman {params["page"]} setelah {MAX_ATTEMPTS} percobaan : {e}"
                ) from e
            delay = BASE_DELAY_SECONDS * 2 ** (attempt - 1) + random.uniform(1, 0)
            logger.warning(
                "Halaman %s percobaan %d/%d gagal %s. coba lagi dalam %.1f detik",
                params["page"],
                attempt,
                MAX_ATTEMPTS,
                e,
                delay,
            )
            time.sleep(delay)


def iter_page(params: dict) -> Iterator[tuple[int, dict]]:
    for page in range(1, MAX_PAGE + 1):
        data = fetch_with_retry({**params, "page": page})
        tracks = _as_list(data["tracks"]["track"])

        if not tracks:
            return

        for track in tracks:
            for k in ("streamable", "image"):
                track.pop(k, None)

        yield page, data

        if page >= int(data["tracks"]["@attr"]["totalPages"]):
            return


def _write_atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def extract_scope(scope: str, params: dict, run_date: str) -> None:
    output_dir = RAW / "chart" / f"scope={scope}" / f"dt={run_date}"
    success_marker = output_dir / "_SUCCESS"

    success_marker.unlink(missing_ok=True)

    source_total = None
    total_pages = 0
    tracks_total = 0
    per_pages = None

    for page, data in iter_page(params):
        tracks_obj = data["tracks"]
        n = len(_as_list(tracks_obj["track"]))
        if source_total is None:
            source_total = int(tracks_obj["@attr"]["total"])
            per_pages = int(tracks_obj["@attr"]["perPage"])
        _write_atomic_json(output_dir / f"page={page}.json", data)
        total_pages += 1
        tracks_total += n
        logger.info("scope=%s page=%d lagu=%d", scope, page, n)

    if total_pages == 0:
        raise LastFMFatalError("Chart kosong: halaman 1 tidak berisi lagu")

    expected = min(MAX_PAGE * per_pages, source_total)
    if expected != tracks_total:
        raise LastFMFatalError(
            f"Rekonsiliasi gagal: terekstrak {tracks_total} lagu, "
            f"seharusnya {expected} (total di sumber {source_total})"
        )

    manifest = {
        "scope": scope,
        "run_date": run_date,
        "total_pages": total_pages,
        "total_tracks": tracks_total,
        "expected": expected,
        "per_page": per_pages,
        "source_total": source_total,
        "run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    _write_atomic_json(success_marker, manifest)
    logger.info(
        "Scope %s sukses. %d halaman. total track %d. sesuai expected",
        scope,
        total_pages,
        tracks_total,
    )


def main() -> int:

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    run_date = datetime.now(wib).strftime("%Y-%m-%d")
    load_dotenv(ROOT_DIR / ".env")
    API_KEY = os.environ.get("API_KEY")
    if not API_KEY:
        logger.error("API_KEY tidak ditemukan pada file .env")
        return 1

    common = {
        "limit": LIMIT,
        "api_key": API_KEY,
        "format": "json",
    }

    for scope, scope_params in SCOPES.items():
        try:
            extract_scope(scope, {**common, **scope_params}, run_date)
        except LastFMFatalError as e:
            logger.error("Scope %s GAGAL : %s", scope, e)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

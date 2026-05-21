"""Download Kentucky precinct voting PDF results.

The source URL is numbered sequentially:
    https://vrsws.sos.ky.gov/liveresults/PrecinctPdf/{N}

By default, downloads start at N=3 and stop at the first bad link.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_URL = "https://vrsws.sos.ky.gov/liveresults/PrecinctPdf/{number}"
PDF_MAGIC = b"%PDF"
USER_AGENT = "KYvoting-downloader/1.0"


def fetch_pdf(number: int, timeout: float) -> bytes | None:
    """Return PDF bytes for a result number, or None when the link is bad."""
    url = BASE_URL.format(number=number)
    request = Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                print(f"Stopping at {number}: HTTP {response.status}", file=sys.stderr)
                return None

            data = response.read()
    except HTTPError as exc:
        print(f"Stopping at {number}: HTTP {exc.code}", file=sys.stderr)
        return None
    except URLError as exc:
        print(f"Stopping at {number}: request failed ({exc.reason})", file=sys.stderr)
        return None

    if not data.startswith(PDF_MAGIC):
        print(f"Stopping at {number}: response is not a PDF", file=sys.stderr)
        return None

    return data


def download_sequence(start: int, output_dir: Path, timeout: float, delay: float) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    number = start

    while True:
        pdf = fetch_pdf(number, timeout=timeout)
        if pdf is None:
            break

        destination = output_dir / f"precinct_{number}.pdf"
        temp_destination = destination.with_suffix(".pdf.tmp")
        temp_destination.write_bytes(pdf)
        temp_destination.replace(destination)

        count += 1
        print(f"Downloaded {number} -> {destination}")
        number += 1

        if delay > 0:
            time.sleep(delay)

    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download sequential Kentucky precinct voting result PDFs."
    )
    parser.add_argument(
        "--start",
        type=int,
        default=3,
        help="first result number to request; default: 3",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data") / "precinct_pdfs",
        help="directory where PDFs will be saved; default: data/precinct_pdfs",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="request timeout in seconds; default: 30",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="seconds to wait between successful downloads; default: 0.25",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.start < 0:
        print("--start must be zero or greater", file=sys.stderr)
        return 2

    downloaded = download_sequence(
        start=args.start,
        output_dir=args.output_dir,
        timeout=args.timeout,
        delay=args.delay,
    )
    print(f"Finished. Downloaded {downloaded} PDF(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

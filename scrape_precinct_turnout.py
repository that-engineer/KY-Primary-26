"""Scrape Kentucky live-results precinct turnout details to CSV.

The source URL is numbered sequentially:
    https://vrsws.sos.ky.gov/liveresults?id={N}

By default, discovery scans N=1718 through N=4643, refines county boundaries,
and then walks each target county until the county changes.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
import random

import fitz
import requests
from bs4 import BeautifulSoup


BASE_URL = "https://vrsws.sos.ky.gov/liveresults?id={number}"

ENV_PATH = Path(__file__).with_name(".env")


def load_local_env(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError(f"Invalid .env line {line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise RuntimeError(f"Invalid .env line {line_number}: missing key")
        os.environ.setdefault(key, value.strip())


def load_mapping_from_env(name: str) -> dict[str, str]:
    raw_value = os.environ.get(name)
    if not raw_value:
        raise RuntimeError(f"Missing {name}. Add it to {ENV_PATH}.")

    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} must be a JSON object.") from exc

    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in value.items()
    ):
        raise RuntimeError(f"{name} must be a JSON object with string keys and values.")
    return value


load_local_env()
cookies = load_mapping_from_env("KY_VOTING_COOKIES_JSON")
headers = load_mapping_from_env("KY_VOTING_HEADERS_JSON")
try:
    USER_AGENT = headers["user-agent"]
except KeyError:
    raise RuntimeError("KY_VOTING_HEADERS_JSON must include a user-agent header.") from None

FIELDNAMES = (
    "county",
    "precinct",
    "registered_voters",
    "ballots_cast",
    "voter_turnout_percent",
)


@dataclass(frozen=True)
class PrecinctTurnout:
    county: str
    precinct: str
    registered_voters: str
    ballots_cast: str
    voter_turnout_percent: str


@dataclass(frozen=True)
class PrecinctUrl:
    county: str
    precinct: str
    number: int


@dataclass(frozen=True)
class DiscoveredPrecinct:
    number: int
    turnout: PrecinctTurnout


class BlockedPageError(RuntimeError):
    """Raised when the site returns its acceptable-use/firewall page."""


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize(text: str) -> str:
    return clean_text(text).upper()


def natural_sort_key(path: Path) -> list[int | str]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", str(path))]


def county_sort_key(county: str) -> str:
    return normalize(re.sub(r"\s+County$", "", county, flags=re.I))


def precinct_matches(expected: str, actual: str) -> bool:
    normalized_expected = normalize(expected)
    normalized_actual = normalize(actual)
    return normalized_expected == normalized_actual or normalized_expected in normalized_actual


def parse_title(title: str) -> tuple[str, str]:
    match = re.fullmatch(r"(.+?)\s+County\s+Precinct\s+(.+)", title, flags=re.I)
    if not match:
        return title, ""

    county = f"{match.group(1).strip()} County"
    precinct = match.group(2).strip()
    return county, precinct


def extract_metric_value(text: str, label_patterns: tuple[str, ...]) -> str:
    for pattern in label_patterns:
        text = re.sub(pattern, "", text, flags=re.I)

    percent_match = re.search(r"\d+(?:\.\d+)?\s*%", text)
    if percent_match:
        return percent_match.group(0).replace(" ", "")

    number_match = re.search(r"\d[\d,]*", text)
    if number_match:
        return number_match.group(0)

    return clean_text(text)


def extract_pdf_county(lines: list[str]) -> str:
    for line in lines:
        match = re.fullmatch(r"OFFICIAL BALLOT FOR (.+ COUNTY)", line, flags=re.I)
        if match:
            return match.group(1).title()

    for line in lines:
        match = re.fullmatch(r"(.+ County), KY", line, flags=re.I)
        if match:
            return match.group(1).title()

    return ""


def extract_pdf_precinct(lines: list[str]) -> str:
    if not lines:
        return ""

    if normalize(lines[0]) == "PRECINCT SUMMARY RESULTS REPORT":
        for index, line in enumerate(lines):
            if re.fullmatch(r".+ County, KY", line, flags=re.I):
                return lines[index + 1] if index + 1 < len(lines) else ""
        return ""

    return lines[0]


def load_target_precincts(results_csv: Path) -> set[tuple[str, str]]:
    with results_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return {
            (clean_text(row["county"]), clean_text(row["precinct"]))
            for row in reader
            if clean_text(row.get("county", "")) and clean_text(row.get("precinct", ""))
        }


def build_precinct_url_map(pdf_dir: Path, start: int) -> dict[tuple[str, str], PrecinctUrl]:
    precincts: list[tuple[str, str]] = []

    for pdf_path in sorted(pdf_dir.glob("*.pdf"), key=natural_sort_key):
        last_precinct: tuple[str, str] | None = None
        with fitz.open(pdf_path) as document:
            for page in document:
                lines = [line.strip() for line in page.get_text("text").splitlines() if line.strip()]
                precinct = (extract_pdf_county(lines), extract_pdf_precinct(lines))

                if not precinct[0] or not precinct[1] or precinct == last_precinct:
                    continue

                precincts.append(precinct)
                last_precinct = precinct

    return {
        (county, precinct): PrecinctUrl(county=county, precinct=precinct, number=start + index)
        for index, (county, precinct) in enumerate(precincts)
    }


def print_precinct_url_dictionary(precinct_urls: list[PrecinctUrl]) -> None:
    print("Massie/Gallrein precinct URL dictionary:", file=sys.stderr)
    for precinct_url in precinct_urls:
        key = f"{precinct_url.county} Precinct {precinct_url.precinct}"
        print(f"  {key}: {precinct_url.number}", file=sys.stderr)


def print_discovered_precinct_dictionary(precincts: list[DiscoveredPrecinct]) -> None:
    print("Massie/Gallrein precinct URL dictionary:", file=sys.stderr)
    for precinct in precincts:
        turnout = precinct.turnout
        key = f"{turnout.county} Precinct {turnout.precinct}"
        print(f"  {key}: {precinct.number}", file=sys.stderr)


def parse_precinct_turnout(html: str) -> PrecinctTurnout | None:
    if "Acceptable Use Policy" in html and "website scraping" in html:
        raise BlockedPageError("Kentucky SBE acceptable-use/firewall page returned")

    soup = BeautifulSoup(html, "html.parser")
    title_node = soup.select_one("#mapDetailsTitle")
    detail_nodes = soup.select("div.details-item.details-item2")

    if title_node is None or len(detail_nodes) < 3:
        return None

    county, precinct = parse_title(clean_text(title_node.get_text(" ", strip=True)))
    values = [clean_text(node.get_text(" ", strip=True)) for node in detail_nodes[:3]]

    return PrecinctTurnout(
        county=county,
        precinct=precinct,
        registered_voters=extract_metric_value(values[0], ("registered voters",)),
        ballots_cast=extract_metric_value(values[1], ("ballots cast",)),
        voter_turnout_percent=extract_metric_value(values[2], ("voter turnout", "turnout")),
    )


def fetch_html(session: requests.Session, number: int, timeout: float) -> str | None:
    url = BASE_URL.format(number=number)
    response = session.get(url, timeout=timeout, headers=headers, cookies=cookies)
    if response.status_code == 404:
        return None
    if response.status_code == 403:
        return response.text

    response.raise_for_status()
    return response.text


def fetch_precinct_turnout(
    session: requests.Session,
    number: int,
    timeout: float,
    cache: dict[int, PrecinctTurnout | None],
) -> PrecinctTurnout | None:
    if number not in cache:
        html = fetch_html(session, number=number, timeout=timeout)
        cache[number] = parse_precinct_turnout(html) if html is not None else None

    return cache[number]


def sleep_between_requests(delay: float) -> None:
    if delay > 0:
        time.sleep(random.random() * 2 + 1)


def discover_target_precincts(
    start: int,
    end: int,
    scan_step: int,
    target_precincts: set[tuple[str, str]],
    filter_mode: str,
    timeout: float,
    delay: float,
) -> list[DiscoveredPrecinct]:
    target_counties = sorted({county for county, _ in target_precincts}, key=county_sort_key)
    target_county_keys = {county_sort_key(county) for county in target_counties}
    normalized_target_precincts = {
        (normalize(county), normalize(precinct)) for county, precinct in target_precincts
    }
    cache: dict[int, PrecinctTurnout | None] = {}
    discovered: dict[int, PrecinctTurnout] = {}

    with requests.Session() as session:

        def get(number: int) -> PrecinctTurnout | None:
            if number < start or number > end:
                return None
            row = fetch_precinct_turnout(session, number=number, timeout=timeout, cache=cache)
            sleep_between_requests(delay)
            return row

        observations: list[tuple[int, PrecinctTurnout]] = []
        sample_numbers = list(range(start, end + 1, scan_step))
        if sample_numbers[-1] != end:
            sample_numbers.append(end)

        for number in sample_numbers:
            print(f"Scanning {number}", file=sys.stderr)
            row = get(number)
            if row is not None:
                observations.append((number, row))

        if not observations:
            return []

        observations.sort(key=lambda item: item[0])

        def find_candidate(target_county: str) -> int | None:
            target_key = county_sort_key(target_county)
            matching = [number for number, row in observations if county_sort_key(row.county) == target_key]
            if matching:
                return matching[0]

            lower = start
            upper = end
            for (left_number, left_row), (right_number, right_row) in zip(observations, observations[1:]):
                left_key = county_sort_key(left_row.county)
                right_key = county_sort_key(right_row.county)
                if left_key <= target_key <= right_key:
                    lower = left_number
                    upper = right_number
                    break
            else:
                return None

            while lower < upper:
                midpoint = (lower + upper) // 2
                print(f"Refining {target_county} at {midpoint}", file=sys.stderr)
                row = get(midpoint)
                if row is None:
                    lower = midpoint + 1
                    continue

                if county_sort_key(row.county) < target_key:
                    lower = midpoint + 1
                else:
                    upper = midpoint

            for number in range(lower, min(end, lower + scan_step) + 1):
                row = get(number)
                if row is None:
                    continue

                row_key = county_sort_key(row.county)
                if row_key == target_key:
                    return number
                if row_key > target_key:
                    break

            return None

        def should_keep(row: PrecinctTurnout) -> bool:
            if county_sort_key(row.county) not in target_county_keys:
                return False
            if filter_mode == "county":
                return True

            return any(
                normalize(row.county) == target_county and precinct_matches(target_precinct, row.precinct)
                for target_county, target_precinct in normalized_target_precincts
            )

        def walk_county(seed_number: int, target_county: str) -> None:
            target_key = county_sort_key(target_county)

            number = seed_number
            while number >= start:
                print(f"Walking down {number}", file=sys.stderr)
                row = get(number)
                if row is None or county_sort_key(row.county) != target_key:
                    break
                if should_keep(row):
                    discovered[number] = row
                number -= 1

            number = seed_number + 1
            while number <= end:
                print(f"Walking up {number}", file=sys.stderr)
                row = get(number)
                if row is None or county_sort_key(row.county) != target_key:
                    break
                if should_keep(row):
                    discovered[number] = row
                number += 1

        for target_county in target_counties:
            print(f"Finding {target_county}", file=sys.stderr)
            candidate = find_candidate(target_county)
            if candidate is None:
                print(f"Warning: did not locate {target_county}", file=sys.stderr)
                continue
            walk_county(candidate, target_county)

    return [
        DiscoveredPrecinct(number=number, turnout=discovered[number])
        for number in sorted(discovered)
    ]


def scrape_precinct_urls(
    precinct_urls: list[PrecinctUrl],
    timeout: float,
    delay: float,
    max_consecutive_misses: int,
) -> list[PrecinctTurnout]:
    rows: list[PrecinctTurnout] = []
    misses = 0

    with requests.Session() as session:
        #session.headers.update({"User-Agent": USER_AGENT})

        for precinct_url in precinct_urls:
            print(
                f"Fetching {precinct_url.number}: "
                f"{precinct_url.county} Precinct {precinct_url.precinct}",
                file=sys.stderr,
            )
            try:
                html = fetch_html(session, number=precinct_url.number, timeout=timeout)
                row = parse_precinct_turnout(html) if html is not None else None
            except BlockedPageError as exc:
                print(f"Stopping at {precinct_url.number}: {exc}", file=sys.stderr)
                break
            except requests.RequestException as exc:
                print(f"Stopping at {precinct_url.number}: request failed ({exc})", file=sys.stderr)
                break

            if row is None:
                misses += 1
                print(
                    f"No expected turnout details at {precinct_url.number} "
                    f"({misses}/{max_consecutive_misses} consecutive misses)",
                    file=sys.stderr,
                )
                if misses >= max_consecutive_misses:
                    break
            else:
                expected = (normalize(precinct_url.county), normalize(precinct_url.precinct))
                actual = (normalize(row.county), normalize(row.precinct))
                if actual[0] != expected[0] or not precinct_matches(precinct_url.precinct, row.precinct):
                    print(
                        "Warning: expected "
                        f"{precinct_url.county} Precinct {precinct_url.precinct}, "
                        f"got {row.county} Precinct {row.precinct}",
                        file=sys.stderr,
                    )
                rows.append(row)
                misses = 0

            if delay > 0:
                #time.sleep(delay)
                time.sleep(random.random() * 2 + 1)

    return rows


def write_csv(rows: list[PrecinctTurnout], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "county": row.county,
                    "precinct": row.precinct,
                    "registered_voters": row.registered_voters,
                    "ballots_cast": row.ballots_cast,
                    "voter_turnout_percent": row.voter_turnout_percent,
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape Kentucky live-results precinct turnout details."
    )
    parser.add_argument(
        "--start",
        type=int,
        default=1718,
        help="first live-results id to scan; default: 1718",
    )
    parser.add_argument(
        "--end",
        type=int,
        default=4643,
        help="last live-results id to scan; default: 4643",
    )
    parser.add_argument(
        "--scan-step",
        type=int,
        default=100,
        help="coarse scan step size before refining county boundaries; default: 100",
    )
    parser.add_argument(
        "--target-results",
        type=Path,
        default=Path("data") / "massie_gallrein_precinct_results.csv",
        help=(
            "CSV whose counties define which live-results ids to scrape; "
            "default: data/massie_gallrein_precinct_results.csv"
        ),
    )
    parser.add_argument(
        "--filter-mode",
        choices=("county", "precinct"),
        default="county",
        help=(
            "county scrapes every precinct in counties present in --target-results; "
            "precinct scrapes only exact county/precinct pairs from --target-results; default: county"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data") / "precinct_turnout.csv",
        help="CSV output path; default: data/precinct_turnout.csv",
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
        default=1.75,
        help="seconds to wait between requests; default: 1.75",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.start < 0:
        print("--start must be zero or greater", file=sys.stderr)
        return 2
    if args.end < args.start:
        print("--end must be greater than or equal to --start", file=sys.stderr)
        return 2
    if args.scan_step < 1:
        print("--scan-step must be at least 1", file=sys.stderr)
        return 2
    if not args.target_results.is_file():
        print(f"Target results CSV not found: {args.target_results}", file=sys.stderr)
        return 2

    target_precincts = load_target_precincts(args.target_results)
    discovered_precincts = discover_target_precincts(
        start=args.start,
        end=args.end,
        scan_step=args.scan_step,
        target_precincts=target_precincts,
        filter_mode=args.filter_mode,
        timeout=args.timeout,
        delay=args.delay,
    )
    print(f"Discovered precinct URLs: {len(discovered_precincts)}", file=sys.stderr)
    print_discovered_precinct_dictionary(discovered_precincts)

    rows = [precinct.turnout for precinct in discovered_precincts]
    write_csv(rows, args.output)

    print(f"Rows written: {len(rows)}")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Parse Massie/Gallrein precinct results from Kentucky result PDFs.

The parser scans PDFs below ``data/`` and extracts the Republican
United States Representative in Congress, 4th Congressional District race
between Thomas Massie and Ed Gallrein.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import fitz


RACE_PHRASE = "UNITED STATES REPRESENTATIVE IN CONGRESS 4TH CONGRESSIONAL DISTRICT"
MASSIE = "THOMAS MASSIE"
GALLREIN = "ED GALLREIN"

VOTE_TYPES = (
    "absentee_mail_in",
    "absentee_walk_in",
    "early_voting",
    "election_day_voting",
)

FIELDNAMES = (
    "pdf_file",
    "page_number",
    "county",
    "precinct",
    "source_format",
    "massie_absentee_mail_in",
    "massie_absentee_walk_in",
    "massie_early_voting",
    "massie_election_day_voting",
    "massie_total",
    "gallrein_absentee_mail_in",
    "gallrein_absentee_walk_in",
    "gallrein_early_voting",
    "gallrein_election_day_voting",
    "gallrein_total",
)


@dataclass(frozen=True)
class CandidateCounts:
    absentee_mail_in: int | None
    absentee_walk_in: int | None
    early_voting: int | None
    election_day_voting: int | None
    total: int


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.upper()).strip()


def parse_int(text: str) -> int | None:
    cleaned = text.replace(",", "")
    if re.fullmatch(r"\d+", cleaned):
        return int(cleaned)
    return None


def extract_candidate_counts(lines: list[str], candidate: str) -> CandidateCounts | None:
    normalized_candidate = normalize(candidate)

    for index, line in enumerate(lines):
        if normalize(line) != normalized_candidate:
            continue

        values: list[int] = []
        for following in lines[index + 1 :]:
            if normalize(following) in {MASSIE, GALLREIN} and values:
                break
            if following.startswith(("REP ", "DEM ")) and "Vote For" not in following:
                break
            if following in {"Cast Votes:", "Undervotes:", "Overvotes:"}:
                break

            value = parse_int(following)
            if value is not None:
                values.append(value)
                if len(values) == 5:
                    break

        if len(values) >= 5:
            return CandidateCounts(*values[:4], total=values[4])
        if len(values) == 1:
            return CandidateCounts(None, None, None, None, total=values[0])

    return None


def extract_county(lines: list[str]) -> str:
    for line in lines:
        match = re.fullmatch(r"OFFICIAL BALLOT FOR (.+ COUNTY)", line, flags=re.I)
        if match:
            return match.group(1).title()

    for line in lines:
        match = re.fullmatch(r"(.+ County), KY", line, flags=re.I)
        if match:
            return match.group(1).title()

    return ""


def extract_precinct(lines: list[str]) -> str:
    if not lines:
        return ""

    if normalize(lines[0]) == "PRECINCT SUMMARY RESULTS REPORT":
        for index, line in enumerate(lines):
            if re.fullmatch(r".+ County, KY", line, flags=re.I):
                return lines[index + 1] if index + 1 < len(lines) else ""
        return ""

    return lines[0]


def page_contains_target_race(lines: list[str]) -> bool:
    normalized_lines = [normalize(line) for line in lines]
    return (
        any(RACE_PHRASE in line for line in normalized_lines)
        and MASSIE in normalized_lines
        and GALLREIN in normalized_lines
    )


def row_from_page(pdf_path: Path, page_number: int, text: str) -> dict[str, object] | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not page_contains_target_race(lines):
        return None

    massie = extract_candidate_counts(lines, MASSIE)
    gallrein = extract_candidate_counts(lines, GALLREIN)
    if massie is None or gallrein is None:
        raise ValueError(f"Could not parse both candidate rows on {pdf_path} page {page_number}")

    source_format = "vote_type_breakdown"
    if any(getattr(massie, vote_type) is None for vote_type in VOTE_TYPES) or any(
        getattr(gallrein, vote_type) is None for vote_type in VOTE_TYPES
    ):
        source_format = "total_only"

    row: dict[str, object] = {
        "pdf_file": str(pdf_path),
        "page_number": page_number,
        "county": extract_county(lines),
        "precinct": extract_precinct(lines),
        "source_format": source_format,
        "massie_total": massie.total,
        "gallrein_total": gallrein.total,
    }

    for candidate_prefix, counts in (("massie", massie), ("gallrein", gallrein)):
        for vote_type in VOTE_TYPES:
            value = getattr(counts, vote_type)
            row[f"{candidate_prefix}_{vote_type}"] = "" if value is None else value

    return row


def find_pdf_paths(input_dir: Path) -> list[Path]:
    return sorted(
        input_dir.rglob("*.pdf"),
        key=lambda path: [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", str(path))],
    )


def parse_pdfs(input_dir: Path) -> tuple[list[dict[str, object]], set[Path]]:
    rows: list[dict[str, object]] = []
    matching_pdfs: set[Path] = set()

    for pdf_path in find_pdf_paths(input_dir):
        with fitz.open(pdf_path) as document:
            for page_index, page in enumerate(document, start=1):
                row = row_from_page(pdf_path, page_index, page.get_text("text"))
                if row is None:
                    continue

                rows.append(row)
                matching_pdfs.add(pdf_path)

    return rows, matching_pdfs


def write_csv(rows: list[dict[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract Thomas Massie and Ed Gallrein 4th District precinct results."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("data"),
        help="directory to scan recursively for PDFs; default: data",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data") / "massie_gallrein_precinct_results.csv",
        help="CSV output path; default: data/massie_gallrein_precinct_results.csv",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows, matching_pdfs = parse_pdfs(args.input_dir)
    write_csv(rows, args.output)

    total_only_rows = sum(1 for row in rows if row["source_format"] == "total_only")
    print(f"Matching PDFs: {len(matching_pdfs)}")
    print(f"Rows written: {len(rows)}")
    print(f"Rows with vote-type breakdown: {len(rows) - total_only_rows}")
    print(f"Rows with totals only: {total_only_rows}")
    print(f"Wrote {args.output}")

    if total_only_rows:
        print(
            "Warning: totals-only rows do not expose Absentee/Early/Election Day columns in the PDF text.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

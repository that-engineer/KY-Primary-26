# KY-Primary-26

Short data analysis of Kentucky's 4th congressional district Republican
primary, including precinct result PDFs, turnout scraping, parsing, and plots.

## Setup

```powershell
.\.venv\Scripts\Activate.ps1
```

The turnout scraper reads request metadata from a local `.env` file that is not
committed. It expects `KY_VOTING_COOKIES_JSON` and
`KY_VOTING_HEADERS_JSON` to contain JSON objects.

## Download PDFs

```powershell
python .\download_precinct_pdfs.py
```

The downloader starts at `N=3` and stops at the first bad link. Files are saved
under `data\precinct_pdfs` by default.

## Parse Massie/Gallrein Results

```powershell
python .\parse_massie_gallrein_results.py
```

The parser scans `data\` recursively for PDFs containing the Thomas Massie vs.
Ed Gallrein United States Representative in Congress, 4th Congressional District
race. It writes precinct-level results to
`data\massie_gallrein_precinct_results.csv`.

## Scrape Precinct Turnout

```powershell
python .\scrape_precinct_turnout.py
```

The scraper scans `https://vrsws.sos.ky.gov/liveresults?id={N}` from `N=1718`
through `N=4643` by default. It samples that range with `--scan-step`, refines
county boundaries by binary search, then walks up and down from each discovered
county until the county changes. Counties not present in
`data\massie_gallrein_precinct_results.csv` are skipped. It writes
`data\precinct_turnout.csv` with county, precinct, registered voters, ballots
cast, and voter turnout percentage.

Useful options:

```powershell
python .\scrape_precinct_turnout.py --scan-step 150 --delay 1
python .\scrape_precinct_turnout.py --filter-mode precinct
python .\scrape_precinct_turnout.py --start 1718 --end 4643
```

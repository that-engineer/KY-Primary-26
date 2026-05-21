from pathlib import Path

import pandas as pd
import seaborn as sns
from matplotlib import pyplot as plt
from matplotlib.ticker import PercentFormatter


RESULTS_CSV = Path("data/massie_gallrein_precinct_results.csv")
TURNOUT_CSV = Path("data/precinct_turnout.csv")
OUTPUT_PATH = Path("outputs/massie_gallrein_turnout_vote_share_scatter.png")
OUTPUT_PATH2 = Path("outputs/massie_gallrein_vote_share_kdeplot.png")
OUTPUT_PATH3 = Path("outputs/massie_gallrein_vote_number_scatter.png")
CANDIDATE_PALETTE = {"Thomas Massie": "#1f77b4", "Ed Gallrein": "#d62728"}


def precinct_code(series: pd.Series) -> pd.Series:
    return series.astype(str).str.extract(r"^([^\s-]+)", expand=False).str.strip()


def percent_to_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace("%", "", regex=False), errors="coerce")


def main() -> None:
    results = pd.read_csv(RESULTS_CSV)
    turnout = pd.read_csv(TURNOUT_CSV)

    results["precinct_code"] = precinct_code(results["precinct"])
    turnout["precinct_code"] = precinct_code(turnout["precinct"])
    turnout["voter_turnout_percent"] = percent_to_number(turnout["voter_turnout_percent"])

    merged = results.merge(
        turnout[
            [
                "county",
                "precinct_code",
                "registered_voters",
                "ballots_cast",
                "voter_turnout_percent",
            ]
        ],
        on=["county", "precinct_code"],
        how="left",
        validate="one_to_one",
        indicator=True,
    )

    unmatched = merged[merged["_merge"] != "both"]
    if not unmatched.empty:
        missing = unmatched[["county", "precinct"]].to_string(index=False)
        raise ValueError(f"Turnout rows were not found for these precincts:\n{missing}")

    merged["race_total"] = merged["massie_total"] + merged["gallrein_total"]
    plot_data = pd.concat(
        [
            merged.assign(
                candidate="Thomas Massie",
                vote_share_percent=merged["massie_total"] / merged["race_total"] * 100,
            ),
            merged.assign(
                candidate="Ed Gallrein",
                vote_share_percent=merged["gallrein_total"] / merged["race_total"] * 100,
            ),
        ],
        ignore_index=True,
    )
    print(f"Matched precincts: {len(merged)}")

    ## Voter turnout vs vote share scatterplot
    sns.set_theme(style="whitegrid", context="talk")
    fig, ax = plt.subplots(figsize=(12, 8))
    sns.scatterplot(
        data=plot_data,
        x="voter_turnout_percent",
        y="vote_share_percent",
        hue="candidate",
        palette=CANDIDATE_PALETTE,
        alpha=0.78,
        s=62,
        edgecolor="white",
        linewidth=0.35,
        ax=ax,
    )
    for candidate, color in CANDIDATE_PALETTE.items():
        sns.regplot(
            data=plot_data[plot_data["candidate"] == candidate],
            x="voter_turnout_percent",
            y="vote_share_percent",
            scatter=False,
            ci=None,
            color=color,
            line_kws={"linewidth": 2.5},
            ax=ax,
        )

    ax.set_title("Voter Turnout vs. Vote Share by Precinct")
    ax.set_xlabel("Voter Turnout")
    ax.set_ylabel("Vote Share")
    ax.xaxis.set_major_formatter(PercentFormatter(xmax=100))
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100))
    ax.set_xlim(left=0)
    ax.set_ylim(0, 100)
    ax.legend(title="", frameon=True)
    sns.despine(fig=fig)
    fig.tight_layout()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT_PATH, dpi=200)
    print(f"Saved scatterplot to {OUTPUT_PATH}")
    
    ## Vote share KDE plot
    sns.set_theme(style="whitegrid", context="talk")
    fig, ax = plt.subplots(figsize=(12, 8))
    sns.kdeplot(
        data=plot_data,
        x="vote_share_percent",
        hue="candidate",
        palette=CANDIDATE_PALETTE,
        alpha=0.78,
        linewidth=2.5,
        ax=ax,
    )

    ax.set_title("Vote Share KDE plot")
    ax.set_xlabel("Vote Share")
    ax.set_ylabel("Probability Density Function")
    ax.xaxis.set_major_formatter(PercentFormatter(xmax=100))
    ax.set_xlim(left=0, right=100)
    sns.despine(fig=fig)
    fig.tight_layout()

    OUTPUT_PATH2.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT_PATH2, dpi=200)
    print(f"Saved kdeplot to {OUTPUT_PATH2}")
    
    ## Number of votes per candidate for each precinct
    sns.set_theme(style="whitegrid", context="talk")
    fig, ax = plt.subplots(figsize=(12, 8))
    sns.scatterplot(
        data=plot_data,
        x="massie_total",
        y="gallrein_total",
        hue="voter_turnout_percent",
        palette='viridis',
        alpha=0.78,
        s=62,
        edgecolor="white",
        linewidth=0.35,
        ax=ax,
    )

    ax.set_title("Votes for each Candidate at Every Precinct")
    ax.set_xlabel("Total votes for Massie")
    ax.set_ylabel("Total votes for Gallrein")
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    lims = [
        min(ax.get_xlim()[0], ax.get_ylim()[0]),
        max(ax.get_xlim()[1], ax.get_ylim()[1])
    ]
    ax.plot(lims, lims, linestyle='--', color='black', linewidth=1.0)
    ax.legend(title="", frameon=True)
    ax.set_aspect('equal')
    sns.despine(fig=fig)
    fig.tight_layout()

    OUTPUT_PATH3.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT_PATH3, dpi=200)
    print(f"Saved scatterplot to {OUTPUT_PATH3}")

if __name__ == "__main__":
    main()

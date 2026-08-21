"""
extract_tolerated_sentences.py

Extracts text starting at "the patient tolerated" (case-insensitive) and
running to the next "." from text columns in an Excel file, and writes the
results to a new Excel file.

Usage:
    python extract_tolerated_sentences.py input.xlsx --output results.xlsx
    python extract_tolerated_sentences.py input.xlsx --sheet "Sheet1" --columns "Report Text" "Findings"

If --columns is not given, every text-like column is scanned.
"""

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

# Matches from "the patient tolerated" up to and including the next period.
# re.DOTALL lets "." match newlines too, so it still works if the sentence
# is broken across lines within a cell.
PATTERN = re.compile(r"the patient tolerated.*?\.", re.IGNORECASE | re.DOTALL)


def extract_matches(text) -> list[str]:
    """Return all matches of PATTERN in `text`."""
    if not isinstance(text, str) or not text.strip():
        return []
    return [m.group(0).strip() for m in PATTERN.finditer(text)]


def run_debug_report(input_path: Path, sheet):
    """Print diagnostics to help figure out why zero matches were found."""
    raw = pd.read_excel(input_path, sheet_name=sheet)
    sheets = raw if isinstance(raw, dict) else {str(sheet if sheet is not None else 0): raw}

    print("=== DEBUG REPORT ===")
    for name, df in sheets.items():
        print(f"\nSheet: {name}  ({len(df)} rows)")
        print("Columns and dtypes:")
        for col in df.columns:
            print(f"  - {col!r}: {df[col].dtype}")

        phrase = "the patient tolerated"
        total_hits = 0
        for col in df.columns:
            if not pd.api.types.is_string_dtype(df[col]) and df[col].dtype != object:
                continue
            series = df[col].astype(str).str.lower()
            hits = series.str.contains(phrase, regex=False, na=False).sum()
            if hits:
                print(f"  Raw substring '{phrase}' found in column {col!r}: {hits} cell(s)")
                total_hits += hits
        if total_hits == 0:
            print(f"  No raw occurrences of '{phrase}' found anywhere in this sheet.")
    print("=== END DEBUG REPORT ===\n")


def process_excel(input_path: Path, sheet, columns=None) -> pd.DataFrame:
    df = pd.read_excel(input_path, sheet_name=sheet)

    if isinstance(df, dict):
        frames = []
        for sheet_name, sheet_df in df.items():
            frames.append(_extract_from_dataframe(sheet_df, columns, sheet_name))
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    return _extract_from_dataframe(df, columns, sheet)


def _extract_from_dataframe(df: pd.DataFrame, columns, sheet_name) -> pd.DataFrame:
    if isinstance(columns, str):
        columns = [columns]

    if columns:
        target_cols = [c for c in columns if c in df.columns]
        missing = set(columns) - set(target_cols)
        if missing:
            print(f"Warning: columns not found and skipped: {missing}", file=sys.stderr)
    else:
        target_cols = [
            c for c in df.columns
            if pd.api.types.is_string_dtype(df[c]) or df[c].dtype == object
        ]

    results = []
    for row_idx, row in df.iterrows():
        for col in target_cols:
            for match in extract_matches(row[col]):
                results.append(
                    {
                        "sheet": sheet_name,
                        "row": row_idx + 2,  # +2 accounts for header row and 0-index
                        "column": col,
                        "match": match,
                    }
                )

    return pd.DataFrame(results, columns=["sheet", "row", "column", "match"])


def main():
    parser = argparse.ArgumentParser(
        description='Extract text starting at "the patient tolerated" and ending at the next "." from an Excel file.'
    )
    parser.add_argument("input", type=Path, help="Path to the input .xlsx file")
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Path to write results to (.xlsx or .csv). Defaults to <input>_tolerated_sentences.xlsx"
    )
    parser.add_argument(
        "--sheet", default=0,
        help="Sheet name or index to read (default: first sheet). Use 'all' to scan every sheet."
    )
    parser.add_argument(
        "--columns", nargs="*", default=None,
        help="Specific column name(s) to scan. If omitted, all text columns are scanned."
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Print sheet names, column names/dtypes, and a raw substring match count "
             "before running extraction, to help diagnose zero-result runs."
    )
    parser.add_argument(
        "--debug-phrase", default="the patient tolerated",
        help="Phrase to search for in --debug mode (default: 'the patient tolerated'). "
             "Try a shorter phrase like 'tolerated' to see actual wording variants."
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    sheet_arg = None if str(args.sheet).lower() == "all" else args.sheet

    if args.debug:
        run_debug_report(args.input, sheet_arg)

    results_df = process_excel(args.input, sheet_arg, args.columns)

    if results_df.empty:
        print('No matches for "the patient tolerated ... ." were found.')
        return

    output_path = args.output or args.input.with_name(
        args.input.stem + "_tolerated_sentences.xlsx"
    )

    if output_path.suffix.lower() == ".csv":
        results_df.to_csv(output_path, index=False)
    else:
        results_df.to_excel(output_path, index=False)

    print(f"Found {len(results_df)} match(es). Results saved to: {output_path}")


if __name__ == "__main__":
    main()
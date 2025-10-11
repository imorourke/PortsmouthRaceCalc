"""
Main entry point for checking the database results
"""

from db_input import get_database
from database.series.series import Series

from pathlib import Path
from shutil import rmtree

from decimal import Decimal

import yaml
import sys

from typing import Any, Callable, List, Optional, Tuple

import subprocess


PERL_DIR = Path(__file__).parent / "perl"
PERL_FILE = PERL_DIR / "race_scorer_v04.pl"


def main():
    database = get_database()

    test_dir_base = PERL_DIR / "tests"
    if not test_dir_base.exists():
        test_dir_base.mkdir()

    overall_success = True

    for name, s in database.series.items():
        check_dir = test_dir_base / name
        if check_dir.exists():
            rmtree(check_dir)

        check_dir.mkdir()

        with (check_dir / "boats.yaml").open("w") as f:
            yaml.safe_dump(s.perl_boat_dict(), f)

        with (check_dir / "series.yaml").open("w") as f:
            yaml.safe_dump(s.perl_series_dict(), f)

        args = ["perl", PERL_FILE.absolute()]

        success = False

        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=check_dir
        )
        _, stderr = proc.communicate()

        if proc.returncode != 0:
            print(f"{s.name} Process Error!", file=sys.stderr)
            for l in stderr.decode("utf-8").splitlines():
                print(f"    {l.strip()}", file=sys.stderr)

        else:
            with (check_dir / "dumper.yaml").open("r") as f:
                dump_output = yaml.safe_load(f)

            success = check_series_with_file(series=s, perl_output=dump_output)

            if not success:
                print(f"{s.name} Check Error :-(", file=sys.stderr)

        if not success:
            overall_success = False

    if overall_success:
        print("All Pass!")
    else:
        sys.exit(1)


def check_series_with_file(series: Series, perl_output: dict) -> bool:
    # Create functions for each section to check
    def check_finished_race_count(
        series: Series, perl: dict
    ) -> Optional[Tuple[str, Any, Any]]:
        # Check the finished race count
        finished_race_count = sum(
            [1 for r in series.races if skip in r.get_skipper_race_points()]
        )
        if finished_race_count != perl["finished_races"]:
            return "Count", finished_race_count, perl["finished_races"]

        return None

    def check_series_points(
        series: Series, perl: dict
    ) -> Optional[Tuple[str, Any, Any]]:
        # Check the series points
        series_points = series.skipper_points_list(skip)
        series_points_perl = perl["low_n_list"]

        # If the skipper finished the series...
        if series_points:
            # Move to points scored
            series_points = series_points.points_scored

            # Ensure that there are no string results in the Perl output
            if any([isinstance(sp, str) for sp in series_points_perl]):
                return "Number/String", series_points, series_points_perl

            # Convert into decimal values
            series_points_perl = [round(Decimal(p), 1) for p in series_points_perl]

            # Check that the lengths and sum match
            if len(series_points) != len(series_points_perl):
                return "Point Count", len(series_points), len(series_points_perl)

            # Check each value for error
            for i, (a, b) in enumerate(
                zip(sorted(series_points), sorted(series_points_perl))
            ):
                if a != b:
                    return f"Point[{i}]", a, b

            # Check that the lengths and sum match
            if sum(series_points) != sum(series_points_perl):
                return "Sum Count", len(series_points), len(series_points_perl)

        elif len(series_points_perl) != 1:
            # Erroneous Perl output
            return "Array Length", 1, series_points_perl
        else:
            # Check the output strings for DNQ/na
            series_points_perl = series_points_perl[0]
            if series_points_perl not in ("na", "DNQ"):
                return "Point String", "na/DNQ", series_points_perl

        return None

    def check_race_results(
        series: Series, perl: dict
    ) -> Optional[Tuple[str, Any, Any]]:
        for i, r in enumerate(series.races):
            # Format Python result
            pts = r.get_skipper_race_points()
            if skip in pts:
                pts = pts[skip]
            elif skip in r.rc_skippers():
                pts = "RC"
            else:
                pts = None

            # Format Perl result
            pts_perl = perl["race"]
            if i + 1 in pts_perl:
                pts_perl = pts_perl[i + 1]
            else:
                pts_perl = None

            # Check for agreement
            if pts is None and pts_perl is None:
                continue
            elif pts != pts_perl:
                return f"Race[{i + 1}]", pts, pts_perl

        return None

    def check_rc_race_count(
        series: Series, perl: dict
    ) -> Optional[Tuple[str, Any, Any]]:
        # Check the RC race count
        rc_count = sum([1 for r in series.races if skip in r.rc_skippers()])
        rc_count_perl = perl["rced_races"]

        if rc_count != rc_count_perl:
            return "RC Count", rc_count, rc_count_perl

    def check_rc_points(series: Series, perl: dict) -> Optional[Tuple[str, Any, Any]]:
        # Check the RC points for the skipper
        rc_pts = series.get_skipper_rc_points(skip)
        rc_pts_perl = perl["rc_points"]
        if rc_pts is None:
            if rc_pts_perl != "na":
                return "RC Points", "na", rc_pts_perl
        else:
            rc_pts_perl = round(Decimal(rc_pts_perl), 1)
            if rc_pts != rc_pts_perl:
                return "RC Points", rc_pts, rc_pts_perl

        return None

    check_functions: List[Callable[[Series, dict], Optional[Tuple[str, Any, Any]]]] = [
        check_finished_race_count,
        check_series_points,
        check_race_results,
        check_rc_race_count,
        check_rc_points,
    ]

    # Create a list of error outputs
    error_outputs: List[str] = list()

    # Iterate over each skipper
    for skip in series.get_all_skippers():
        # Extract the Perl results
        perl_results = perl_output["skip"][skip.identifier]

        # Check results
        for fcn in check_functions:
            res = fcn(series, perl_results)
            if res:
                prm_name, py_val, perl_val = res
                error_outputs.append(
                    f"Skipper {skip.identifier} Parameter `{prm_name}` Python=`{py_val}` != Perl=`{perl_val}`"
                )
                break

    if error_outputs:
        print(f"Series {series.name}")
        for l in error_outputs:
            print(f"  {l}")

        return False

    return True


if __name__ == "__main__":
    main()

"""
Main entry point for a static compilation of the scoring
"""

import argparse
from decimal import Decimal
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable

from .staticgen import StaticApplication
from .database import utils, MasterDatabase
from .database.series.series import Series

import yaml

# Disable live plotting of data, so plotting only writes to files
import matplotlib

matplotlib.use("Agg")

PERL_DIR = Path(__file__).parent / "perl"
PERL_FILE = PERL_DIR / "race_scorer_v04.pl"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input_file", type=Path)
    p.add_argument("-t", "--test", default=None, type=Path)
    p.add_argument("-w", "--website", default=None, type=Path)

    args = p.parse_args()

    database = MasterDatabase(config_file=args.input_file)
    database.trim_fleets_lists()

    if args.website is not None:
        app = StaticApplication(__name__, build_path=args.website)
        app.add_list(name="series_name", values=list(database.series.keys()))
        app.add_list(name="fleet_name", values=list(database.fleets.keys()))
        app.add_list(
            name="boat_code",
            values={
                name: fleet.boat_types.keys() for name, fleet in database.fleets.items()
            },
        )
        app.add_list(name="skipper_name", values=list(database.skippers.keys()))
        app.add_list(
            name="series_race_index",
            values={
                name: list(range(0, len(series.races)))
                for name, series in database.series.items()
            },
        )
        app.jinja_env.globals.update(format_time=utils.format_time)

        @app.route("/favicon.ico")
        def favicon():
            return app.send_from_directory("static", "favicon.ico", binary=True)

        @app.route("/index.html")
        def index_page():
            max_count = max([len(l[1]) for l in database.series_display_group])

            return app.render_template(
                "index.html",
                database=database,
                series_list=list(reversed(list(database.series.values()))),
                series_latest=database.series_latest,
                fleets_list=list(database.fleets.values()),
                series_group=database.series_display_group,
                series_group_count=max_count,
            )

        @app.route("/series/<string:series_name>/index.html")
        def series_page(series_name: str):
            return app.render_template(
                "series_page.html",
                database=database,
                series=database.series[series_name],
            )

        @app.route(
            "/series/<string:series_name>/race_<int:series_name/series_race_index>.html"
        )
        def series_race_page(series_name: str, series_race_index: int):
            series = database.series[series_name]
            race = series.races[series_race_index]

            return app.render_template(
                "race_page.html", database=database, series=series, race=race
            )

        @app.route("/series/<string:series_name>/images/rank_history.png")
        def series_rank_history_plot(series_name: str):
            return database.series[series_name].get_plot_series_rank()

        @app.route("/series/<string:series_name>/images/point_history.png")
        def series_point_history_plot(series_name: str):
            return database.series[series_name].get_plot_series_points()

        @app.route("/series/<string:series_name>/images/all_race_results.png")
        def series_normalized_race_results_plot(series_name: str):
            return database.series[series_name].get_plot_normalized_race_time_results()

        @app.route("/series/<string:series_name>/images/boat_pie_chart.png")
        def series_boat_pie_plot(series_name: str):
            return database.series[series_name].get_plot_boat_pie_chart()

        @app.route(
            "/series/<string:series_name>/images/race_<int:series_name/series_race_index>.png"
        )
        def series_individual_race_results_plot(
            series_name: str, series_race_index: int
        ):
            return (
                database.series[series_name]
                .races[series_race_index]
                .get_plot_race_time_results()
            )

        @app.route("/series/<string:series_name>/previous_input.yaml")
        def series_previous_input_file(series_name: str):
            return yaml.safe_dump(database.series[series_name].perl_series_dict())

        @app.route("/series/<string:series_name>/previous_boat.yaml")
        def series_previous_boat_file(series_name: str):
            return yaml.safe_dump(database.series[series_name].perl_boat_dict())

        @app.route("/fleet/<string:fleet_name>/index.html")
        def fleet_page(fleet_name: str):
            if fleet_name in database.fleets:
                fleet = database.fleets[fleet_name]
                return app.render_template(
                    "fleet_page.html", database=database, fleet=fleet
                )
            else:
                raise ValueError(f"{fleet_name} not in database")

        @app.route(
            "/fleet/<string:fleet_name>/boats/<string:fleet_name/boat_code>.html"
        )
        def boat_page(fleet_name: str, boat_code: str):
            fleet = database.fleets[fleet_name]
            boat = fleet.get_boat(boat_code)

            return app.render_template(
                "boat_page.html", database=database, boat=boat, fleet=fleet
            )

        @app.route(
            "/fleet/<string:fleet_name>/boats/images/<string:fleet_name/boat_code>_statistics.png"
        )
        def boat_page_points_plot(fleet_name: str, boat_code: str):
            return database.boat_statistics[fleet_name][boat_code].get_plot_points()

        @app.route("/skippers/index.html")
        def skipper_page_all():
            return app.render_template("skippers_page_all.html", database=database)

        @app.route("/skippers/<string:skipper_name>.html")
        def skipper_page_ind(skipper_name: str):
            return app.render_template(
                "skippers_page_individual.html",
                database=database,
                skipper_name=skipper_name,
            )

        @app.route("/skippers/images/<string:skipper_name>_boats.png")
        def skipper_page_boats_used_plot(skipper_name: str):
            return database.skipper_statistics[skipper_name].get_plot_boats()

        @app.route("/skippers/images/<string:skipper_name>_results.png")
        def skipper_page_race_results_plot(skipper_name: str):
            return database.skipper_statistics[skipper_name].get_plot_race_results()

        app.build()

    if args.test is not None:
        main_check(database, args.test)


def main_check(database: MasterDatabase, test_dir_base: Path):
    if not test_dir_base.exists():
        test_dir_base.mkdir()

    overall_success = True

    for name, s in database.series.items():
        check_dir = test_dir_base / name
        if check_dir.exists():
            shutil.rmtree(check_dir)

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
    ) -> tuple[str, Any, Any] | None:
        # Check the finished race count
        finished_race_count = sum(
            [1 for r in series.races if skip in r.get_skipper_race_points()]
        )
        if finished_race_count != perl["finished_races"]:
            return "Count", finished_race_count, perl["finished_races"]

        return None

    def check_series_points(series: Series, perl: dict) -> tuple[str, Any, Any] | None:
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

    def check_race_results(series: Series, perl: dict) -> None | tuple[str, Any, Any]:
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

    def check_rc_race_count(series: Series, perl: dict) -> tuple[str, Any, Any] | None:
        # Check the RC race count
        rc_count = sum([1 for r in series.races if skip in r.rc_skippers()])
        rc_count_perl = perl["rced_races"]

        if rc_count != rc_count_perl:
            return "RC Count", rc_count, rc_count_perl

    def check_rc_points(series: Series, perl: dict) -> tuple[str, Any, Any] | None:
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

    check_functions: list[Callable[[Series, dict], None | tuple[str, Any, Any]]] = [
        check_finished_race_count,
        check_series_points,
        check_race_results,
        check_rc_race_count,
        check_rc_points,
    ]

    # Create a list of error outputs
    error_outputs: list[str] = list()

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

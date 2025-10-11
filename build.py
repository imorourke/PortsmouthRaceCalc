"""
Main entry point for a static compilation of the scoring
"""

from staticgen import StaticApplication

from db_input import get_database
import database.utils as utils

import yaml

# Disable live plotting of data, so plotting only writes to files
import matplotlib

matplotlib.use("Agg")

# Construct the database instance
database = get_database()

app = StaticApplication(__name__)
app.add_list(name="series_name", values=list(database.series.keys()))
app.add_list(name="fleet_name", values=list(database.fleets.keys()))
app.add_list(
    name="boat_code",
    values={name: fleet.boat_types.keys() for name, fleet in database.fleets.items()},
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
        "series_page.html", database=database, series=database.series[series_name]
    )


@app.route("/series/<string:series_name>/race_<int:series_name/series_race_index>.html")
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
def series_individual_race_results_plot(series_name: str, series_race_index: int):
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
        return app.render_template("fleet_page.html", database=database, fleet=fleet)
    else:
        raise ValueError(f"{fleet_name} not in database")


@app.route("/fleet/<string:fleet_name>/boats/<string:fleet_name/boat_code>.html")
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


if __name__ == "__main__":
    app.build()

"""
Initializes the main database object library
"""

from .fleets import Fleet, BoatType, WindMap
from .skippers import Skipper

from .series import Race, Series
from .series import finishes

from .statistics import SkipperStatistics, BoatStatistics

import datetime
from pathlib import Path
import yaml

from collections.abc import Sequence


class MasterDatabase:
    """
    Master database to keep track of all input parameters
    """

    def __init__(
        self,
        config_file: Path,
    ):
        """
        Initializes the master database with the provided inputs
        :param input_folder: folder where the input files are provided
        :param fleet_file: filename to override the fleet input file
        :param skipper_file: filename to override the skipper input file
        :param series_file: filename to override the series input file
        """
        # Define the input folder
        self.config_file: Path = config_file
        with self.config_file.open("r") as f:
            self.config: dict[str, ...] = yaml.safe_load(f)

        # Define the fleet name
        self.name = (
            self.config["name"] if "name" in self.config else "PortsmouthRaceCalc"
        )
        self.name_short = (
            self.config["name_short"] if "name_short" in self.config else "PRC"
        )
        self.disclaimer = (
            self.config["disclaimer"] if "disclaimer" in self.config else None
        )
        self.code_location = (
            self.config["code_location"] if "code_location" in self.config else None
        )

        # Load the database
        self.fleets = self.__load_fleets()
        self.series, self.skippers = self.__load_series()

        self.series_latest = list(reversed(list(self.series.values())))[0]

        # Group series values by name
        self.series_by_year: Dict[str, List[Series]] = dict()
        for s in self.series.values():
            s_g = s.name.split("_")[0].strip()
            if s_g not in self.series_by_year:
                self.series_by_year[s_g] = list()

            self.series_by_year[s_g].append(s)

        # Create the new series values
        total_series: Dict[str, Series] = dict()

        for year_name, year_series in self.series_by_year.items():
            new_s_fleet = None
            fleet_options: Optional[Tuple[int, Fleet]] = None
            for s in year_series:
                s_opts = s.valid_required_skippers, s.fleet
                if fleet_options is None:
                    fleet_options = s_opts
                elif fleet_options != s_opts:
                    raise RuntimeError(f"cannot create overall series for {year_name}")

            if fleet_options is None:
                continue

            new_s = Series(
                name=f"{year_name}_season_total",
                valid_required_skippers=fleet_options[0],
                fleet=fleet_options[1],
                qualify_count_override=None,
                exclude_from_statistics=True,
            )

            for s in year_series:
                for r in s.races:
                    new_s.add_race(r)

            total_series[year_name] = new_s

        max_count = max([len(l) for l in self.series_by_year.values()])

        self.series_display_group: list[tuple[str, Sequence[None | Series]]] = list()

        for sl_key in reversed(sorted(self.series_by_year.keys())):
            sl: List[Series] = self.series_by_year[sl_key]
            sl.sort(key=lambda x: x.name)
            sl_opt: List[Optional[Series]] = list(sl)

            while len(sl_opt) < max_count:
                sl_opt.append(None)

            sl_opt.append(total_series[sl_key] if sl_key in total_series else None)

            self.series_display_group.append((sl_key, sl_opt))

        for y, s in total_series.items():
            if s.name not in self.series:
                self.series[s.name] = s
                self.series_by_year[y].append(s)

        # Define the statistics
        self.skipper_statistics: dict[str, SkipperStatistics] = dict()
        self.boat_statistics: dict[str, dict[str, BoatStatistics]] = dict()

        # Define the figures to generate
        self.__fig_gen_dict: dict[str, bool] = dict()

        # Update statistics
        self.update_statistics()

    def trim_fleets_lists(self) -> None:
        """
        Trims the fleet lists to only contain boats that exist in a series
        """
        # Remove any boat that has zero races tied to it
        for fleet_name, fleet in self.fleets.items():
            fleet_stats = self.boat_statistics[fleet_name]

            for boat_name, stats in fleet_stats.items():
                if not stats.has_nonzero_races():
                    fleet.boat_types.pop(boat_name)

        # Update statistics
        self.update_statistics()

    def latest_race_date(self) -> datetime.datetime | None:
        """
        Provides the latest race time
        :return: the latest race time in all series
        """
        latest = None

        for s in self.series.values():
            series_date = s.latest_race_date()
            if series_date is None:
                continue
            elif latest is None or latest < series_date:
                latest = series_date

        return latest

    def latest_race_date_string(self) -> str:
        """
        Provides a string associated with the latest race date
        :return: string for the latest race date in the database
        """
        date = self.latest_race_date()
        if date is None:
            return "Unknown"
        else:
            return date.strftime("%B %d, %Y")

    def __update_statistics_skipper(self) -> None:
        """
        Updates the skipper statistics
        """
        # Clear out existing statistics
        self.skipper_statistics.clear()

        # Define all race results
        race_results: Dict[Skipper, List[int]] = dict()
        boat_results: Dict[Skipper, List[BoatType]] = dict()

        # Check all series, races, and values for results
        for series in self.series.values():
            if series.exclude_from_statistics:
                continue

            for race in series.races:
                # Iterate over the race results
                for skipper, race_result in race.get_skipper_race_points().items():
                    # Define the rounded point result
                    point_value = int(round(race_result))

                    # Add the point result into the skipper point results
                    if skipper not in race_results:
                        race_results[skipper] = list()

                    race_results[skipper].append(point_value)

                # Iterate over the race finishes for which boats were used by each skipper
                for skipper, race_finish in race._race_finishes.items():
                    if skipper not in boat_results:
                        boat_results[skipper] = list()

                    if isinstance(race_finish, finishes.RaceFinishRC):
                        continue
                    else:
                        boat_results[skipper].append(race_finish.boat)

        # Total the results for the skippers
        for skipper in self.skippers.values():
            results_skipper: Dict[int, int] = dict()
            results_boat: Dict[BoatType, int] = dict()

            if skipper in race_results:
                for race_finish in race_results[skipper]:
                    if race_finish not in results_skipper:
                        results_skipper[race_finish] = 0
                    results_skipper[race_finish] += 1

            if skipper in boat_results:
                for boat in boat_results[skipper]:
                    if boat not in results_boat:
                        results_boat[boat] = 0
                    results_boat[boat] += 1

            self.skipper_statistics[skipper.identifier] = SkipperStatistics(
                skipper=skipper, point_counts=results_skipper, boats_used=results_boat
            )

    def __update_statistics_boat(self) -> None:
        """
        Updates the boat statistics
        """
        # Clear out existing statistics
        self.boat_statistics.clear()

        # Define all race results
        boat_results: dict[BoatType, list[int]] = dict()
        skippers_for_boat: dict[BoatType, list[Skipper]] = dict()
        series_for_boat: dict[BoatType, dict[str, Series]] = dict()

        # Check all series, races, and values for results
        for series in self.series.values():
            if series.exclude_from_statistics:
                continue

            for race in series.races:
                # Iterate over the race results
                for skipper, race_result in race.get_skipper_race_points().items():
                    # Define the rounded point result
                    point_value = int(round(race_result))

                    # Extract the boat used
                    boat = race._race_finishes[skipper].boat

                    # Add the point result into the boat point results
                    if boat not in boat_results:
                        boat_results[boat] = list()

                    boat_results[boat].append(point_value)

                    # Add the skipper to the boat list
                    if boat not in skippers_for_boat:
                        skippers_for_boat[boat] = list()
                    if skipper not in skippers_for_boat[boat]:
                        skippers_for_boat[boat].append(skipper)

                    # Add the series to the boat list
                    if boat not in series_for_boat:
                        series_for_boat[boat] = dict()
                    if series.name not in series_for_boat[boat]:
                        series_for_boat[boat][series.name] = series

        # Total the results for the fleets and boats
        for fleet in self.fleets.values():
            fleet_results: Dict[BoatType, BoatStatistics] = dict()

            for boat in fleet.boat_types.values():
                results_boat: Dict[int, int] = dict()
                results_skipper: List[Skipper] = list()
                results_series: List[Series] = list()

                if boat in boat_results:
                    for result in boat_results[boat]:
                        if result not in results_boat:
                            results_boat[result] = 0
                        results_boat[result] += 1

                if boat in skippers_for_boat:
                    results_skipper.extend(
                        sorted(skippers_for_boat[boat], key=lambda x: x.identifier)
                    )

                if boat in series_for_boat:
                    results_series.extend(
                        reversed(
                            sorted(series_for_boat[boat].values(), key=lambda x: x.name)
                        )
                    )

                fleet_results[boat] = BoatStatistics(
                    boat=boat,
                    point_counts=results_boat,
                    skippers=results_skipper,
                    series=results_series,
                )

            self.boat_statistics[fleet.name] = {
                boat.code: stats for boat, stats in fleet_results.items()
            }

    def update_statistics(self) -> None:
        """
        Updates and recalculates any statistics contained within the database
        """
        self.__update_statistics_skipper()
        self.__update_statistics_boat()

    def __load_fleets(self) -> dict[str, Fleet]:
        """
        Loads the fleet database from the provided files
        :return: the list of fleets loaded
        """
        # Read the YAML input file
        fleet_data: dict[str, str | dict[str, int | list[int]]] = self.config["fleets"]

        # Initialize the dictionary
        fleets: dict[str, Fleet] = dict()

        # Iterate over each fleet name
        for fleet_name in fleet_data:
            # Raise error if fleet name already exists
            if fleet_name in fleets:
                raise ValueError("Cannot add a duplicate fleet {:s}".format(fleet_name))

            # Otherwise, extract the dictionary
            fleet_dict = fleet_data[fleet_name]

            # Read in the portsmouth table for the fleet
            portsmouth_file: Path = (
                self.config_file.parent / fleet_dict["portsmouth_table"]
            )
            with portsmouth_file.open("r") as table_handle:
                boat_table = table_handle.read()

            # Obtain the wind mapping
            wind_map_dict = fleet_dict["wind_map"]
            wind_map = WindMap(default_index=wind_map_dict["default_index"])
            for map_val in wind_map_dict["map_values"]:
                wind_map.add_wind_parameters(
                    start_wind=map_val["start_bf"],
                    end_wind=map_val["end_bf"],
                    index=map_val["index"],
                )

            # Define the fleet object
            fleets[fleet_name] = Fleet(
                name=fleet_name,
                boat_types=BoatType.load_from_csv(
                    fleet_name=fleet_name, csv_table=boat_table, wind_map=wind_map
                ),
                wind_map=wind_map,
                source=fleet_dict["source"] if "source" in fleet_dict else None,
            )

        # Set the fleet object to the loaded parameters
        return fleets

    def __load_series(self) -> tuple[dict[str, Series], dict[str, Skipper]]:
        """
        Loads the series database from the provided files
        """
        # Initialize the series dictionary
        series_values = dict()
        series_skippers: dict[str, Skipper] = dict()

        # Read in the series YAML data
        series_data = self.config["series"]

        # Iterate over the series name
        for series_name in series_data:
            # Raise an error if the series name already exists
            if series_name in series_values:
                raise ValueError(
                    "Cannot add a duplicate series {:s}".format(series_name)
                )

            # Extract the series dictionary
            s = series_data[series_name]

            # Extract the fleet name, raising an error if it doesn't exist, and extract the fleet if it does
            fleet_name = s["fleet"]
            if fleet_name not in self.fleets:
                raise ValueError(
                    "Fleet {:s} does not exist in fleet structure".format(fleet_name)
                )
            fleet = self.fleets[fleet_name]

            # Define the qualify count overrides
            if "qualify_count" in series_data:
                qualify_count_override = series_data["qualify_count"]
            else:
                qualify_count_override = None

            # Define the series object
            series = Series(
                name=series_name,
                valid_required_skippers=s["valid_required_skippers"],
                fleet=fleet,
                qualify_count_override=qualify_count_override,
            )

            # Look for a series offset time
            series_offset_time = 0
            if "offset_time" in s:
                series_offset_time = s["offset_time"]

            # Load in the race data YAML object from the provided file
            race_file = self.config_file.parent / s["race_file"]
            with race_file.open("r") as f:
                all_race_data = yaml.safe_load(f)

            # Define a function for getting skipper values
            def get_skipper(skip_id_val: str) -> Skipper:
                if skip_id_val not in series_skippers:
                    series_skippers[skip_id_val] = Skipper(identifier=skip_id_val)

                return series_skippers[skip_id_val]

            # Extract the boat data and set default boats for each skipper
            boat_list = all_race_data["boats"]
            for skipper_id, boat_code in boat_list.items():
                series.add_skipper_boat(
                    skipper=get_skipper(skipper_id),
                    boat=series.fleet.get_boat(boat_code),
                )

            # Extract the race data
            race_list = all_race_data["races"]

            # Iterate over each race date
            for race_date_dict in race_list:
                # Extract the race committee
                rc_racers = race_date_dict["rc"]
                if rc_racers is None:
                    rc_racers = dict()

                race_committee = [get_skipper(person) for person in rc_racers]

                # Iterate over each race
                for race_dict in race_date_dict["races"]:
                    # Define the race boat dictionary
                    race_boat_dict = dict(series.boat_dict)

                    if "boat_overrides" in race_dict:
                        # Extract the race boat overrides
                        race_boat_overrides = race_dict["boat_overrides"]

                        # Update the values based on the skipper identifiers provided
                        for skipper_id, boat_code in race_boat_overrides.items():
                            skip = get_skipper(skipper_id)
                            if skip in race_boat_dict:
                                race_boat_dict[skip] = series.fleet.get_boat(boat_code)

                    # Create the race object
                    race_name = f"{series.name}##{len(series.races)}"
                    race = Race(
                        name=race_name,
                        fleet=series.fleet,
                        boat_dict=race_boat_dict,
                        required_skippers=series.valid_required_skippers,
                        rc=race_committee,
                        date=datetime.datetime.strptime(
                            race_date_dict["date"], "%Y_%m_%d"
                        ),
                        wind_bf=race_dict["wind_bf"],
                        notes=race_dict["notes"],
                    )

                    # Extract the race time results
                    time_values = race_dict.get("times", {})

                    # Define the override time
                    offset_time = series_offset_time
                    if "offset_time" in race_dict:
                        offset_time = race_dict["offset_time"]

                    # Set an empty list of no time values are provided
                    if time_values is None:
                        time_values = list()

                    # Iterate over each of the skipper time values, creating a race time and adding it to the race
                    for skipper_id in time_values:
                        # Extract the time result
                        input_finish_result = time_values[skipper_id]

                        # Extract the skipper and boat
                        skipper = get_skipper(skipper_id)

                        if skipper in race.boat_dict:
                            boat = race.boat_dict[skipper]
                        else:
                            raise ValueError(
                                f"unknown boat provided for skipper {skipper.identifier}"
                            )

                        # Check for other race types
                        if isinstance(input_finish_result, str):
                            # Define the lowercase values
                            input_finish_result = input_finish_result.lower()

                            # Check for finish in place
                            if input_finish_result == "dnf":
                                race_finish = finishes.RaceFinishDNF(
                                    boat=boat, skipper=skipper
                                )
                            elif input_finish_result == "dsq":
                                race_finish = finishes.RaceFinishDQ(
                                    boat=boat, skipper=skipper
                                )
                            elif input_finish_result == "dns":
                                race_finish = finishes.RaceFinishDNS(
                                    boat=boat, skipper=skipper
                                )
                            elif input_finish_result[:3] == "fip":
                                race_finish = finishes.RaceFinishFIP(
                                    boat=boat,
                                    skipper=skipper,
                                    place=int(input_finish_result[3:]),
                                )
                            else:
                                raise ValueError(
                                    f'unknown race finish type "{input_finish_result}"'
                                )
                        elif isinstance(input_finish_result, int):
                            race_finish = finishes.RaceFinishTime(
                                boat=race.boat_dict[skipper],
                                skipper=skipper,
                                wind_bf=race.wind_bf,
                                input_time_s=input_finish_result,
                                offset_time_s=offset_time,
                            )
                        else:
                            raise ValueError("unknown finish time provided")

                        # Add the resulting race finish
                        race.add_skipper_finish(race_finish)

                    # Add the race to the series
                    series.add_race(race)

            # Reset the series to update values
            series.reset()

            # Set the series value to the loaded parameters
            series_values[series_name] = series

        # Set the series object to the loaded parameters
        return series_values, series_skippers

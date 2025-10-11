"""
Provides a database for use in calculating and scoring a race series
"""

import datetime
import decimal
import math

from typing import Union, List, Dict, Optional, Tuple

from dataclasses import dataclass

from ..fleets import Fleet, BoatType
from ..skippers import Skipper

from ..utils import capitalize_words, round_score
from ..utils.plotting import figure_to_data

import matplotlib.pyplot as plt

from . import Race, finishes


@dataclass
class ScoreList:
    """
    Provides a class to maintain the score list
    """

    # Points used in the scoring
    points_scored: List[decimal.Decimal]

    # Extra points not used in scoring
    points_excluded: List[decimal.Decimal]

    @property
    def score(self) -> decimal.Decimal:
        """
        Returns the resulting score value
        """
        return round_score(sum(self.points_scored))

    @property
    def all_points(self) -> List[decimal.Decimal]:
        """
        Provides the total list of all points used
        """
        return self.points_scored + self.points_excluded


@dataclass
class SkipperRank:
    """
    Provides a class to maintain the skipper rank
    """

    # Raw Rank (not tie-broken)
    rank: int

    # Tie-Broken Rank
    rank_tie_broken: Optional[int]

    def __lt__(self, other) -> bool:
        if not isinstance(other, SkipperRank):
            raise RuntimeError("cannot compare non-skipperrank objects")

        if self.rank != other.rank:
            return self.rank < other.rank
        elif self.rank_tie_broken is not None and other.rank_tie_broken is not None:
            return self.rank_tie_broken < other.rank_tie_broken
        else:
            raise RuntimeError(
                "cannot compare one tie-broken rank to a non-tie-broken rank like this"
            )


class Series:
    """
    Defines a series of series, defined by a fleet type and a list of series
    """

    def __init__(
        self,
        name: str,
        valid_required_skippers: int,
        fleet: Fleet,
        qualify_count_override: Union[int, None] = None,
        exclude_from_statistics: bool = False,
    ):
        """
        Initializes the series with the input parameters
        :param name: The unique name of the series
        :param valid_required_skippers: The number of racers needed to indicate a valid race
        :param fleet: The fleet object to be used to define the corrected scoring parameters
        :param qualify_count_override: The number of series required to qualify for a scoring place
        :param exclude_from_statistics: True if this is a series that is automatically generated and should be excluded from statistics
        """
        # Save input values and setup for races
        self.name = name
        self._qualify_count_override = qualify_count_override
        self.valid_required_skippers = valid_required_skippers
        self.fleet = fleet
        self.races: List[Race] = list()
        self.boat_dict: Dict[Skipper, BoatType] = dict()
        self.exclude_from_statistics = exclude_from_statistics
        self.race_order: Dict[str, int] = dict()

        # Define memoization parameters
        self.__skipper_rc_pts = None
        self.__skippers: Optional[List[Skipper]] = None
        self.__points: Optional[Dict[Skipper, List[Union[float, int]]]] = None
        self.__ranks: Optional[Dict[Skipper, SkipperRank]] = None
        self.__plot_series_rank_history: Optional[bytes] = None
        self.__plot_series_point_history: Optional[bytes] = None

    def reset(self) -> None:
        """
        Resets any stored calculated parameters and sets the race index parameter for each race
        """
        # Clear all memoization parameters
        self.__skipper_rc_pts = None
        self.__skippers = None
        self.__points = None
        self.__ranks = None
        self.__ranks_tie_broken = None
        self.__plot_series_rank_history = None
        self.__plot_series_point_history = None

        # Clear the race counter and reset all races
        for i, r in enumerate(self.races):
            r.reset()

    def latest_race_date(self) -> Optional[datetime.datetime]:
        """
        Provides the latest race date for the given series
        :return: datetime for the latest race
        """
        latest = None
        for r in self.races:
            if latest is None or latest < r.date:
                latest = r.date
        return latest

    def add_skipper_boat(self, skipper: Skipper, boat: BoatType) -> None:
        """
        Adds the given skipper and boat to the race boat dictionary
        :param skipper: the skipper to add to the database
        :param boat: the boat to associate by default for the skipper
        """
        if skipper not in self.boat_dict:
            self.boat_dict[skipper] = boat
        else:
            raise ValueError(
                "Cannot add duplicate boat entry to series {:s} for {:s}".format(
                    self.name, skipper.identifier
                )
            )

    def skipper_num_finished(self, skipper: Skipper) -> int:
        """
        :param skipper: the skipper to check
        :return: the number of series that a skipper has finished with a time
        """
        count = 0
        for r in self.races:
            if skipper in r._race_finishes:
                res = r._race_finishes[skipper]
                if res.finished() and not isinstance(res, finishes.RaceFinishRC):
                    count += 1
        return count

    def skipper_num_rc(self, skipper: Skipper) -> int:
        """
        :param skipper: the skipper to check
        :return: the number of series that a skipper has been RC for
        """
        count = 0
        for r in self.races:
            if skipper in r._race_finishes:
                res = r._race_finishes[skipper]
                if isinstance(res, finishes.RaceFinishRC):
                    count += 1
        return count

    def skipper_num_dnf(self, skipper: Skipper) -> int:
        """
        :param skipper: the skipper to check
        :return: the number of races that a skipper has had a DNF finish
        """
        count = 0
        for r in self.races:
            if skipper in r._race_finishes:
                res = r._race_finishes[skipper]
                if isinstance(res, finishes.RaceFinishDNF):
                    count += 1
        return count

    def skipper_qualifies(self, skipper: Skipper) -> bool:
        """
        Returns whether a skipper has met the qualification count for the series
        :param skipper: The skipper to check
        :return: True if the skipper qualifies, False otherwise
        """
        # Initialize a count for valid series and valid RC (RC may only be counted twice for qualification)
        count = self.skipper_num_finished(skipper)
        count_rc = self.skipper_num_rc(skipper)
        count_dnf = self.skipper_num_dnf(skipper)

        # Return true if the count meets the qualify-count threshold
        return count + min(2, count_rc) + count_dnf >= self.qualify_count

    def get_skipper_rc_points(self, skipper: Skipper) -> Optional[decimal.Decimal]:
        """
        Returns the number of points associated with RC for a given Skipper
        :param skipper: skipper to get RC points for
        :return: Number of points estimated for RC series for a given Skipper
        """
        if self.__skipper_rc_pts is None:
            # Initialize the dictionary
            self.__skipper_rc_pts = dict()

            # Calculate RC point parameters
            for skip in self.get_all_skippers():
                # Obtain the series for the skipper
                skipper_races = [
                    r for r in self.valid_races() if skip in r._race_finishes
                ]

                # Obtain the results from each of the finished series and sort
                point_values = [
                    r.get_skipper_race_points()[skip]
                    for r in skipper_races
                    if skip in r.get_skipper_race_points()
                ]
                point_values = [p for p in point_values if p is not None]
                point_values.sort()

                # Set the score to None if no point values
                if len(point_values) == 0:
                    score = None
                else:
                    # Pop the highest value if we have more than one entry
                    if len(point_values) > 1:
                        point_values.pop()

                    # Calculate the score
                    score = round_score(
                        decimal.Decimal(sum(point_values) / len(point_values))
                    )

                # Set the results
                self.__skipper_rc_pts[skip] = score

        # Return points if the skipper is in the list
        if skipper in self.__skipper_rc_pts:
            return self.__skipper_rc_pts[skipper]
        else:
            return None

    def skipper_points_list(self, skipper: Skipper) -> Optional[ScoreList]:
        """
        Returns the points used to calculate the resulting score for a series
        :param skipper: the skipper identifier to search for
        :return: list of points equal to the qualification, or DNQ if no qualification
        """
        if self.__points is None:
            # Initialize the dictionary
            points = dict()

            # Calculate for all skippers
            for skip in self.get_all_skippers():
                # Obtain the results for a given skipper for all series
                points_list = list()

                # Ignore if the skipper doesn't qualify
                if not self.skipper_qualifies(skip):
                    continue

                # Define the number of RC points added
                rc_points_added_count = 0

                # Determine the maximum RC performed on a day
                rc_max_count = 2

                # Iterate over each race
                for r in [r for r in self.races if r.valid_for_rc(skip)]:
                    # Obtain the results
                    results = r.get_skipper_race_points()

                    value_to_add = None

                    # Define flags
                    can_add_rc = skip in r._race_finishes and isinstance(
                        r._race_finishes[skip], finishes.RaceFinishRC
                    )
                    can_add_rc = can_add_rc and rc_points_added_count < rc_max_count

                    # Add the results to the list if the skipper has a result
                    if skip in results:
                        value_to_add = results[skip]
                    elif can_add_rc:
                        value_to_add = self.get_skipper_rc_points(skip)
                        rc_points_added_count += 1

                    if value_to_add is not None:
                        points_list.append(value_to_add)

                # Add the sum of the lowest to qualify
                points_list.sort()

                if len(points_list) > 0:
                    points[skip] = ScoreList(
                        points_scored=points_list[: self.qualify_count],
                        points_excluded=points_list[self.qualify_count :],
                    )

            # Append the result to the static variable
            self.__points = points

        # Return the pre-calculated result
        return self.__points.get(skipper, None)

    def skipper_points_string(self, skipper: Skipper) -> str:
        """
        Provides the resulting points list string for a given skipper
        :param skipper: the skipper to get the points for
        :return: comma-separated list of points used in the scoring process
        """
        point_list = self.skipper_points_list(skipper=skipper)
        if point_list is None:
            return ""
        else:
            str_a = ", ".join([f"{v}" for v in point_list.points_scored])
            str_b = ", ".join([f"{v}" for v in point_list.points_excluded])

            if len(str_b) > 0:
                return f"{str_a} ({str_b})"
            else:
                return str_a

    def add_race(self, race: Race) -> None:
        """
        Adds a race to the race list. Races are in the order they are added to the list
        :param race: The race object to add
        """
        if race.name in self.race_order:
            raise ValueError(f"Duplicate race with name {race.name} provided!")

        self.race_order[race.name] = len(self.races)
        self.races.append(race)
        self.reset()

    def get_race_num(self, race: Race) -> int:
        """
        Gets the race index for the given race in the series as a 1-index value
        """
        if race.name in self.race_order:
            return self.race_order[race.name] + 1
        else:
            raise RuntimeError(f"No race with {race.name} found in series {self.name}")

    @property
    def qualify_count(self) -> int:
        """
        Provides the qualification count for the series
        """
        if self._qualify_count_override is not None:
            return self._qualify_count_override
        else:
            qualify_count = int(math.ceil(len(self.valid_races()) / 2))
            return qualify_count

    def valid_races(self) -> List[Race]:
        """
        Returns the number of valid series held
        :return: count of valid series
        """
        return [r for r in self.races if r.valid()]

    def get_all_skippers(self) -> List[Skipper]:
        """
        Provides all skippers in the series
        :return: list of unique skipper objects between all series
        """
        if self.__skippers is None:
            # Define the output list
            skippers = list()

            # Check each race for skippers
            all_skipper_instances = [
                rt.skipper for r in self.races for rt in r._race_finishes.values()
            ]

            # Iterate over all skipper values
            for s in all_skipper_instances:
                if s not in skippers:
                    skippers.append(s)

            # Save the resulting skipper dictionary
            self.__skippers = skippers

        # Return the skipper list
        return self.__skippers

    def __inner_get_skipper_rank(self, skipper: Skipper) -> Optional[SkipperRank]:
        """
        Provides the rank in the series for the given skipper
        :param skipper: the skipper to check
        :return: the resulting rank, or None if skipper does not qualify
        """
        # Construct the ranks if needed
        if self.__ranks is None:
            self.__ranks = dict()
            last_rank = None
            last_score = None
            for i, skip_point in enumerate(self.get_all_skippers_sorted()):
                pts = self.skipper_points_list(skipper=skip_point)
                if pts is not None:
                    current_rank = i + 1
                    if pts.score != last_score:
                        r = current_rank
                    else:
                        r = last_rank

                    self.__ranks[skip_point] = SkipperRank(r, current_rank)
                    last_rank = r
                    last_score = pts.score

            for val in set([val.rank for val in self.__ranks.values()]):
                # Determine the count for the current value
                cnt = len([None for v in self.__ranks.values() if val == v.rank])

                # If the count is not large enough, set all the second values to 1
                if cnt <= 1:
                    for key, key_val in self.__ranks.items():
                        if key_val.rank == val:
                            self.__ranks[key].rank_tie_broken = None

        # Return the resulting rank
        return self.__ranks.get(skipper, None)

    def get_skipper_rank(self, skipper: Skipper) -> Optional[SkipperRank]:
        """
        Provides the rank in the series for the given skipper
        :param skipper: the skipper to check
        :return: the resulting rank, or None if skipper does not qualify
        """
        # Return the resulting rank
        rnk = self.__inner_get_skipper_rank(skipper=skipper)
        if rnk:
            return rnk
        else:
            return None

    def get_all_skippers_sorted(self) -> List[Skipper]:
        """
        Provides all skippers in the series, sorted first by points, and then by alphabet
        :return: list of unique skipper objects between all series, sorted
        """

        # Define a helper dataclass
        @dataclass
        class SkipperMap:
            skipper: Skipper
            result: Optional[ScoreList]

        # Iterate over scores that are the same
        score_mapping: List[SkipperMap] = list()
        for s in self.get_all_skippers():
            sm = SkipperMap(skipper=s, result=self.skipper_points_list(s))

            score_mapping.append(sm)

        # Define an insertion-sort method
        def compare_results(a: SkipperMap, b: SkipperMap) -> bool:
            # Return true if a has a lower score
            if a.result is not None and b.result is not None:
                # Determine based on rules
                if a.result.score == b.result.score:
                    # Find first entry where A is less than B
                    for ap, bp in zip(a.result.points_scored, b.result.points_scored):
                        if ap != bp:
                            return ap < bp

                    # Otherwise, look at the latest race results
                    for race in reversed(self.races):
                        res = race.get_skipper_race_points()
                        if a.skipper in res and b.skipper in res:
                            a_res = res[a.skipper]
                            b_res = res[b.skipper]

                            if a_res != b_res:
                                return a_res < b_res

                # Just return the lower score
                else:
                    return a.result.score < b.result.score

            if a.result is None and b.result is None:
                # Check for lower RC score
                # Otherwise, use alphabetic

                a_rc = self.get_skipper_rc_points(a.skipper)
                b_rc = self.get_skipper_rc_points(b.skipper)

                if a_rc is not None and b_rc is not None:
                    if a_rc != b_rc:
                        return a_rc < b_rc
                elif a_rc is not None and b_rc is None:
                    return True
                elif a_rc is None and b_rc is not None:
                    return False

            # Return based on result values
            elif a.result is not None and b.result is None:
                return True
            elif a.result is None and b.result is not None:
                return False

            # Use the fallthrough using the skipper
            return a.skipper.identifier < b.skipper.identifier

        # Define the skipper list
        skippers = list()

        # Add the first entry
        if score_mapping:
            skippers.append(score_mapping.pop())

        # Insertion-sort remaining
        while score_mapping:
            s = score_mapping.pop()

            added = False
            i = 0

            while i < len(skippers):
                if compare_results(s, skippers[i]):
                    skippers.insert(i, s)
                    added = True
                    break
                else:
                    i += 1

            if not added:
                skippers.append(s)

        # Return the result
        return [s.skipper for s in skippers]

    def get_plot_normalized_race_time_results(self) -> bytes:
        """
        Provides a plot of the fraction of corrected / minimum race time as a function of race score
        :return: An encoded base64 HTML source string of figure, empty on failure
        """
        plt.ioff()
        f = plt.figure()

        for race in self.valid_races():
            # Define the result list for the scatter plot
            results_list = list()

            # Add each valid item to the scatter plot
            for skipper, score in race.get_skipper_race_points().items():
                rt = race._race_finishes[skipper]
                if isinstance(rt, finishes.RaceFinishTime):
                    results_list.append(
                        (score, rt.corrected_time_s / race.min_time_s())
                    )

            # Sort the values
            results_list.sort(key=lambda x: x[0])

            # Plot results
            plt.plot([x[0] for x in results_list], [y[1] for y in results_list], "o--")

        s = f.get_size_inches()
        f.set_size_inches(w=1.15 * s[0], h=s[1])

        # Assign the legend and axes labels
        plt.legend(
            ["Race {:d}".format(self.get_race_num(r)) for r in self.valid_races()],
            loc="upper left",
            bbox_to_anchor=(1.04, 1),
            borderaxespad=0,
        )
        plt.xlabel("Score [points]")
        plt.ylabel("Normalized Finish Time [corrected / shortest]")
        plt.tight_layout(rect=(0, 0, 1, 1))

        # Encode the image
        img_val = figure_to_data(f)
        plt.close(f)
        return img_val

    def get_plot_boat_pie_chart(self) -> bytes:
        """
        Provides a pie chart for the count for each existing boat in the series
        :return: An encoded base64 HTML source string of figure, empty on failure
        """
        # Obtain the boats for each skipper
        skip_boat_dict = dict()
        boat_type_dict = dict()

        # Iterate over each race to calculate the boat result
        for r in self.races:
            for rt in r._race_finishes.values():
                if (
                    rt.skipper not in skip_boat_dict
                    and rt.skipper in r.get_skipper_race_points()
                ):
                    skip_boat_dict[rt.skipper] = rt.boat.code

                    if rt.boat.code not in boat_type_dict:
                        boat_type_dict[rt.boat.code] = 0
                    boat_type_dict[rt.boat.code] += 1

        # Combine values together
        combined = [(key, val) for key, val in boat_type_dict.items()]
        labels = [v[0].upper() for v in combined]
        sizes = [v[1] for v in combined]

        def percent2count(percent):
            return "{:1.0f}".format(round(percent / 100.0 * sum(sizes)))

        # Plot
        plt.ioff()
        f = plt.figure()
        ax = plt.gca()
        ax.pie(
            sizes,
            labels=labels,
            autopct=percent2count,
            explode=[0.03 for _ in combined],
        )
        ax.axis("equal")

        # Save resulting image and close
        byte_val = figure_to_data(f)
        plt.close(f)
        return byte_val

    def _setup_point_rank_plots(self) -> None:
        """
        Generates the plots for the rank and point histories
        :return:
        """
        # Initialize plotting requirements
        img_types = ["Rank", "Points"]
        img_vals = [bytes() for _ in range(len(img_types))]

        # Plot if able
        if plt is not None:
            # Define the skipper list
            skipper_db = {
                skipper: (list(), list())
                for skipper in self.get_all_skippers()
                if self.skipper_points_list(skipper) is not None
            }

            # Create an inner series object to track point values
            series = Series(
                name=self.name,
                valid_required_skippers=self.valid_required_skippers,
                fleet=self.fleet,
                qualify_count_override=self._qualify_count_override,
            )

            # Iterate over each race to return a list of point values
            race_vals = list()
            for i, race in enumerate(self.races):
                series.add_race(race)

                if len(series.valid_races()) == 0:
                    continue

                race_vals.append(i + 1)
                for skipper, (list_val_rank, list_val_points) in skipper_db.items():
                    r = series.get_skipper_rank(skipper)
                    list_val_rank.append(r.rank if r is not None else None)

                    sp = series.skipper_points_list(skipper)
                    list_val_points.append(sp.score if sp else None)

            # Iterate for each figure
            for i in range(len(img_vals)):
                # Define the figure
                plt.ioff()
                f = plt.figure()

                # Ignore none skippers
                finished_skippers = {
                    s: self.get_skipper_rank(skipper=s) for s in skipper_db.keys()
                }
                finished_skippers = {
                    s: r for s, r in finished_skippers.items() if r is not None
                }

                # Plot each skipper that has finished
                for skipper, _ in sorted(finished_skippers.items(), key=lambda x: x[1]):
                    plt.plot(
                        race_vals,
                        skipper_db[skipper][i],
                        "*--",
                        label=skipper.identifier,
                    )

                # Label the plot
                plt.xlabel("Race Number")
                plt.ylabel(f"Skipper {img_types[i]}")
                plt.legend(bbox_to_anchor=(1.04, 1), borderaxespad=0)
                plt.tight_layout(rect=(0, 0, 1, 1))

                # Save results
                img_vals[i] = figure_to_data(f)

                # Close figure
                plt.close(f)

        # Compress and save the result
        self.__plot_series_rank_history = img_vals[0]
        self.__plot_series_point_history = img_vals[1]

    def get_plot_series_points(self) -> bytes:
        """
        Provides a plot of skipper point values over time for those that qualified at the end of the series
        :return: the resulting figure
        """
        if self.__plot_series_point_history is None:
            self._setup_point_rank_plots()

        if self.__plot_series_point_history is None:
            raise RuntimeError("no plot series point history provided")

        return self.__plot_series_point_history

    def get_plot_series_rank(self) -> bytes:
        """
        Provides a plot of skipper rank values over time for those that qualified at the end of the series
        :return: the resulting figure
        """
        if self.__plot_series_rank_history is None:
            self._setup_point_rank_plots()

        if self.__plot_series_rank_history is None:
            raise RuntimeError("no plot series rank history provided")

        return self.__plot_series_rank_history

    def fancy_name(self) -> str:
        """
        Provides the fancy name, removing underscores for spaces and capitalizing
        :return: fancy name string
        """
        return capitalize_words(self.name.replace("_", " "))

    def perl_series_dict(self) -> dict:
        """
        Provides the YAML series dictionary result
        """
        race_dict = dict()

        for i, r in enumerate(self.races):
            if r.wind_bf is not None:
                wind_map_node = r.fleet.wind_map.get_wind_map_for_beaufort(r.wind_bf)
                if wind_map_node.start_bf != wind_map_node.end_bf:
                    wind_val = f"{wind_map_node.start_bf}-{wind_map_node.end_bf}"
                else:
                    wind_val = f"{wind_map_node.start_bf}"
            else:
                wind_val = "?"

            race_dict[i + 1] = {
                "wind": wind_val,
                "date": r.date.strftime("%Y_%m_%d"),
                "RC": [s.identifier for s in r.rc_skippers()],
                "notes": r.notes,
                "skip": {
                    rr.skipper.identifier: {
                        "time": rr.perl_entry(),
                        "boat": rr.boat.code.upper(),
                    }
                    for rr in r.starting_boat_results()
                },
            }

        return {"race": race_dict}

    def perl_boat_dict(self) -> dict:
        """
        Provides the YAML boat dictionary result
        """
        # Create the boat dict
        boat_dict = dict()
        for r in self.races:
            for b in r.boat_dict.values():
                if b.display_code not in boat_dict:
                    # Ensure that a default value is provided
                    if b.dpn_values[0] is None:
                        raise RuntimeError(
                            f"unable to find default DPN for {b.display_code} boat"
                        )

                    # Setup the boat dictionary
                    boat_dict[b.display_code] = {
                        "full_name": b.name,
                        "hc_of_wind": {
                            "D-PN": b.dpn_values[0].value(),
                            "0-1": b.dpn_for_beaufort(0).value(),
                            "2-3": b.dpn_for_beaufort(2).value(),
                            "4": b.dpn_for_beaufort(4).value(),
                            "5-9": b.dpn_for_beaufort(5).value(),
                        },
                    }

        # Return the result
        return {"boat": boat_dict}

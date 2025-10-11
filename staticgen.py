"""
Core function for the static site generator
Similar in functionality to a basic Flask application
"""

import copy
import datetime
from pathlib import Path
from os import PathLike
import re
import shutil
from typing import Any, Callable, Dict, List, Tuple, Optional, Union, Generator

import jinja2


class StaticApplication:
    """
    Defines a static application with a Flask-like interface
    """

    # Define the regular expressions to check for

    # Check for a specific variable value
    __VAR_CHECK = re.compile(r"<(?P<type>[a-zA-z]+):(?P<varname>[\w_]+(/[\w_]+)*)>")

    # Check for an overall path for validity
    __PATH_CHECK = re.compile(
        r"^/(([\w_]+|<[a-zA-z]+:[\w_]+(/[\w_]+)*>)|/?)*([\w_]*\.\w+)$"
    )

    # Check for a file extension
    __EXT_CHECK = re.compile(r"\.\w+$")

    def __init__(
        self,
        name: str = "",
        base_path: Path | None = None,
        build_path: Path | None = None,
    ):
        """
        Initializes the static application with the provided values
        :param name: the name of the application
        :param base_path: the base path of the application
        """
        # Store input parameters
        self.name = name
        if base_path is None:
            base_path = Path(__file__).parent

        # Define maps to link path values to variable names and rendering functions
        self.path_name_map: dict[str, Callable[[Any], None | str | bytes]] = dict()
        self.path_function_map: dict[str, str] = dict()

        # Define path parameters
        self.base_path: Path = base_path.absolute()
        self.build_path: Path = (
            base_path / "build" if build_path is None else build_path
        )
        self.static_path: Path = base_path / "static"

        # Define the Jinja2 environment
        self.jinja_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(base_path / "templates")
        )
        self.jinja_env.globals.update(url_for=self.url_for)
        self.jinja_env.globals.update(get_build_time=self.get_build_time)

        # Define the iterable lists used to generate pages
        self.iter_lists = dict()

        # Define the build start time
        dt = datetime.datetime.utcnow()
        self.build_time_string = dt.strftime("%B %d, %Y at %H:%M:%S UTC")

        # Define a function to help with relative URL parameters
        self._url_for_relative: Optional[Path] = None

    def add_list(self, name: str, values: list | dict) -> None:
        """
        Adds an iterable item to the list
        :param name: the variable name of the item
        :param values: the values, either as a list or a dictionary, to use
        """
        if isinstance(values, list):
            self.iter_lists[name] = list(values)
        elif isinstance(values, dict):
            self.iter_lists[name] = {k: list(v) for k, v in values.items()}
        else:
            raise TypeError("unknown input type for values detected")

    def get_build_time(self) -> str:
        """
        Provides the build time for the site
        :return: the resulting timestring
        """
        return self.build_time_string

    def route(self, path: str) -> Callable:
        """
        Provides a decorator function to define an application route
        :param path: is the path root to associate
        :return: the resulting function
        """
        # Ensure that the path is valid
        if not self.__PATH_CHECK.match(path):
            raise ValueError(f"Path '{path}' is invalid as provided")

        # Define the decorator function
        def decorator(func: Callable) -> Callable:
            # Save the function and link the name to the path
            self.path_name_map[path] = func
            self.path_function_map[func.__name__] = path

            # Return the original function
            return func

        # Return the decorator
        return decorator

    def _iter_generator(
        self, path: str, vars_so_far: Optional[Dict[str, Union[str, int]]]
    ) -> Generator[Tuple[str, Dict[str, Union[str, int]]], None, None]:
        """
        Define an iterable generator to link through the iterable lists for a given path and provide the paths that
        need to be rendered
        :param path: the base path to use
        :param vars_so_far: the variable values found thus far
        :return: a generator returning the path string and dictionary with the variable values used
        """
        # Search for any variables present in the input path
        search_result = self.__VAR_CHECK.search(path)
        if search_result:
            # Ensure that the expected values are contained within
            if (
                "type" not in search_result.groupdict()
                or "varname" not in search_result.groupdict()
            ):
                raise RuntimeError(
                    "missing types or varname within replacement path string"
                )

            # Extract the types and define the expected type value
            type_str = search_result.groupdict()["type"].lower()
            var_str = search_result.groupdict()["varname"]

            # Pull the value list for the current results
            if "/" in var_str:
                actual_varname = var_str[var_str.rfind("/") + 1 :]
                value_list = self.iter_lists[actual_varname]
                while "/" in var_str:
                    init_varname = var_str[: var_str.find("/")]
                    if vars_so_far is None:
                        raise RuntimeError("variable cannot be None")
                    value_list = value_list[vars_so_far[init_varname]]
                    var_str = var_str[var_str.find("/") + 1 :]
            else:
                value_list = self.iter_lists[var_str]

            # Iterate over each of the values in the provided list
            for value in value_list:
                # Check the type string values
                if type_str == "string":
                    value_str = value
                elif type_str == "int":
                    value_str = f"{value:d}"
                else:
                    raise ValueError(f"unknown type string '{type_str}' provided")

                # Perform the replacement value
                path_chars = list(path)
                path_chars[search_result.start() : search_result.end()] = list(
                    value_str
                )
                new_path_str = "".join(path_chars)

                # Define the variable list
                if vars_so_far is None:
                    vars_so_far_iter = dict()
                else:
                    vars_so_far_iter = copy.deepcopy(vars_so_far)

                # Add the current value to the dictionary
                if var_str in vars_so_far_iter:
                    raise ValueError(f"duplicate key for {var_str} provided")
                else:
                    vars_so_far_iter[var_str] = value

                # Iterate any further replacement values
                for result in self._iter_generator(
                    path=new_path_str, vars_so_far=vars_so_far_iter
                ):
                    yield result
        else:
            # Generate a new variable list
            if vars_so_far is None:
                vars_so_far = dict()
            else:
                vars_so_far = copy.deepcopy(vars_so_far)

            # Return the result
            yield path, vars_so_far

    def render_template(self, template_name: str, **kwargs) -> str:
        """
        Renders the input template with the provided arguments
        :param template_name: is the template name to use
        :param kwargs: the arguments to render the template with
        :return: the resulting rendered string
        """
        template = self.jinja_env.get_template(template_name)
        return template.render(**kwargs)

    def url_for(self, function_name: str, **kwargs) -> str:
        """
        Provides the URL for a given path. If the _url_for_relative is set, the returned URL will be relative
        :param function_name: is the function name ot find a path for, or 'static'
        :param kwargs: the arguments to call the function with
        :return:
        """
        # Return a static parameter
        if function_name == "static":
            filename = kwargs["filename"]
            if len(filename) > 0 and filename[0] != "/":
                filename = f"/{filename}"
            url_val = filename

        # Return a dynamic parameter
        else:
            # Obtain the path from the path list
            url_val = self.path_function_map[function_name]

            # Attempt to replace all variable values as necessary
            search_result = self.__VAR_CHECK.search(url_val)
            while search_result:
                # Extract search results
                var_type = search_result.groupdict()["type"]
                var_name = search_result.groupdict()["varname"]

                # Find the true variable name
                if "/" in var_name:
                    var_name = var_name[var_name.rfind("/") + 1 :]

                # Define the resulting variable value from the kwargs
                if var_type == "string":
                    var_value = kwargs[var_name]
                elif var_type == "int":
                    var_value = f"{kwargs[var_name]:d}"
                else:
                    raise NotImplementedError()

                # Replace the search values with the resulting strings
                url_val = list(url_val)
                url_val[search_result.start() : search_result.end()] = var_value
                url_val = "".join(url_val)

                # Re-update the search results
                search_result = self.__VAR_CHECK.search(url_val)

        # Update the URL val to ensure validity
        url_val = self._update_url(url_val)

        # Determine the relative path if needed
        if self._url_for_relative is not None:
            # Define initialization variables
            num_path_ups = 0
            url_path = self._url_for_relative

            # Determine the number of times that the directory will need to be moved up to find a valid relative path
            while True:
                try:
                    path_result = Path(url_val).parent.relative_to(url_path)
                    break
                except ValueError:
                    num_path_ups += 1
                    url_path = url_path.parent

            # Add the resulting directory tree movements
            for i in range(num_path_ups):
                path_result = Path("..") / path_result

            # Add the resulting filename and convert to a POSIX path to return
            path_result /= Path(url_val).name
            url_val = path_result.as_posix()

        # Return the resulting URL value
        return url_val

    @staticmethod
    def _update_url(url: str) -> str:
        """
        Ensures validity of the provided URL
        :param url: the URL to check
        :return: the updated URL
        """
        # Check file name validity
        if len(url) == 0:
            raise ValueError("path cannot be empty")
        elif url[0] != "/":
            raise ValueError("path must start with /")

        # Return the result
        return url

    def send_from_directory(
        self,
        directory: Union[str, PathLike],
        path: Union[str, PathLike],
        binary: bool = False,
    ) -> Union[bytes, str]:
        """
        Provides a resulting name from the given directory
        :param directory: the directory name (often static)
        :param path: the file path within the directory
        :param binary: whether the file should be loaded as a binary or as text
        :return: the resulting data values
        """
        target_file = self.base_path / directory / path
        if not target_file.exists():
            raise RuntimeError(f"target file {str(target_file)} does not exist")

        with target_file.open("rb" if binary else "r") as f:
            return f.read()

    def __clear_build_dir(self) -> None:
        """
        Creates the build directory if necessary and clears out
        and existing files or directories in the build path
        """
        if self.build_path.exists():
            for path in self.build_path.glob("*"):
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        else:
            self.build_path.mkdir(exist_ok=False, parents=False)

    def build(self) -> None:
        """
        Performs the actual build of the HTML site into the specified build directory
        """
        # Clear out the build directory
        self.__clear_build_dir()

        # Iterate over each path value type
        for path, func in self.path_name_map.items():
            for path_val, kwargs in self._iter_generator(path=path, vars_so_far=None):
                # Define the file name for the file
                file_name = self._update_url(path_val)

                # Remove the initial '/' from the file_name
                file_name = file_name[1:]

                # Define the resulting path
                resulting_path = self.build_path.absolute() / file_name

                # Generate any required parent paths
                path_so_far = self.build_path.absolute()
                for part in Path(file_name).parts[:-1]:
                    path_so_far /= part
                    if not path_so_far.exists():
                        path_so_far.mkdir(parents=False, exist_ok=False)

                # Run the function to get the resulting data
                self._url_for_relative = Path(path_val).parent
                parse_result = func(*[], **kwargs)
                self._url_for_relative = None

                # Write either the string or bytes to the output
                if isinstance(parse_result, str):
                    with resulting_path.open("w") as f:
                        f.write(parse_result)
                elif isinstance(parse_result, bytes):
                    with resulting_path.open("wb") as f:
                        f.write(parse_result)
                elif parse_result is None:
                    pass
                else:
                    raise ValueError("unexpected file result provided")

                # Write the result
                if parse_result is not None:
                    print(f"Wrote {str(resulting_path)}")
                else:
                    print(f"Skipping {str(resulting_path)}")

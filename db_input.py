"""
Provide a common reference for obtaining the database
"""

from database.db import MasterDatabase

from pathlib import Path


def get_database() -> MasterDatabase:
    database = MasterDatabase(
        config_file=Path(__file__).parent / "input" / "config.yaml"
    )
    database.trim_fleets_lists()
    return database

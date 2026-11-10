# Beancount 3.2.0 beancount/scripts/example.py (`bean-example`): the name the parity corpus uses (S0094).
import datetime
from typing import TextIO

def write_example_file(
    date_birth: datetime.date, date_begin: datetime.date, date_end: datetime.date, reformat: bool, file: TextIO
) -> None: ...

from datetime import date, datetime
from typing import Optional

from app.exceptions import APIError
from app import error_codes as ec


def parse_date_param(date_str: str, param_name: str) -> date:
    """
    Parse a YYYY-MM-DD date string, raising APIError on invalid format.

    Args:
        date_str: Date string to parse.
        param_name: Name of the parameter (used in error messages).

    Returns:
        Parsed date object.

    Raises:
        APIError: If the date string is not valid YYYY-MM-DD.
    """
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise APIError(
            message=f"Invalid {param_name} format",
            code=ec.VALIDATION_ERROR,
            status_code=422,
            details={
                "field": param_name,
                "value": date_str,
                "help": "Date must be in YYYY-MM-DD format",
            },
        )


def parse_optional_date_param(date_str: Optional[str], param_name: str) -> Optional[date]:
    """Parse a date string if non-None, otherwise return None."""
    if date_str is None:
        return None
    return parse_date_param(date_str, param_name)

from pydantic import BaseModel, Field
from typing import Optional, List

from app.schemas.csv_schemas import CsvColumnMapping, InvalidRuleSummary


class XlsRule(BaseModel):
    """Full XLS import rule definition."""
    name: str = Field(..., description="Human-readable rule name")
    sheet_index: int = Field(default=0, description="0-based sheet index to read from")
    sheet_name: Optional[str] = Field(default=None, description="Sheet name to read from (overrides sheet_index if provided)")
    skip_lines_start: int = Field(default=0, description="Number of rows to skip at the start (including header row)")
    skip_lines_end: int = Field(default=0, description="Number of rows to skip at the end")
    date_format: str = Field(default="%Y-%m-%d", description="Date format string (strftime tokens), used when date cell is a string")
    decimal_separator: str = Field(default=".", description="Decimal separator character for string-valued amount cells")
    columns: CsvColumnMapping = Field(..., description="Column index mappings (same as CSV rules)")
    default_account: str = Field(..., description="Default Beancount account for this source")
    default_currency: str = Field(default="USD", description="Default currency")
    negate_amounts: bool = Field(default=False, description="Whether to negate parsed amounts")


class XlsRuleSummary(BaseModel):
    """Lightweight listing model for XLS rules."""
    filename: str = Field(..., description="YAML filename of the rule")
    name: str = Field(..., description="Human-readable rule name")
    default_account: str = Field(..., description="Default Beancount account")
    default_currency: str = Field(default="USD", description="Default currency")


class XlsRuleListData(BaseModel):
    """Response wrapper for XLS rules listing."""
    rules: List[XlsRuleSummary] = Field(default_factory=list, description="Available XLS rules")
    invalid_rules: List[InvalidRuleSummary] = Field(default_factory=list, description="Rule files that failed to load")
    rules_dir: Optional[str] = Field(default=None, description="Path to the rules directory")

from pydantic import BaseModel, Field, model_validator
from typing import Optional, List


class CsvColumnMapping(BaseModel):
    """Column index mapping for CSV files (0-based indices).

    Use either 'amount' for a single amount column, or both 'amount_debit'
    and 'amount_credit' for banks that use separate DR/CR columns.
    """
    date: int = Field(..., description="Column index for transaction date")
    amount: Optional[int] = Field(default=None, description="Column index for transaction amount (single column)")
    amount_debit: Optional[int] = Field(default=None, description="Column index for debit amounts (money out)")
    amount_credit: Optional[int] = Field(default=None, description="Column index for credit amounts (money in)")
    payee: Optional[int] = Field(default=None, description="Column index for payee name")
    narration: Optional[int] = Field(default=None, description="Column index for narration/description")
    memo: Optional[int] = Field(default=None, description="Column index for memo/reference")

    @model_validator(mode='after')
    def validate_amount_columns(self) -> 'CsvColumnMapping':
        has_single = self.amount is not None
        has_split = self.amount_debit is not None and self.amount_credit is not None
        if not has_single and not has_split:
            raise ValueError("Either 'amount' or both 'amount_debit' and 'amount_credit' must be specified")
        if has_single and has_split:
            raise ValueError("Cannot specify both 'amount' and 'amount_debit'/'amount_credit'")
        return self


class CsvRule(BaseModel):
    """Full CSV import rule definition."""
    name: str = Field(..., description="Human-readable rule name")
    separator: str = Field(default=",", description="Column separator character")
    encoding: str = Field(default="utf-8", description="File encoding")
    skip_lines_start: int = Field(default=0, description="Number of lines to skip at the start (before header)")
    skip_lines_end: int = Field(default=0, description="Number of lines to skip at the end")
    date_format: str = Field(default="%Y-%m-%d", description="Date format string (strftime tokens)")
    decimal_separator: str = Field(default=".", description="Decimal separator character")
    columns: CsvColumnMapping = Field(..., description="Column index mappings")
    default_account: str = Field(..., description="Default Beancount account for this source")
    default_currency: str = Field(default="USD", description="Default currency")
    negate_amounts: bool = Field(default=False, description="Whether to negate parsed amounts")


class CsvRuleSummary(BaseModel):
    """Lightweight listing model for CSV rules."""
    filename: str = Field(..., description="YAML filename of the rule")
    name: str = Field(..., description="Human-readable rule name")
    default_account: str = Field(..., description="Default Beancount account")
    default_currency: str = Field(default="USD", description="Default currency")


class CsvRuleListData(BaseModel):
    """Response wrapper for CSV rules listing."""
    rules: List[CsvRuleSummary] = Field(default_factory=list, description="Available CSV rules")
    rules_dir: Optional[str] = Field(default=None, description="Path to the rules directory")

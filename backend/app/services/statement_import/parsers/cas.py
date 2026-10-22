"""CAMS / KFintech CAS (Indian mutual funds) → ``StatementData``.

The only module that imports ``casparser`` (MIT; dev-docs/cas-import.md §3). It
returns Finzytrack's own types and maps casparser's exceptions to error codes with
user-facing messages, so a parser bug, an API change or a swap to another parser
touches this file only.

The password is used to open the PDF and then dropped: never stored, never logged.
PAN, email, phone and address are read by casparser but never copied out of here.
"""

from __future__ import annotations

import io
import logging
import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional

from app import error_codes as ec
from app.services.statement_import.types import (
    StatementData, StmtAccount, StmtInvestor, StmtPosition, StmtRow, StmtSecurity, row_id,
)

logger = logging.getLogger(__name__)

SOURCE = "cas"


class StatementParseError(Exception):
    """A statement could not be read. ``code`` is an ``error_codes`` value."""

    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _clean_name(name: str) -> str:
    """Tidy a scheme name: collapse whitespace and drop a parenthesis the parser cut
    off at a line end ("… Growth (Non" → "… Growth")."""
    name = re.sub(r"\s+", " ", name or "").strip()
    if name.count("(") > name.count(")"):
        name = name[: name.rfind("(")].rstrip(" -")
    name = re.sub(r"\s*\(Non[- ]?Demat\)", "", name, flags=re.I)
    return name.strip(" -")


def _to_date(value) -> date:
    if isinstance(value, date):
        return value
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(value), fmt).date()
        except ValueError:
            continue
    raise StatementParseError(ec.CAS_PARSE_FAILED, f"Unreadable date in the statement: {value}")


def _dec(value) -> Optional[Decimal]:
    return Decimal(str(value)) if value is not None else None


def is_cas_pdf(content: bytes, password: Optional[str]) -> bool:
    """True when the PDF is a CAMS / KFintech / NSDL / CDSL consolidated account
    statement — for the AI import tab's hand-over (§3.1). Never raises: a PDF that
    can't be opened (wrong or missing password) is simply "not recognised"."""
    try:
        from casparser.enums import FileType
        from casparser.parsers.detect import _open_document, detect_file_type
        doc = _open_document(io.BytesIO(content), password or "")
        try:
            return detect_file_type(None, None, _doc=doc) != FileType.UNKNOWN
        finally:
            doc.close()
    except Exception:
        return False


def parse_cas_pdf(content: bytes, password: str) -> StatementData:
    """Parse a CAS PDF into ``StatementData``. Raises ``StatementParseError``."""
    try:
        import casparser
        from casparser.exceptions import (
            CASIntegrityError, CASParseError, HeaderParseError, IncorrectPasswordError, ParserException,
        )
    except ImportError as e:  # pragma: no cover — a packaging fault
        raise StatementParseError(ec.MISSING_DEPENDENCY, "The CAS reader is not installed.", 500) from e

    try:
        cas = casparser.read_cas_pdf(io.BytesIO(content), password or "")
    except IncorrectPasswordError as e:
        raise StatementParseError(
            ec.CAS_WRONG_PASSWORD,
            "Wrong password. Enter the password you chose when you requested the statement.",
        ) from e
    except (HeaderParseError, CASParseError) as e:
        raise StatementParseError(
            ec.CAS_UNRECOGNIZED, "This doesn't look like a CAS from CAMS or KFintech.",
        ) from e
    except CASIntegrityError as e:
        raise StatementParseError(
            ec.CAS_INTEGRITY,
            "The statement's transactions don't add up to its closing balances, so it can't be imported safely.",
        ) from e
    except ParserException as e:
        raise StatementParseError(ec.CAS_PARSE_FAILED, "The statement could not be read.") from e
    except Exception as e:
        logger.exception("Unexpected error while reading a CAS")
        raise StatementParseError(ec.CAS_PARSE_FAILED, "The statement could not be read.") from e

    if not hasattr(cas, "folios"):
        # NSDL / CDSL depository statements parse into a different shape (§10: later).
        raise StatementParseError(
            ec.CAS_UNRECOGNIZED,
            "This is a depository (NSDL/CDSL) statement. Only CAMS and KFintech mutual fund statements can be imported for now.",
        )
    return _to_statement(cas)


def _to_statement(cas) -> StatementData:
    """Map casparser's ``CASData`` onto the neutral types (also used by tests)."""
    variant = str(getattr(cas.cas_type, "value", cas.cas_type)).lower()
    issuer = str(getattr(cas.file_type, "value", cas.file_type))

    investors: List[StmtInvestor] = []
    investor_by_pan: Dict[str, str] = {}
    fallback_name = getattr(getattr(cas, "investor_info", None), "name", None)

    accounts: List[StmtAccount] = []
    securities: Dict[str, StmtSecurity] = {}
    positions: List[StmtPosition] = []
    rows: List[StmtRow] = []
    occurrences: Counter = Counter()
    index = 0

    for folio in cas.folios:
        pan_key = (folio.PAN or "").strip().upper() or f"folio:{folio.folio}"
        if pan_key not in investor_by_pan:
            key = f"I{len(investor_by_pan) + 1}"
            investor_by_pan[pan_key] = key
            investors.append(StmtInvestor(key=key, name=(folio.name or None)))
        inv = investor_by_pan[pan_key]
        folio_id = re.sub(r"\s+", "", str(folio.folio))
        institution = re.sub(r"\s+", " ", folio.amc or "").strip()
        accounts.append(StmtAccount(id=folio_id, institution=institution, investor=inv))

        for scheme in folio.schemes:
            sec_key = scheme.isin or f"{scheme.rta}:{scheme.rta_code}"
            if sec_key not in securities:
                securities[sec_key] = StmtSecurity(
                    key=sec_key,
                    name=_clean_name(scheme.scheme),
                    institution=institution,
                    isin=scheme.isin,
                    amfi=str(scheme.amfi) if scheme.amfi else None,
                    category=scheme.type,
                )
            val = scheme.valuation
            positions.append(StmtPosition(
                account_id=folio_id,
                security_key=sec_key,
                advisor=(scheme.advisor or "").strip() or None,
                open_units=_dec(scheme.open) or Decimal("0"),
                close_units=_dec(scheme.close) or Decimal("0"),
                close_calculated=_dec(scheme.close_calculated),
                valuation_date=_to_date(val.date) if val and val.date else None,
                valuation_nav=_dec(val.nav) if val else None,
                valuation_value=_dec(val.value) if val else None,
                valuation_cost=_dec(val.cost) if val else None,
            ))
            for t in scheme.transactions:
                when = _to_date(t.date)
                kind = str(getattr(t.type, "value", t.type))
                units, amount = _dec(t.units), _dec(t.amount)
                occ_key = (folio_id, sec_key, when, kind, units, amount)
                occurrences[occ_key] += 1
                rows.append(StmtRow(
                    id=row_id(SOURCE, folio_id, sec_key, when, kind, units, amount, occurrences[occ_key]),
                    index=index,
                    account_id=folio_id,
                    security_key=sec_key,
                    date=when,
                    kind=kind,
                    description=re.sub(r"\s+", " ", t.description or "").strip(),
                    amount=amount,
                    units=units,
                    nav=_dec(t.nav),
                    balance=_dec(t.balance),
                ))
                index += 1

    if len(investors) == 1 and investors[0].name is None:
        investors[0] = StmtInvestor(key=investors[0].key, name=fallback_name)

    period = cas.statement_period
    return StatementData(
        source=SOURCE,
        variant=variant,
        issuer=issuer,
        period_start=_to_date(getattr(period, "from_", None) or period.model_dump(by_alias=True)["from"]),
        period_end=_to_date(period.to),
        currency="INR",
        investors=investors,
        accounts=accounts,
        securities=list(securities.values()),
        positions=positions,
        rows=rows,
        warnings=list(cas.parse_warnings or []),
    )

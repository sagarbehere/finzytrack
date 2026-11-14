"""Record Fava's insert routing as vectors for the splicing primitives (S0115; ADR-0009; the oracle is Fava itself).

For each case below it writes the case's files to a temporary folder, loads the root with Beancount's own loader,
reads the `fava-option` entries with Fava's own `parse_options`, and asks Fava's own `find_insert_position`
(`fava/core/file.py`) where each new entry goes, with the root as the default file. It prints the vectors as JSON:
file names relative to the case, `line` as Fava returns it (the 0-based line index the entry goes before, or null
for an append to the default file). `engine/tests/ledger/test_insert_routing.py` checks `source_edit.route_insert`
against the committed output, `engine/tests/ledger/fava_insert_vectors.json`; never edit that file by hand.

Fava is not a dependency of anything here (ADR-0009): run this in a throwaway environment outside the repository
that holds exactly fava==1.30.16 and beancount==3.2.0, from the repository root, for example:
    <throwaway venv>/bin/python -I scripts/oracle/record_fava_insert.py > engine/tests/ledger/fava_insert_vectors.json
The cases are synthetic (INV-23).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

FAVA_VERSION = "1.30.16"
BEANCOUNT_VERSION = "3.2.0"

OPEN = "".join(
    f"2020-01-01 open {a} USD\n"
    for a in (
        "Assets:Cash",
        "Assets:Bank",
        "Expenses:Food",
        "Expenses:Food:Lunch",
        "Expenses:Coffee",
        "Income:Salary",
        "Equity:Opening",
    )
)


def option(date: str, value: str | None, key: str = "insert-entry") -> str:
    tail = "" if value is None else f" {value}"
    return f'{date} custom "fava-option" "{key}"{tail}\n'


def txn(date: str, *accounts: str) -> str:
    lines = [f'{date} * "Shop" "x"\n']
    lines += [f"  {a}  1.00 USD\n" for a in accounts[:-1]]
    lines.append(f"  {accounts[-1]}\n")
    return "".join(lines)


FOOD = txn("2024-02-01", "Expenses:Food", "Assets:Cash")
FOOD_FIRST = txn("2024-02-01", "Assets:Cash", "Expenses:Food")
LUNCH = txn("2024-02-01", "Expenses:Food:Lunch", "Assets:Cash")
COFFEE = txn("2024-02-01", "Expenses:Coffee", "Assets:Cash")
TWO = txn("2024-02-01", "Expenses:Coffee", "Expenses:Food")

# (name, files in the loader's include order (the root first), entries to insert)
CASES: list[tuple[str, list[tuple[str, str]], list[str]]] = [
    ("no-options", [("main.bean", OPEN)], [FOOD, "2024-02-01 open Assets:New USD\n"]),
    (
        "one-option",
        [("main.bean", OPEN + 'include "food.bean"\n'), ("food.bean", FOOD + option("2020-01-01", '"Expenses:Food"'))],
        [FOOD, FOOD_FIRST, COFFEE, LUNCH, txn("2020-01-01", "Expenses:Food", "Assets:Cash")],
    ),
    (
        "dates",
        [
            (
                "main.bean",
                OPEN
                + option("2021-01-01", '"Expenses:Food"')
                + "\n"
                + option("2023-01-01", '"Expenses:Food"')
                + "\n"
                + option("2025-01-01", '"Expenses:Food"'),
            )
        ],
        [FOOD, txn("2022-06-01", "Expenses:Food", "Assets:Cash"), txn("2026-01-01", "Expenses:Food", "Assets:Cash")],
    ),
    (
        "account-order",
        [
            (
                "main.bean",
                OPEN + option("2021-01-01", '"Expenses:Food"') + "\n" + option("2021-01-01", '"Expenses:Coffee"'),
            )
        ],
        [TWO, txn("2024-02-01", "Expenses:Food", "Expenses:Coffee"), txn("2024-02-01", "Assets:Cash", "Income:Salary")],
    ),
    (
        "patterns",
        [
            (
                "main.bean",
                OPEN
                + option("2021-01-01", '"Food"')
                + option("2021-01-02", '"Expenses"')
                + option("2021-01-03", '".*:Lunch$"')
                + option("2021-01-04", '"Assets:Ca"'),
            )
        ],
        [
            FOOD,
            LUNCH,
            COFFEE,
            txn("2024-02-01", "Income:Salary", "Assets:Cash"),
            txn("2021-01-03", "Equity:Opening", "Expenses:Food:Lunch"),
        ],
    ),
    (
        "directive-accounts",
        [("main.bean", OPEN + option("2021-01-01", '"Assets:Bank"') + "\n" + option("2021-01-02", '"Expenses:Food"'))],
        [
            "2024-02-01 open Assets:Bank:Sub USD\n",
            "2024-02-01 close Expenses:Food\n",
            "2024-02-01 balance Assets:Bank  1.00 USD\n",
            "2024-02-01 pad Expenses:Food Assets:Bank\n",
            "2024-02-01 pad Assets:Bank Expenses:Food\n",
            '2024-02-01 note Assets:Bank "a note"\n',
            '2024-02-01 document Assets:Bank "statement.pdf"\n',
            '2024-02-01 custom "budget" Expenses:Food "x" Assets:Bank\n',
            '2024-02-01 custom "budget" "Expenses:Food"\n',
            "2024-02-01 price EUR 1.10 USD\n",
            '2024-02-01 event "location" "home"\n',
            "2024-02-01 commodity EUR\n",
        ],
    ),
    (
        "same-date-ties",
        [
            ("main.bean", OPEN + 'include "a.bean"\ninclude "b.bean"\n' + option("2021-01-01", '"Expenses"')),
            ("a.bean", "\n\n" + option("2021-01-01", '"Expenses:Food"')),
            (
                "b.bean",
                option("2021-01-01", '"Expenses:F"') + "\n\n\n\n\n\n\n\n\n\n\n\n\n" + option("2021-01-01", '"Exp"'),
            ),
        ],
        [FOOD, COFFEE],
    ),
    (
        "odd-options",
        [
            (
                "main.bean",
                OPEN
                + option("2021-01-01", '"("')
                + option("2021-01-02", None)
                + option("2021-01-03", '"Expenses:Coffee"', key="insert_entry")
                + option("2021-01-04", "Expenses:Food")
                + option("2021-01-05", "12")
                + option("2021-01-06", '"Income"', key="not-an-option"),
            )
        ],
        [
            FOOD,
            COFFEE,
            txn("2024-02-01", "Income:Salary", "Assets:Cash"),
            txn("2021-01-02", "Income:Salary", "Assets:Cash"),
        ],
    ),
    (
        "crlf-and-tabs",
        [("main.bean", (OPEN + option("2021-01-01", '"Expenses:Food"')).replace("\n", "\r\n"))],
        [FOOD, '2024-02-01 *\t"Shop"\n\tExpenses:Food\t1.00 USD\n\tAssets:Cash\n'],
    ),
]


def record() -> dict[str, Any]:
    # Imported here: they exist only in the throwaway environment.
    import beancount
    import fava
    from beancount import loader
    from beancount.core import data
    from beancount.parser import parser
    from fava.core.fava_options import parse_options
    from fava.core.file import find_insert_position

    if (fava.__version__, beancount.__version__) != (FAVA_VERSION, BEANCOUNT_VERSION):
        sys.exit(f"needs fava {FAVA_VERSION} and beancount {BEANCOUNT_VERSION}")
    vectors: list[dict[str, Any]] = []
    for name, files, inserts in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for rel, text in files:
                (root / rel).write_bytes(text.encode())
            entries, _, _ = loader.load_file(str(root / files[0][0]))
            options, _ = parse_options([e for e in entries if isinstance(e, data.Custom)])
            out: list[dict[str, Any]] = []
            for text in inserts:
                new, errors, _ = parser.parse_string(text)
                if len(new) != 1 or errors:
                    sys.exit(f"{name}: {text!r} is not one entry: {errors}")
                filename, line = find_insert_position(new[0], options.insert_entry, str(root / files[0][0]))
                out.append({"text": text, "path": Path(filename).relative_to(root).as_posix(), "line": line})
        vectors.append({"name": name, "files": [list(f) for f in files], "inserts": out})
    return {
        "$comment": (
            f"Recorded from Fava {FAVA_VERSION} (find_insert_position, parse_options) on Beancount {BEANCOUNT_VERSION} "
            "by scripts/oracle/record_fava_insert.py; never edit by hand (S0115)."
        ),
        "vectors": vectors,
    }


if __name__ == "__main__":
    json.dump(record(), sys.stdout, indent=1, ensure_ascii=False)
    sys.stdout.write("\n")

"""Meta-tests for scripts/check_money_dates.py (INV-06: money is exact; INV-07: civil dates, never a UTC "today").

The rules, from the spec (ADR-0023: money is `Decimal`, float only at display and in documented aggregation;
"today" is a civil date from the zone and the injected clock, never the UTC date):
- money is never turned into, read as, or summed as a float in product code;
- a civil date comes only from `app.domain.dates.civil_date`; nothing reads the system's day.
Each rule sits beside the planted case that must not pass and the allowed form that must (EP-29); the last test runs
the check on the real backend and engine trees.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_money_dates as cmd  # noqa: E402

PRODUCT = "backend/src/app/platform/planted.py"
HOME = "backend/src/app/domain/dates.py"

FLOAT_MONEY = (
    "from decimal import Decimal\nx = float(Decimal('10.50'))\n",
    "amounts = ['10.50']\ntotal = sum(map(float, amounts))\n",
    "import json\nx = json.loads('1.5', parse_float=float)\n",
    "convert = float\n",
    "def f(x: str) -> float:\n    return float(x)\n",
    "rows = sorted(['1.5', '10'], key=float)\n",
    "def f(v: str) -> bool:\n    return isinstance(float(v), int)\n",  # only the class argument is a type
)
FROM_FLOAT = (
    "from decimal import Decimal\nx = Decimal.from_float(0.1)\n",
    "import decimal\nx = decimal.getcontext().create_decimal_from_float(0.1)\n",
    "from decimal import Decimal\nmake = Decimal.from_float\n",
)
JSON_AS_FLOAT = (
    "import json\nx = json.loads('{\"amount\": 10.50}')\n",
    "import json\nwith open('f') as fh:\n    x = json.load(fh)\n",
    "import json as j\nx = j.loads(text)\n",
    "from json import loads\nx = loads(text)\n",
    "from json import loads as read\nx = read(text)\n",
    "import json\nd = json.JSONDecoder()\n",
    "from json import JSONDecoder\nd = JSONDecoder(strict=True)\n",
)
MATH_FLOAT = (
    "import math\nx = math.fsum(amounts)\n",
    "import math\nx = math.sqrt(amount)\n",
    "import math as m\nx = m.floor(amount)\n",
    "from math import fsum\n",
    "from math import isfinite, prod\n",
)
ZONELESS = (
    "from datetime import UTC, datetime\nx = datetime.now(UTC).date()\n",
    "def f(clock):\n    return clock.now().date()\n",
    "def f(now):\n    return now.astimezone().date()\n",
    "def f(now):\n    return now.astimezone()\n",
)
SYSTEM_DAY = (
    "from datetime import date\nd = date\nx = d.today()\n",
    "import datetime\nD = datetime.datetime\nx = D.today()\n",
    "from datetime import datetime\nalias = datetime\nx = alias.utcnow()\n",
    "from datetime import datetime\nx = datetime.utcfromtimestamp(0)\n",
)
ALLOWED = (
    # float as a type, a duration marked as not money, and a float literal (pyright refuses it in Decimal arithmetic)
    "def f(seconds: float) -> float:\n    return seconds\n",
    "type Json = bool | int | float | str | None\n",
    "def f(v: object) -> bool:\n    return isinstance(v, int | float)\n",
    "def f(v: object) -> bool:\n    return isinstance(v, (int, float))\n",
    "def f(v: object) -> str:\n    match v:\n        case float():\n            return 'f'\n        case _:\n            return ''\n",
    "x: float = 0.5\n",
    "timeout = 30.0\n",
    "def f(v: int) -> float:\n    return float(v)  # not money: a duration in seconds\n",
    "from typing import Callable\nnow: Callable[[], float]\n",
    # Decimal from text, JSON with Decimal, math's checks on a float
    "from decimal import Decimal\nx = Decimal('10.50')\n",
    "import json\nfrom decimal import Decimal\nx = json.loads(text, parse_float=Decimal)\n",
    "import json\nx = json.dumps({'a': '10.50'})\n",
    "import math\nok = math.isfinite(0.5) and not math.isnan(0.5) and math.inf > 0\n",
    "from math import isfinite, inf\n",
    # a civil date with its zone, a date from given parts, a field named today, a match on the date class
    "from datetime import UTC, datetime\nx = datetime.now(UTC).astimezone(UTC)\n",
    "def f(now, zone):\n    return now.astimezone(tz=zone)\n",
    "from datetime import date\nx = date(2026, 3, 31)\n",
    "from datetime import date\nx = date.fromisoformat('2026-03-31')\n",
    "def f(target):\n    return target.today\n",
    "def f(params):\n    return {'today': params.today}\n",
    "import datetime\ndef f(v: object) -> bool:\n    match v:\n        case datetime.date():\n            return True\n"
    "        case _:\n            return False\n",
)


def found(source: str, rel: str = PRODUCT) -> list[str]:
    return cmd.problems_in(source, rel)


class Money(unittest.TestCase):
    def test_float_used_as_a_value_is_refused(self) -> None:
        for source in FLOAT_MONEY:
            with self.subTest(source=source):
                self.assertTrue(any("INV-06" in p and "float" in p for p in found(source)), found(source))

    def test_a_decimal_made_from_a_float_is_refused(self) -> None:
        for source in FROM_FLOAT:
            with self.subTest(source=source):
                self.assertTrue(any("from_float" in p for p in found(source)), found(source))

    def test_a_json_decode_without_parse_float_is_refused(self) -> None:
        for source in JSON_AS_FLOAT:
            with self.subTest(source=source):
                self.assertTrue(any("parse_float" in p for p in found(source)), found(source))

    def test_math_beyond_its_checks_is_refused(self) -> None:
        for source in MATH_FLOAT:
            with self.subTest(source=source):
                self.assertTrue(any("math." in p for p in found(source)), found(source))

    def test_the_mark_needs_a_reason(self) -> None:
        # Must not pass: an empty mark says nothing.
        self.assertTrue(found("x = float(v)  # not money:\n"))
        self.assertTrue(found("x = float(v)  # money: a duration\n"))


class Dates(unittest.TestCase):
    def test_a_date_without_a_zone_is_refused_outside_the_home(self) -> None:
        for source in ZONELESS:
            with self.subTest(source=source):
                self.assertTrue(any("INV-07" in p for p in found(source)), found(source))

    def test_the_home_may_take_the_date_in_a_zone(self) -> None:
        self.assertEqual(found("def f(now, tz):\n    return now.astimezone(tz).date()\n", HOME), [])

    def test_the_home_is_one_file(self) -> None:
        # Must not pass: another file named dates.py elsewhere is not the home.
        source = "def f(now):\n    return now.astimezone().date()\n"
        self.assertTrue(found(source, "engine/src/ledger_engine/dates.py"))
        self.assertTrue(found(source, "backend/src/app/platform/dates.py"))

    def test_the_systems_day_is_refused_through_an_alias_too(self) -> None:
        for source in SYSTEM_DAY:
            with self.subTest(source=source):
                self.assertTrue(any("INV-07" in p for p in found(source)), found(source))

    def test_the_systems_day_is_refused_in_the_home_too(self) -> None:
        self.assertTrue(found("from datetime import date\nd = date\nx = d.today()\n", HOME))


class Shape(unittest.TestCase):
    def test_allowed_forms_pass(self) -> None:
        for source in ALLOWED:
            with self.subTest(source=source):
                self.assertEqual(found(source), [])

    def test_a_file_that_does_not_parse_fails_closed(self) -> None:
        self.assertTrue(found("def (:\n"))

    def test_each_problem_names_its_file_and_line(self) -> None:
        self.assertEqual(found("x = 1\ny = float(z)\n"), [f"{PRODUCT}:2: " + cmd.FLOAT_MESSAGE])

    def test_the_command_scans_a_folder_and_fails_on_a_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "src"
            (folder / "pkg").mkdir(parents=True)
            (folder / "pkg" / "ok.py").write_text("x = 1\n")
            self.assertEqual(cmd.main([str(folder)]), 0)
            (folder / "pkg" / "bad.py").write_text("x = float(y)\n")
            self.assertEqual(cmd.main([str(folder)]), 1)
            self.assertEqual(cmd.main([str(Path(tmp) / "missing")]), 1)


class RealTrees(unittest.TestCase):
    def test_the_backend_and_engine_product_code_is_clean(self) -> None:
        # INV-06, INV-07: the check holds on the real trees (backend/src, engine/src).
        trees = [cmd.ROOT / t for t in cmd.TREES]
        self.assertTrue(all(t.is_dir() for t in trees))
        self.assertEqual(cmd.check(trees), [])
        self.assertGreater(sum(1 for _ in cmd.files(trees)), 100)  # the trees were walked, not an empty folder


if __name__ == "__main__":
    unittest.main()

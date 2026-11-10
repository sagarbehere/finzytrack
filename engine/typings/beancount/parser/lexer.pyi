# Beancount 3.2.0 beancount/parser/lexer.py: the names the engine uses (S0098).
from collections.abc import Iterator
from typing import IO

def lex_iter(file: str | IO[bytes], builder: object | None = None) -> Iterator[tuple[str, int, bytes, object]]: ...

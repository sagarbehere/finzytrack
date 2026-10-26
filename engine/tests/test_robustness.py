"""The engine never dies on input (S0035 independent verifier F1; server.py docstring: "each [request] gets exactly
one reply" and "the engine keeps serving after any refusal").

Whatever a line holds, the engine answers it with exactly one line that is a valid message of the committed schema
(a reply or a refusal), and the next request is served. The hostile lines come from a seeded generator (the engine's
environment has no Hypothesis; a fixed seed keeps a failure reproducible, EP-28): lone surrogates, odd `v`/`id`/`op`
types, a byte-order mark, whitespace, duplicate keys, params that are not objects, cut lines and raw garbage bytes.
"""

from __future__ import annotations

import io
import json
import random
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from ledger_engine.protocol import server
from ledger_engine.protocol.messages import MAX_ID, HandshakeResult, NoParams
from ledger_engine.protocol.server import Engine, Handler, serve

REPO = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((REPO / "contracts" / "engine-protocol.schema.json").read_text(encoding="utf-8"))
PING = b'{"v":1,"id":2,"op":"ping","params":{}}\n'
HANDSHAKE = b'{"v":1,"id":0,"op":"handshake","params":{}}\n'


def any_message() -> Draft202012Validator:
    names = ["Refusal", *(f"{op.capitalize()}Reply" for op in SCHEMA["x-ops"])]
    return Draft202012Validator({"anyOf": [{"$ref": f"#/$defs/{n}"} for n in names], "$defs": SCHEMA["$defs"]})


VALID = any_message()


def one_reply(out: bytes) -> dict[str, Any]:
    assert out.endswith(b"\n")
    assert out.count(b"\n") == 1, "one reply is one line"
    reply = json.loads(out.decode("utf-8"))  # strict UTF-8: a lone surrogate cannot pass
    assert VALID.is_valid(reply), reply  # pyright: ignore[reportUnknownMemberType] (jsonschema's stubs)
    return reply


def ready() -> Engine:
    engine = Engine()
    assert one_reply(engine.handle(HANDSHAKE))["ok"] is True
    return engine


def still_serving(engine: Engine) -> None:
    assert one_reply(engine.handle(PING))["ok"] is True


# -- the cases the verifier found ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        rb'{"v":1,"id":1,"op":"\ud800","params":{}}',  # unknown op, lone surrogate escape
        rb'{"v":2,"id":1,"op":"\udfff","params":{}}',  # version mismatch echoing the op
        rb'{"v":1,"id":1,"op":"x\udbff","params":[]}',  # params not an object, op echoed
        rb'{"v":1,"id":1,"op":"ping","params":{"\ud800":1}}',  # the bad key named in the params message
        rb'{"v":1,"id":1,"op":"ping","params":{"x\udc00y":[]}}',
    ],
)
def test_text_that_is_not_valid_unicode_is_refused_and_the_engine_keeps_serving(raw: bytes) -> None:
    engine = ready()
    assert one_reply(engine.handle(raw))["ok"] is False
    still_serving(engine)


def test_an_op_that_cannot_be_echoed_exactly_is_answered_with_op_null() -> None:
    """The client matches a refusal by `op` or accepts null; an altered echo would read as another request's."""
    engine = ready()
    reply = one_reply(engine.handle(rb'{"v":1,"id":5,"op":"x\ud800","params":{}}'))
    assert (reply["id"], reply["op"], reply["error"]["key"]) == (5, None, "protocol.unknown_op")


def test_a_long_unknown_op_is_not_echoed() -> None:
    """Echoing an op close to the line limit would make the reply longer than the limit."""
    engine = ready()
    reply = one_reply(engine.handle(json.dumps({"v": 1, "id": 5, "op": "x" * 100_000, "params": {}}).encode()))
    assert (reply["id"], reply["op"], reply["error"]["key"]) == (5, None, "protocol.unknown_op")
    short = one_reply(engine.handle(b'{"v":1,"id":6,"op":"no.such.op","params":{}}'))
    assert short["op"] == "no.such.op"  # an ordinary unknown op is still echoed


def test_a_huge_params_key_gives_a_short_message() -> None:
    engine = ready()
    reply = one_reply(
        engine.handle(json.dumps({"v": 1, "id": 5, "op": "ping", "params": {"k" * 1_000_000: 1}}).encode())
    )
    assert reply["error"]["key"] == "protocol.invalid_params"
    assert len(reply["error"]["message"]) <= 1000


def test_a_handler_that_returns_the_wrong_result_is_internal_and_the_engine_keeps_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong = HandshakeResult(protocol=1, engine_version="x", beancount_version="y")
    monkeypatch.setitem(server.PRODUCTION, "ping", Handler(NoParams, lambda _: wrong))
    engine = ready()
    reply = one_reply(engine.handle(PING))
    assert (reply["id"], reply["op"], reply["error"]["key"]) == (2, "ping", "engine.internal")
    monkeypatch.undo()
    still_serving(ready())


def test_serve_answers_even_when_the_handler_itself_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The last-resort guard in the read loop: one reply per line whatever goes wrong inside."""
    engine = ready()
    real = engine.handle
    calls = {"n": 0}

    def flaky(line: bytes) -> bytes:
        calls["n"] += 1
        if calls["n"] == 1:
            msg = "a bug below the request boundary"
            raise RuntimeError(msg)
        return real(line)

    monkeypatch.setattr(engine, "handle", flaky)
    out = io.BytesIO()
    serve(io.BytesIO(PING + PING), out, engine)
    replies = [one_reply(r + b"\n") for r in out.getvalue().splitlines()]
    assert [r["ok"] for r in replies] == [False, True]
    assert replies[0]["error"]["key"] == "engine.internal"


# -- the property: any line gets one valid reply and the engine keeps serving -------------------------------------

MISSING = object()
VS: list[object] = [1, 1, 1, 2, 0, -1, "1", 1.0, True, None, [], {}, 2**70, MISSING]
IDS: list[object] = [1, 0, MAX_ID, MAX_ID + 1, -1, "1", 1.5, True, None, [], {}, 2**70, MISSING]
OPS: list[object] = [
    "ping", "handshake", "x", "", "\ud800", "x\udfff", chr(0xFEFF), "a" * 300, "test.fail", 1, None, [], {}, MISSING,
]  # fmt: skip
PARAMS: list[object] = [
    {}, {}, [], "x", None, 1, {"\ud800": 1}, {"a": "\ud800"}, {"x": [[[[]]]]}, {"x": 1}, {"\udfff" * 50: 1}, MISSING,
]  # fmt: skip


def _text(rng: random.Random) -> str:
    alphabet = ["a", "\u00e9", "\x00", '"', "\\", chr(0x2028), chr(0xD800), chr(0xDFFF), chr(0xFEFF), chr(0x1F600), " "]
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))


def _envelope(rng: random.Random) -> bytes:
    obj: dict[str, object] = {}
    for key, pool in (("v", VS), ("id", IDS), ("op", [*OPS, _text(rng)]), ("params", PARAMS)):
        value = rng.choice(pool)
        if value is not MISSING:
            obj[key] = value
    if rng.random() < 0.1:
        obj[_text(rng)] = rng.choice(PARAMS[:-1])
    return json.dumps(obj, ensure_ascii=rng.random() < 0.7).encode("utf-8", "surrogatepass")


def _mangle(rng: random.Random, raw: bytes) -> bytes:
    match rng.randrange(9):
        case 0:
            return b"\xef\xbb\xbf" + raw  # a byte-order mark
        case 1:
            return b" \t\r" + raw + b" \r"
        case 2:
            return raw.replace(b"{", b'{"v":1,', 1)  # a duplicate key (when `v` is there)
        case 3:
            return raw[: rng.randrange(len(raw) + 1)]  # cut
        case 4:
            at = rng.randrange(len(raw) + 1)
            junk = bytes(rng.choice([b for b in range(256) if b != 0x0A]) for _ in range(rng.randint(1, 8)))
            return raw[:at] + junk + raw[at:]
        case 5:
            return bytes(rng.choice([b for b in range(256) if b != 0x0A]) for _ in range(rng.randint(0, 40)))
        case 6:
            return b"[" + raw + b"]"
        case _:
            return raw


def hostile_lines(seed: int, n: int) -> list[bytes]:
    rng = random.Random(seed)  # noqa: S311 (test data, not secrets)
    return [_mangle(rng, _envelope(rng)) for _ in range(n)]


def test_every_hostile_line_gets_one_valid_reply_and_the_engine_keeps_serving() -> None:
    engine = Engine(test_ops=True)
    for raw in hostile_lines(20261026, 3000):
        one_reply(engine.handle(raw))
        assert one_reply(engine.handle(HANDSHAKE))["ok"] is True
        still_serving(engine)


def test_the_read_loop_answers_every_hostile_line() -> None:
    lines = hostile_lines(7, 1000)
    out = io.BytesIO()
    serve(
        io.BytesIO(b"".join(raw + b"\n" for raw in [HANDSHAKE.rstrip(b"\n"), *lines, PING.rstrip(b"\n")])),
        out,
        Engine(),
    )
    replies = out.getvalue().split(b"\n")
    assert replies[-1] == b""
    assert len(replies) - 1 == len(lines) + 2
    for r in replies[:-1]:
        one_reply(r + b"\n")
    assert json.loads(replies[-2])["ok"] is True

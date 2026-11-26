"""The owner's hand check on Windows (S0150, BL-0283, BL-0087): `scripts/windows_hand_check.py`, run as the README of
`scripts/windows_hand_check/` says, drives the product's open and save on a scratch ledger in a OneDrive folder and
prints a report to paste back. Here its automatic part (`--auto`: no prompt, every step that needs the owner's hands
reported as skipped) runs on a scratch folder on every OS, so the script cannot break unseen before the owner runs it
once: the engine's placeholder mode, the setup, the open, a save, the burst of saves, the looks at each file, the
cloud-only step's request (OneDrive's `attrib`, which only acts in a OneDrive folder) and the report's privacy (no
absolute path). The OneDrive facts themselves are the owner's run.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "windows_hand_check.py"
ENGINE_VENV = BACKEND.parent / "engine" / ".venv"

NEEDS_ENGINE = pytest.mark.skipif(not ENGINE_VENV.is_dir(), reason="needs the engine's environment (engine/.venv)")


def run(*args: str, cwd: Path = BACKEND) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 (this interpreter, our own script)
        [sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True, check=False, timeout=900, cwd=cwd
    )


@pytest.fixture
def report(tmp_path: Path) -> tuple[dict[str, Any], str, Path]:
    scratch = tmp_path / "check"
    scratch.mkdir()
    done = run("--folder", str(scratch), "--auto")
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-4000:]
    return cast("dict[str, Any]", json.loads(done.stdout)), done.stdout, scratch


@NEEDS_ENGINE
def test_the_automatic_part_opens_and_saves_through_the_product_and_reports_each_step(
    report: tuple[dict[str, Any], str, Path],
) -> None:
    """Must not pass: the script fails, or a step it runs by itself is missing from the report or does not save."""
    got, _, scratch = report
    assert got["platform"] == sys.platform
    assert got["mode"]["engine"] == (1 if os.name == "nt" else None), got["mode"]
    assert got["mode"]["packaged"] == "not asked (no --packaged)"
    assert got["open"]["outcome"] == "opened", got["open"]
    assert got["save"]["outcome"] == "saved", got["save"]
    assert got["save"]["text_on_disk"] == "the plan's text", got["save"]
    burst = got["burst"]
    assert [b["outcome"] for b in burst["saves"]] == ["saved"] * burst["count"], burst
    assert burst["count"] >= 5
    made = got["made_folder"]  # S0151 round 1: a folder made there, watched, then removed
    assert made["remove_file"] == "ok", made
    assert made["folder_before_removal"]["file"] == "made", made
    if os.name == "nt":  # S0152: no OneDrive on the runner, so the script marks it read-only itself, as OneDrive does
        assert made["read_only_set_by"] == "the script (--auto: no OneDrive here)", made
        assert made["rmdir"].startswith("PermissionError"), made
        assert "READONLY" in made["folder_after_refusal"]["attrs"], made
        assert made["engine_rmdir_made"] == "ok", made  # the engine's port: clear, then remove
    else:
        assert (made["rmdir"], made["engine_rmdir_made"]) == ("ok", "not needed (rmdir ok)"), made
    assert made["gone"] is True, made
    assert not (scratch / "made").exists()
    for step in ("offline", "paused", "q1_unlisted_folder", "q2_cloud_only_plugin"):
        assert got[step] == "skipped (--auto)", (step, got[step])
    assert set(got["looks"]) >= {"after_setup", "after_save", "after_free_up_space"}, got["looks"]
    assert (scratch / "report.json").is_file()
    assert json.loads((scratch / "report.json").read_text(encoding="utf-8")) == got


@NEEDS_ENGINE
def test_the_report_names_no_absolute_path(report: tuple[dict[str, Any], str, Path]) -> None:
    """Must not pass (INV-23: the owner pastes the report into a session, and it may enter a journal): the scratch
    folder, the temp folder or the user's home appears in the report."""
    _, text, scratch = report
    for path in {str(scratch), str(scratch.resolve()), str(Path.home()), os.path.realpath(scratch)}:
        assert path not in text
        assert json.dumps(path)[1:-1] not in text  # as JSON writes it (a Windows path's backslashes doubled)


@NEEDS_ENGINE
def test_a_folder_that_is_not_empty_is_refused_and_left_as_it_is(tmp_path: Path) -> None:
    """Must not pass: the script writes in a folder that holds anything (it may only write its own scratch folder)."""
    (tmp_path / "mine.txt").write_bytes(b"keep")
    done = run("--folder", str(tmp_path), "--auto")
    assert done.returncode == 2, done.stdout + done.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["mine.txt"]
    assert (tmp_path / "mine.txt").read_bytes() == b"keep"


@NEEDS_ENGINE
def test_without_a_folder_off_windows_it_refuses_before_anything() -> None:
    """Must not pass: off Windows, or without OneDrive, the script makes a folder anywhere."""
    if os.name == "nt" and os.environ.get("ONEDRIVE"):  # Windows: names are case-blind
        pytest.skip("this PC has OneDrive: the script would make its scratch folder there")
    done = run("--auto")
    assert done.returncode == 2, done.stdout + done.stderr
    assert "OneDrive" in done.stderr


def _script() -> Any:
    spec = importlib.util.spec_from_file_location("windows_hand_check", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _non_ascii_homes(tmp_path: Path) -> list[str]:
    """Absolute paths with a character outside ASCII, built at run time (no home-path shape in the source, INV-23): a
    temp folder named like a user folder, the same in Windows' spelling, and a OneDrive for Business folder."""
    return [
        str(tmp_path / "S\u00f8ren"),
        "\\".join(["C:", "Users", "S\u00f8ren"]),
        str(tmp_path / "x" / "OneDrive - F\u00f6retag AB"),
    ]


@pytest.mark.parametrize("which", [0, 1, 2])
def test_a_path_with_a_character_outside_ascii_is_cleaned_from_the_report(tmp_path: Path, which: int) -> None:
    """S0150 round 1 (V2, M2; INV-23). Must not pass: a known path with a character outside ASCII (a user name, a
    OneDrive for Business folder) reaches the report, as itself or as JSON's `\\uXXXX` escapes, in a value (raw or
    `repr`'s) or a key."""
    home = _non_ascii_homes(tmp_path)[which]
    m = _script()
    clean = m.Cleaner()
    clean.add(home, "<home>")
    sep = "\\" if "\\" in home else os.sep
    file = f"{home}{sep}OneDrive{sep}main.bean"
    text = clean.report({"save": {"details": repr(file), "path": file}, file: [file]})
    for spelling in ("s\u00f8ren", "S\u00f8ren", "\\u00f8", "\u00f6", "\\u00f6", str(tmp_path)):
        assert spelling not in text, text
    assert text.count("<home>") == 4, text


def test_a_failure_outside_a_step_still_gives_a_cleaned_report_and_closes_the_scheduler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S0150 round 1 (the verifier's question). Must not pass: a failure outside a step (the scheduler that does not
    start) ends the run with no report, or leaves its client open."""
    m = _script()

    class Broken:
        def __init__(self, app: object, cache: object) -> None:
            raise RuntimeError("the scheduler did not start")  # noqa: TRY003

    monkeypatch.setattr(m, "Product", Broken)

    def no_mode(command: object) -> None:
        del command

    monkeypatch.setattr(m, "mode_of", no_mode)
    scratch = tmp_path / "check"
    scratch.mkdir()
    report = m.run(scratch, True, None, m.Cleaner())
    assert report["unexpected_failure"] == "RuntimeError: the scheduler did not start", report
    assert report["mode"] == {"engine": None, "packaged": "not asked (no --packaged)"}


def test_a_scheduler_that_does_not_start_closes_its_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """S0150 round 1. Must not pass: `Product` raises out of its start with its client and scheduler open."""
    m = _script()
    closed: list[str] = []

    class Jobs:
        tickets: list[object]

        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def start(self) -> object:
            return "refused"

        def close(self) -> None:
            closed.append("jobs")

    class Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def close(self) -> None:
            closed.append("client")

    monkeypatch.setattr(m, "Jobs", Jobs)
    monkeypatch.setattr(m, "EngineClient", Client)
    product_argv = m.bootstrap_argv(m.venv_python(m.ENGINE_VENV), m.venv_site(m.ENGINE_VENV), m.ENGINE_RUN)

    def no_args(argv: object) -> None:  # no engine environment needed here
        del argv

    def command(*args: object) -> object:
        del args
        return m.EngineCommand(argv=product_argv, env={})

    monkeypatch.setattr(m, "parse_args", no_args)
    monkeypatch.setattr(m, "engine_command", command)
    app = m.prepare(tmp_path / "app")
    (tmp_path / "cache").mkdir()
    with pytest.raises(m.Stop, match="did not start"):
        m.Product(app, tmp_path / "cache")
    assert closed == ["jobs", "client"]

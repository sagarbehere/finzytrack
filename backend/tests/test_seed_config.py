"""First-run config seeding (`app.service_factory.seed_config`).

The install directory is not a trustworthy inventory of the current release. On
Windows the app is a folder the user unzips, so extracting a new release over an
old one leaves behind every file the new version dropped. Seeding must therefore
copy only what the app supports, or a *brand-new* install can end up holding
legacy content — which is what put the "Upgrade saved dashboards" gate in front of
the setup wizard on a fresh install.
"""

from pathlib import Path

from app import service_factory


def _bundle(tmp_path: Path, monkeypatch) -> Path:
    """A synthetic seed_config bundle carrying both supported and stale content."""
    seed = tmp_path / "bundle" / "seed_config"
    (seed / "recipes" / "dashboards").mkdir(parents=True)
    (seed / "recipes" / "widgets").mkdir(parents=True)
    (seed / "csv_rules").mkdir(parents=True)

    (seed / "config.yaml").write_text("setup_complete: false\n", encoding="utf-8")
    (seed / "recipes" / "dashboards" / "a.json").write_text('{"id":"a"}\n', encoding="utf-8")
    # Left over from a release before the DAG refactor removed standalone widgets.
    (seed / "recipes" / "widgets" / "expense-treemap.json").write_text(
        '{"id":"expense-treemap","query":"SELECT 1"}\n', encoding="utf-8"
    )
    (seed / "csv_rules" / "example.yaml").write_text("rules: []\n", encoding="utf-8")

    monkeypatch.setattr(service_factory, "SEED_CONFIG_DIR", seed)
    return seed


def test_seed_copies_supported_content(tmp_path: Path, monkeypatch):
    _bundle(tmp_path, monkeypatch)
    config = tmp_path / "config"

    service_factory.seed_config(config)

    assert (config / "config.yaml").read_text(encoding="utf-8") == "setup_complete: false\n"
    assert (config / "recipes" / "dashboards" / "a.json").exists()
    # Trees outside recipes/ are copied whole — the filter is recipe-scoped.
    assert (config / "csv_rules" / "example.yaml").exists()


def test_seed_skips_unsupported_recipe_paths(tmp_path: Path, monkeypatch):
    """A stale `recipes/widgets/` in the bundle must not reach a new install: the
    recipes router only serves `dashboards/`, and seeding those files made a first
    run open with the migration gate instead of the setup wizard."""
    _bundle(tmp_path, monkeypatch)
    config = tmp_path / "config"

    service_factory.seed_config(config)

    assert not (config / "recipes" / "widgets").exists()
    assert sorted(p.name for p in (config / "recipes").iterdir()) == ["dashboards"]


def test_seed_never_overwrites_an_existing_config(tmp_path: Path, monkeypatch):
    _bundle(tmp_path, monkeypatch)
    config = tmp_path / "config"
    config.mkdir()
    (config / "config.yaml").write_text("setup_complete: true\n", encoding="utf-8")

    service_factory.seed_config(config)

    assert (config / "config.yaml").read_text(encoding="utf-8") == "setup_complete: true\n"
    assert not (config / "recipes").exists()

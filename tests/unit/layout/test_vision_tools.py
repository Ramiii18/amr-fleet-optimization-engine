"""The tools of the vision step (D42): plan generator, render script, scorer."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from amr_optimizer.layout import compile_layout, load_layout, require_well_formed

pytest.importorskip("matplotlib")

ROOT = Path(__file__).resolve().parents[3]
TRUTH = ROOT / "layouts" / "ground_truth"
NAMES = ("plain_hall", "l_hall", "mixed_hall")


def script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", NAMES)
def test_every_ground_truth_is_well_formed_and_has_its_plan(name: str) -> None:
    layout = load_layout(TRUTH / f"{name}.layout.json")
    assert layout.description.startswith("Synthetic")
    require_well_formed(compile_layout(layout), robots=8)
    meta = json.loads((TRUTH / f"{name}.plan.json").read_text(encoding="utf-8"))
    assert (meta["rows"], meta["columns"]) == (layout.rows, layout.columns)
    assert (TRUTH / f"{name}.plan.png").is_file()


def test_the_plan_generator_is_deterministic(tmp_path: Path) -> None:
    generator = script("make_synthetic_floorplan")
    layout = load_layout(TRUTH / "mixed_hall.layout.json")
    first = generator.draw(layout, tmp_path / "a.png")
    second = generator.draw(layout, tmp_path / "b.png")
    assert (tmp_path / "a.png").read_bytes() == (tmp_path / "b.png").read_bytes()
    committed = json.loads((TRUTH / "mixed_hall.plan.json").read_text(encoding="utf-8"))
    assert first | {"image": "x"} == second | {"image": "x"} == committed | {"image": "x"}
    assert first["px_per_m"] == 24 and first["origin_px"] == [48.0, 48.0]


def test_the_render_is_deterministic_and_takes_the_plan(tmp_path: Path) -> None:
    render = script("render_layout").render
    layout = TRUTH / "plain_hall.layout.json"
    plan = TRUTH / "plain_hall.plan.png"
    for out in ("a.png", "b.png"):
        render(layout, tmp_path / out, plan, 24.0, (48.0, 48.0))
    assert (tmp_path / "a.png").read_bytes() == (tmp_path / "b.png").read_bytes()
    render(layout, tmp_path / "map.png")  # without a plan: the map alone
    assert (tmp_path / "map.png").stat().st_size > 0
    with pytest.raises(ValueError, match="px-per-m"):
        render(layout, tmp_path / "c.png", plan, None)


@pytest.mark.parametrize("name", ["plain_hall"])
def test_a_truth_scores_perfectly_against_itself(name: str) -> None:
    scorer = script("score_transcription")
    truth = TRUTH / f"{name}.layout.json"
    lines = "\n".join(scorer.score(truth, truth, 6, 150.0))
    assert "shift of +0 rows, +0 columns" in lines
    assert "(100.0%)" in lines
    assert "Same storage cells: yes" in lines
    assert "| +0.0 [+0.0, +0.0] |" in lines


def test_the_scorer_finds_a_shift_and_a_missing_cell(tmp_path: Path) -> None:
    from amr_optimizer.layout import Layout, save_layout

    truth = load_layout(TRUTH / "plain_hall.layout.json")
    rows = [list("#" + row) for row in truth.grid]  # one column too many on the left
    rows[4][3] = "#"  # a storage cell missed
    path = tmp_path / "copy.layout.json"
    save_layout(Layout(tuple("".join(r) for r in rows), 1.0, "test"), path)
    lines = "\n".join(
        script("score_transcription").score(TRUTH / "plain_hall.layout.json", path, 6, 150.0)
    )
    assert "shift of +0 rows, +1 columns" in lines
    assert "| storage | 42 | 100.0% | 97.6% |" in lines
    assert "Same storage cells: no" in lines


def test_the_degraded_plan_is_deterministic(tmp_path: Path) -> None:
    generator = script("make_synthetic_floorplan")
    layout = load_layout(TRUTH / "plain_hall.layout.json")
    for name in ("a.png", "b.png"):
        meta = generator.draw(layout, tmp_path / name, generator.DEGRADED_PX_PER_M, dimensions=True)
        generator.degrade(
            tmp_path / name, tmp_path / name, angle_deg=1.5, blur_px=0.8, noise=0.06, seed=7
        )
    assert (tmp_path / "a.png").read_bytes() == (tmp_path / "b.png").read_bytes()
    assert meta["px_per_m"] == 21.7
    committed = json.loads((TRUTH / "plain_hall.degraded.json").read_text(encoding="utf-8"))
    assert (committed["px_per_m"], committed["rotation_deg"], committed["noise_seed"]) == (
        21.7,
        1.5,
        7,
    )


def test_the_render_can_straighten_a_tilted_plan(tmp_path: Path) -> None:
    render = script("render_layout").render
    plan = TRUTH / "plain_hall.degraded.png"
    for out in ("a.png", "b.png"):
        render(TRUTH / "plain_hall.layout.json", tmp_path / out, plan, 21.7, (60.0, 60.0), -1.5)
    assert (tmp_path / "a.png").read_bytes() == (tmp_path / "b.png").read_bytes()

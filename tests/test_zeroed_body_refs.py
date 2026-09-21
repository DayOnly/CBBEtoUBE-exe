"""The converter's reference bodies are BodySlide's zeroed builds (#zeroed-body-refs).

The CBBE body the warp morphs FROM, the UBE body it morphs TOWARD and the UBE
body injected on the body-swap path come from src/zeroed_body.py first. Name
discovery picked the 3BA mod folder's own femalebody on a real modlist -- a
preset build the game never loads, whose weight morph moved weight-1 garments
~0.6u further in than weight 0 -- and it stays only as the fallback when the
game's body is not a zeroed build. An explicit override still wins, and
CBBE2UBE_NO_ZEROED_BODY_REFS=1 restores the old discovery outright.
"""
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import nif_convert_bodyrefs as br  # noqa: E402
from src import zeroed_body as zb  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    br._BODY_DISCOVERY_CACHE.clear()        # mutate, never rebind: shared object
    br._ZEROED_WARNED.clear()
    for v in ("CBBE2UBE_CBBE_BODY_0", "CBBE2UBE_CBBE_BODY_1", "CBBE2UBE_UBE_BODY",
              "CBBE2UBE_UBE_BODY_0", "CBBE2UBE_UBE_BODY_1"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(br, "ZEROED_BODY_REFS", True)
    # name discovery would find THIS body: the preset one a mod folder ships
    legacy = tmp_path / "3BA mod" / "femalebody_1.nif"
    legacy.parent.mkdir()
    legacy.write_bytes(b"nif")
    monkeypatch.setattr(br, "_iter_femalebody_nifs",
                        lambda w: iter([(legacy.parent, legacy)]))
    monkeypatch.setattr(br, "_shape_has_3ba_topology", lambda p: True)
    monkeypatch.setattr(br, "_find_user_preset_body", lambda w="_1": legacy)
    yield legacy
    br._BODY_DISCOVERY_CACHE.clear()


def _zeroed(calls, fail=False):
    def fake(kind, weight="_1", **_kw):
        calls.append((kind, weight))
        if fail:
            raise zb.ZeroedBodyError("the game's femalebody is a preset build")
        return zb.ZeroedBody(Path(f"zeroed_{kind}{weight}.nif"), "Body", "set", 0.0)
    return fake


def test_the_warp_morphs_from_the_zeroed_cbbe_body(monkeypatch):
    calls = []
    monkeypatch.setattr(zb, "zeroed_body", _zeroed(calls))
    assert br._find_cbbe_base_body("_1") == Path("zeroed_cbbe_1.nif")
    assert br._find_cbbe_base_body("_0") == Path("zeroed_cbbe_0.nif")
    assert calls == [("cbbe", "_1"), ("cbbe", "_0")]


def test_the_warp_morphs_toward_the_zeroed_ube_body(monkeypatch):
    monkeypatch.setattr(zb, "zeroed_body", _zeroed([]))
    assert br._find_ube_femalebody("_0") == Path("zeroed_ube_0.nif")


def test_the_injected_body_is_the_zeroed_ube_body(monkeypatch):
    from src import auto_convert as ac
    monkeypatch.setattr(zb, "zeroed_body", _zeroed([]))
    assert ac._find_ube_body_ref() == Path("zeroed_ube_1.nif")


def test_no_zeroed_body_falls_back_to_discovery_with_one_warning(monkeypatch, capsys, _fresh):
    monkeypatch.setattr(zb, "zeroed_body", _zeroed([], fail=True))
    assert br._find_cbbe_base_body("_1") == _fresh
    br._BODY_DISCOVERY_CACHE.clear()
    assert br._find_cbbe_base_body("_1") == _fresh
    err = capsys.readouterr().err
    assert err.count("no zeroed CBBE body at weight 1") == 1


def test_the_off_switch_restores_discovery_by_name(monkeypatch, _fresh):
    calls = []
    monkeypatch.setattr(zb, "zeroed_body", _zeroed(calls))
    monkeypatch.setattr(br, "ZEROED_BODY_REFS", False)
    assert br._find_cbbe_base_body("_1") == _fresh
    assert calls == []


def test_an_explicit_override_still_wins(monkeypatch, tmp_path):
    mine = tmp_path / "chosen_1.nif"
    mine.write_bytes(b"nif")
    monkeypatch.setattr(zb, "zeroed_body", _zeroed([]))
    monkeypatch.setenv("CBBE2UBE_CBBE_BODY_1", str(mine))
    monkeypatch.setenv("CBBE2UBE_UBE_BODY", str(mine))
    assert br._find_cbbe_base_body("_1") == mine
    assert br._find_ube_femalebody("_1") == mine

"""Guard: the `validate` subcommand must resolve master TIER correctly.

Master-tier classification opens each master to read its TES4 flags, because an
ESL-flagged .esp (ESPFE) is master-tier while looking like a regular .esp. When
`validate_patch` is called WITHOUT search dirs it cannot open them, falls back
to "regular", and every ESPFE master is misclassified -- so any .esm/.esl later
in the master list trips a bogus "master-ordering ... crash" on output whose
order is actually correct. Measured on real output: 2/2 plugins reported a
master-ordering crash that vanished entirely once the dirs were supplied.
"""
import struct

from src import auto_convert, esp, ube_patcher


def _plugin(path, masters, flags=0):
    e = esp.ESP(header=esp.TES4Header(masters=list(masters)), groups=[])
    e.save(path)
    if flags:                      # stamp TES4 record flags in place
        b = bytearray(path.read_bytes())
        b[8:12] = struct.pack("<I", flags)
        path.write_bytes(bytes(b))


def test_espfe_master_is_not_reported_as_an_ordering_error(tmp_path):
    data = tmp_path / "Data"
    data.mkdir()
    _plugin(data / "Light.esp", [], flags=0x200)      # ESPFE: master-tier
    _plugin(data / "Later.esl", [])                   # master-tier by extension
    target = tmp_path / "Combined.esp"
    _plugin(target, ["Skyrim.esm", "Light.esp", "Later.esl"])

    ube_patcher.clear_esm_tier_cache()
    blind = [w for w in ube_patcher.validate_patch(target, check_nifs=False)
             if "master-ordering" in str(w)]
    ube_patcher.clear_esm_tier_cache()
    seeing = [w for w in ube_patcher.validate_patch(
        target, check_nifs=False, master_data_dirs=[data])
        if "master-ordering" in str(w)]

    assert blind, ("fixture no longer reproduces the blind-classification "
                   "false positive; rewrite it")
    assert not seeing, (
        "an ESPFE master was still misreported as an ordering error even with "
        f"search dirs supplied: {seeing}")


def test_validate_command_passes_master_data_dirs(tmp_path, monkeypatch):
    """The fix is in the COMMAND: it must hand the dirs to validate_patch.
    Without this the false positive returns and every run ends 'with errors'."""
    seen = {}

    def _fake(path, **kw):
        seen["dirs"] = kw.get("master_data_dirs")
        return []

    mod = tmp_path / "mod"
    mod.mkdir()
    _plugin(mod / "Combined.esp", ["Skyrim.esm"])
    idx = {"skyrim.esm": str(tmp_path / "Data" / "Skyrim.esm")}
    monkeypatch.setattr(ube_patcher, "validate_patch", _fake)
    monkeypatch.setattr(auto_convert.paths, "discover_layout", lambda: None)
    monkeypatch.setattr(auto_convert.paths, "plugin_file_index", lambda l: idx)

    args = type("A", (), {"mod_dir": str(mod), "meshes_root": None,
                          "no_nifs": True})()
    auto_convert._cmd_validate(args)

    assert seen.get("dirs"), (
        "the validate command called validate_patch without master_data_dirs")

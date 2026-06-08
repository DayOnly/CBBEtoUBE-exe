"""(b) Regenerate the deployed Vanilla_UBE_Race_Compat.esp WITH body
coverage (166 vanilla body ARMAs + ARMO overrides) + humanoid armor
race-extension, skin-free (hardened guard), beast-free (DefaultRace gate
+ minimal master dirs -> no beast UBE race discovery)."""
import os
import io,sys,shutil
from pathlib import Path
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding="utf-8",errors="replace")
sys.path.insert(0,'.')
from src import ube_patcher as up, vanilla_bsa_armor as vba
DATA=Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\Stock Game\Data")
UBE=Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\UBE 2.0 U. 0.7")
OUT=Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\CBBEtoUBE Auto\Vanilla_UBE_Race_Compat.esp")
data_dirs=[DATA, UBE]
m=vba.enumerate_vanilla_body_meshes([DATA])
conv=set()
for k in m:
    s=k.replace("\\","/").lower()
    if s.startswith("meshes/"): s=s[len("meshes/"):]
    conv.add(s)
print(f"vanilla body meshes enumerated: {len(conv)}")
# back up current deployed
bak=OUT.with_name(OUT.stem+".prebodycov.bak")
if not bak.exists(): shutil.copy2(OUT,bak); print(f"backed up -> {bak.name}")
stats=up.generate_vanilla_race_compat_patch(OUT, data_dirs, converted_rel_paths=conv)
print("REGENERATED Vanilla_UBE_Race_Compat.esp:")
for k in ("arma_overrides","body_arma_minted","body_armo_overrides",
          "skipped_nude_skin","skipped_non_default_race"):
    print(f"   {k}: {stats.get(k)}")
warns=[w for w in stats.get("validation_warnings",[]) if "missing-nif" not in w]
print(f"   validation_warnings (non-missing-nif): {warns or 'NONE'}")

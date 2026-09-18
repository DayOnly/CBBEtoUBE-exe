# What is vendored here

`.pynifly/` holds PyNifly -- the `pyn` Python package and `NiflyDLL.dll` -- from the
upstream repository linked in [THIRD-PARTY-NOTICES.md](../THIRD-PARTY-NOTICES.md),
plus this project's local changes to it. This file records exactly which bytes those
are, so a build can be tied to its corresponding source (GPL-3.0 section 6), and
`tests/test_pynifly_provenance.py` checks every line of the record below.

- **Upstream release:** tag `V25.9`, published 2026-03-23 according to the upstream
  release list.
- **How it was identified:** the DLL reports version 25.9.0 through its
  `getVersion` export, and 8 of the 18 files in `pyn/`
  are byte-identical to that tag's files (same git blob ids).
- **Upstream commit:** not recorded. The tag was matched by file contents, not by a
  commit id; add the commit when it is next looked up.
- **Local changes:** 10 of the 18 files differ from the tag. Each
  change is `patches/<file>.diff`, a unified diff from the upstream file (`a/`) to the
  vendored one (`b/`). Running `git apply -R -p1 patches/<file>.diff` in this folder
  turns a vendored file back into the upstream file byte for byte.
- **Updating:** a new upstream release, a changed DLL or a new local edit means
  regenerating the patches and this record together; the test fails until they agree.

Recorded 2026-09-15.

## Record

```text
tag          V25.9
released     2026-03-23
commit       not recorded
dll_sha256   cb1801dbebcf9d95811100de7872af88257cf8bffca506ddfbe1125ae4a46130
dll_version  25.9.0
file  __init__.py        upstream e69de29bb2d1d6434b8b29ae775ad8c2e48c5391  local e69de29bb2d1d6434b8b29ae775ad8c2e48c5391
file  bgsmaterial.py     upstream d94e497478e756de1b0fdb53730a23adeed2c02c  local 8641888fa950a5a3176a2140a7c82a6c01ab776d  patch patches/bgsmaterial.py.diff
file  bhk_autopack.py    upstream c62d3d4a5919e1c02b0bceffb38cf573823ab8c0  local 7a9d3eac0d3ff22657d9b7a81df125de05a9c1cb  patch patches/bhk_autopack.py.diff
file  bhk_autounpack.py  upstream 0712f86bec1f3a8c1bd33260bf1834484b800db7  local 7eceb0e792969925238a4f3b1ea8dc61862927ad  patch patches/bhk_autounpack.py.diff
file  mesh_segment.py    upstream b7836eaff062d7c6a1229dcc591c0acb986fe8b3  local b7836eaff062d7c6a1229dcc591c0acb986fe8b3
file  mopp_compiler.py   upstream 800b4f12c9f302cf5f6254c977a0a00a0f7d62d0  local e58aaa7da59ef1f9684f7ae07fec9f5afeff5a4a  patch patches/mopp_compiler.py.diff
file  nifconstants.py    upstream d765cf902b3381ca5b0186499260d40c36aaf710  local d765cf902b3381ca5b0186499260d40c36aaf710
file  nifdefs.py         upstream 350f1e8e3421d85671db058d4cdfafb41a1a7df2  local 88c78bba2c7449688dc7840cf03b09c8e00029d8  patch patches/nifdefs.py.diff
file  niflydll.py        upstream 16c5c4f8458f4f9d82917a3688b6badc5444603a  local 8b831581b548a93ff99edc2c64e7d870fbf27606  patch patches/niflydll.py.diff
file  niflytools.py      upstream 977a83ffe2656e5e72811a3d5f6639b4068e99dd  local c0617a7bd607d0606fd2c8acb04f5e79380316ba  patch patches/niflytools.py.diff
file  pynenum.py         upstream 93be20465f980b848c158f4efbc9c985d5959a17  local 93be20465f980b848c158f4efbc9c985d5959a17
file  pynifly.md         upstream 876c41f7240fdee2b65a203ebacd41bac5ccefb0  local 876c41f7240fdee2b65a203ebacd41bac5ccefb0
file  pynifly.py         upstream 711baf4555144ab42b8ee46ba74b45e722977ca6  local 612e29dc98c06dbe183b3bfe27f7c030c1ebe9c7  patch patches/pynifly.py.diff
file  pynmathutils.py    upstream dfadce7f0c14b9d662e0e7fd16a4d5064f1cf8bc  local d5a6348019f944271b507b0b15031f39a2fa4f45  patch patches/pynmathutils.py.diff
file  structs.py         upstream c8637dd029a7117246439433908c8555dc0316b6  local c8637dd029a7117246439433908c8555dc0316b6
file  tri_strip.py       upstream 10471cd9ea534bd7eac336f157c717fa2568e854  local ef551dea5a163a537f7398e2c3bbd069e569ac62  patch patches/tri_strip.py.diff
file  triangulate.py     upstream 7c1460e4bdc3bf0ba749d305c1eeaa1fc7c0e467  local 7c1460e4bdc3bf0ba749d305c1eeaa1fc7c0e467
file  xmltools.py        upstream 38c989e8545b8fc17f45070c2422f6cbe52398b0  local 38c989e8545b8fc17f45070c2422f6cbe52398b0
```

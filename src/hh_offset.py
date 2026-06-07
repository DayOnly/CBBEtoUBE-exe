"""High-heel offset (HH_OFFSET) preservation for converted boots.

The heel-height of a heeled boot is a `NiFloatExtraData` named "HH_OFFSET" on the
root node (NiOverride / RaceMenu High Heels reads it and raises the actor at
runtime). pynifly CANNOT read or write NiFloatExtraData — it silently drops the
block on load — so a boot run through the converter loses its heel and the
character sinks into the ground.

This module preserves it WITHOUT pynifly: it parses the SSE NIF binary directly
(lossless, round-trip verified) to (a) read the source boot's HH_OFFSET float and
(b) transplant an equivalent NiFloatExtraData block into the pynifly-written
converted NIF. SAFETY: every operation first checks that our parser round-trips
the file byte-for-byte; if it doesn't (an unusual header layout we don't fully
model), we refuse to touch the file and the caller falls back to ESP-only. The
transplant must run AFTER all pynifly saves (pynifly would drop the block again).
"""
import struct


def _parse(data: bytes) -> dict:
    o = 0
    nl = data.index(b"\x0a", o)
    hdr_string = data[o:nl + 1]; o = nl + 1
    version, = struct.unpack_from("<I", data, o); o += 4
    endian = data[o]; o += 1
    user_version, = struct.unpack_from("<I", data, o); o += 4
    num_blocks, = struct.unpack_from("<I", data, o); o += 4
    bs_version, = struct.unpack_from("<I", data, o); o += 4

    def sstr(o):
        n = data[o]; o += 1
        return data[o:o + n], o + n
    author, o = sstr(o)
    proc, o = sstr(o)
    export, o = sstr(o)
    num_bt, = struct.unpack_from("<H", data, o); o += 2
    block_types = []
    for _ in range(num_bt):
        n, = struct.unpack_from("<I", data, o); o += 4
        block_types.append(data[o:o + n]); o += n
    bti = list(struct.unpack_from("<%dH" % num_blocks, data, o)); o += 2 * num_blocks
    bsizes = list(struct.unpack_from("<%dI" % num_blocks, data, o)); o += 4 * num_blocks
    num_strings, = struct.unpack_from("<I", data, o); o += 4
    max_str, = struct.unpack_from("<I", data, o); o += 4
    strings = []
    for _ in range(num_strings):
        n, = struct.unpack_from("<I", data, o); o += 4
        strings.append(data[o:o + n]); o += n
    num_groups, = struct.unpack_from("<I", data, o); o += 4
    groups = list(struct.unpack_from("<%dI" % num_groups, data, o)) if num_groups else []
    o += 4 * num_groups
    blocks = []
    for sz in bsizes:
        blocks.append(data[o:o + sz]); o += sz
    footer = data[o:]
    return dict(hdr_string=hdr_string, version=version, endian=endian,
                user_version=user_version, num_blocks=num_blocks,
                bs_version=bs_version, author=author, proc=proc, export=export,
                block_types=block_types, bti=bti, bsizes=bsizes,
                max_str=max_str, strings=strings, num_groups=num_groups,
                groups=groups, blocks=blocks, footer=footer)


def _serialize(p: dict) -> bytes:
    out = bytearray()
    out += p["hdr_string"]
    out += struct.pack("<I", p["version"])
    out += bytes([p["endian"]])
    out += struct.pack("<I", p["user_version"])
    out += struct.pack("<I", p["num_blocks"])
    out += struct.pack("<I", p["bs_version"])
    for s in (p["author"], p["proc"], p["export"]):
        out += bytes([len(s)]) + s
    out += struct.pack("<H", len(p["block_types"]))
    for bt in p["block_types"]:
        out += struct.pack("<I", len(bt)) + bt
    out += struct.pack("<%dH" % p["num_blocks"], *p["bti"])
    out += struct.pack("<%dI" % p["num_blocks"], *p["bsizes"])
    out += struct.pack("<I", len(p["strings"]))
    out += struct.pack("<I", p["max_str"])
    for s in p["strings"]:
        out += struct.pack("<I", len(s)) + s
    out += struct.pack("<I", p["num_groups"])
    if p["num_groups"]:
        out += struct.pack("<%dI" % p["num_groups"], *p["groups"])
    for b in p["blocks"]:
        out += b
    out += p["footer"]
    return bytes(out)


def _parse_if_lossless(data: bytes):
    """Parse, but only return the dict if it round-trips byte-for-byte (so we
    never modify a file our parser doesn't fully understand). Else None."""
    try:
        p = _parse(data)
        return p if _serialize(p) == data else None
    except Exception:
        return None


def read_hh_offset(path) -> "float | None":
    """Return the boot's HH_OFFSET float, or None if absent / unparseable."""
    try:
        data = open(path, "rb").read()
    except OSError:
        return None
    if b"HH_OFFSET" not in data:
        return None
    p = _parse_if_lossless(data)
    if p is None:
        return None
    fed_t = next((i for i, bt in enumerate(p["block_types"])
                  if bt == b"NiFloatExtraData"), None)
    if fed_t is None:
        return None
    for bi, ti in enumerate(p["bti"]):
        if ti != fed_t or len(p["blocks"][bi]) < 8:
            continue
        nm, = struct.unpack_from("<i", p["blocks"][bi], 0)
        if 0 <= nm < len(p["strings"]) and p["strings"][nm] == b"HH_OFFSET":
            return struct.unpack_from("<f", p["blocks"][bi], 4)[0]
    return None


def transplant_hh_offset(dst_path, value: float) -> bool:
    """Insert a NiFloatExtraData "HH_OFFSET" = `value` on the armor's TRI-SHAPE in
    the (pynifly-written) converted NIF at `dst_path` (NiOverride High Heels reads
    it from the shape, not the file root). Returns True on success. No-op
    + False if our parser can't round-trip the file (caller should then fall back
    to the original mesh). Must be called AFTER all pynifly saves of dst_path."""
    try:
        data = open(dst_path, "rb").read()
    except OSError:
        return False
    p = _parse_if_lossless(data)
    if p is None:
        return False
    # string
    if b"HH_OFFSET" in p["strings"]:
        s_idx = p["strings"].index(b"HH_OFFSET")
    else:
        s_idx = len(p["strings"])
        p["strings"].append(b"HH_OFFSET")
        p["max_str"] = max(p["max_str"], len(b"HH_OFFSET"))
    # block type
    if b"NiFloatExtraData" in p["block_types"]:
        t_idx = p["block_types"].index(b"NiFloatExtraData")
    else:
        t_idx = len(p["block_types"])
        p["block_types"].append(b"NiFloatExtraData")
    # new block (NiExtraData name int + float), appended -> index = old count
    new_idx = p["num_blocks"]
    p["blocks"].append(struct.pack("<i", s_idx) + struct.pack("<f", float(value)))
    p["bti"].append(t_idx)
    p["bsizes"].append(8)
    p["num_blocks"] += 1
    # Link the HH_OFFSET onto the armor's TRI-SHAPE, NOT the file root node.
    # NiOverride / RaceMenu High Heels reads HH_OFFSET from the BSTriShape, not the
    # root NiNode -- proven by diffing a WORKING original boot (HH_OFFSET sits on its
    # BSTriShape `boots`, extraDataList=[HH,BODYTRI]) against our earlier root-attached
    # transplant, which carried the right value (4.65) but the game ignored it (boot
    # clipped into the ground). All NiObjectNET blocks share the layout
    # Name(int@0), NumExtraData(uint@4), ExtraData list(int[]@8). Attach to the first
    # tri-shape; fall back to the root only if the NIF somehow has no shape.
    _SHAPE_TYPES = (b"BSTriShape", b"BSDynamicTriShape", b"BSSubIndexTriShape",
                    b"NiTriShape", b"NiTriStrips")
    target = 0
    for _bi, _ti in enumerate(p["bti"]):
        if p["block_types"][_ti] in _SHAPE_TYPES:
            target = _bi
            break
    rb = bytearray(p["blocks"][target])
    if len(rb) < 8:
        return False
    n_extra, = struct.unpack_from("<I", rb, 4)
    ins = 8 + 4 * n_extra
    if ins > len(rb):
        return False
    struct.pack_into("<I", rb, 4, n_extra + 1)
    rb[ins:ins] = struct.pack("<i", new_idx)
    p["blocks"][target] = bytes(rb)
    p["bsizes"][target] = len(p["blocks"][target])
    out = _serialize(p)
    # validate: must re-parse losslessly AND contain a readable HH_OFFSET float
    if _parse_if_lossless(out) is None or b"HH_OFFSET" not in out:
        return False
    open(dst_path, "wb").write(out)
    return True

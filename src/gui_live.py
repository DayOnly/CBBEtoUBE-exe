# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026 DayOnly
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""The Live steps tab: the newest piece as a large, slowly turning 3D view that
steps through the conversion by itself, a strip of the five steps under it,
and the pieces before it as small cards. #live-steps

Not interactive. The conversion's workers drop one record per piece into a
folder (see live_steps.py). This panel:

  * polls the folder on the Tk loop (one directory listing every POLL_MS);
  * hands new records to a LOADER thread, which reads the written NIF, draws
    the five small step pictures (live_steps.render_piece) and builds the 3D
    scenes (live_view3d.scenes_for);
  * has a VIEWER thread draw turntable frames of the newest piece at up to FPS
    frames a second, only while the tab is on screen;
  * shows the latest frame on the Tk loop. Nothing is rendered on the Tk loop
    and nothing in a conversion worker.

The large view holds each step for STEP_S seconds (FLAG_S for a step with a red
border) while the piece turns once every TURN_S seconds. A `_0` and `_1` of one
piece are one entry (the later one, which has seen the pair's body zap,
replaces the earlier). Records the panel has read are deleted; a backlog is
skipped down to the newest few.
"""
from __future__ import annotations

import math
import os
import queue
import re
import threading
import time
from pathlib import Path

MAX_PIECES = 4                 # the one on show plus three earlier cards
POLL_MS = 600
FRAME_MS = 66                  # Tk picks up a new frame this often
FPS = 15
TURN_S = 10.0
STEP_S = 6.0
FLAG_S = 10.0
STRIP_W, STRIP_H = 84, 112     # the step pictures under the large view
CARD_W = 60                    # earlier pieces: their picture shrunk to about this wide
FOLDER_NAME = "_live_steps"
LEGEND = ("Warp / Fit: blue = still, red = moved 4u or more.  Weights: colour = strongest bone, "
          "magenta = no weight.  Zap: skin = kept (it shows through the cloth), see-through = "
          "removed.  Grey = the UBE body.  Red border = check this step.")
BAD_COLOUR = "#d03a2f"


def row_key(path_text: str) -> str:
    """The entry a record belongs to: its output path without the weight suffix."""
    p = str(path_text or "")
    return re.sub(r"_[01](\.nif)?$", r"\1", p, flags=re.IGNORECASE).lower()


def clear_folder(folder) -> None:
    """Delete the records a previous run left (only this panel's own files)."""
    try:
        for e in os.scandir(folder):
            if e.name.endswith(".npz") or e.name.endswith(".tmp"):
                try:
                    os.unlink(e.path)
                except OSError:
                    pass
    except OSError:
        pass


def newest_records(folder, seen: "set[str]") -> "list[str]":
    """Record files not yet taken, oldest first (names start with a timestamp)."""
    try:
        names = sorted(e.name for e in os.scandir(folder)
                       if e.name.endswith(".npz") and e.name not in seen)
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names]


def step_at(elapsed: float, flags) -> "tuple[int, float]":
    """Which step the large view shows `elapsed` seconds into a piece, and how far
    into it: each step for STEP_S, a flagged one for FLAG_S, round and round."""
    durs = [FLAG_S if f else STEP_S for f in flags] or [STEP_S]
    t = elapsed % sum(durs)
    for i, d in enumerate(durs):
        if t < d:
            return i, t / d
        t -= d
    return len(durs) - 1, 1.0


def _load_job(path):
    """Read one record: the step pictures and the 3D scenes. On the loader thread."""
    from . import live_steps, live_view3d
    rec = live_steps.read_record(path)
    meta = rec["meta"]
    written, note = None, ""
    if str(meta.get("status", "")).startswith("converted") and meta.get("dst"):
        try:
            written = live_steps.read_written(meta["dst"])
        except Exception as e:                       # noqa: BLE001
            note = f"written file not readable: {type(e).__name__}"
    thumbs = live_steps.render_piece(rec, written, STRIP_W, STRIP_H)
    if note:
        for t in thumbs:
            if t["step"] in ("Weights", "Zap") and t["rgb"] is None:
                t["note"] = note
    scenes = {}
    if live_view3d.available():
        try:
            scenes = live_view3d.scenes_for(rec, written)
        except Exception:                            # noqa: BLE001 - the strip still shows
            scenes = {}
    return {"key": row_key(meta.get("dst") or meta.get("src")),
            "title": live_steps.piece_title(meta),
            "status": str(meta.get("status", "")),
            "thumbs": thumbs, "scenes": scenes, "t0": None}


class _Viewer:
    """Draws turntable frames of the piece on show, on its own thread."""

    def __init__(self):
        self.lock = threading.Lock()
        self.piece = None
        self.size = (440, 560)
        self.bg = (0.13, 0.12, 0.10)
        self.visible = False
        self.frame = None              # (seq, ppm bytes, step index)
        self.seq = 0
        self._wake = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="live-view").start()

    def show(self, piece):
        with self.lock:
            self.piece = piece
        self._wake.set()

    def _run(self):
        from . import live_view3d
        live_view3d.available()         # numpy and the DLL load here, not on Tk
        while True:
            with self.lock:
                piece, (w, h), bg, vis = self.piece, self.size, self.bg, self.visible
            if piece is None or not vis or not piece["scenes"]:
                self._wake.wait(0.25)
                self._wake.clear()
                continue
            t = time.monotonic()
            if piece["t0"] is None:
                piece["t0"] = t
            flags = [th["bad"] for th in piece["thumbs"]]
            i, _ = step_at(t - piece["t0"], flags)
            name = piece["thumbs"][i]["step"] if i < len(piece["thumbs"]) else ""
            scene = piece["scenes"].get(name)
            ppm = None
            if scene is not None:
                theta = 2 * math.pi * ((t - piece["t0"]) / TURN_S)
                try:
                    img = scene.render(theta, w, h, bg)
                    ppm = b"P6 %d %d 255\n" % (w, h) + img.tobytes()
                except Exception:                    # noqa: BLE001 - never kill the thread
                    ppm = None
            with self.lock:
                self.seq += 1
                self.frame = (self.seq, ppm, i)
            time.sleep(max(0.0, 1.0 / FPS - (time.monotonic() - t)))


class LivePanel:
    """The Live steps tab. Build once; `start(folder)` per run, `stop()` at its end."""

    def __init__(self, tk, ttk, parent, after, font=None):
        self.tk, self.ttk, self.after = tk, ttk, after
        self.folder = None
        self.active = False
        self.pieces: "list[dict]" = []          # newest first
        self.palette = None
        self._seen: "set[str]" = set()
        self._jobs: "queue.Queue" = queue.Queue()
        self._done: "queue.Queue" = queue.Queue()
        self._loader = None
        self._viewer = None
        self._images = []
        self._strip_boxes = []
        self._poll_id = None
        self._frame_id = None
        self._idle_polls = 0
        self._shown_seq = 0
        self._shown_step = -1
        self.font = font

        f = self.frame = ttk.Frame(parent)
        f.pack(fill="both", expand=True, padx=8, pady=8)
        f.columnconfigure(0, weight=0)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(0, weight=1)
        left = self.left = ttk.Frame(f)
        left.grid(row=0, column=0, sticky="nsew")
        self.hero_box = tk.Frame(left, bd=0, highlightthickness=1)
        self.hero_box.pack(anchor="nw")
        self.hero_img = tk.PhotoImage(width=440, height=560)
        self.hero = tk.Label(self.hero_box, image=self.hero_img, bd=0)
        self.hero.pack()
        self.strip = ttk.Frame(left)
        self.strip.pack(anchor="nw", pady=(8, 0))
        right = ttk.Frame(f)
        right.grid(row=0, column=1, sticky="nsew", padx=(16, 0))
        self.title = ttk.Label(right, text="Live steps", font=font)
        self.title.pack(anchor="w")
        self.step_name = ttk.Label(right, text="", font=(font[0], 18) if font else None)
        self.step_name.pack(anchor="w", pady=(10, 0))
        self.step_note = ttk.Label(right, text="Pictures appear here while a conversion runs.",
                                   style="Hint.TLabel", wraplength=360, justify="left")
        self.step_note.pack(anchor="w", pady=(2, 12))
        self.legend = ttk.Label(right, text=LEGEND, style="Hint.TLabel", wraplength=360,
                                justify="left")
        self.legend.pack(anchor="w")
        self.cards_head = ttk.Label(right, text="", font=font)
        self.cards_head.pack(anchor="w", pady=(16, 4))
        self.cards = ttk.Frame(right)
        self.cards.pack(anchor="w", fill="x")
        f.bind("<Configure>", self._resized, add="+")

    # ---- run lifecycle (Tk thread) ----------------------------------------
    def start(self, folder) -> None:
        self.folder = str(folder)
        Path(self.folder).mkdir(parents=True, exist_ok=True)
        clear_folder(self.folder)
        self._seen.clear()
        self.pieces = []
        self.active = True
        self._idle_polls = 0
        self.step_note.configure(text="Waiting for the first piece...")
        self._redraw()
        if self._loader is None or not self._loader.is_alive():
            self._loader = threading.Thread(target=self._load_loop, daemon=True,
                                            name="live-steps")
            self._loader.start()
        if self._viewer is None:
            self._viewer = _Viewer()
            self._resized()
            self._push_theme()
        self._schedule()
        self._schedule_frame()

    def stop(self) -> None:
        """The run ended: keep polling a few more times for the last records. The
        view keeps turning on the last piece."""
        self.active = False
        self._idle_polls = 0

    def set_off(self, off: bool) -> None:
        if not self.pieces:
            self.step_note.configure(
                text="Live steps is off (Diagnostics > Window)." if off
                else "Pictures appear here while a conversion runs.")

    def _schedule(self):
        if self._poll_id is None:
            self._poll_id = self.after(POLL_MS, self._poll)

    def _schedule_frame(self):
        if self._frame_id is None:
            self._frame_id = self.after(FRAME_MS, self._show_frame)

    def _poll(self):
        self._poll_id = None
        try:
            if not self.frame.winfo_exists():
                return
        except Exception:
            return
        new = newest_records(self.folder, self._seen) if self.folder else []
        for p in new:
            self._seen.add(os.path.basename(p))
        if new:
            self._jobs.put(new)
        changed = False
        while True:
            try:
                piece = self._done.get_nowait()
            except queue.Empty:
                break
            self.pieces = [r for r in self.pieces if r["key"] != piece["key"]]
            self.pieces.insert(0, piece)
            del self.pieces[MAX_PIECES:]
            changed = True
        if changed:
            self._shown_step = -1
            if self._viewer is not None:
                self._viewer.show(self.pieces[0])
            self._redraw()
        if self.active or self._idle_polls < 8 or not self._jobs.empty():
            if not self.active:
                self._idle_polls += 1
            self._schedule()

    def _show_frame(self):
        self._frame_id = None
        try:
            if not self.frame.winfo_exists():
                return
            vis = bool(self.frame.winfo_ismapped()) and \
                self.frame.winfo_toplevel().state() != "iconic"
        except Exception:
            return
        v = self._viewer
        if v is not None:
            with v.lock:
                v.visible = vis
                fr = v.frame
            if vis and fr is not None and fr[0] != self._shown_seq:
                self._shown_seq = fr[0]
                if fr[1] is not None:
                    try:
                        self.hero_img.configure(data=fr[1], format="PPM")
                    except Exception:
                        pass
                if fr[2] != self._shown_step:
                    self._shown_step = fr[2]
                    self._mark_step(fr[2])
        self._schedule_frame()

    # ---- the loader thread ---------------------------------------------------
    def _load_loop(self):
        while True:
            batch = self._jobs.get()
            while True:                       # coalesce: newest few only
                try:
                    batch += self._jobs.get_nowait()
                except queue.Empty:
                    break
            # A pair is two records for one piece, so twice the pieces shown.
            keep = batch[-2 * MAX_PIECES:]
            for p in batch[:-2 * MAX_PIECES]:
                self._drop(p)
            for p in keep:                    # oldest first, the newest lands on top
                try:
                    piece = _load_job(p)
                except Exception as e:        # noqa: BLE001 - never kill the thread
                    piece = {"key": p, "title": Path(p).name, "status": "error",
                             "thumbs": [], "scenes": {}, "t0": None,
                             "error": f"{type(e).__name__}: {e}"}
                self._drop(p)
                self._done.put(piece)

    @staticmethod
    def _drop(p):
        try:
            os.unlink(p)
        except OSError:
            pass

    # ---- size and theme -------------------------------------------------------
    def _hero_size(self):
        try:
            h = self.frame.winfo_height()
        except Exception:
            h = 0
        h = max(320, min(900, h - STRIP_H - 70))
        h -= h % 20                                    # fewer rebuilds while dragging
        return int(h * 0.78) // 2 * 2, h

    def _resized(self, _e=None):
        w, h = self._hero_size()
        if self._viewer is not None:
            with self._viewer.lock:
                self._viewer.size = (w, h)
        try:
            if (self.hero_img.width(), self.hero_img.height()) != (w, h):
                self.hero_img.configure(width=w, height=h)
                self._paint_blank()
        except Exception:
            pass

    def _push_theme(self):
        p = self.palette or {}
        if self._viewer is not None and p.get("logbg"):
            bg = tuple(int(p["logbg"][i:i + 2], 16) / 255.0 for i in (1, 3, 5))
            with self._viewer.lock:
                self._viewer.bg = bg

    def _paint_blank(self):
        p = self.palette or {"logbg": "#201e1a"}
        try:
            self.hero_img.put(p["logbg"], to=(0, 0, self.hero_img.width(), self.hero_img.height()))
        except Exception:
            pass

    def apply_theme(self, palette) -> None:
        self.palette = dict(palette)
        p = self.palette
        try:
            self.hero_box.configure(bg=p["border"], highlightbackground=p["border"])
            self.hero.configure(bg=p["logbg"])
        except Exception:
            pass
        self._push_theme()
        if not self.pieces:
            self._paint_blank()
        self._redraw()

    # ---- drawing (Tk thread) -------------------------------------------------
    def _mark_step(self, i):
        p = self.palette or {"border": "#5c5445", "accent": "#c9a24b"}
        if not self.pieces:
            return
        piece = self.pieces[0]
        th = piece["thumbs"]
        for k, box in enumerate(self._strip_boxes):
            bad = k < len(th) and th[k]["bad"]
            box.configure(bg=BAD_COLOUR if bad else (p["accent"] if k == i else p["border"]),
                          padx=3 if k == i else 1, pady=3 if k == i else 1)
        if i < len(th):
            self.step_name.configure(text=th[i]["step"])
            self.step_note.configure(text=th[i]["note"] or "")

    def _photo(self, thumb, bg):
        from . import live_steps
        ppm = live_steps.compose(thumb, bg) if thumb.get("rgb") is not None else None
        if ppm is None:
            img = self.tk.PhotoImage(width=thumb["mask"].shape[1] if thumb.get("mask") is not None
                                     else STRIP_W, height=STRIP_H)
            img.put(bg, to=(0, 0, img.width(), img.height()))
        else:
            img = self.tk.PhotoImage(data=ppm, format="PPM")
        self._images.append(img)
        return img

    def _redraw(self):
        tk, ttk = self.tk, self.ttk
        p = self.palette or {"bg": "#262421", "border": "#5c5445", "logbg": "#201e1a",
                             "accent": "#c9a24b"}
        for w in list(self.strip.winfo_children()) + list(self.cards.winfo_children()):
            w.destroy()
        self._images = []
        self._strip_boxes = []
        if not self.pieces:
            self.title.configure(text="Live steps")
            self.step_name.configure(text="")
            self.cards_head.configure(text="")
            return
        cur = self.pieces[0]
        self.title.configure(text=cur["title"] + ("" if cur["status"].startswith("converted")
                                                  else f"  ({cur['status']})"))
        if cur.get("error"):
            self.step_note.configure(text="could not draw: " + cur["error"])
        elif not cur["scenes"]:
            self.step_note.configure(text="The large view needs native/liveview.dll; "
                                          "the step pictures below still show.")
        for i, t in enumerate(cur["thumbs"]):
            cell = ttk.Frame(self.strip)
            cell.grid(row=0, column=i, padx=(0, 4), sticky="n")
            box = tk.Frame(cell, bg=BAD_COLOUR if t["bad"] else p["border"], padx=1, pady=1)
            box.pack()
            tk.Label(box, image=self._photo(t, p["logbg"]), bd=0, bg=p["logbg"]).pack()
            ttk.Label(cell, text=t["step"], style="Hint.TLabel").pack(anchor="w")
            self._strip_boxes.append(box)
        self._mark_step(max(self._shown_step, 0))
        earlier = self.pieces[1:]
        self.cards_head.configure(text="Earlier pieces" if earlier else "")
        for piece in earlier:
            card = ttk.Frame(self.cards)
            card.pack(anchor="w", fill="x", pady=(0, 6))
            shot = next((t for t in reversed(piece["thumbs"]) if t.get("rgb") is not None), None)
            flags = sum(1 for t in piece["thumbs"] if t["bad"])
            box = tk.Frame(card, bg=BAD_COLOUR if flags else p["border"], padx=1, pady=1)
            box.pack(side="left")
            if shot is not None:
                img = self._photo(shot, p["logbg"])
                small = img.subsample(max(1, img.width() // CARD_W))
                self._images.append(small)
                tk.Label(box, image=small, bd=0, bg=p["logbg"]).pack()
            txt = piece["title"] + ("\n" + (f"{flags} step(s) to check" if flags else "no flags"))
            ttk.Label(card, text=txt, style="Hint.TLabel", justify="left").pack(
                side="left", padx=(8, 0), anchor="n")

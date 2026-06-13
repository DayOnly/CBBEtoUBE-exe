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

"""Optional Tkinter GUI for the CBBE/3BA -> UBE converter (the `gui` subcommand).

Thin + additive: it drives the SAME `auto_convert.main(["auto", ...])` pipeline
the CLI uses, on a BACKGROUND THREAD so the window stays responsive, streaming
the pipeline's stdout into a scrolling log via a thread-safe queue. The
conversion logic is unchanged; this is purely a front-end.

Two modes (radio):
  * "Convert all"  -> the normal full `auto` run (every armor mod).
  * "Convert selected mods" -> reconvert ONLY the ticked mods (fast), then the
    pipeline re-merges ALL patches in _unmerged_patches/ into the Combined ESP,
    so untouched mods keep their existing patch + meshes. Passes `--only-mods`.
    The mod checklist is populated by `auto_convert.list_convertible_mods()`
    (same discovery as `auto`, so the names match what `--only-mods` expects).

Threading model (important):
  * The converter is long-running and fans out across a ProcessPoolExecutor.
    Running it on the Tk main loop would freeze the window, so it runs on a
    daemon worker thread. Mod-list discovery (seconds) also runs on a daemon
    thread; both ENQUEUE results and update widgets only on the main thread.
  * Tk is NOT thread-safe: worker threads only ever ENQUEUE text + a final
    (DONE, exit_code) sentinel, or schedule a main-thread callback via
    root.after(0, ...). ALL widget updates happen on the main thread.
  * freeze_support() (in cbbe_to_ube_main, called first) still intercepts the
    ProcessPoolExecutor worker re-launches, so pool workers never reach the GUI.

tkinter is imported lazily INSIDE launch_gui(), so importing this module is
side-effect-free (no window) -- safe for tests / static analysis.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
from pathlib import Path

_DONE = object()  # sentinel; the worker pushes (_DONE, exit_code) when finished


def mod_name_matches(name: str, query: str) -> bool:
    """Case-insensitive, multi-token AND match for the checklist Filter box:
    every whitespace-separated token in `query` must appear in `name` (so
    "ruby fl" matches "DDV - Ruby Flower"). Empty query matches everything.
    Module-level (not a closure) so the filter contract is unit-testable."""
    q = query.strip().lower()
    if not q:
        return True
    low = name.lower()
    return all(tok in low for tok in q.split())


class _QueueWriter:
    """Write-through stream: enqueues every chunk for the UI log AND forwards to
    the original stream so the console / log-file tee keeps working. Unknown
    attribute access (isatty, fileno, encoding, ...) delegates to the wrapped
    stream so callers can't tell the difference."""

    def __init__(self, q: "queue.Queue", orig):
        self._q = q
        self._orig = orig

    def write(self, s):
        if s:
            self._q.put(s)
        try:
            if self._orig is not None:
                self._orig.write(s)
        except Exception:
            pass
        return len(s) if s else 0

    def flush(self):
        try:
            if self._orig is not None:
                self._orig.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._orig, name)


def launch_gui(argv=None, auto_close_ms=None) -> int:
    # auto_close_ms: smoke-test hook -- when set, the window auto-destroys after
    # that many ms (verifies the whole UI constructs + renders without error,
    # no human needed). None = normal interactive run.
    import tkinter as tk
    from tkinter import ttk, scrolledtext, filedialog, messagebox
    from . import auto_convert
    from . import paths as _paths

    root = tk.Tk()
    root.title("CBBE/3BA to UBE Converter")
    root.geometry("860x680")
    root.minsize(680, 520)

    q: "queue.Queue" = queue.Queue()
    state = {"running": False, "result": None, "output_dir": None}

    try:
        mods_root = _paths.mods_root()
    except Exception:
        mods_root = None
    default_out = str(mods_root / "CBBEtoUBE Auto") if mods_root else ""

    # ---- tk vars ----
    out_var = tk.StringVar(value=default_out)
    workers_var = tk.IntVar(value=max(1, (os.cpu_count() or 2) - 1))
    copy_tex = tk.BooleanVar(value=False)  # default: resolve textures via VFS
    skip_van = tk.BooleanVar(value=False)
    skip_vanbody = tk.BooleanVar(value=False)
    dry = tk.BooleanVar(value=False)
    mode = tk.StringVar(value="all")            # "all" | "selected"
    force_vanilla = tk.BooleanVar(value=False)
    convert_overlays = tk.BooleanVar(value=False)   # rebake body overlays to UBE
    overlays_only = tk.BooleanVar(value=False)      # ONLY overlays, skip armor
    mod_vars: "dict[str, tk.BooleanVar]" = {}    # mod name -> checkbox var (PERSISTS across filtering)
    mod_checkboxes: list = []                    # Checkbutton widgets (toggled during a run)
    mod_cbs: "dict[str, object]" = {}            # mod name -> its Checkbutton (for show/hide on filter)
    mod_items_all: list = []                     # full unfiltered scan result (master order)
    search_var = tk.StringVar()                  # live filter text for the checklist

    # ---- mode/selection helpers (defined early; reference widgets created
    # below -- resolved at CALL time, never during construction) ----
    def _on_mode():
        if mode.get() == "selected":
            mods_box.pack(fill="x", padx=8, pady=4, before=bar)
        else:
            mods_box.pack_forget()

    _matches = mod_name_matches   # module-level matcher (unit-tested)

    def _update_title():
        # Reflect filter/selection state in the frame label. Skipped during a run
        # (the label is commandeered to show "selection locked").
        if state["running"]:
            return
        total = len(mod_items_all)
        if total == 0:
            txt = "Mods to reconvert (Refresh, then tick)"
        else:
            q = search_var.get()
            shown = sum(1 for it in mod_items_all if _matches(it["name"], q))
            ticked = sum(1 for v in mod_vars.values() if v.get())
            hidden_ticked = sum(
                1 for it in mod_items_all
                if mod_vars.get(it["name"]) and mod_vars[it["name"]].get()
                and not _matches(it["name"], q))
            txt = f"Mods to reconvert: {shown}/{total} shown, {ticked} ticked"
            if hidden_ticked:
                txt += f"  ({hidden_ticked} ticked but hidden by filter)"
        try:
            mods_box.configure(text=txt)
        except Exception:
            pass

    def _apply_filter():
        # Show/hide checkbuttons to match the filter WITHOUT touching mod_vars,
        # so ticks survive filtering. Re-pack matches in master order.
        q = search_var.get()
        for cb in mod_cbs.values():
            cb.pack_forget()
        for it in mod_items_all:
            if _matches(it["name"], q):
                mod_cbs[it["name"]].pack(anchor="w")
        try:
            _canvas.configure(scrollregion=_canvas.bbox("all"))
            _canvas.yview_moveto(0.0)
        except Exception:
            pass
        _update_title()

    def _set_all(val):
        # All/None act on the VISIBLE (filtered) set only -- so "kco" + All ticks
        # just that family. With an empty filter this is every mod, as before.
        q = search_var.get()
        for it in mod_items_all:
            name = it["name"]
            if _matches(name, q) and name in mod_vars:
                mod_vars[name].set(val)
        _update_title()

    def _populate_mods(items):
        if state["running"]:
            return     # a Refresh finished mid-run -> don't clobber "Converting..."
        for w in _inner.winfo_children():
            w.destroy()
        mod_vars.clear()
        mod_checkboxes.clear()
        mod_cbs.clear()
        mod_items_all[:] = items
        for it in items:
            v = tk.BooleanVar(value=False)
            mod_vars[it["name"]] = v
            cb = ttk.Checkbutton(_inner, variable=v, command=_update_title,
                                 text=f'{it["name"]}  ({it["nifs"]} nif)')
            mod_cbs[it["name"]] = cb
            mod_checkboxes.append(cb)
        _apply_filter()   # packs matches in order + sets scrollregion + title
        if items:
            status.set(f"{len(items)} convertible mods found. "
                       "Tick the ones to reconvert (or type in Filter to narrow).")
        else:
            status.set("No convertible mods found (or layout not detected).")

    def _refresh_mods():
        status.set("Scanning mods (this can take a few seconds)...")

        def work():
            try:
                od = out_var.get().strip()
                items = auto_convert.list_convertible_mods(
                    Path(od) if od else None)
            except Exception as e:
                items = []
                q.put(f"\n[mod scan failed: {e}]\n")
            root.after(0, lambda: _populate_mods(items))

        threading.Thread(target=work, daemon=True).start()

    # ---- options ----
    cfg = ttk.LabelFrame(root, text="Options")
    cfg.pack(fill="x", padx=8, pady=(8, 4))
    cfg.columnconfigure(1, weight=1)

    ttk.Label(cfg, text="Output mod folder:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
    ttk.Entry(cfg, textvariable=out_var).grid(row=0, column=1, sticky="we", padx=4)

    def _browse():
        d = filedialog.askdirectory(initialdir=out_var.get() or ".")
        if d:
            out_var.set(d)

    ttk.Button(cfg, text="Browse...", command=_browse).grid(row=0, column=2, padx=4)

    ttk.Label(cfg, text="Worker processes:").grid(row=1, column=0, sticky="w", padx=4, pady=4)
    ttk.Spinbox(cfg, from_=1, to=(os.cpu_count() or 64),
                textvariable=workers_var, width=6).grid(row=1, column=1, sticky="w", padx=4)

    ttk.Checkbutton(cfg, text="Copy textures into output (default: resolve via VFS)",
                    variable=copy_tex).grid(row=2, column=1, sticky="w", padx=4)
    ttk.Checkbutton(cfg, text="Skip vanilla race coverage patch (full run only)",
                    variable=skip_van).grid(row=3, column=1, sticky="w", padx=4)
    ttk.Checkbutton(cfg, text="Skip standalone vanilla body conversion (full run only)",
                    variable=skip_vanbody).grid(row=4, column=1, sticky="w", padx=4)
    ttk.Checkbutton(cfg, text="Dry run (list the mods that WOULD convert, then stop)",
                    variable=dry).grid(row=5, column=1, sticky="w", padx=4)

    ttk.Label(cfg, text="Mode:").grid(row=6, column=0, sticky="w", padx=4, pady=4)
    mframe = ttk.Frame(cfg)
    mframe.grid(row=6, column=1, sticky="w")
    ttk.Radiobutton(mframe, text="Convert all", value="all",
                    variable=mode, command=_on_mode).pack(side="left")
    ttk.Radiobutton(mframe, text="Convert selected mods", value="selected",
                    variable=mode, command=_on_mode).pack(side="left", padx=8)
    ttk.Checkbutton(cfg, text="Also refresh vanilla coverage on selected run (slower)",
                    variable=force_vanilla).grid(row=7, column=1, sticky="w", padx=4)
    ttk.Checkbutton(cfg, text="Convert body overlays (tattoos / body paints) to UBE",
                    variable=convert_overlays).grid(row=8, column=1, sticky="w", padx=4)
    ttk.Checkbutton(cfg, text="    └ Overlays ONLY (skip the armor reconvert)",
                    variable=overlays_only).grid(row=9, column=1, sticky="w", padx=4)

    # ---- action bar + progress ----
    bar = ttk.Frame(root)
    bar.pack(fill="x", padx=8, pady=4)
    run_btn = ttk.Button(bar, text="Convert")
    run_btn.pack(side="left")
    open_out_btn = ttk.Button(bar, text="Open output folder", state="disabled")
    open_out_btn.pack(side="left", padx=4)
    open_rep_btn = ttk.Button(bar, text="Open report", state="disabled")
    open_rep_btn.pack(side="left", padx=4)

    # ---- selected-mods checklist (shown only in "selected" mode) ----
    mods_box = ttk.LabelFrame(root, text="Mods to reconvert (Refresh, then tick)")
    _topbar = ttk.Frame(mods_box)
    _topbar.pack(fill="x")
    refresh_btn = ttk.Button(_topbar, text="Refresh mod list", command=_refresh_mods)
    refresh_btn.pack(side="left", padx=4, pady=4)
    all_btn = ttk.Button(_topbar, text="All", width=4, command=lambda: _set_all(True))
    all_btn.pack(side="left")
    none_btn = ttk.Button(_topbar, text="None", width=5, command=lambda: _set_all(False))
    none_btn.pack(side="left", padx=(0, 4))
    ttk.Label(_topbar, text="Filter:").pack(side="left", padx=(8, 2))
    search_entry = ttk.Entry(_topbar, textvariable=search_var, width=20)
    search_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
    search_entry.bind("<Escape>", lambda e: search_var.set(""))   # Esc clears
    # Live filter as the user types. All/None then act on the visible subset.
    search_var.trace_add("write", lambda *a: _apply_filter())
    sel_widgets = [refresh_btn, all_btn, none_btn, search_entry]   # locked while a run is active
    _cwrap = ttk.Frame(mods_box)
    _cwrap.pack(fill="both", expand=True)
    _canvas = tk.Canvas(_cwrap, height=150, highlightthickness=0)
    _sb = ttk.Scrollbar(_cwrap, orient="vertical", command=_canvas.yview)
    _inner = ttk.Frame(_canvas)
    _inner.bind("<Configure>",
                lambda e: _canvas.configure(scrollregion=_canvas.bbox("all")))
    _canvas.create_window((0, 0), window=_inner, anchor="nw")
    _canvas.configure(yscrollcommand=_sb.set)
    _canvas.pack(side="left", fill="both", expand=True)
    _sb.pack(side="right", fill="y")

    prog = ttk.Progressbar(root, mode="indeterminate")
    prog.pack(fill="x", padx=8, pady=2)
    status = tk.StringVar(value="Idle. Pick options and press Convert.")
    ttk.Label(root, textvariable=status).pack(anchor="w", padx=8)

    log = scrolledtext.ScrolledText(root, height=20, wrap="word",
                                    state="disabled", font=("Consolas", 9))
    log.pack(fill="both", expand=True, padx=8, pady=(4, 8))

    def _append(s):
        log.configure(state="normal")
        log.insert("end", s)
        log.see("end")
        log.configure(state="disabled")

    def _open_path(p):
        try:
            os.startfile(str(p))    # Windows shell open
        except Exception as e:
            _append(f"\n[could not open {p}: {e}]\n")

    def _build_argv():
        a = ["auto"]
        if out_var.get().strip():
            a += ["-o", out_var.get().strip()]
        a += ["--workers", str(int(workers_var.get()))]
        if copy_tex.get():
            a.append("--copy-textures")
        if dry.get():
            a.append("--list-only")
        if mode.get() == "selected":
            # Reconvert only the ticked mods; _cmd_auto auto-skips the vanilla
            # coverage steps for an --only-mods run unless --force-vanilla.
            for name, v in mod_vars.items():
                if v.get():
                    a += ["--only-mods", name]
            if force_vanilla.get():
                a.append("--force-vanilla")
        else:
            if skip_van.get():
                a.append("--no-vanilla-compat")
            if skip_vanbody.get():
                a.append("--no-vanilla-bodies")
        # Overlay transfer (independent of mode). --overlays-only wins if both
        # ticked (it early-returns before the armor work).
        if overlays_only.get():
            a.append("--overlays-only")
        elif convert_overlays.get():
            a.append("--convert-overlays")
        return a

    def _worker(argv_run):
        orig_out, orig_err = sys.stdout, sys.stderr
        rc = 1
        try:
            sys.stdout = _QueueWriter(q, orig_out)
            sys.stderr = _QueueWriter(q, orig_err)
            rc = auto_convert.main(argv_run)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
        except Exception:
            import traceback
            q.put("\n*** conversion crashed ***\n" + traceback.format_exc())
            rc = 1
        finally:
            sys.stdout, sys.stderr = orig_out, orig_err
            q.put((_DONE, rc))

    def _start():
        if state["running"]:
            return
        if mode.get() == "selected" and not any(v.get() for v in mod_vars.values()):
            messagebox.showinfo(
                "Pick mods",
                "Tick at least one mod to reconvert, or switch to 'Convert all'.\n"
                "(Use 'Refresh mod list' if the list is empty.)")
            return
        state["running"] = True
        state["output_dir"] = out_var.get().strip() or default_out
        run_btn.configure(state="disabled")
        open_out_btn.configure(state="disabled")
        open_rep_btn.configure(state="disabled")
        # Lock the selection UI: stop a mid-run Refresh from starting + flip the
        # "...then tick" frame label so it doesn't linger while converting.
        try:
            mods_box.configure(text="Mods (conversion running - selection locked)")
        except Exception:
            pass
        for w in sel_widgets + mod_checkboxes:
            try:
                w.configure(state="disabled")
            except Exception:
                pass
        prog.start(12)
        status.set("Converting... this can take many minutes; the window stays responsive.")
        argv_run = _build_argv()
        _append("> CBBEtoUBE " + " ".join(argv_run) + "\n\n")
        # Don't let the frozen entry pause for a keypress after the GUI closes.
        os.environ["CBBE2UBE_NO_PAUSE"] = "1"
        threading.Thread(target=_worker, args=(argv_run,), daemon=True).start()

    run_btn.configure(command=_start)

    def _finish(rc):
        state["running"] = False
        state["result"] = rc
        prog.stop()
        run_btn.configure(state="normal")
        for w in sel_widgets + mod_checkboxes:
            try:
                w.configure(state="normal")
            except Exception:
                pass
        _update_title()   # restore the "{shown}/{total} shown, {ticked} ticked" label
        od = state.get("output_dir")
        if od and Path(od).is_dir():
            open_out_btn.configure(state="normal", command=lambda p=od: _open_path(p))
            rep = Path(od) / "conversion_summary.txt"
            if rep.is_file():
                open_rep_btn.configure(state="normal", command=lambda p=rep: _open_path(p))
        if rc == 0:
            status.set("Done - success (exit 0). Review the log + report for any coverage notes.")
        else:
            status.set(f"Finished with exit code {rc} - check the log for errors/warnings.")
        _append(f"\n=== finished (exit {rc}) ===\n")

    def _poll():
        try:
            while True:
                item = q.get_nowait()
                if isinstance(item, tuple) and item and item[0] is _DONE:
                    _finish(item[1])
                else:
                    _append(item)
        except queue.Empty:
            pass
        root.after(120, _poll)

    def _on_close():
        if state["running"] and not messagebox.askokcancel(
                "Quit", "A conversion is still running. Quit anyway?\n"
                "(Worker processes may keep running briefly.)"):
            return
        root.destroy()

    _on_mode()  # set initial mods-box visibility for the default mode ("all")
    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.after(120, _poll)
    if auto_close_ms:
        root.after(int(auto_close_ms), root.destroy)  # smoke-test self-close
    root.mainloop()
    return int(state.get("result") or 0)

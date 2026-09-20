"""The mutation pairs scripts/mutation_gate.py runs. #mutation-gate

Each pair mutates one guarded thing in a worktree and names the tests that
must go red. They are the pairs that were measured by hand when each guard
landed -- the hardening plan's lens E (2026-09-15) for the 1.4.1 fixes, and
the 1.4.2 / 1.5 items since -- with the anchors as they stand in this tree;
tests/test_mutation_gate.py checks that every anchor still matches its
declared count and every expected test id exists, so a refactor that moves
an anchor is caught at the next suite run rather than at the next release.

A pair that `needs` a display is judged only where one exists; the gate
reports it as NOT_JUDGED elsewhere rather than as caught or missed.

Generated once from the measuring drivers (every anchor by repr, so what the
gate applies is byte for byte what was measured); edited by hand since."""
from scripts.mutation_gate import Pair

PAIRS = (
    # #covered-skin-target (2026-09-17): the pure target, its reach test and the
    # switch's default. The wiring itself is proven by the exe A/B on the
    # reported mod, not by a unit test (it needs the live UBE body).
    Pair('CST-a', 'the covered-skin target ignores how close each skin vertex is',
         edits=(
             ('src/nif_convert_weights.py', 'wgt = 1.0 / (max(c, 0.0) + eps)', 'wgt = 1.0  # MUTATED: uniform', 1),
         ),
         tests=('tests/test_covered_skin_target.py',),
         expect=('test_a_gusset_over_static_cleft_skin_targets_the_pelvis', 'test_a_vertex_over_thigh_skin_keeps_the_thigh'),
    ),
    Pair('CST-b', 'the covered-skin target ships switched OFF',
         edits=(
             ('src/nif_convert.py', 'COVERED_SKIN_TARGET = (not _flag("CBBE2UBE_NO_COVERED_SKIN_TARGET", False))', 'COVERED_SKIN_TARGET = (_flag("CBBE2UBE_NO_COVERED_SKIN_TARGET", False))  # MUTATED', 1),
         ),
         tests=('tests/test_covered_skin_target.py',),
         expect=('test_the_switch_defaults_on_and_the_env_turns_it_off',),
    ),
    Pair('CST-e', 'the body-swap reskin ignores the covered-skin target',
         edits=(
             ('src/nif_convert_weights.py', 'propagated[_gi] = float((body_arr[_bis] * _w).sum())', 'pass  # MUTATED: nearest only', 1),
         ),
         tests=('tests/test_covered_skin_target.py',),
         expect=('test_the_reskin_aims_a_covering_vertex_at_the_skin_it_covers',),
    ),
    Pair('CST-d', 'the fitted-cloth conform ignores the covered-skin target',
         edits=(
             ('src/nif_convert_weights.py', 'bd = _cov_target.get(i) or body_w[idx[i]]', 'bd = body_w[idx[i]]  # MUTATED: nearest only', 1),
         ),
         tests=('tests/test_covered_skin_target.py',),
         expect=('test_the_conform_core_aims_at_the_covered_skin_when_it_has_one',),
    ),
    Pair('CST-c', 'the cover map charges a garment vertex with skin any distance away',
         edits=(
             ('src/nif_convert_weights.py', 'if dist > reach:', 'if False:  # MUTATED: no reach', 1),
         ),
         tests=('tests/test_covered_skin_target.py',),
         expect=('test_the_map_pairs_each_body_vertex_with_its_nearest_garment_vertex_within_reach',),
    ),
    # The lens measured this pair with two call sites, before B-7 (0d868e0,
    # 2026-09-15) moved the cap into src/__init__.py; the gate's first full run
    # found it MISSED (8 passed): the package now caps on any import, so the
    # two entry-point calls are redundant. All three sites, or nothing moves.
    Pair('E-a', 'the BLAS thread cap is never applied (all three call sites)',
         edits=(
             ('cbbe_to_ube_main.py', '\ncap_blas_threads()\n', '\npass  # MUTATED: cap removed\n', 1),
             ('src/auto_convert.py', '\ncap_blas_threads()\n', '\npass  # MUTATED: cap removed\n', 1),
             ('src/__init__.py', '\n_cap_blas_threads()\n', '\npass  # MUTATED: cap removed\n', 1),
         ),
         tests=('tests/test_blas_thread_cap.py',),
         expect=('test_a_direct_nif_convert_import_pays_no_arena', 'test_any_import_of_the_package_caps_before_numpy', 'test_importing_auto_convert_sets_every_thread_var', 'test_the_cap_actually_saves_commit_charge', 'test_the_frozen_entry_point_caps_at_module_level'),
    ),
    # B-7 alone: the entry points still cap; only the package-level cap is gone.
    Pair('E-a2', 'a direct import of the package no longer caps before numpy (B-7)',
         edits=(
             ('src/__init__.py', '\n_cap_blas_threads()\n', '\npass  # MUTATED: cap removed\n', 1),
         ),
         tests=('tests/test_blas_thread_cap.py',),
         expect=('test_any_import_of_the_package_caps_before_numpy', 'test_a_direct_nif_convert_import_pays_no_arena'),
    ),
    Pair('E-b', 'the run log is truncated instead of rotated aside',
         edits=(
             ('cbbe_to_ube_main.py', '    try:\n        if not os.path.exists(path):\n            return\n', '    return  # MUTATED: rotation disabled\n    try:\n        if not os.path.exists(path):\n            return\n', 1),
         ),
         tests=('tests/test_memory_death_is_reported.py',),
         expect=('test_both_launch_paths_rotate_to_the_same_filename', 'test_rotation_does_not_mangle_a_path_containing_the_extension', 'test_the_run_log_is_rotated_not_truncated'),
    ),
    Pair('E-c1', 'the failures file is never written',
         edits=(
             ('src/auto_convert.py', 'def _write_failures_file() -> None:\n    import json as _json\n', 'def _write_failures_file() -> None:\n    return  # MUTATED: never writes\n    import json as _json\n', 1),
         ),
         tests=('tests/test_memory_death_is_reported.py', 'tests/test_deterministic_output.py', 'tests/test_exe_parity_convert.py', 'tests/test_vanilla_sweep.py'),
         expect=('test_mod_failure_still_blocks_merge', 'test_sweep_failure_does_not_block_merge'),
    ),
    Pair('E-c2', 'a failures file that cannot be written is skipped in silence',
         edits=(
             ('src/auto_convert.py', '    except OSError as _e:\n        # THE FAILURE REPORT ITSELF.', '    except OSError as _e:\n        return  # MUTATED: silent\n        # THE FAILURE REPORT ITSELF.', 1),
         ),
         tests=('tests/test_memory_death_is_reported.py', 'tests/test_deterministic_output.py', 'tests/test_exe_parity_convert.py', 'tests/test_vanilla_sweep.py'),
         expect=('test_a_failures_file_that_cannot_be_written_is_said_out_loud',),
    ),
    Pair('E-d', 'the pool is rounded up and a worker priced at 0.5 GB of commit charge',
         edits=(
             ('src/auto_convert.py', '    n = max(1, min(cpu, int(gb / budget)))\n', '    n = max(1, min(cpu, int(gb / budget + 0.5)))  # MUTATED\n', 1),
             ('src/auto_convert.py', '\nWORKER_COMMIT_GB = 2.0\n', '\nWORKER_COMMIT_GB = 0.5  # MUTATED\n', 1),
         ),
         tests=('tests/test_worker_mem_budget.py', 'tests/test_sanitize_worker_budget.py'),
         expect=('test_a_disabled_page_file_shrinks_the_pool', 'test_a_healthy_page_file_does_not_change_anything', 'test_a_machine_that_cannot_report_commit_still_works', 'test_ram_caps_the_pool_below_cpu_count', 'test_the_guard_can_only_lower_never_raise', 'test_the_pool_never_exceeds_the_machines_own_ram'),
    ),
    Pair('E-d1', 'the pool is rounded up past what the machine can commit',
         edits=(
             ('src/auto_convert.py', '    n = max(1, min(cpu, int(gb / budget)))\n', '    n = max(1, min(cpu, int(gb / budget + 0.5)))  # MUTATED\n', 1),
         ),
         tests=('tests/test_worker_mem_budget.py', 'tests/test_sanitize_worker_budget.py'),
         expect=('test_a_healthy_page_file_does_not_change_anything', 'test_a_machine_that_cannot_report_commit_still_works', 'test_ram_caps_the_pool_below_cpu_count', 'test_the_guard_can_only_lower_never_raise', 'test_the_pool_never_exceeds_the_machines_own_ram'),
    ),
    Pair('E-d2', 'a worker is priced at 0.5 GB of commit charge',
         edits=(
             ('src/auto_convert.py', '\nWORKER_COMMIT_GB = 2.0\n', '\nWORKER_COMMIT_GB = 0.5  # MUTATED\n', 1),
         ),
         tests=('tests/test_worker_mem_budget.py', 'tests/test_sanitize_worker_budget.py'),
         expect=('test_a_disabled_page_file_shrinks_the_pool',),
    ),
    Pair('E-e', 'a dead worker in the end-of-run sweep is reported as nothing',
         edits=(
             ('src/nif_convert_writer.py', '            "shapes_fixed": shapes_fixed, "pool_error": pool_error}', '            "shapes_fixed": shapes_fixed}  # MUTATED: key dropped', 1),
             ('src/nif_convert_writer.py', '    except (BrokenProcessPool, MemoryError) as _me:\n', '    except (BrokenProcessPool, MemoryError) as _me:  # MUTATED\n        _me = None\n', 1),
         ),
         tests=('tests/test_memory_death_is_reported.py', 'tests/test_sanitize_worker_budget.py'),
         expect=('test_a_dead_worker_in_the_sweep_is_reported',),
    ),
    Pair('E-e2', "the sweep's pool error is read and ignored at its call site",
         edits=(
             ('src/auto_convert.py', '            if vc.get("pool_error"):\n', '            if vc.get("pool_error") and False:  # MUTATED\n', 1),
         ),
         tests=('tests/test_memory_death_is_reported.py', 'tests/test_sanitize_worker_budget.py', 'tests/test_vanilla_sweep.py'),
         expect=('test_a_dead_sweep_worker_reaches_the_log_the_tally_and_the_failures_file',),
    ),
    Pair('E-f', 'the asset-name scan matches nothing',
         edits=(
             ('scripts/repo_hygiene.py', '    if pattern is None:\n        return []\n    out = []\n    m = pattern.search(path.replace', '    return []  # MUTATED\n    if pattern is None:\n        return []\n    out = []\n    m = pattern.search(path.replace', 1),
         ),
         tests=('tests/test_public_repo_hygiene.py', 'tests/test_repo_hygiene_hooks.py'),
         expect=('test_a_bare_entry_cannot_see_a_name_inside_a_longer_identifier', 'test_the_denylist_check_can_actually_fail'),
    ),
    Pair('E-g', 'a negative tip value cannot be parsed by the acceptance gate',
         edits=(
             ('scripts/analysis/acceptance.py', '_NUMCOL = r"([+-]?\\d+(?:\\.\\d+)?)"', '_NUMCOL = r"(\\d+(?:\\.\\d+)?)"  # MUTATED', 1),
         ),
         tests=('tests/test_acceptance_gate.py', 'tests/test_acceptance_clip_row.py'),
         expect=('test_a_NEGATIVE_follow_cell_is_not_silently_dropped', 'test_a_NEGATIVE_tip_parses_at_all', 'test_the_tip_pattern_is_checked_against_the_printers_own_layout'),
    ),
    Pair('E-h', 'a test patches a moved callee on the nif_convert shim (planted file)',
         edits=(
             ('tests/test_zz_planted.py', None, 'from src import nif_convert as nc\ndef test_planted(monkeypatch):\n    monkeypatch.setattr(nc, "_reach_iters", lambda *a, **k: 1)\n', 0),
         ),
         tests=('tests/test_split_preconditions.py',),
         expect=('test_no_test_patches_a_moved_callee_on_nc',),
    ),
    Pair('F11-a', 'the gate drops the build-time clause',
         edits=(
             ('scripts/release_gate.py', '    if stamp is None:\n        add(FAIL, "build time", "no stamp commit to compare with")\n    else:\n        add(*_build_time_verdict(repo, stamp, build))\n\n', '', 1),
         ),
         tests=('tests/test_release_gate.py', 'tests/test_build_identity.py', 'tests/test_workflow_hardening.py'),
         expect=('test_a_build_from_before_the_epoch_rule_is_not_checked_not_failed', 'test_a_build_pinned_to_any_other_time_fails_build_time', 'test_a_build_pinned_to_its_commits_time_passes_build_time', 'test_a_correct_chain_passes'),
    ),
    Pair('F11-b', 'a build pinned to the wrong time passes',
         edits=(
             ('scripts/release_gate.py', '    if str(epoch) != commit_time:', '    if False and str(epoch) != commit_time:', 1),
         ),
         tests=('tests/test_release_gate.py', 'tests/test_build_identity.py', 'tests/test_workflow_hardening.py'),
         expect=('test_a_build_pinned_to_any_other_time_fails_build_time',),
    ),
    Pair('F11-c', 'rebuild-check never judges the exe',
         edits=(
             ('scripts/release_gate.py', '    if exe_rel in differ:\n        note = ""', '    if False:\n        note = ""', 1),
         ),
         tests=('tests/test_release_gate.py', 'tests/test_build_identity.py', 'tests/test_workflow_hardening.py'),
         expect=('test_a_rebuild_differing_in_what_source_or_lock_determine_fails[CBBEtoUBE.exe-exe]',),
    ),
    Pair('F11-d', 'interpreter-file differences are judged PASS',
         edits=(
             ('scripts/release_gate.py', '        verdicts.append((NOT_CHECKED, "interpreter files",', '        verdicts.append((PASS, "interpreter files",', 1),
         ),
         tests=('tests/test_release_gate.py', 'tests/test_build_identity.py', 'tests/test_workflow_hardening.py'),
         expect=('test_a_rebuild_on_another_cpython_build_is_reported_not_judged[_internal/_ctypes.pyd]', 'test_a_rebuild_on_another_cpython_build_is_reported_not_judged[_internal/base_library.zip]', 'test_a_rebuild_on_another_cpython_build_is_reported_not_judged[_internal/licenses/CPython-LICENSE.txt]', 'test_a_rebuild_on_another_cpython_build_is_reported_not_judged[_internal/python310.dll]', 'test_a_rebuild_on_another_cpython_build_is_reported_not_judged[_internal/tcl8/init.tcl]'),
    ),
    Pair('F11-e', 'program-file differences pass',
         edits=(
             ('scripts/release_gate.py', '    if program:\n        verdicts.append((FAIL, "program files",', '    if False:\n        verdicts.append((FAIL, "program files",', 1),
         ),
         tests=('tests/test_release_gate.py', 'tests/test_build_identity.py', 'tests/test_workflow_hardening.py'),
         expect=('test_a_rebuild_differing_in_what_source_or_lock_determine_fails[USING.md-program files]', 'test_a_rebuild_differing_in_what_source_or_lock_determine_fails[_internal/NiflyDLL.dll-program files]', 'test_a_rebuild_differing_in_what_source_or_lock_determine_fails[_internal/numpy/core.pyd-program files]'),
    ),
    Pair('F11-f', 'a forbidden marker that shipped passes',
         edits=(
             ('scripts/release_gate.py', '    shipped = [(m, where) for m in rules["absent"] for where in [_marker_found(m, info)] if where]', '    shipped = []', 1),
         ),
         tests=('tests/test_release_gate.py', 'tests/test_build_identity.py', 'tests/test_workflow_hardening.py'),
         expect=('test_a_forbidden_marker_that_shipped_fails_by_name',),
    ),
    Pair('F11-g', 'a claimed marker missing from the exe passes',
         edits=(
             ('scripts/release_gate.py', '        elif _marker_found(m, info):\n            claimed.append(m)\n        else:\n            missing.append(m)\n', '        else:\n            claimed.append(m)\n', 1),
         ),
         tests=('tests/test_release_gate.py', 'tests/test_build_identity.py', 'tests/test_workflow_hardening.py'),
         expect=('test_a_claimed_marker_missing_from_the_exe_fails_by_name_and_claim',),
    ),
    Pair('F11-h', 'the entry script is no longer scanned',
         edits=(
             ('scripts/release_gate.py', '    names.update({n: code_names(c) for n, c in read_entry_scripts(exe).items()})\n', '', 1),
         ),
         tests=('tests/test_release_gate.py', 'tests/test_build_identity.py', 'tests/test_workflow_hardening.py'),
         expect=('test_every_marker_holds_on_the_tracked_exe', 'test_the_inspector_reads_the_entry_script_as_well_as_the_pyz'),
    ),
    Pair('F11-i', 'later markers are checked as if claimed (since ignored)',
         edits=(
             ('scripts/release_gate.py', '        if have is not None and version_tuple(m["since"]) > have:', '        if False:', 1),
         ),
         tests=('tests/test_release_gate.py', 'tests/test_build_identity.py', 'tests/test_workflow_hardening.py'),
         expect=('test_a_marker_claimed_by_a_later_version_is_not_checked',),
    ),
    Pair('F11-n', 'a version that claims no marker passes on nothing',
         edits=(
             ('scripts/release_gate.py', '    elif not claimed:\n        first = min(later,', '    elif False:\n        first = min(later,', 1),
         ),
         tests=('tests/test_release_gate.py', 'tests/test_build_identity.py', 'tests/test_workflow_hardening.py'),
         expect=('test_a_version_that_claims_no_marker_is_not_checked_not_passed',),
    ),
    Pair('F11-j', 'an empty marker list is accepted',
         edits=(
             ('scripts/release_gate.py', '        if not isinstance(rows, list) or not rows:', '        if not isinstance(rows, list):', 1),
         ),
         tests=('tests/test_release_gate.py', 'tests/test_build_identity.py', 'tests/test_workflow_hardening.py'),
         expect=('test_a_marker_list_that_could_not_fail_is_refused[present0-absent0-nothing to find]', 'test_a_marker_list_that_could_not_fail_is_refused[present1-absent1-nothing to refuse]'),
    ),
    Pair('F11-k', 'the stamp stops recording the epoch',
         edits=(
             ('scripts/build_identity.py', '        "source_date_epoch": pinned,', '        "source_date_epoch": None,', 1),
         ),
         tests=('tests/test_release_gate.py', 'tests/test_build_identity.py', 'tests/test_workflow_hardening.py'),
         expect=('test_the_stamp_records_the_epoch_it_was_pinned_to',),
    ),
    Pair('F11-l', 'the build script no longer pins the epoch',
         edits=(
             ('scripts/build_exe.ps1', '        $env:SOURCE_DATE_EPOCH = $epoch\n', '', 1),
         ),
         tests=('tests/test_release_gate.py', 'tests/test_build_identity.py', 'tests/test_workflow_hardening.py'),
         expect=('test_the_build_script_pins_the_epoch_to_the_commit',),
    ),
    Pair('F11-m', 'the workflow loses the rebuild check',
         edits=(
             ('.github/workflows/release-gate.yml', '          python scripts/release_gate.py rebuild-check "$REF" dist/CBBEtoUBE\n', '', 1),
         ),
         tests=('tests/test_release_gate.py', 'tests/test_build_identity.py', 'tests/test_workflow_hardening.py'),
         expect=('test_the_release_workflow_rebuilds_the_exe_and_checks_the_result',),
    ),
    Pair('C07-a', 'nothing is written before the first source',
         edits=(
             ('src/auto_convert.py', '    _stamp_run_start(output, planned=len(sources), workers=_planned_workers,\n                     orphan_temps_removed=_orphans_removed)\n    results = []\n', '    results = []\n', 1),
         ),
         tests=('tests/test_report_checkpoint.py', 'tests/test_report_template.py', 'tests/test_build_info.py', 'tests/test_gui_reporting_surface.py', 'tests/test_conversion_report.py', 'tests/test_run_warnings_reach_the_tally.py'),
         expect=('test_the_settings_and_an_empty_report_exist_before_the_first_mod',),
    ),
    Pair('C07-b', 'no checkpoint after each source',
         edits=(
             ('src/auto_convert.py', '            finally:\n                # Whatever happened to this source -- converted, failed, or a\n                # death on its way out -- the report on disk now describes the\n                # run up to here. #report-checkpoint\n                _checkpoint_report(output, results, planned=len(sources),\n                                   workers=_planned_workers,\n                                   orphan_temps_removed=_orphans_removed)\n    finally:\n', '    finally:\n', 1),
         ),
         tests=('tests/test_report_checkpoint.py', 'tests/test_report_template.py', 'tests/test_build_info.py', 'tests/test_gui_reporting_surface.py', 'tests/test_conversion_report.py', 'tests/test_run_warnings_reach_the_tally.py'),
         expect=('test_a_run_that_dies_leaves_its_checkpoint_marked_incomplete', 'test_a_source_that_fails_is_in_the_next_checkpoint', 'test_the_report_the_settings_and_the_failures_file_are_written_atomically', 'test_the_settings_and_an_empty_report_exist_before_the_first_mod'),
    ),
    Pair('C07-c', 'every report claims to be complete',
         edits=(
             ('src/auto_convert.py', '            "complete": bool(complete),', '            "complete": True,', 1),
         ),
         tests=('tests/test_report_checkpoint.py', 'tests/test_report_template.py', 'tests/test_build_info.py', 'tests/test_gui_reporting_surface.py', 'tests/test_conversion_report.py', 'tests/test_run_warnings_reach_the_tally.py'),
         expect=('test_a_checkpoint_costs_milliseconds', 'test_a_run_that_dies_leaves_its_checkpoint_marked_incomplete', 'test_the_settings_and_an_empty_report_exist_before_the_first_mod'),
    ),
    Pair('C07-d', 'the report goes back to a plain write',
         edits=(
             ('src/auto_convert.py', '        atomic_write_bytes(out, json.dumps(rep, indent=2, default=str).encode("utf-8"))', '        out.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")', 1),
         ),
         tests=('tests/test_report_checkpoint.py', 'tests/test_report_template.py', 'tests/test_build_info.py', 'tests/test_gui_reporting_surface.py', 'tests/test_conversion_report.py', 'tests/test_run_warnings_reach_the_tally.py'),
         expect=('test_the_report_the_settings_and_the_failures_file_are_written_atomically',),
    ),
    Pair('C07-e', 'the failures file goes back to a plain write',
         edits=(
             ('src/auto_convert.py', '        atomic_write_bytes(_failures_file_path(),\n                           _json.dumps({"failures": _RUN_FAILURES}, indent=1)\n                           .encode("utf-8"))\n', '        _failures_file_path().write_text(\n            _json.dumps({"failures": _RUN_FAILURES}, indent=1), encoding="utf-8")\n', 1),
         ),
         tests=('tests/test_report_checkpoint.py', 'tests/test_report_template.py', 'tests/test_build_info.py', 'tests/test_gui_reporting_surface.py', 'tests/test_conversion_report.py', 'tests/test_run_warnings_reach_the_tally.py'),
         expect=('test_the_report_the_settings_and_the_failures_file_are_written_atomically',),
    ),
    Pair('C07-f', 'the settings sidecar ignores the pool that ran',
         edits=(
             ('src/build_info.py', '        atomic_write_bytes(p, json.dumps(run_config(workers=workers), indent=2,', '        atomic_write_bytes(p, json.dumps(run_config(), indent=2,', 1),
         ),
         tests=('tests/test_report_checkpoint.py', 'tests/test_report_template.py', 'tests/test_build_info.py', 'tests/test_gui_reporting_surface.py', 'tests/test_conversion_report.py', 'tests/test_run_warnings_reach_the_tally.py'),
         expect=('test_a_run_that_dies_leaves_its_checkpoint_marked_incomplete', 'test_write_run_config_names_the_pool_that_ran'),
    ),
    Pair('C07-g', 'the planned count is dropped',
         edits=(
             ('src/auto_convert.py', '            "sources_planned": (len(results) if planned is None else int(planned)),', '            "sources_planned": len(results),', 1),
         ),
         tests=('tests/test_report_checkpoint.py', 'tests/test_report_template.py', 'tests/test_build_info.py', 'tests/test_gui_reporting_surface.py', 'tests/test_conversion_report.py', 'tests/test_run_warnings_reach_the_tally.py'),
         expect=('test_the_settings_and_an_empty_report_exist_before_the_first_mod',),
    ),
    Pair('C07-h', 'the Results tab never says INCOMPLETE',
         edits=(
             ('src/gui.py', '        _unfinished = rep.get("complete") is False', '        _unfinished = False', 1),
         ),
         tests=('tests/test_report_checkpoint.py', 'tests/test_report_template.py', 'tests/test_build_info.py', 'tests/test_gui_reporting_surface.py', 'tests/test_conversion_report.py', 'tests/test_run_warnings_reach_the_tally.py'),
         expect=('test_the_results_tab_says_when_the_run_did_not_finish',),
         needs=('display',),
    ),
    Pair('C07-i', "a caller resets the heading behind the renderer's back again",
         edits=(
             ('src/gui.py', '            _render_results()\n            _show_results_tab()\n', '            _render_results()\n            _res_head.configure(text="Last run")\n            _show_results_tab()\n', 1),
         ),
         tests=('tests/test_report_checkpoint.py', 'tests/test_report_template.py', 'tests/test_build_info.py', 'tests/test_gui_reporting_surface.py', 'tests/test_conversion_report.py', 'tests/test_run_warnings_reach_the_tally.py'),
         expect=('test_the_results_heading_is_owned_by_the_renderer',),
    ),
    Pair('C07-j', 'the problem report stays silent about a dead run',
         edits=(
             ('src/report_template.py', '    if report.get("complete") is False:\n        lines.insert(0,', '    if False:\n        lines.insert(0,', 1),
         ),
         tests=('tests/test_report_checkpoint.py', 'tests/test_report_template.py', 'tests/test_build_info.py', 'tests/test_gui_reporting_surface.py', 'tests/test_conversion_report.py', 'tests/test_run_warnings_reach_the_tally.py'),
         expect=('test_a_report_from_a_run_that_died_says_so_first',),
    ),
    Pair('I7-a', 'an action is back on a moving tag',
         edits=(
             ('.github/workflows/tests.yml', '      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5\n', '      - uses: actions/checkout@v5\n', 1),
         ),
         tests=('tests/test_workflow_hardening.py',),
         expect=('test_every_action_is_pinned_to_a_commit',),
    ),
    Pair('I7-b', 'the permissions block is gone from a workflow',
         edits=(
             ('.github/workflows/tests.yml', 'permissions:\n  contents: read\n', '', 1),
         ),
         tests=('tests/test_workflow_hardening.py',),
         expect=('test_every_workflow_grants_only_read',),
    ),
    Pair('I7-c', 'a workflow grants write',
         edits=(
             ('.github/workflows/tests.yml', 'permissions:\n  contents: read\n', 'permissions:\n  contents: write\n', 1),
         ),
         tests=('tests/test_workflow_hardening.py',),
         expect=('test_every_workflow_grants_only_read',),
    ),
    # B-2 (#osd-columnar), measured 2026-09-16 on a cold copy of the patched
    # tree: 11 of 11 caught, each by the test named here.
    Pair('B2-a', 'the OSD parse reads the deltas big-endian',
         edits=(
             ('src/osd.py', 'OSD_RECORD = np.dtype([("idx", "<u2"), ("delta", "<f4", (3,))])', 'OSD_RECORD = np.dtype([("idx", "<u2"), ("delta", ">f4", (3,))])', 1),
         ),
         tests=('tests/test_osd_columnar.py',),
         expect=('test_the_parse_matches_the_struct_oracle_field_for_field', 'test_the_save_matches_the_struct_oracle_byte_for_byte'),
    ),
    Pair('B2-b', 'the OSD save writes float64 deltas',
         edits=(
             ('src/osd.py', '            rec = np.empty(n, dtype=OSD_RECORD)\n', '            rec = np.empty(n, dtype=np.dtype([("idx", "<u2"), ("delta", "<f8", (3,))]))\n', 1),
         ),
         tests=('tests/test_osd_columnar.py',),
         expect=('test_the_save_matches_the_struct_oracle_byte_for_byte',),
    ),
    Pair('B2-c', 'the tuples are cached on the morph',
         edits=(
             ('src/osd.py', '        d = self.delta.astype(np.float64)\n        return list(zip(self.idx.tolist(), d[:, 0].tolist(),\n                        d[:, 1].tolist(), d[:, 2].tolist()))\n', '        d = self.delta.astype(np.float64)\n        self._offsets = list(zip(self.idx.tolist(), d[:, 0].tolist(),\n                                 d[:, 1].tolist(), d[:, 2].tolist()))\n        return self._offsets\n', 1),
         ),
         tests=('tests/test_osd_columnar.py',),
         expect=('test_the_tuples_are_made_on_demand_and_never_kept',),
    ),
    Pair('B2-d', 'a parse keeps a tuple per offset as well',
         edits=(
             ('src/osd.py', '            morphs.append(OsdMorph(name=name, idx=idx, delta=delta))\n', '            m = OsdMorph(name=name, idx=idx, delta=delta)\n            m._rows = m.offsets\n            morphs.append(m)\n', 1),
         ),
         tests=('tests/test_osd_columnar.py',),
         expect=('test_a_parse_costs_the_file_not_a_tuple_per_offset',),
    ),
    Pair('B2-e', 'the amplitude map uses einsum',
         edits=(
             ('src/nif_convert_trigen.py', '        outward = d[:, 0] * bn[ii, 0] + d[:, 1] * bn[ii, 1] + d[:, 2] * bn[ii, 2]\n', '        outward = np.einsum("ij,ij->i", d, bn[ii])\n', 1),
         ),
         tests=('tests/test_osd_columnar.py',),
         expect=('test_the_amplitude_map_matches_the_scalar_loop_bit_for_bit',),
    ),
    Pair('B2-f', 'the amplitude map sums in another order',
         edits=(
             ('src/nif_convert_trigen.py', '        outward = d[:, 0] * bn[ii, 0] + d[:, 1] * bn[ii, 1] + d[:, 2] * bn[ii, 2]\n', '        outward = d[:, 2] * bn[ii, 2] + d[:, 1] * bn[ii, 1] + d[:, 0] * bn[ii, 0]\n', 1),
         ),
         tests=('tests/test_osd_columnar.py',),
         expect=('test_the_amplitude_map_matches_the_scalar_loop_bit_for_bit',),
    ),
    Pair('B2-g', 'the morph stack keeps sliders below its floor',
         edits=(
             ('src/nif_convert_trigen.py', '        if np.linalg.norm(d[ok], axis=1).max() < _MORPH_STACK_MIN:\n            continue\n', '', 1),
         ),
         tests=('tests/test_osd_columnar.py',),
         expect=('test_the_morph_stack_matches_the_scalar_loop_bit_for_bit',),
    ),
    Pair('B2-h', 'the TRI writer truncates instead of rounding',
         edits=(
             ('src/tri.py', '                q = np.round(arr * inv)\n', '                q = np.floor(arr * inv)\n', 1),
         ),
         tests=('tests/test_osd_columnar.py',),
         expect=('test_the_tri_save_matches_the_struct_oracle_byte_for_byte',),
    ),
    Pair('B2-i', 'the TRI parse multiplies in float32',
         edits=(
             ('src/tri.py', '                    delta = rec["q"].astype(np.float64) * mult\n', '                    delta = (rec["q"].astype(np.float32) * np.float32(mult)).astype(np.float64)\n', 1),
         ),
         tests=('tests/test_osd_columnar.py',),
         expect=('test_the_tri_parse_matches_the_struct_oracle_bit_for_bit',),
    ),
    Pair('B2-j', 'the verbatim body morphs lose their bounds filter',
         edits=(
             ('src/sliderset_gen.py', '            _in = body_m.idx < body_n            # uint16: never negative\n', '            _in = np.ones(len(body_m), dtype=bool)\n', 1),
         ),
         tests=('tests/test_osd_columnar.py',),
         expect=('test_body_morphs_are_copied_verbatim_inside_the_body_only',),
    ),
    Pair('B2-k', 'an index past uint16 wraps silently',
         edits=(
             ('src/osd.py', '            idx = idx.astype(np.int64, copy=False)\n            if idx.size and (int(idx.min()) < 0 or int(idx.max()) > 0xFFFF):\n                raise ValueError(\n                    f"vert_idx exceeds uint16 max in morph {self.name!r}")\n            idx = idx.astype(np.uint16)\n', '            idx = idx.astype(np.uint16)\n', 1),
         ),
         tests=('tests/test_osd_columnar.py',),
         expect=('test_an_index_beyond_uint16_is_refused_at_construction',),
    ),
    # E-07 (#batch-door), measured 2026-09-16 on a cold copy of the patched
    # tree: 5 of 5 caught, each by the test named here.
    Pair('E07-a', 'the copy helper copies nothing',
         edits=(
             ('src/nif_convert_writer.py', '    # createShapeFromData requires tuple sequences; numpy rows trigger a\n    # "expected c_float_Array_3 instance, got numpy.ndarray" error.\n    if override_verts is not None:\n', '    return None\n    # createShapeFromData requires tuple sequences; numpy rows trigger a\n    # "expected c_float_Array_3 instance, got numpy.ndarray" error.\n    if override_verts is not None:\n', 1),
         ),
         tests=('tests/test_convert_paths_through_the_batch_door.py',),
         expect=('test_the_body_swap_path_replaces_the_inline_body_with_the_ube_body', 'test_the_copy_path_writes_the_garment_it_was_given'),
    ),
    Pair('E07-b', 'the body injection raises',
         edits=(
             ('src/nif_convert.py', '    for s in ube_nif.shapes:\n        if s.name not in body_inject_names:\n            continue\n        if s.name == "BaseShape" and not inject_baseshape:\n            continue\n', '    raise RuntimeError("mutant: nothing injected")\n    for s in ube_nif.shapes:\n        if s.name not in body_inject_names:\n            continue\n        if s.name == "BaseShape" and not inject_baseshape:\n            continue\n', 1),
         ),
         tests=('tests/test_convert_paths_through_the_batch_door.py',),
         expect=('test_the_body_swap_path_replaces_the_inline_body_with_the_ube_body',),
    ),
    Pair('E07-c', 'the recorder swallows a pass failure',
         edits=(
             ('src/nif_convert_telemetry.py', '    try:\n        key = f"{label}: {type(exc).__name__}"\n', '    return\n    try:\n        key = f"{label}: {type(exc).__name__}"\n', 1),
         ),
         tests=('tests/test_convert_paths_through_the_batch_door.py',),
         expect=('test_a_pass_that_raises_is_reported_in_the_result_not_lost',),
    ),
    Pair('E07-d', 'the BaseShape is never injected',
         edits=(
             ('src/nif_convert.py', '        if s.name == "BaseShape" and not inject_baseshape:\n            continue\n', '        if s.name == "BaseShape":\n            continue\n', 1),
         ),
         tests=('tests/test_convert_paths_through_the_batch_door.py',),
         expect=('test_the_body_swap_path_replaces_the_inline_body_with_the_ube_body',),
    ),
    Pair('E07-e', 'pass failures no longer ride the result',
         edits=(
             ('src/nif_convert.py', '    _pf = _piece_pass_failures() + _piece_pass_effects()\n', '    _pf = _piece_pass_effects()\n', 1),
         ),
         tests=('tests/test_convert_paths_through_the_batch_door.py',),
         expect=('test_a_pass_that_raises_is_reported_in_the_result_not_lost',),
    ),
    # H-10 (#user-warnings), measured 2026-09-16 on a cold copy of the patched
    # tree: 7 of 7 caught, each by the test named here.
    Pair('H10-a', 'the helper drops the FIX line',
         edits=(
             ('src/user_warnings.py', '    if fix:\n        lines.append(f"{pad}FIX: {fix}")\n', '', 1),
         ),
         tests=('tests/test_user_warnings.py',),
         expect=('test_warn_prints_what_where_consequence_and_fix_in_the_house_shape',),
    ),
    Pair('H10-b', 'the problem marker is gone',
         edits=(
             ('src/user_warnings.py', 'PROBLEM = "!!"\n', 'PROBLEM = ""\n', 1),
         ),
         tests=('tests/test_user_warnings.py', 'tests/test_vanilla_sweep.py'),
         expect=('test_a_dead_sweep_worker_reaches_the_log_the_tally_and_the_failures_file', 'test_a_note_and_a_block_indent_keep_the_shape'),
    ),
    Pair('H10-c', 'plain_error hands back the repr',
         edits=(
             ('src/user_warnings.py', '    msg = str(exc).strip()\n    name = type(exc).__name__\n    return f"{name}: {msg}" if msg else name\n', '    return repr(exc)\n', 1),
         ),
         tests=('tests/test_user_warnings.py',),
         expect=('test_a_source_that_raises_is_recorded_in_plain_words', 'test_plain_error_is_the_type_and_the_message_never_a_repr'),
    ),
    Pair('H10-d', 'a site prints a Python repr again',
         edits=(
             ('src/auto_convert.py', '            warn(f"conversion failed: {plain_error(e)}",\n', '            warn(f"conversion failed: {e!r}",\n', 1),
         ),
         tests=('tests/test_user_warnings.py',),
         expect=('test_no_user_facing_print_or_warning_carries_a_python_repr',),
    ),
    Pair('H10-e', 'the recorder is fed the repr again',
         edits=(
             ('src/auto_convert.py', '                _record_failure("source failed", src.name,\n                                "whole source", plain_error(err))\n', '                _record_failure("source failed", src.name,\n                                "whole source", repr(err))\n', 1),
         ),
         tests=('tests/test_user_warnings.py',),
         expect=('test_a_source_that_raises_is_recorded_in_plain_words', 'test_no_user_facing_print_or_warning_carries_a_python_repr'),
    ),
    Pair('H10-f', 'a warning loses both its consequence and its fix',
         edits=(
             ('src/auto_convert.py', '            warn(f"pre-warm failed (non-fatal): {plain_error(e)}",\n                 consequence="the first pieces pay the cold start")\n', '            warn(f"pre-warm failed (non-fatal): {plain_error(e)}")\n', 1),
         ),
         tests=('tests/test_user_warnings.py',),
         expect=('test_the_warning_surface_is_current_and_actionable',),
    ),
    Pair('H10-g', 'the surface scan finds nothing',
         edits=(
             ('scripts/warning_surface.py', '            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)\n                    and node.func.id == "warn"):\n                continue\n', '            if True:\n                continue\n', 1),
         ),
         tests=('tests/test_user_warnings.py',),
         expect=('test_the_warning_surface_is_current_and_actionable',),
    ),
    # H-13 (#per-file-progress, #cancel-says-so), measured 2026-09-16 on a cold
    # copy of the patched tree: 7 of 7 caught, each by the test named here.
    Pair('H13-a', 'the status line ignores the cancel',
         edits=(
             ('src/failure_summary.py', '    if cancelled:\n        return ("Cancelled - the run was stopped at your request. The Results "\n', '    if False:\n        return ("Cancelled - the run was stopped at your request. The Results "\n', 1),
         ),
         tests=('tests/test_gui_progress_and_cancel.py',),
         expect=('test_a_cancelled_run_is_worded_as_cancelled_not_as_an_error',),
    ),
    Pair('H13-b', 'Cancel no longer records itself',
         edits=(
             ('src/gui.py', '        state["cancelled"] = True   # _finish words the end from this  #cancel-says-so\n', '', 1),
         ),
         tests=('tests/test_gui_progress_and_cancel.py',),
         expect=('test_the_window_sets_the_flag_on_cancel_and_finish_reads_it',),
    ),
    Pair('H13-c', 'the popup opens over a cancelled run again',
         edits=(
             ('src/gui.py', '        if not cancelled:\n            _show_failures_popup(fails)\n', '        _show_failures_popup(fails)\n', 1),
         ),
         tests=('tests/test_gui_progress_and_cancel.py',),
         expect=('test_the_window_sets_the_flag_on_cancel_and_finish_reads_it',),
    ),
    Pair('H13-d', 'the batch stops printing the per-file marker',
         edits=(
             ('src/auto_convert.py', '                    print(f"[progress-nif] {done} {len(work_items)}", flush=True)\n', '', 1),
         ),
         tests=('tests/test_gui_progress_and_cancel.py',),
         expect=('test_both_markers_parse_and_the_batch_prints_the_per_file_one',),
    ),
    Pair('H13-e', 'the bar jumps a mod ahead again',
         edits=(
             ('src/gui.py', '    base = max(0, mod_index - 1)\n', '    base = mod_index\n', 1),
         ),
         tests=('tests/test_gui_progress_and_cancel.py',),
         expect=('test_the_bar_sits_on_the_mods_finished_plus_this_mods_share',),
    ),
    Pair('H13-f', 'the mod estimate counts every file instead of the ones left',
         edits=(
             ('src/gui.py', '    return _fmt_eta(per_file * max(0, nif_total - nif_done))\n', '    return _fmt_eta(per_file * nif_total)\n', 1),
         ),
         tests=('tests/test_gui_progress_and_cancel.py',),
         expect=('test_the_mod_eta_comes_from_its_own_rate',),
    ),
    Pair('H13-g', 'the window stops reading the per-file marker',
         edits=(
             ('src/gui.py', '                    for m in _NIF_RX.finditer(item):\n                        _update_nif_progress(int(m.group(1)), int(m.group(2)))\n', '', 1),
         ),
         tests=('tests/test_gui_progress_and_cancel.py',),
         expect=('test_the_window_reads_the_per_file_marker_and_strips_both',),
    ),
    # G-7 (#env-through-helpers), measured 2026-09-16 on a cold copy of the
    # patched tree: 8 of 8 caught, each by the test named here. The gate
    # runs these with the shell's CBBE2UBE_* stripped by the tests themselves
    # (a set knob skips its default check by name).
    Pair('G7-a', 'a migrated switch goes back to a raw read',
         edits=(
             ('src/fit_metrics.py', 'CAST_CULL = not _flag("CBBE2UBE_NO_CAST_CULL", False)\n', 'CAST_CULL = os.environ.get("CBBE2UBE_NO_CAST_CULL") != "1"\n', 1),
         ),
         tests=('tests/test_raw_env_reads.py',),
         expect=('test_only_the_listed_paths_and_strings_are_read_raw', 'test_the_retirement_census_has_nothing_left_uncounted', 'test_the_surface_census_sees_every_migrated_switch'),
    ),
    Pair('G7-b', 'a new raw read through os.getenv',
         edits=(
             ('src/overlay_transfer.py', 'from .envflags import knob as _knob\n', 'from .envflags import knob as _knob\n_PLANTED = os.getenv("CBBE2UBE_PLANTED_KNOB", "")\n', 1),
         ),
         tests=('tests/test_raw_env_reads.py',),
         expect=('test_only_the_listed_paths_and_strings_are_read_raw',),
    ),
    Pair('G7-c', 'a new raw read through a local alias, split across lines',
         edits=(
             ('src/discovery.py', '    _bodymatch = not _flag("CBBE2UBE_NO_BODYMATCH_SELECT", False)\n', '    import os as _os\n    _planted = _os.environ.get(\n        "CBBE2UBE_PLANTED_ALIAS", "")\n    _bodymatch = not _flag("CBBE2UBE_NO_BODYMATCH_SELECT", False)\n', 1),
         ),
         tests=('tests/test_raw_env_reads.py',),
         expect=('test_only_the_listed_paths_and_strings_are_read_raw',),
    ),
    Pair('G7-d', 'the allowlist outlives a read it excused',
         edits=(
             ('src/gui_settings.py', 'override = os.environ.get("CBBE2UBE_CONFIG", "").strip()\n', 'override = os.environ.get("CBBE2UBE_CONFIG_FILE", "").strip()\n', 1),
         ),
         tests=('tests/test_raw_env_reads.py',),
         expect=('test_only_the_listed_paths_and_strings_are_read_raw',),
    ),
    Pair('G7-e', "a knob's default moves",
         edits=(
             ('src/fit_metrics.py', 'PUSH_ITERS = _knob("CBBE2UBE_PUSH_ITERS", 8, int)\n', 'PUSH_ITERS = _knob("CBBE2UBE_PUSH_ITERS", 9, int)\n', 1),
         ),
         tests=('tests/test_raw_env_reads.py',),
         expect=('test_the_resolved_default_is_what_the_raw_read_resolved_to[fit_metrics.PUSH_ITERS]',),
    ),
    Pair('G7-f', 'an int knob loses its cast',
         edits=(
             ('src/fit_metrics.py', 'RAY_CHUNK = _knob("CBBE2UBE_RAY_CHUNK", 512, int)\n', 'RAY_CHUNK = _knob("CBBE2UBE_RAY_CHUNK", 512)\n', 1),
         ),
         tests=('tests/test_raw_env_reads.py',),
         expect=('test_an_int_knob_keeps_its_cast_and_a_float_knob_its_type',),
    ),
    Pair('G7-g', 'a kill switch loses its polarity',
         edits=(
             ('src/fit_metrics.py', 'PUSH_ENABLED = not _flag("CBBE2UBE_NO_MIN_PUSH", False)\n', 'PUSH_ENABLED = _flag("CBBE2UBE_NO_MIN_PUSH", False)\n', 1),
         ),
         tests=('tests/test_raw_env_reads.py',),
         expect=('test_the_resolved_default_is_what_the_raw_read_resolved_to[fit_metrics.PUSH_ENABLED]',),
    ),
    Pair('G7-h', "the retirement census counts the window's write again",
         edits=(
             ('scripts/analysis/flag_retirement.py', '                      r"(CBBE2UBE_NO_[A-Z0-9_]+)(?=[\\"\'])(?![\\"\']\\]\\s*=[^=])")\n', '                      r"(CBBE2UBE_NO_[A-Z0-9_]+)")\n', 1),
         ),
         tests=('tests/test_raw_env_reads.py',),
         expect=('test_the_retirement_census_has_nothing_left_uncounted',),
    ),
    Pair('G7-i', 'a comment contradicts the polarity of a flag the audit now reads',
         edits=(
             ('src/nif_convert.py', 'SELFINT_REPAIR = not _flag("CBBE2UBE_NO_SELFINT_REPAIR", False)\n', '# Opt-in: default OFF.\nSELFINT_REPAIR = not _flag("CBBE2UBE_NO_SELFINT_REPAIR", False)\n', 1),
         ),
         tests=('tests/test_raw_env_reads.py', 'tests/test_comment_audit.py', 'tests/test_gui_settings.py', 'tests/test_incremental_config_floor.py'),
         expect=('test_the_polarity_check_finds_nothing',),
    ),
    Pair('G7-j', 'a raw boolean read returns to a converter module',
         edits=(
             ('src/nif_convert.py', 'SELFINT_REPAIR = not _flag("CBBE2UBE_NO_SELFINT_REPAIR", False)\n', 'SELFINT_REPAIR = not _flag("CBBE2UBE_NO_SELFINT_REPAIR", False)\n_PLANTED_RAW = os.environ.get("CBBE2UBE_PLANTED_RAW", "") == "1"\n', 1),
         ),
         tests=('tests/test_raw_env_reads.py', 'tests/test_comment_audit.py', 'tests/test_gui_settings.py', 'tests/test_incremental_config_floor.py'),
         expect=('test_only_the_listed_paths_and_strings_are_read_raw', 'test_every_gui_env_is_read_by_src', 'test_env_prefix_scan_covers_the_real_converter_surface'),
    ),
    # G-8 (#kill-switch-verdicts), measured 2026-09-16 on a cold copy of the
    # patched tree: 6 of 6 caught, each by the test named here. G8-a re-adds
    # the retired switch's binding; the census counts any mention of its
    # variable in src/tests/scripts/docs as a reference, so this file (under
    # scripts/) is the one place its name may appear in those trees.
    Pair('G8-a', "the retired switch's binding returns",
         edits=(
             ('src/nif_convert.py', 'BACK_MIN_VERTS = _knob("CBBE2UBE_BACK_MIN_VERTS", 24, int)\n', 'BACK_MIN_VERTS = _knob("CBBE2UBE_BACK_MIN_VERTS", 24, int)\nBACK_RESIDUAL_VERBOSE = not _flag("CBBE2UBE_BACK_RESIDUAL_QUIET", False)\n', 1),
         ),
         tests=('tests/test_flag_retirement.py',),
         expect=('test_every_standing_switch_carries_a_verdict_status', 'test_the_report_says_beside_each_standing_switch_what_it_waits_on'),
    ),
    Pair('G8-b', 'a standing switch loses its table entry',
         edits=(
             ('scripts/analysis/flag_retirement.py', '    "PROXY_ENCLOSE_GUARD": "one maintainer note + the 2026-09-01 audit; no test",\n', '', 1),
         ),
         tests=('tests/test_flag_retirement.py',),
         expect=('test_every_standing_switch_carries_a_verdict_status', 'test_the_report_says_beside_each_standing_switch_what_it_waits_on'),
    ),
    Pair('G8-c', 'the table names a switch that is not standing',
         edits=(
             ('scripts/analysis/flag_retirement.py', '    "_TIGHT_SOFTBODY_GATE": "one maintainer note + the 2026-09-01 audit; no test",\n', '    "_TIGHT_SOFTBODY_GATE": "one maintainer note + the 2026-09-01 audit; no test",\n    "COHERENCE_REPAIR": "planted: referenced ten times, not standing",\n', 1),
         ),
         tests=('tests/test_flag_retirement.py',),
         expect=('test_every_standing_switch_carries_a_verdict_status', 'test_the_report_says_beside_each_standing_switch_what_it_waits_on'),
    ),
    Pair('G8-d', 'the report stops printing the status',
         edits=(
             ('scripts/analysis/flag_retirement.py', '        out("        %s" % verdict_status(r["name"]))\n', '', 1),
         ),
         tests=('tests/test_flag_retirement.py',),
         expect=('test_the_report_says_beside_each_standing_switch_what_it_waits_on', 'test_an_unrecorded_standing_switch_is_named_as_such'),
    ),
    Pair('G8-e', 'an unknown switch reads as recorded',
         edits=(
             ('scripts/analysis/flag_retirement.py', '    return "UNRECORDED -- decoration until the record says otherwise"\n', '    return "awaiting verdict: (unknown)"\n', 1),
         ),
         tests=('tests/test_flag_retirement.py',),
         expect=('test_an_unrecorded_standing_switch_is_named_as_such',),
    ),
    Pair('G8-f', 'the standing set is computed without the reference test',
         edits=(
             ('scripts/analysis/flag_retirement.py', '    return [r for r in rs if r["resolves_on"] and r["n_refs"] == 0\n            and not r["in_live_settings"]]\n', '    return [r for r in rs if r["resolves_on"]\n            and not r["in_live_settings"]]\n', 1),
         ),
         tests=('tests/test_flag_retirement.py',),
         expect=('test_every_standing_switch_carries_a_verdict_status',),
    ),
    # B-3 (#postflight-release), measured 2026-09-17 on a cold copy of the
    # patched tree: 4 of 4 caught, each by the test named here, with the
    # cyclic collector disabled inside the tests. B3-a reaches only the
    # first test: the passes drop their shape lists before the file, so
    # the container clearing alone frees the file there.
    Pair('B3-a', 'the release leaves the block-to-file link in place',
         edits=(
             ('src/nif_io.py', '        for a in ("file", "_parent"):\n', '        for a in ("_parent",):\n', 1),
         ),
         tests=('tests/test_postflight_releases_nif_pairs.py',),
         expect=('test_a_released_file_dies_under_reference_counting_alone',),
    ),
    Pair('B3-b', 'the release is a no-op',
         edits=(
             ('src/nif_io.py', '    d = getattr(nf, "__dict__", None)\n    if d is None:\n        return\n', '    return\n    d = getattr(nf, "__dict__", None)\n    if d is None:\n        return\n', 1),
         ),
         tests=('tests/test_postflight_releases_nif_pairs.py',),
         expect=('test_a_released_file_dies_under_reference_counting_alone', 'test_the_divergence_pass_leaves_no_file_open', 'test_the_jiggle_sync_pass_leaves_no_file_open'),
    ),
    Pair('B3-c', 'the divergence pass stops releasing its pair',
         edits=(
             ('src/auto_convert.py', '            nif_io.release_nif(n0)\n            nif_io.release_nif(n1)\n', '            pass\n', 1),
         ),
         tests=('tests/test_postflight_releases_nif_pairs.py',),
         expect=('test_the_divergence_pass_leaves_no_file_open',),
    ),
    Pair('B3-d', 'the jiggle-sync pass stops releasing its pair',
         edits=(
             ('src/nif_convert_weights.py', '        for _f in nf.values():\n            nif_io.release_nif(_f)\n', '        pass\n', 1),
         ),
         tests=('tests/test_postflight_releases_nif_pairs.py',),
         expect=('test_the_jiggle_sync_pass_leaves_no_file_open',),
    ),
    # #worktree-per-branch and #hook-fail-closed (2026-09-17): the hooks refuse
    # to run without the denylist, look for it in the primary checkout from a
    # linked worktree, and refuse a commit on an integration branch or in the
    # primary checkout; the lane and onboard helpers give a clone what the
    # hooks need. Measured on a cold copy before the commit.
    Pair('HK-a', 'a missing denylist no longer refuses (the shared check)',
         edits=(
             ('scripts/hook_precommit.py', '        absent = H.no_denylist_problem(os.environ.get(H.NO_DENYLIST_ENV) == "1")\n', '        absent = None  # MUTATED\n', 1),
         ),
         tests=('tests/test_repo_hygiene_hooks.py',),
         expect=('test_precommit_refuses_without_a_denylist_unless_acknowledged', 'test_commitmsg_refuses_without_a_denylist_unless_acknowledged', 'test_prepush_refuses_without_a_denylist_unless_acknowledged'),
    ),
    Pair('HK-b', 'the commit-msg hook bypasses the shared denylist check',
         edits=(
             ('scripts/hook_commitmsg.py', '    denylist = P.denylist_for_hook(root, problems)\n', '    denylist, _n, _w = P.find_denylist(root)  # MUTATED\n', 1),
         ),
         tests=('tests/test_repo_hygiene_hooks.py',),
         expect=('test_commitmsg_refuses_without_a_denylist_unless_acknowledged',),
    ),
    Pair('HK-c', 'the pre-push hook bypasses the shared denylist check',
         edits=(
             ('scripts/hook_prepush.py', '    denylist = P.denylist_for_hook(Path(__file__).resolve().parent.parent, problems)\n', '    denylist, _n, _w = P.find_denylist(Path(__file__).resolve().parent.parent)  # MUTATED\n', 1),
         ),
         tests=('tests/test_repo_hygiene_hooks.py',),
         expect=('test_prepush_refuses_without_a_denylist_unless_acknowledged',),
    ),
    Pair('HK-d', 'a linked worktree no longer reads the primary checkout\'s denylist',
         edits=(
             ('scripts/hook_precommit.py', '    primary = primary_checkout_root()\n', '    primary = None  # MUTATED\n', 1),
         ),
         tests=('tests/test_repo_hygiene_hooks.py',),
         expect=('test_a_linked_worktree_finds_the_primary_checkouts_denylist',),
    ),
    Pair('HK-e', 'a commit on an integration branch or in the primary checkout is no longer refused',
         edits=(
             ('scripts/repo_hygiene.py', '    if acknowledged:\n        return None\n    if branch in INTEGRATION_BRANCHES:\n', '    if True:  # MUTATED\n        return None\n    if branch in INTEGRATION_BRANCHES:\n', 1),
         ),
         tests=('tests/test_repo_hygiene_hooks.py',),
         expect=('test_a_commit_is_refused_on_an_integration_branch_and_in_the_primary_checkout',),
    ),
    Pair('WF-a', 'a new lane does not get the denylist',
         edits=(
             ('scripts/lane.py', '        shutil.copy2(deny, lane / H.DENYLIST_FILE)\n', '        pass  # MUTATED\n', 1),
         ),
         tests=('tests/test_lane_workflow.py',),
         expect=('test_lane_new_and_rm_make_and_unmake_a_worktree_with_what_the_hooks_need',),
    ),
    Pair('WF-b', 'onboarding points git at the wrong hooks directory',
         edits=(
             ('scripts/onboard.py', '    ("core.hooksPath", ".githooks"),\n', '    ("core.hooksPath", ".git/hooks"),  # MUTATED\n', 1),
         ),
         tests=('tests/test_lane_workflow.py',),
         expect=('test_onboard_configures_the_clone_and_reports_what_it_cannot_supply',),
    ),
    # #tool-frame-floors (2026-09-20): the harness guards. These mutate a
    # MEASUREMENT tool, not the converter, so no in-game verdict is owed --
    # none of them can move a vertex. Both directions of the frame chooser are
    # armed separately: a pair arming only one would pass for a chooser
    # hardcoded to the other, which is exactly the bug being guarded.
    Pair('TFF-a', 'the frame chooser always transforms, as three tools used to',
         edits=(
             ('scripts/analysis/standoff_audit.py', '    return (raw, "raw") if dr < dw else (world, "world")', '    return world, "world"  # MUTATED: always transform', 1),
         ),
         tests=('tests/test_frame_chooser.py',),
         expect=('test_a_world_stored_shape_with_a_stale_transform_keeps_its_raw_verts',),
    ),
    Pair('TFF-b', 'the frame chooser never transforms, stranding 403 shapes',
         edits=(
             ('scripts/analysis/standoff_audit.py', '    return (raw, "raw") if dr < dw else (world, "world")', '    return raw, "raw"  # MUTATED: never transform', 1),
         ),
         tests=('tests/test_frame_chooser.py',),
         expect=('test_a_skin_stored_shape_is_transformed',),
    ),
    Pair('TFF-c', 'the coverage floor is present but can never fire',
         edits=(
             ('scripts/analysis/bust_verdict.py', 'CTRL_MIN_COVERED = 1.0', 'CTRL_MIN_COVERED = 0.0  # MUTATED: dead guard', 1),
         ),
         tests=('tests/test_tool_exclusion_guards.py',),
         expect=('test_a_garment_covering_nothing_is_a_control_failure', 'test_the_floor_is_above_zero'),
    ),
    Pair('TFF-d', 'the proxy rule drops its rendering half and keeps the token',
         edits=(
             ('scripts/analysis/survey_motion_clipping.py', '    return bool(PROXY_TOKEN.search(nm)) and not renders(s)', '    return bool(PROXY_TOKEN.search(nm))  # MUTATED: token alone', 1),
         ),
         tests=('tests/test_tool_exclusion_guards.py',),
         expect=('test_a_rendered_shape_is_never_a_proxy_however_it_is_named',),
    ),
    Pair('TFF-e', 'the proxy rule stops exempting collar',
         edits=(
             ('scripts/analysis/survey_motion_clipping.py', '    if "collar" in nm.lower():', '    if False:  # MUTATED: no collar exemption', 1),
         ),
         tests=('tests/test_tool_exclusion_guards.py',),
         expect=('test_collar_is_exempt',),
    ),
)

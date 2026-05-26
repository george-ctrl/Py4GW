"""
TunnelsOfTheForsaken/__init__.py — entry point for the TotF Auraway SC bot.

Py4GW calls configure() once on script load, main() every frame, draw() for UI.
Role detection reads the equipped skill bar (Dasher vs Aura); the player
clicks Start in the UI when all accounts are ready.

On every Start click all submodules are reloaded from disk, so code changes to
constants.py, agents.py, and the role scripts are picked up without restarting
Py4GW.  Consumable toggle state is preserved across reloads.

How to set up:
    1. Fill in all TODO values in constants.py (model IDs, waypoints).
    2. Equip the correct build on each account (see PvX wiki page).
    3. Load this script on all 4 accounts simultaneously.
    4. Click Start in the UI on each account — role detection is automatic.

File layout:
    __init__.py         ← you are here (entry point)
    constants.py        ← all TotF-specific IDs and coordinates
    agents.py           ← NPC and party-member lookup helpers
    roles/
        __init__.py     ← imports both roles to register them
        _shared.py      ← service constructors shared by both roles
        dasher.py       ← DasherRole (1 account)
        aura.py         ← AuraRole   (3 accounts)
"""

from __future__ import annotations

# ── Py4GW package bootstrap ───────────────────────────────────────────────────
# Py4GW 3.x exec()s scripts from C++ without setting __file__ or __package__,
# so the normal package machinery is unavailable.  We try five strategies to
# locate the real file path, then register a minimal stub so relative imports
# inside roles/ work.
import sys as _sys, os as _os, types as _types

# Run when __package__ is absent OR resolves to something without __path__
# (e.g. the "Py4GW" C extension module).
if not __package__ or not hasattr(_sys.modules.get(__package__), "__path__"):
    import inspect as _inspect
    _frame   = _inspect.currentframe()
    _this_file = ""

    # S1: co_filename — works if Py4GW compiles scripts with their real path.
    _cf = _frame.f_code.co_filename if _frame is not None else ""
    if _os.path.isfile(_cf):
        _this_file = _cf

    # S2: __file__ injected into the exec globals.
    if not _this_file:
        _gf = globals().get("__file__", "")
        if _os.path.isfile(_gf):
            _this_file = _gf

    # S3: walk ALL Python call-stack frames looking for any local var that is a
    # real .py path (helps when Py4GW has a Python-level script runner).
    if not _this_file and _frame is not None:
        _cur = _frame.f_back
        while _cur is not None and not _this_file:
            for _, _fv in _cur.f_locals.items():
                if isinstance(_fv, str) and _fv.endswith(".py") and _os.path.isfile(_fv):
                    _this_file = _fv
                    break
            _cur = _cur.f_back

    # S4: sys.argv — Py4GW may pass the script path as an argument.
    if not _this_file:
        for _arg in _sys.argv:
            if isinstance(_arg, str) and _arg.endswith(".py") and _os.path.isfile(_arg):
                _this_file = _arg
                break

    # S5: locate via Py4GWCoreLib (always importable) — derive the Py4GW root
    # from its __file__, then walk Widgets/ for our directory name.  This is the
    # reliable last resort when Py4GW exec()s entirely from C++.
    if not _this_file:
        try:
            _clib = __import__("Py4GWCoreLib")
            _root = _os.path.dirname(_os.path.dirname(
                _os.path.abspath(_clib.__file__)
            ))
            _root_depth = _root.count(_os.sep)
            _skip       = {".git", "__pycache__", "node_modules", ".venv", "venv"}
            _search     = _os.path.join(_root, "Widgets")
            if not _os.path.isdir(_search):
                _search = _root
            for _dp, _dns, _fns in _os.walk(_search):
                if _dp.count(_os.sep) - _root_depth > 8:
                    _dns[:] = []
                    continue
                _dns[:] = [d for d in _dns if d not in _skip]
                if _os.path.basename(_dp) == "TunnelsOfTheForsaken" and "__init__.py" in _fns:
                    _this_file = _os.path.join(_dp, "__init__.py")
                    break
        except Exception:
            pass

    if _this_file:
        _d = _os.path.dirname(_os.path.abspath(_this_file))
        _p = _os.path.dirname(_d)
        _n = _os.path.basename(_d)
        if _p not in _sys.path:
            _sys.path.insert(0, _p)
        if _n not in _sys.modules or not hasattr(_sys.modules[_n], "__path__"):
            _stub = _types.ModuleType(_n)
            _stub.__path__    = [_d]
            _stub.__package__ = _n
            _stub.__file__    = _this_file
            _sys.modules[_n]  = _stub
        __package__ = _n
# ─────────────────────────────────────────────────────────────────────────────

import importlib as _importlib

import Py4GW
import PyImGui

from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.py4gwcorelib_src.Console import ConsoleLog, Console

from Py4GWCoreLib.sc_framework import SCCoordinator, SCRole, SCRuntime, draw_path_overlay

# Derive package name and directory from the stub registered by the bootstrap.
# Used by _reload_submodules() on every Start press.
_PKG_NAME: str = __package__ or ""
if not _PKG_NAME or not hasattr(_sys.modules.get(_PKG_NAME), "__path__"):
    raise RuntimeError(
        f"TunnelsOfTheForsaken bootstrap failed (__package__={__package__!r}). "
        "Py4GW must compile scripts with their real path or set __file__ in the "
        "exec namespace.  Check the Py4GW version / script loader settings."
    )
_PKG_DIR: str = _sys.modules[_PKG_NAME].__path__[0]

# Flush any stale cached submodules from a previous Py4GW load so edits to
# role files and _shared.py take effect without a full environment reset.
for _k in [k for k in _sys.modules if k.startswith(_PKG_NAME + ".")]:
    del _sys.modules[_k]

# Initial submodule import — fires @register_role decorators.
from . import roles  # noqa: F401
from .constants import ALL_CONSUMABLES
from .roles._shared import _cons_enabled, _verbose_log, _movement_debug  # dicts kept in sync with draw()

MODULE_NAME = "TotF Auraway"
BOT_NAME    = "Tunnels of the Forsaken"

# ── module-level state ────────────────────────────────────────────────────────
_bot_tree    = None   # BottingTree; created on Start, cleared on Stop
_role        = None   # Detected SCRole instance
_coord       = None   # SCCoordinator scoped to this account
_setup_err   = ""     # Non-empty if role detection failed
_initialized = False  # True once role+coord are ready


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_own_email() -> str:
    own_agent_id = Player.GetAgentID()
    for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
        if acc.IsAccount and acc.AgentData.AgentID == own_agent_id:
            return str(acc.AccountEmail)
    return ""


def _reload_submodules() -> None:
    """
    Purge all cached TotF submodules and reimport them from disk.

    Steps:
      1. Clear _ROLE_REGISTRY so @register_role decorators don't accumulate
         duplicates across multiple Start presses.
      2. Remove every TunnelsOfTheForsaken.* entry from sys.modules.
      3. Restore the package stub (needed by relative imports in the modules).
      4. Reimport roles — fires @register_role for DasherRole and AuraRole.
    """
    from Py4GWCoreLib.sc_framework.role import _ROLE_REGISTRY
    _ROLE_REGISTRY.clear()

    for key in [k for k in _sys.modules if k.startswith(_PKG_NAME + ".")]:
        del _sys.modules[key]

    stub = _types.ModuleType(_PKG_NAME)
    stub.__path__    = [_PKG_DIR]
    stub.__package__ = _PKG_NAME
    _sys.modules[_PKG_NAME] = stub

    _importlib.import_module(f"{_PKG_NAME}.roles")


def _log_detection_failure() -> None:
    """Log full skill bar and registered roles to help diagnose mismatches."""
    from Py4GWCoreLib.sc_framework.role import _ROLE_REGISTRY
    from .constants import SkillID

    raw = [GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(s) for s in range(1, 9)]
    filtered = [sid for sid in raw if sid]
    ConsoleLog(MODULE_NAME, f"  Raw bar  (slots 1-8): {raw}", Console.MessageType.Warning)
    ConsoleLog(MODULE_NAME, f"  Filtered (non-zero) : {filtered}", Console.MessageType.Warning)

    if not _ROLE_REGISTRY:
        ConsoleLog(MODULE_NAME, "  _ROLE_REGISTRY is EMPTY — roles were never imported!", Console.MessageType.Error)
        return

    ConsoleLog(MODULE_NAME, f"  Registered roles: {[r.role_id for r in _ROLE_REGISTRY]}", Console.MessageType.Warning)
    ConsoleLog(MODULE_NAME, f"  Dasher expects Barbs({SkillID.Barbs}) AND Dash({SkillID.Dash})", Console.MessageType.Warning)
    ConsoleLog(MODULE_NAME, f"  Aura   expects GrentsAura({SkillID.GrentsAura}) AND EbonEscape({SkillID.EbonEscape})", Console.MessageType.Warning)


def _start_bot() -> None:
    global _bot_tree, _role, _coord, _setup_err, _cons_enabled, _verbose_log, _movement_debug

    # Preserve user toggle state across the reload.
    saved_cons      = dict(_cons_enabled)
    saved_verbose   = _verbose_log["enabled"]
    saved_mvt_debug = _movement_debug["enabled"]

    ConsoleLog(MODULE_NAME, "Reloading submodules…", Console.MessageType.Info)
    _reload_submodules()

    # Re-bind module-level dicts to the freshly imported _shared so the UI
    # stays in sync with the tick closures that captured them at build time.
    new_shared = _importlib.import_module(f"{_PKG_NAME}.roles._shared")
    _cons_enabled = new_shared._cons_enabled
    _cons_enabled.update(saved_cons)
    _verbose_log = new_shared._verbose_log
    _verbose_log["enabled"] = saved_verbose
    _movement_debug = new_shared._movement_debug
    _movement_debug["enabled"] = saved_mvt_debug

    email = _get_own_email()
    if not email:
        _setup_err = "Could not resolve account email — is the player loaded?"
        ConsoleLog(MODULE_NAME, _setup_err, Console.MessageType.Error)
        return

    try:
        _role = SCRole.detect()
    except ValueError as exc:
        _setup_err = f"Role detection failed: {exc}"
        ConsoleLog(MODULE_NAME, _setup_err, Console.MessageType.Error)
        return

    _coord     = SCCoordinator(email)
    _setup_err = ""
    _bot_tree  = SCRuntime.start(BOT_NAME, _coord, _role)
    ConsoleLog(MODULE_NAME, f"Bot started ({_role.role_id})", Console.MessageType.Success)


def _stop_bot() -> None:
    global _bot_tree
    if _bot_tree is not None:
        _bot_tree.Stop()
        _bot_tree = None
    ConsoleLog(MODULE_NAME, "Bot stopped", Console.MessageType.Info)


# ── Py4GW lifecycle ───────────────────────────────────────────────────────────

def main():
    """Called every frame by Py4GW.  Drives lazy init and the BottingTree."""
    global _role, _coord, _setup_err, _initialized

    # Lazy init: keep trying each frame until the player is in-world and the
    # skill bar is readable.  configure() in Py4GW is a settings-panel hook,
    # not a startup hook, so we can't rely on it for one-time initialization.
    if not _initialized and not _setup_err:
        email = _get_own_email()
        if email:
            try:
                _role        = SCRole.detect()
                _coord       = SCCoordinator(email)
                _initialized = True
                ConsoleLog(MODULE_NAME, f"Ready — role: {_role.role_id}", Console.MessageType.Success)
            except ValueError as exc:
                _log_detection_failure()
                _setup_err = f"Role detection failed: {exc}"
                ConsoleLog(MODULE_NAME, _setup_err, Console.MessageType.Error)

    if _bot_tree is not None:
        _bot_tree.tick()


def _draw_movement_tab() -> None:
    """Content of the Movement debug tab."""
    import math as _m
    import time as _t
    from Py4GWCoreLib.sc_framework.movement import _overlay

    path   = _overlay["path"]
    wp_idx = _overlay["wp_idx"]
    tol    = _overlay["tolerance"]

    # ── path state ────────────────────────────────────────────────────────────
    PyImGui.text("Path")
    PyImGui.separator()
    if not path:
        PyImGui.text("  No active path")
    else:
        n_wps = len(path)
        PyImGui.text(f"  Waypoints: {min(wp_idx, n_wps)} / {n_wps}  (tol={tol:.0f})")
        if 0 <= wp_idx < n_wps:
            wx, wy = path[wp_idx]
            PyImGui.text(f"  Next WP:  ({wx:.0f}, {wy:.0f})")
            try:
                px, py = Player.GetXY()
                dist = _m.hypot(wx - px, wy - py)
                PyImGui.text(f"  Player:   ({px:.0f}, {py:.0f})")
                PyImGui.text(f"  Dist:     {dist:.0f} units")
            except Exception:
                pass
        else:
            PyImGui.text("  All waypoints complete")

    PyImGui.spacing()

    # ── avoidance state ───────────────────────────────────────────────────────
    PyImGui.text("Avoidance")
    PyImGui.separator()

    avoiding   = _overlay.get("avoiding", False)
    strategy   = _overlay.get("strategy", "strafe")
    start_ms   = _overlay.get("strafe_start_ms", 0.0)
    blocker_id = _overlay.get("sticky_blocker_id")
    sample     = _overlay.get("avoid_sample")
    goal       = _overlay.get("avoid_goal")
    cfg        = _overlay.get("avoid_cfg")

    if avoiding:
        PyImGui.text_colored("  State:    AVOIDING", (1.0, 0.8, 0.1, 1.0))
        PyImGui.text(f"  Strategy: {strategy}")
        if cfg is not None:
            elapsed = _t.monotonic() * 1_000.0 - start_ms
            PyImGui.text(f"  Strafe:   {elapsed:.0f} / {cfg.escalation_ms:.0f} ms")
        if blocker_id is not None:
            PyImGui.text(f"  Blocker:  agent {blocker_id}")
        if sample is not None:
            side = "LEFT" if sample.is_left else "RIGHT"
            PyImGui.text(
                f"  Obs:      dist={sample.distance:.0f}"
                f"  dot={sample.dot:.0f}  cross={sample.cross:.0f}  {side}"
            )
    else:
        PyImGui.text_colored("  State:    CLEAR", (0.3, 1.0, 0.3, 1.0))

    if goal is not None:
        PyImGui.text(f"  Goal:     ({goal[0]:.0f}, {goal[1]:.0f})")

    PyImGui.spacing()

    # ── avoidance config ──────────────────────────────────────────────────────
    if cfg is not None:
        PyImGui.text("Config")
        PyImGui.separator()
        PyImGui.text(f"  Check radius:    {cfg.check_radius:.0f}")
        PyImGui.text(f"  Path half-width: {cfg.path_half_width:.0f}")
        PyImGui.text(f"  Agent radius:    {cfg.agent_radius:.0f}")
        PyImGui.text(f"  Escalation:      {cfg.escalation_ms:.0f} ms")
        PyImGui.text(f"  Deflect angle:   {cfg.deflect_angle_deg:.0f}°")
        PyImGui.text(f"  Strafe release:  {cfg.strafe_release_margin:.0f}")
        PyImGui.spacing()

    # ── log button ────────────────────────────────────────────────────────────
    if PyImGui.button("Log Movement State"):
        _log_movement_state(_overlay)


def _log_movement_state(overlay: dict) -> None:
    """Write a full snapshot of movement + avoidance state to the console log."""
    import math as _m
    import time as _t

    path   = overlay["path"]
    wp_idx = overlay["wp_idx"]
    tol    = overlay["tolerance"]
    ConsoleLog(MODULE_NAME, "=== Movement State Snapshot ===", Console.MessageType.Info, log=True)
    ConsoleLog(MODULE_NAME, f"Path: {len(path)} wps  idx={wp_idx}  tol={tol:.0f}", Console.MessageType.Info, log=True)
    if 0 <= wp_idx < len(path):
        wx, wy = path[wp_idx]
        ConsoleLog(MODULE_NAME, f"Next WP: ({wx:.0f}, {wy:.0f})", Console.MessageType.Info, log=True)
        try:
            px, py = Player.GetXY()
            dist = _m.hypot(wx - px, wy - py)
            ConsoleLog(MODULE_NAME, f"Player: ({px:.0f}, {py:.0f})  dist={dist:.0f}", Console.MessageType.Info, log=True)
        except Exception:
            pass

    avoiding   = overlay.get("avoiding", False)
    strategy   = overlay.get("strategy", "strafe")
    start_ms   = overlay.get("strafe_start_ms", 0.0)
    sample     = overlay.get("avoid_sample")
    goal       = overlay.get("avoid_goal")
    cfg        = overlay.get("avoid_cfg")

    state_str = "AVOIDING" if avoiding else "CLEAR"
    ConsoleLog(MODULE_NAME, f"Avoidance: {state_str}", Console.MessageType.Info, log=True)
    if avoiding:
        elapsed = _t.monotonic() * 1_000.0 - start_ms
        ConsoleLog(MODULE_NAME, f"Strategy: {strategy}  strafe_elapsed={elapsed:.0f}ms", Console.MessageType.Info, log=True)
        if sample is not None:
            side = "LEFT" if sample.is_left else "RIGHT"
            ConsoleLog(
                MODULE_NAME,
                f"Blocker: agent={sample.agent_id}  dist={sample.distance:.0f}"
                f"  dot={sample.dot:.0f}  cross={sample.cross:.0f}  {side}",
                Console.MessageType.Info, log=True,
            )
    if goal is not None:
        ConsoleLog(MODULE_NAME, f"Goal: ({goal[0]:.0f}, {goal[1]:.0f})", Console.MessageType.Info, log=True)
    if cfg is not None:
        ConsoleLog(
            MODULE_NAME,
            f"Config: check_r={cfg.check_radius:.0f}  half_w={cfg.path_half_width:.0f}"
            f"  agent_r={cfg.agent_radius:.0f}  esc_ms={cfg.escalation_ms:.0f}"
            f"  deflect={cfg.deflect_angle_deg:.0f}°  strafe_rel={cfg.strafe_release_margin:.0f}",
            Console.MessageType.Info, log=True,
        )
    ConsoleLog(MODULE_NAME, "=== End Snapshot ===", Console.MessageType.Info, log=True)


def draw():
    """Control panel: Start/Stop/Pause, status, consumable toggles, movement debug."""
    global _setup_err, _initialized

    if not PyImGui.begin(BOT_NAME, True):
        PyImGui.end()
        return

    if PyImGui.begin_tab_bar("TotFTabs"):

        # ── Main tab ──────────────────────────────────────────────────────────
        if PyImGui.begin_tab_item("Main"):

            # header / controls
            if _setup_err:
                PyImGui.text_colored(_setup_err, (1.0, 0.3, 0.3, 1.0))
                if PyImGui.button("Retry Detection"):
                    _setup_err   = ""
                    _initialized = False

            elif _role is None:
                PyImGui.text("Waiting for player to load…")

            else:
                PyImGui.text(f"Role:  {_role.role_id}")

                if _bot_tree is None:
                    if PyImGui.button("Start"):
                        _start_bot()
                else:
                    step  = _bot_tree.GetBlackboardValue("current_step_name") or "—"
                    stuck = _bot_tree.tree.blackboard.get("STUCK", False)
                    PyImGui.text(f"Step:  {step}")
                    if stuck:
                        PyImGui.text_colored("STUCK", (1.0, 0.8, 0.0, 1.0))

                    if _bot_tree.IsPaused():
                        if PyImGui.button("Resume"):
                            _bot_tree.Pause(False)
                    else:
                        if PyImGui.button("Pause"):
                            _bot_tree.Pause(True)

                    PyImGui.same_line(0, -1)
                    if PyImGui.button("Stop"):
                        _stop_bot()

            # outpost panel (role-specific)
            _outpost_ui = getattr(_role, "outpost", None)
            if _bot_tree is not None and _outpost_ui is not None:
                _outpost_ui.draw_section()

            # debug toggles
            PyImGui.separator()
            _verbose_log["enabled"]    = PyImGui.checkbox("Verbose Logging",  _verbose_log["enabled"])
            _movement_debug["enabled"] = PyImGui.checkbox("Movement Debug",   _movement_debug["enabled"])

            # consumables
            PyImGui.separator()
            PyImGui.text("Consumables")
            PyImGui.same_line(0, -1)
            all_on = all(_cons_enabled.get(spec.key, True) for spec in ALL_CONSUMABLES)
            if PyImGui.button("Deselect All" if all_on else "Select All"):
                for spec in ALL_CONSUMABLES:
                    _cons_enabled[spec.key] = not all_on
            PyImGui.columns(2, "cons_cols", False)
            for spec in ALL_CONSUMABLES:
                current = _cons_enabled.get(spec.key, True)
                updated = PyImGui.checkbox(spec.label, current)
                _cons_enabled[spec.key] = updated
                PyImGui.next_column()
            PyImGui.columns(1, "cons_end", False)

            PyImGui.end_tab_item()

        # ── Movement tab ──────────────────────────────────────────────────────
        if PyImGui.begin_tab_item("Movement"):
            _draw_movement_tab()
            PyImGui.end_tab_item()

        PyImGui.end_tab_bar()

    PyImGui.end()

    # 3D world overlay — rendered every frame, independent of the ImGui window.
    if _movement_debug["enabled"]:
        draw_path_overlay()

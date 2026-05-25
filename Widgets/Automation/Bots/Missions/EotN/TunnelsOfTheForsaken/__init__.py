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
# Py4GW loads __init__.py as a bare script; __package__ is None, which makes
# all relative imports below fail.  Register a minimal package stub so Python's
# import machinery can resolve them before we actually use them.
import sys as _sys, os as _os, types as _types
if not __package__:
    import inspect as _inspect
    _frame = _inspect.currentframe()
    _this_file = _frame.f_code.co_filename if _frame is not None else ""
    _d = _os.path.dirname(_os.path.abspath(_this_file))
    _p = _os.path.dirname(_d)
    _n = _os.path.basename(_d)
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
    if _n not in _sys.modules:
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

from Py4GWCoreLib.sc_framework import SCCoordinator, SCRole, SCRuntime

# Derive package name and directory from the stub registered by the bootstrap.
# Used by _reload_submodules() on every Start press.
_PKG_NAME: str = __package__                          # "TunnelsOfTheForsaken"
_PKG_DIR:  str = _sys.modules[_PKG_NAME].__path__[0] # absolute path to this dir

# Initial submodule import — fires @register_role decorators.
from . import roles  # noqa: F401
from .constants import ALL_CONSUMABLES
from .roles._shared import _cons_enabled              # dict kept in sync with draw()

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
    global _bot_tree, _role, _coord, _setup_err, _cons_enabled

    # Preserve the user's consumable toggles across the reload.
    saved_cons = dict(_cons_enabled)

    ConsoleLog(MODULE_NAME, "Reloading submodules…", Console.MessageType.Info)
    _reload_submodules()

    # Re-bind _cons_enabled to the freshly created dict in the reloaded _shared,
    # then restore the saved toggle state so the UI stays consistent.
    new_shared = _importlib.import_module(f"{_PKG_NAME}.roles._shared")
    _cons_enabled = new_shared._cons_enabled
    _cons_enabled.update(saved_cons)

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


def draw():
    """Control panel: Start/Stop/Pause, status, consumable toggles."""
    global _setup_err, _initialized

    if not PyImGui.begin(BOT_NAME, True):
        PyImGui.end()
        return

    # ── header / controls ─────────────────────────────────────────────────────
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

            PyImGui.same_line()
            if PyImGui.button("Stop"):
                _stop_bot()

    # ── consumables ───────────────────────────────────────────────────────────
    PyImGui.separator()
    PyImGui.text("Consumables")
    PyImGui.columns(2, "cons_cols", False)
    for spec in ALL_CONSUMABLES:
        current = _cons_enabled.get(spec.key, True)
        updated = PyImGui.checkbox(spec.label, current)
        _cons_enabled[spec.key] = updated
        PyImGui.next_column()
    PyImGui.columns(1, "cons_end", False)

    PyImGui.end()

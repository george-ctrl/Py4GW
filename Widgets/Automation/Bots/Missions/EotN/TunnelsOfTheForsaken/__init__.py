"""
TunnelsOfTheForsaken/__init__.py — entry point for the TotF Auraway SC bot.

Py4GW calls configure() once on script load, then calls tick() every frame.
Each GW account loads this same file independently; role detection reads the
equipped skill bar to decide whether this account is the Dasher or an Aura.

How to set up:
    1. Fill in all TODO values in constants.py (model IDs, waypoints).
    2. Equip the correct build on each account (see PvX wiki page).
    3. Load this script on all 4 accounts simultaneously.
    4. Press Start — role detection is automatic.

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

import Py4GW
import PyImGui

from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.py4gwcorelib_src.Console import ConsoleLog, Console

from Py4GWCoreLib.sc_framework import SCCoordinator, SCRole, SCRuntime

# Import roles module so @register_role decorators fire before detect() runs.
from . import roles  # noqa: F401

MODULE_NAME = "TotF Auraway"
BOT_NAME    = "Tunnels of the Forsaken"

# ── module-level state ────────────────────────────────────────────────────────
# BottingTree instance created by configure(); ticked every frame by tick().
_bot_tree = None


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_own_email() -> str:
    """
    Retrieve this account's email from shared memory.

    Matches the local player's agent ID against all active account slots to
    find the one that belongs to this GW process.
    """
    own_agent_id = Player.GetAgentID()
    for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
        if acc.IsAccount and acc.AgentData.AgentID == own_agent_id:
            return str(acc.AccountEmail)
    return ""


# ── Py4GW lifecycle ───────────────────────────────────────────────────────────

def configure():
    """
    Called once by Py4GW when the script is loaded.

    Detects the role from the equipped skill bar, wires up the BottingTree,
    and starts the run.  If detection fails (wrong build equipped), logs an
    error and does nothing — the script can be reloaded after fixing the bar.
    """
    global _bot_tree

    email = _get_own_email()
    if not email:
        ConsoleLog(MODULE_NAME, "Could not resolve own account email — is the player loaded?", Console.MessageType.Error)
        return

    try:
        role = SCRole.detect()
    except ValueError as exc:
        ConsoleLog(MODULE_NAME, f"Role detection failed: {exc}", Console.MessageType.Error)
        return

    ConsoleLog(MODULE_NAME, f"Detected role: {role.role_id}", Console.MessageType.Success)

    coord    = SCCoordinator(email)
    _bot_tree = SCRuntime.start(BOT_NAME, coord, role)

    ConsoleLog(MODULE_NAME, f"Bot started ({role.role_id})", Console.MessageType.Success)


def tick():
    """Called every frame by Py4GW.  Drives the BottingTree."""
    if _bot_tree is not None:
        _bot_tree.tick()


def draw_ui():
    """
    Optional: draw a minimal status overlay using PyImGui.

    Shows current role and planner step in the Py4GW overlay so operators
    can verify which phase each account is in.
    """
    if _bot_tree is None:
        return

    if PyImGui.begin(BOT_NAME, True):
        step = _bot_tree.GetBlackboardValue("current_step_name") or "—"
        PyImGui.text(f"Step: {step}")
        PyImGui.text(f"Stuck: {_bot_tree.tree.blackboard.get('STUCK', False)}")
        PyImGui.text(f"SF active: {_bot_tree.tree.blackboard.get('SF_ACTIVE', '?')}")
    PyImGui.end()

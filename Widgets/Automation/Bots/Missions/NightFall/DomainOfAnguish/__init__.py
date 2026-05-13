"""
DomainOfAnguish/__init__.py — entry point for the DoA 3-3 SC bot.

Py4GW calls configure() once on script load, then tick() every frame.
Each GW account loads the same file; role detection reads the equipped
skill bar to decide which of the 8 roles this account is playing.

Team composition (8 players):
    MainTank   (MT)  — A/W, Shadow Form + Dark Escape
    TrenchTank (TT)  — A/W, Shadow Form + Recall
    VoR              — Me/P, Visions of Regret
    TK               — Me/A, Telekinesis
    Empathy          — Me/D, Empathy
    Backfire   (BF)  — Me/Rt, Backfire
    UAMonk     (UA)  — Mo/R, Unyielding Aura
    Emo              — E/Mo, Ether Renewal

Veil 3-3 split:
    Team-A (MT + VoR + UA + Emo)  → tendrils 1-3
    Team-B (TT + TK + Emp + BF)   → derv hill / tendrils 4-6
    Backfire skips Jadoth (unique to 3-3).

Run order: Foundry → Veil → City → Gloom

How to set up:
    1. Fill in all TODO values in constants.py (model IDs, skill IDs, waypoints).
    2. Equip the correct build on each account (see DoA 3-3 wiki builds).
    3. Load this script on all 8 accounts simultaneously.
    4. Press Start — role detection is automatic from the skill bar.

File layout:
    __init__.py         ← you are here (entry point)
    constants.py        ← all DoA-specific IDs, coordinates, and tuning values
    agents.py           ← NPC and enemy lookup helpers
    roles/
        __init__.py     ← imports all roles to register them
        _shared.py      ← service constructors shared by all roles
        tank.py         ← MainTankRole, TrenchTankRole
        spiker.py       ← VoRRole, TKRole, EmpathyRole, BackfireRole
        support.py      ← UAMonkRole, EmoRole
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

MODULE_NAME = "DoA 3-3 SC"
BOT_NAME    = "Domain of Anguish 3-3"

_bot_tree = None


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_own_email() -> str:
    own_agent_id = Player.GetAgentID()
    for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
        if acc.IsAccount and acc.AgentData.AgentID == own_agent_id:
            return str(acc.AccountEmail)
    return ""


# ── Py4GW lifecycle ───────────────────────────────────────────────────────────

def configure():
    """
    Called once when the script loads.

    Detects role from the equipped skill bar, builds the BottingTree,
    and starts the run.  Logs an error (without crashing) if detection
    fails — reload the script after correcting the equipped build.
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

    coord     = SCCoordinator(email)
    _bot_tree = SCRuntime.start(BOT_NAME, coord, role)

    ConsoleLog(MODULE_NAME, f"Bot started ({role.role_id})", Console.MessageType.Success)


def tick():
    """Called every frame by Py4GW."""
    if _bot_tree is not None:
        _bot_tree.tick()


def draw_ui():
    """Minimal status overlay — shows current role, phase, and stuck flag."""
    if _bot_tree is None:
        return

    if PyImGui.begin(BOT_NAME, True):
        step  = _bot_tree.GetBlackboardValue("current_step_name") or "—"
        stuck = _bot_tree.tree.blackboard.get("STUCK", False)
        PyImGui.text(f"Step:  {step}")
        PyImGui.text(f"Stuck: {stuck}")
    PyImGui.end()

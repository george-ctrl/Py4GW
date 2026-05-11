"""
agents.py — TotF NPC and party member lookup helpers.

All functions return an agent ID (int) or None if not found.
They are designed to be passed as callables to SCActions and
PartyFollowService — the framework calls them each tick so they should
be fast (scan is bounded by search_range).
"""

from __future__ import annotations

from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.py4gwcorelib_src.Utils import Utils

from .constants import ModelID, SkillID


# ── generic scan helper ───────────────────────────────────────────────────────

def _nearest_agent_by_model(model_id: int, max_range: float = 5_000.0) -> int | None:
    """
    Scan AgentArray and return the nearest live agent with ``model_id``.

    Returns None if model_id is 0 (not yet filled in constants.py) or if no
    matching agent is within max_range.
    """
    if model_id == 0:
        return None  # constant not filled in yet

    from Py4GWCoreLib.AgentArray import AgentArray
    from Py4GWCoreLib.Agent import Agent

    px, py = Player.GetXY()
    best_id   = None
    best_dist = max_range + 1.0

    for agent_id in AgentArray.GetAgentArray():
        if not agent_id:
            continue
        if not Agent.IsValid(agent_id):
            continue
        if not Agent.IsAlive(agent_id):
            continue
        if Agent.GetModelID(agent_id) != model_id:
            continue
        ax, ay = Agent.GetXY(agent_id)
        dist = Utils.Distance((px, py), (ax, ay))
        if dist < best_dist:
            best_dist = dist
            best_id   = agent_id

    return best_id


# ── TotF-specific lookups ─────────────────────────────────────────────────────

def find_althea() -> int | None:
    """Return the agent ID of Althea (quest-giver NPC, Level 1)."""
    return _nearest_agent_by_model(ModelID.ALTHEA)


def find_enraged_phantom() -> int | None:
    """Return the agent ID of the Enraged Phantom (Level 3 enemy)."""
    return _nearest_agent_by_model(ModelID.ENRAGED_PHANTOM)


def find_varny() -> int | None:
    """Return the agent ID of Varny the Zealot (final boss, Level 3)."""
    return _nearest_agent_by_model(ModelID.VARNY)


def find_dasher() -> int | None:
    """
    Find the Dasher account's agent ID via shared memory skill bars.

    Scans all active accounts and returns the agent ID of the first account
    whose skill bar contains Barbs — the Dasher's unique signature skill.
    Skips this account's own agent ID.

    Called by PartyFollowService; the service caches the result after the
    first successful lookup and only re-calls on cache invalidation.
    """
    own_agent_id = Player.GetAgentID()

    for acc in GLOBAL_CACHE.ShMem.GetAllAccountData():
        # Only look at real player accounts, not heroes or NPCs.
        if not acc.IsAccount:
            continue
        # Skip self.
        if acc.AgentData.AgentID == own_agent_id:
            continue
        # Check if Barbs is on this account's skill bar.
        bar_ids = {
            acc.AgentData.Skillbar.Skills[i].Id
            for i in range(8)
        }
        if SkillID.Barbs in bar_ids:
            return acc.AgentData.AgentID

    return None

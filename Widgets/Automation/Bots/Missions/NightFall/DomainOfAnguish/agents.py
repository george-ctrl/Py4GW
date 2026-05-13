"""
agents.py — agent lookup helpers for DoA 3-3.

All finders return the nearest live agent matching the model ID, or None
if the model ID is still 0 (TODO) or no matching agent is in range.
"""

from __future__ import annotations

from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE

from .constants import ModelID, CLEAR_WAIT_RADIUS


# ── core helper ───────────────────────────────────────────────────────────────

def _nearest_alive(model_id: int):
    """Return the nearest live agent with the given model ID, or None."""
    if model_id == 0:
        return None  # placeholder not yet recorded
    agent = GLOBAL_CACHE.AgentArray.GetNearestAgentByModelID(model_id)
    if agent and GLOBAL_CACHE.AgentArray.GetAgentHP(agent.AgentID) > 0:
        return agent
    return None


def area_clear(center: tuple[float, float], radius: float = CLEAR_WAIT_RADIUS) -> bool:
    """
    Return True when no live hostile agent is within radius of center.

    Used by WaitForCondition nodes after MT/TT signals MT_PULL_SET.
    TODO: swap in the correct AgentArray radius API once confirmed.
    """
    enemy = GLOBAL_CACHE.AgentArray.GetNearestEnemy()
    if enemy is None:
        return True
    ex, ey = GLOBAL_CACHE.AgentArray.GetAgentXY(enemy.AgentID)
    cx, cy = center
    return ((ex - cx) ** 2 + (ey - cy) ** 2) > radius ** 2


# ── Foundry ───────────────────────────────────────────────────────────────────

def find_snake_silzesh():
    return _nearest_alive(ModelID.SNAKE_SILZESH)

def find_snake_yendzarsh():
    return _nearest_alive(ModelID.SNAKE_YENDZARSH)

def find_snake_valkyss():
    return _nearest_alive(ModelID.SNAKE_VALKYSS)

def find_the_fury():
    return _nearest_alive(ModelID.THE_FURY)


# ── Veil ──────────────────────────────────────────────────────────────────────

def find_smothering_tendril():
    """Return the nearest live Smothering Tendril (any of the 6)."""
    return _nearest_alive(ModelID.SMOTHERING_TENDRIL)

def find_dreadspawn_maw():
    return _nearest_alive(ModelID.DREADSPAWN_MAW)

def find_monk_lord():
    return _nearest_alive(ModelID.MONK_LORD)

def find_jadoth():
    return _nearest_alive(ModelID.JADOTH)


# ── City ──────────────────────────────────────────────────────────────────────

def find_city_wall_enemy():
    return _nearest_alive(ModelID.CITY_WALL_ENEMY)


# ── Gloom ─────────────────────────────────────────────────────────────────────

def find_greater_darkness():
    return _nearest_alive(ModelID.GREATER_DARKNESS)

def find_darkness():
    """Return any live standard Darkness from the wave phase."""
    return _nearest_alive(ModelID.DARKNESS)

def find_earth_tormentor():
    return _nearest_alive(ModelID.EARTH_TORMENTOR)

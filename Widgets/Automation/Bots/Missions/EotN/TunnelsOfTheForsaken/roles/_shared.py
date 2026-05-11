"""
_shared.py — service constructors shared by all TotF roles.

This module instantiates the generic sc_framework services with TotF-specific
lambdas.  It is the ONLY place in the TotF scripts that translates framework
patterns into GW skill/effect API calls.

Both DasherRole and AuraRole import the helpers below; AuraRole also adds
EbonEscape follow via PartyFollowService.
"""

from __future__ import annotations

from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree

from Py4GWCoreLib.sc_framework.services import UpkeepService, StuckWatchdog

from ..constants import SkillID, UPKEEP_COOLDOWN_MS, SF_RECAST_BUFFER_MS, SOD_RECAST_BUFFER_MS, STUCK_THRESHOLD_MS


# ── helpers ───────────────────────────────────────────────────────────────────

def _player_id() -> int:
    """Return the local player's agent ID (evaluated each call)."""
    return Player.GetAgentID()


def _slot_for(skill_id: int) -> int:
    """
    Return the skill bar slot (1–8) for a given skill ID, or 0 if not found.

    Scans slots 1–8 via GLOBAL_CACHE.SkillBar.GetSkillIDBySlot each call.
    Cheap enough to call on every upkeep tick.
    """
    for slot in range(1, 9):
        if GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(slot) == skill_id:
            return slot
    return 0


# ── Shadow Form upkeep ────────────────────────────────────────────────────────

def make_sf_upkeep() -> BehaviorTree:
    """
    Shadow Form upkeep service.

    Recasts Shadow Form whenever it is not active or has less than
    SF_RECAST_BUFFER_MS remaining.  Required by both Dasher and Aura roles.
    """
    return UpkeepService.build(
        "ShadowFormUpkeep",
        should_cast_fn=lambda: (
            not GLOBAL_CACHE.Effects.HasEffect(_player_id(), SkillID.ShadowForm)
            or GLOBAL_CACHE.Effects.GetEffectTimeRemaining(_player_id(), SkillID.ShadowForm) < SF_RECAST_BUFFER_MS
        ),
        cast_fn=lambda: GLOBAL_CACHE.SkillBar.UseSkill(_slot_for(SkillID.ShadowForm)),
        cooldown_ms=UPKEEP_COOLDOWN_MS,
    )


# ── Shroud of Distress upkeep ─────────────────────────────────────────────────

def make_sod_upkeep() -> BehaviorTree:
    """
    Shroud of Distress upkeep service.

    Recasts Shroud of Distress whenever it is not active or has less than
    SOD_RECAST_BUFFER_MS remaining.  Required by both roles.
    """
    return UpkeepService.build(
        "ShroudUpkeep",
        should_cast_fn=lambda: (
            not GLOBAL_CACHE.Effects.HasEffect(_player_id(), SkillID.ShroudOfDistress)
            or GLOBAL_CACHE.Effects.GetEffectTimeRemaining(_player_id(), SkillID.ShroudOfDistress) < SOD_RECAST_BUFFER_MS
        ),
        cast_fn=lambda: GLOBAL_CACHE.SkillBar.UseSkill(_slot_for(SkillID.ShroudOfDistress)),
        cooldown_ms=UPKEEP_COOLDOWN_MS,
    )


# ── "I Am Unstoppable!" upkeep ────────────────────────────────────────────────

def make_iau_upkeep() -> BehaviorTree:
    """
    "I Am Unstoppable!" upkeep service.

    Recasts whenever the skill is not active.  The skill ID is stored in the
    skill bar; slot is resolved dynamically so build variants work without
    change.
    """
    IAU_SKILL_ID = 1076   # "I Am Unstoppable!" GW skill ID

    return UpkeepService.build(
        "IAUUpkeep",
        should_cast_fn=lambda: not GLOBAL_CACHE.Effects.HasEffect(_player_id(), IAU_SKILL_ID),
        cast_fn=lambda: GLOBAL_CACHE.SkillBar.UseSkill(_slot_for(IAU_SKILL_ID)),
        cooldown_ms=UPKEEP_COOLDOWN_MS,
    )


# ── Stuck watchdog ────────────────────────────────────────────────────────────

def make_stuck_watchdog(recovery_cast_fn=None) -> BehaviorTree:
    """
    Stuck watchdog service.

    Monitors position delta.  If the player has not moved for STUCK_THRESHOLD_MS,
    calls recovery_cast_fn (e.g. Death's Charge to the nearest ally).
    Pass None to skip the active recovery and only set the STUCK flag.
    """
    return StuckWatchdog.build_service(
        stuck_threshold_ms=STUCK_THRESHOLD_MS,
        on_stuck=recovery_cast_fn,
    )

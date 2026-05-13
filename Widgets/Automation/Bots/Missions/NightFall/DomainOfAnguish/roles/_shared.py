"""
_shared.py — service constructors shared by all DoA roles.

Tanks (MT, TT): SF + SoD upkeep.
Spikers (VoR, TK, Empathy, BF): hex-reapply PeriodicService.
Support (UA, Emo): UA / EtherRenewal upkeep.
All roles share the stuck watchdog.
"""

from __future__ import annotations

from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree

from Py4GWCoreLib.sc_framework.services import UpkeepService, StuckWatchdog, PeriodicService

from ..constants import (
    SkillID,
    UPKEEP_COOLDOWN_MS,
    SF_RECAST_BUFFER_MS,
    SOD_RECAST_BUFFER_MS,
    STUCK_THRESHOLD_MS,
    HEX_REAPPLY_MS,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _player_id() -> int:
    return Player.GetAgentID()


def _slot_for(skill_id: int) -> int:
    """Return skill bar slot (1-8) for the given skill ID, or 0 if absent."""
    for slot in range(1, 9):
        if GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(slot) == skill_id:
            return slot
    return 0


# ── Shadow Form ────────────────────────────────────────────────────────────────

def make_sf_upkeep() -> BehaviorTree:
    return UpkeepService.build(
        "ShadowFormUpkeep",
        should_cast_fn=lambda: (
            not GLOBAL_CACHE.Effects.HasEffect(_player_id(), SkillID.ShadowForm)
            or GLOBAL_CACHE.Effects.GetEffectTimeRemaining(
                _player_id(), SkillID.ShadowForm
            ) < SF_RECAST_BUFFER_MS
        ),
        cast_fn=lambda: GLOBAL_CACHE.SkillBar.UseSkill(_slot_for(SkillID.ShadowForm)),
        cooldown_ms=UPKEEP_COOLDOWN_MS,
    )


# ── Shroud of Distress ─────────────────────────────────────────────────────────

def make_sod_upkeep() -> BehaviorTree:
    return UpkeepService.build(
        "ShroudUpkeep",
        should_cast_fn=lambda: (
            not GLOBAL_CACHE.Effects.HasEffect(_player_id(), SkillID.ShroudOfDistress)
            or GLOBAL_CACHE.Effects.GetEffectTimeRemaining(
                _player_id(), SkillID.ShroudOfDistress
            ) < SOD_RECAST_BUFFER_MS
        ),
        cast_fn=lambda: GLOBAL_CACHE.SkillBar.UseSkill(_slot_for(SkillID.ShroudOfDistress)),
        cooldown_ms=UPKEEP_COOLDOWN_MS,
    )


# ── Unyielding Aura ────────────────────────────────────────────────────────────

def make_ua_upkeep() -> BehaviorTree:
    """UA Monk: keep UnyieldingAura maintained at all times."""
    return UpkeepService.build(
        "UAUpkeep",
        should_cast_fn=lambda: not GLOBAL_CACHE.Effects.HasEffect(
            _player_id(), SkillID.UnyieldingAura
        ),
        cast_fn=lambda: GLOBAL_CACHE.SkillBar.UseSkill(_slot_for(SkillID.UnyieldingAura)),
        cooldown_ms=UPKEEP_COOLDOWN_MS,
    )


# ── Ether Renewal ──────────────────────────────────────────────────────────────

def make_er_upkeep() -> BehaviorTree:
    """Emo: maintain Ether Renewal for energy regeneration (critical in City)."""
    return UpkeepService.build(
        "EtherRenewalUpkeep",
        should_cast_fn=lambda: not GLOBAL_CACHE.Effects.HasEffect(
            _player_id(), SkillID.EtherRenewal
        ),
        cast_fn=lambda: GLOBAL_CACHE.SkillBar.UseSkill(_slot_for(SkillID.EtherRenewal)),
        cooldown_ms=UPKEEP_COOLDOWN_MS,
    )


# ── Hex reapply ────────────────────────────────────────────────────────────────

def make_hex_reapply(skill_id: int, service_name: str) -> BehaviorTree:
    """
    Periodic service that reapplies a spiker hex every HEX_REAPPLY_MS.

    Used by VoR, TK, Empathy, and Backfire roles.  The actual target is
    selected in the planner's spike node; this service handles duration refresh.
    """
    return PeriodicService.build(
        service_name,
        interval_ms=HEX_REAPPLY_MS,
        action_fn=lambda: (
            GLOBAL_CACHE.SkillBar.UseSkill(
                _slot_for(skill_id),
                GLOBAL_CACHE.AgentArray.GetNearestEnemy(),
            )
            if _slot_for(skill_id) and GLOBAL_CACHE.AgentArray.GetNearestEnemy()
            else None
        ),
    )


# ── Stuck watchdog ─────────────────────────────────────────────────────────────

def make_stuck_watchdog(recovery_cast_fn=None) -> BehaviorTree:
    return StuckWatchdog.build_service(
        stuck_threshold_ms=STUCK_THRESHOLD_MS,
        on_stuck=recovery_cast_fn,
    )

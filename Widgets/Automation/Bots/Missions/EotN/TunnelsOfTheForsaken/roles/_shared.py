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
from Py4GWCoreLib.py4gwcorelib_src.Console import ConsoleLog, Console

from Py4GWCoreLib.sc_framework.services import UpkeepService, StuckWatchdog, PeriodicService

from ..constants import (
    SkillID, UPKEEP_COOLDOWN_MS, SF_RECAST_BUFFER_MS, SOD_RECAST_BUFFER_MS,
    STUCK_THRESHOLD_MS, CONS_UPKEEP_INTERVAL_MS, ALL_CONSUMABLES,
)

# ── Verbose logging ───────────────────────────────────────────────────────────

_LOG_TAG = "TotF Auraway"
_verbose_log: dict = {"enabled": False}


def log(msg: str) -> None:
    if _verbose_log["enabled"]:
        ConsoleLog(_LOG_TAG, msg, Console.MessageType.Info)


_movement_debug: dict = {"enabled": False}


def movement_log(msg: str) -> None:
    if _movement_debug["enabled"]:
        ConsoleLog(_LOG_TAG, msg, Console.MessageType.Info)


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
    def _cast():
        GLOBAL_CACHE.SkillBar.UseSkill(_slot_for(SkillID.ShadowForm))

    return UpkeepService.build(
        "ShadowFormUpkeep",
        should_cast_fn=lambda: (
            not GLOBAL_CACHE.Effects.HasEffect(_player_id(), SkillID.ShadowForm)
            or GLOBAL_CACHE.Effects.GetEffectTimeRemaining(_player_id(), SkillID.ShadowForm) < SF_RECAST_BUFFER_MS
        ),
        cast_fn=_cast,
        cooldown_ms=UPKEEP_COOLDOWN_MS,
    )


# ── Shroud of Distress upkeep ─────────────────────────────────────────────────

def make_sod_upkeep() -> BehaviorTree:
    """
    Shroud of Distress upkeep service.

    Recasts Shroud of Distress whenever it is not active or has less than
    SOD_RECAST_BUFFER_MS remaining.  Required by both roles.
    """
    def _cast():
        GLOBAL_CACHE.SkillBar.UseSkill(_slot_for(SkillID.ShroudOfDistress))

    return UpkeepService.build(
        "ShroudUpkeep",
        should_cast_fn=lambda: (
            not GLOBAL_CACHE.Effects.HasEffect(_player_id(), SkillID.ShroudOfDistress)
            or GLOBAL_CACHE.Effects.GetEffectTimeRemaining(_player_id(), SkillID.ShroudOfDistress) < SOD_RECAST_BUFFER_MS
        ),
        cast_fn=_cast,
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
    def _cast():
        GLOBAL_CACHE.SkillBar.UseSkill(_slot_for(SkillID.IAmUnstoppable))

    return UpkeepService.build(
        "IAUUpkeep",
        should_cast_fn=lambda: not GLOBAL_CACHE.Effects.HasEffect(_player_id(), SkillID.IAmUnstoppable),
        cast_fn=_cast,
        cooldown_ms=UPKEEP_COOLDOWN_MS,
    )


# ── Dash upkeep ──────────────────────────────────────────────────────────────

def make_dash_upkeep() -> BehaviorTree:
    """Dash upkeep for the runner. Recasts whenever the stance is not active."""
    def _cast():
        GLOBAL_CACHE.SkillBar.UseSkill(_slot_for(SkillID.Dash))

    return UpkeepService.build(
        "DashUpkeep",
        should_cast_fn=lambda: not GLOBAL_CACHE.Effects.HasEffect(_player_id(), SkillID.Dash),
        cast_fn=_cast,
        cooldown_ms=UPKEEP_COOLDOWN_MS,
    )


# ── Dwarven Stability upkeep ──────────────────────────────────────────────────

def make_dwarven_stability_upkeep() -> BehaviorTree:
    """Dwarven Stability upkeep for the runner. Recasts whenever the stance is not active."""
    def _cast():
        GLOBAL_CACHE.SkillBar.UseSkill(_slot_for(SkillID.DwarvenStability))

    return UpkeepService.build(
        "DwarvenStabilityUpkeep",
        should_cast_fn=lambda: not GLOBAL_CACHE.Effects.HasEffect(_player_id(), SkillID.DwarvenStability),
        cast_fn=_cast,
        cooldown_ms=UPKEEP_COOLDOWN_MS,
    )


# ── Stuck watchdog ────────────────────────────────────────────────────────────

# ── Consumable upkeep ─────────────────────────────────────────────────────────
# _cons_enabled is read by draw_ui() in __init__.py; toggling a key at runtime
# takes effect on the next upkeep tick (no restart needed).

_cons_enabled: dict = {spec.key: True for spec in ALL_CONSUMABLES}


def get_enabled_consumable_specs() -> list:
    """Return (model_id, effect_name) pairs for all currently enabled consumables."""
    return [
        (spec.model_id, spec.effect_name)
        for spec in ALL_CONSUMABLES
        if _cons_enabled.get(spec.key, True)
    ]


def make_consumable_service() -> BehaviorTree:
    """
    Periodic service that re-applies every enabled consumable whose effect is absent.

    Runs every CONS_UPKEEP_INTERVAL_MS.  Each tick iterates only the enabled
    specs, resolves the effect ID once, checks HasEffect, and uses the item
    if the effect is missing and the item is in inventory.
    """
    def _upkeep() -> None:
        pid = Player.GetAgentID()
        for spec in ALL_CONSUMABLES:
            if not _cons_enabled.get(spec.key, True):
                continue
            effect_id = int(GLOBAL_CACHE.Skill.GetID(spec.effect_name) or 0)
            if effect_id <= 0:
                continue
            if GLOBAL_CACHE.Effects.HasEffect(pid, effect_id):
                continue
            item_id = int(GLOBAL_CACHE.Inventory.GetFirstModelID(spec.model_id) or 0)
            if item_id <= 0:
                continue
            log(f"Consumables: applying {spec.label}")
            GLOBAL_CACHE.Inventory.UseItem(item_id)

    return PeriodicService.build(
        "ConsumableUpkeep",
        action_fn=_upkeep,
        interval_ms=CONS_UPKEEP_INTERVAL_MS,
        run_immediately=True,
    )


def make_stuck_watchdog(recovery_cast_fn=None) -> BehaviorTree:
    """
    Stuck watchdog service.

    Monitors position delta.  If the player has not moved for STUCK_THRESHOLD_MS,
    calls recovery_cast_fn (e.g. Death's Charge to the nearest ally).
    Pass None to skip the active recovery and only set the STUCK flag.
    """
    def _on_stuck():
        log("StuckWatchdog: stuck detected — triggering recovery")
        if recovery_cast_fn:
            recovery_cast_fn()

    return StuckWatchdog.build_service(
        stuck_threshold_ms=STUCK_THRESHOLD_MS,
        on_stuck=_on_stuck,
    )

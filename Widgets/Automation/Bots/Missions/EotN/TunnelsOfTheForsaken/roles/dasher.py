"""
dasher.py — TotF Auraway Dasher role.

The Dasher (1 account) is responsible for:
    Getting There   Run from Piken to dungeon entrance using Shadow Form.
    Level 1         Wait for all Auras to grab Althea's quest, then
                    execute the gate glitch to skip to Level 2.
    Level 2         Run the route; Auras follow via Ebon Escape.
    Level 3         Apply hexes (Barbs / MoP variant) to the Enraged Phantom,
                    pick up the boss key, trigger Varny from the cliffside,
                    then maintain Barbs on Varny throughout the fight.

Detection signature:  Barbs AND Dash on the skill bar.

Variant detection (auto, from Optional slot):
    HeartOfShadow equipped → DasherVariant.HOS  (faster L1/L2 jump lines)
    MarkOfPain    equipped → DasherVariant.MOP  (better boss DPS for PUGs)
    anything else          → DasherVariant.STANDARD
"""

from __future__ import annotations

import enum

from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE
from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Py4GWCoreLib.routines_src.behaviourtrees_src.composite import BTComposite

from Py4GWCoreLib.sc_framework import (
    SCRole, register_role, SCCoordinator,
    SCMovement, RecoveryStrategy,
    SCActions,
)

from ._shared import _slot_for
from ..constants import (
    Barriers, Signals, SkillID, ModelID, QuestID,
    Waypoints, PARTY_SIZE, BARBS_INTERVAL_MS,
)
from ..agents import find_enraged_phantom, find_varny
from ._shared import make_sf_upkeep, make_sod_upkeep, make_iau_upkeep, make_stuck_watchdog


# ── Variant ───────────────────────────────────────────────────────────────────

class DasherVariant(enum.Enum):
    STANDARD = "standard"  # Dash gate glitch (default).
    HOS      = "hos"       # Heart of Shadow jump lines (experienced teams).
    MOP      = "mop"       # Mark of Pain on enemies for cleave damage.


def _detect_variant() -> DasherVariant:
    """
    Inspect the Optional slot (slot 7) to select the correct Dasher variant.

    The wiki build uses slot 7 as the Optional slot.  If Heart of Shadow or
    Mark of Pain is equipped there, switch to the matching variant.
    """
    optional_skill = GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(7)
    if optional_skill == SkillID.HeartOfShadow:
        return DasherVariant.HOS
    if optional_skill == SkillID.MarkOfPain:
        return DasherVariant.MOP
    return DasherVariant.STANDARD


# ── Role class ────────────────────────────────────────────────────────────────

@register_role
class DasherRole(SCRole):
    """Dasher role for TotF Auraway."""

    role_id = "dasher"

    @classmethod
    def _matches(cls, skill_bar: list[int]) -> bool:
        """Detected when Barbs AND Dash are both on the skill bar."""
        return SkillID.Barbs in skill_bar and SkillID.Dash in skill_bar

    # ── services ──────────────────────────────────────────────────────────

    def register_services(self) -> list[tuple[str, BehaviorTree]]:
        """
        Parallel services active throughout the entire run.

        Stuck recovery uses Death's Charge to teleport to the nearest ally.
        Dasher does NOT register Ebon Escape follow — that is only for Auras.
        """
        dc_slot = _slot_for(SkillID.DeathsCharge)
        dc_recovery = (
            lambda: GLOBAL_CACHE.SkillBar.UseSkill(dc_slot)
            if dc_slot else None
        )

        return [
            ("ShadowForm",  make_sf_upkeep()),
            ("Shroud",      make_sod_upkeep()),
            ("IAU",         make_iau_upkeep()),
            ("StuckWatch",  make_stuck_watchdog(dc_recovery)),
        ]

    # ── planner ───────────────────────────────────────────────────────────

    def build_planner(self, coord: SCCoordinator) -> BehaviorTree:
        """
        Full Dasher planner sequence.

        Structure:
            GettingThere → Level1 → Level2 → Level3
        """
        variant = _detect_variant()

        return BehaviorTree(
            BTComposite.Sequence(
                _build_getting_there(),
                _build_level1(coord, variant),
                _build_level2(coord),
                _build_level3(coord, variant),
                name="DasherPlanner",
            )
        )


# ── phase builders ────────────────────────────────────────────────────────────

def _sf_active() -> bool:
    """Pre-move safety check: Shadow Form must be active before running."""
    from Py4GWCoreLib.Player import Player
    return GLOBAL_CACHE.Effects.HasEffect(Player.GetAgentID(), SkillID.ShadowForm)


def _build_getting_there() -> BehaviorTree:
    """
    Run from Piken Square to the dungeon entrance.

    Uses SF as the pre-move safety check — we won't move until SF is active.
    Recovery falls back to STRAFE (BTMovement's built-in).
    """
    return BTComposite.Sequence(
        SCMovement.RunPath(
            Waypoints.PIKEN_TO_DUNGEON,
            pre_move_check_fn=_sf_active,
            recovery=RecoveryStrategy.STRAFE,
            name="Dasher:GettingThere",
        ),
        name="GettingThere",
    )


def _build_level1(coord: SCCoordinator, variant: DasherVariant) -> BehaviorTree:
    """
    Level 1 — gate glitch.

    Wait for all 3 Auras to grab Althea's quest (they signal individually),
    then move to the gate and execute the glitch.  Signal GATE_DONE so Auras
    know they can follow.
    """
    # Choose the glitch action based on variant.
    if variant == DasherVariant.HOS:
        glitch_action = _heart_of_shadow_jump()
    else:
        glitch_action = _gate_glitch_dash()

    return BTComposite.Sequence(
        # Yield until all 3 Auras have signalled QUEST_GRABBED.
        coord.wait_for_n_node(Signals.QUEST_GRABBED, n=PARTY_SIZE - 1, name="WaitQuestGrabbed"),
        # Move to the gate glitch position.
        SCMovement.Move(
            *Waypoints.GATE_GLITCH_POS,
            pre_move_check_fn=_sf_active,
            name="Dasher:MoveToGate",
        ),
        # Execute the glitch.
        glitch_action,
        # Tell Auras the gate is done.
        coord.signal_node(Signals.GATE_DONE, name="SignalGateDone"),
        name="Level1",
    )


def _build_level2(coord: SCCoordinator) -> BehaviorTree:
    """
    Level 2 — run the route.

    Auras spam Ebon Escape on the Dasher (handled in their own
    EbonEscapeFollow service) so the Dasher just runs straight through.
    """
    return BTComposite.Sequence(
        SCMovement.RunPath(
            Waypoints.LEVEL2_ROUTE,
            pre_move_check_fn=_sf_active,
            name="Dasher:Level2Run",
        ),
        name="Level2",
    )


def _build_level3(coord: SCCoordinator, variant: DasherVariant) -> BehaviorTree:
    """
    Level 3 — hexes, boss key, trigger, and boss hex loop.

    Sequence:
        1.  Run to Enraged Phantom.
        2.  Apply hexes (Barbs; MoP if variant).
        3.  Wait for all 4 to confirm Phantom is dead.
        4.  Pick up boss key.
        5.  Move to cliffside trigger position.
        6.  Signal boss triggered; all roles rendezvous at boss room.
        7.  Maintain Barbs on Varny until boss dead barrier is satisfied.
    """
    # Hex actions for the Phantom.
    hex_phantom_steps = [
        SCActions.CastSkill(
            slot_fn=lambda: _slot_for(SkillID.Barbs),
            target_fn=find_enraged_phantom,
            name="ApplyBarbs:Phantom",
        ),
    ]
    if variant == DasherVariant.MOP:
        hex_phantom_steps.append(
            SCActions.CastSkill(
                slot_fn=lambda: _slot_for(SkillID.MarkOfPain),
                target_fn=find_enraged_phantom,
                name="ApplyMoP:Phantom",
            )
        )

    return BTComposite.Sequence(
        # ── Enraged Phantom ──────────────────────────────────────────────
        SCMovement.Move(*Waypoints.PHANTOM_POS, pre_move_check_fn=_sf_active, name="Dasher:MoveToPhantom"),
        *hex_phantom_steps,
        coord.barrier_node(Barriers.PHANTOM_DEAD, required=PARTY_SIZE, name="PhantomDeadBarrier"),

        # ── Boss key and trigger ──────────────────────────────────────────
        SCActions.PickupNearestItem(ModelID.BOSS_KEY, name="PickupBossKey"),
        SCMovement.Move(*Waypoints.CLIFFSIDE_TRIGGER, pre_move_check_fn=_sf_active, name="Dasher:MoveToCliffside"),
        _trigger_boss_from_cliffside(),
        coord.signal_node(Signals.BOSS_TRIGGERED, name="SignalBossTriggered"),

        # ── Boss fight ────────────────────────────────────────────────────
        SCMovement.Move(*Waypoints.BOSS_ROOM_POS, pre_move_check_fn=_sf_active, name="Dasher:MoveToBossRoom"),
        coord.barrier_node(Barriers.ALL_AT_BOSS, required=PARTY_SIZE, name="AllAtBossBarrier"),
        _barbs_loop_on_varny(coord, variant),
        coord.barrier_node(Barriers.BOSS_DEAD, required=PARTY_SIZE, name="BossDeadBarrier"),
        name="Level3",
    )


# ── atomic action helpers ─────────────────────────────────────────────────────

def _gate_glitch_dash() -> BehaviorTree:
    """
    Gate glitch using Dash.

    Dash into the gate boundary at the recorded GATE_GLITCH_POS.
    The exact movement is a micro-path recorded at the glitch point;
    adjust GATE_GLITCH_POS in constants.py for the correct angle.
    """
    return SCActions.CastSkill(
        slot_fn=lambda: _slot_for(SkillID.Dash),
        name="GateGlitch:Dash",
    )


def _heart_of_shadow_jump() -> BehaviorTree:
    """
    Gate glitch using Heart of Shadow (HOS variant).

    HoS teleports the player forward ~300 units, allowing the gate to be
    bypassed without stopping.  See Toolbox Notes on the wiki for jump lines.
    """
    return SCActions.CastSkill(
        slot_fn=lambda: _slot_for(SkillID.HeartOfShadow),
        name="GateGlitch:HoS",
    )


def _trigger_boss_from_cliffside() -> BehaviorTree:
    """
    Trigger Varny the Zealot by interacting from the cliffside.

    The exact interaction depends on the dungeon mechanic (move to trigger
    zone).  This is currently modelled as a simple positional action —
    moving to CLIFFSIDE_TRIGGER is sufficient; refine if a specific
    object interaction is needed.
    """
    # Moving to CLIFFSIDE_TRIGGER in Level3 sequence IS the trigger.
    # This node signals that the trigger has been reached and returns SUCCESS.
    def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        return BehaviorTree.NodeState.SUCCESS  # already at the spot from prior Move

    return BehaviorTree(BehaviorTree.ActionNode(_tick, name="TriggerBoss"))


def _barbs_loop_on_varny(coord: SCCoordinator, variant: DasherVariant) -> BehaviorTree:
    """
    Maintain Barbs (and optionally MoP) on Varny until the boss is dead.

    The loop re-applies Barbs every BARBS_INTERVAL_MS.  IsLockSatisfied is
    polled each tick via a ConditionNode wrapping the barrier check; when
    satisfied the Sequence completes and the planner advances.

    This is implemented as a RepeaterForeverNode gated by a BOSS_DEAD
    condition check in a SelectorNode:
        SelectorNode(
            BossDeadCheck → SUCCESS (exits loop),
            ReapplyBarbs  → RUNNING (keeps looping),
        )
    """
    import time

    state: dict = {"last_barbs_ms": 0.0}

    def _barbs_tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        from Py4GWCoreLib.py4gwcorelib_src.Utils import Utils
        import Py4GW

        # Exit condition: barrier already satisfied by all roles.
        if coord._is_satisfied(Barriers.BOSS_DEAD, PARTY_SIZE):
            return BehaviorTree.NodeState.SUCCESS

        now = time.monotonic() * 1_000.0
        if (now - state["last_barbs_ms"]) >= BARBS_INTERVAL_MS:
            target = find_varny()
            if target:
                GLOBAL_CACHE.SkillBar.UseSkill(_slot_for(SkillID.Barbs), target)
                if variant == DasherVariant.MOP:
                    mop_slot = _slot_for(SkillID.MarkOfPain)
                    if mop_slot:
                        GLOBAL_CACHE.SkillBar.UseSkill(mop_slot, target)
            state["last_barbs_ms"] = now

        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(BehaviorTree.ActionNode(_barbs_tick, name="BarbsLoop"))

"""
aura.py — TotF Auraway Aura (damage dealer) role.

All three Aura accounts run this same role script.  Each acts independently
and coordinates with the Dasher and the other Auras via shared-memory
barriers and signals.

Responsibilities:
    Getting There   Run from Piken to dungeon entrance using Shadow Form.
    Level 1         Locate Althea, take her quest, signal QUEST_GRABBED,
                    then wait for the Dasher to signal GATE_DONE.
    Level 2         Spam Ebon Escape on the Dasher to keep up without
                    stopping to recast SF/SoD (handled by EbonEscapeFollow
                    service running in parallel).
    Level 3         DPS Enraged Phantom → barrier → run to boss room →
                    barrier → DPS Varny until BOSS_DEAD barrier satisfied.

Detection signature:  Grenth's Aura (GrentsAura) AND Ebon Escape on the bar.
"""

from __future__ import annotations

from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Py4GWCoreLib.routines_src.behaviourtrees_src.composite import BTComposite

from Py4GWCoreLib.sc_framework import (
    SCRole, register_role, SCCoordinator,
    SCMovement, RecoveryStrategy,
    SCActions,
    PartyFollowService,
)

from ..constants import (
    Barriers, Signals, SkillID, QuestID,
    Waypoints, PARTY_SIZE, EE_TRIGGER_DISTANCE, EE_COOLDOWN_MS,
)
from ..agents import find_althea, find_enraged_phantom, find_varny, find_dasher
from ._shared import make_sf_upkeep, make_sod_upkeep, make_iau_upkeep, make_stuck_watchdog


# ── Role class ────────────────────────────────────────────────────────────────

@register_role
class AuraRole(SCRole):
    """Aura (damage dealer) role for TotF Auraway.  All 3 Aura accounts use this."""

    role_id = "aura"

    @classmethod
    def _matches(cls, skill_bar: list[int]) -> bool:
        """Detected when Grenth's Aura AND Ebon Escape are both on the skill bar."""
        return SkillID.GrentsAura in skill_bar and SkillID.EbonEscape in skill_bar

    # ── services ──────────────────────────────────────────────────────────

    def register_services(self) -> list[tuple[str, BehaviorTree]]:
        """
        Parallel services for Aura accounts.

        EbonEscapeFollow is the Aura-specific addition: it keeps the Aura
        near the Dasher during Level 2 by casting EE whenever the distance
        exceeds EE_TRIGGER_DISTANCE.  The target (Dasher agent ID) is
        resolved lazily via find_dasher() and cached by the service.

        Stuck recovery also uses Death's Charge if the slot is equipped,
        otherwise only the STUCK blackboard flag is set.
        """
        ee_slot = _slot_for_aura(SkillID.EbonEscape)
        dc_slot = _slot_for_aura(SkillID.DeathsCharge)

        dc_recovery = (
            (lambda: GLOBAL_CACHE.SkillBar.UseSkill(dc_slot))
            if dc_slot else None
        )

        ee_follow = PartyFollowService.build(
            "EbonEscapeFollow",
            target_fn=find_dasher,
            follow_fn=lambda aid: GLOBAL_CACHE.SkillBar.UseSkill(ee_slot, aid),
            max_distance=EE_TRIGGER_DISTANCE,
            cooldown_ms=EE_COOLDOWN_MS,
        )

        return [
            ("ShadowForm",      make_sf_upkeep()),
            ("Shroud",          make_sod_upkeep()),
            ("IAU",             make_iau_upkeep()),
            ("EbonEscapeFollow", ee_follow),
            ("StuckWatch",      make_stuck_watchdog(dc_recovery)),
        ]

    # ── planner ───────────────────────────────────────────────────────────

    def build_planner(self, coord: SCCoordinator) -> BehaviorTree:
        """Full Aura planner sequence: GettingThere → Level1 → Level2 → Level3."""
        return BehaviorTree(
            BTComposite.Sequence(
                _build_getting_there(),
                _build_level1(coord),
                _build_level2(coord),
                _build_level3(coord),
                name="AuraPlanner",
            )
        )


# ── shared helper ─────────────────────────────────────────────────────────────

def _slot_for_aura(skill_id: int) -> int:
    """Return the skill bar slot for a given skill ID, or 0 if not equipped."""
    for slot in range(1, 9):
        if GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(slot) == skill_id:
            return slot
    return 0


def _sf_active() -> bool:
    """Pre-move safety check: Shadow Form must be active before running."""
    return GLOBAL_CACHE.Effects.HasEffect(Player.GetAgentID(), SkillID.ShadowForm)


# ── phase builders ────────────────────────────────────────────────────────────

def _build_getting_there() -> BehaviorTree:
    """
    Run from Piken Square to the dungeon entrance.

    Identical to the Dasher's Getting There phase — all roles enter together.
    """
    return BTComposite.Sequence(
        SCMovement.RunPath(
            Waypoints.PIKEN_TO_DUNGEON,
            pre_move_check_fn=_sf_active,
            recovery=RecoveryStrategy.STRAFE,
            name="Aura:GettingThere",
        ),
        name="GettingThere",
    )


def _build_level1(coord: SCCoordinator) -> BehaviorTree:
    """
    Level 1 — take Althea's quest, then follow Dasher through the gate.

    Each Aura independently moves to Althea, accepts the quest, and signals
    QUEST_GRABBED.  The Dasher's wait_for_n_node counts all 3 signals before
    glitching the gate.  After signalling, the Aura waits for GATE_DONE before
    moving on so it doesn't outrun the Dasher.
    """
    return BTComposite.Sequence(
        # Move to Althea and take the quest.
        SCMovement.Move(*Waypoints.ALTHEA_POS, pre_move_check_fn=_sf_active, name="Aura:MoveToAlthea"),
        SCActions.TakeQuest(
            quest_id=QuestID.ALTHEA_QUEST,
            npc_agent_id_fn=find_althea,
            name="TakeAltheaQuest",
        ),
        # Signal to the Dasher that this Aura has the quest.
        coord.signal_node(Signals.QUEST_GRABBED, name="SignalQuestGrabbed"),
        # Wait for Dasher to complete the gate glitch.
        coord.wait_for_n_node(Signals.GATE_DONE, n=1, name="WaitGateDone"),
        name="Level1",
    )


def _build_level2(coord: SCCoordinator) -> BehaviorTree:
    """
    Level 2 — run with the Dasher.

    The EbonEscapeFollow service (registered in register_services) runs in
    parallel and casts EE on the Dasher whenever distance > EE_TRIGGER_DISTANCE.
    This phase just needs to advance the planner; the follow is automatic.
    """
    return BTComposite.Sequence(
        SCMovement.RunPath(
            Waypoints.LEVEL2_ROUTE,
            pre_move_check_fn=_sf_active,
            name="Aura:Level2Run",
        ),
        name="Level2",
    )


def _build_level3(coord: SCCoordinator) -> BehaviorTree:
    """
    Level 3 — kill Enraged Phantom, then DPS Varny.

    Sequence:
        1.  Run to Phantom position.
        2.  Spam damage skills on Phantom until PHANTOM_DEAD barrier.
        3.  Wait for Dasher to signal BOSS_TRIGGERED.
        4.  Run to boss room and rendezvous (ALL_AT_BOSS barrier).
        5.  DPS Varny until BOSS_DEAD barrier is satisfied.
    """
    return BTComposite.Sequence(
        # ── Enraged Phantom ──────────────────────────────────────────────
        SCMovement.Move(*Waypoints.PHANTOM_POS, pre_move_check_fn=_sf_active, name="Aura:MoveToPhantom"),
        _dps_loop(coord, Barriers.PHANTOM_DEAD, find_enraged_phantom, "PhantomDPS"),
        coord.barrier_node(Barriers.PHANTOM_DEAD, required=PARTY_SIZE, name="PhantomDeadBarrier"),

        # ── Boss ─────────────────────────────────────────────────────────
        coord.wait_for_n_node(Signals.BOSS_TRIGGERED, n=1, name="WaitBossTriggered"),
        SCMovement.Move(*Waypoints.BOSS_ROOM_POS, pre_move_check_fn=_sf_active, name="Aura:MoveToBossRoom"),
        coord.barrier_node(Barriers.ALL_AT_BOSS, required=PARTY_SIZE, name="AllAtBossBarrier"),
        _dps_loop(coord, Barriers.BOSS_DEAD, find_varny, "VarnyDPS"),
        coord.barrier_node(Barriers.BOSS_DEAD, required=PARTY_SIZE, name="BossDeadBarrier"),
        name="Level3",
    )


# ── DPS loop helper ───────────────────────────────────────────────────────────

def _dps_loop(
    coord: SCCoordinator,
    done_barrier: int,
    target_fn,
    name: str,
) -> BehaviorTree:
    """
    Generic DPS loop: cycle Grenth's Aura + Unseen Fury until done_barrier fires.

    The loop checks the barrier each tick via coord._is_satisfied().  When
    all PARTY_SIZE accounts have posted the barrier, SUCCESS is returned and
    the planner advances.

    Args:
        coord:        SCCoordinator for barrier polling.
        done_barrier: Barrier ID that signals the target is dead.
        target_fn:    Returns the current target agent ID (or None).
        name:         Label for BT debug output.
    """
    import time
    state: dict = {"last_skill_ms": 0.0}
    SKILL_INTERVAL_MS = 500  # How often to cycle skills.

    def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        # Done when the barrier is satisfied by all roles.
        if coord._is_satisfied(done_barrier, PARTY_SIZE):
            return BehaviorTree.NodeState.SUCCESS

        now = time.monotonic() * 1_000.0
        target = target_fn()

        if target and (now - state["last_skill_ms"]) >= SKILL_INTERVAL_MS:
            # Cycle Grenth's Aura, then Unseen Fury on the target.
            grents_slot = _slot_for_aura(SkillID.GrentsAura)
            fury_slot   = _slot_for_aura(SkillID.UnseenFury)
            if grents_slot:
                GLOBAL_CACHE.SkillBar.UseSkill(grents_slot, target)
            if fury_slot:
                GLOBAL_CACHE.SkillBar.UseSkill(fury_slot, target)
            state["last_skill_ms"] = now

        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(BehaviorTree.ActionNode(_tick, name=name))

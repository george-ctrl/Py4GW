"""
dasher.py — TotF Auraway Main Runner role.

The Main Runner (1 account) is responsible for:
    Getting There   Travel from Piken Square through Verdant Cascades and
                    enter Tunnels of the Forsaken.
    Level 1         Run the full Level 1 path, perform the Heart of Shadow
                    wall-skip, run the post-skip path, execute the gate clip,
                    then wait for all Auras to have grabbed their quest before
                    signalling GATE_DONE.
    Level 2         Run the route; Auras follow via Ebon Escape.
    Level 3         Apply hexes (Barbs / MoP variant) to the Enraged Phantom,
                    pick up the boss key, trigger Varny from the cliffside,
                    then maintain Barbs on Varny throughout the fight.

Detection signature:  Barbs AND Dash on the skill bar.

Variant (Optional slot, slot 7):
    MarkOfPain equipped → DasherVariant.MOP  (extra cleave on boss)
    anything else       → DasherVariant.STANDARD
"""

from __future__ import annotations

import enum
import time

from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.Agent import Agent
from Py4GWCoreLib.AgentArray import AgentArray
from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Py4GWCoreLib.py4gwcorelib_src.Utils import Utils
from Py4GWCoreLib.routines_src.behaviourtrees_src.composite import BTComposite
from Py4GWCoreLib.sc_framework import (
    SCRole, register_role, SCCoordinator,
    SCMovement, RecoveryStrategy,
    SCActions,
)
from Py4GWCoreLib.sc_framework.avoidance import AvoidanceConfig

from ._gate_clip import GateClipper
from ._shared import _slot_for, log, movement_log
from ..constants import (
    MapID, Barriers, Signals, SkillID, ModelID,
    Waypoints, HoSSkip, PARTY_SIZE, BARBS_INTERVAL_MS,
)
from ..outpost import OutpostHandler
from ..agents import find_enraged_phantom, find_varny
from ._shared import (
    make_sf_upkeep, make_sod_upkeep, make_iau_upkeep,
    make_dash_upkeep, make_dwarven_stability_upkeep,
    make_stuck_watchdog, make_consumable_service,
    set_watchdog_paused, suppress_watchdog_for,
)


# ── Variant ───────────────────────────────────────────────────────────────────

class DasherVariant(enum.Enum):
    STANDARD = "standard"
    MOP      = "mop"       # Mark of Pain in Optional slot for extra boss DPS


def _detect_variant() -> DasherVariant:
    optional_skill = GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(7)
    if optional_skill == SkillID.MarkOfPain:
        return DasherVariant.MOP
    return DasherVariant.STANDARD


# ── Role class ────────────────────────────────────────────────────────────────

@register_role
class DasherRole(SCRole):
    """Main Runner role for TotF Auraway."""

    role_id = "dasher"

    @classmethod
    def _matches(cls, skill_bar: list[int]) -> bool:
        return SkillID.Barbs in skill_bar and SkillID.Dash in skill_bar

    def register_services(self) -> list[tuple[str, BehaviorTree]]:
        self.outpost = OutpostHandler(
            exit_pos=Waypoints.PIKEN_OUTPOST_EXIT,
            label="Outpost — Piken Square",
        )
        dc_slot = _slot_for(SkillID.DeathsCharge)
        dc_recovery = (
            lambda: GLOBAL_CACHE.SkillBar.UseSkill(dc_slot)
            if dc_slot else None
        )
        return [
            ("ShadowForm",       make_sf_upkeep()),
            ("Shroud",           make_sod_upkeep()),
            ("IAU",              make_iau_upkeep()),
            ("Dash",             make_dash_upkeep()),
            ("DwarvenStability", make_dwarven_stability_upkeep()),
            ("Consumables",      make_consumable_service()),
            ("StuckWatch",       make_stuck_watchdog(dc_recovery)),
        ]

    def build_planner(self, coord: SCCoordinator) -> BehaviorTree:
        variant = _detect_variant()
        return BTComposite.Sequence(
            self.outpost.build_node(name="Dasher:Outpost"),
            _build_getting_there(),
            _build_level1(coord),
            _build_level2(coord),
            _build_level3(coord, variant),
            name="DasherPlanner",
        )


# ── shared helpers ────────────────────────────────────────────────────────────

def _mono_ms() -> float:
    return time.monotonic() * 1_000.0

def _point_in_polygon(px: float, py: float, polygon: tuple) -> bool:
    """Ray-casting point-in-polygon test (Jordan curve theorem)."""
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


# ── Getting There ─────────────────────────────────────────────────────────────

def _build_getting_there() -> BehaviorTree:
    """
    Travel from Verdant Cascades to TotF Level 1.

    Outpost exit is handled by the OutpostHandler step that precedes this in
    the planner sequence.  OutpostHandler returns SUCCESS as soon as the map
    is no longer an outpost (including during the loading screen), so this
    function must wait for VC to finish loading before issuing any movement.

    1. Wait until Verdant Cascades is fully loaded and explorable.
    2. RunPath through Verdant Cascades with avoidance to the dungeon
       entrance portal.  SF is maintained by the parallel upkeep service.
       Walking into the portal crosses the zone line automatically.
    3. Wait for the TotF Level 1 instance to finish loading.
    """
    from Py4GWCoreLib.Map import Map

    return BTComposite.Sequence(
        SCActions.WaitForCondition(
            lambda: Map.IsExplorable() and Map.GetMapID() == MapID.VERDANT_CASCADES,
            timeout_ms=30_000,
            name="Dasher:WaitVerdantLoad",
        ),
        SCMovement.RunPath(
            Waypoints.VERDANT_TO_DUNGEON,
            recovery=RecoveryStrategy.STRAFE,
            avoidance=AvoidanceConfig(),
            log_fn=movement_log,
            name="Dasher:GettingThere",
        ),
        SCActions.WaitForCondition(
            lambda: Map.IsExplorable() and Map.GetMapID() == MapID.TUNNELS_LEVEL1,
            timeout_ms=30_000,
            name="Dasher:WaitDungeonLoad",
        ),
        name="GettingThere",
    )


# ── Level 1 ───────────────────────────────────────────────────────────────────

def _build_level1(coord: SCCoordinator) -> BehaviorTree:
    """
    Level 1 sequence for the Main Runner.

    1. Run the 17-waypoint Level 1 path toward the HoS skip wall.
    2. Heart of Shadow skip over the wall.
    3. Run the 16-waypoint post-skip path to the gate area.
    4. Gate clip through the Level 1 gate.
    5. Wait for all Auras to signal QUEST_GRABBED (safety valve if runner is too fast).
    6. Signal GATE_DONE so Auras know they can proceed.
    """
    hos_pos = Waypoints.LEVEL1_MAIN_RUNNER[-1]
    return BTComposite.Sequence(
        SCMovement.RunPath(
            Waypoints.LEVEL1_MAIN_RUNNER[:-1],
            recovery=RecoveryStrategy.STRAFE,
            avoidance=AvoidanceConfig(),
            log_fn=movement_log,
            name="Dasher:Level1Run",
        ),
        # Final approach to the HoS skip wall — no avoidance so enemies near
        # the wall do not deflect the character away from the required position.
        SCMovement.Move(
            *hos_pos,
            log_fn=movement_log,
            name="Dasher:HoSApproach",
        ),
        _build_hos_skip(),
        SCMovement.RunPath(
            Waypoints.HOS_POST_SKIP,
            recovery=RecoveryStrategy.STRAFE,
            avoidance=AvoidanceConfig(),
            log_fn=movement_log,
            name="Dasher:PostSkipRun",
        ),
        _build_gate_clip_node(),
        suppress_watchdog_for(
            coord.wait_for_n_node(Signals.QUEST_GRABBED, n=PARTY_SIZE - 1, name="WaitQuestGrabbed", log_fn=log),
            name="WaitQuestGrabbed",
        ),
        coord.signal_node(Signals.GATE_DONE, name="SignalGateDone", log_fn=log),
        name="Level1",
    )


# ── Heart of Shadow skip ──────────────────────────────────────────────────────

def _build_hos_skip() -> BehaviorTree:
    """
    Jump over the Level 1 wall using Heart of Shadow.

    The skip targets the nearest alive hostile that is generally west of the
    player (agent.x < player.x - HoSSkip.WEST_THRESHOLD) and within cast
    range.  After casting, success is confirmed when the player lands inside
    HoSSkip.SUCCESS_POLYGON (the known landing area on the other side of the wall).

    State machine:
        FIND_TARGET  scan enemies; if found advance to CAST; timeout → retry
        CAST         fire HoS; record pre-skip position; advance to VERIFY
        VERIFY       poll position; success → SUCCESS; timeout → retry
    """
    state: dict = {
        "phase":         "find_target",
        "target_id":     None,
        "pre_pos":       None,
        "phase_ms":      0.0,
        "retries":       0,
    }

    def _transition(phase: str) -> None:
        log(f"HoSSkip: → {phase}")
        state["phase"]    = phase
        state["phase_ms"] = _mono_ms()

    def _find_west_target() -> int | None:
        px, py = Player.GetXY()
        best_id   = None
        best_dist = HoSSkip.CAST_RANGE
        try:
            for aid in AgentArray.GetEnemyArray():
                if not Agent.IsAlive(aid):
                    continue
                ax, ay = Agent.GetXY(aid)
                if ax > px - HoSSkip.WEST_THRESHOLD:
                    continue
                dist = Utils.Distance((px, py), (ax, ay))
                if dist < best_dist:
                    best_dist = dist
                    best_id   = aid
        except Exception:
            pass
        return best_id

    def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        phase   = state["phase"]
        elapsed = _mono_ms() - state["phase_ms"]

        if phase == "find_target":
            target = _find_west_target()
            if target is not None:
                state["target_id"] = target
                state["pre_pos"]   = Player.GetXY()
                _transition("cast")
            elif elapsed >= HoSSkip.FIND_TIMEOUT_MS:
                state["retries"] += 1
                if state["retries"] > HoSSkip.MAX_RETRIES:
                    log("HoSSkip: FAILED — max retries reached")
                    return BehaviorTree.NodeState.FAILURE
                log(f"HoSSkip: target not found, retry {state['retries']}/{HoSSkip.MAX_RETRIES}")
                _transition("find_target")

        elif phase == "cast":
            slot = _slot_for(SkillID.HeartOfShadow)
            if slot:
                GLOBAL_CACHE.SkillBar.UseSkill(slot, state["target_id"])
            _transition("verify")

        elif phase == "verify":
            px, py = Player.GetXY()
            if _point_in_polygon(px, py, HoSSkip.SUCCESS_POLYGON):
                log("HoSSkip: SUCCESS")
                state["phase"]   = "find_target"
                state["retries"] = 0
                return BehaviorTree.NodeState.SUCCESS
            if elapsed >= HoSSkip.VERIFY_MS:
                state["retries"] += 1
                if state["retries"] > HoSSkip.MAX_RETRIES:
                    log("HoSSkip: FAILED — max retries reached")
                    return BehaviorTree.NodeState.FAILURE
                log(f"HoSSkip: cast did not land, retry {state['retries']}/{HoSSkip.MAX_RETRIES}")
                _transition("find_target")

        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(BehaviorTree.ActionNode(_tick, name="HoSSkip"))


# ── Gate clip ─────────────────────────────────────────────────────────────────

def _build_gate_clip_node() -> BehaviorTree:
    dc_slot = _slot_for(SkillID.DeathsCharge)

    def _dc_fallback() -> None:
        if not dc_slot:
            return
        px, py   = Player.GetXY()
        best_id  = None
        best_d   = float("inf")
        try:
            for aid in AgentArray.GetEnemyArray():
                if not Agent.IsAlive(aid):
                    continue
                ax, ay = Agent.GetXY(aid)
                d = Utils.Distance((px, py), (ax, ay))
                if 200.0 < d < 700.0 and d < best_d:
                    best_d  = d
                    best_id = aid
        except Exception:
            pass
        if best_id:
            log(f"GateClip: DC fallback → agent {best_id} (d={best_d:.0f})")
            GLOBAL_CACHE.SkillBar.UseSkill(dc_slot, best_id)

    clipper = GateClipper(dc_fn=_dc_fallback, log_fn=log)

    def _tick(_: BehaviorTree.Node) -> BehaviorTree.NodeState:
        set_watchdog_paused(clipper.at_wall)
        return clipper.update()

    return BehaviorTree(BehaviorTree.ActionNode(_tick, name="GateClip"))


# ── Level 2 ───────────────────────────────────────────────────────────────────

def _build_level2(coord: SCCoordinator) -> BehaviorTree:
    return BTComposite.Sequence(
        SCMovement.RunPath(
            Waypoints.LEVEL2_ROUTE,
            avoidance=AvoidanceConfig(),
            log_fn=movement_log,
            name="Dasher:Level2Run",
        ),
        name="Level2",
    )


# ── Level 3 ───────────────────────────────────────────────────────────────────

def _build_level3(coord: SCCoordinator, variant: DasherVariant) -> BehaviorTree:
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
        SCMovement.Move(*Waypoints.PHANTOM_POS, avoidance=AvoidanceConfig(), log_fn=movement_log, name="Dasher:MoveToPhantom"),
        *hex_phantom_steps,
        suppress_watchdog_for(
            coord.barrier_node(Barriers.PHANTOM_DEAD, required=PARTY_SIZE, name="PhantomDeadBarrier", log_fn=log),
            name="PhantomDeadBarrier",
        ),

        SCActions.PickupNearestItem(ModelID.BOSS_KEY, name="PickupBossKey"),
        SCMovement.Move(*Waypoints.CLIFFSIDE_TRIGGER, avoidance=AvoidanceConfig(), log_fn=movement_log, name="Dasher:MoveToCliffside"),
        coord.signal_node(Signals.BOSS_TRIGGERED, name="SignalBossTriggered", log_fn=log),

        SCMovement.Move(*Waypoints.BOSS_ROOM_POS, avoidance=AvoidanceConfig(), log_fn=movement_log, name="Dasher:MoveToBossRoom"),
        suppress_watchdog_for(
            coord.barrier_node(Barriers.ALL_AT_BOSS, required=PARTY_SIZE, name="AllAtBossBarrier", log_fn=log),
            name="AllAtBossBarrier",
        ),
        _barbs_loop_on_varny(coord, variant),
        suppress_watchdog_for(
            coord.barrier_node(Barriers.BOSS_DEAD, required=PARTY_SIZE, name="BossDeadBarrier", log_fn=log),
            name="BossDeadBarrier",
        ),
        name="Level3",
    )


def _barbs_loop_on_varny(coord: SCCoordinator, variant: DasherVariant) -> BehaviorTree:
    state: dict = {"last_barbs_ms": 0.0}

    def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        if coord._is_satisfied(Barriers.BOSS_DEAD, PARTY_SIZE):
            return BehaviorTree.NodeState.SUCCESS

        now = _mono_ms()
        if (now - state["last_barbs_ms"]) >= BARBS_INTERVAL_MS:
            target = find_varny()
            if target:
                log(f"BarbsLoop: applying Barbs on Varny (agent {target})")
                GLOBAL_CACHE.SkillBar.UseSkill(_slot_for(SkillID.Barbs), target)
                if variant == DasherVariant.MOP:
                    mop_slot = _slot_for(SkillID.MarkOfPain)
                    if mop_slot:
                        log("BarbsLoop: applying Mark of Pain")
                        GLOBAL_CACHE.SkillBar.UseSkill(mop_slot, target)
            else:
                log("BarbsLoop: Varny not found this tick")
            state["last_barbs_ms"] = now

        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(BehaviorTree.ActionNode(_tick, name="BarbsLoop"))

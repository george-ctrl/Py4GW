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
import math
import time

from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE
from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.Agent import Agent
from Py4GWCoreLib.AgentArray import AgentArray
from Py4GWCoreLib.Camera import Camera
from Py4GWCoreLib.UIManager import UIManager
from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Py4GWCoreLib.py4gwcorelib_src.Utils import Utils
from Py4GWCoreLib.routines_src.behaviourtrees_src.composite import BTComposite
from Py4GWCoreLib.enums_src.UI_enums import ControlAction

from Py4GWCoreLib.sc_framework import (
    SCRole, register_role, SCCoordinator,
    SCMovement, RecoveryStrategy,
    SCActions,
)
from Py4GWCoreLib.sc_framework.avoidance import AvoidanceConfig

from ._shared import _slot_for, log, movement_log
from ..constants import (
    MapID, Barriers, Signals, SkillID, ModelID,
    Waypoints, GateClip, HoSSkip, PARTY_SIZE, BARBS_INTERVAL_MS,
)
from ..outpost import OutpostHandler
from ..agents import find_enraged_phantom, find_varny
from ._shared import (
    make_sf_upkeep, make_sod_upkeep, make_iau_upkeep,
    make_dash_upkeep, make_dwarven_stability_upkeep,
    make_stuck_watchdog, make_consumable_service,
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

def _sf_active() -> bool:
    return GLOBAL_CACHE.Effects.HasEffect(Player.GetAgentID(), SkillID.ShadowForm)

def _mono_ms() -> float:
    return time.monotonic() * 1_000.0


# ── Getting There ─────────────────────────────────────────────────────────────

def _build_getting_there() -> BehaviorTree:
    """
    Travel from The Breach / Verdant Cascades to TotF Level 1.

    Outpost exit is handled by the OutpostHandler step that precedes this in
    the planner sequence.  This function owns only the explorable portion:

    1. RunPath through Verdant Cascades with avoidance to the dungeon
       entrance portal.  SF must be active before each step (cast by the
       upkeep service while the node waits with RUNNING).
       Walking into the portal crosses the zone line automatically — no
       NPC interaction required.
    2. Wait for the instance map to finish loading.
    """
    from Py4GWCoreLib.Map import Map

    return BTComposite.Sequence(
        SCMovement.RunPath(
            Waypoints.VERDANT_TO_DUNGEON,
            pre_move_check_fn=_sf_active,
            recovery=RecoveryStrategy.STRAFE,
            avoidance=AvoidanceConfig(),
            log_fn=movement_log,
            name="Dasher:GettingThere",
        ),
        SCActions.WaitForCondition(
            lambda: Map.GetMapID() == MapID.TUNNELS_LEVEL1,
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
    return BTComposite.Sequence(
        SCMovement.RunPath(
            Waypoints.LEVEL1_MAIN_RUNNER,
            pre_move_check_fn=_sf_active,
            recovery=RecoveryStrategy.STRAFE,
            avoidance=AvoidanceConfig(),
            log_fn=movement_log,
            name="Dasher:Level1Run",
        ),
        _build_hos_skip(),
        SCMovement.RunPath(
            Waypoints.HOS_POST_SKIP,
            pre_move_check_fn=_sf_active,
            recovery=RecoveryStrategy.STRAFE,
            avoidance=AvoidanceConfig(),
            log_fn=movement_log,
            name="Dasher:PostSkipRun",
        ),
        _build_gate_clip_node(),
        coord.wait_for_n_node(Signals.QUEST_GRABBED, n=PARTY_SIZE - 1, name="WaitQuestGrabbed", log_fn=log),
        coord.signal_node(Signals.GATE_DONE, name="SignalGateDone", log_fn=log),
        name="Level1",
    )


# ── Heart of Shadow skip ──────────────────────────────────────────────────────

def _build_hos_skip() -> BehaviorTree:
    """
    Jump over the Level 1 wall using Heart of Shadow.

    The skip targets the nearest alive hostile that is generally west of the
    player (agent.x < player.x - HoSSkip.WEST_THRESHOLD) and within cast
    range.  After casting, success is confirmed when player.y > HoSSkip.SUCCESS_Y.

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
            _px, py = Player.GetXY()
            if py > HoSSkip.SUCCESS_Y:
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
    """
    Mob-collision gate clip at the end of Level 1.

    Ports the gate_clip_test.py state machine as a single BT ActionNode.

    States:
        approach     walk to GateClip.GATE_POS
        wait_mobs    wait for hostile agents within MOB_WAIT_RADIUS
        lure_wait    wait LURE_WAIT_MS for mobs to stop moving + reach melee range
        walk_back    hold MoveBackward; watch for any tracked enemy to start moving
        clip         jiggle Forward+StrafeRight until player crosses SUCCESS_Y
        done         (internal) issue post-clip move and return SUCCESS next tick
    """
    _MOB_WAIT_RADIUS    = 600.0
    _MELEE_RANGE        = 160.0
    _LURE_WAIT_MS       = 3_000
    _WALK_BACK_TIMEOUT  = 8_000
    _CLIP_DURATION_MS   = 6_000
    _CLIP_PRESS_MS      = 200
    _CLIP_RELEASE_MS    = 50
    _CLIP_JIGGLE_RAD    = math.radians(5.0)
    _APPROACH_REISSUE   = 500
    _ARRIVAL_DIST       = 80.0
    _MAX_RETRIES        = 10
    _WALK_YAW           = math.pi   # 180°
    _CLIP_YAW           = 3.022     # 173.1°

    state: dict = {
        "phase":          "approach",
        "phase_ms":       0.0,
        "tracked":        set(),
        "backward_held":  False,
        "clip_phase":     "idle",
        "clip_phase_ms":  0.0,
        "clip_jiggle":    1,
        "clip_held":      False,
        "retry":          0,
        "last_move_ms":   0.0,
    }

    def _now() -> float:
        return _mono_ms()

    def _elapsed() -> float:
        return _now() - state["phase_ms"]

    def _transition(phase: str) -> None:
        log(f"GateClip: → {phase}")
        state["phase"]    = phase
        state["phase_ms"] = _now()

    def _hold_backward() -> None:
        if not state["backward_held"]:
            UIManager.Keydown(ControlAction.ControlAction_MoveBackward.value, 0)
            state["backward_held"] = True

    def _release_backward() -> None:
        if state["backward_held"]:
            UIManager.Keyup(ControlAction.ControlAction_MoveBackward.value, 0)
            state["backward_held"] = False

    def _release_clip_keys() -> None:
        if state["clip_held"]:
            UIManager.Keyup(ControlAction.ControlAction_MoveForward.value, 0)
            UIManager.Keyup(ControlAction.ControlAction_StrafeRight.value, 0)
            state["clip_held"]  = False
        state["clip_phase"] = "idle"

    def _release_all() -> None:
        _release_backward()
        _release_clip_keys()

    def _nearby_hostiles() -> list:
        px, py = Player.GetXY()
        result = []
        try:
            for aid in AgentArray.GetEnemyArray():
                if not Agent.IsAlive(aid):
                    continue
                dist = Utils.Distance((px, py), Agent.GetXY(aid))
                if dist < _MOB_WAIT_RADIUS:
                    result.append(aid)
        except Exception:
            pass
        return result

    def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        phase = state["phase"]

        # ── done ─────────────────────────────────────────────────────────────
        if phase == "done":
            _release_all()
            return BehaviorTree.NodeState.SUCCESS

        px, py = Player.GetXY()

        # ── approach ─────────────────────────────────────────────────────────
        if phase == "approach":
            dist = Utils.Distance((px, py), GateClip.GATE_POS)
            if dist < _ARRIVAL_DIST:
                _transition("wait_mobs")
                return BehaviorTree.NodeState.RUNNING
            now = _now()
            if now - state["last_move_ms"] > _APPROACH_REISSUE:
                Player.Move(*GateClip.GATE_POS)
                state["last_move_ms"] = now

        # ── wait_mobs ─────────────────────────────────────────────────────────
        elif phase == "wait_mobs":
            Camera.SetYaw(_WALK_YAW)
            if _nearby_hostiles():
                _transition("lure_wait")

        # ── lure_wait ─────────────────────────────────────────────────────────
        elif phase == "lure_wait":
            Camera.SetYaw(_WALK_YAW)
            if _elapsed() < _LURE_WAIT_MS:
                return BehaviorTree.NodeState.RUNNING
            hostiles = _nearby_hostiles()
            if any(Agent.IsMoving(aid) for aid in hostiles):
                return BehaviorTree.NodeState.RUNNING
            far = [aid for aid in hostiles
                   if Utils.Distance((px, py), Agent.GetXY(aid)) > _MELEE_RANGE]
            if far:
                return BehaviorTree.NodeState.RUNNING
            state["tracked"] = set(hostiles)
            _transition("walk_back")

        # ── walk_back ─────────────────────────────────────────────────────────
        elif phase == "walk_back":
            Camera.SetYaw(_WALK_YAW)
            _hold_backward()
            for aid in list(state["tracked"]):
                try:
                    if Agent.IsAlive(aid) and Agent.IsMoving(aid):
                        _release_backward()
                        _transition("clip")
                        return BehaviorTree.NodeState.RUNNING
                except Exception:
                    pass
            if _elapsed() >= _WALK_BACK_TIMEOUT:
                _release_backward()
                _transition("lure_wait")

        # ── clip ──────────────────────────────────────────────────────────────
        elif phase == "clip":
            now        = _now()
            clip_phase = state["clip_phase"]
            Camera.SetYaw(_CLIP_YAW + state["clip_jiggle"] * _CLIP_JIGGLE_RAD)

            if clip_phase == "idle":
                UIManager.Keydown(ControlAction.ControlAction_MoveForward.value, 0)
                UIManager.Keydown(ControlAction.ControlAction_StrafeRight.value, 0)
                state["clip_held"]      = True
                state["clip_phase"]     = "pressing"
                state["clip_phase_ms"]  = now

            elif clip_phase == "pressing":
                if now - state["clip_phase_ms"] >= _CLIP_PRESS_MS:
                    UIManager.Keyup(ControlAction.ControlAction_MoveForward.value, 0)
                    UIManager.Keyup(ControlAction.ControlAction_StrafeRight.value, 0)
                    state["clip_held"]     = False
                    state["clip_phase"]    = "releasing"
                    state["clip_phase_ms"] = now

            elif clip_phase == "releasing":
                if now - state["clip_phase_ms"] >= _CLIP_RELEASE_MS:
                    state["clip_jiggle"] = -state["clip_jiggle"]
                    UIManager.Keydown(ControlAction.ControlAction_MoveForward.value, 0)
                    UIManager.Keydown(ControlAction.ControlAction_StrafeRight.value, 0)
                    state["clip_held"]     = True
                    state["clip_phase"]    = "pressing"
                    state["clip_phase_ms"] = now

            if py > GateClip.SUCCESS_Y and px > GateClip.SUCCESS_MIN_X:
                log("GateClip: SUCCESS — through the gate")
                _release_all()
                Camera.SetYaw(_WALK_YAW)
                Player.Move(*GateClip.DEST_POS)
                state["phase"] = "done"
                return BehaviorTree.NodeState.RUNNING  # one more frame before SUCCESS

            if _elapsed() >= _CLIP_DURATION_MS:
                _release_all()
                state["retry"] += 1
                if state["retry"] > _MAX_RETRIES:
                    log("GateClip: FAILED — max retries reached")
                    return BehaviorTree.NodeState.FAILURE
                log(f"GateClip: clip timeout, retry {state['retry']}/{_MAX_RETRIES}")
                _transition("approach")

        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(BehaviorTree.ActionNode(_tick, name="GateClip"))


# ── Level 2 ───────────────────────────────────────────────────────────────────

def _build_level2(coord: SCCoordinator) -> BehaviorTree:
    return BTComposite.Sequence(
        SCMovement.RunPath(
            Waypoints.LEVEL2_ROUTE,
            pre_move_check_fn=_sf_active,
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
        SCMovement.Move(*Waypoints.PHANTOM_POS, pre_move_check_fn=_sf_active, avoidance=AvoidanceConfig(), log_fn=movement_log, name="Dasher:MoveToPhantom"),
        *hex_phantom_steps,
        coord.barrier_node(Barriers.PHANTOM_DEAD, required=PARTY_SIZE, name="PhantomDeadBarrier", log_fn=log),

        SCActions.PickupNearestItem(ModelID.BOSS_KEY, name="PickupBossKey"),
        SCMovement.Move(*Waypoints.CLIFFSIDE_TRIGGER, pre_move_check_fn=_sf_active, avoidance=AvoidanceConfig(), log_fn=movement_log, name="Dasher:MoveToCliffside"),
        coord.signal_node(Signals.BOSS_TRIGGERED, name="SignalBossTriggered", log_fn=log),

        SCMovement.Move(*Waypoints.BOSS_ROOM_POS, pre_move_check_fn=_sf_active, avoidance=AvoidanceConfig(), log_fn=movement_log, name="Dasher:MoveToBossRoom"),
        coord.barrier_node(Barriers.ALL_AT_BOSS, required=PARTY_SIZE, name="AllAtBossBarrier", log_fn=log),
        _barbs_loop_on_varny(coord, variant),
        coord.barrier_node(Barriers.BOSS_DEAD, required=PARTY_SIZE, name="BossDeadBarrier", log_fn=log),
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

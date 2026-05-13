"""
tank.py — MainTankRole and TrenchTankRole for DoA 3-3.

Main Tank (MT): A/W — Shadow Form + Dark Escape.
    Primary aggro controller.  Leads all pulls throughout the run.
    Solos the Monk Lord pull in Veil.
    Leads Team-A (tendrils 1-3) and stands on the Gloom rift.

Trench Tank (TT): A/W — Shadow Form + Recall (no Dark Escape).
    Secondary aggro, handles initial approach pulls.
    Leads Team-B (TK + Empathy + BF) to the derv-hill split in Veil.
    Kills tendrils 4-6 with Team-B.

Detection:
    MainTankRole:  ShadowForm AND DarkEscape on bar.
    TrenchTankRole: ShadowForm AND Recall on bar, WITHOUT DarkEscape.
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
)

from ._shared import _slot_for, make_sf_upkeep, make_sod_upkeep, make_stuck_watchdog
from ..constants import (
    Barriers, Signals, SkillID, Waypoints,
    PARTY_SIZE, VEIL_TEAM_A_SIZE, VEIL_TEAM_B_SIZE,
)
from ..agents import (
    find_snake_silzesh, find_snake_yendzarsh, find_snake_valkyss,
    find_the_fury, find_monk_lord, find_smothering_tendril,
    find_dreadspawn_maw, find_city_wall_enemy,
    find_greater_darkness, find_darkness, find_earth_tormentor,
    area_clear,
)


# ── shared pre-move guard ─────────────────────────────────────────────────────

def _sf_active() -> bool:
    return GLOBAL_CACHE.Effects.HasEffect(Player.GetAgentID(), SkillID.ShadowForm)


# ── MainTankRole ──────────────────────────────────────────────────────────────

@register_role
class MainTankRole(SCRole):
    """Main Tank — primary aggro controller throughout the run."""

    role_id = "mt"

    @classmethod
    def _matches(cls, skill_bar: list[int]) -> bool:
        return SkillID.ShadowForm in skill_bar and SkillID.DarkEscape in skill_bar

    def register_services(self):
        dc_slot = _slot_for(SkillID.DeathsCharge)
        dc_fn = (lambda: GLOBAL_CACHE.SkillBar.UseSkill(dc_slot)) if dc_slot else None
        return [
            ("ShadowForm", make_sf_upkeep()),
            ("Shroud",     make_sod_upkeep()),
            ("StuckWatch", make_stuck_watchdog(dc_fn)),
        ]

    def build_planner(self, coord: SCCoordinator) -> BehaviorTree:
        return BehaviorTree(
            BTComposite.Sequence(
                _mt_foundry(coord),
                _mt_veil(coord),
                _mt_city(coord),
                _mt_gloom(coord),
                name="MT:Planner",
            )
        )


# ── TrenchTankRole ────────────────────────────────────────────────────────────

@register_role
class TrenchTankRole(SCRole):
    """Trench Tank — secondary aggro, leads Team-B Veil split."""

    role_id = "tt"

    @classmethod
    def _matches(cls, skill_bar: list[int]) -> bool:
        return (
            SkillID.ShadowForm in skill_bar
            and SkillID.Recall in skill_bar
            and SkillID.DarkEscape not in skill_bar
        )

    def register_services(self):
        dc_slot = _slot_for(SkillID.DeathsCharge)
        dc_fn = (lambda: GLOBAL_CACHE.SkillBar.UseSkill(dc_slot)) if dc_slot else None
        return [
            ("ShadowForm", make_sf_upkeep()),
            ("Shroud",     make_sod_upkeep()),
            ("StuckWatch", make_stuck_watchdog(dc_fn)),
        ]

    def build_planner(self, coord: SCCoordinator) -> BehaviorTree:
        return BehaviorTree(
            BTComposite.Sequence(
                _tt_foundry(coord),
                _tt_veil(coord),
                _tt_city(coord),
                _tt_gloom(coord),
                name="TT:Planner",
            )
        )


# ── MT phase builders ─────────────────────────────────────────────────────────

def _mt_foundry(coord: SCCoordinator) -> BehaviorTree:
    """
    MT Foundry — 5 rooms.

    Rooms 1-2: pull to spike position → signal MT_PULL_SET → wait clear.
    Room 3:    move to far corner; barrier all 8; pull to spike position.
               CRITICAL: over-aggro in R3 wipes — barrier before any movement.
    Room 4:    run through pulling gate mobs in motion (no stop).
    Room 5:    talk to all 3 snakes in sequence; signal per snake group.
               Do NOT accept quest rewards — triggers undesired boss mobs.
    """
    return BTComposite.Sequence(
        SCMovement.RunPath(
            Waypoints.OUTPOST_TO_FOUNDRY,
            pre_move_check_fn=_sf_active,
            name="MT:ToFoundry",
        ),

        # Room 1
        _pull_and_wait(
            coord,
            pull_pos=Waypoints.FOUNDRY_R1_PULL_POS,
            spike_pos=Waypoints.FOUNDRY_R1_SPIKE_POS,
            clear_fn=lambda: area_clear(Waypoints.FOUNDRY_R1_SPIKE_POS),
            prefix="MT:R1",
        ),

        # Room 2
        _pull_and_wait(
            coord,
            pull_pos=Waypoints.FOUNDRY_R2_PULL_POS,
            spike_pos=Waypoints.FOUNDRY_R2_SPIKE_POS,
            clear_fn=lambda: area_clear(Waypoints.FOUNDRY_R2_SPIKE_POS),
            prefix="MT:R2",
        ),

        # Room 3 — barrier all 8 at corner before pulling
        SCMovement.Move(
            *Waypoints.FOUNDRY_R3_FAR_CORNER,
            pre_move_check_fn=_sf_active,
            name="MT:R3Corner",
        ),
        coord.barrier_node(
            Barriers.FOUNDRY_R3_POSITIONED,
            required=PARTY_SIZE,
            name="MT:R3Barrier",
        ),
        _pull_and_wait(
            coord,
            pull_pos=Waypoints.FOUNDRY_R3_FAR_CORNER,
            spike_pos=Waypoints.FOUNDRY_R3_SPIKE_POS,
            clear_fn=lambda: area_clear(Waypoints.FOUNDRY_R3_SPIKE_POS),
            prefix="MT:R3",
        ),

        # Room 4 — run through, no barrier needed
        SCMovement.RunPath(
            Waypoints.FOUNDRY_R4_RUN_THROUGH,
            pre_move_check_fn=_sf_active,
            name="MT:R4RunThrough",
        ),

        # Room 5 — talk to all 3 snakes; others hold at FOUNDRY_R5_HOLD_POS
        SCMovement.Move(*Waypoints.FOUNDRY_R5_SNAKE_1, pre_move_check_fn=_sf_active, name="MT:Snake1Pos"),
        SCActions.InteractNPC(find_snake_silzesh, name="MT:TalkSilzesh"),
        SCActions.WaitForCondition(lambda: area_clear(Waypoints.FOUNDRY_R5_SNAKE_1), timeout_ms=60_000, name="MT:WaitSnake1"),
        coord.signal_node(Signals.SNAKE_1_DONE, name="MT:SignalSnake1"),

        SCMovement.Move(*Waypoints.FOUNDRY_R5_SNAKE_2, pre_move_check_fn=_sf_active, name="MT:Snake2Pos"),
        SCActions.InteractNPC(find_snake_yendzarsh, name="MT:TalkYendzarsh"),
        SCActions.WaitForCondition(lambda: area_clear(Waypoints.FOUNDRY_R5_SNAKE_2), timeout_ms=60_000, name="MT:WaitSnake2"),
        coord.signal_node(Signals.SNAKE_2_DONE, name="MT:SignalSnake2"),

        SCMovement.Move(*Waypoints.FOUNDRY_R5_SNAKE_3, pre_move_check_fn=_sf_active, name="MT:Snake3Pos"),
        SCActions.InteractNPC(find_snake_valkyss, name="MT:TalkValkyss"),
        SCActions.WaitForCondition(lambda: area_clear(Waypoints.FOUNDRY_R5_SNAKE_3), timeout_ms=60_000, name="MT:WaitSnake3"),
        coord.signal_node(Signals.SNAKE_3_DONE, name="MT:SignalSnake3"),

        # Wait for The Fury's group to clear
        SCActions.WaitForCondition(
            lambda: find_the_fury() is None,
            timeout_ms=120_000,
            name="MT:WaitFury",
        ),
        coord.barrier_node(Barriers.FOUNDRY_DONE, required=PARTY_SIZE, name="MT:FoundryDone"),
        name="MT:Foundry",
    )


def _mt_veil(coord: SCCoordinator) -> BehaviorTree:
    """
    MT Veil — Team-A side.

    Environmental: Demonic Miasma — 50 dmg on block/dodge.  SF bypasses this.

    1. Pull first groups uphill (mesmers lack shadow steps in 3-3).
    2. Navigate 360 hills (5 hills × 6 waves each).
    3. Solo-pull Monk Lord while rest hold.
    4. Lead Team-A to tendrils 1-3; barrier VEIL_TENDRILS_A.
    5. Wait for Team-B (VEIL_TENDRILS_B).
    6. Kill Dreadspawn Maw — gate to Gloom opens on death.
    """
    return BTComposite.Sequence(
        SCMovement.RunPath(Waypoints.OUTPOST_TO_VEIL, pre_move_check_fn=_sf_active, name="MT:ToVeil"),

        # Uphill pull — mesmers without shadow steps wait for this signal
        SCMovement.Move(*Waypoints.VEIL_UPHILL_PULL, pre_move_check_fn=_sf_active, name="MT:UphillPull"),
        coord.signal_node(Signals.MT_PULLING, name="MT:SignalUphillPull"),
        SCActions.WaitForCondition(
            lambda: area_clear(Waypoints.VEIL_UPHILL_PULL),
            timeout_ms=60_000,
            name="MT:WaitUphillClear",
        ),
        coord.signal_node(Signals.MT_PULL_SET, name="MT:SignalUphillSet"),
        coord.barrier_node(Barriers.VEIL_UPHILL_SET, required=PARTY_SIZE, name="MT:UphillBarrier"),

        # 360 hills
        _mt_360_hills(),

        # Solo Monk Lord
        SCMovement.Move(*Waypoints.VEIL_MONK_LORD_PULL, pre_move_check_fn=_sf_active, name="MT:MonkLordPull"),
        coord.signal_node(Signals.MT_PULLING, name="MT:SignalMonkLord"),
        SCActions.WaitForCondition(
            lambda: find_monk_lord() is None,
            timeout_ms=60_000,
            name="MT:WaitMonkLord",
        ),
        coord.signal_node(Signals.MT_PULL_SET, name="MT:SignalMonkLordDone"),

        # Team-A tendrils 1-3
        SCMovement.RunPath(Waypoints.VEIL_TENDRIL_A_ROUTE, pre_move_check_fn=_sf_active, name="MT:TendrilARoute"),
        coord.signal_node(Signals.MT_PULL_SET, name="MT:SignalTendrilSpike"),
        _kill_tendrils(3, name="MT:TendrilsA"),
        coord.barrier_node(Barriers.VEIL_TENDRILS_A, required=VEIL_TEAM_A_SIZE, name="MT:TendrilABarrier"),

        # Wait for Team-B to finish their side
        coord.barrier_node(Barriers.VEIL_TENDRILS_B, required=VEIL_TEAM_B_SIZE, name="MT:WaitTendrilB"),

        # Kill Dreadspawn Maw
        SCMovement.Move(*Waypoints.VEIL_MAW_POS, pre_move_check_fn=_sf_active, name="MT:ToMaw"),
        coord.signal_node(Signals.MT_PULL_SET, name="MT:SignalMaw"),
        SCActions.WaitForCondition(lambda: find_dreadspawn_maw() is None, timeout_ms=120_000, name="MT:WaitMaw"),
        coord.barrier_node(Barriers.VEIL_MAW_DEAD, required=PARTY_SIZE, name="MT:MawBarrier"),
        coord.barrier_node(Barriers.VEIL_DONE, required=PARTY_SIZE, name="MT:VeilDone"),
        name="MT:Veil",
    )


def _mt_360_hills() -> BehaviorTree:
    """
    MT navigates all 5 Veil hills.

    Each hill: move to hill → wait for the wave to die → advance.
    6th wave per hill is a mixed group — same wait logic applies.
    """
    hills = [
        Waypoints.VEIL_HILL_1,
        Waypoints.VEIL_HILL_2,
        Waypoints.VEIL_HILL_3,
        Waypoints.VEIL_HILL_4,
        Waypoints.VEIL_HILL_5,
    ]
    steps = []
    for i, hill in enumerate(hills, start=1):
        steps += [
            SCMovement.Move(*hill, pre_move_check_fn=_sf_active, name=f"MT:Hill{i}"),
            SCActions.WaitForCondition(
                lambda h=hill: area_clear(h),
                timeout_ms=90_000,
                name=f"MT:WaitHill{i}",
            ),
        ]
    return BTComposite.Sequence(*steps, name="MT:360Hills")


def _mt_city(coord: SCCoordinator) -> BehaviorTree:
    """
    MT City — wall clearing.

    Environmental: Repressive Energy — 2 energy lost per skill use or attack.
    MT pulls to wall, signals, then holds.  Spikers drain hexes; minimal SF casts.
    Gate opens only after ALL exterior wall enemies are dead.
    """
    return BTComposite.Sequence(
        SCMovement.RunPath(Waypoints.OUTPOST_TO_CITY, pre_move_check_fn=_sf_active, name="MT:ToCity"),
        SCMovement.RunPath(Waypoints.CITY_WALL_APPROACH, pre_move_check_fn=_sf_active, name="MT:CityApproach"),
        SCMovement.Move(*Waypoints.CITY_WALL_SPIKE_POS, pre_move_check_fn=_sf_active, name="MT:CityWallPos"),
        coord.signal_node(Signals.MT_PULL_SET, name="MT:SignalCityWall"),
        SCActions.WaitForCondition(
            lambda: find_city_wall_enemy() is None,
            timeout_ms=120_000,
            name="MT:WaitWallClear",
        ),
        coord.barrier_node(Barriers.CITY_WALL_CLEAR, required=PARTY_SIZE, name="MT:CityWall"),
        coord.barrier_node(Barriers.CITY_DONE, required=PARTY_SIZE, name="MT:CityDone"),
        name="MT:City",
    )


def _mt_gloom(coord: SCCoordinator) -> BehaviorTree:
    """
    MT Gloom — Darkness phases then Earth Tormentors.

    Environmental: Shroud of Darkness — 50% miss chance (irrelevant for hex-spike).

    Phase 1: Approach → 2 Darknesses spawn → kill all 3 (incl. Greater Darkness).
    Phase 2: 5 more Darknesses spawn → kill all.
    Rift:    MT stands on rift to trigger Spirit + Water phases (~25s each).
    Phase 3: 25 Earth Tormentors spawn around Greater Darkness corpse.
             They teleport until aggro'd — MT pulls them into spike.

    Note: Darkness teleports around map when killed — wait for aggro before spiking.
    """
    return BTComposite.Sequence(
        SCMovement.RunPath(Waypoints.OUTPOST_TO_GLOOM, pre_move_check_fn=_sf_active, name="MT:ToGloom"),
        SCMovement.Move(*Waypoints.GLOOM_ENTRY, pre_move_check_fn=_sf_active, name="MT:GloomEntry"),
        coord.signal_node(Signals.MT_PULLING, name="MT:SignalGloomPhase1"),

        # Phase 1: 3 initial Darknesses (Greater Darkness + 2 spawned)
        SCActions.WaitForCondition(
            lambda: find_greater_darkness() is None and find_darkness() is None,
            timeout_ms=90_000,
            name="MT:WaitPhase1",
        ),
        coord.barrier_node(Barriers.GLOOM_INITIAL_DEAD, required=PARTY_SIZE, name="MT:GloomInitial"),

        # Phase 2: 5 wave Darknesses
        coord.signal_node(Signals.MT_PULL_SET, name="MT:SignalGloomWave"),
        SCActions.WaitForCondition(
            lambda: find_darkness() is None,
            timeout_ms=90_000,
            name="MT:WaitPhase2",
        ),
        coord.barrier_node(Barriers.GLOOM_WAVE_DEAD, required=PARTY_SIZE, name="MT:GloomWave"),

        # Stand on rift to trigger spirit and water phases
        SCMovement.Move(*Waypoints.GLOOM_RIFT_POS, pre_move_check_fn=_sf_active, name="MT:StandOnRift"),
        coord.signal_node(Signals.GLOOM_ON_RIFT, name="MT:SignalRift"),

        # Phase 3: Earth Tormentors
        SCMovement.Move(*Waypoints.GLOOM_EARTH_WAIT, pre_move_check_fn=_sf_active, name="MT:EarthWait"),
        coord.signal_node(Signals.MT_PULL_SET, name="MT:SignalEarths"),
        SCActions.WaitForCondition(
            lambda: find_earth_tormentor() is None,
            timeout_ms=180_000,
            name="MT:WaitEarths",
        ),
        coord.barrier_node(Barriers.GLOOM_EARTHS_DEAD, required=PARTY_SIZE, name="MT:EarthsDead"),
        coord.barrier_node(Barriers.GLOOM_DONE, required=PARTY_SIZE, name="MT:GloomDone"),
        coord.barrier_node(Barriers.DOA_COMPLETE, required=PARTY_SIZE, name="MT:DoAComplete"),
        name="MT:Gloom",
    )


# ── TT phase builders ─────────────────────────────────────────────────────────

def _tt_foundry(coord: SCCoordinator) -> BehaviorTree:
    """
    TT Foundry — initial approach pulls then hold in Room 5.

    TT initiates the first approach in each room while MT positions for the spike.
    In Room 5, TT holds at FOUNDRY_R5_HOLD_POS and waits for MT's snake signals.
    """
    return BTComposite.Sequence(
        SCMovement.RunPath(Waypoints.OUTPOST_TO_FOUNDRY, pre_move_check_fn=_sf_active, name="TT:ToFoundry"),

        # R1: TT first approach
        SCMovement.Move(*Waypoints.FOUNDRY_R1_PULL_POS, pre_move_check_fn=_sf_active, name="TT:R1Approach"),
        coord.signal_node(Signals.TT_PULLING, name="TT:SignalR1"),
        coord.wait_for_n_node(Signals.MT_PULL_SET, n=1, name="TT:WaitR1"),

        # R2-R3: follow MT signals
        coord.wait_for_n_node(Signals.MT_PULL_SET, n=1, name="TT:WaitR2"),
        coord.barrier_node(Barriers.FOUNDRY_R3_POSITIONED, required=PARTY_SIZE, name="TT:R3Barrier"),
        coord.wait_for_n_node(Signals.MT_PULL_SET, n=1, name="TT:WaitR3Spike"),

        # R4: run through
        SCMovement.RunPath(Waypoints.FOUNDRY_R4_RUN_THROUGH, pre_move_check_fn=_sf_active, name="TT:R4Run"),

        # R5: hold; wait for all 3 snakes then Fury clear
        SCMovement.Move(*Waypoints.FOUNDRY_R5_HOLD_POS, pre_move_check_fn=_sf_active, name="TT:R5Hold"),
        coord.wait_for_n_node(Signals.SNAKE_1_DONE, n=1, name="TT:WaitSnake1"),
        coord.wait_for_n_node(Signals.SNAKE_2_DONE, n=1, name="TT:WaitSnake2"),
        coord.wait_for_n_node(Signals.SNAKE_3_DONE, n=1, name="TT:WaitSnake3"),
        SCActions.WaitForCondition(lambda: find_the_fury() is None, timeout_ms=120_000, name="TT:WaitFury"),
        coord.barrier_node(Barriers.FOUNDRY_DONE, required=PARTY_SIZE, name="TT:FoundryDone"),
        name="TT:Foundry",
    )


def _tt_veil(coord: SCCoordinator) -> BehaviorTree:
    """
    TT Veil — leads Team-B (TK + Empathy + BF) to the derv-hill split.

    In 3-3: Backfire skips Jadoth; TK accompanies Backfire to derv hill.
    TT signals TT_PULLING so Team-B spikers know to follow this route.
    """
    return BTComposite.Sequence(
        SCMovement.RunPath(Waypoints.OUTPOST_TO_VEIL, pre_move_check_fn=_sf_active, name="TT:ToVeil"),

        # Wait for MT uphill prep before splitting
        coord.barrier_node(Barriers.VEIL_UPHILL_SET, required=PARTY_SIZE, name="TT:UphillWait"),

        # Signal Team-B to follow TT to derv hill
        coord.signal_node(Signals.TT_PULLING, name="TT:SignalSplit"),
        coord.signal_node(Signals.BF_SPLIT, name="TT:SignalBFSplit"),

        # Navigate to Team-B tendril route and kill tendrils 4-6
        SCMovement.RunPath(Waypoints.VEIL_TENDRIL_B_ROUTE, pre_move_check_fn=_sf_active, name="TT:TendrilBRoute"),
        coord.signal_node(Signals.TT_PULL_SET, name="TT:SignalTendrilSpike"),
        _kill_tendrils(3, name="TT:TendrilsB"),
        coord.barrier_node(Barriers.VEIL_TENDRILS_B, required=VEIL_TEAM_B_SIZE, name="TT:TendrilBDone"),

        # Cross-barrier: wait for Team-A before rejoining for Maw
        coord.barrier_node(Barriers.VEIL_TENDRILS_A, required=VEIL_TEAM_A_SIZE, name="TT:WaitTendrilA"),
        coord.barrier_node(Barriers.VEIL_MAW_DEAD, required=PARTY_SIZE, name="TT:MawBarrier"),
        coord.barrier_node(Barriers.VEIL_DONE, required=PARTY_SIZE, name="TT:VeilDone"),
        name="TT:Veil",
    )


def _tt_city(coord: SCCoordinator) -> BehaviorTree:
    """TT City — follows MT to wall, assists with kills."""
    return BTComposite.Sequence(
        SCMovement.RunPath(Waypoints.OUTPOST_TO_CITY, pre_move_check_fn=_sf_active, name="TT:ToCity"),
        SCMovement.RunPath(Waypoints.CITY_WALL_APPROACH, pre_move_check_fn=_sf_active, name="TT:CityApproach"),
        coord.wait_for_n_node(Signals.MT_PULL_SET, n=1, name="TT:WaitCityWall"),
        coord.barrier_node(Barriers.CITY_WALL_CLEAR, required=PARTY_SIZE, name="TT:CityWall"),
        coord.barrier_node(Barriers.CITY_DONE, required=PARTY_SIZE, name="TT:CityDone"),
        name="TT:City",
    )


def _tt_gloom(coord: SCCoordinator) -> BehaviorTree:
    """TT Gloom — follows MT through all phases."""
    return BTComposite.Sequence(
        SCMovement.RunPath(Waypoints.OUTPOST_TO_GLOOM, pre_move_check_fn=_sf_active, name="TT:ToGloom"),
        coord.wait_for_n_node(Signals.MT_PULLING, n=1, name="TT:WaitGloomPhase1"),
        coord.barrier_node(Barriers.GLOOM_INITIAL_DEAD, required=PARTY_SIZE, name="TT:GloomInitial"),
        coord.wait_for_n_node(Signals.MT_PULL_SET, n=1, name="TT:WaitGloomWave"),
        coord.barrier_node(Barriers.GLOOM_WAVE_DEAD, required=PARTY_SIZE, name="TT:GloomWave"),
        coord.wait_for_n_node(Signals.GLOOM_ON_RIFT, n=1, name="TT:WaitRift"),
        coord.wait_for_n_node(Signals.MT_PULL_SET, n=1, name="TT:WaitEarths"),
        coord.barrier_node(Barriers.GLOOM_EARTHS_DEAD, required=PARTY_SIZE, name="TT:EarthsDead"),
        coord.barrier_node(Barriers.GLOOM_DONE, required=PARTY_SIZE, name="TT:GloomDone"),
        coord.barrier_node(Barriers.DOA_COMPLETE, required=PARTY_SIZE, name="TT:DoAComplete"),
        name="TT:Gloom",
    )


# ── shared action helpers ─────────────────────────────────────────────────────

def _pull_and_wait(
    coord: SCCoordinator,
    pull_pos: tuple[float, float],
    spike_pos: tuple[float, float],
    clear_fn,
    prefix: str,
) -> BehaviorTree:
    """
    Common Foundry room pattern:
      move to pull position → signal MT_PULLING
      → move to spike position → signal MT_PULL_SET
      → wait for area clear.
    """
    return BTComposite.Sequence(
        SCMovement.Move(*pull_pos, pre_move_check_fn=_sf_active, name=f"{prefix}:Pull"),
        coord.signal_node(Signals.MT_PULLING, name=f"{prefix}:SignalPull"),
        SCMovement.Move(*spike_pos, pre_move_check_fn=_sf_active, name=f"{prefix}:SpikePos"),
        coord.signal_node(Signals.MT_PULL_SET, name=f"{prefix}:SignalSet"),
        SCActions.WaitForCondition(clear_fn, timeout_ms=60_000, name=f"{prefix}:WaitClear"),
        name=f"{prefix}:PullAndWait",
    )


def _kill_tendrils(n: int, name: str) -> BehaviorTree:
    """
    Kill n Smothering Tendrils in sequence.

    Each iteration: approach nearest tendril → wait for it to die.
    Spikers observe the pull signal and apply hexes independently.
    """
    state: dict = {"killed": 0}

    def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        if state["killed"] >= n:
            return BehaviorTree.NodeState.SUCCESS
        tendril = find_smothering_tendril()
        if tendril is None:
            state["killed"] += 1
        return (
            BehaviorTree.NodeState.SUCCESS
            if state["killed"] >= n
            else BehaviorTree.NodeState.RUNNING
        )

    return BehaviorTree(BehaviorTree.ActionNode(_tick, name=name))

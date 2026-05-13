"""
spiker.py — VoRRole, TKRole, EmpathyRole, BackfireRole for DoA 3-3.

All four are mesmer-type hex spikers.  Their planners share the same skeleton:
  1. Follow MT (Team-A) or TT (Team-B) to each spike position.
  2. Wait for the relevant pull signal (MT_PULL_SET or TT_PULL_SET).
  3. Apply their hex to the nearest enemy.
  4. Wait for clear condition.
  5. Repeat per area/room.

Veil 3-3 split assignment:
  Team-A (follow MT): VoRRole
  Team-B (follow TT, derv-hill split): TKRole, EmpathyRole, BackfireRole
      Backfire skips Jadoth (unique to 3-3).
      TK accompanies Backfire to derv hill.

Detection signatures:
  VoRRole:       VisionsOfRegret on bar
  TKRole:        Telekinesis on bar
  EmpathyRole:   Empathy on bar  (and NOT Backfire — avoids ambiguity)
  BackfireRole:  Backfire on bar (and NOT Empathy)
"""

from __future__ import annotations

from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE
from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Py4GWCoreLib.routines_src.behaviourtrees_src.composite import BTComposite

from Py4GWCoreLib.sc_framework import (
    SCRole, register_role, SCCoordinator,
    SCMovement, SCActions,
)

from ._shared import _slot_for, make_hex_reapply, make_stuck_watchdog
from ..constants import (
    Barriers, Signals, SkillID, Waypoints,
    PARTY_SIZE, VEIL_TEAM_A_SIZE, VEIL_TEAM_B_SIZE,
)
from ..agents import (
    find_smothering_tendril, find_dreadspawn_maw,
    find_city_wall_enemy, find_earth_tormentor,
    find_jadoth, area_clear,
)


# ── shared spike helper ───────────────────────────────────────────────────────

def _spike_on_signal(
    coord: SCCoordinator,
    wait_signal: int,
    skill_id: int,
    name: str,
) -> BehaviorTree:
    """
    Wait for a pull signal, then cast skill_id on the nearest enemy.
    Returns SUCCESS immediately after the cast attempt.
    """
    return BTComposite.Sequence(
        coord.wait_for_n_node(wait_signal, n=1, name=f"{name}:WaitSignal"),
        SCActions.CastSkill(
            slot_fn=lambda s=skill_id: _slot_for(s),
            target_fn=lambda: GLOBAL_CACHE.AgentArray.GetNearestEnemy(),
            name=f"{name}:CastHex",
        ),
        name=name,
    )


def _spiker_foundry(
    coord: SCCoordinator,
    skill_id: int,
    prefix: str,
    team_signal: int = Signals.MT_PULL_SET,
) -> BehaviorTree:
    """Generic Foundry planner for any spiker: wait per room → hex → wait clear."""
    rooms = [
        (Waypoints.FOUNDRY_R1_SPIKE_POS, "R1"),
        (Waypoints.FOUNDRY_R2_SPIKE_POS, "R2"),
        (Waypoints.FOUNDRY_R3_SPIKE_POS, "R3"),
    ]
    steps = [
        SCMovement.RunPath(Waypoints.OUTPOST_TO_FOUNDRY, name=f"{prefix}:ToFoundry"),
    ]
    for spike_pos, room in rooms:
        if room == "R3":
            steps.append(
                SCMovement.Move(*Waypoints.FOUNDRY_R3_FAR_CORNER, name=f"{prefix}:R3Corner")
            )
            steps.append(
                coord.barrier_node(Barriers.FOUNDRY_R3_POSITIONED, required=PARTY_SIZE, name=f"{prefix}:R3Barrier")
            )
        steps += [
            _spike_on_signal(coord, team_signal, skill_id, f"{prefix}:{room}Spike"),
            SCActions.WaitForCondition(
                lambda pos=spike_pos: area_clear(pos),
                timeout_ms=60_000,
                name=f"{prefix}:{room}Clear",
            ),
        ]
    # R4 run-through and R5 hold
    steps += [
        SCMovement.RunPath(Waypoints.FOUNDRY_R4_RUN_THROUGH, name=f"{prefix}:R4Run"),
        SCMovement.Move(*Waypoints.FOUNDRY_R5_HOLD_POS, name=f"{prefix}:R5Hold"),
        coord.wait_for_n_node(Signals.SNAKE_1_DONE, n=1, name=f"{prefix}:WaitSnake1"),
        coord.wait_for_n_node(Signals.SNAKE_2_DONE, n=1, name=f"{prefix}:WaitSnake2"),
        coord.wait_for_n_node(Signals.SNAKE_3_DONE, n=1, name=f"{prefix}:WaitSnake3"),
        SCActions.WaitForCondition(
            lambda: area_clear(Waypoints.FOUNDRY_R5_HOLD_POS),
            timeout_ms=120_000,
            name=f"{prefix}:WaitFury",
        ),
        coord.barrier_node(Barriers.FOUNDRY_DONE, required=PARTY_SIZE, name=f"{prefix}:FoundryDone"),
    ]
    return BTComposite.Sequence(*steps, name=f"{prefix}:Foundry")


def _spiker_city(coord: SCCoordinator, skill_id: int, prefix: str) -> BehaviorTree:
    """City: follow MT to wall, apply hex once, wait for clear."""
    return BTComposite.Sequence(
        SCMovement.RunPath(Waypoints.OUTPOST_TO_CITY, name=f"{prefix}:ToCity"),
        SCMovement.RunPath(Waypoints.CITY_WALL_APPROACH, name=f"{prefix}:CityApproach"),
        _spike_on_signal(coord, Signals.MT_PULL_SET, skill_id, f"{prefix}:CitySpike"),
        SCActions.WaitForCondition(
            lambda: find_city_wall_enemy() is None,
            timeout_ms=120_000,
            name=f"{prefix}:WaitWall",
        ),
        coord.barrier_node(Barriers.CITY_WALL_CLEAR, required=PARTY_SIZE, name=f"{prefix}:CityWall"),
        coord.barrier_node(Barriers.CITY_DONE, required=PARTY_SIZE, name=f"{prefix}:CityDone"),
        name=f"{prefix}:City",
    )


def _spiker_gloom(coord: SCCoordinator, skill_id: int, prefix: str) -> BehaviorTree:
    """Gloom: apply hex on each phase after MT signals."""
    return BTComposite.Sequence(
        SCMovement.RunPath(Waypoints.OUTPOST_TO_GLOOM, name=f"{prefix}:ToGloom"),
        _spike_on_signal(coord, Signals.MT_PULL_SET, skill_id, f"{prefix}:GloomPhase1Spike"),
        coord.barrier_node(Barriers.GLOOM_INITIAL_DEAD, required=PARTY_SIZE, name=f"{prefix}:GloomInitial"),
        _spike_on_signal(coord, Signals.MT_PULL_SET, skill_id, f"{prefix}:GloomWaveSpike"),
        coord.barrier_node(Barriers.GLOOM_WAVE_DEAD, required=PARTY_SIZE, name=f"{prefix}:GloomWave"),
        coord.wait_for_n_node(Signals.GLOOM_ON_RIFT, n=1, name=f"{prefix}:WaitRift"),
        _spike_on_signal(coord, Signals.MT_PULL_SET, skill_id, f"{prefix}:GloomEarthSpike"),
        SCActions.WaitForCondition(
            lambda: find_earth_tormentor() is None,
            timeout_ms=180_000,
            name=f"{prefix}:WaitEarths",
        ),
        coord.barrier_node(Barriers.GLOOM_EARTHS_DEAD, required=PARTY_SIZE, name=f"{prefix}:EarthsDead"),
        coord.barrier_node(Barriers.GLOOM_DONE, required=PARTY_SIZE, name=f"{prefix}:GloomDone"),
        coord.barrier_node(Barriers.DOA_COMPLETE, required=PARTY_SIZE, name=f"{prefix}:DoADone"),
        name=f"{prefix}:Gloom",
    )


# ── VoRRole ───────────────────────────────────────────────────────────────────

@register_role
class VoRRole(SCRole):
    """
    Visions of Regret caller — Team-A.

    Applies VoR to the primary target after MT signals, then assists Maw kill.
    Also acts as coordination hub — VoR caller is typically the human player;
    this bot implementation automates the hex-apply and movement only.
    """

    role_id = "vor"

    @classmethod
    def _matches(cls, skill_bar: list[int]) -> bool:
        return SkillID.VisionsOfRegret in skill_bar

    def register_services(self):
        return [
            ("VoRReapply", make_hex_reapply(SkillID.VisionsOfRegret, "VoRReapply")),
            ("StuckWatch", make_stuck_watchdog()),
        ]

    def build_planner(self, coord: SCCoordinator) -> BehaviorTree:
        return BehaviorTree(
            BTComposite.Sequence(
                _spiker_foundry(coord, SkillID.VisionsOfRegret, "VoR"),
                _vor_veil(coord),
                _spiker_city(coord, SkillID.VisionsOfRegret, "VoR"),
                _spiker_gloom(coord, SkillID.VisionsOfRegret, "VoR"),
                name="VoR:Planner",
            )
        )


def _vor_veil(coord: SCCoordinator) -> BehaviorTree:
    """VoR Veil — Team-A side: wait uphill → hills → tendrils 1-3 → Maw."""
    return BTComposite.Sequence(
        SCMovement.RunPath(Waypoints.OUTPOST_TO_VEIL, name="VoR:ToVeil"),
        coord.wait_for_n_node(Signals.MT_PULL_SET, n=1, name="VoR:WaitUphill"),
        coord.barrier_node(Barriers.VEIL_UPHILL_SET, required=PARTY_SIZE, name="VoR:UphillBarrier"),
        # 360 hills: follow MT, apply VoR on each wave
        _veil_hills_spiker(coord, SkillID.VisionsOfRegret, "VoR"),
        # Tendrils 1-3
        SCMovement.RunPath(Waypoints.VEIL_TENDRIL_A_ROUTE, name="VoR:TendrilARoute"),
        _spike_on_signal(coord, Signals.MT_PULL_SET, SkillID.VisionsOfRegret, "VoR:TendrilSpike"),
        SCActions.WaitForCondition(lambda: find_smothering_tendril() is None, timeout_ms=60_000, name="VoR:WaitTendril1"),
        _spike_on_signal(coord, Signals.MT_PULL_SET, SkillID.VisionsOfRegret, "VoR:TendrilSpike2"),
        SCActions.WaitForCondition(lambda: find_smothering_tendril() is None, timeout_ms=60_000, name="VoR:WaitTendril2"),
        _spike_on_signal(coord, Signals.MT_PULL_SET, SkillID.VisionsOfRegret, "VoR:TendrilSpike3"),
        SCActions.WaitForCondition(lambda: find_smothering_tendril() is None, timeout_ms=60_000, name="VoR:WaitTendril3"),
        coord.barrier_node(Barriers.VEIL_TENDRILS_A, required=VEIL_TEAM_A_SIZE, name="VoR:TendrilADone"),
        coord.barrier_node(Barriers.VEIL_TENDRILS_B, required=VEIL_TEAM_B_SIZE, name="VoR:WaitTendrilB"),
        # Maw
        SCMovement.Move(*Waypoints.VEIL_MAW_POS, name="VoR:ToMaw"),
        _spike_on_signal(coord, Signals.MT_PULL_SET, SkillID.VisionsOfRegret, "VoR:MawSpike"),
        SCActions.WaitForCondition(lambda: find_dreadspawn_maw() is None, timeout_ms=120_000, name="VoR:WaitMaw"),
        coord.barrier_node(Barriers.VEIL_MAW_DEAD, required=PARTY_SIZE, name="VoR:MawDead"),
        coord.barrier_node(Barriers.VEIL_DONE, required=PARTY_SIZE, name="VoR:VeilDone"),
        name="VoR:Veil",
    )


# ── TKRole ────────────────────────────────────────────────────────────────────

@register_role
class TKRole(SCRole):
    """
    Telekinesis spiker — Team-B (accompanies Backfire to derv hill in Veil).

    Waits for BF_SPLIT signal before following TT to VEIL_TENDRIL_B_ROUTE.
    """

    role_id = "tk"

    @classmethod
    def _matches(cls, skill_bar: list[int]) -> bool:
        return SkillID.Telekinesis in skill_bar

    def register_services(self):
        return [
            ("TKReapply", make_hex_reapply(SkillID.Telekinesis, "TKReapply")),
            ("StuckWatch", make_stuck_watchdog()),
        ]

    def build_planner(self, coord: SCCoordinator) -> BehaviorTree:
        return BehaviorTree(
            BTComposite.Sequence(
                _spiker_foundry(coord, SkillID.Telekinesis, "TK"),
                _team_b_veil(coord, SkillID.Telekinesis, "TK"),
                _spiker_city(coord, SkillID.Telekinesis, "TK"),
                _spiker_gloom(coord, SkillID.Telekinesis, "TK"),
                name="TK:Planner",
            )
        )


# ── EmpathyRole ───────────────────────────────────────────────────────────────

@register_role
class EmpathyRole(SCRole):
    """
    Empathy spiker — Team-B.

    Detection: Empathy on bar AND NOT Backfire (to avoid collision with BF role).
    """

    role_id = "empathy"

    @classmethod
    def _matches(cls, skill_bar: list[int]) -> bool:
        return (
            SkillID.Empathy in skill_bar
            and SkillID.Backfire not in skill_bar
        )

    def register_services(self):
        return [
            ("EmpReapply", make_hex_reapply(SkillID.Empathy, "EmpReapply")),
            ("StuckWatch", make_stuck_watchdog()),
        ]

    def build_planner(self, coord: SCCoordinator) -> BehaviorTree:
        return BehaviorTree(
            BTComposite.Sequence(
                _spiker_foundry(coord, SkillID.Empathy, "Emp"),
                _team_b_veil(coord, SkillID.Empathy, "Emp"),
                _spiker_city(coord, SkillID.Empathy, "Emp"),
                _spiker_gloom(coord, SkillID.Empathy, "Emp"),
                name="Emp:Planner",
            )
        )


# ── BackfireRole ──────────────────────────────────────────────────────────────

@register_role
class BackfireRole(SCRole):
    """
    Backfire spiker — Team-B, Jadoth skip.

    In 3-3: Backfire skips Jadoth entirely (unlike 6-0 where Jadoth is killed).
    Backfire routes around Jadoth's aggro range on VEIL_TENDRIL_B_ROUTE.
    Detection: Backfire on bar AND NOT Empathy.
    """

    role_id = "backfire"

    @classmethod
    def _matches(cls, skill_bar: list[int]) -> bool:
        return (
            SkillID.Backfire in skill_bar
            and SkillID.Empathy not in skill_bar
        )

    def register_services(self):
        return [
            ("BFReapply", make_hex_reapply(SkillID.Backfire, "BFReapply")),
            ("StuckWatch", make_stuck_watchdog()),
        ]

    def build_planner(self, coord: SCCoordinator) -> BehaviorTree:
        return BehaviorTree(
            BTComposite.Sequence(
                _spiker_foundry(coord, SkillID.Backfire, "BF"),
                _bf_veil(coord),
                _spiker_city(coord, SkillID.Backfire, "BF"),
                _spiker_gloom(coord, SkillID.Backfire, "BF"),
                name="BF:Planner",
            )
        )


def _bf_veil(coord: SCCoordinator) -> BehaviorTree:
    """
    Backfire Veil — Team-B with Jadoth skip.

    Waits for BF_SPLIT signal (posted by TT), then follows VEIL_TENDRIL_B_ROUTE
    which routes around Jadoth.  Applies Backfire to each tendril group.
    """
    return BTComposite.Sequence(
        SCMovement.RunPath(Waypoints.OUTPOST_TO_VEIL, name="BF:ToVeil"),
        coord.barrier_node(Barriers.VEIL_UPHILL_SET, required=PARTY_SIZE, name="BF:UphillWait"),

        # Wait for TT to signal the split before departing
        coord.wait_for_n_node(Signals.BF_SPLIT, n=1, name="BF:WaitSplit"),

        # Route bypasses Jadoth — VEIL_TENDRIL_B_ROUTE must be recorded to avoid his aggro
        SCMovement.RunPath(Waypoints.VEIL_TENDRIL_B_ROUTE, name="BF:TendrilBRoute"),
        _spike_on_signal(coord, Signals.TT_PULL_SET, SkillID.Backfire, "BF:TendrilSpike"),
        SCActions.WaitForCondition(lambda: find_smothering_tendril() is None, timeout_ms=60_000, name="BF:WaitTendril1"),
        _spike_on_signal(coord, Signals.TT_PULL_SET, SkillID.Backfire, "BF:TendrilSpike2"),
        SCActions.WaitForCondition(lambda: find_smothering_tendril() is None, timeout_ms=60_000, name="BF:WaitTendril2"),
        _spike_on_signal(coord, Signals.TT_PULL_SET, SkillID.Backfire, "BF:TendrilSpike3"),
        SCActions.WaitForCondition(lambda: find_smothering_tendril() is None, timeout_ms=60_000, name="BF:WaitTendril3"),
        coord.barrier_node(Barriers.VEIL_TENDRILS_B, required=VEIL_TEAM_B_SIZE, name="BF:TendrilBDone"),

        # Cross-barrier before rejoining for Maw
        coord.barrier_node(Barriers.VEIL_TENDRILS_A, required=VEIL_TEAM_A_SIZE, name="BF:WaitTendrilA"),
        SCMovement.Move(*Waypoints.VEIL_MAW_POS, name="BF:ToMaw"),
        _spike_on_signal(coord, Signals.MT_PULL_SET, SkillID.Backfire, "BF:MawSpike"),
        SCActions.WaitForCondition(lambda: find_dreadspawn_maw() is None, timeout_ms=120_000, name="BF:WaitMaw"),
        coord.barrier_node(Barriers.VEIL_MAW_DEAD, required=PARTY_SIZE, name="BF:MawDead"),
        coord.barrier_node(Barriers.VEIL_DONE, required=PARTY_SIZE, name="BF:VeilDone"),
        name="BF:Veil",
    )


# ── shared Veil helpers ───────────────────────────────────────────────────────

def _team_b_veil(
    coord: SCCoordinator,
    skill_id: int,
    prefix: str,
) -> BehaviorTree:
    """Team-B Veil planner for TK and Empathy (same structure, different skill)."""
    return BTComposite.Sequence(
        SCMovement.RunPath(Waypoints.OUTPOST_TO_VEIL, name=f"{prefix}:ToVeil"),
        coord.barrier_node(Barriers.VEIL_UPHILL_SET, required=PARTY_SIZE, name=f"{prefix}:UphillWait"),
        coord.wait_for_n_node(Signals.BF_SPLIT, n=1, name=f"{prefix}:WaitSplit"),
        SCMovement.RunPath(Waypoints.VEIL_TENDRIL_B_ROUTE, name=f"{prefix}:TendrilBRoute"),
        _spike_on_signal(coord, Signals.TT_PULL_SET, skill_id, f"{prefix}:TSpike1"),
        SCActions.WaitForCondition(lambda: find_smothering_tendril() is None, timeout_ms=60_000, name=f"{prefix}:WaitT1"),
        _spike_on_signal(coord, Signals.TT_PULL_SET, skill_id, f"{prefix}:TSpike2"),
        SCActions.WaitForCondition(lambda: find_smothering_tendril() is None, timeout_ms=60_000, name=f"{prefix}:WaitT2"),
        _spike_on_signal(coord, Signals.TT_PULL_SET, skill_id, f"{prefix}:TSpike3"),
        SCActions.WaitForCondition(lambda: find_smothering_tendril() is None, timeout_ms=60_000, name=f"{prefix}:WaitT3"),
        coord.barrier_node(Barriers.VEIL_TENDRILS_B, required=VEIL_TEAM_B_SIZE, name=f"{prefix}:TendrilBDone"),
        coord.barrier_node(Barriers.VEIL_TENDRILS_A, required=VEIL_TEAM_A_SIZE, name=f"{prefix}:WaitTendrilA"),
        SCMovement.Move(*Waypoints.VEIL_MAW_POS, name=f"{prefix}:ToMaw"),
        _spike_on_signal(coord, Signals.MT_PULL_SET, skill_id, f"{prefix}:MawSpike"),
        SCActions.WaitForCondition(lambda: find_dreadspawn_maw() is None, timeout_ms=120_000, name=f"{prefix}:WaitMaw"),
        coord.barrier_node(Barriers.VEIL_MAW_DEAD, required=PARTY_SIZE, name=f"{prefix}:MawDead"),
        coord.barrier_node(Barriers.VEIL_DONE, required=PARTY_SIZE, name=f"{prefix}:VeilDone"),
        name=f"{prefix}:Veil",
    )


def _veil_hills_spiker(
    coord: SCCoordinator,
    skill_id: int,
    prefix: str,
) -> BehaviorTree:
    """Apply hex at each of the 5 hills, waiting for MT signal each time."""
    hills = [
        (Waypoints.VEIL_HILL_1, "Hill1"),
        (Waypoints.VEIL_HILL_2, "Hill2"),
        (Waypoints.VEIL_HILL_3, "Hill3"),
        (Waypoints.VEIL_HILL_4, "Hill4"),
        (Waypoints.VEIL_HILL_5, "Hill5"),
    ]
    steps = []
    for hill_pos, tag in hills:
        steps += [
            SCMovement.Move(*hill_pos, name=f"{prefix}:{tag}"),
            _spike_on_signal(coord, Signals.MT_PULL_SET, skill_id, f"{prefix}:{tag}Spike"),
            SCActions.WaitForCondition(
                lambda h=hill_pos: area_clear(h),
                timeout_ms=90_000,
                name=f"{prefix}:{tag}Clear",
            ),
        ]
    return BTComposite.Sequence(*steps, name=f"{prefix}:360Hills")

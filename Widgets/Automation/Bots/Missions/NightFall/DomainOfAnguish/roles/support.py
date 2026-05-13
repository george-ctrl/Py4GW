"""
support.py — UAMonkRole and EmoRole for DoA 3-3.

UA Monk (Mo/R): maintains UnyieldingAura, rezzes dead party members via UA.
    Follows MT (Team-A) throughout the run.
    Detection: UnyieldingAura on bar.

Emo (E/Mo): maintains EtherRenewal for energy management.
    Critical in City (Repressive Energy drains 2e per skill).
    Follows MT (Team-A) throughout the run.
    Detection: EtherRenewal on bar.

Both support roles do not apply offensive hexes; they observe MT_PULL_SET
signals only to confirm position and advance with the group.
"""

from __future__ import annotations

from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Py4GWCoreLib.routines_src.behaviourtrees_src.composite import BTComposite

from Py4GWCoreLib.sc_framework import (
    SCRole, register_role, SCCoordinator,
    SCMovement, SCActions,
)

from ._shared import make_ua_upkeep, make_er_upkeep, make_stuck_watchdog
from ..constants import (
    Barriers, Signals, SkillID, Waypoints,
    PARTY_SIZE, VEIL_TEAM_A_SIZE, VEIL_TEAM_B_SIZE,
)
from ..agents import (
    find_smothering_tendril, find_dreadspawn_maw,
    find_city_wall_enemy, find_earth_tormentor,
    find_the_fury, area_clear,
)


# ── UAMonkRole ────────────────────────────────────────────────────────────────

@register_role
class UAMonkRole(SCRole):
    """
    UA Monk — maintains UnyieldingAura; rezzes party members automatically.

    Follows MT (Team-A).  No offensive contribution; presence in barriers
    ensures the group doesn't advance before the monk catches up.
    """

    role_id = "ua"

    @classmethod
    def _matches(cls, skill_bar: list[int]) -> bool:
        return SkillID.UnyieldingAura in skill_bar

    def register_services(self):
        return [
            ("UAUpkeep",   make_ua_upkeep()),
            ("StuckWatch", make_stuck_watchdog()),
        ]

    def build_planner(self, coord: SCCoordinator) -> BehaviorTree:
        return BehaviorTree(
            BTComposite.Sequence(
                _support_foundry(coord, "UA"),
                _support_veil_team_a(coord, "UA"),
                _support_city(coord, "UA"),
                _support_gloom(coord, "UA"),
                name="UA:Planner",
            )
        )


# ── EmoRole ───────────────────────────────────────────────────────────────────

@register_role
class EmoRole(SCRole):
    """
    Emocraft — maintains EtherRenewal for energy management.

    Particularly critical in City where Repressive Energy drains 2 energy
    per skill use.  Emo sustains the party's healing capacity throughout.
    Follows MT (Team-A).
    """

    role_id = "emo"

    @classmethod
    def _matches(cls, skill_bar: list[int]) -> bool:
        return SkillID.EtherRenewal in skill_bar

    def register_services(self):
        return [
            ("ERUpkeep",   make_er_upkeep()),
            ("StuckWatch", make_stuck_watchdog()),
        ]

    def build_planner(self, coord: SCCoordinator) -> BehaviorTree:
        return BehaviorTree(
            BTComposite.Sequence(
                _support_foundry(coord, "Emo"),
                _support_veil_team_a(coord, "Emo"),
                _support_city(coord, "Emo"),
                _support_gloom(coord, "Emo"),
                name="Emo:Planner",
            )
        )


# ── shared support planners ───────────────────────────────────────────────────

def _support_foundry(coord: SCCoordinator, prefix: str) -> BehaviorTree:
    """
    Support Foundry — follow MT to each room, wait for clear, advance.

    Stays at FOUNDRY_R5_HOLD_POS during the snake phase to avoid
    triggering additional Foundry boss spawns.
    """
    return BTComposite.Sequence(
        SCMovement.RunPath(Waypoints.OUTPOST_TO_FOUNDRY, name=f"{prefix}:ToFoundry"),

        # R1-R2: wait for MT clear signal before moving up
        coord.wait_for_n_node(Signals.MT_PULL_SET, n=1, name=f"{prefix}:WaitR1"),
        SCActions.WaitForCondition(
            lambda: area_clear(Waypoints.FOUNDRY_R1_SPIKE_POS),
            timeout_ms=60_000,
            name=f"{prefix}:R1Clear",
        ),
        coord.wait_for_n_node(Signals.MT_PULL_SET, n=1, name=f"{prefix}:WaitR2"),
        SCActions.WaitForCondition(
            lambda: area_clear(Waypoints.FOUNDRY_R2_SPIKE_POS),
            timeout_ms=60_000,
            name=f"{prefix}:R2Clear",
        ),

        # R3: barrier at corner, then follow after spike
        SCMovement.Move(*Waypoints.FOUNDRY_R3_FAR_CORNER, name=f"{prefix}:R3Corner"),
        coord.barrier_node(Barriers.FOUNDRY_R3_POSITIONED, required=PARTY_SIZE, name=f"{prefix}:R3Barrier"),
        coord.wait_for_n_node(Signals.MT_PULL_SET, n=1, name=f"{prefix}:WaitR3"),
        SCActions.WaitForCondition(
            lambda: area_clear(Waypoints.FOUNDRY_R3_SPIKE_POS),
            timeout_ms=60_000,
            name=f"{prefix}:R3Clear",
        ),

        # R4: run through
        SCMovement.RunPath(Waypoints.FOUNDRY_R4_RUN_THROUGH, name=f"{prefix}:R4Run"),

        # R5: hold and wait
        SCMovement.Move(*Waypoints.FOUNDRY_R5_HOLD_POS, name=f"{prefix}:R5Hold"),
        coord.wait_for_n_node(Signals.SNAKE_1_DONE, n=1, name=f"{prefix}:WaitSnake1"),
        coord.wait_for_n_node(Signals.SNAKE_2_DONE, n=1, name=f"{prefix}:WaitSnake2"),
        coord.wait_for_n_node(Signals.SNAKE_3_DONE, n=1, name=f"{prefix}:WaitSnake3"),
        SCActions.WaitForCondition(
            lambda: find_the_fury() is None,
            timeout_ms=120_000,
            name=f"{prefix}:WaitFury",
        ),
        coord.barrier_node(Barriers.FOUNDRY_DONE, required=PARTY_SIZE, name=f"{prefix}:FoundryDone"),
        name=f"{prefix}:Foundry",
    )


def _support_veil_team_a(coord: SCCoordinator, prefix: str) -> BehaviorTree:
    """
    Support Veil — Team-A (follow MT).

    Waits at uphill position, follows through 360 hills, then kills
    tendrils 1-3 with Team-A before rejoining for Maw.
    """
    hills = [
        (Waypoints.VEIL_HILL_1, "Hill1"),
        (Waypoints.VEIL_HILL_2, "Hill2"),
        (Waypoints.VEIL_HILL_3, "Hill3"),
        (Waypoints.VEIL_HILL_4, "Hill4"),
        (Waypoints.VEIL_HILL_5, "Hill5"),
    ]
    hill_steps = []
    for hill_pos, tag in hills:
        hill_steps += [
            SCMovement.Move(*hill_pos, name=f"{prefix}:{tag}"),
            SCActions.WaitForCondition(
                lambda h=hill_pos: area_clear(h),
                timeout_ms=90_000,
                name=f"{prefix}:{tag}Clear",
            ),
        ]

    return BTComposite.Sequence(
        SCMovement.RunPath(Waypoints.OUTPOST_TO_VEIL, name=f"{prefix}:ToVeil"),
        coord.wait_for_n_node(Signals.MT_PULL_SET, n=1, name=f"{prefix}:WaitUphill"),
        coord.barrier_node(Barriers.VEIL_UPHILL_SET, required=PARTY_SIZE, name=f"{prefix}:UphillBarrier"),
        *hill_steps,
        # Monk Lord: support holds while MT solos
        coord.wait_for_n_node(Signals.MT_PULL_SET, n=1, name=f"{prefix}:WaitMonkLord"),
        # Tendrils 1-3
        SCMovement.RunPath(Waypoints.VEIL_TENDRIL_A_ROUTE, name=f"{prefix}:TendrilARoute"),
        SCActions.WaitForCondition(
            lambda: find_smothering_tendril() is None,
            timeout_ms=60_000,
            name=f"{prefix}:WaitTendril1",
        ),
        SCActions.WaitForCondition(
            lambda: find_smothering_tendril() is None,
            timeout_ms=60_000,
            name=f"{prefix}:WaitTendril2",
        ),
        SCActions.WaitForCondition(
            lambda: find_smothering_tendril() is None,
            timeout_ms=60_000,
            name=f"{prefix}:WaitTendril3",
        ),
        coord.barrier_node(Barriers.VEIL_TENDRILS_A, required=VEIL_TEAM_A_SIZE, name=f"{prefix}:TendrilADone"),
        coord.barrier_node(Barriers.VEIL_TENDRILS_B, required=VEIL_TEAM_B_SIZE, name=f"{prefix}:WaitTendrilB"),
        # Maw
        SCMovement.Move(*Waypoints.VEIL_MAW_POS, name=f"{prefix}:ToMaw"),
        SCActions.WaitForCondition(
            lambda: find_dreadspawn_maw() is None,
            timeout_ms=120_000,
            name=f"{prefix}:WaitMaw",
        ),
        coord.barrier_node(Barriers.VEIL_MAW_DEAD, required=PARTY_SIZE, name=f"{prefix}:MawDead"),
        coord.barrier_node(Barriers.VEIL_DONE, required=PARTY_SIZE, name=f"{prefix}:VeilDone"),
        name=f"{prefix}:Veil",
    )


def _support_city(coord: SCCoordinator, prefix: str) -> BehaviorTree:
    """
    Support City — follow MT to wall, hold position.

    Emo's EtherRenewal is especially critical here.  Minimal skill use
    from non-Emo support; upkeep services already gate on energy.
    """
    return BTComposite.Sequence(
        SCMovement.RunPath(Waypoints.OUTPOST_TO_CITY, name=f"{prefix}:ToCity"),
        SCMovement.RunPath(Waypoints.CITY_WALL_APPROACH, name=f"{prefix}:CityApproach"),
        SCMovement.Move(*Waypoints.CITY_WALL_SPIKE_POS, name=f"{prefix}:CityWallPos"),
        coord.wait_for_n_node(Signals.MT_PULL_SET, n=1, name=f"{prefix}:WaitWall"),
        SCActions.WaitForCondition(
            lambda: find_city_wall_enemy() is None,
            timeout_ms=120_000,
            name=f"{prefix}:WaitWallClear",
        ),
        coord.barrier_node(Barriers.CITY_WALL_CLEAR, required=PARTY_SIZE, name=f"{prefix}:CityWall"),
        coord.barrier_node(Barriers.CITY_DONE, required=PARTY_SIZE, name=f"{prefix}:CityDone"),
        name=f"{prefix}:City",
    )


def _support_gloom(coord: SCCoordinator, prefix: str) -> BehaviorTree:
    """Support Gloom — follow MT through all three Darkness phases."""
    return BTComposite.Sequence(
        SCMovement.RunPath(Waypoints.OUTPOST_TO_GLOOM, name=f"{prefix}:ToGloom"),
        coord.wait_for_n_node(Signals.MT_PULLING, n=1, name=f"{prefix}:WaitPhase1"),
        coord.barrier_node(Barriers.GLOOM_INITIAL_DEAD, required=PARTY_SIZE, name=f"{prefix}:GloomInitial"),
        coord.wait_for_n_node(Signals.MT_PULL_SET, n=1, name=f"{prefix}:WaitWave"),
        coord.barrier_node(Barriers.GLOOM_WAVE_DEAD, required=PARTY_SIZE, name=f"{prefix}:GloomWave"),
        coord.wait_for_n_node(Signals.GLOOM_ON_RIFT, n=1, name=f"{prefix}:WaitRift"),
        coord.wait_for_n_node(Signals.MT_PULL_SET, n=1, name=f"{prefix}:WaitEarths"),
        SCActions.WaitForCondition(
            lambda: find_earth_tormentor() is None,
            timeout_ms=180_000,
            name=f"{prefix}:WaitEarthsClear",
        ),
        coord.barrier_node(Barriers.GLOOM_EARTHS_DEAD, required=PARTY_SIZE, name=f"{prefix}:EarthsDead"),
        coord.barrier_node(Barriers.GLOOM_DONE, required=PARTY_SIZE, name=f"{prefix}:GloomDone"),
        coord.barrier_node(Barriers.DOA_COMPLETE, required=PARTY_SIZE, name=f"{prefix}:DoADone"),
        name=f"{prefix}:Gloom",
    )

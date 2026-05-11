"""
constants.py — all TotF Auraway run-specific IDs and coordinates.

Everything GW-specific for this SC lives here so role scripts stay clean.
Values marked TODO must be recorded in-game:
    Coordinates  — stand at the spot and call Player.GetXY().
    Model IDs    — scan AgentArray and inspect agent.ModelID while the
                   NPC/item is in range.
    Skill IDs    — cross-reference with the GW skill ID list or
                   SkillBar.GetSkillIDBySlot() while the bar is equipped.
"""


# ── Barrier IDs ───────────────────────────────────────────────────────────────
# Each barrier must be a unique integer across the run.
# They share the SC_LOCK_KIND=50 namespace in shared memory.

class Barriers:
    """Rendezvous points where all (or a subset of) roles must synchronise."""
    PHANTOM_DEAD = 1001  # All 4 confirmed Enraged Phantom is dead.
    ALL_AT_BOSS  = 1002  # All 4 have reached the boss room.
    BOSS_DEAD    = 1003  # All 4 confirmed Varny the Zealot is dead.


# ── Signal IDs ────────────────────────────────────────────────────────────────
# Signals are one-shot broadcasts — posted by one role, observed by others.

class Signals:
    """One-shot notifications posted by a single role for others to observe."""
    QUEST_GRABBED  = 2001  # Each Aura posts this after taking Althea's quest.
    GATE_DONE      = 2002  # Dasher posts after completing the L1 gate glitch.
    BOSS_TRIGGERED = 2003  # Dasher posts after triggering Varny from cliffside.


# ── Skill IDs ─────────────────────────────────────────────────────────────────
# Used in role detection (_matches) and in upkeep service lambdas.
# All values are GW skill IDs (integers).

class SkillID:
    ShadowForm       = 826
    ShroudOfDistress = 1031
    DeadlyParadox    = 572
    Barbs            = 1649
    Dash             = 1062
    DwarvenStability = 3422
    GrentsAura       = 1467   # Grenth's Aura
    EbonEscape       = 2996
    UnseenFury       = 2302
    MarkOfPain       = 1638
    DeathsCharge     = 2278
    HeartOfShadow    = 1001   # TODO: verify ID


# ── Model IDs ─────────────────────────────────────────────────────────────────
# NPC and item model IDs.  Scan AgentArray / ItemArray in-game to fill these.

class ModelID:
    ALTHEA          = 0   # TODO: quest-giver NPC on Level 1
    ENRAGED_PHANTOM = 0   # TODO: boss on Level 3
    VARNY           = 0   # TODO: Varny the Zealot (final boss)
    BOSS_KEY        = 0   # TODO: item that unlocks the final boss door


# ── Quest IDs ─────────────────────────────────────────────────────────────────

class QuestID:
    ALTHEA_QUEST = 0  # TODO: quest given by Althea on Level 1


# ── Waypoints ─────────────────────────────────────────────────────────────────
# All coordinates are (x, y) in GW world space.
# Record in-game: stand at the location, open console, call Player.GetXY().

class Waypoints:
    # Getting There: Piken Square outpost → dungeon entrance wall-scrape point.
    PIKEN_TO_DUNGEON: list[tuple[float, float]] = [
        (0.0, 0.0),  # TODO: first waypoint exiting Piken
        (0.0, 0.0),  # TODO: mid-point running south
        (0.0, 0.0),  # TODO: wall-scrape entry point
    ]

    # Level 1 — Dasher: position from which to trigger the gate glitch.
    GATE_GLITCH_POS: tuple[float, float] = (0.0, 0.0)  # TODO

    # Level 1 — Aura: position of Althea NPC to take the quest.
    ALTHEA_POS: tuple[float, float] = (0.0, 0.0)  # TODO

    # Level 2 — shared run route; same for Dasher and all Auras.
    LEVEL2_ROUTE: list[tuple[float, float]] = [
        (0.0, 0.0),  # TODO
    ]

    # Level 3 — approach position for the Enraged Phantom group.
    PHANTOM_POS: tuple[float, float] = (0.0, 0.0)  # TODO

    # Level 3 — Dasher: cliffside position from which to trigger Varny.
    CLIFFSIDE_TRIGGER: tuple[float, float] = (0.0, 0.0)  # TODO

    # Level 3 — all roles: entry point of the boss room.
    BOSS_ROOM_POS: tuple[float, float] = (0.0, 0.0)  # TODO


# ── Gate clip geometry ────────────────────────────────────────────────────────
# The gate is a horizontal wall at constant Y ≈ 3495, spanning the X range
# below. Clip direction is positive Y (south → north, toward the corridor).
#
# Valid clip points recorded in-game (12 points, gate open):
#   Y mean: 3495.4   Y span: 2.2 units   X span: -8973 to -8390
#
# The player must stand within [GATE_X_MIN, GATE_X_MAX] on the south side
# (Y ≈ 3495), face south, hold MoveBackward (= north into gate), and rely on
# mob collision to push them through to Y > CORRIDOR_Y.

class GateClip:
    # Gate wall Y — horizontal line the player must cross.
    GATE_WALL_Y   = 3495.0

    # Valid clip zone X extents — do not position outside these.
    GATE_X_MIN    = -8973.0   # western edge
    GATE_X_MAX    = -8390.0   # eastern edge

    # Corridor Y on the north (destination) side of the gate.
    CORRIDOR_Y    = 3529.0

    # Player approach position: on the gate wall, south side.
    APPROACH_POS: tuple[float, float] = (-8796.57, 3495.70)

    # Face-south Y: issue Player.Move(player_x, FACE_SOUTH_Y) to orient the
    # character toward mob spawn (south, negative Y direction).
    # MoveBackward then pushes the player north (positive Y) into the gate.
    FACE_SOUTH_Y  = 3000.0

    # Clip success: player Y > this = clipped through to the corridor side.
    SUCCESS_Y     = 3515.0

    # A hostile agent within this Y-distance of GATE_WALL_Y is "at the gate".
    MOB_AT_GATE_DIST  = 150.0   # TODO: tune in-game

    # Aggro considered broken when no mob is near the gate for this many ticks.
    AGGRO_BREAK_TICKS = 5       # TODO: tune in-game


# ── Tuning constants ──────────────────────────────────────────────────────────

PARTY_SIZE          = 4        # Total accounts in a full TotF run.

SF_RECAST_BUFFER_MS = 3_000    # Recast Shadow Form when < 3 s remain.
SOD_RECAST_BUFFER_MS = 3_000   # Same for Shroud of Distress.
UPKEEP_COOLDOWN_MS  = 250      # Minimum ms between upkeep cast attempts.

EE_TRIGGER_DISTANCE = 500.0    # Cast Ebon Escape when > this far from Dasher.
EE_COOLDOWN_MS      = 1_500    # Don't spam EE faster than this.

BARBS_INTERVAL_MS   = 10_000   # Barbs recharge interval on the boss.
STUCK_THRESHOLD_MS  = 2_000    # StuckWatchdog: ms before declaring stuck.

"""
constants.py — all DoA 3-3 SC run-specific IDs and coordinates.

Everything GW-specific for this SC lives here so role scripts stay clean.
Values marked TODO must be recorded in-game:
    Coordinates  — stand at the spot and call Player.GetXY().
    Model IDs    — scan AgentArray and inspect agent.ModelID while the
                   NPC/item is in range.
    Skill IDs    — cross-reference with the GW skill ID list or
                   SkillBar.GetSkillIDBySlot() while the bar is equipped.

DoA 3-3 run order: Foundry → Veil → City → Gloom
Veil 3-3 split:   Team-A (MT + VoR + UAMonk + Emo)  → tendrils 1-3
                   Team-B (TT + TK + Empathy + BF)   → derv hill / tendrils 4-6
"""


# ── Barrier IDs ────────────────────────────────────────────────────────────────
# Each barrier is a unique integer in the SC_LOCK_KIND=50 shared-memory namespace.

class Barriers:
    """Rendezvous points where all (or a subset of) roles must synchronise."""
    # Foundry
    FOUNDRY_R3_POSITIONED = 1001  # All 8 at Room 3 far corner before spike
    FOUNDRY_DONE          = 1002  # All 8 confirm Foundry clear

    # Veil
    VEIL_UPHILL_SET       = 1003  # All 8 acknowledge MT uphill pull complete
    VEIL_TENDRILS_A       = 1004  # Team-A (4): tendrils 1-3 dead
    VEIL_TENDRILS_B       = 1005  # Team-B (4): tendrils 4-6 dead
    VEIL_MAW_DEAD         = 1006  # All 8 confirm Dreadspawn Maw dead
    VEIL_DONE             = 1007  # All 8 done with Veil

    # City
    CITY_WALL_CLEAR       = 1008  # All 8 confirm wall enemies dead
    CITY_DONE             = 1009  # All 8 done with City

    # Gloom
    GLOOM_INITIAL_DEAD    = 1010  # All 8 confirm 3 initial Darknesses dead
    GLOOM_WAVE_DEAD       = 1011  # All 8 confirm 5 wave Darknesses dead
    GLOOM_EARTHS_DEAD     = 1012  # All 8 confirm all Earth Tormentors dead
    GLOOM_DONE            = 1013  # All 8 done with Gloom

    # Final
    DOA_COMPLETE          = 1014  # All 8 confirm full DoA run complete


# ── Signal IDs ────────────────────────────────────────────────────────────────
# Signals are one-shot broadcasts — posted by one role, observed by others.

class Signals:
    """One-shot notifications posted by a single role for others to observe."""
    MT_PULLING      = 2001  # MT has begun a pull — spikers hold position
    MT_PULL_SET     = 2002  # MT mobs positioned — all spikers may spike
    TT_PULLING      = 2003  # TT has begun a pull (Team-B) — hold
    TT_PULL_SET     = 2004  # TT mobs positioned — Team-B spikers spike
    BF_SPLIT        = 2005  # Backfire + TK departing for Veil derv-hill split
    SNAKE_1_DONE    = 2006  # Foundry R5: Silzesh group cleared
    SNAKE_2_DONE    = 2007  # Foundry R5: Yendzarsh group cleared
    SNAKE_3_DONE    = 2008  # Foundry R5: Valkyss group cleared
    GLOOM_ON_RIFT   = 2009  # Standing on rift — triggers spirit + water phases


# ── Skill IDs ─────────────────────────────────────────────────────────────────

class SkillID:
    # ── Assassin / tank ───────────────────────────────────────────────────
    ShadowForm       = 826
    ShroudOfDistress = 1031
    DeadlyParadox    = 572
    DeathsCharge     = 2278
    DarkEscape       = 830    # TODO: verify — used to distinguish MT from TT
    Recall           = 1067   # TODO: verify — TT detection marker

    # ── Mesmer / spiker ───────────────────────────────────────────────────
    VisionsOfRegret  = 0      # TODO: VoR elite — verify GW skill ID
    Telekinesis      = 0      # TODO: TK damage spell — verify GW skill ID
    Empathy          = 133    # Mesmer hex (fairly confident)
    Backfire         = 148    # Mesmer hex (fairly confident)

    # ── Monk / UA ─────────────────────────────────────────────────────────
    UnyieldingAura   = 0      # TODO: UA monk elite — verify GW skill ID

    # ── Elementalist / Emo ────────────────────────────────────────────────
    EtherRenewal     = 0      # TODO: Emo core skill — verify GW skill ID


# ── Model IDs ─────────────────────────────────────────────────────────────────

class ModelID:
    # ── Foundry ───────────────────────────────────────────────────────────
    SNAKE_SILZESH    = 0   # TODO: Room 5 — first snake NPC (Silzesh)
    SNAKE_YENDZARSH  = 0   # TODO: Room 5 — General Yendzarsh
    SNAKE_VALKYSS    = 0   # TODO: Room 5 — Captain Valkyss
    THE_FURY         = 0   # TODO: The Fury (R5 final boss group)

    # ── Veil ──────────────────────────────────────────────────────────────
    SMOTHERING_TENDRIL = 0  # TODO: 6 total; each killed spawns a Stygian group
    DREADSPAWN_MAW     = 0  # TODO: Veil boss — gate opens when dead
    MONK_LORD          = 0  # TODO: MT solos this pull
    JADOTH             = 0  # TODO: skipped by Backfire in 3-3

    # ── City ──────────────────────────────────────────────────────────────
    CITY_WALL_ENEMY    = 0  # TODO: generic enemy type on City wall

    # ── Gloom ─────────────────────────────────────────────────────────────
    GREATER_DARKNESS   = 0  # TODO: The Greater Darkness (initial phase anchor)
    DARKNESS           = 0  # TODO: standard Darkness (5 wave darknesses)
    EARTH_TORMENTOR    = 0  # TODO: spawns when Darknesses die (25 total)


# ── Quest IDs ─────────────────────────────────────────────────────────────────

class QuestID:
    BROOD_WARS        = 0  # TODO: Veil quest (accept to spawn Smothering Tendrils)
    DEATHBRINGER_CO   = 0  # TODO: Gloom quest (triggers Greater Darkness)
    CITY_QUEST        = 0  # TODO: City liberation quest
    FOUNDRY_QUEST     = 0  # TODO: Foundry quest


# ── Waypoints ─────────────────────────────────────────────────────────────────
# All (x, y) in GW world space.  Record in-game: Player.GetXY() while standing.

class Waypoints:
    # ── Getting There: outpost → each area ───────────────────────────────
    OUTPOST_TO_FOUNDRY: list[tuple[float, float]] = [
        (0.0, 0.0),  # TODO
    ]
    OUTPOST_TO_VEIL: list[tuple[float, float]] = [
        (0.0, 0.0),  # TODO
    ]
    OUTPOST_TO_CITY: list[tuple[float, float]] = [
        (0.0, 0.0),  # TODO
    ]
    OUTPOST_TO_GLOOM: list[tuple[float, float]] = [
        (0.0, 0.0),  # TODO
    ]

    # ── Foundry ───────────────────────────────────────────────────────────
    # Room 1 — initial group pull then spike.
    FOUNDRY_R1_PULL_POS:   tuple[float, float] = (0.0, 0.0)  # TODO
    FOUNDRY_R1_SPIKE_POS:  tuple[float, float] = (0.0, 0.0)  # TODO

    # Room 2 — Torturewebs + Dreamies.
    FOUNDRY_R2_PULL_POS:   tuple[float, float] = (0.0, 0.0)  # TODO
    FOUNDRY_R2_SPIKE_POS:  tuple[float, float] = (0.0, 0.0)  # TODO

    # Room 3 — CRITICAL: far-corner spike to avoid over-aggro with 16 enemies.
    # All spikers must barrier here before the pull is initiated.
    FOUNDRY_R3_FAR_CORNER: tuple[float, float] = (0.0, 0.0)  # TODO: record carefully
    FOUNDRY_R3_SPIKE_POS:  tuple[float, float] = (0.0, 0.0)  # TODO

    # Room 4 — run through while pulling gate mobs (no full stop needed).
    FOUNDRY_R4_RUN_THROUGH: list[tuple[float, float]] = [
        (0.0, 0.0),  # TODO
    ]

    # Room 5 — three snakes; MT talks to each in sequence.
    FOUNDRY_R5_SNAKE_1:  tuple[float, float] = (0.0, 0.0)  # TODO: Silzesh
    FOUNDRY_R5_SNAKE_2:  tuple[float, float] = (0.0, 0.0)  # TODO: Yendzarsh
    FOUNDRY_R5_SNAKE_3:  tuple[float, float] = (0.0, 0.0)  # TODO: Valkyss
    FOUNDRY_R5_HOLD_POS: tuple[float, float] = (0.0, 0.0)  # TODO: non-MT hold here

    # ── Veil ──────────────────────────────────────────────────────────────
    # Uphill pull: MT positions mobs on slope for mesmers lacking shadow steps.
    VEIL_UPHILL_PULL:  tuple[float, float] = (0.0, 0.0)  # TODO

    # 360 phase: 5 hills, each with 6 waves.
    VEIL_HILL_1: tuple[float, float] = (0.0, 0.0)  # TODO
    VEIL_HILL_2: tuple[float, float] = (0.0, 0.0)  # TODO
    VEIL_HILL_3: tuple[float, float] = (0.0, 0.0)  # TODO
    VEIL_HILL_4: tuple[float, float] = (0.0, 0.0)  # TODO
    VEIL_HILL_5: tuple[float, float] = (0.0, 0.0)  # TODO

    # Monk Lord: MT solos this pull while rest hold.
    VEIL_MONK_LORD_PULL: tuple[float, float] = (0.0, 0.0)  # TODO

    # Tendril routes (team-split).
    # Team-A (MT + VoR + UAMonk + Emo) — main-side tendrils 1-3.
    VEIL_TENDRIL_A_ROUTE: list[tuple[float, float]] = [(0.0, 0.0)]  # TODO
    # Team-B (TT + TK + Empathy + BF) — derv-hill tendrils 4-6.
    VEIL_TENDRIL_B_ROUTE: list[tuple[float, float]] = [(0.0, 0.0)]  # TODO

    VEIL_MAW_POS: tuple[float, float] = (0.0, 0.0)  # TODO: Dreadspawn Maw approach

    # ── City ──────────────────────────────────────────────────────────────
    CITY_WALL_APPROACH: list[tuple[float, float]] = [(0.0, 0.0)]  # TODO
    CITY_WALL_SPIKE_POS: tuple[float, float] = (0.0, 0.0)         # TODO

    # ── Gloom ─────────────────────────────────────────────────────────────
    # Approach position for initial Darkness encounter.
    GLOOM_ENTRY:      tuple[float, float] = (0.0, 0.0)  # TODO

    # Standing here triggers the Spirit and Water phases (~25s after earths die).
    GLOOM_RIFT_POS:   tuple[float, float] = (0.0, 0.0)  # TODO

    # Hold position for the Earth Tormentor clear phase.
    GLOOM_EARTH_WAIT: tuple[float, float] = (0.0, 0.0)  # TODO


# ── Party composition ──────────────────────────────────────────────────────────

PARTY_SIZE       = 8   # Full DoA 8-man run
VEIL_TEAM_A_SIZE = 4   # MT + VoR + UAMonk + Emo (main-side split)
VEIL_TEAM_B_SIZE = 4   # TT + TK + Empathy + BF  (derv-hill split)


# ── Tuning constants ───────────────────────────────────────────────────────────

SF_RECAST_BUFFER_MS  = 3_000   # Recast Shadow Form when < 3s remain
SOD_RECAST_BUFFER_MS = 3_000   # Same for Shroud of Distress
UPKEEP_COOLDOWN_MS   = 250     # Minimum ms between upkeep cast attempts
STUCK_THRESHOLD_MS   = 2_000   # StuckWatchdog: ms before declaring stuck

HEX_REAPPLY_MS       = 8_000   # Reapply Empathy / Backfire / VoR if duration expires
CLEAR_WAIT_RADIUS    = 1_200.0 # Radius (GW units) used to check "area clear" conditions

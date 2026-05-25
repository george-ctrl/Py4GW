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


# ── Map IDs ───────────────────────────────────────────────────────────────────

class MapID:
    PIKEN_SQUARE     = 40
    VERDANT_CASCADES = 102   # explorable area containing the TotF entrance
    TUNNELS_LEVEL1   = 880   # Tunnels of the Forsaken — Level 1 entry map


# ── Barrier IDs ───────────────────────────────────────────────────────────────

class Barriers:
    """Rendezvous points where all (or a subset of) roles must synchronise."""
    PHANTOM_DEAD = 1001
    ALL_AT_BOSS  = 1002
    BOSS_DEAD    = 1003


# ── Signal IDs ────────────────────────────────────────────────────────────────

class Signals:
    """One-shot notifications posted by a single role for others to observe."""
    QUEST_GRABBED  = 2001  # Each Aura posts this after taking Althea's quest.
    GATE_DONE      = 2002  # Dasher posts after completing the Level 1 gate clip.
    BOSS_TRIGGERED = 2003  # Dasher posts after triggering Varny from cliffside.


# ── Skill IDs ─────────────────────────────────────────────────────────────────

class SkillID:
    ShadowForm       = 826
    ShroudOfDistress = 1031
    IAmUnstoppable   = 2356
    DwarvenStability = 2423
    Dash             = 1043
    Barbs            = 101
    HeartOfShadow    = 1032
    DeathsCharge     = 952
    EbonEscape       = 2420
    FinishHim        = 2353
    UnseenFury       = 1041
    GrentsAura       = 2013
    DeadlyParadox    = 572
    MarkOfPain       = 1638


# ── Model IDs ─────────────────────────────────────────────────────────────────

class ModelID:
    ALTHEA            = 0   # TODO: quest-giver NPC on Level 1
    ENRAGED_PHANTOM   = 0   # TODO: boss on Level 3
    VARNY             = 0   # TODO: Varny the Zealot (final boss)
    BOSS_KEY          = 0   # TODO: item that unlocks the final boss door
    DUNGEON_ENTRANCE  = 0   # TODO: portal/stone at the TotF entrance in Verdant Cascades


# ── Quest IDs ─────────────────────────────────────────────────────────────────

class QuestID:
    ALTHEA_QUEST = 0  # TODO: quest given by Althea on Level 1


# ── Waypoints ─────────────────────────────────────────────────────────────────

class Waypoints:
    # Getting There: Piken Square exit portal (outpost coordinates).
    # Use SCMovement.Move without avoidance for this step; the zone transition
    # into Verdant Cascades happens automatically when the player reaches the portal.
    PIKEN_OUTPOST_EXIT: tuple[float, float] = (20494.66, 6629.92)

    # Getting There: Verdant Cascades → TotF entrance (explorable coordinates).
    # Use SCMovement.RunPath with avoidance after the zone transition.
    VERDANT_TO_DUNGEON: list[tuple[float, float]] = [
        (20846.21, 5982.23),
        (21000.13, 5384.71),
        (20997.68, 4809.39),
        (20977.00, 4241.12),
        (20862.77, 3739.09),
        (20704.69, 3185.83),
        (20625.88, 2514.84),
        (20437.00, 2025.83),
        (19928.16, 1585.43),
        (19399.82, 1096.19),
        (18935.94,  640.02),
        (18927.88,   20.01),
        (18729.95, -493.96),
        (18302.44, -608.92),
        (17960.84, -593.15),
        (16929.71, -857.57),
    ]

    # Level 1 — Main Runner: run to the Heart of Shadow skip point.
    LEVEL1_MAIN_RUNNER: list[tuple[float, float]] = [
        (-20777.44, -4917.79),
        (-20080.97, -4835.40),
        (-19504.61, -4778.42),
        (-18689.78, -4726.71),
        (-17953.98, -4684.72),
        (-17408.66, -4494.17),
        (-16891.56, -4513.82),
        (-16311.62, -5061.58),
        (-15715.28, -5653.31),
        (-15122.37, -6145.13),
        (-14190.22, -6603.75),
        (-13422.85, -6959.89),
        (-12015.90, -6804.33),
        (-11578.52, -7339.75),
        (-10805.00, -7281.65),
        (-10094.46, -6964.38),
        ( -9312.00, -6532.62),
    ]

    # Level 1 — Main Runner: path after Heart of Shadow skip to the gate clip position.
    HOS_POST_SKIP: list[tuple[float, float]] = [
        (-8879.22, -6026.11),
        (-8905.95, -5023.53),
        (-8823.89, -4241.53),
        (-9213.21, -3629.31),
        (-9754.44, -3098.13),
        (-10413.22, -2449.55),
        (-10898.73, -2318.20),
        (-11038.38, -1770.10),
        (-11202.14, -1070.32),
        (-10808.56,  -632.56),
        (-10445.59,  -265.43),
        ( -9896.66,   320.59),
        ( -9281.33,   954.30),
        ( -8860.69,  1582.87),
        ( -8560.28,  2161.09),
        ( -8617.59,  2762.07),
    ]

    # Level 1 — Aura: Althea NPC position (take quest here).
    ALTHEA_POS: tuple[float, float] = (0.0, 0.0)  # TODO

    # Level 2 — shared run route; same for Runner and all Auras.
    LEVEL2_ROUTE: list[tuple[float, float]] = [
        (0.0, 0.0),  # TODO
    ]

    # Level 3 — approach position for the Enraged Phantom group.
    PHANTOM_POS: tuple[float, float] = (0.0, 0.0)  # TODO

    # Level 3 — Runner: cliffside position from which to trigger Varny.
    CLIFFSIDE_TRIGGER: tuple[float, float] = (0.0, 0.0)  # TODO

    # Level 3 — all roles: entry point of the boss room.
    BOSS_ROOM_POS: tuple[float, float] = (0.0, 0.0)  # TODO


# ── Heart of Shadow Skip ──────────────────────────────────────────────────────
# The skip happens at the end of LEVEL1_MAIN_RUNNER.  A hostile agent to the
# west of the player is targeted and HoS is cast to teleport over the wall.
# Success is detected by the player's Y coordinate crossing SUCCESS_Y.

class HoSSkip:
    WEST_THRESHOLD  = 50.0     # agent.x must be < player.x - this to count as "west"
    CAST_RANGE      = 900.0    # only consider targets within this distance
    SUCCESS_Y       = -6200.0  # player.y > this after cast = skip succeeded
    FIND_TIMEOUT_MS = 8_000    # max ms to wait for a valid target before retrying
    VERIFY_MS       = 2_000    # max ms after cast to detect success before retrying
    MAX_RETRIES     = 5


# ── Gate Clip ─────────────────────────────────────────────────────────────────
# The gate is a horizontal wall at Y ≈ 3495.  The runner lures mobs to the wall,
# walks backwards, and uses mob-collision to clip through to the corridor side.

class GateClip:
    GATE_WALL_Y   = 3495.0
    GATE_X_MIN    = -8973.0
    GATE_X_MAX    = -8390.0
    CORRIDOR_Y    = 3529.0
    SUCCESS_MIN_X = -8900.0

    GATE_POS: tuple[float, float] = (-8821.39, 3495.75)  # mob lure position
    CLIP_POS: tuple[float, float] = (-8639.05, 3495.43)  # walk-backwards endpoint
    DEST_POS: tuple[float, float] = (-8645.88, 4210.55)  # destination after clip

    FACE_SOUTH_Y  = 3000.0
    SUCCESS_Y     = 3515.0

    MOB_AT_GATE_DIST  = 150.0
    AGGRO_BREAK_TICKS = 5


# ── Consumables ───────────────────────────────────────────────────────────────

class ConsumableSpec:
    """One consumable: display label, unique key, item model ID, GW effect name."""
    __slots__ = ("label", "key", "model_id", "effect_name")

    def __init__(self, label: str, key: str, model_id: int, effect_name: str) -> None:
        self.label       = label
        self.key         = key
        self.model_id    = model_id
        self.effect_name = effect_name


# All consumables supported by the bot.  The UI shows one checkbox per entry.
# Conset (party-wide) items are listed first, then pcons (personal buffs).
ALL_CONSUMABLES: list = [
    # ── Conset ────────────────────────────────────────────────────────────────
    ConsumableSpec("Essence of Celerity", "essence",     24859, "Essence_of_Celerity_item_effect"),
    ConsumableSpec("Grail of Might",      "grail",       24861, "Grail_of_Might_item_effect"),
    ConsumableSpec("Armor of Salvation",  "armor",       24860, "Armor_of_Salvation_item_effect"),
    # ── Pcons ─────────────────────────────────────────────────────────────────
    ConsumableSpec("Birthday Cupcake",    "cupcake",     22269, "Birthday_Cupcake_skill"),
    ConsumableSpec("Golden Egg",          "golden_egg",  22752, "Golden_Egg_skill"),
    ConsumableSpec("Candy Corn",          "candy_corn",  28432, "Candy_Corn_skill"),
    ConsumableSpec("Candy Apple",         "candy_apple", 28431, "Candy_Apple_skill"),
    ConsumableSpec("Pumpkin Pie",         "pumpkin_pie", 28436, "Pie_Induced_Ecstasy"),
    ConsumableSpec("Drake Kabob",         "drake_kabob", 17060, "Drake_Skin"),
    ConsumableSpec("Skalefin Soup",       "skalefin",    17061, "Skale_Vigor"),
    ConsumableSpec("Pahnai Salad",        "pahnai",      17062, "Pahnai_Salad_item_effect"),
    ConsumableSpec("War Supplies",        "war_supplies",35121, "Well_Supplied"),
]


# ── Tuning constants ──────────────────────────────────────────────────────────

PARTY_SIZE           = 4

SF_RECAST_BUFFER_MS  = 3_000
SOD_RECAST_BUFFER_MS = 3_000
UPKEEP_COOLDOWN_MS   = 250

EE_TRIGGER_DISTANCE  = 500.0
EE_COOLDOWN_MS       = 1_500

BARBS_INTERVAL_MS    = 10_000
STUCK_THRESHOLD_MS   = 2_000

CONS_UPKEEP_INTERVAL_MS = 5_000  # how often the consumable service checks each item

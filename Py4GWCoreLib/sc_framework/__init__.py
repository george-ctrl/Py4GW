"""
sc_framework — generic multi-role speedclear coordination framework.

This package knows nothing about Guild Wars skills, builds, dungeon layouts,
or any game-specific content.  Every GW-specific detail belongs in the SC
scripts that import and instantiate this framework.

Public surface
──────────────
Coordination:
    SCCoordinator       Per-account barrier / signal / wait factory.

Movement:
    SCMovement          Move + RunPath with safety guard and recovery.
    RecoveryStrategy    Enum of stuck-recovery behaviours.

Services (parallel):
    UpkeepService       Maintain any recurring action (buff, consumable …).
    StuckWatchdog       Monitor position; trigger recovery on stall.
    PartyFollowService  Follow a party member with an injected skill.
    PeriodicService     Call an action every N ms.

Actions (atomic BT nodes):
    SCActions           CastSkill, WaitForCondition, InteractNPC,
                        PickupNearestItem, TakeQuest.

Role system:
    SCRole              Abstract base class for role scripts.
    register_role       Class decorator for auto-detection.

Runtime:
    SCRuntime           Wires role + services → BottingTree and starts it.
"""

from .coordinator import SCCoordinator
from .movement    import SCMovement, RecoveryStrategy
from .services    import UpkeepService, StuckWatchdog, PartyFollowService, PeriodicService
from .actions     import SCActions
from .role        import SCRole, register_role
from .runtime     import SCRuntime

__all__ = [
    "SCCoordinator",
    "SCMovement",
    "RecoveryStrategy",
    "UpkeepService",
    "StuckWatchdog",
    "PartyFollowService",
    "PeriodicService",
    "SCActions",
    "SCRole",
    "register_role",
    "SCRuntime",
]

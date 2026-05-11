"""
role.py — abstract base class for speedclear role scripts.

Each speedclear defines one SCRole subclass per role.  Decorate the class
with @register_role so SCRole.detect() can find it automatically by reading
the current skill bar from shared memory.

Subclass contract:
    role_id          Class-level string identifying the role ("dasher", "aura").
    _matches(bar)    Return True if this role's skill signature is in bar.
    build_planner()  Return the full BT planner sequence for this role.
    register_services()  Return service trees to run in parallel (optional).

Usage (in a SC role file):
    from Py4GWCoreLib.sc_framework.role import SCRole, register_role

    @register_role
    class DasherRole(SCRole):
        role_id = "dasher"

        @classmethod
        def _matches(cls, skill_bar: list[int]) -> bool:
            return SkillID.Barbs in skill_bar and SkillID.Dash in skill_bar

        def build_planner(self, coord: SCCoordinator) -> BehaviorTree:
            ...

        def register_services(self) -> list[tuple[str, BehaviorTree]]:
            return [("ShadowForm", make_sf_upkeep()), ...]
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree

if TYPE_CHECKING:
    from .coordinator import SCCoordinator

# All classes decorated with @register_role are stored here.
# SCRole.detect() iterates this list in registration order.
_ROLE_REGISTRY: list[type["SCRole"]] = []


def register_role(cls: type["SCRole"]) -> type["SCRole"]:
    """
    Class decorator — register a role class for automatic detection.

    Apply this to every concrete SCRole subclass.  Detection order follows
    registration order, so more specific roles should be registered first
    if their skill signatures could be a subset of a broader role.
    """
    _ROLE_REGISTRY.append(cls)
    return cls


class SCRole(ABC):
    """
    Abstract base for speedclear role scripts.

    Subclass once per role per speedclear.  The framework calls detect() at
    runtime to instantiate the correct role from the equipped skill bar.
    """

    # Identify this role in log output and the BottingTree UI.
    role_id: str = "unknown"

    # ── subclass contract ─────────────────────────────────────────────────

    @classmethod
    @abstractmethod
    def _matches(cls, skill_bar: list[int]) -> bool:
        """
        Return True if this role's signature is present in skill_bar.

        skill_bar is a list of skill IDs currently equipped in slots 1–8.
        Zero values (empty slots) are already filtered out before this call.
        """
        ...

    @abstractmethod
    def build_planner(self, coord: "SCCoordinator") -> BehaviorTree:
        """
        Return the full planner sequence tree for this role.

        Called once by SCRuntime.start() and passed to BottingTree as the
        main planner.  The coord parameter provides the barrier/signal nodes
        scoped to this account's email.
        """
        ...

    def register_services(self) -> list[tuple[str, BehaviorTree]]:
        """
        Return a list of (name, service_tree) pairs to run in parallel.

        Override to add upkeep, stuck watchdog, party follow, etc.
        The default implementation returns an empty list (no services).
        Each tree is passed to BottingTree.AddServiceTree by SCRuntime.
        """
        return []

    # ── auto-detection ────────────────────────────────────────────────────

    @classmethod
    def detect(cls) -> "SCRole":
        """
        Read the current skill bar and return the matching SCRole instance.

        Reads slots 1–8 from GLOBAL_CACHE.SkillBar, filters zeros, and
        passes the list to each registered subclass's _matches() classmethod.
        Returns an instance of the first matching class.

        Raises:
            ValueError: if no registered role matches the equipped skills.
                        The error message includes the raw skill bar so it is
                        easy to diagnose a wrong build in the GW console.
        """
        from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE

        skill_bar: list[int] = [
            GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(slot)
            for slot in range(1, 9)
        ]
        # Remove empty or invalid slots.
        skill_bar = [sid for sid in skill_bar if sid]

        for role_cls in _ROLE_REGISTRY:
            if role_cls._matches(skill_bar):
                return role_cls()

        raise ValueError(
            f"No registered SCRole matched skill bar: {skill_bar}.  "
            "Ensure the correct build is equipped and that the role class "
            "is decorated with @register_role."
        )

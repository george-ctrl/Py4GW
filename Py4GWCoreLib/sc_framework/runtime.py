"""
runtime.py — wires a detected role into a BottingTree and starts execution.

SCRuntime.start() is the single entry point called by each SC script's
__init__.py.  It:
    1. Creates a BottingTree configured for SC use (no combat pause, no HeroAI).
    2. Attaches all service trees returned by role.register_services().
    3. Sets the role's planner tree as the main BottingTree planner.
    4. Starts the tree.

Usage (in a SC entry __init__.py):
    from Py4GWCoreLib.sc_framework import SCCoordinator, SCRole, SCRuntime

    def configure(bot_name: str):
        email = _get_own_email()
        coord = SCCoordinator(email)
        role  = SCRole.detect()         # reads skill bar automatically
        return SCRuntime.start(bot_name, coord, role)
"""

from __future__ import annotations

from Py4GWCoreLib.BottingTree import BottingTree

from .coordinator import SCCoordinator
from .role import SCRole


class SCRuntime:
    """Entry point: creates a BottingTree wired up for a SC role."""

    @staticmethod
    def start(
        bot_name: str,
        coord: SCCoordinator,
        role: SCRole,
        *,
        pause_on_combat: bool = False,
        isolation_enabled: bool = False,
    ) -> BottingTree:
        """
        Create a BottingTree, attach all services and the planner, and start.

        Service trees are attached before the planner so they begin ticking
        (upkeep, stuck watchdog, follow) before the first movement command.

        Args:
            bot_name:          Display name shown in the Py4GW bot UI.
            coord:             SCCoordinator scoped to this account's email.
            role:              Detected SCRole instance (from SCRole.detect()).
            pause_on_combat:   Set False for SC runs — roles manage their own
                               safety (SF, SoD) via upkeep services.
            isolation_enabled: Set False to keep all accounts in the same
                               HeroAI group while running the SC.

        Returns:
            The running BottingTree.  BottingTree.tick() is called by the
            Py4GW frame loop automatically; no further setup is needed.
        """
        tree = BottingTree.Create(
            bot_name=bot_name,
            pause_on_combat=pause_on_combat,
            isolation_enabled=isolation_enabled,
        )

        # SC characters run Shadow Form and must never engage in combat.
        # Disabling HeroAI prevents the character from attacking, using skills
        # for combat, or looting — all of which interfere with the run-through.
        tree.DisableHeadlessHeroAI(reset_runtime=False)
        tree.DisableLooting()

        # Register parallel services before starting the planner.
        for service_name, service_tree in role.register_services():
            tree.AddServiceTree(service_name, service_tree)

        # Set the role's planner as the main BottingTree routine.
        planner = role.build_planner(coord)
        tree.SetPlannerTree(planner)

        tree.Start()
        return tree

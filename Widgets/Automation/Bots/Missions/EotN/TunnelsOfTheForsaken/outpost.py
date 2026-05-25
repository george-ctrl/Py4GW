"""
outpost.py — TotF Auraway outpost-phase handler.

Always the first step in the bot planner.  Handles everything that must
happen while the player is still in the outpost before the explorable run.

    OutpostHandler.build_node()     — BT node inserted first in build_planner
    OutpostHandler.draw_section()   — ImGui section for the main panel

Current behaviour:
    • Skip immediately if already in the explorable (mid-run restart).
    • Optional "Hold at outpost" checkbox to delay departure.
    • Walk to the exit portal via periodic Player.Move re-issue (no
      autopathing, no SC overhead — no enemies, no avoidance, no SF guard
      in an outpost).
    • Zone-line crossing into The Breach happens automatically when the
      player reaches the portal — no NPC interaction required.

Extend build_node() when new pre-departure steps are needed:
    – waiting for all party members to be in the outpost
    – accepting / turning in quests from NPCs
    – countdown timer before auto-depart
    – role-specific setup checks
"""

from __future__ import annotations

import time

import PyImGui

from Py4GWCoreLib.py4gwcorelib_src.BehaviorTree import BehaviorTree


class OutpostHandler:
    """
    One instance per role, created in register_services() when the bot starts.

    Args:
        exit_pos:  World coordinates of the outpost exit portal.
        label:     Section heading shown in the ImGui panel.
    """

    def __init__(
        self,
        exit_pos: tuple[float, float],
        label:    str = "Outpost",
    ) -> None:
        self._exit_pos = exit_pos
        self._label    = label
        self._hold     = False   # GUI: hold at outpost before departing

    # ── BT node ───────────────────────────────────────────────────────────

    def build_node(self, name: str = "Outpost") -> BehaviorTree:
        """
        Return a BT node that is placed first in the planner sequence.

        Tick behaviour:
            • Not in outpost  → SUCCESS immediately.
            • Hold checked    → RUNNING (waits until unchecked).
            • Otherwise       → periodically re-issues Player.Move to exit_pos
                                and returns SUCCESS once within tolerance.
                                Succeeds as soon as the map changes too.

        Add new pre-departure steps inside the movement block below.
        """
        from Py4GWCoreLib.Map import Map
        from Py4GWCoreLib.Player import Player
        from Py4GWCoreLib.py4gwcorelib_src.Utils import Utils

        _TOLERANCE  = 80.0
        _REISSUE_MS = 500.0
        state: dict = {"last_move_ms": 0.0}

        def _tick(_node: BehaviorTree.Node) -> BehaviorTree.NodeState:
            if not Map.IsOutpost():
                return BehaviorTree.NodeState.SUCCESS
            if self._hold:
                return BehaviorTree.NodeState.RUNNING
            # ── outpost steps go here ─────────────────────────────────────
            px, py = Player.GetXY()
            if Utils.Distance((px, py), self._exit_pos) <= _TOLERANCE:
                return BehaviorTree.NodeState.SUCCESS
            now = time.monotonic() * 1_000.0
            if now - state["last_move_ms"] >= _REISSUE_MS:
                Player.Move(float(self._exit_pos[0]), float(self._exit_pos[1]))
                state["last_move_ms"] = now
            return BehaviorTree.NodeState.RUNNING

        return BehaviorTree(BehaviorTree.ActionNode(_tick, name=name))

    # ── GUI ───────────────────────────────────────────────────────────────

    def draw_section(self) -> None:
        """
        Render the outpost section inside an already-open ImGui window.
        Call from the main draw() between separator() calls.
        """
        from Py4GWCoreLib.Map import Map

        PyImGui.separator()
        PyImGui.text(self._label)

        if Map.IsOutpost():
            PyImGui.text_colored("  In outpost", (1.0, 0.8, 0.2, 1.0))
            self._hold = PyImGui.checkbox("  Hold at outpost##op", self._hold)
        else:
            PyImGui.text_colored("  In explorable", (0.4, 1.0, 0.4, 1.0))
            if self._hold:
                self._hold = False  # auto-clear once the zone line is crossed

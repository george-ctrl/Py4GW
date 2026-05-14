import PyImGui

from Py4GWCoreLib.Player import Player
from Py4GWCoreLib.Agent import Agent
from Py4GWCoreLib.AgentArray import AgentArray
from Py4GWCoreLib.GlobalCache import GLOBAL_CACHE
from Py4GWCoreLib.py4gwcorelib_src.Timer import ThrottledTimer

MODULE_NAME = "StaleTargetCrashReproducer"

_fire_on      = False
_locked_id    = 0
_skill_slot   = 1
_aftercast_ms = 250   # match HeroAI HandleCombat (250) or BuildMgr CastSkillID (1000)

_slot_buf      = [1]
_id_buf        = [0]
_aftercast_buf = [250]

_timer = ThrottledTimer(500)  # fire every 500ms so the delay window is visible
_nearby: list[tuple[int, str]] = []
_nearby_timer = ThrottledTimer(1000)


def configure():
    pass


def _refresh_nearby():
    global _nearby
    player_id = Player.GetAgentID()
    _nearby = [
        (aid, f"id={aid}  valid={Agent.IsValid(aid)}")
        for aid in AgentArray.GetAgentArray()
        if aid and aid != player_id
    ]


def _tick():
    global _fire_on, _locked_id, _skill_slot, _aftercast_ms

    if _nearby_timer.IsExpired():
        _nearby_timer.Reset()
        _refresh_nearby()

    if not _fire_on or not _locked_id:
        return
    if not _timer.IsExpired():
        return
    _timer.Reset()

    Player.ChangeTarget(_locked_id)
    GLOBAL_CACHE.SkillBar.UseSkill(_skill_slot, _locked_id, aftercast_delay=_aftercast_ms)


def _draw_ui():
    global _fire_on, _locked_id, _skill_slot, _aftercast_ms, _slot_buf, _id_buf, _aftercast_buf

    PyImGui.begin(MODULE_NAME, PyImGui.WindowFlags.NoCollapse)

    valid = bool(Agent.IsValid(_locked_id)) if _locked_id else False
    color = (0.3, 1.0, 0.3, 1.0) if valid else (1.0, 0.3, 0.3, 1.0)
    PyImGui.push_style_color(PyImGui.ImGuiCol.Text, color)
    PyImGui.text(f"Locked: {_locked_id or '(none)'}  valid={valid}  slot={_skill_slot}")
    PyImGui.pop_style_color(1)

    PyImGui.set_next_item_width(100)
    r = PyImGui.input_int("##id", _id_buf[0])
    if r is not None:
        _id_buf[0] = max(0, int(r))
    PyImGui.same_line(0.0, 4.0)
    if PyImGui.button("Lock##id", 50, 0) and _id_buf[0]:
        _locked_id = _id_buf[0]
        _fire_on = False

    PyImGui.set_next_item_width(60)
    s = PyImGui.input_int("Slot##slot", _slot_buf[0])
    if s is not None:
        _slot_buf[0] = max(1, min(8, int(s)))
        _skill_slot = _slot_buf[0]

    PyImGui.set_next_item_width(80)
    a = PyImGui.input_int("Aftercast ms##ac", _aftercast_buf[0])
    if a is not None:
        _aftercast_buf[0] = max(0, int(a))
        _aftercast_ms = _aftercast_buf[0]

    PyImGui.separator()

    PyImGui.begin_child("##agents", (0, 100), True)
    for aid, label in _nearby:
        if PyImGui.selectable(label, aid == _locked_id, PyImGui.SelectableFlags.NoFlag, (0.0, 0.0)):
            _locked_id = aid
            _fire_on = False
    PyImGui.end_child()

    PyImGui.separator()

    col = (0.15, 0.55, 0.15, 1.0) if _fire_on else (0.35, 0.35, 0.35, 1.0)
    PyImGui.push_style_color(PyImGui.ImGuiCol.Button, col)
    if PyImGui.button("[ ON ]  Fire" if _fire_on else "[ OFF ] Fire", -1, 0):
        _fire_on = not _fire_on
    PyImGui.pop_style_color(1)

    PyImGui.end()


def main():
    _tick()
    _draw_ui()

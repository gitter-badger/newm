from __future__ import annotations
from typing import Optional, TYPE_CHECKING, cast, TypeVar

import math
import logging

from pywm import PyWMView, PyWMViewDownstreamState
from pywm.pywm import PyWMDownstreamState
from pywm.pywm_view import PyWMViewUpstreamState

from .state import ViewState, LayoutState, WorkspaceState
from .interpolation import ViewDownstreamInterpolation
from .animate import Animate
from .overlay import MoveResizeFloatingOverlay
from .config import configured_value

if TYPE_CHECKING:
    from .layout import Layout, Workspace
else:
    Layout = TypeVar('Layout')


logger = logging.getLogger(__name__)

conf_corner_radius = configured_value('view.corner_radius', 12.5)
conf_padding = configured_value('view.padding', 8)
conf_fullscreen_padding = configured_value('view.fullscreen_padding', 0)
conf_border_ws_switch = configured_value('view.border_ws_switch', 10.)

conf_float_callback = configured_value('view.should_float', lambda view: None)
conf_floating_min_size = configured_value('view.floating_min_size', True)

conf_panel_lock_h = configured_value('panels.lock.h', 0.6)
conf_panel_lock_w = configured_value('panels.lock.w', 0.7)
conf_panel_lock_corner_radius = configured_value('panels.lock.corner_radius', 50)
conf_panel_launcher_h = configured_value('panels.launcher.h', 0.8)
conf_panel_launcher_w = configured_value('panels.launcher.w', 0.8)
conf_panel_launcher_corner_radius = configured_value('panels.launcher.corner_radius', 0)
conf_panel_notifiers_h = configured_value('panels.notifiers.h', 0.3)
conf_panel_notifiers_w = configured_value('panels.notifiers.w', 0.2)

conf_anim_t = configured_value('anim_time', .3)

class View(PyWMView[Layout], Animate[PyWMViewDownstreamState]):
    def __init__(self, wm: Layout, handle: int):
        PyWMView.__init__(self, wm, handle)
        Animate.__init__(self)

        # Initial state while waiting for map
        self._initial_state: Optional[PyWMViewDownstreamState] = None

        self.panel: Optional[str] = None

    def __str__(self) -> str:
        if self.up_state is None:
            return "<View %d>" % self._handle

        return "<View %d (%s): %s, %s, %s, %s, xwayland=%s, floating=%s>" % (
            self._handle, ("child(%d)" % self.parent._handle) if self.parent is not None else "root",
             self.title, self.app_id, self.role, self.pid,
             self.is_xwayland, self.up_state.is_floating)

    def is_dialog(self) -> bool:
        if self.up_state is None:
            return True

        return self.panel is None and self.up_state.is_floating

    def is_window(self) -> bool:
        if self.up_state is None:
            return False

        return self.panel is None and not self.up_state.is_floating

    def is_panel(self) -> bool:
        return self.panel is not None

    def _decide_floating(self) -> tuple[bool, Optional[tuple[int, int]], Optional[tuple[float, float]]]:
        size_hint: Optional[tuple[int, int]] = None
        pos_hint: Optional[tuple[float, float]] = None

        floats = self.up_state is not None and self.up_state.is_floating
        try:
            hints = conf_float_callback()(self)
            if hints is not None:
                if isinstance(hints, tuple):
                    if len(hints) >= 1:
                        floats = hints[0]
                    if len(hints) >= 2:
                        size_hint = hints[1]
                    if len(hints) >= 3:
                        pos_hint = hints[2]
                else:
                    floats = hints != False
        except Exception:
            logger.exception("floats callback")

        if (self.up_state is not None and
           self.up_state.size_constraints[0] > 0 and
           self.up_state.size_constraints[2] > 0 and
           self.up_state.size_constraints[0] == self.up_state.size_constraints[1] and
           self.up_state.size_constraints[2] == self.up_state.size_constraints[3]):
            floats = True

        if self.parent is not None:
            floats = True

        return floats, size_hint, pos_hint

    """
    Panel
    """
    def _init_panel(self, ws: Workspace) -> PyWMViewDownstreamState:
        return PyWMViewDownstreamState()

    def _main_panel(self, ws: Workspace, state: LayoutState, ws_state:WorkspaceState) -> tuple[Optional[LayoutState], Optional[LayoutState]]:
        if self._initial_state is None:
            logger.debug("Suppressing duplicate map")
            return None, None

        logger.debug("Main: Registered panel %s: %s", self.app_id, self.panel)
        self.damage()

        # Place dummy ViewState
        ws_state1 = ws_state.with_view_state(self, is_tiled=False)
        state1 = state.setting_workspace_state(ws, ws_state1)
        return state1, None

    def _reducer_panel(self, up_state: PyWMViewUpstreamState, state: LayoutState, self_state: ViewState, ws: Workspace, ws_state: WorkspaceState) -> PyWMViewDownstreamState:
        result = PyWMViewDownstreamState()

        if self.panel == "notifiers":
            result.z_index = 6
            result.accepts_input = False
            result.lock_enabled = True

            result.size = (
                int(ws.width * conf_panel_notifiers_w()),
                int(ws.height * conf_panel_notifiers_h()))

            result.box = (
                ws.width * (1. - conf_panel_notifiers_w())/2.,
                ws.height * (1. - conf_panel_notifiers_h()),
                ws.width * conf_panel_notifiers_w(),
                ws.height * conf_panel_notifiers_h())

        elif self.panel == "launcher":
            result.z_index = 5
            result.accepts_input = True
            result.corner_radius = conf_panel_launcher_corner_radius()

            result.size = (
                round(ws.width * conf_panel_launcher_w()),
                round(ws.height * conf_panel_launcher_h()))

            result.box = (
                (ws.width - result.size[0]) / 2.,
                (ws.height - result.size[1]) / 2. + (1. - state.launcher_perc) * ws.height,
                result.size[0],
                result.size[1])

        elif self.panel == "lock":
            result.z_index = 100
            result.accepts_input = True
            result.corner_radius = conf_panel_lock_corner_radius()
            result.lock_enabled = True

            result.size = (
                round(ws.width * conf_panel_lock_w()),
                round(ws.height * conf_panel_lock_h()))

            result.box = (
                (ws.width - result.size[0]) / 2.,
                (ws.height - result.size[1]) / 2. + (1. - state.lock_perc) * ws.height,
                result.size[0],
                result.size[1])

        return result

    """
    Layer
    """
    def _init_layer(self, ws: Workspace) -> PyWMViewDownstreamState:
        result = PyWMViewDownstreamState()

        width: int = 0
        height: int = 0
        if self.up_state is not None:
            width, height = self.up_state.size_constraints[1:3]

        # TODO
        if width == 0:
            width = 500
        if height == 0:
            height = 500

        result.size = (width, height)
        result.fixed_output = self.up_state.fixed_output if (self.up_state is not None and self.up_state.fixed_output is not None) else ws.outputs[0]
        return result

    def _main_layer(self, ws: Workspace, state: LayoutState, ws_state: WorkspaceState) -> tuple[Optional[LayoutState], Optional[LayoutState]]:
        if self._initial_state is None:
            logger.debug("Suppressing duplicate map")
            return None, None

        ws_state1 = ws_state.with_view_state(
            self,
            is_tiled=False, i=0, j=0, w=1, h=1,
            stack_idx=self._handle,
        )

        ws_state2 = ws_state.with_view_state(
            self,
            is_tiled=False, i=0, j=0, w=1, h=1,
            stack_idx=self._handle,
        ).focusing_view(self)

        return state.setting_workspace_state(ws, ws_state1), state.setting_workspace_state(ws, ws_state2)

    """
    Floating
    """
    def _init_floating(self, ws: Workspace, size_hint: Optional[tuple[int, int]]=None, pos_hint: Optional[tuple[float, float]]=None) -> PyWMViewDownstreamState:
        """
        Set floating attributes on init if it is clear the window will float
        """
        result = PyWMViewDownstreamState()

        width, height = 0, 0
        if self.up_state is not None:
            logger.debug("Init floating: size=%s constraints=%s hints=%s" %
                         (self.up_state.size, self.up_state.size_constraints, size_hint))

            width, height = self.up_state.size
            if size_hint is not None:
                width, height = size_hint
            elif conf_floating_min_size():
                if self.up_state.size_constraints[0] > 0 and self.up_state.size_constraints[2] > 0:
                    width, height = self.up_state.size_constraints[0], self.up_state.size_constraints[2]

            min_w, max_w, min_h, max_h = self.up_state.size_constraints
            if max_w <= 0:
                max_w = width
            if max_h <= 0:
                max_h = height
            width = max(min_w, min(max_w, width))
            height = max(min_h, min(max_h, height))

        result.floating = True
        result.size = (width, height)

        return result

    def _main_floating(self, ws: Workspace, state: LayoutState, ws_state: WorkspaceState, size_hint: Optional[tuple[int, int]], pos_hint: Optional[tuple[float, float]]) -> tuple[Optional[LayoutState], Optional[LayoutState]]:
        initial_state = self._init_floating(ws, size_hint=size_hint, pos_hint=pos_hint)
        width, height = initial_state.size

        if self.up_state is not None and self.up_state.size != (width, height):
            logger.debug("Delaying map for resize: %s != %s" % (self.up_state.size, (width, height)))
            self._initial_state = initial_state
            self.damage()
            return None, None


        if self._initial_state is None:
            logger.debug("Suppressing duplicate map")
            return None, None
        w, h = width, height

        if pos_hint is not None:
            ci = ws_state.i + pos_hint[0] * ws_state.size
            cj = ws_state.j + pos_hint[1] * ws_state.size
            logger.debug("Respecting position hint %f %f" % pos_hint)

        elif self.parent is not None:
            try:
                p_state = state.get_view_state(cast(View, self.parent))
                ci = p_state.i + p_state.w / 2.
                cj = p_state.j + p_state.h / 2.
            except:
                logger.warn("Unexpected: Could not access parent %s state" % self.parent)
        else:
            ci = ws_state.i + ws_state.size / 2.
            cj = ws_state.j + ws_state.size / 2.


        wt, ht = w / ws.width * ws_state.size, h / ws.height * ws_state.size
        i = ci - wt / 2.
        j = cj - ht / 2.

        ws_state1 = ws_state.with_view_state(
            self,
            is_tiled=False,
            float_pos=(ci, cj),
            float_size=(0, 0),
            stack_idx=self._handle,
        )

        ws_state2 = ws_state1.replacing_view_state(
            self,
            is_tiled=False,
            float_pos=(i, j),
            float_size=(w, h),
            stack_idx=self._handle,
        ).focusing_view(self)

        self.focus()
        return state.setting_workspace_state(ws, ws_state1), state.setting_workspace_state(ws, ws_state2)


    def _reducer_floating(self, up_state: PyWMViewUpstreamState, state: LayoutState, self_state: ViewState, ws: Workspace, ws_state: WorkspaceState) -> PyWMViewDownstreamState:
        result = PyWMViewDownstreamState()
        result.floating = True
        result.accepts_input = True
        result.corner_radius = conf_corner_radius() if self.parent is None else 0

        # TODO This is only for layer shell
        result.fixed_output = up_state.fixed_output

        # z_index based on hierarchy
        depth = 0
        p = self.parent
        while p is not None:
            depth += 1
            p = p.parent

        result.z_index = depth + 2

        # Keep focused view on top
        if self.is_focused():
            result.z_index += 1

        # Handle client size
        result.size = self_state.float_size if self_state.float_size[0] > 0 else (-1, -1)
        width, height = up_state.size

        # For animation purposes
        if self_state.float_size == (0, 0):
            width, height = 0, 0

        # Handle box
        size = ws_state.size_origin if ws_state.size_origin is not None else ws_state.size
        width *= size / ws_state.size
        height *= size / ws_state.size

        i, j = self_state.float_pos

        x = i - ws_state.i
        y = j - ws_state.j

        x *= ws.width / ws_state.size
        y *= ws.height / ws_state.size
        result.box = (x, y, width, height)
        result.mask = (-100000, -100000, width + 200000, height + 200000)

        if result.size[0] > 0 and result.size[1] > 0:
            if 0.99 < result.box[2] / result.size[0] < 1.01:
                if result.box[2] != result.size[0]:
                    logger.debug("Potential scaling issue (%s): w = %f != %d", self.app_id, result.box[2], result.size[0])
            if 0.99 < result.box[3] / result.size[1] < 1.01:
                if result.box[3] != result.size[1]:
                    logger.debug("Potential scaling issue (%s): h = %f != %d", self.app_id, result.box[3], result.size[1])


        result.opacity = 1.0 if (result.lock_enabled and not state.final) else state.background_opacity
        result.box = (result.box[0] + ws.pos_x, result.box[1] + ws.pos_y, result.box[2], result.box[3])

        # Workspaces don't really matter for floating windows, just leave them attached to initial workspace
        result.workspace = None

        return result

    """
    Tiled
    """
    def _init_tiled(self, ws: Workspace) -> PyWMViewDownstreamState:
        """
        Make a best-guess assumption w=h=1 and workspace size unchanged to ask the view to open with correct size
        Note that we can't be sure the view is going to be tiled at this stage - views do change min / max sizes later on
        which means they might be detected as floating in on_map
        """
        if self.up_state is not None:
            ws = self.wm.get_active_workspace()
            ws_state = self.wm.state.get_workspace_state(ws)
            result = self._reducer_tiled(self.up_state, self.wm.state, ViewState(w=1, h=1), ws, ws_state)
            result.box = (0, 0, 0, 0)
            result.floating = None
            return result
        else:
            return PyWMViewDownstreamState()

    def _main_tiled(self, ws: Workspace, state: LayoutState, ws_state:WorkspaceState) -> tuple[Optional[LayoutState], Optional[LayoutState]]:
        if self._initial_state is None:
            logger.debug("Suppressing duplicate map")
            return None, None

        min_w, _, min_h, _ = self.up_state.size_constraints if self.up_state is not None else (0., 0., 0., 0.)
        min_w *= ws_state.size / ws.width
        min_h *= ws_state.size / ws.height

        w = max(math.ceil(min_w), 1)
        h = max(math.ceil(min_h), 1)

        i: float = 0.
        j: float = 0.
        i, j = self.wm.place_initial(ws, w, h)

        second_state = (i, j, w, h)

        i, j, w, h = second_state
        i1, j1, w1, h1 = second_state

        i += .5*w
        j += .5*h
        w = 0
        h = 0

        ws_state1 = ws_state.with_view_state(
            self,
            is_tiled=True, i=i, j=j, w=w, h=h,
            scale_origin=(w1, h1), move_origin=(i1, j1),
            stack_idx=self._handle,
        )

        ws_state2 = ws_state1.replacing_view_state(
            self,
            i=i1, j=j1, w=w1, h=h1, scale_origin=(None, None), move_origin=(None, None)
        ).focusing_view(self)

        self.focus()
        return state.setting_workspace_state(ws, ws_state1), state.setting_workspace_state(ws, ws_state2)


    def _reducer_tiled(self, up_state: PyWMViewUpstreamState, state: LayoutState, self_state: ViewState, ws: Workspace, ws_state: WorkspaceState) -> PyWMViewDownstreamState:
        result = PyWMViewDownstreamState()
        result.floating = False
        result.accepts_input = True
        result.corner_radius = conf_corner_radius() if self.parent is None else 0

        if ws_state.is_fullscreen() and conf_fullscreen_padding() == 0:
            result.corner_radius = 0

        """
        z_index based on hierarchy
        """
        depth = 0
        p = self.parent
        while p is not None:
            depth += 1
            p = p.parent

        result.z_index = depth

        """
        Keep focused view on top
        """
        if self.is_focused():
            result.z_index += 1

        """
        Handle box
        """
        i = self_state.i
        j = self_state.j
        w = self_state.w
        h = self_state.h

        _, stack_idx, stack_len = self_state.stack_data
        if stack_len > 1:
            i += 0.05 * stack_idx / (stack_len - 1)
            j += 0.05 * ws.width / ws.height * stack_idx / (stack_len - 1)
            w -= 0.05
            h -= 0.05 * ws.width / ws.height

        x = i - ws_state.i
        y = j - ws_state.j

        x *= ws.width / ws_state.size
        y *= ws.height / ws_state.size
        w *= ws.width / ws_state.size
        h *= ws.height / ws_state.size

        padding = conf_fullscreen_padding() if ws_state.is_fullscreen() else conf_padding()

        if w != 0 and h != 0:
            x += padding
            y += padding
            w -= 2*padding
            h -= 2*padding

        """
        An attempt to reduce the effect of dreadful CSD
        As always Chrome is ahead in this regard, rendering completely unwanted shadows
        --> If up_state.offset indicates weird CSD stuff going on, just fix the tile using masks
        """
        use_mask_for_offset: Optional[tuple[float, float]] = None

        if up_state.size[0] > 0 and up_state.size[1] > 0:
            ox = up_state.offset[0] / up_state.size[0] * w
            oy = up_state.offset[1] / up_state.size[1] * h
            x -= ox
            y -= oy
            use_mask_for_offset = ox, oy

        """
        Handle client size
        """
        w_for_size, h_for_size = self_state.scale_origin
        if w_for_size is None or h_for_size is None:
            w_for_size, h_for_size = self_state.w, self_state.h

        size = ws_state.size_origin if ws_state.size_origin is not None else ws_state.size

        w_for_size *= ws.width / size
        h_for_size *= ws.height / size
        w_for_size -= 2*padding
        h_for_size -= 2*padding

        width = round(w_for_size)
        height = round(h_for_size)

        result.size = (width, height)

        """
        Override: Keep aspect-ratio of windows
        """
        min_w, max_w, min_h, max_h = up_state.size_constraints

        if width < min_w and min_w > 0:
            width = min_w
        if width > max_w and max_w > 0:
            width = max_w
        if height < min_h and min_h > 0:
            height = min_h
        if height > max_h and max_h > 0:
            width = min_w

        old_ar = result.size[1] / result.size[0] if result.size[0] > 0 else 1.
        new_ar = height / width if width > 0 else 1.

        if abs(old_ar - new_ar) > 0.001:
            if new_ar < old_ar:
                # new width is larger - would appear scaled up vertically
                h *= new_ar / old_ar
            else:
                # new width is smaller - would appear scaled up horizontally
                w *= old_ar / new_ar

        result.size = (width, height)
        result.box = (x, y, w, h)

        if use_mask_for_offset is not None:
            result.mask = (use_mask_for_offset[0], use_mask_for_offset[1], w, h)

        if result.size[0] > 0 and result.size[1] > 0:
            if 0.99 < result.box[2] / result.size[0] < 1.01:
                if result.box[2] != result.size[0]:
                    logger.debug("Potential scaling issue (%s): w = %f != %d", self.app_id, result.box[2], result.size[0])
            if 0.99 < result.box[3] / result.size[1] < 1.01:
                if result.box[3] != result.size[1]:
                    logger.debug("Potential scaling issue (%s): h = %f != %d", self.app_id, result.box[3], result.size[1])

        result.opacity = 1.0 if (result.lock_enabled and not state.final) else state.background_opacity
        result.box = (result.box[0] + ws.pos_x, result.box[1] + ws.pos_y, result.box[2], result.box[3])

        if self_state.move_origin[0] is not None and self_state.scale_origin[0] is None:
            # No fixed output during a move
            result.workspace = None
        else:
            result.workspace = (ws.pos_x, ws.pos_y, ws.width, ws.height)

        return result


    """
    Init and map
    """
    def init(self) -> PyWMViewDownstreamState:
        logger.info("Init: %s", self)

        ws = self.wm.get_active_workspace()
        if self.up_state is not None and (output := self.up_state.fixed_output) is not None:
            wss = [w for w in self.wm.workspaces if output in w.outputs]
            if len(wss) != 1:
                logger.warn("Unexpected: Could not find output %s in workspaces" % output)
            else:
                ws = wss[0]

        if self.pid is not None:
            self.panel = self.wm.panel_launcher.get_panel_for_pid(self.pid)

        if self.panel is not None:
            self._initial_state = self._init_panel(ws)

        elif self.role == "layer":
            self._initial_state = self._init_layer(ws)

        else:
            floats, size_hint, pos_hint = self._decide_floating()

            if floats:
                self._initial_state = self._init_floating(ws, size_hint=size_hint, pos_hint=pos_hint)

            else:
                self._initial_state = self._init_tiled(ws)

        return self._initial_state

    def main(self, state: LayoutState) -> tuple[Optional[LayoutState], Optional[LayoutState]]:
        logger.info("Main: %s", self)

        ws = self.wm.get_active_workspace()
        if self.up_state is not None and (output := self.up_state.fixed_output) is not None:
            wss = [w for w in self.wm.workspaces if output in w.outputs]
            if len(wss) != 1:
                logger.warn("Unexpected: Could not find output %s in workspaces" % output)
            else:
                ws = wss[0]

        ws_state = state.get_workspace_state(ws)

        if self.panel is not None:
            result = self._main_panel(ws, state, ws_state)

        elif self.role == "layer":
            result = self._main_layer(ws, state, ws_state)

        else:
            floats, size_hint, pos_hint = self._decide_floating()

            if floats:
                result = self._main_floating(ws, state, ws_state, size_hint=size_hint, pos_hint=pos_hint)
            else:
                result = self._main_tiled(ws, state, ws_state)

        if result != (None, None):
            self._initial_state is not None

        return result

    def on_map(self) -> None:
        logger.debug("Map: %s" % self)
        self.wm.animate_to(self.main, conf_anim_t(), None)

    def reducer(self, up_state: PyWMViewUpstreamState, state: LayoutState) -> PyWMViewDownstreamState:
        try:
            self_state, ws_state, ws_handle = state.find_view(self)
            ws = [w for w in self.wm.workspaces if w._handle == ws_handle][0]
            self._initial_state = None
        except Exception:
            if self._initial_state is None:
                logger.warn("Missing initial state: %s" % self)
                return PyWMViewDownstreamState()
            return self._initial_state

        if self.panel is not None:
            return self._reducer_panel(up_state, state, self_state, ws, ws_state)
        elif self_state.is_tiled:
            return self._reducer_tiled(up_state, state, self_state, ws, ws_state)
        elif self_state.is_layer:
            # TODO
            return self._reducer_floating(up_state, state, self_state, ws, ws_state)
        else:
            return self._reducer_floating(up_state, state, self_state, ws, ws_state)


    def animate(self, old_state: LayoutState, new_state: LayoutState, dt: float) -> None:
        if self.up_state is None:
            return

        cur = self.reducer(self.up_state, old_state)
        nxt = self.reducer(self.up_state, new_state)

        self._animate(ViewDownstreamInterpolation(self.wm, cur, nxt), dt)

    def process(self, up_state: PyWMViewUpstreamState) -> PyWMViewDownstreamState:
        return self._process(self.reducer(up_state, self.wm.state))

    def on_event(self, event: str) -> None:
        if event == "request_move":
            if self.up_state is not None and self.up_state.is_floating:
                self.wm.enter_overlay(
                    MoveResizeFloatingOverlay(self.wm, self))
        elif event == "request_fullscreen":
            logger.debug("Client requested fullscreen - following")
            if self.is_focused():
                self.wm.toggle_fullscreen(True)
            self.set_fullscreen(True)
        elif event == "request_nofullscreen":
            logger.debug("Client requests to leave fullscreen - following")
            if self.is_focused():
                self.wm.toggle_fullscreen(False)
            self.set_fullscreen(False)

    def is_focused(self) -> bool:
        return self.up_state is not None and self.up_state.is_focused


    def destroy(self) -> None:
        self.wm.destroy_view(self)


    def toggle_floating(self, state: ViewState, ws: Workspace, ws_state: WorkspaceState) -> tuple[ViewState, ViewState]:
        padding = conf_padding() if not ws_state.is_fullscreen() else 0
        if state.is_tiled:
            float_size = self.up_state.size if self.up_state is not None else (100, 100)
            float_pos = state.i + 0.1, state.j - 0.1
            return state, state.copy(is_tiled=False, float_size=float_size, float_pos=float_pos)

        else:
            w = max(1, round((state.float_size[0] + 2*padding) / ws.width * ws_state.size))
            h = max(1, round((state.float_size[1] + 2*padding) / ws.height * ws_state.size))
            i = round(state.float_pos[0])
            j = round(state.float_pos[1])
            return state, state.copy(is_tiled=True, i=i, j=j, w=w, h=h)

        return state.copy(is_tiled=not self.is_floating, i=i, j=j, w=w, h=h)


    def transform_to_closest_ws(self, ws: Workspace, i0: float, j0: float, w0: float, h0: float) -> tuple[Workspace, float, float, float, float]:
        if self.panel is not None:
            return ws, i0, j0, w0, h0

        ws_state = self.wm.state.get_workspace_state(ws)

        x = i0 - ws_state.i
        y = j0 - ws_state.j

        x *= ws.width / ws_state.size
        y *= ws.height / ws_state.size
        w = w0 * ws.width / ws_state.size
        h = h0 * ws.height / ws_state.size

        x += ws.pos_x
        y += ws.pos_y

        cx = x + .5*w
        cy = y + .5*h

        border_ws_switch = conf_border_ws_switch()

        if ws.pos_x - border_ws_switch <= cx <= ws.pos_x + ws.width + border_ws_switch and ws.pos_y - border_ws_switch <= cy <= ws.pos_y + ws.height + border_ws_switch:
            return ws, i0, j0, w0, h0

        for wsp in self.wm.workspaces:
            if wsp.pos_x < cx < wsp.pos_x + wsp.width and wsp.pos_y < cy < wsp.pos_y + wsp.height:
                wsp_state = self.wm.state.get_workspace_state(wsp)
                wp = w * wsp_state.size / wsp.width
                hp = h * wsp_state.size / wsp.height

                wp = min(wp, wsp_state.size)
                hp = min(hp, wsp_state.size)

                ip = (cx - .5 * wp * wsp.width / wsp_state.size - wsp.pos_x) * wsp_state.size / wsp.width + wsp_state.i
                jp = (cy - .5 * hp * wsp.height / wsp_state.size - wsp.pos_y) * wsp_state.size / wsp.height + wsp_state.j

                return wsp, ip, jp, wp, hp

        logger.debug("View outside of workspaces - defaulting")
        return ws, i0, j0, w0, h0

    def on_resized(self, width: int, height: int, client_leading: bool) -> None:
        if self._initial_state is not None:
            logger.debug("Retrying map")
            self.on_map()
            return

        if client_leading and self.up_state is not None and self.up_state.is_floating:
            self.wm.state.update_view_state(self, float_size=(width, height))
            self.damage()

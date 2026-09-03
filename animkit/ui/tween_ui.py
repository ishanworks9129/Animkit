"""Tween slider UI.

Custom-painted rather than a styled QSlider, for three reasons a QSlider
cannot give you: a centre-anchored fill, travel that continues past the widget
edge for overshoot, and snap-back-to-zero on release.
"""

import logging

from maya import cmds

from animkit.core import settings, undo
from animkit.tools import tween
from animkit.ui import mayawin
from animkit.vendor import qt

QtCore = qt.QtCore
QtGui = qt.QtGui
QtWidgets = qt.QtWidgets

log = logging.getLogger(__name__)

# Renamed from animkitTweenControl. Maya had persisted a broken uiScript under
# that name, and saved workspaceControl state survives deleteUI -- so a fresh
# name is the one guaranteed clean slate. purge_workspace_control_state() is
# the actual fix; this just avoids depending on it for the first run.
CONTROL_NAME = "animkitTweenPanel"
BUILD_CODE = "import animkit.ui.tween_ui as m; m.build()"

# How far past the ends of the bar a DRAG may travel. Generous on purpose:
# reaching 200 means moving a full bar-width beyond the edge, so it is always
# deliberate and never gets in the way.
#
# Both limits are now SHIPPED DEFAULTS rather than the live values -- the live
# ones come from animkit.core.settings and are read per widget, so an animator
# who changed them keeps them across restarts. They are sourced from
# settings.DEFAULTS rather than repeated here so there is exactly one place to
# change a default.
OVERSHOOT = settings.DEFAULTS["tween.overshoot"]

# Limit for TYPED and nudged input, deliberately tighter than the drag clamp.
# Typing 200 is as easy as typing 20, so the guard rail has to do the work the
# physical effort of dragging does. Overshoot past ~130% of the way to the
# neighbouring key is almost always a slip rather than an intent.
NUMERIC_LIMIT = settings.DEFAULTS["tween.numeric_limit"]


class TweenBar(QtWidgets.QWidget):
    """Centre-anchored scrub bar. Emits normalised -1..1 (or beyond)."""

    dragStarted = QtCore.Signal()
    valueChanged = QtCore.Signal(float)
    dragFinished = QtCore.Signal(float)
    nudgeRequested = QtCore.Signal(float)

    def __init__(self, parent=None, overshoot=None):
        super(TweenBar, self).__init__(parent)
        # Per instance, not per module: a settings change must reach a panel
        # built after it without a Maya restart, and a module constant read at
        # import time cannot do that.
        self._overshoot = (
            float(overshoot) if overshoot is not None
            else float(settings.get("tween.overshoot"))
        )
        self.setMinimumHeight(34)
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.SizeHorCursor)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        self._value = 0.0
        self._dragging = False

        self._col_track = QtGui.QColor(48, 48, 48)
        self._col_fill = QtGui.QColor(88, 150, 205)
        self._col_over = QtGui.QColor(205, 122, 72)
        self._col_tick = QtGui.QColor(96, 96, 96)
        self._col_text = QtGui.QColor(220, 220, 220)

    # --- value plumbing -----------------------------------------------------

    def value(self):
        return self._value

    def overshoot(self):
        return self._overshoot

    def set_overshoot(self, value):
        self._overshoot = float(value)
        self.set_value(self._value, notify=False)

    def set_value(self, value, notify=True):
        value = max(-self._overshoot, min(self._overshoot, float(value)))
        if value == self._value:
            return
        self._value = value
        self.update()
        if notify:
            self.valueChanged.emit(value)

    def _x_to_value(self, x):
        w = max(1, self.width())
        return (float(x) / w) * 2.0 - 1.0

    def _value_to_x(self, value):
        w = max(1, self.width())
        return (value + 1.0) * 0.5 * w

    # --- interaction --------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            return super(TweenBar, self).mousePressEvent(event)
        self._dragging = True
        self.dragStarted.emit()
        self._apply_mouse(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._apply_mouse(event)

    def mouseReleaseEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton or not self._dragging:
            return super(TweenBar, self).mouseReleaseEvent(event)
        self._dragging = False
        final = self._value
        self.dragFinished.emit(final)
        # Snap back to centre so the next drag starts from the new pose.
        self._value = 0.0
        self.update()

    def _apply_mouse(self, event):
        pos = qt.event_pos(event)
        value = self._x_to_value(pos.x())

        mods = event.modifiers()
        if mods & QtCore.Qt.ShiftModifier:
            value *= 0.25                      # fine
        if mods & QtCore.Qt.ControlModifier:
            value = round(value * 10.0) / 10.0  # snap to tenths

        self.set_value(value)

    def wheelEvent(self, event):
        """One-shot nudge per notch.

        This deliberately does NOT go through the drag signals: a wheel event
        has no mouse-down, so there is no session for update()/commit() to
        act on. It emits its own signal and the widget applies it as a
        standalone tween.
        """
        delta = event.angleDelta().y()
        if not delta:
            return
        step = 0.05 if event.modifiers() & QtCore.Qt.ShiftModifier else 0.1
        self.nudgeRequested.emit(step if delta > 0 else -step)
        event.accept()


    # --- paint --------------------------------------------------------------

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)

        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        centre = rect.center().x()

        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(self._col_track)
        p.drawRoundedRect(rect, 3, 3)

        # fill from centre
        x = self._value_to_x(self._value)
        x = max(rect.left(), min(rect.right(), x))
        colour = self._col_over if abs(self._value) > 1.0 else self._col_fill
        if abs(x - centre) > 0.5:
            fill = QtCore.QRectF(
                min(centre, x), rect.top(), abs(x - centre), rect.height()
            )
            p.setBrush(colour)
            p.drawRoundedRect(fill, 3, 3)

        # ticks at -1, -0.5, 0, 0.5, 1
        pen = QtGui.QPen(self._col_tick)
        pen.setWidth(1)
        p.setPen(pen)
        for mark in (-1.0, -0.5, 0.0, 0.5, 1.0):
            # Nudge inside the clip boundary: a line drawn exactly on
            # rect.left()/right() does not render, so the -1 and +1 ticks
            # silently vanish while the -0.5/+0.5 ones look fine.
            mx = min(max(self._value_to_x(mark), rect.left() + 0.5),
                     rect.right() - 0.5)
            inset = rect.height() * (0.18 if mark == 0.0 else 0.34)
            p.drawLine(
                QtCore.QPointF(mx, rect.top() + inset),
                QtCore.QPointF(mx, rect.bottom() - inset),
            )

        # readout
        p.setPen(self._col_text)
        font = p.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 0.5))
        p.setFont(font)
        p.drawText(
            rect,
            QtCore.Qt.AlignCenter,
            "{0:+.0f}".format(self._value * 100.0) if self._value else "0",
        )


class TweenWidget(QtWidgets.QWidget):
    """Bar + mode selector + quick buttons, wired to a TweenSession."""

    def __init__(self, parent=None):
        super(TweenWidget, self).__init__(parent)
        self._session = None
        # Read once at construction. Everything below uses these, so a bad
        # value in the file cannot reach a widget constructor -- settings has
        # already coerced it back to a shipped default by this point.
        self._numeric_limit = float(settings.get("tween.numeric_limit"))
        self._quick_values = list(settings.get("tween.quick_buttons"))
        self._build_ui()
        self._connect()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(4)

        self.mode_combo = QtWidgets.QComboBox()
        for mode in tween.MODES:
            self.mode_combo.addItem(tween.MODE_LABELS[mode], mode)
        # Restore the last mode used. An unknown mode in the file -- an old
        # name, a typo -- finds no match and leaves the combo on its first
        # entry, which is the shipped default. No special case needed.
        saved_mode = settings.get("tween.mode")
        index = self.mode_combo.findData(saved_mode)
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)
        self.mode_combo.setToolTip(
            tween.MODE_TOOLTIPS.get(self.mode_combo.currentData(), "")
        )
        top.addWidget(self.mode_combo, 1)

        self.spin = QtWidgets.QDoubleSpinBox()
        self.spin.setDecimals(0)
        self.spin.setSingleStep(5)
        self.spin.setValue(0)
        self.spin.setFixedWidth(64)
        top.addWidget(self.spin)
        self._apply_numeric_limit()

        layout.addLayout(top)

        self.bar = TweenBar(overshoot=settings.get("tween.overshoot"))
        layout.addWidget(self.bar)

        self._quick_layout = QtWidgets.QHBoxLayout()
        self._quick_layout.setSpacing(3)
        self._quick_buttons = []
        layout.addLayout(self._quick_layout)
        self._rebuild_quick_buttons()

        # Right-click anywhere on the panel. Without this the three persisted
        # settings other than the mode would have no way to be set at all, and
        # a preference nobody can change is not a preference.
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

    # --- persisted settings -------------------------------------------------

    def _apply_numeric_limit(self):
        limit = self._numeric_limit * 100.0
        self.spin.setRange(-limit, limit)
        self.spin.setToolTip(
            "Type a value and press Enter to apply it once. "
            "Range +/-{0:.0f}. Drag the bar past its end for more.".format(limit)
        )

    def _rebuild_quick_buttons(self):
        while self._quick_layout.count():
            item = self._quick_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._quick_buttons = []

        for value in self._quick_values:
            label = "{0:.0f}".format(value * 100.0)
            btn = QtWidgets.QPushButton(label)
            btn.setFixedHeight(20)
            btn.setToolTip("Apply {0}% in one click".format(label))
            btn.clicked.connect(lambda _=False, v=value: self._apply_once(v))
            self._quick_layout.addWidget(btn)
            self._quick_buttons.append(btn)

    def _connect(self):
        self.bar.dragStarted.connect(self._on_drag_started)
        self.bar.valueChanged.connect(self._on_drag_moved)
        self.bar.dragFinished.connect(self._on_drag_finished)
        self.bar.nudgeRequested.connect(self._apply_once)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.spin.editingFinished.connect(self._on_spin_committed)

    def current_mode(self):
        return self.mode_combo.currentData()

    # --- session lifecycle --------------------------------------------------

    def _on_mode_changed(self, _index):
        mode = self.current_mode()
        self.mode_combo.setToolTip(tween.MODE_TOOLTIPS.get(mode, ""))
        settings.set("tween.mode", mode)

    def _on_drag_started(self):
        self._session = tween.TweenSession(mode=self.current_mode())
        if not self._session.begin():
            self._session = None
            # Silence here reads as "the tool is broken". Say what happened.
            try:
                cmds.inViewMessage(
                    assistMessage="animkit: nothing to tween "
                                  "(no animated channels at this frame)",
                    position="midCenter",
                    fade=True,
                )
            except Exception:
                pass

    def _on_drag_moved(self, value):
        if self._session is None:
            return
        try:
            # Suspend per mouse-move, NOT across the whole drag. Holding the
            # suspend open for the duration would collapse hundreds of
            # redraws into none -- the animator would be scrubbing a frozen
            # viewport. Scoping it to one handler collapses N attribute
            # writes into one redraw, which is the win you actually want on a
            # heavy rig.
            with undo.suspend_refresh():
                self._session.update(value)
        except Exception:
            log.exception("animkit: tween update failed")
            self._abort()

    def _on_drag_finished(self, value):
        if self._session is None:
            return
        try:
            self._session.commit(value)
        except Exception:
            log.exception("animkit: tween commit failed")
            try:
                self._session.cancel()
            except Exception:
                # cancel() itself failed -- the undo chunk is still open and
                # will swallow every subsequent scene edit. Force it shut.
                try:
                    self._session.force_close()
                except Exception:
                    pass
        finally:
            self._session = None
            undo.force_resume_refresh()

    def _abort(self):
        if self._session is not None:
            try:
                self._session.cancel()
            except Exception:
                try:
                    self._session.force_close()
                except Exception:
                    pass
            self._session = None
        undo.force_resume_refresh()

    def _apply_once(self, value):
        """Apply a one-shot tween. The funnel for every non-drag input.

        Quick buttons, wheel nudges and the numeric field all arrive here, so
        this is the one place the typed/nudged limit needs enforcing -- the
        spinbox range alone would leave the other two unclamped.
        """
        limit = self._numeric_limit
        value = max(-limit, min(limit, float(value)))
        tween.tween_once(value, mode=self.current_mode())

    def _on_spin_committed(self):
        value = self.spin.value() / 100.0
        if abs(value) > 1e-6:
            self._apply_once(value)

    # --- settings menu ------------------------------------------------------

    def _show_menu(self, point):
        menu = QtWidgets.QMenu(self)
        menu.addAction("Quick Buttons...", self._edit_quick_buttons)
        menu.addAction("Drag Limit...", self._edit_overshoot)
        menu.addAction("Typed / Nudge Limit...", self._edit_numeric_limit)
        menu.addSeparator()
        menu.addAction("Reset to Defaults", self._reset_settings)
        qt.exec_popup(menu, self.mapToGlobal(point))

    def _edit_quick_buttons(self):
        current = ", ".join("{0:.0f}".format(v * 100.0) for v in self._quick_values)
        text, ok = QtWidgets.QInputDialog.getText(
            self, "Quick Buttons",
            "Percentages, comma separated (e.g. -100, -60, -30, 30, 60, 100):",
            QtWidgets.QLineEdit.Normal, current,
        )
        if not ok:
            return

        values = []
        for chunk in text.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                values.append(float(chunk) / 100.0)
            except ValueError:
                # One bad entry loses the whole edit rather than silently
                # dropping a button the animator meant to have.
                cmds.warning(
                    "animkit: %r is not a number -- quick buttons unchanged" % chunk
                )
                return
        if not values:
            return

        self._quick_values = values
        settings.set("tween.quick_buttons", values)
        self._rebuild_quick_buttons()

    def _edit_overshoot(self):
        value, ok = QtWidgets.QInputDialog.getDouble(
            self, "Drag Limit",
            "How far past the end of the bar a DRAG may travel, in percent:",
            self.bar.overshoot() * 100.0, 100.0, 1000.0, 0,
        )
        if not ok:
            return
        settings.set("tween.overshoot", value / 100.0)
        self.bar.set_overshoot(value / 100.0)

    def _edit_numeric_limit(self):
        value, ok = QtWidgets.QInputDialog.getDouble(
            self, "Typed / Nudge Limit",
            "Limit for typed values, wheel nudges and quick buttons, in percent:",
            self._numeric_limit * 100.0, 1.0, 1000.0, 0,
        )
        if not ok:
            return
        self._numeric_limit = value / 100.0
        settings.set("tween.numeric_limit", self._numeric_limit)
        self._apply_numeric_limit()

    def _reset_settings(self):
        defaults = settings.reset()
        self._numeric_limit = float(defaults["tween.numeric_limit"])
        self._quick_values = list(defaults["tween.quick_buttons"])
        self.bar.set_overshoot(defaults["tween.overshoot"])
        self._apply_numeric_limit()
        self._rebuild_quick_buttons()
        index = self.mode_combo.findData(defaults["tween.mode"])
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)


# --- entry points -----------------------------------------------------------


def build():
    """Fill the current workspaceControl. Named by BUILD_CODE / uiScript.

    Must work from a cold interpreter -- Maya calls this on startup to restore
    a docked panel, before anything else in this session has run.

    Exceptions are caught and printed rather than propagated. A uiScript that
    raises leaves an empty panel and a single red line easily lost in the
    Script Editor, so print the whole traceback and say which function failed.
    """
    try:
        import animkit

        animkit.startup()

        parent = mayawin.current_parent()
        if parent is None:
            log.error("animkit: build() called with no current parent")
            return None

        if parent.layout() is None:
            QtWidgets.QVBoxLayout(parent)
        mayawin.clear_children(parent)

        widget = TweenWidget(parent)
        parent.layout().setContentsMargins(0, 0, 0, 0)
        parent.layout().addWidget(widget)

        # Tells show() this control was populated successfully. Without it the
        # next show() would restore an empty panel forever.
        parent.setProperty(mayawin.BUILT_PROPERTY, True)
        return widget
    except Exception:
        import traceback

        traceback.print_exc()
        print("animkit: tween_ui.build() failed -- panel will be empty")
        return None


def show():
    """Open the dockable tween panel."""
    return mayawin.show_workspace_control(
        CONTROL_NAME, "Tween", BUILD_CODE, width=300
    )


def reset(*extra_names):
    """Delete the panel and its saved state so show() rebuilds from scratch.

    Use after editing this module: reload_all() replaces the classes, but a live
    workspaceControl still holds widgets built from the old ones.

    Pass extra control names to clean up panels from earlier versions, e.g.
    reset("animkitTweenControl").
    """
    results = {CONTROL_NAME: mayawin.delete_workspace_control(CONTROL_NAME)}
    for name in extra_names:
        results[name] = mayawin.delete_workspace_control(name)
    return results


#: Control names this panel has used previously. reset_all() clears their saved
#: state too, so a stale uiScript cannot outlive a rename.
LEGACY_CONTROL_NAMES = ("animkitTweenControl",)


def reset_all():
    """reset() plus every historical control name."""
    return reset(*LEGACY_CONTROL_NAMES)

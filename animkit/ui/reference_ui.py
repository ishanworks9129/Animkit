"""The Ref tab: a drop zone, and one row per reference that is up.

Two ways in, because the two gestures fail in different places. Dropping on the
viewport is the fast one and it is what an animator will actually do -- but it
needs an event filter on a Maya widget, and a studio with a locked-down or
unusual Qt setup can end up without it. The drop zone in this panel is a plain
QWidget that animkit owns outright, so it works wherever the panel does.

The rows READ THE SCENE every refresh and cache nothing, for the same reason
the Sets panel does: opacity, offset and visibility are all changed by hotkey,
by undo, and by opening a different shot, so any copy kept here is a stale copy
waiting to be shown.

WHY THE OPACITY SLIDER OPENS ITS OWN UNDO CHUNK
------------------------------------------------
A slider drag is ONE user-visible operation and has to be one undo step. Left
alone it is one per pixel of travel: sixty `setAttr` calls, sixty entries, and
an animator who fades a reference has to press Ctrl+Z sixty times to get back
past it. Press opens a chunk, release closes it, and the per-move calls nest
inside -- `undo_chunk` nests, so the operations layer needs no special case for
being driven by a slider.
"""

import logging

from maya import cmds

from animkit.core import settings, undo
from animkit.tools import audio, reference
from animkit.ui import mayawin, style
from animkit.vendor import qt

QtCore = qt.QtCore
QtGui = qt.QtGui
QtWidgets = qt.QtWidgets

log = logging.getLogger(__name__)

#: Non-zero while the panel is the one writing to the scene.
#:
#: The panel watches the scene so it follows a hotkey, the Channel Box and
#: undo without anything being pressed. That watch would otherwise fire on the
#: panel's OWN writes, and rebuilding a row underneath a slider that is still
#: being dragged destroys the widget mid-drag. Module level rather than
#: per-widget because two panels open on one scene both need to stay out of
#: each other's way, and both are looking at the same attributes.
_APPLYING = 0


class _Applying(object):
    """Context manager form of `_APPLYING`. Reentrant, and exception-safe."""

    def __enter__(self):
        global _APPLYING
        _APPLYING += 1
        return self

    def __exit__(self, *_args):
        global _APPLYING
        _APPLYING = max(0, _APPLYING - 1)
        return False


CONTROL_NAME = "animkitRefPanel"
BUILD_CODE = "import animkit.ui.reference_ui as m; m.build()"


# --- the drop zone ----------------------------------------------------------


class DropZone(QtWidgets.QFrame):
    """A rectangle that means "put a file here".

    Painted rather than styled, because a dashed border that lights up under a
    drag is three states of one shape and a stylesheet would be three
    stylesheets swapped at runtime.

    `dropped` carries plain path strings. The QMimeData behind them belongs to
    the drag and is freed the moment the handler returns, so nothing downstream
    is ever handed the event.
    """

    dropped = QtCore.Signal(list)

    #: Logical height. Tall enough to be an obvious target in a docked strip,
    #: short enough not to push the reference rows below the fold.
    HEIGHT = 62

    def __init__(self, parent=None):
        super(DropZone, self).__init__(parent)
        self.setAcceptDrops(True)
        self.setFixedHeight(style.px(self.HEIGHT))
        self.setToolTip(
            "Drop a video, an image, or an image sequence here.\n\n"
            "Dropping one frame of a sequence loads the whole sequence -- so "
            "does dropping the folder, or every frame at once."
        )
        self._hot = False

    # --- drag and drop ---

    def _paths(self, event):
        try:
            mime = event.mimeData()
            if mime is None or not mime.hasUrls():
                return []
            return [url.toLocalFile() for url in mime.urls()
                    if url.isLocalFile() and url.toLocalFile()]
        except Exception:
            log.debug("animkit: could not read dropped mime data",
                      exc_info=True)
            return []

    def dragEnterEvent(self, event):
        if self._paths(event):
            self._hot = True
            self.update()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._hot:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._hot = False
        self.update()
        super(DropZone, self).dragLeaveEvent(event)

    def dropEvent(self, event):
        paths = self._paths(event)
        self._hot = False
        self.update()
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.dropped.emit(paths)

    # --- painting ---

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            accent = style.accent_for("Ref")
            colour = QtGui.QColor(accent if self._hot else style.TEXT_DIM)

            pen = QtGui.QPen(colour)
            pen.setWidthF(max(1.0, style.px(1)))
            pen.setStyle(QtCore.Qt.DashLine)
            painter.setPen(pen)

            inset = style.px(3)
            box = self.rect().adjusted(inset, inset, -inset, -inset)
            if self._hot:
                fill = QtGui.QColor(accent)
                fill.setAlpha(38)
                painter.setBrush(fill)
            else:
                painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRoundedRect(box, style.px(style.RADIUS),
                                    style.px(style.RADIUS))

            painter.setPen(QtGui.QPen(colour))
            painter.drawText(
                box,
                QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap,
                "drop video / images / sound here"
                if not self._hot else "let go to load",
            )
        finally:
            painter.end()


# --- one row ----------------------------------------------------------------


class ReferenceRow(QtWidgets.QFrame):
    """The controls for one reference. Reads its state at construction.

    Rebuilt rather than updated on refresh -- there are one or two of these in
    a scene, and a rebuild cannot show a value the scene no longer holds.
    """

    changed = QtCore.Signal()

    def __init__(self, entry, parent=None):
        super(ReferenceRow, self).__init__(parent)
        self.entry = entry
        self.setObjectName("animkitCard")
        self._chunk = None
        self._build_ui()

    def _build_ui(self):
        column = QtWidgets.QVBoxLayout(self)
        column.setContentsMargins(
            style.px(5), style.px(4), style.px(5), style.px(4)
        )
        column.setSpacing(style.px(3))

        column.addLayout(self._title_row())
        column.addLayout(self._control_row())

    def _title_row(self):
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(style.px(4))

        self._eye = QtWidgets.QPushButton("On" if self.entry.visible else "Off")
        self._eye.setCheckable(True)
        self._eye.setChecked(self.entry.visible)
        self._eye.setFixedSize(style.px(30), style.px(style.ROW_HEIGHT))
        self._eye.setToolTip(
            "Show or hide this reference. It stays in the scene either way.\n\n"
            "Hotkey Editor: animkitRefToggle"
        )
        self._eye.clicked.connect(self._on_eye)
        row.addWidget(self._eye)

        label = QtWidgets.QLabel(self.entry.label)
        # The label is the sequence name; the full path, the range and which
        # camera it is on are in the tooltip. A docked panel is ~300px wide and
        # a path to a network share does not fit in it, so putting the path
        # here would push the buttons off the edge to show a prefix that is the
        # same for every reference in the shot.
        label.setToolTip(self._detail())
        label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        row.addWidget(label, 1)

        free = self.entry.is_free
        self._pin = QtWidgets.QPushButton("Free" if free else "Pinned")
        self._pin.setFixedSize(style.px(46), style.px(style.ROW_HEIGHT))
        self._pin.setToolTip(
            ("Free: this is an ordinary object. Select it and the move, "
             "rotate and scale tools work on it.\n\nPress to pin it to the "
             "camera instead."
             if free else
             "Pinned to the camera: it fills the frame and rides the view, "
             "and cannot be grabbed in the viewport.\n\nPress to free it so "
             "you can move and scale it.")
            + "\n\nHotkey Editor: animkitRefPin"
        )
        self._pin.clicked.connect(self._on_pin)
        row.addWidget(self._pin)

        select = QtWidgets.QPushButton("Sel")
        select.setFixedSize(style.px(28), style.px(style.ROW_HEIGHT))
        select.setToolTip(
            "Select this image plane, so the buttons and hotkeys act on it "
            "alone rather than on every reference.\n\nFor a free reference "
            "this is also how you get the manipulator onto it."
        )
        select.clicked.connect(self._on_select)
        row.addWidget(select)

        remove = QtWidgets.QPushButton("X")
        remove.setFixedSize(style.px(22), style.px(style.ROW_HEIGHT))
        remove.setProperty("animkitDanger", True)
        remove.setToolTip("Remove this reference. One undo step.")
        remove.clicked.connect(self._on_remove)
        row.addWidget(remove)
        return row

    def _control_row(self):
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(style.px(4))

        fade = QtWidgets.QLabel("fade")
        fade.setObjectName("animkitHint")
        row.addWidget(fade)

        self._opacity = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._opacity.setRange(0, 100)
        self._opacity.setValue(int(round(self.entry.opacity * 100)))
        self._opacity.setToolTip(
            "How solid the reference is. Fade it until the rig reads through "
            "it."
        )
        # See the module docstring: one drag has to be one undo step.
        self._opacity.sliderPressed.connect(self._open_chunk)
        self._opacity.sliderReleased.connect(self._close_chunk)
        self._opacity.valueChanged.connect(self._on_opacity)
        row.addWidget(self._opacity, 1)

        offset = QtWidgets.QLabel("starts at")
        offset.setObjectName("animkitHint")
        row.addWidget(offset)

        self._offset = QtWidgets.QSpinBox()
        self._offset.setRange(-100000, 100000)
        self._offset.setValue(self.entry.starts_at)
        self._offset.setFixedWidth(style.px(60))
        self._offset.setEnabled(self.entry.is_sequence)
        self._offset.setToolTip(self._start_tip())
        self._offset.valueChanged.connect(self._on_offset)
        row.addWidget(self._offset)
        return row

    def _start_tip(self):
        """Why this box shows a frame number and not `frameOffset`.

        Maya's own value is unreadable as a quantity: a reference dropped on
        frame 32 reports an offset of -31, which tells an animator nothing
        unless they already know the sequence starts at 1 and that Maya adds
        the offset to time. The frame it starts on is the same fact, in the
        units of the thing they are looking at.
        """
        if not self.entry.is_sequence:
            return "A still has only one frame, so there is nothing to move."
        span = self.entry.frame_range()
        covers = ""
        if span:
            start = self.entry.starts_at
            covers = ("\n\nRight now it covers frames %d to %d, and holds its "
                      "first and last frame outside that."
                      % (start, start + (span[1] - span[0])))
        return (
            "The timeline frame this reference STARTS on. Type a frame to "
            "move it there; Sync is the same thing aimed at the current frame."
            + covers
            + "\n\nHotkey Editor: animkitRefSlipBack / animkitRefSlipForward"
        )

    def _detail(self):
        """The tooltip: where it came from, and what Maya will actually play."""
        lines = [self.entry.source or self.entry.path]
        span = self.entry.frame_range()
        if span:
            lines.append("sequence %d-%d" % span)
        if not self.entry.loaded:
            lines.append("Maya has read no pixels from this file.")
        camera = self.entry.camera
        if camera:
            lines.append("pinned to camera %s" % camera)
        else:
            lines.append("free -- move, rotate and scale it like any object")
        return "\n".join(lines)

    # --- actions ---

    def _open_chunk(self):
        self._close_chunk()
        self._chunk = undo.LazyChunk("animkit: reference opacity")
        self._chunk.open()

    def _close_chunk(self):
        if self._chunk is not None:
            try:
                self._chunk.close()
            finally:
                self._chunk = None

    def _on_opacity(self, value):
        try:
            # Guarded for the whole write: without it the scene watch fires on
            # this very setAttr and rebuilds the row -- which deletes the
            # slider the animator still has hold of, mid-drag.
            with _Applying():
                reference.set_opacity(value / 100.0, nodes=[self.entry])
        except Exception:
            log.exception("animkit: could not set reference opacity")
            self._close_chunk()

    def _on_offset(self, value):
        try:
            with _Applying():
                reference.set_start(value, nodes=[self.entry])
        except Exception:
            log.exception("animkit: could not set the reference start frame")

    def _on_eye(self):
        reference.set_visible(self._eye.isChecked(), nodes=[self.entry])
        self.changed.emit()

    def _on_pin(self):
        reference.pin(nodes=[self.entry])
        self.changed.emit()

    def _on_select(self):
        node = self.entry.transform
        if cmds.objExists(node):
            cmds.select(node)
        self.changed.emit()

    def _on_remove(self):
        reference.remove(nodes=[self.entry])
        self.changed.emit()

    def hideEvent(self, event):
        """A panel closed mid-drag must not leave an undo chunk open.

        An open chunk swallows every scene edit that follows it, so the
        animator's next hour of work becomes one undo entry. This is cheap
        insurance against a code path that never reaches sliderReleased.
        """
        self._close_chunk()
        super(ReferenceRow, self).hideEvent(event)


class AudioRow(QtWidgets.QFrame):
    """The controls for one sound.

    Deliberately not the same widget as a reference row. A sound has no
    opacity, no camera and no orientation, and the one thing it has that a
    reference does not -- whether it is the clip the time slider is actually
    playing -- has no equivalent on the other side. Sharing a row would mean
    half the controls disabled on each.
    """

    changed = QtCore.Signal()

    def __init__(self, clip, parent=None):
        super(AudioRow, self).__init__(parent)
        self.clip = clip
        self.setObjectName("animkitCard")
        self._build_ui()

    def _build_ui(self):
        column = QtWidgets.QVBoxLayout(self)
        column.setContentsMargins(
            style.px(5), style.px(4), style.px(5), style.px(4)
        )
        column.setSpacing(style.px(3))

        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(style.px(4))

        live = self.clip.active
        self._live = QtWidgets.QPushButton("On" if live else "Off")
        self._live.setCheckable(True)
        self._live.setChecked(live)
        self._live.setFixedSize(style.px(30), style.px(style.ROW_HEIGHT))
        self._live.setToolTip(
            "Whether this is the sound on the time slider.\n\n"
            "Maya plays ONE at a time, so pressing this swaps rather than "
            "adds -- which is why a second sound you dropped is silent."
        )
        self._live.clicked.connect(self._on_live)
        top.addWidget(self._live)

        label = QtWidgets.QLabel(self.clip.label)
        label.setToolTip(self._detail())
        label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        top.addWidget(label, 1)

        self._mute = QtWidgets.QPushButton(
            "Muted" if self.clip.muted else "Mute")
        self._mute.setFixedHeight(style.px(style.ROW_HEIGHT))
        self._mute.setToolTip(
            "Silence this sound without removing it.\n\n"
            "Hotkey Editor: animkitAudioMute"
        )
        self._mute.clicked.connect(self._on_mute)
        top.addWidget(self._mute)

        select = QtWidgets.QPushButton("Sel")
        select.setFixedHeight(style.px(style.ROW_HEIGHT))
        select.setToolTip("Select the audio node.")
        select.clicked.connect(self._on_select)
        top.addWidget(select)

        remove = QtWidgets.QPushButton("X")
        remove.setFixedHeight(style.px(style.ROW_HEIGHT))
        remove.setFixedWidth(style.px(22))
        remove.setProperty("animkitDanger", True)
        remove.setToolTip(
            "Delete this sound.\n\nHotkey Editor: animkitAudioRemove")
        remove.clicked.connect(self._on_remove)
        top.addWidget(remove)
        column.addLayout(top)

        bottom = QtWidgets.QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(style.px(4))
        caption = QtWidgets.QLabel("starts at")
        caption.setObjectName("animkitHint")
        bottom.addWidget(caption)

        self._start = QtWidgets.QSpinBox()
        self._start.setRange(-100000, 100000)
        self._start.setValue(self.clip.starts_at)
        self._start.setFixedWidth(style.px(60))
        self._start.setToolTip(
            "The timeline frame this sound starts on.\n\n"
            "Hotkey Editor: animkitAudioSlipBack / animkitAudioSlipForward"
        )
        self._start.valueChanged.connect(self._on_start)
        bottom.addWidget(self._start)
        bottom.addStretch(1)

        length = self.clip.length
        if length:
            span = style.hint("%d frames" % length)
            bottom.addWidget(span)
        column.addLayout(bottom)

    def _detail(self):
        parts = [self.clip.source or self.clip.path]
        length = self.clip.length
        if length:
            parts.append("frames %d-%d at the current scene rate"
                         % (self.clip.starts_at,
                            self.clip.starts_at + length))
        played = self.clip.path
        if played and played != self.clip.source:
            parts.append("converted to wav: %s" % played)
        return "\n".join(parts)

    def _on_live(self):
        try:
            with _Applying():
                audio.activate(self.clip if self._live.isChecked() else None)
        except Exception:
            log.exception("animkit: could not change the time slider sound")
        self.changed.emit()

    def _on_mute(self):
        try:
            with _Applying():
                audio.set_muted(not self.clip.muted, nodes=[self.clip])
        except Exception:
            log.exception("animkit: could not mute the sound")
        self.changed.emit()

    def _on_start(self, value):
        try:
            with _Applying():
                audio.set_start(value, nodes=[self.clip])
        except Exception:
            log.exception("animkit: could not set the sound start frame")

    def _on_select(self):
        try:
            cmds.select(self.clip.node, replace=True)
        except Exception:
            log.exception("animkit: could not select the sound")

    def _on_remove(self):
        try:
            with _Applying():
                audio.remove(nodes=[self.clip])
        except Exception:
            log.exception("animkit: could not remove the sound")
        self.changed.emit()


# --- the panel --------------------------------------------------------------


class ReferenceWidget(QtWidgets.QWidget):
    """Drop zone, viewport-drop switch, and the references that are up."""

    def __init__(self, parent=None):
        super(ReferenceWidget, self).__init__(parent)
        self._rows = QtWidgets.QVBoxLayout()
        self._callbacks = []
        self._attribute_callbacks = []
        self._pending = False
        # Before the UI is built, because whether the watch took decides
        # whether a Refresh button is built at all.
        self._live = self._watch_scene()
        self._build_ui()
        self._wire_viewport_drop()
        self.refresh()

    # --- staying in step with the scene ---

    def _watch_scene(self):
        """Refresh when the SCENE changes, not only when a button is pressed.

        Deleting a plane in the Outliner, undoing a drop, or opening another
        shot all left this panel showing rows for references that no longer
        exist -- and pressing Remove on one of those is an operation aimed at a
        node that is gone. Refresh had to be pressed to find out.

        Node callbacks rather than a scriptJob on `idle`: these fire only for
        imagePlane nodes, so the cost is nothing until an image plane is
        actually made or destroyed, and `idle` would re-read the scene forever
        for a panel that mostly sits still.
        """
        try:
            import maya.api.OpenMaya as om
        except Exception:
            log.debug("animkit: no API to watch the scene with", exc_info=True)
            return False

        def changed(*_args):
            self._queue_refresh()

        self._on_attribute = changed

        try:
            self._callbacks = [
                om.MDGMessage.addNodeAddedCallback(changed, "imagePlane"),
                om.MDGMessage.addNodeRemovedCallback(changed, "imagePlane"),
                om.MDGMessage.addNodeAddedCallback(changed, "audio"),
                om.MDGMessage.addNodeRemovedCallback(changed, "audio"),
                om.MSceneMessage.addCallback(
                    om.MSceneMessage.kAfterOpen, changed),
                om.MSceneMessage.addCallback(
                    om.MSceneMessage.kAfterNew, changed),
                # Undo and redo change ATTRIBUTES without adding or removing a
                # node, so the node callbacks above never fire for them. That
                # is what made Ctrl+Z on an offset look like it had done
                # nothing: the scene had gone back, and only the spinbox still
                # showed the new value.
                om.MEventMessage.addEventCallback("Undo", changed),
                om.MEventMessage.addEventCallback("Redo", changed),
            ]
        except Exception:
            log.exception("animkit: could not watch the scene for references")
            self._unwatch_scene()
            return False
        return True

    def _watch_attributes(self, nodes):
        """Follow the ATTRIBUTES the rows display, on the shapes now up.

        Node callbacks catch a reference appearing or being deleted; they say
        nothing about a hotkey slipping one, a Channel Box edit, or a script
        fading one. Without this the panel is only ever as current as the last
        press of Refresh, which is what made Refresh feel compulsory.

        Re-registered on every refresh because the set of shapes changes, and
        filtered HARD: `frameExtension` is driven by time and changes on every
        frame of playback, so an unfiltered callback here would rebuild the
        whole panel sixty times a second the moment anybody hit play.
        """
        try:
            import maya.api.OpenMaya as om
        except Exception:
            return

        # `offset` and `mute` are the audio node's half of this list. A sound
        # has no frameExtension, so nothing here is driven by time and none of
        # it fires during playback.
        watched = ("frameOffset", "alphaGain", "visibility", "offset", "mute")

        def attribute_changed(message, plug, _other, _data):
            if _APPLYING:
                return
            # kAttributeSet covers a setAttr; kAttributeEval and the rest are
            # noise for our purposes and arrive constantly during playback.
            if not (message & om.MNodeMessage.kAttributeSet):
                return
            try:
                name = plug.partialName(useLongNames=True)
            except Exception:
                return
            if name in watched:
                self._queue_refresh()

        for name in nodes:
            try:
                selection = om.MSelectionList()
                selection.add(name)
                node = selection.getDependNode(0)
                self._attribute_callbacks.append(
                    om.MNodeMessage.addAttributeChangedCallback(
                        node, attribute_changed)
                )
            except Exception:
                log.debug("animkit: could not watch %r", name, exc_info=True)

    def _unwatch_attributes(self):
        try:
            import maya.api.OpenMaya as om
        except Exception:
            self._attribute_callbacks = []
            return
        for identifier in self._attribute_callbacks:
            try:
                om.MMessage.removeCallback(identifier)
            except Exception:
                log.debug("animkit: could not remove an attribute callback",
                          exc_info=True)
        self._attribute_callbacks = []

    def _queue_refresh(self):
        """Refresh once, after Maya has finished what it is doing.

        A node-removed callback runs DURING the delete, with the node half
        gone: reading the scene there gives a list that includes something that
        is about to stop existing, and rebuilding Qt widgets inside a DG
        callback is its own kind of trouble. Deferring moves the work to the
        idle queue, where the scene is whole again.

        The flag collapses a burst into one refresh. `Remove` on three
        references fires three callbacks, and three full scene reads to draw
        the same three rows is work nobody asked for.
        """
        if self._pending:
            return
        self._pending = True

        def run():
            self._pending = False
            if not self._alive():
                return
            try:
                self.refresh()
            except Exception:
                log.exception("animkit: could not refresh the reference panel")

        try:
            cmds.evalDeferred(run, lowestPriority=True)
        except Exception:
            self._pending = False
            log.debug("animkit: could not defer a refresh", exc_info=True)

    def _alive(self):
        """False once Maya has deleted the C++ side out from under us.

        The callbacks outlive the widget unless something removes them, and a
        callback that touches a dead wrapper takes Maya down rather than
        raising. Checking here AND unhooking in `closeEvent` because either one
        alone has a gap: the check cannot fire if the callback list is already
        being walked, and the close event does not always arrive.
        """
        try:
            self.objectName()
            return True
        except Exception:
            return False

    def _unwatch_scene(self):
        self._unwatch_attributes()
        try:
            import maya.api.OpenMaya as om
        except Exception:
            self._callbacks = []
            return
        for identifier in self._callbacks:
            try:
                om.MMessage.removeCallback(identifier)
            except Exception:
                log.debug("animkit: could not remove a scene callback",
                          exc_info=True)
        self._callbacks = []

    def closeEvent(self, event):
        self._unwatch_scene()
        super(ReferenceWidget, self).closeEvent(event)

    # --- construction ---

    def _build_ui(self):
        column = QtWidgets.QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(style.px(5))

        self._zone = DropZone()
        self._zone.dropped.connect(self._on_dropped)
        column.addWidget(self._zone)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(style.px(style.GUTTER))

        load = QtWidgets.QPushButton("Load...")
        load.setFixedHeight(style.px(style.ROW_HEIGHT))
        load.setToolTip(
            "Browse for a video or an image sequence.\n\n"
            "Hotkey Editor: animkitRefLoad"
        )
        load.clicked.connect(self._on_load)
        buttons.addWidget(load, 1)

        sync = QtWidgets.QPushButton("Sync")
        sync.setFixedHeight(style.px(style.ROW_HEIGHT))
        sync.setToolTip(
            "Move the reference in TIME so its first frame lands on the frame "
            "you are sitting on now.\n\n"
            "It does not reload anything -- it only sets the offset. Sync at "
            "frame 100 means the reference now starts at 100, so it covers "
            "frames 100 onwards and holds its first frame before that.\n\n"
            "Hotkey Editor: animkitRefSync"
        )
        sync.clicked.connect(self._on_sync)
        buttons.addWidget(sync)

        arrange = QtWidgets.QPushButton("Arrange")
        arrange.setFixedHeight(style.px(style.ROW_HEIGHT))
        arrange.setToolTip(
            "Lay every free reference out side by side, facing the view you "
            "are looking through now.\n\n"
            "New drops already tuck in beside the ones already up. This is "
            "for tidying a set that has drifted, for re-aiming them after you "
            "have orbited, and for references loaded by an older animkit.\n\n"
            "Hotkey Editor: animkitRefArrange"
        )
        arrange.clicked.connect(self._on_arrange)
        buttons.addWidget(arrange)

        # NO Refresh button when the panel is following the scene, which is the
        # normal case. A control whose only job is "make what you are looking
        # at be true" is an admission that it might not be, and every animator
        # who saw it reasonably asked what it was for. It is built only when
        # the watch could not be installed -- and then it is the only way to
        # see the scene, and it says so.
        if not self._live:
            refresh = QtWidgets.QPushButton("Refresh")
            refresh.setFixedHeight(style.px(style.ROW_HEIGHT))
            refresh.setToolTip(
                "Re-read the scene and rebuild this list.\n\n"
                "This button is here because animkit could not watch the "
                "scene in this Maya, so the panel cannot follow changes on "
                "its own. See the Script Editor for why."
            )
            refresh.clicked.connect(self.refresh)
            buttons.addWidget(refresh)
        column.addLayout(buttons)

        mode = QtWidgets.QHBoxLayout()
        mode.setContentsMargins(0, 0, 0, 0)
        mode.setSpacing(style.px(4))
        caption = QtWidgets.QLabel("new drops:")
        caption.setObjectName("animkitHint")
        mode.addWidget(caption)

        # Three ways a reference can sit, in one control. Upright and angled
        # are both FREE -- they differ only in whether the board takes the
        # camera's pitch and roll as well as its yaw -- so a second combo for
        # a choice that is meaningless when pinned would be a worse control.
        self._attach = QtWidgets.QComboBox()
        self._attach.addItem("free -- front, no rotation",
                             (reference.ATTACH_FREE, reference.ORIENT_FRONT))
        self._attach.addItem("free -- upright, turned to view",
                             (reference.ATTACH_FREE, reference.ORIENT_UPRIGHT))
        self._attach.addItem("free -- angled to view",
                             (reference.ATTACH_FREE, reference.ORIENT_VIEW))
        self._attach.addItem("pinned to camera",
                             (reference.ATTACH_CAMERA, reference.ORIENT_FRONT))
        self._attach.setFixedHeight(style.px(style.ROW_HEIGHT))
        self._attach.setToolTip(
            "Front puts the board at rotate (0,0,0), square-on in the front "
            "view -- the same as Maya's own Create > Free Image Plane, and "
            "the same every time whatever the viewport is pointing at. It is "
            "an ordinary object: the move, rotate and scale tools work on "
            "it.\n\n"
            "Upright stands it vertically but turns it to face the view you "
            "load from. Through the default persp that is rotate (0, 45, 0), "
            "which leaves the picture squashed to 71% of its width in the "
            "front view.\n\n"
            "Angled to view matches the camera exactly, pitch and roll "
            "included, so it leans. Right for matching one specific camera "
            "angle, awkward everywhere else.\n\n"
            "Pinned rides the camera and always fills the frame, but cannot "
            "be grabbed in the viewport.\n\n"
            "This is remembered, and only affects the NEXT drop -- use Pin on "
            "a row to convert one that is already up, and Arrange to re-stand "
            "the ones you have."
        )
        self._attach.setCurrentIndex(self._attach_index())
        self._attach.currentIndexChanged.connect(self._on_attach_changed)
        mode.addWidget(self._attach, 1)
        column.addLayout(mode)

        column.addLayout(self._start_row())
        column.addLayout(self._detail_row())

        self._viewport = QtWidgets.QCheckBox(
            "Drag files onto the 3D view or the timeline")
        self._viewport.setToolTip(
            "On, you never need this panel to load anything: drag straight "
            "out of Explorer.\n\n"
            "Onto a 3D VIEW puts a video or image on that view's camera. "
            "Several at once land side by side.\n\n"
            "Onto the TIMELINE starts it on the frame you dropped it at, "
            "which is how a dialogue track gets lined up with a shot in one "
            "gesture.\n\n"
            "Switch it off if another tool needs the drop instead. The zone "
            "above keeps working either way."
        )
        self._viewport.stateChanged.connect(self._on_viewport_toggled)
        column.addWidget(self._viewport)

        self._rows.setSpacing(style.px(3))
        column.addLayout(self._rows)

        cache = QtWidgets.QHBoxLayout()
        cache.setContentsMargins(0, 0, 0, 0)
        cache.setSpacing(style.px(4))
        self._cache = style.hint("")
        cache.addWidget(self._cache, 1)

        clear = QtWidgets.QPushButton("Clear")
        clear.setFixedHeight(style.px(20))
        clear.setFixedWidth(style.px(44))
        clear.setProperty("animkitDanger", True)
        clear.setToolTip(
            "Delete every converted video sequence.\n\n"
            "A reference already up will go blank -- drop the video again to "
            "rebuild it.\n\nHotkey Editor: animkitRefClearCache"
        )
        clear.clicked.connect(self._on_clear_cache)
        cache.addWidget(clear)
        column.addLayout(cache)

        self._hint = style.hint("")
        column.addWidget(self._hint)
        column.addStretch(1)

    def _start_row(self):
        """Where a new reference lands in TIME. The animator's call, not ours.

        This existed as a hard-coded "the frame you are on" and read as the
        tool moving the reference for reasons of its own -- the only visible
        trace being an offset like -58.
        """
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(style.px(4))
        caption = QtWidgets.QLabel("drops start:")
        caption.setObjectName("animkitHint")
        row.addWidget(caption)

        self._start = QtWidgets.QComboBox()
        self._start.addItem("at the start of the range",
                            reference.START_RANGE)
        self._start.addItem("at the current frame", reference.START_CURRENT)
        self._start.addItem("on its own frame numbers",
                            reference.START_NATIVE)
        self._start.setFixedHeight(style.px(style.ROW_HEIGHT))
        self._start.setToolTip(self._start_row_tip())

        current = settings.get("reference.start_at")
        index = self._start.findData(current)
        self._start.setCurrentIndex(index if index >= 0 else 0)
        self._start.currentIndexChanged.connect(self._on_start_changed)
        row.addWidget(self._start, 1)
        return row

    def _start_row_tip(self):
        return (
            "Which timeline frame a newly dropped reference starts on.\n\n"
            "Start of the range lines it up with the shot and is the same "
            "every time.\n\n"
            "Current frame was the old behaviour: drop while parked on frame "
            "59 and the reference starts at 59. Useful when you are placing "
            "reference for one specific action, surprising the rest of the "
            "time.\n\n"
            "Its own frame numbers applies no offset at all, so a render "
            "numbered 101-200 sits at 101-200.\n\n"
            "Only affects the NEXT drop. Change one already up in its "
            "'starts at' box, or press Sync."
        )

    def _detail_row(self):
        """How much of a video survives conversion. The blur control.

        Here rather than buried in settings.json because it is the one setting
        an animator discovers by being bitten. 720 is right for a performance
        reference read behind a rig at 30% opacity, and wrong for a screen
        recording, where the downscale plus 4:2:0 JPEG turns small type into
        mush -- and the person who needs native is looking at the mush when
        they need it.

        Only the NEXT conversion changes. The cache is keyed on this, so a clip
        already converted stays as it is until it is dropped again, and the old
        copy stays on disk until Clear.
        """
        from animkit.core import transcode

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(style.px(4))
        caption = QtWidgets.QLabel("video detail:")
        caption.setObjectName("animkitHint")
        row.addWidget(caption)

        self._height = QtWidgets.QComboBox()
        self._height.addItem("720p -- small cache", (720, "jpg"))
        self._height.addItem("1080p", (1080, "jpg"))
        self._height.addItem("native -- sharpest", (transcode.NATIVE, "jpg"))
        self._height.addItem("native, lossless png -- biggest",
                             (transcode.NATIVE, "png"))
        self._height.setFixedHeight(style.px(style.ROW_HEIGHT))
        self._height.setToolTip(
            "How much of a dropped VIDEO is kept when it is converted.\n\n"
            "720p is plenty for acting reference and keeps the cache small. "
            "Use native for a screen recording or anything with text in it -- "
            "at 720p the type is resampled away, and scaling the plane up in "
            "the viewport cannot bring back pixels that were never written."
            "\n\n"
            "Lossless png is the last step and it is a small one: jpg here is "
            "already full-chroma and measures 0.996 SSIM against the source. "
            "It roughly doubles the cache and adds about a third to the "
            "conversion time.\n\n"
            "Sharper costs time and disk -- they are the same dial. Measured "
            "on a 1080p60 clip, per 30 seconds of video: 720p 157MB in 4.2s, "
            "native 211MB in 3.6s, native png 436MB in 4.8s.\n\n"
            "Affects the next conversion. Re-drop a video to rebuild it, then "
            "press Clear to reclaim the old copy."
        )

        current = (transcode.normalise_height(
                       settings.get("reference.convert_height")),
                   settings.get("reference.convert_format"))
        index = self._height.findData(current)
        if index < 0:
            # A combination hand-edited into settings.json. Shown as itself
            # rather than snapped to one of ours: a combo that silently
            # disagrees with the file is worse than an extra entry.
            self._height.addItem(
                "%s %s" % ("native" if not current[0] else "%dp" % current[0],
                           current[1]), current)
            index = self._height.count() - 1
        self._height.setCurrentIndex(index)
        self._height.currentIndexChanged.connect(self._on_height_changed)
        row.addWidget(self._height, 1)
        return row

    def _wire_viewport_drop(self):
        """Honour the saved setting, and show what actually happened.

        The checkbox reports whether panels are hooked RIGHT NOW rather than
        what the setting says, because a setting that says yes and a Maya that
        would not hook is exactly the case worth seeing.
        """
        from animkit.ui import viewport_drop

        try:
            viewport_drop.install_if_wanted()
            active = viewport_drop.is_installed()
        except Exception:
            log.exception("animkit: could not wire viewport drop")
            active = False

        self._viewport.blockSignals(True)
        self._viewport.setChecked(active)
        self._viewport.blockSignals(False)

    # --- state ---

    def refresh(self):
        _clear_layout(self._rows)
        # Before reading, so a node deleted since the last refresh cannot be
        # left with a live callback pointing at it.
        self._unwatch_attributes()

        try:
            found = reference.references()
        except Exception:
            log.exception("animkit: could not read the scene's references")
            self._hint.setText("Could not read the scene -- see the Script "
                               "Editor.")
            return

        for entry in found:
            row = ReferenceRow(entry)
            row.changed.connect(self.refresh)
            self._rows.addWidget(row)

        try:
            sounds = audio.clips()
        except Exception:
            log.exception("animkit: could not read the scene's sounds")
            sounds = []
        for clip in sounds:
            row = AudioRow(clip)
            row.changed.connect(self.refresh)
            self._rows.addWidget(row)

        self._watch_attributes([entry.shape for entry in found]
                               + [clip.node for clip in sounds])
        self._hint.setText(self._hint_text(found, sounds))
        self._cache.setText(self._cache_text())

    def _cache_text(self):
        """What the converted-video cache is costing, in words.

        Shown rather than hidden because it is the one thing this feature adds
        to an animator's disk without asking, and a number they can see is a
        number they can decide about.
        """
        from animkit.core import transcode

        if not transcode.is_available(settings.get("reference.ffmpeg") or None):
            return ("No ffmpeg found, so a dropped video goes to Maya as a "
                    "movie -- which on this platform usually draws nothing.")
        folders, size = transcode.cache_size()
        if not folders:
            return "No converted video cached yet."
        return "Converted video cache: %d clip(s), %.0f MB." % (
            folders, size / (1024.0 * 1024.0))

    def _on_clear_cache(self):
        from animkit.core import transcode

        gone = transcode.clear_cache()
        if gone:
            cmds.warning(
                "animkit: cleared %d converted clip(s). Any reference still "
                "up will draw nothing until you drop the video again." % gone
            )
        else:
            cmds.warning("animkit: nothing cached to clear")
        self.refresh()

    def _hint_text(self, found, sounds=()):
        if sounds and not found:
            silent = [clip for clip in sounds if not clip.active]
            if silent and len(sounds) > 1:
                return (
                    "%d sound(s). Maya plays ONE at a time -- press On beside "
                    "another to swap which is on the timeline."
                    % len(sounds)
                )
            return (
                "%d sound(s) on the timeline. Turn the speaker on in the time "
                "slider if you cannot hear it, and set the start frame above."
                % len(sounds)
            )
        if not found:
            return (
                "Nothing up yet. Drop a video or an image sequence above, or "
                "onto a viewport. A reference lands on the frame you are "
                "sitting on, so scrub to where it should start first."
            )
        blank = [entry for entry in found if not entry.loaded]
        if blank:
            return (
                "%d reference(s). %s has no pixels -- this Maya has no decoder "
                "for it. Convert it to an image sequence; that scrubs better "
                "than a movie anyway." % (len(found), blank[0].label)
            )
        if any(entry.is_free for entry in found):
            return (
                "%d reference(s). A free one is an ordinary object -- press "
                "Sel, then use the move, rotate and scale tools on it. Press "
                "Frame if you lose it off screen." % len(found)
            )
        return (
            "%d reference(s). animkitRefSlipBack / animkitRefSlipForward are "
            "worth a hotkey each -- lining reference up with animation is "
            "most of what this tab is for." % len(found)
        )

    # --- actions ---

    def _on_dropped(self, paths):
        try:
            with _Applying():
                reference.drop(list(paths))
        except Exception:
            log.exception("animkit: could not load the dropped files")
            cmds.warning("animkit: could not load that -- see the Script "
                         "Editor")
        self.refresh()

    def _on_load(self):
        with _Applying():
            reference.load_prompt()
        self.refresh()

    def _on_sync(self):
        with _Applying():
            reference.sync_to_current()
        self.refresh()

    def _on_arrange(self):
        with _Applying():
            reference.arrange()
        self.refresh()

    def _attach_index(self):
        """Which entry the saved settings mean.

        Orientation is meaningless once pinned, so a pinned setting matches on
        attachment alone -- otherwise a saved (camera, "upright") pair would
        fall through to the default and the combo would disagree with the file.
        """
        attach = settings.get("reference.attach")
        orient = settings.get("reference.orient")
        for index in range(self._attach.count()):
            entry_attach, entry_orient = self._attach.itemData(index)
            if entry_attach != attach:
                continue
            if attach == reference.ATTACH_CAMERA or entry_orient == orient:
                return index
        return 0

    def _on_attach_changed(self, _index):
        chosen = self._attach.currentData()
        if not chosen:
            return
        attach, orient = chosen
        settings.set("reference.attach", attach)
        # Left alone when pinning, so switching to pinned and back does not
        # quietly forget which kind of free the animator had chosen.
        if attach != reference.ATTACH_CAMERA:
            settings.set("reference.orient", orient)

    def _on_start_changed(self, _index):
        chosen = self._start.currentData()
        if chosen:
            settings.set("reference.start_at", chosen)

    def _on_height_changed(self, _index):
        # `if not chosen:` would be wrong here and quietly so -- the tuple for
        # native is (0, "jpg"), and 0 is the entry an animator changes this for.
        chosen = self._height.currentData()
        if chosen is None:
            return
        height, image_format = chosen
        settings.set("reference.convert_height", int(height))
        settings.set("reference.convert_format", str(image_format))

    def _on_viewport_toggled(self, _state):
        from animkit.ui import viewport_drop

        wanted = self._viewport.isChecked()
        try:
            viewport_drop.set_enabled(wanted)
        except Exception:
            log.exception("animkit: could not change viewport drop")

        active = viewport_drop.is_installed()
        if wanted and not active:
            # Say so rather than leaving a ticked box that does nothing.
            cmds.warning(
                "animkit: no viewport could be hooked for drops. Open a 3D "
                "view and tick this again -- the drop zone in this tab works "
                "regardless."
            )
            self._viewport.blockSignals(True)
            self._viewport.setChecked(False)
            self._viewport.blockSignals(False)
            settings.set("reference.viewport_drop", False)


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


# --- entry points -----------------------------------------------------------


def build():
    """Fill the current workspaceControl. Named by BUILD_CODE / uiScript.

    Must work from a cold interpreter -- Maya restores docked panels on startup
    by executing this string before anything else has run. See animkit.ui.mayawin.
    """
    try:
        import animkit

        animkit.startup()

        parent = mayawin.current_parent()
        if parent is None:
            log.error("animkit: reference_ui.build() called with no parent")
            return None

        if parent.layout() is None:
            QtWidgets.QVBoxLayout(parent)
        mayawin.clear_children(parent)

        widget = ReferenceWidget(parent)
        style.apply_to(widget, style.accent_for("Ref"))
        parent.layout().setContentsMargins(0, 0, 0, 0)
        parent.layout().addWidget(widget)

        parent.setProperty(mayawin.BUILT_PROPERTY, True)
        return widget
    except Exception:
        import traceback

        traceback.print_exc()
        print("animkit: reference_ui.build() failed -- panel will be empty")
        return None


def show():
    """Open the dockable reference panel."""
    return mayawin.show_workspace_control(
        CONTROL_NAME, "Reference", BUILD_CODE, width=style.px(320)
    )


def reset():
    """Delete the panel and its saved state so show() rebuilds from scratch."""
    return mayawin.delete_workspace_control(CONTROL_NAME)

"""Qt binding shim for Maya 2022-2026.

    Maya 2022-2024 -> PySide2 / shiboken2 / Qt5
    Maya 2025-2026 -> PySide6 / shiboken6 / Qt6

Import Qt through this module only. Never `from PySide2 import ...` anywhere
else in the codebase -- that is the single change that makes a 2024-only tool
crash on 2026.

Consider swapping this for Qt.py (github.com/mottosson/Qt.py) if you end up
needing broad coverage; this file covers the differences that actually bite
in Maya tools and nothing more.
"""

QT_BINDING = None

try:
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401
    from shiboken6 import wrapInstance, getCppPointer  # noqa: F401

    QT_BINDING = "PySide6"
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets  # noqa: F401
    from shiboken2 import wrapInstance, getCppPointer  # noqa: F401

    QT_BINDING = "PySide2"

IS_QT6 = QT_BINDING == "PySide6"


# --- Differences that actually break code -----------------------------------

# QAction moved QtWidgets -> QtGui in Qt6.
QAction = QtGui.QAction if IS_QT6 else QtWidgets.QAction
QActionGroup = QtGui.QActionGroup if IS_QT6 else QtWidgets.QActionGroup

# QRegExp was removed in Qt6.
QRegularExpression = QtCore.QRegularExpression


def exec_dialog(dialog):
    """QDialog.exec_() was renamed exec() in Qt6."""
    return dialog.exec() if IS_QT6 else dialog.exec_()


def exec_popup(widget, *args):
    """Same rename, for QMenu and anything else that takes positional args.

    Separate from exec_dialog because QMenu.exec_ takes a position and
    QDialog.exec_ takes nothing, and collapsing them into one signature just
    moves the confusion somewhere less obvious.
    """
    return widget.exec(*args) if IS_QT6 else widget.exec_(*args)


def event_pos(event):
    """QMouseEvent.pos() is deprecated in Qt6 in favour of position().

    Returns a QPoint in widget-local coordinates on both bindings.
    """
    if IS_QT6:
        return event.position().toPoint()
    return event.pos()


def event_global_pos(event):
    if IS_QT6:
        return event.globalPosition().toPoint()
    return event.globalPos()


def as_qwidget(pointer, cls=None):
    """Wrap a Maya MQtUtil pointer as a Python QWidget.

    MQtUtil returns an int-able pointer; `long` no longer exists in Py3.
    """
    if pointer is None:
        return None
    return wrapInstance(int(pointer), cls or QtWidgets.QWidget)

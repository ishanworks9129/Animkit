"""The one place colours, metrics and stylesheets are defined.

Every panel reads from here. Scattering `setStyleSheet("color: rgb(210,140,140)")`
through the widgets is how a tool ends up with four slightly different reds and
no way to change any of them -- which is exactly where this codebase was before
this module existed.

DENSITY IS THE POINT
--------------------
An animation tool panel is docked in a strip beside a viewport, and it competes
for width with the thing the animator is actually looking at. So the metrics
here are tighter than a general Qt app would use: 20px rows, 2px gutters,
icon-first buttons with the label in the tooltip. That is not decoration, it is
the difference between a panel that stays open and one that gets closed.

SCALING IS NOT OPTIONAL
-----------------------
Every metric goes through `px()`, which multiplies by the screen's device pixel
ratio. Hardcoded pixel sizes are correct on exactly one monitor: the panel that
looks right on a 1080p laptop has 11px buttons on a 4K display, and the animator
concludes the tool is broken rather than that it is unscaled.
"""

import logging

from animkit.vendor import qt

QtCore = qt.QtCore
QtGui = qt.QtGui
QtWidgets = qt.QtWidgets

log = logging.getLogger(__name__)


# --- palette ----------------------------------------------------------------

#: Base surfaces, darkest to lightest. Deliberately darker than Maya's own grey
#: so the panel reads as a tool rather than as part of the viewport chrome.
BACKGROUND = "#2b2b2b"
SURFACE = "#333335"
SURFACE_RAISED = "#3d3d40"

BUTTON = "#454548"
BUTTON_HOVER = "#55555a"
BUTTON_DOWN = "#2d2d30"

BORDER = "#232325"
SEPARATOR = "#3a3a3d"

TEXT = "#d6d6d8"
TEXT_DIM = "#8b8b90"
TEXT_ON_ACCENT = "#ffffff"

#: One accent per operation group. A group's colour appears on its header rule,
#: on its icons, and on the hover state of its buttons -- so a panel scanned at
#: a glance separates into bands without needing to be read.
ACCENTS = {
    "Timing": "#e0a458",
    "Tangents": "#5b9bd5",
    "Cycle": "#6bbf73",
    "Edit": "#c96565",
    "Pose": "#a684d0",
    "Sets": "#4fb3a8",
    "Tween": "#5b9bd5",
    "Ref": "#d2854f",
}
ACCENT_DEFAULT = "#5b9bd5"

#: Destructive operations. Not a confirmation dialog -- a dialog on a button an
#: animator presses fifty times an hour gets clicked through blind, and every
#: operation here is one undo step anyway. Colour is the whole warning.
DANGER = "#c96565"
DANGER_HOVER = "#d87878"


def accent_for(group):
    return ACCENTS.get(group, ACCENT_DEFAULT)


def colour(value):
    """A QColor from any of the strings above."""
    return QtGui.QColor(value)


# --- metrics ----------------------------------------------------------------

#: Logical sizes, before DPI scaling. Change them here, not in a widget.
ROW_HEIGHT = 22
ICON_BUTTON = 26
ICON_SIZE = 16
GUTTER = 2
MARGIN = 6
GROUP_GAP = 8
RADIUS = 3


def scale():
    """The screen's device pixel ratio, or 1.0 when there is no screen.

    Read at call time rather than cached. An animator drags a floating panel
    from a laptop screen to a 4K monitor mid-session, and a ratio captured at
    import is wrong from that moment on.
    """
    try:
        app = QtWidgets.QApplication.instance()
        if app is None:
            return 1.0
        screen = app.primaryScreen()
        if screen is None:
            return 1.0
        return float(screen.devicePixelRatio()) or 1.0
    except Exception:
        log.debug("animkit: could not read the device pixel ratio",
                  exc_info=True)
        return 1.0


def px(value):
    """A logical size in real pixels for this screen."""
    return int(round(value * scale()))


# --- stylesheet -------------------------------------------------------------

_SHEET = """
QWidget#animkitRoot {{
    background: {background};
}}

QFrame#animkitCard {{
    background: {surface};
    border: 1px solid {border};
    border-radius: {radius}px;
}}

QLabel#animkitGroupTitle {{
    color: {text_dim};
    font-size: {small}px;
    font-weight: bold;
    letter-spacing: 1px;
    padding: 0px;
}}

QLabel#animkitHint {{
    color: {text_dim};
    font-size: {small}px;
}}

QLabel {{
    color: {text};
}}

QPushButton {{
    background: {button};
    color: {text};
    border: 1px solid {border};
    border-radius: {radius}px;
    padding: 1px {pad}px;
}}
QPushButton:hover {{
    background: {button_hover};
    border: 1px solid {accent};
}}
QPushButton:pressed {{
    background: {button_down};
}}
QPushButton:disabled {{
    color: {text_dim};
    background: {surface};
}}

QPushButton[animkitDanger="true"]:hover {{
    background: {danger};
    border: 1px solid {danger_hover};
    color: {text_on_accent};
}}

QPushButton:checked {{
    background: {accent};
    color: {text_on_accent};
}}

/* Sliders, spin boxes and check boxes arrived with the Ref tab. They are
   defined here rather than there for the same reason every other colour is:
   a stylesheet in a widget is how a tool ends up with two greys that are
   nearly the same and no way to change either. */
QSlider::groove:horizontal {{
    background: {background};
    border: 1px solid {border};
    border-radius: 2px;
    height: 4px;
}}
QSlider::sub-page:horizontal {{
    background: {accent};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {button_hover};
    border: 1px solid {border};
    border-radius: 2px;
    width: 8px;
    margin: -5px 0px;
}}
QSlider::handle:horizontal:hover {{
    background: {accent};
}}

QSpinBox, QDoubleSpinBox {{
    background: {background};
    color: {text};
    border: 1px solid {border};
    border-radius: {radius}px;
    padding: 1px 2px;
    selection-background-color: {accent};
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {accent};
}}
QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {text_dim};
    background: {surface};
}}

QCheckBox {{
    color: {text_dim};
    font-size: {small}px;
    spacing: {pad}px;
}}
QCheckBox::indicator {{
    width: 11px;
    height: 11px;
    border: 1px solid {border};
    border-radius: 2px;
    background: {background};
}}
QCheckBox::indicator:checked {{
    background: {accent};
}}

QLineEdit {{
    background: {background};
    color: {text};
    border: 1px solid {border};
    border-radius: {radius}px;
    padding: 1px {pad}px;
    selection-background-color: {accent};
}}
QLineEdit:focus {{
    border: 1px solid {accent};
}}

QTabWidget::pane {{
    border: 1px solid {border};
    border-radius: {radius}px;
    background: {surface};
    top: -1px;
}}
QTabBar::tab {{
    background: {background};
    color: {text_dim};
    border: 1px solid {border};
    border-bottom: none;
    border-top-left-radius: {radius}px;
    border-top-right-radius: {radius}px;
    padding: 3px {pad}px;
    margin-right: 1px;
}}
QTabBar::tab:selected {{
    background: {surface};
    color: {text};
}}
QTabBar::tab:hover {{
    color: {text};
}}

QToolTip {{
    background: {surface_raised};
    color: {text};
    border: 1px solid {border};
    padding: 3px;
}}

QScrollArea {{
    border: none;
    background: transparent;
}}
"""


def sheet(accent=ACCENT_DEFAULT):
    """The panel stylesheet, with one accent colour substituted through it."""
    return _SHEET.format(
        background=BACKGROUND,
        surface=SURFACE,
        surface_raised=SURFACE_RAISED,
        button=BUTTON,
        button_hover=BUTTON_HOVER,
        button_down=BUTTON_DOWN,
        border=BORDER,
        text=TEXT,
        text_dim=TEXT_DIM,
        text_on_accent=TEXT_ON_ACCENT,
        danger=DANGER,
        danger_hover=DANGER_HOVER,
        accent=accent,
        radius=RADIUS,
        pad=6,
        small=max(9, int(round(9 * 1.0))),
    )


#: The objectName every styled root carries, so the stylesheet has something to
#: select on.
#:
#: It MUST NOT equal any workspaceControl name. It used to be "animkitPanel",
#: which is also `panel.CONTROL_NAME`, and the collision was silent and nasty:
#: `MQtUtil.findControl("animkitPanel")` walks Maya's UI and returns the FIRST
#: widget with that objectName, which was the styled panel INSIDE the control
#: rather than the control itself. Everything that identifies a control by name
#: then answered about the wrong widget --
#:
#:   `mayawin.is_built()` read the built-marker off the inner widget, never
#:   found it, and reported a correctly built panel as empty. So every
#:   `panel.show()` logged "its uiScript did not populate it" and then deleted
#:   and rebuilt a panel that was fine.
#:
#:   `panel.show(tab=...)` did `findChildren(AnimkitPanel)` on what was already
#:   the AnimkitPanel, got nothing, and silently never switched tab.
#:
#: Only the main panel was affected, because it is the only one whose control
#: name happened to equal this string. `test_style_object_name_cannot_collide`
#: is what keeps a future control from being named into the same trap.
ROOT_OBJECT_NAME = "animkitRoot"


def apply_to(widget, accent=ACCENT_DEFAULT):
    """Style a panel and everything under it."""
    widget.setObjectName(ROOT_OBJECT_NAME)
    widget.setStyleSheet(sheet(accent))
    return widget


# --- small shared widgets ---------------------------------------------------


def group_title(text, group=""):
    """A group header: the name, then a rule in the group's accent colour."""
    box = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(px(6))

    label = QtWidgets.QLabel(text.upper())
    label.setObjectName("animkitGroupTitle")
    row.addWidget(label)

    rule = QtWidgets.QFrame()
    rule.setFrameShape(QtWidgets.QFrame.HLine)
    rule.setFixedHeight(1)
    rule.setStyleSheet("background: %s; border: none;" % accent_for(group))
    row.addWidget(rule, 1)

    return box


def hint(text):
    label = QtWidgets.QLabel(text)
    label.setObjectName("animkitHint")
    label.setWordWrap(True)
    return label

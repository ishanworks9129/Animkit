"""Video and image reference: drop a file on the viewport, get an image plane.

    from animkit.tools import reference
    reference.drop(["C:/ref/walk.0001.png"])   # what drag-and-drop calls
    reference.slip(-1)                          # nudge it a frame earlier
    reference.toggle()                          # reference off, look at the rig

An animator working from reference does four things all day: put it up, line it
up with the animation, fade it back to see the rig through it, and hide it. Each
of those is one operation here and one button in the panel, and every one of
them is a single undo step.

WHY AN IMAGE PLANE AND NOT A FLOATING VIDEO WINDOW
---------------------------------------------------
Because the reference has to be in the same space as the animation to be worth
anything. A window beside the viewport gets compared by eye across a screen; an
image plane at the back of the camera frustum is behind the character, at the
same scale, in the same frame, and it plays off the same time slider -- so
scrubbing scrubs both and there is nothing to keep in sync. It is also saved
in the scene, so tomorrow's session opens with the reference already up.

The cost is that it belongs to a camera, which is why a drop is aimed at the
viewport it landed on rather than at a global default.

RECOGNITION IS BY TAG, NEVER BY NAME
------------------------------------
An image plane is animkit's if it carries the `animkitReference` attribute.
Same rule as the rest of the codebase: a shot already has image planes the
layout department put there, matched to the shot camera, and a tool that
decided ownership from `imagePlane*` would happily delete one of those on
`Remove`. It also means an animator can rename the node in the Outliner and
nothing breaks.

The camera an image plane is attached to is likewise read from its `.message`
connection into `cameraShape.imagePlane[]` -- not from its parent path, which
for an image plane is Maya's under-world form (`|persp|perspShape->|imagePlane1`)
and not something to be parsing.

"DID IT ACTUALLY LOAD" HAS A REAL ANSWER
-----------------------------------------
`coverageX/coverageY` on the shape is -1 until Maya has read pixels, and the
image's true dimensions afterwards. So the loader does not have to trust the
extension, and it does not have to guess: it sets the file, asks, and refuses
an image Maya could not read rather than leaving a blank plane on the camera
that reads as "the tool did nothing".

A VIDEO IS CONVERTED, NOT HANDED OVER
-------------------------------------
Maya cannot decode `.mp4` on an image plane. Not "badly" -- measured on
2024/Windows, `coverage` stays -1 in both Movie and Image File mode, so the
plane loads and draws nothing. A MJPEG `.avi` reads fine, which is the trap:
the format nobody has works and the format everybody has does not.

So a dropped `.mp4`, `.mov` or `.avi` goes through `core.transcode` and becomes
a cached image sequence before Maya ever sees it, at the SCENE's frame rate so
frame N of the reference is frame N of the timeline. The animator sees a
progress bar and then a reference; they do not see a video editor.

The plane keeps the movie's NAME as its label, because `frame.####.jpg` in a
cache folder is not what was dropped and is not what should have to be
recognised in a panel.

When there is no ffmpeg -- the setting is off, or a platform has no bundled
binary and none on PATH -- the movie is handed to Maya as it always was, and a
plane whose pixels never arrive is KEPT rather than deleted, with a message
saying why. Deleting it would leave an animator who dropped a perfectly good
file with no evidence anything happened; a visible empty plane plus a warning
tells them exactly what to do next.

TIME: THE REFERENCE MEETS THE ANIMATION WHERE THE ANIMATOR IS
--------------------------------------------------------------
Maya wires `frameExtension` to time as soon as `useFrameExtension` is on, and
draws image number `frameExtension + frameOffset`. So `frameOffset` is the only
thing that decides which frame of the reference is on screen now, and every
timing operation here is arithmetic on it:

    offset = first_frame_of_the_sequence - the_frame_it_should_appear_on

A drop lands at the current frame rather than at frame 1 because the animator
is sitting on the frame they want it to start at -- that is what made them drop
it there.
"""

import logging
import math
import os

from maya import cmds

from animkit.core import media, settings, transcode, undo
from animkit.tools import registry

log = logging.getLogger(__name__)

#: The tag that makes an image plane one of ours. Holds the display label.
TAG_ATTR = "animkitReference"
#: The source path exactly as dropped, before sequence resolution. Kept so the
#: panel can say where a reference came from when `imageName` has been
#: repathed by a pipeline tool.
SOURCE_ATTR = "animkitSource"

#: Where animkit last STOOD a free plane up: its world matrix and its size,
#: written at placement time.
#:
#: It exists to answer one question -- "has the animator moved this?" -- which
#: is what makes laying a new drop out beside the references already up safe.
#: Re-flowing a row means moving planes that are already in the scene, and
#: moving a board somebody deliberately parked against a wall is a worse bug
#: than the stacking it fixes. A plane whose matrix still matches this is one
#: nobody has touched, so it can be shuffled along freely; anything else is
#: left exactly where it is. A plane made by an older animkit has no such
#: attribute and is therefore treated as touched -- the safe reading.
PLACED_ATTR = "animkitPlacement"

#: How far a matrix may drift and still count as untouched. Generous enough to
#: absorb the float round-trip through `xform`, far tighter than any nudge an
#: animator could make with a manipulator.
PLACED_TOLERANCE = 1e-4

#: Where a newly dropped reference starts. `reference.start_at` picks one.
#:
#:   RANGE    the first frame of the playback range -- the start of the shot.
#:   CURRENT  the frame the animator is sitting on.
#:   NATIVE   no offset at all: the reference's own frame numbers land on the
#:            timeline frames of the same number.
START_RANGE = "range"
START_CURRENT = "current"
START_NATIVE = "native"

#: Helper nodes that hold the frame clamp, message-connected to the shape here
#: so `remove` can delete them instead of orphaning three utility nodes per
#: reference in the scene.
TIMING_ATTR = "animkitTiming"

#: imagePlane.type. Probed, not guessed -- the enum is
#: `Image File:Texture:Movie`.
TYPE_IMAGE = 0
TYPE_MOVIE = 2

#: imagePlane.fit. `Best` letterboxes the image inside the camera aperture,
#: which is what makes a 16:9 reference sit correctly in a 1.85 camera instead
#: of being stretched to it.
FIT_BEST = 1

#: coverageX before Maya has read any pixels. See the module docstring.
NO_PIXELS = -1

#: How a reference sits in the scene. These are not two configurations of one
#: thing, they are two different objects, and the choice is made at creation:
#:
#:   FREE    a top-level transform. Select it, move it, rotate it, scale it
#:           with the ordinary manipulators, exactly like any other object.
#:   CAMERA  parented under the camera SHAPE, in Maya's under-world. It rides
#:           the camera and always fills the frame -- and it cannot be grabbed
#:           in the viewport. `lockedToCamera 0` does NOT free it; the plane
#:           stays parented under the camera either way.
#:
#: Free is the default because "put it where I want it and let me scale it" is
#: what an animator does with a reference board, and because a locked plane
#: that will not respond to the move tool reads as a broken object rather than
#: as a deliberate choice. `pin()` converts between the two in place.
ATTACH_FREE = "free"
ATTACH_CAMERA = "camera"

#: How much of the camera's view a new free plane fills. Not 1.0, so the
#: animator can see it is a plane sitting in the scene rather than a viewport
#: overlay -- and so the manipulator handles are reachable at the edges.
VIEW_FILL = 0.8

#: How a new FREE reference is oriented. `reference.orient` picks one.
#:
#:   FRONT    rotate (0,0,0) -- axis-aligned, facing world +Z. The same
#:            transform Maya's own `Create > Free Image Plane` produces.
#:   UPRIGHT  vertical, turned to face the camera: rotate (0, yaw, 0).
#:   VIEW     the camera's frame exactly, pitch and roll included.
#:
#: FRONT is the default because it is the only one that does not depend on
#: where the viewport happened to be pointing. The others make the same file
#: land differently every time, and through the default persp -- which sits at
#: exactly 45 degrees of yaw -- they leave the board foreshortened to 71% of
#: its width in the `front` view, which reads as a stretched reference rather
#: than as a board seen at an angle.
ORIENT_FRONT = "front"
ORIENT_UPRIGHT = "upright"
ORIENT_VIEW = "view"

#: Gap between side-by-side boards, as a fraction of the column each one gets.
#: Not zero: two references butted edge to edge read as one wide picture, and
#: the whole point of dropping four at once is to compare four things.
SLOT_GUTTER = 0.06


# --- one reference ----------------------------------------------------------


class Reference(object):
    """A thin reader over one tagged image plane. Holds no state.

    Deliberately not cached, for the same reason `sets.SelectionSet` is not:
    the animator is changing opacity and offset while the panel is open, and a
    stale wrapper showing the previous value is worse than reading four
    attributes again.
    """

    def __init__(self, shape):
        self.shape = shape

    # --- identity ---

    @property
    def label(self):
        try:
            return cmds.getAttr(self.shape + "." + TAG_ATTR) or self.shape
        except Exception:
            return self.shape

    @property
    def source(self):
        try:
            return cmds.getAttr(self.shape + "." + SOURCE_ATTR) or self.path
        except Exception:
            return self.path

    @property
    def path(self):
        try:
            return cmds.getAttr(self.shape + ".imageName") or ""
        except Exception:
            return ""

    @property
    def transform(self):
        """The image plane's transform, or the shape if it somehow has none."""
        parents = cmds.listRelatives(self.shape, parent=True, fullPath=True)
        return parents[0] if parents else self.shape

    @property
    def camera(self):
        """The camera this plane draws through, or None for a free plane.

        Read from the message connection, not from the parent path -- see the
        module docstring on Maya's under-world naming.
        """
        try:
            found = cmds.listConnections(
                self.shape + ".message", source=False, destination=True
            ) or []
        except Exception:
            return None
        for node in found:
            if cmds.nodeType(node) == "camera":
                return node
            shapes = cmds.listRelatives(node, shapes=True, type="camera") or []
            if shapes:
                return node
        return None

    # --- state the panel shows ---

    @property
    def is_free(self):
        """True when this plane is a top-level object you can grab and move.

        Asked of the scene rather than stored: `pin()` converts a reference
        either way, and so does the animator with Maya's own image plane
        commands, so a flag written at creation would be a lie by lunchtime.
        """
        return self.camera is None

    @property
    def is_sequence(self):
        try:
            return bool(cmds.getAttr(self.shape + ".useFrameExtension"))
        except Exception:
            return False

    @property
    def offset(self):
        try:
            return int(cmds.getAttr(self.shape + ".frameOffset"))
        except Exception:
            return 0

    @property
    def starts_at(self):
        """The TIMELINE frame this reference's first image appears on.

        The number an animator actually has in mind, and the one the panel
        shows. `frameOffset` is Maya's internal form of the same fact and it is
        unreadable as a value: a reference dropped on frame 32 reports an
        offset of -31, which says nothing about anything unless you already
        know the sequence starts at 1 and that Maya adds the offset to time.

        Derived rather than stored, for the same reason `frame_range` is: the
        sequence on disk is the truth and a stored copy goes stale.
        """
        span = self.frame_range()
        first = span[0] if span else 1
        return int(first) - self.offset

    @property
    def opacity(self):
        try:
            return float(cmds.getAttr(self.shape + ".alphaGain"))
        except Exception:
            return 1.0

    @property
    def visible(self):
        try:
            return bool(cmds.getAttr(self.shape + ".visibility"))
        except Exception:
            return False

    @property
    def depth(self):
        try:
            return float(cmds.getAttr(self.shape + ".depth"))
        except Exception:
            return 0.0

    @property
    def loaded(self):
        """True when Maya has actually read pixels from the file."""
        try:
            cmds.dgdirty(self.shape)
            return int(cmds.getAttr(self.shape + ".coverageX")) > NO_PIXELS
        except Exception:
            return False

    def frame_range(self):
        """(first, last) of the sequence on disk, or None.

        Re-read from the folder rather than stored, because a sequence that is
        still rendering grows while the reference is up and a stored range
        would be the range at drop time forever.
        """
        found = media.sequence_of(self.path)
        if found is None:
            return None
        return (found.first, found.last)

    def __repr__(self):
        return "<Reference %s>" % self.label


# --- finding them -----------------------------------------------------------


def _is_ours(shape):
    try:
        return bool(cmds.attributeQuery(TAG_ATTR, node=shape, exists=True))
    except Exception:
        return False


def references():
    """Every animkit reference in the scene, in creation order.

    Image planes the layout department put on the shot camera are not here and
    are never touched -- that is what the tag is for.
    """
    found = []
    for shape in cmds.ls(type="imagePlane") or []:
        if _is_ours(shape):
            found.append(Reference(shape))
    return found


def _selected_references():
    """Any animkit reference reachable from the current selection.

    Selecting an image plane in the Outliner selects its TRANSFORM, and every
    operation here acts on the shape, so the selection has to be walked down
    one level. Selecting the shape directly works too.
    """
    found = []
    for node in cmds.ls(selection=True, long=True) or []:
        candidates = [node]
        candidates += cmds.listRelatives(
            node, shapes=True, type="imagePlane", fullPath=True) or []
        for candidate in candidates:
            if cmds.nodeType(candidate) == "imagePlane" and _is_ours(candidate):
                found.append(Reference(candidate))
    return found


def targets():
    """What an operation acts on: the selected references, else all of them.

    Same shape as every other targeting decision in this codebase -- narrow to
    what is selected when something is, act on everything when nothing is. A
    shot usually has one reference up, so "nothing selected" is the normal case
    and it has to do the obvious thing.
    """
    chosen = _selected_references()
    return chosen if chosen else references()


# --- creating one -----------------------------------------------------------


def _panel_camera():
    """The camera of the viewport the animator is looking at, or None.

    `getPanel -withFocus` can name a panel that is not a modelPanel at all --
    the Graph Editor has focus for most of an animator's day -- so the type is
    checked rather than assumed.
    """
    for query in ({"withFocus": True}, {"visiblePanels": True}):
        try:
            panels = cmds.getPanel(**query)
        except Exception:
            continue
        for panel in ([panels] if isinstance(panels, str) else (panels or [])):
            try:
                if cmds.getPanel(typeOf=panel) != "modelPanel":
                    continue
                camera = cmds.modelPanel(panel, q=True, camera=True)
            except Exception:
                continue
            if camera and cmds.objExists(camera):
                return camera
    return None


def _resolve_camera(camera=None):
    """Which camera a new reference attaches to.

    Falls back through: what the caller asked for, the viewport with focus, any
    visible viewport, `persp`. A reference with no camera at all is a plane
    floating at the origin that the animator has to find and orient by hand,
    so there is no "free plane" fallback -- if there is no camera to attach to
    there is nothing useful to make.
    """
    if camera and cmds.objExists(camera):
        return camera
    found = _panel_camera()
    if found:
        return found
    return "persp" if cmds.objExists("persp") else None


def _camera_shape(camera):
    """The camera shape under `camera`, which may be a transform or a shape."""
    if not camera or not cmds.objExists(camera):
        return None
    if cmds.nodeType(camera) == "camera":
        return camera
    shapes = cmds.listRelatives(
        camera, shapes=True, type="camera", fullPath=True) or []
    return shapes[0] if shapes else None


def image_aspect(shape, default=16.0 / 9.0):
    """Width / height of the image actually loaded, from `coverage`.

    The same attribute that answers "did this file load" also answers "what
    shape is it", so a free plane can be built to the picture's proportions
    instead of Maya's square 10x10 default -- which letterboxes every
    reference an animator will ever drop.
    """
    try:
        cmds.dgdirty(shape)
        wide = float(cmds.getAttr(shape + ".coverageX"))
        high = float(cmds.getAttr(shape + ".coverageY"))
    except Exception:
        return default
    if wide > 0 and high > 0:
        return wide / high
    return default


def placement_distance(camera, override=None):
    """How far in front of the camera to stand a new plane up.

    Defaults to the camera's **centre of interest** -- the point it is aimed
    at, and what Maya itself tumbles around -- so the reference lands where
    the animator is looking rather than at some fixed distance.

    A fixed distance is wrong in the case that matters most. The orthographic
    views sit 1000 units out, so 40 units in front of `front` puts the
    reference 960 units from the character, correctly sized and completely
    somewhere else. Centre of interest puts it on the origin for `top`,
    `front` and `side`, and next to the character for a framed `persp`.

    A positive `reference.distance` overrides, for a rig or a shot where the
    centre of interest is not where the animator wants the board.
    """
    if override and float(override) > 0:
        return float(override)
    shape = _camera_shape(camera)
    if shape is not None:
        try:
            interest = float(
                cmds.camera(shape, q=True, centerOfInterest=True))
            if interest > 1e-4:
                return interest
        except Exception:
            log.debug("animkit: no centre of interest on %r", camera,
                      exc_info=True)
    return 40.0


def _view_size(camera, distance):
    """(width, height) of what the camera sees at `distance`, in world units.

    Orthographic cameras are a separate case rather than an afterthought: an
    animator dropping a front-view reference is usually looking through `front`,
    where field of view means nothing and `orthographicWidth` is the answer.
    """
    shape = _camera_shape(camera)
    if shape is None:
        return (distance, distance * 0.75)
    try:
        if cmds.getAttr(shape + ".orthographic"):
            wide = float(cmds.getAttr(shape + ".orthographicWidth"))
            aperture_x = float(cmds.getAttr(shape + ".horizontalFilmAperture"))
            aperture_y = float(cmds.getAttr(shape + ".verticalFilmAperture"))
            ratio = (aperture_y / aperture_x) if aperture_x else 0.75
            return (wide, wide * ratio)

        vertical = math.radians(
            float(cmds.camera(shape, q=True, verticalFieldOfView=True)))
        horizontal = math.radians(
            float(cmds.camera(shape, q=True, horizontalFieldOfView=True)))
        return (2.0 * distance * math.tan(horizontal / 2.0),
                2.0 * distance * math.tan(vertical / 2.0))
    except Exception:
        log.debug("animkit: could not read the view size", exc_info=True)
        return (distance, distance * 0.75)


def slot_fit(view_wide, view_high, aspect, slot=0, slots=1):
    """(width, height, sideways offset) for one board in a row of `slots`.

    Pure arithmetic: no scene, no camera, no image plane. So the layout of a
    multi-file drop can be asserted on directly, which matters because "four
    videos landed in exactly the same place and only the last one is visible"
    is a bug that presents as "only one of my four files loaded".

    The row is centred on what the camera is looking at and spans the same
    `VIEW_FILL` of the view that a single reference would. Each board is fitted
    into its own column, keeping ITS OWN aspect -- a portrait phone clip beside
    a 16:9 screen recording stays portrait and simply gets less width, rather
    than both being squeezed to a shared shape.
    """
    slots = max(1, int(slots))
    slot = min(max(0, int(slot)), slots - 1)

    span = float(view_wide) * VIEW_FILL
    pitch = span / slots
    column = pitch * (1.0 - SLOT_GUTTER) if slots > 1 else pitch

    high = float(view_high) * VIEW_FILL
    wide = high * aspect if aspect else high
    if wide > column:
        wide = column
        high = wide / aspect if aspect else wide

    # Centres, so an odd count keeps one board dead ahead and an even count
    # straddles the centre. Left to right in the order dropped.
    offset = (slot - (slots - 1) / 2.0) * pitch
    return (wide, high, offset)


def upright_axes(forward):
    """(X, Y, Z) world axes for a board that STANDS UP facing `forward`.

    `forward` is the camera's +Z -- the direction from what it is looking at
    back towards the lens -- and a free image plane's face is its own +Z, so
    the board's Z wants to point along it. Upright uses only the HORIZONTAL
    part: the board takes the camera's yaw and none of its pitch or roll, so it
    stands vertically like Maya's own `Create > Free Image Plane` does, instead
    of leaning back at whatever angle the persp happened to be tumbled to.

    Through `front` this returns the identity, which is exactly the transform
    Maya's native import produces. Through a persp yawed 45 degrees it returns
    the same board turned 45 degrees about Y -- rotate (0, 45, 0), vertical,
    facing the animator, and three clean channels rather than three junk ones.

    Returns None when there is no horizontal part to use, which is a camera
    looking straight down or straight up. There is no upright answer for that
    view -- every vertical plane is edge-on to it -- so the caller falls back
    to exact facing, and a `top` camera correctly gets a plane lying flat.

    Pure arithmetic, no scene, so the awkward cases are testable directly.
    """
    length = math.sqrt(forward[0] * forward[0] + forward[2] * forward[2])
    if length < 1e-4:
        return None
    x, z = forward[0] / length, forward[2] / length
    # X = Y cross Z, which for world up and a horizontal normal (x, 0, z) is
    # (z, 0, -x). Written as the cross product it would be, so the handedness
    # is checkable by eye: X cross Y == Z, no mirror.
    return ([z, 0.0, -x], [0.0, 1.0, 0.0], [x, 0.0, z])


def _place_free(transform, shape, camera, distance, aspect, slot=0, slots=1,
                orient=None):
    """Stand a free plane up in front of the camera, facing it, filling it.

    A free image plane is created at the origin, in the ground plane, at 10x10
    -- so left alone it is edge-on to a perspective view and square. An
    animator who drops a reference and sees nothing has been given a puzzle,
    not a tool.

    The camera's own world matrix supplies the orientation, with the axes
    normalised first: copying it wholesale would hand the plane the camera's
    scale, and a rig with a scaled camera group is not unusual. Maya cameras
    look down **-Z**, so offsetting along -Z puts the plane in front of the
    lens.

    A FREE IMAGE PLANE'S FACE IS ITS LOCAL +Z
    ------------------------------------------
    Measured against Maya's own `Create > Free Image Plane`, which makes a
    plane at rotate (0,0,0) that is VERTICAL: readable through `front`, edge-on
    through `top` and `side`. The quad stands in the local XY plane -- width
    along X, height along Y -- with its face along local **+Z**.

    That is what lets exact facing hand the transform the camera's frame
    unchanged. Maya cameras look down their own -Z, so a plane wearing the
    camera's axes has its +Z pointing back down the lens, which is the way
    round that faces the viewer.

    Worth stating flatly because it is easy to get backwards and expensive to
    check: headless Maya computes no bounding box for an image plane and Maya
    Software will not render a detached one, so nothing in the test tier can
    see which way a plane faces. Rotate channels cannot tell you either --
    through an axis-aligned camera every candidate mapping produces (0,0,0).
    The only instruments are a real viewport and Maya's own native import.

    THE DEFAULT IS AXIS-ALIGNED, NOT CAMERA-RELATIVE
    -------------------------------------------------
    Three orientations, and the argument between them is really about whether
    a reference should depend on where the viewport was pointing when it was
    loaded. FRONT says no.

    VIEW gives the board the camera's frame outright, so one loaded through a
    tumbled persp leans in world space -- correct from the spot it was loaded
    at, skew from everywhere else, and rotate channels like (-27.938, 45, 0)
    that are fiddly to straighten. UPRIGHT drops the pitch and roll and keeps
    the yaw, which stands the board up but still leaves it turned to wherever
    the viewport was: through the default persp that is rotate (0, 45, 0), and
    the cost lands in the orthographic views, where the picture is squashed to
    cos(45) = 71% of its width and reads as a STRETCHED reference rather than
    as a board seen at an angle.

    FRONT is rotate (0,0,0) -- the identity, the same transform Maya's own
    `Create > Free Image Plane` gives. It is square-on in `front`, edge-on in
    `top` and `side`, and it is the same every time regardless of the viewport.
    The other two stay available for matching a specific camera angle;
    `reference.orient` chooses, and the Ref tab's "new drops" is where.

    `slot` / `slots` place one board in a row of several. See `slot_fit`.
    """
    try:
        matrix = cmds.xform(camera, q=True, worldSpace=True, matrix=True)
    except Exception:
        log.debug("animkit: could not read the camera matrix", exc_info=True)
        return False

    axes = []
    for row in range(3):
        vector = matrix[row * 4:row * 4 + 3]
        length = math.sqrt(sum(value * value for value in vector)) or 1.0
        axes.append([value / length for value in vector])

    eye = matrix[12:15]
    forward = axes[2]

    if orient is None:
        orient = settings.get("reference.orient")
    if orient == ORIENT_FRONT:
        standing = ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0])
    elif orient == ORIENT_UPRIGHT:
        standing = upright_axes(forward)
    else:
        standing = None
    if standing is None:
        # Exact facing: the camera's own frame, unchanged. The board's +Z face
        # then points back down the lens, which is the way round that faces the
        # viewer -- and for a camera looking straight down it lays the board
        # flat, which is what a top-down reference should be.
        standing = (axes[0], axes[1], forward)
    right, up, normal = standing

    view_wide, view_high = _view_size(camera, distance)
    wide, high, sideways = slot_fit(view_wide, view_high, aspect, slot, slots)

    # Along the BOARD's right, not the camera's. For an upright row those differ
    # whenever the camera is rolled, and laying out along the camera's right
    # would stagger the boards out of their shared plane.
    placed = [
        right[0], right[1], right[2], 0.0,
        up[0], up[1], up[2], 0.0,
        normal[0], normal[1], normal[2], 0.0,
        eye[0] - forward[0] * distance + right[0] * sideways,
        eye[1] - forward[1] * distance + right[1] * sideways,
        eye[2] - forward[2] * distance + right[2] * sideways,
        1.0,
    ]

    try:
        cmds.xform(transform, worldSpace=True, matrix=placed)
        # `width` and `height`, NOT `sizeX`/`sizeY`. They are separate,
        # unconnected attributes and only these two size a FREE plane --
        # setting sizeX on one leaves it 10x10 and looking like nothing
        # happened. sizeX/sizeY are the camera-attached pair.
        cmds.setAttr(shape + ".width", float(wide))
        cmds.setAttr(shape + ".height", float(high))
    except Exception:
        log.debug("animkit: could not place the reference", exc_info=True)
        return False
    _remember_placement(transform, shape)
    return True


def lay_out(entries, camera=None, distance=None):
    """Stand a list of references up in one row, facing `camera`.

    The row is built in the order given, left to right, and every board is
    re-sized to its share of the view -- so this is what turns three references
    dropped at three different moments into the row the animator would have got
    by dropping them together.

    Camera-attached references are skipped rather than refused: they fill the
    frame by definition and have no position of their own to set, so a mixed
    selection lays out the free ones and leaves the rest alone.
    """
    free = [entry for entry in entries
            if entry.is_free and cmds.objExists(entry.transform)]
    if not free:
        return []

    target = _resolve_camera(camera)
    if target is None:
        cmds.warning(
            "animkit: no camera to lay the reference out in front of -- open "
            "a viewport first"
        )
        return []
    if distance is None:
        distance = placement_distance(target,
                                      settings.get("reference.distance"))

    placed = []
    for slot, entry in enumerate(free):
        if _place_free(entry.transform, entry.shape, target, distance,
                       image_aspect(entry.shape), slot=slot, slots=len(free)):
            placed.append(entry)
    return placed


def _placement_state(transform, shape):
    """The 18 numbers that say where and how big a free plane is right now."""
    matrix = cmds.xform(transform, q=True, worldSpace=True, matrix=True)
    return list(matrix) + [float(cmds.getAttr(shape + ".width")),
                           float(cmds.getAttr(shape + ".height"))]


def _remember_placement(transform, shape):
    """Record where animkit just put this plane. Never raises.

    Failing to record it is not a reason to fail a load -- the only cost is
    that the plane reads as touched forever after, which means a later drop
    leaves it alone. That is the conservative direction to fail in.
    """
    try:
        state = _placement_state(transform, shape)
        if not cmds.attributeQuery(PLACED_ATTR, node=shape, exists=True):
            cmds.addAttr(shape, longName=PLACED_ATTR, dataType="string")
        cmds.setAttr(shape + "." + PLACED_ATTR,
                     " ".join("%.6g" % value for value in state),
                     type="string")
    except Exception:
        log.debug("animkit: could not record the placement of %r", transform,
                  exc_info=True)


def _untouched(entry):
    """True when this free plane is still exactly where animkit put it.

    False for a plane the animator moved, rotated or resized, for one made by
    an animkit that predates the attribute, and for anything that cannot be
    read -- all of which mean the same thing to the caller: do not move it.
    """
    try:
        if not cmds.attributeQuery(PLACED_ATTR, node=entry.shape, exists=True):
            return False
        stored = cmds.getAttr(entry.shape + "." + PLACED_ATTR)
        if not stored:
            return False
        was = [float(value) for value in stored.split()]
        now = _placement_state(entry.transform, entry.shape)
    except Exception:
        log.debug("animkit: could not read the placement of %r", entry.shape,
                  exc_info=True)
        return False
    if len(was) != len(now):
        return False
    return all(abs(a - b) <= PLACED_TOLERANCE for a, b in zip(was, now))


def _tag(shape, label, source):
    if not cmds.attributeQuery(TAG_ATTR, node=shape, exists=True):
        cmds.addAttr(shape, longName=TAG_ATTR, dataType="string")
    cmds.setAttr(shape + "." + TAG_ATTR, label, type="string")
    if not cmds.attributeQuery(SOURCE_ATTR, node=shape, exists=True):
        cmds.addAttr(shape, longName=SOURCE_ATTR, dataType="string")
    cmds.setAttr(shape + "." + SOURCE_ATTR, source, type="string")


def scene_fps():
    """Frames per second of the current scene, for any time unit Maya has.

    Derived from `MTime` rather than mapped from the `currentUnit` string.
    The string form is an open set -- `film`, `ntsc`, `pal`, `ntscf`,
    `23.976fps`, `119.88fps` -- and a lookup table is a list that goes stale
    the next time Autodesk adds a rate. Asking MTime how many UI frames are in
    one second is the same question with an answer that cannot drift.
    """
    try:
        import maya.api.OpenMaya as om

        rate = om.MTime(1.0, om.MTime.kSeconds).asUnits(om.MTime.uiUnit())
        if rate and rate > 0:
            return float(rate)
    except Exception:
        log.debug("animkit: could not read the scene frame rate",
                  exc_info=True)
    return float(transcode.DEFAULT_FPS)


def _convert_movie(path, label):
    """Turn a movie into a cached image sequence. Returns a path, or None.

    Shows a progress window, because converting a two-minute reference takes
    real seconds and a Maya that has simply stopped responding is how an
    animator learns to distrust a tool. Interruptable, and cancelling leaves no
    half-written sequence behind -- `transcode` throws the folder away.
    """
    if not settings.get("reference.convert_movies"):
        return None

    override = settings.get("reference.ffmpeg") or None
    if not transcode.is_available(override):
        cmds.warning(
            "animkit: no ffmpeg available, so %s goes to Maya as a movie. "
            "Maya cannot decode most containers on an image plane -- if the "
            "plane stays blank, that is why." % label
        )
        return None

    fps = scene_fps()
    title = "animkit: converting %s" % label
    opened = False
    try:
        cmds.progressWindow(
            title="animkit", progress=0, status=title + "...",
            isInterruptable=True, minValue=0, maxValue=100,
        )
        opened = True
    except Exception:
        log.debug("animkit: no progress window available", exc_info=True)

    def report(done, total):
        if not opened:
            return True
        try:
            if cmds.progressWindow(q=True, isCancelled=True):
                return False
            if total:
                cmds.progressWindow(
                    edit=True, progress=int(100.0 * done / total),
                    status="%s  (%d / %d frames)" % (title, done, total),
                )
            else:
                cmds.progressWindow(
                    edit=True, status="%s  (%d frames)" % (title, done))
        except Exception:
            return True
        return True

    try:
        result = transcode.convert(
            path,
            fps=fps,
            height=settings.get("reference.convert_height"),
            image_format=settings.get("reference.convert_format"),
            override=override,
            progress=report,
        )
    finally:
        if opened:
            try:
                cmds.progressWindow(endProgress=True)
            except Exception:
                pass

    if result is None:
        cmds.warning(
            "animkit: could not convert %s -- see the Script Editor. It goes "
            "to Maya as a movie instead, which may draw nothing." % label
        )
        return None

    log.info(
        "animkit: converted %s to %d frames at %.3f fps%s",
        label, result.frames, fps,
        " (cached)" if result.cached else " in %.1fs" % result.seconds,
    )
    return result.first_frame


def clamp_frames(shape, first, last):
    """Make the reference HOLD its first and last frame instead of breaking.

    Maya draws image `frameExtension + frameOffset`, and nothing stops that sum
    leaving the sequence. Scrub before a reference starts and Maya is asked for
    image -99; scrub past the end and it is asked for image 31 of 24. Neither
    file exists, so the plane goes blank or draws the magenta missing-image
    pattern, and the animator -- who did nothing but move the time slider --
    sees a reference that has broken itself.

    It is easy to hit and hard to read as anything but a bug. Sync at frame 100
    on a sequence starting at 1 sets the offset to -99, which is CORRECT and
    means the reference now covers frames 100 to 123 and nothing else. And a
    reference loaded at frame 1 in a scene whose range starts at 0 is broken on
    the very first frame of the shot.

    `frameIn` and `frameOut` are not the answer -- measured, they do not clamp
    `outputFrameExtension` at all.

    So the arithmetic is done in the DG instead:

        frameExtension = clamp(time + frameOffset, first, last) - frameOffset

    and Maya then adds `frameOffset` back to get the clamped number. Doing it
    this way rather than folding the offset in means `frameOffset` STAYS the
    single control: slip, sync and the panel's spinbox all still write the one
    attribute they always wrote, and none of them needs to know this exists.

    Three utility nodes, not an expression. An expression here would evaluate
    in Python on every frame of every playback, and Maya's own image-plane
    expression is exactly what this module was written to avoid.

    Never raises: a reference that will not clamp is still a working reference.
    """
    try:
        existing = cmds.listConnections(shape + ".frameExtension", source=True,
                                        destination=False, plugs=True) or []
        for plug in existing:
            try:
                cmds.disconnectAttr(plug, shape + ".frameExtension")
            except Exception:
                log.debug("animkit: could not unhook %s", plug, exc_info=True)

        # time + offset
        total = cmds.createNode("plusMinusAverage", name="animkitRefTime#")
        cmds.setAttr(total + ".operation", 1)
        cmds.connectAttr("time1.outTime", total + ".input1D[0]")
        cmds.connectAttr(shape + ".frameOffset", total + ".input1D[1]")

        held = cmds.createNode("clamp", name="animkitRefHold#")
        cmds.setAttr(held + ".minR", float(first))
        cmds.setAttr(held + ".maxR", float(last))
        cmds.connectAttr(total + ".output1D", held + ".inputR")

        # ...and take the offset back off, because Maya adds it again.
        back = cmds.createNode("plusMinusAverage", name="animkitRefFrame#")
        cmds.setAttr(back + ".operation", 2)
        cmds.connectAttr(held + ".outputR", back + ".input1D[0]")
        cmds.connectAttr(shape + ".frameOffset", back + ".input1D[1]")
        cmds.connectAttr(back + ".output1D", shape + ".frameExtension")
    except Exception:
        log.warning("animkit: could not clamp the reference frame range",
                    exc_info=True)
        return []

    made = [total, held, back]
    try:
        if not cmds.attributeQuery(TIMING_ATTR, node=shape, exists=True):
            cmds.addAttr(shape, longName=TIMING_ATTR, attributeType="message",
                         multi=True, indexMatters=False)
        for node in made:
            cmds.connectAttr(node + ".message",
                             shape + "." + TIMING_ATTR, nextAvailable=True)
    except Exception:
        log.debug("animkit: could not tag the timing nodes", exc_info=True)
    return made


def timing_nodes(shape):
    """The helper nodes belonging to one reference. [] if it has none.

    A reference made before this existed has no such nodes and still works --
    it simply goes blank outside its range the way it always did.
    """
    if not cmds.attributeQuery(TIMING_ATTR, node=shape, exists=True):
        return []
    try:
        return cmds.listConnections(shape + "." + TIMING_ATTR, source=True,
                                    destination=False) or []
    except Exception:
        log.debug("animkit: could not read the timing nodes", exc_info=True)
        return []


def default_start(item):
    """The timeline frame a newly dropped reference should start on.

    Deliberately NOT the current frame by default. That was the original
    behaviour and the reasoning was that an animator sitting on a frame is
    sitting on the frame they want the reference to start at -- which is true
    when they are placing reference for one specific action, and wrong the rest
    of the time. What it looks like otherwise is the tool moving the reference
    for reasons of its own: drop a video while parked on frame 59 and it starts
    at 59, with an offset of -58 as the only visible trace.

    The start of the playback range needs no explaining, and Sync is one click
    away for the case where the old behaviour was right.
    """
    mode = settings.get("reference.start_at")
    if mode == START_NATIVE:
        # offset 0. The reference's own numbering, which is what a render
        # numbered 101-200 wants -- it belongs at 101-200.
        try:
            return int(item.first) if item.is_sequence else 1
        except Exception:
            return 1
    if mode != START_CURRENT:
        try:
            return float(cmds.playbackOptions(q=True, minTime=True))
        except Exception:
            log.debug("animkit: no playback range to start from",
                      exc_info=True)
    try:
        return cmds.currentTime(q=True)
    except Exception:
        return 1


def offset_for(item, frame):
    """The `frameOffset` that puts the item's first frame on `frame`.

    Maya draws image number `frameExtension + frameOffset`, and
    `frameExtension` is time. So at scene frame F the image shown is
    F + offset, and wanting `first` there means offset = first - F.

    A still has no frame extension and this is unused, but it is computed the
    same way so that turning a still into a sequence later needs no special
    case.
    """
    return int(item.first) - int(round(frame))


def load(path, camera=None, frame=None, opacity=None, depth=None,
         attach=None, slot=0, slots=1):
    """Put one file up as reference. Returns a Reference, or None.

    `path` is a single file: one frame of a sequence, a still, or a movie.
    Whether it is part of a sequence is discovered from the folder, so the
    caller does not have to know.

    `attach` is ATTACH_FREE (a plane you can move, rotate and scale) or
    ATTACH_CAMERA (one that rides the camera and fills the frame). It defaults
    to the `reference.attach` setting, which ships as free.

    `slot` and `slots` say "this is board 2 of 4", which stands it beside the
    others instead of on top of them. Only a FREE plane can be laid out that
    way -- a camera-attached one fills the frame by definition, so the pair is
    ignored there rather than quietly producing four planes in one place.

    One undo step, and a lazy one -- a hotkey pressed on a file Maya cannot
    read must not leave an entry in the undo queue.
    """
    item = media.item_for(path)
    if item is None:
        cmds.warning(
            "animkit: %s is not an image or a movie animkit can load"
            % os.path.basename(path or "that")
        )
        return None

    if not os.path.isfile(item.path):
        cmds.warning("animkit: %s is not on disk" % item.path)
        return None

    # A movie becomes an image sequence before Maya ever sees it. Maya cannot
    # decode .mp4 on an image plane at all -- measured, not assumed -- so
    # handing it one is handing it a blank plane. The label stays the movie's
    # name, because `frame.####.jpg` in a cache folder is not what the animator
    # dropped and is not what they should have to recognise in the panel.
    label = item.label
    if item.kind == media.KIND_MOVIE:
        converted = _convert_movie(item.path, label)
        if converted is not None:
            replacement = media.item_for(converted)
            if replacement is not None:
                item = replacement

    # A free plane still needs a camera -- not to hang off, but to be stood up
    # in front of. Created without one it lies flat at the origin at 10x10,
    # edge-on to a perspective view, which looks exactly like nothing happened.
    target = _resolve_camera(camera)
    if target is None:
        cmds.warning(
            "animkit: no camera to put the reference in front of -- open a "
            "viewport first"
        )
        return None

    if frame is None:
        frame = default_start(item)
    if opacity is None:
        opacity = settings.get("reference.opacity")
    if depth is None:
        depth = settings.get("reference.depth")
    if attach is None:
        attach = settings.get("reference.attach")
    free = attach != ATTACH_CAMERA

    # `cmds.imagePlane` SELECTS what it creates, and nothing in its flags turns
    # that off. Two things go wrong if it is left that way. The animator loses
    # the controls they had selected, to a gesture that has nothing to do with
    # selection -- and every reference operation targets the selection first,
    # so the next Fade or Slip silently acts on the plane just dropped instead
    # of on the reference the animator was looking at. Both are invisible until
    # somebody wonders why only one of two references moved.
    restore = cmds.ls(selection=True, long=True) or []

    with undo.LazyChunk("animkit: load reference") as chunk:
        chunk.open()
        if free:
            made = cmds.imagePlane()
        else:
            made = cmds.imagePlane(camera=target, showInAllViews=False)
        shape = made[1]

        cmds.setAttr(
            shape + ".type",
            TYPE_MOVIE if item.kind == media.KIND_MOVIE else TYPE_IMAGE,
        )
        cmds.setAttr(shape + ".imageName", item.path, type="string")
        cmds.setAttr(shape + ".fit", FIT_BEST)
        cmds.setAttr(shape + ".maintainRatio", True)
        cmds.setAttr(shape + ".alphaGain", float(opacity))

        if free:
            # After imageName, because the plane is built to the shape of the
            # picture and that is read back off the loaded image.
            _place_free(made[0], shape, target,
                        placement_distance(
                            target, settings.get("reference.distance")),
                        image_aspect(shape), slot=slot, slots=slots)
        else:
            cmds.setAttr(shape + ".depth", float(depth))
            # Only through the camera it belongs to. A reference attached to
            # persp and drawn in all four panes of a four-up layout hides the
            # shot camera's own view of the character. Meaningless for a free
            # plane, which is an object in the scene and should be visible from
            # everywhere like one.
            cmds.setAttr(shape + ".displayOnlyIfCurrent", True)

        if item.is_sequence or item.kind == media.KIND_MOVIE:
            # Maya wires time -> frameExtension itself the moment this is set.
            # There is no expression to write and none should be written; the
            # one Maya's own UI creates is what this replaces.
            cmds.setAttr(shape + ".useFrameExtension", True)
            cmds.setAttr(shape + ".frameOffset", offset_for(item, frame))
            if item.is_sequence:
                # Only a sequence knows where it ends. An unconverted movie
                # does not, so it keeps Maya's own unclamped wiring.
                clamp_frames(shape, item.first, item.last)

        _tag(shape, label, path)
        _restore_selection(restore)

        found = Reference(shape)
        if not found.loaded:
            if item.kind == media.KIND_MOVIE:
                # Kept on purpose. See the module docstring: whether Maya can
                # decode a container is invisible from here, and an animator
                # who dropped a good .mp4 needs to see something happened.
                cmds.warning(
                    "animkit: Maya read no pixels from %s. The plane is in "
                    "the scene but may stay blank -- this Maya has no decoder "
                    "for that container. Convert it to an image sequence "
                    "(png or jpg); it scrubs better than a movie anyway."
                    % item.label
                )
            else:
                cmds.delete(made[0])
                cmds.warning(
                    "animkit: Maya could not read %s -- the file is corrupt, "
                    "truncated, or not really a %s."
                    % (item.label, media.extension(item.path) or "image")
                )
                return None

    _describe(found, item, label)
    return found


def _restore_selection(nodes):
    """Put back what was selected before, skipping anything now gone.

    Deliberately not a raise on failure: this is housekeeping at the end of an
    operation that has already succeeded, and losing a selection is not a
    reason to lose the reference too.
    """
    try:
        existing = [node for node in nodes if cmds.objExists(node)]
        if existing:
            cmds.select(existing)
        else:
            cmds.select(clear=True)
    except Exception:
        log.debug("animkit: could not restore the selection", exc_info=True)


def _describe(found, item, label=None):
    """Say what arrived. Silence after a drop reads as a drop that missed."""
    parts = [label or item.label]
    if item.is_sequence:
        parts.append("%d frames (%d-%d)"
                     % (item.frame_count, item.first, item.last))
        gaps = item.sequence.missing
        if gaps:
            parts.append("%d MISSING, first at %d" % (len(gaps), gaps[0]))
        if item.sequence.mixed_padding:
            parts.append(
                "other frame paddings in that folder are not part of it"
            )
    elif item.kind == media.KIND_MOVIE:
        parts.append("movie")
    camera = found.camera
    if camera:
        parts.append("pinned to %s" % camera)
    else:
        parts.append("move and scale it like any object")

    message = "animkit: " + " -- ".join(parts)
    log.info(message)
    try:
        cmds.inViewMessage(assistMessage=message, position="midCenterBot",
                           fade=True)
    except Exception:
        pass


def drop(paths, camera=None, frame=None):
    """Load everything in a drop. The entry point drag-and-drop calls.

    A drop of an entire sequence is one reference, not one per file -- see
    `core.media`, which is where that decision lives and is tested.

    SEVERAL FILES AT ONCE LAND IN A ROW, NOT IN A PILE
    ---------------------------------------------------
    Every free plane is stood up in front of the camera, so N of them built the
    same way are N planes in exactly the same place -- and the animator sees
    one reference and concludes the other three did not load. So a drop of more
    than one thing shares the view out between them, left to right in the order
    dropped. Comparing two takes side by side is most of why anybody selects
    two files at once.

    A file that fails leaves its column empty rather than re-flowing the rest:
    the boards keep the positions the animator watched them land in, and a gap
    is a visible "that one did not load" instead of a silent renumbering.

    AND SO DOES A SECOND DROP, MINUTES LATER
    -----------------------------------------
    Laying out only within one drop fixes half the problem. Load one reference,
    look at it, then load another, and the second is stood up in front of the
    camera exactly like the first was -- on top of it. Two boards intersecting
    at the origin is the same "did the second one load?" as before, arrived at
    the long way round.

    So the row is rebuilt across the whole scene: the references already up
    that NOBODY HAS TOUCHED join the new ones and the lot is laid out together.
    Untouched is the load-bearing word and it is why `PLACED_ATTR` exists --
    re-flowing means moving planes that are already in the scene, and dragging
    a board the animator deliberately parked somewhere is a worse bug than the
    stacking it fixes. A plane that has been moved, rotated or resized keeps
    its place and simply takes no part in the row.

    Returns the Reference list. Everything that went wrong has already been
    said out loud by the time this returns; a caller in an event handler has
    nowhere to report an exception to, so nothing raises out of here.
    """
    paths = list(paths or [])

    # A drop is whatever the animator had selected in Explorer, and that
    # routinely mixes a dialogue track in with the video. Sound is a different
    # object with a different home -- an `audio` node on the time slider, not
    # an image plane on a camera -- so it is handed off here rather than being
    # refused as "not an image or a movie", which is what it used to get.
    sounds = [path for path in paths
              if media.kind(path) == media.KIND_AUDIO]
    if sounds:
        try:
            from animkit.tools import audio

            audio.drop(sounds, frame=frame)
        except Exception:
            log.exception("animkit: could not load the dropped sound")
            cmds.warning("animkit: could not load the sound -- see the Script "
                         "Editor")
        paths = [path for path in paths if path not in sounds]
        if not paths:
            return []

    try:
        items = media.resolve(paths)
    except Exception:
        log.exception("animkit: could not read the dropped paths")
        cmds.warning("animkit: could not read what was dropped")
        return []

    if not items:
        ignored = media.rejected(paths)
        if ignored:
            cmds.warning(
                "animkit: nothing to load -- %s %s not an image or a movie"
                % (", ".join(os.path.basename(p) for p in ignored[:3]),
                   "is" if len(ignored) == 1 else "are")
            )
        else:
            cmds.warning("animkit: nothing to load in that drop")
        return []

    # Read BEFORE anything is created, so the new planes are not in this list.
    # Only free ones can be laid out, and only untouched ones may be moved.
    try:
        joining = [entry for entry in references()
                   if entry.is_free and _untouched(entry)]
    except Exception:
        log.debug("animkit: could not read the references already up",
                  exc_info=True)
        joining = []

    made = []
    # Slots are reserved for the references that will join the row, so the new
    # ones land to the right of them and the whole lot is re-flowed together
    # below. Placing each new plane into its final slot as it is created means
    # the animator never sees them pile up and then jump apart.
    slots = len(joining) + len(items)
    # One chunk around the whole drop: dropping four files is one gesture, so
    # it is one Ctrl+Z. load() opens its own lazy chunk inside this one, which
    # Maya nests without complaint.
    with undo.undo_chunk("animkit: drop reference"):
        for index, item in enumerate(items):
            try:
                found = load(item.path, camera=camera, frame=frame,
                             slot=len(joining) + index, slots=slots)
            except Exception:
                log.exception("animkit: could not load %s", item.path)
                cmds.warning("animkit: could not load %s -- see the Script "
                             "Editor" % item.label)
                continue
            if found is not None:
                made.append(found)

        # Now shuffle the untouched ones along to make room. Done after the
        # loads and inside the same chunk, so it is part of the same Ctrl+Z --
        # and skipped entirely when nothing new arrived, because moving the
        # existing row for a drop that failed would be gratuitous.
        if made and joining:
            free_made = [entry for entry in made if entry.is_free]
            if free_made:
                lay_out(joining + free_made, camera=camera)
    return made


def load_prompt():
    """Ask for a file, then load it. The entry point with no drag involved.

    Here rather than in the UI layer for the same reason `sets.store_prompt`
    is: a hotkey has to work with no panel open. Degrades to a warning under
    mayapy and in batch rather than raising.
    """
    try:
        chosen = cmds.fileDialog2(
            fileMode=4,   # one or more existing files
            caption="animkit: load reference",
            okCaption="Load",
            fileFilter=_file_filter(),
        )
    except Exception:
        cmds.warning(
            "animkit: no UI available to pick a file -- call "
            "animkit.tools.reference.load('C:/path/to/ref.mp4') instead"
        )
        return 0
    if not chosen:
        return 0
    return len(drop(chosen))


def _file_filter():
    """A Maya fileDialog2 filter built from the extension sets.

    Generated so that adding an extension to `core.media` adds it to the
    browser too -- a list typed out here is one that disagrees with what the
    loader accepts the first time either changes.
    """
    images = " ".join("*.%s" % e for e in sorted(media.IMAGE_EXTENSIONS))
    movies = " ".join("*.%s" % e for e in sorted(media.MOVIE_EXTENSIONS))
    sounds = " ".join("*.%s" % e for e in sorted(media.AUDIO_EXTENSIONS))
    return (
        "Reference media (%s %s %s);;Images (%s);;Movies (%s);;"
        "Sound (%s);;All Files (*.*)"
        % (images, movies, sounds, images, movies, sounds)
    )


# --- operating on them ------------------------------------------------------


def _acted(nodes, verb):
    """Say what happened, including when the answer is nothing.

    The two ways nothing happens -- no references at all, and a selection with
    no reference in it -- are different problems with different fixes, so they
    get different sentences.
    """
    if nodes:
        return len(nodes)
    if references():
        cmds.warning(
            "animkit: nothing to %s -- select the reference you mean, or "
            "deselect everything to act on all of them." % verb
        )
    else:
        cmds.warning(
            "animkit: no reference in this scene to %s. Drag a video or an "
            "image sequence onto the viewport, or press Load in the Ref tab."
            % verb
        )
    return 0


def slip(frames=1, nodes=None):
    """Shift the reference in time. Positive shows a LATER frame of it now.

    This is the operation that gets used most: the reference is up, the
    animation is a few frames adrift of it, and lining them up is worth a
    hotkey on each direction.
    """
    found = nodes if nodes is not None else targets()
    if not found:
        return _acted(found, "slip")
    with undo.undo_chunk("animkit: slip reference"):
        for entry in found:
            cmds.setAttr(entry.shape + ".frameOffset",
                         entry.offset + int(frames))
    return len(found)


def set_offset(value, nodes=None):
    """Put the reference at an absolute offset. What the panel's spinbox sets.

    Separate from `slip` because a spinbox reports where it now is, not how far
    it moved, and deriving the delta from a stale widget value is how a control
    ends up fighting the animator when two panels are open on the same scene.
    """
    found = nodes if nodes is not None else targets()
    if not found:
        return _acted(found, "offset")
    with undo.undo_chunk("animkit: reference offset"):
        for entry in found:
            cmds.setAttr(entry.shape + ".frameOffset", int(value))
    return len(found)


def set_start(frame, nodes=None):
    """Put the reference's first image on timeline frame `frame`.

    What the panel's "starts at" box sets, and what Sync is a one-click form
    of. Absolute rather than relative for the same reason `set_offset` is:
    a spinbox reports where it now is, not how far it moved.
    """
    found = nodes if nodes is not None else targets()
    if not found:
        return _acted(found, "move")
    with undo.undo_chunk("animkit: reference start frame"):
        for entry in found:
            span = entry.frame_range()
            first = span[0] if span else 1
            cmds.setAttr(entry.shape + ".frameOffset",
                         int(first) - int(round(frame)))
    return len(found)


def sync_to_current(nodes=None):
    """Put the reference's first frame on the frame the animator is sitting on.

    The way back from a slip that went too far, and how a reference dropped at
    the wrong time gets re-aimed without arithmetic.
    """
    found = nodes if nodes is not None else targets()
    if not found:
        return _acted(found, "sync")
    frame = cmds.currentTime(q=True)
    moved = 0
    with undo.undo_chunk("animkit: sync reference to current frame"):
        for entry in found:
            span = entry.frame_range()
            # A still has no first frame to align, and a movie's first frame is
            # 1 by definition. Only a sequence knows where it starts.
            first = span[0] if span else 1
            cmds.setAttr(entry.shape + ".frameOffset",
                         int(first) - int(round(frame)))
            moved += 1
    return moved


def set_opacity(value, nodes=None):
    """Fade the reference so the rig reads through it. 0..1."""
    found = nodes if nodes is not None else targets()
    if not found:
        return _acted(found, "fade")
    value = max(0.0, min(1.0, float(value)))
    with undo.undo_chunk("animkit: reference opacity"):
        for entry in found:
            cmds.setAttr(entry.shape + ".alphaGain", value)
    return len(found)


def fade(delta, nodes=None):
    """Nudge opacity by `delta`, clamped. What the two panel buttons call."""
    found = nodes if nodes is not None else targets()
    if not found:
        return _acted(found, "fade")
    with undo.undo_chunk("animkit: reference opacity"):
        for entry in found:
            cmds.setAttr(
                entry.shape + ".alphaGain",
                max(0.0, min(1.0, entry.opacity + float(delta))),
            )
    return len(found)


def set_visible(visible, nodes=None):
    found = nodes if nodes is not None else targets()
    if not found:
        return _acted(found, "hide")
    with undo.undo_chunk("animkit: reference visibility"):
        for entry in found:
            cmds.setAttr(entry.shape + ".visibility", bool(visible))
    return len(found)


def toggle(nodes=None):
    """Reference on / reference off.

    Flips ALL of them to the same state rather than inverting each, because
    two references half-toggled is a state nobody asked for. If any is showing,
    the gesture means hide.
    """
    found = nodes if nodes is not None else targets()
    if not found:
        return _acted(found, "toggle")
    showing = any(entry.visible for entry in found)
    return set_visible(not showing, nodes=found)


def remove(nodes=None):
    """Delete the reference planes. Destructive, and one undo step.

    Deletes the TRANSFORM, not the shape: deleting the shape alone leaves an
    empty image plane transform parented under the camera, invisible in the
    Outliner's default view and impossible to select in the viewport.
    """
    found = nodes if nodes is not None else targets()
    if not found:
        return _acted(found, "remove")
    doomed = []
    for entry in found:
        node = entry.transform
        if not cmds.objExists(node):
            continue
        doomed.append(node)
        # The clamp's utility nodes are not under the transform, so deleting
        # the plane alone would leave three of them behind per reference --
        # invisible in the Outliner's default view and accumulating quietly
        # across a day of dropping and removing reference.
        doomed.extend(timing_nodes(entry.shape))
    if not doomed:
        return 0
    with undo.undo_chunk("animkit: remove reference"):
        cmds.delete([node for node in doomed if cmds.objExists(node)])
    return len(found)


def _renamed(node, action):
    """Run `action(node)` and return what that node is called afterwards.

    An image plane's NAME encodes where it is parented -- `imagePlaneShape1`
    while it is free, `perspShape->imagePlaneShape1` once it is attached. So
    attaching or detaching one renames it, and every name held across that call
    is stale: the next `setAttr` fails with "No object matches name", pointing
    at a node that plainly exists.

    The UUID does not move, so it is what carries identity across the edit.
    That is the same rule the rest of this codebase follows for rig nodes; it
    just has to be applied to animkit's own node here too.
    """
    uuid = cmds.ls(node, uuid=True)
    action(node)
    if not uuid:
        return node
    found = cmds.ls(uuid[0], long=True) or []
    return found[0] if found else node


def arrange(nodes=None, camera=None):
    """Lay every free reference out in one row, facing the current view.

    The button for "these are a mess, sort them out". Unlike a drop, this moves
    references whether or not the animator has touched them -- because asking
    for it IS the permission, and a tidy-up that silently skipped the boards
    you had dragged around would tidy nothing you actually wanted tidied.

    It is also the way back for references made before animkit recorded where
    it put them: they have no placement to compare against, so a drop leaves
    them alone forever, and this is what re-flows them.

    Ordered by how far left each one currently sits, so a row that is already
    roughly right keeps its reading order instead of being shuffled into
    whatever the scene happens to list.
    """
    found = nodes if nodes is not None else targets()
    free = [entry for entry in found if entry.is_free]
    if not free:
        return _acted([], "arrange")

    target = _resolve_camera(camera)
    if target is None:
        cmds.warning(
            "animkit: no camera to arrange the reference in front of -- open "
            "a viewport first"
        )
        return 0

    # Left-to-right as the animator currently sees them, which is the camera's
    # own right axis and not world X -- through a camera looking down +X those
    # are opposites, and sorting by world X would silently reverse the row.
    try:
        matrix = cmds.xform(target, q=True, worldSpace=True, matrix=True)
        right = matrix[0:3]

        def sideways(entry):
            spot = cmds.xform(entry.transform, q=True, worldSpace=True,
                              translation=True)
            return sum(a * b for a, b in zip(spot, right))

        free.sort(key=sideways)
    except Exception:
        log.debug("animkit: could not sort the references, using scene order",
                  exc_info=True)

    with undo.undo_chunk("animkit: arrange reference"):
        placed = lay_out(free, camera=target)
    if not placed:
        cmds.warning("animkit: could not arrange the reference -- see the "
                     "Script Editor")
    return len(placed)


def framable(nodes=None):
    """The transforms `frame_reference` would look at.

    Split out because `viewFit` needs a viewport and the decision does not --
    under `mayapy` there is no active view at all, so this is the half that can
    be tested and the framing itself is what the in-Maya self-test covers.
    """
    found = nodes if nodes is not None else targets()
    return [entry.transform for entry in found
            if entry.is_free and cmds.objExists(entry.transform)]


def pin(nodes=None):
    """Swap a reference between free and pinned-to-the-camera.

    Free is a top-level object with working move, rotate and scale handles.
    Pinned rides the camera and always fills the frame, and cannot be grabbed
    in the viewport at all -- so this is the button for "let me move that",
    and for putting it back afterwards.

    Flips ALL of the targets to the same state rather than inverting each, for
    the same reason `toggle` does: two references half-converted is a state
    nobody asked for. If any is free, the gesture means pin.

    Maya does the conversion in place with `imagePlane -e -detach` and
    `-e -camera`, so the image, the frame offset and the opacity all survive
    it. A newly freed plane is stood up in front of the camera, because
    detaching leaves it wherever the under-world put it.
    """
    found = nodes if nodes is not None else targets()
    if not found:
        return _acted(found, "pin")

    going_free = not any(entry.is_free for entry in found)
    changed = 0
    # Converting SELECTS the plane, same as creating one does.
    restore = cmds.ls(selection=True, long=True) or []

    with undo.undo_chunk("animkit: pin reference"):
        for entry in found:
            try:
                if going_free:
                    if entry.is_free:
                        continue
                    camera = _resolve_camera(entry.camera)
                    shape = _renamed(entry.shape,
                                     lambda node: cmds.imagePlane(
                                         node, edit=True, detach=True))
                    cmds.setAttr(shape + ".displayOnlyIfCurrent", False)
                    parents = cmds.listRelatives(
                        shape, parent=True, fullPath=True) or []
                    _place_free(
                        parents[0] if parents else shape, shape, camera,
                        placement_distance(
                            camera, settings.get("reference.distance")),
                        image_aspect(shape),
                    )
                else:
                    if not entry.is_free:
                        continue
                    camera = _resolve_camera(None)
                    if camera is None:
                        cmds.warning(
                            "animkit: no camera to pin the reference to -- "
                            "open a viewport first"
                        )
                        break
                    shape = _renamed(entry.shape,
                                     lambda node, cam=camera: cmds.imagePlane(
                                         node, edit=True, camera=cam))
                    cmds.setAttr(shape + ".depth",
                                 float(settings.get("reference.depth")))
                    cmds.setAttr(shape + ".displayOnlyIfCurrent", True)
                changed += 1
            except Exception:
                log.exception("animkit: could not convert %s", entry.shape)
        _restore_selection(restore)

    if changed:
        try:
            cmds.inViewMessage(
                assistMessage="animkit: reference is now %s"
                              % ("free -- move and scale it" if going_free
                                 else "pinned to the camera"),
                position="midCenterBot", fade=True,
            )
        except Exception:
            pass
    return changed


def frame_reference(nodes=None):
    """Look at the reference. `f` on a plane you have lost behind the rig.

    A free plane is an object in the scene, so it can end up off screen -- the
    animator moved the camera, or dropped it while looking somewhere else. This
    is the way back that does not involve hunting in the Outliner.
    """
    found = nodes if nodes is not None else targets()
    if not found:
        return _acted(found, "frame")
    visible = framable(found)
    if not visible:
        cmds.warning(
            "animkit: nothing to frame -- a pinned reference is already in "
            "the camera's view. Press Pin first if you want to move it."
        )
        return 0

    restore = cmds.ls(selection=True, long=True) or []
    try:
        cmds.select(visible)
        cmds.viewFit()
    except Exception:
        # No active view: mayapy, batch, or every panel closed. Not a failure
        # worth a traceback -- there is simply nothing to point at.
        log.debug("animkit: could not frame the reference", exc_info=True)
        cmds.warning("animkit: no viewport to frame the reference in")
        return 0
    finally:
        _restore_selection(restore)
    return len(visible)


def set_depth(value, nodes=None):
    """How far down the frustum the plane sits.

    Deliberately not a panel button and not in the registry: the plane has to
    be behind the character and in front of the far clip, and where that is
    depends on the shot rather than on the moment, so it is a thing set once
    and not a thing nudged. `reference.depth` in the settings is what a new
    reference uses; this is how to change one that is already up.
    """
    found = nodes if nodes is not None else targets()
    if not found:
        return _acted(found, "move")
    with undo.undo_chunk("animkit: reference depth"):
        for entry in found:
            cmds.setAttr(entry.shape + ".depth", float(value))
    return len(found)


# --- the registry -----------------------------------------------------------

OPERATIONS = (
    registry.Operation(
        "animkitRefLoad", "Load",
        "Browse for a video or image sequence and put it up as reference",
        load_prompt, group="Ref",
    ),
    registry.Operation(
        "animkitRefToggle", "Show",
        "Show or hide the reference without deleting it",
        toggle, group="Ref",
    ),
    registry.Operation(
        "animkitRefSlipBack", "-1",
        "Show one frame EARLIER of the reference at the current frame",
        slip, {"frames": -1}, group="Ref",
    ),
    registry.Operation(
        "animkitRefSlipForward", "+1",
        "Show one frame LATER of the reference at the current frame",
        slip, {"frames": 1}, group="Ref",
    ),
    registry.Operation(
        "animkitRefSync", "Sync",
        "Line the reference's first frame up with the current frame",
        sync_to_current, group="Ref",
    ),
    registry.Operation(
        "animkitRefFadeDown", "Fade",
        "Make the reference more transparent so the rig reads through it",
        fade, {"delta": -0.2}, group="Ref",
    ),
    registry.Operation(
        "animkitRefFadeUp", "Solid",
        "Make the reference more solid",
        fade, {"delta": 0.2}, group="Ref",
    ),
    registry.Operation(
        "animkitRefPin", "Pin",
        "Swap the reference between free (move and scale it like any object) "
        "and pinned to the camera",
        pin, group="Ref",
    ),
    registry.Operation(
        "animkitRefArrange", "Arrange",
        "Lay every free reference out side by side, facing the current view",
        arrange, group="Ref",
    ),
    registry.Operation(
        "animkitRefFrame", "Frame",
        "Look at the reference -- the way back to a free plane you have lost "
        "off screen",
        frame_reference, group="Ref",
    ),
    registry.Operation(
        "animkitRefRemove", "Remove",
        "Delete the reference image planes animkit made. Touches no other "
        "image plane in the scene",
        remove, group="Ref", destructive=True,
    ),
)

BY_NAME = dict((op.name, op) for op in OPERATIONS)

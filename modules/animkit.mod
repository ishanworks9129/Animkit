# animkit Maya module file
#
# Install (studio):
#   1. Copy this file to a path on MAYA_MODULE_PATH, e.g.
#        Windows: %USERPROFILE%\Documents\maya\modules\animkit.mod
#        Studio:  \server\pipeline\maya\modules\animkit.mod
#   2. Edit the path on the "+ animkit" line below to point at the repo root.
#   3. Restart Maya.
#
# The module root must contain the `animkit` package directory. Maya adds
# <root>/scripts to PYTHONPATH automatically; the `PYTHONPATH +:= .` line below
# adds the root itself, which is where the package actually lives.
#
# MAYA_SCRIPT_PATH picks up startup/userSetup.py, which calls animkit.startup()
# at launch so the runTimeCommands and scene callbacks are registered without
# the animator editing their own userSetup.py. The startup folder holds that
# one file deliberately -- pointing at scripts/ instead would put bench.py and
# rig_probe.py on every animator's import path.
#
# Prefer to drive startup from a managed, studio-wide userSetup.py? Delete the
# MAYA_SCRIPT_PATH line and use userSetup_example.py instead.
#
# One .mod can serve every Maya version as long as the code is pure Python.
# The moment you ship a compiled .pyd you need one MAYAVERSION line per
# version -- see the commented block at the bottom.

+ animkit 0.1.0 C:/Users/ishan/Desktop/Animbot
PYTHONPATH +:= .
MAYA_SCRIPT_PATH +:= startup

# Per-version form, for when you start shipping compiled plug-ins:
#
# + MAYAVERSION:2024 PLATFORM:win64 animkit 0.1.0 //server/pipeline/animkit
# PYTHONPATH +:= .
# MAYA_SCRIPT_PATH +:= startup
# MAYA_PLUG_IN_PATH +:= plug-ins/2024/win64
#
# + MAYAVERSION:2026 PLATFORM:win64 animkit 0.1.0 //server/pipeline/animkit
# PYTHONPATH +:= .
# MAYA_PLUG_IN_PATH +:= plug-ins/2026/win64

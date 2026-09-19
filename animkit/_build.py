"""Which build this is. Rewritten by scripts/make_release.ps1.

A working copy says "dev". A package cut with -Recipient carries that
recipient's name and a build id, so a usage log or a bug report that comes
back can be matched to the exact zip it came from -- and so can a copy that
turns up somewhere it was not sent.

Nothing imports this directly except animkit.core.usage.build().
"""

#: Short id for this build. "dev" in a working copy.
ID = "dev"

#: Who the package was cut for. Empty in a working copy.
RECIPIENT = ""

#: When it was cut, YYYY-MM-DD. Empty in a working copy.
DATE = ""

"""Works out whether the data folder will survive the container being replaced.

Inside Docker, /proc/self/mountinfo lists what is mounted where. The data
folder is safe if it sits on a host folder (a bind mount, which is what a
TrueNAS Host Path is) or a named volume. It is lost on redeploy if it's only
the container's own filesystem, or an unnamed ("anonymous") volume, which is
what Docker makes when no storage is set up.
"""
import os
import posixpath
import re

ANONYMOUS = re.compile(r"/volumes/[0-9a-f]{64}/_data$")


def _unescape(s: str) -> str:
    # mountinfo writes spaces and other awkward characters as octal escapes.
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def classify(mountinfo: str, folder: str) -> str:
    """'permanent', 'temporary' or 'unknown' for the folder, given mountinfo text."""
    folder = posixpath.normpath(folder)
    best = None  # (mount point, root, filesystem type) of the closest mount holding the folder
    for line in mountinfo.splitlines():
        fields = line.split()
        if " - " not in line or len(fields) < 5:
            continue
        root, point = _unescape(fields[3]), _unescape(fields[4])
        fstype = line.split(" - ", 1)[1].split()[0]
        inside = folder == point or folder.startswith(point.rstrip("/") + "/")
        if inside and (best is None or len(point) >= len(best[0])):
            best = (point, root, fstype)
    if best is None:
        return "unknown"
    point, root, fstype = best
    if point == "/":
        # Only the container's own filesystem. On a normal (non-container) machine
        # the root filesystem is permanent, so only overlay roots count as temporary.
        return "temporary" if fstype == "overlay" else "permanent"
    # tmpfs lives in memory (TrueNAS calls it "Temporary" storage).
    if ANONYMOUS.search(root) or fstype == "tmpfs":
        return "temporary"
    return "permanent"


def status(db_path: str) -> str:
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as f:
            info = f.read()
    except OSError:  # not Linux (e.g. developing on Windows or a Mac)
        return "unknown"
    return classify(info, os.path.dirname(os.path.abspath(db_path)))

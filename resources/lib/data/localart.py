"""Whether a local art file still exists.

A missing file is fair game to fill, so "missing" is only concluded when the
place the file lives is known to answer: the art's own folder exists, or the
item's media exists and the art lay under the media's folder. Anything else
(a sleeping NAS, a share on another host that does not answer) is
``UNREACHABLE``, which the scan treats as transient. Everything goes through
``xbmcvfs`` so SMB, NFS and other VFS paths work; ``xbmcvfs.exists`` checks a
path ending in a separator as a folder and anything else as a file.

Logging:
    Logger: 'localart'
    Key events:
        - localart.exists_failed (ERROR): a VFS call failed; the art is
          reported UNREACHABLE
"""
from __future__ import annotations

import xbmcvfs

from resources.lib.data.nfo import (
    _first_media_path,
    _local_metadata_folder,
    _sep,
    _split,
)
from resources.lib.utils import get_logger

log = get_logger('localart')

EXISTS = "exists"
MISSING = "missing"
UNREACHABLE = "unreachable"


def _exists(path: str) -> bool:
    return bool(xbmcvfs.exists(path))


def _as_folder(path: str) -> str:
    sep = _sep(path)
    return path if path.endswith(sep) else path + sep


def _media_folder(media: str) -> str:
    """The folder whose contents belong to the media, with a trailing
    separator: a show's folder itself, the folder holding a disc image's
    VIDEO_TS or BDMV, otherwise the media file's folder."""
    if media.endswith(_sep(media)):
        return media
    folder = _local_metadata_folder(media)
    return _as_folder(folder) if folder else ""


def local_art_state(art_path: str, media_path: str) -> str:
    """Classify a local art path as ``EXISTS``, ``MISSING`` or
    ``UNREACHABLE``.

    ``media_path`` is the item's media file (stack:// and archives
    included), a show or season's show folder, or "" when the item has none;
    then only the art's own folder can prove the file is gone.
    """
    try:
        if _exists(art_path):
            return EXISTS
        art_folder = _split(art_path)[0]
        if art_folder and _exists(_as_folder(art_folder)):
            return MISSING
        media = _first_media_path(media_path) if media_path else ""
        under = _media_folder(media) if media else ""
        if under and art_path.startswith(under) and _exists(media):
            return MISSING
    except Exception:
        # A share dropping mid-check must not read as "missing": that would
        # replace the user's art because of a network hiccup.
        log.exception("VFS exists check failed", event="localart.exists_failed",
                      path=art_path)
    return UNREACHABLE

from __future__ import annotations

import shutil
from pathlib import Path

from jmbot.core.persistent_queue import AlbumQueueJob, PersistentAlbumQueue


def recover_interrupted_jobs(queue: PersistentAlbumQueue, download_root: str | Path) -> list[AlbumQueueJob]:
    """Recover interrupted processing jobs and remove partial downloaded images."""

    recovered = queue.recover_interrupted_jobs()
    root = Path(download_root)
    for job in recovered:
        album_dir = root / job.album_id
        if album_dir.exists():
            if album_dir.is_dir():
                shutil.rmtree(album_dir)
            else:
                album_dir.unlink()

    return recovered

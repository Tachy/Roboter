#!/usr/bin/env python3
"""Index new inbox images into the LightlyStudio dataset.

`add_images_from_path` needs the DuckDB write lock, which the running
`lightly-studio.service` holds. So: only when there is genuinely a new file,
briefly stop the service, index, restart. Gated by a local marker file so an
idle run never touches the service.

Run from the STUDIO venv:  ~/lightly/venv/bin/python reindex.py
Driven by  lightly-reindex.timer  (every ~5 min).
"""
from __future__ import annotations

import sys

import common

SEEN = "indexed.txt"


def main() -> int:
    on_disk = common.inbox_basenames()
    seen = common.load_seen(SEEN)
    new = on_disk - seen
    if not new:
        print(f"reindex: nothing new ({len(on_disk)} images already indexed)")
        return 0

    print(f"reindex: {len(new)} new image(s) → stop service, index, restart")
    with common.studio_stopped():
        from lightly_studio.database import db_manager
        db_manager.connect(db_file=str(common.DB_FILE), must_exist=False)
        import lightly_studio as ls

        ds = ls.ImageDataset.load_or_create(name=common.DATASET_NAME)
        ds.add_images_from_path(path=str(common.INBOX), embed=True)

    # record everything currently on disk as indexed (idempotent set)
    common.add_seen(SEEN, sorted(on_disk - seen))
    print(f"reindex: done, {len(on_disk)} images indexed total")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Safe, faster RozMusic catalog import with cover photos

- The systemd unit runs 5 concurrent workers and 0.6-second per-job delay.
- SQLite BEGIN IMMEDIATE atomically claims items before any network transfer.
- Interrupted processing rows are recovered on restart with importer lock exclusion.
- Every worker has an independent SQLite connection. Audio fingerprints retain their existing lock.
- Allowlisted HTTPS images are resized with Pillow (320px JPEG, up to 190KB).
- The cover is uploaded as a temporary storage photo; its message is deleted and its photo file ID stored in media_items.cover_id.
- Uploading photos and audio to the Telegram storage chat is paced.
- A missing or invalid cover does not prevent an audio track from being published.
- Raw music and image files are not kept permanently; only Telegram identifiers are saved.
- Covers are added on future imports; older archive entries are not mass-processed.
- Five concurrent workers aim for fivefold throughput, but actual speed depends on source and Telegram limits.
- Source throttling causes cooldown; jobs return to pending.
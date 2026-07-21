"""Reference solution for s3_incremental_sync_exercise.py.

Only look at this AFTER attempting the exercise yourself -- the point of the
exercise is the struggle of deriving the delta logic, not reading it here.
Run the exercise script; if `incremental_sync` still fails, compare your
attempt to `SOLUTION_incremental_sync` below, then go fix your own copy.
"""


def SOLUTION_incremental_sync(s3, bucket, prefix, local_files, before_mtimes, after_mtimes):
    changed = [
        p for p in after_mtimes
        if p not in before_mtimes or after_mtimes[p] != before_mtimes[p]
    ]
    uploaded = []
    for rel_path in changed:
        if rel_path not in local_files:
            continue  # changed in mtime map but no content given -- nothing to upload
        s3.put_object(Bucket=bucket, Key=prefix + rel_path, Body=local_files[rel_path])
        uploaded.append(rel_path)
    return uploaded


# To verify: paste this function's body over your incremental_sync() in the
# exercise script (or import it) and re-run. Note the real codebase would also
# need a `deleted = [p for p in before_mtimes if p not in after_mtimes]` pass
# calling storage.delete() for each -- see docs/AGENT_S3_ARCHITECTURE.md §7.

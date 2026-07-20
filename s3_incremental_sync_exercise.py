"""Hands-on exercise — Lesson 7 of the S3 course.

Your project's real sync_agent_cache_to_remote() (workspaces/storage/__init__.py)
uploads EVERY local file on EVERY turn, even ones the agent never touched.
Lesson 6 explained why that's wasteful. Your job: fix it, right here.

Runs entirely offline via moto — no AWS account, no cost.

    python s3_incremental_sync_exercise.py

What you'll see:
  1. A baseline run of naive_sync() (== today's real code) on a 10-file
     workspace where only 1 file actually changed. Watch the PUT count.
  2. A call to YOUR incremental_sync() (currently a stub below) that should
     do the same job using only ~1 PUT.

Fill in incremental_sync() where marked TODO. Re-run the script until the
assertion at the bottom passes. If you get stuck, s3_incremental_sync_solution.py
has a reference answer -- but try for real first; that's where the learning is.
"""

import boto3
from moto import mock_aws

BUCKET = "exercise-bucket"
REGION = "us-east-1"


class CountingS3:
    """Wraps a boto3 S3 client and counts calls per operation.

    This stands in for "S3 request cost" from Lesson 6 -- every call you
    make through this wrapper is one billable, latency-adding S3 request.
    """

    def __init__(self, client):
        self._client = client
        self.counts = {"put_object": 0, "get_object": 0, "list_objects_v2": 0, "delete_object": 0}

    def put_object(self, **kwargs):
        self.counts["put_object"] += 1
        return self._client.put_object(**kwargs)

    def get_object(self, **kwargs):
        self.counts["get_object"] += 1
        return self._client.get_object(**kwargs)

    def list_objects_v2(self, **kwargs):
        self.counts["list_objects_v2"] += 1
        return self._client.list_objects_v2(**kwargs)

    def delete_object(self, **kwargs):
        self.counts["delete_object"] += 1
        return self._client.delete_object(**kwargs)

    def total(self):
        return sum(self.counts.values())


def remote_snapshot(s3, bucket, prefix):
    """{key: LastModified-as-float} for everything under prefix. 1 LIST call."""
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    return {o["Key"]: o["LastModified"].timestamp() for o in resp.get("Contents", [])}


def naive_sync(s3, bucket, prefix, local_files):
    """TODAY'S REAL CODE (simplified from sync_agent_cache_to_remote).

    Uploads every local file, every time, regardless of whether it changed.
    local_files: {relative_path: bytes}
    """
    uploaded = []
    for rel_path, content in local_files.items():
        s3.put_object(Bucket=bucket, Key=prefix + rel_path, Body=content)
        uploaded.append(rel_path)
    return uploaded


def incremental_sync(s3, bucket, prefix, local_files, before_mtimes, after_mtimes):
    """YOUR EXERCISE.

    Upload ONLY files that are new or changed between `before_mtimes` and
    `after_mtimes` (both are {relative_path: mtime} snapshots, same shape as
    the real WorkspaceStorage.snapshot()). Everything else should cost 0 PUTs.

    Args:
        s3: CountingS3 -- call s3.put_object(Bucket=bucket, Key=prefix+rel, Body=...)
        local_files: {relative_path: bytes} -- content for files worth uploading
        before_mtimes: {relative_path: mtime} -- snapshot taken BEFORE the turn
        after_mtimes:  {relative_path: mtime} -- snapshot taken AFTER the turn

    Returns:
        list of relative paths actually uploaded.
    """
    # TODO: replace this stub. Hint (from Lesson 6):
    #
    #   changed = [p for p in after_mtimes
    #              if p not in before_mtimes or after_mtimes[p] != before_mtimes[p]]
    #
    # Then only put_object() for paths in `changed`, using local_files[p] as Body.
    raise NotImplementedError("Fill in incremental_sync() -- see the TODO above")


@mock_aws
def main():
    s3_raw = boto3.client("s3", region_name=REGION)
    s3_raw.create_bucket(Bucket=BUCKET)
    prefix = "workspaces/demo/"
    s3 = CountingS3(s3_raw)

    # A 10-file workspace -- roughly what a small real lesson workspace looks like.
    all_files = {f"lessons/{i:04d}.html": f"<h1>v1 file {i}</h1>".encode() for i in range(10)}

    print("=== Step 1: baseline turn -- naive_sync uploads everything ===")
    naive_sync(s3, BUCKET, prefix, all_files)
    print(f"PUTs so far: {s3.counts['put_object']}  (expected 10 -- the whole workspace)\n")

    # Simulate a SECOND turn where the agent only touched ONE file.
    before = {name: 1000.0 for name in all_files}          # last turn's mtimes
    after = dict(before)
    after["lessons/0000.html"] = 2000.0                     # only this one changed
    changed_files = {"lessons/0000.html": b"<h1>v2 -- updated!</h1>"}

    print("=== Step 2: naive_sync AGAIN, but only 1 file actually changed ===")
    naive_sync(s3, BUCKET, prefix, all_files)  # still re-uploads all 10 -- the bug
    naive_total_second_turn = s3.counts["put_object"] - 10
    print(f"Naive PUTs for turn 2: {naive_total_second_turn}  <- the waste Lesson 6 described\n")

    print("=== Step 3: YOUR incremental_sync for the same turn 2 ===")
    s3.counts["put_object"] = 0  # reset counter to isolate your function's cost
    uploaded = incremental_sync(s3, BUCKET, prefix, changed_files, before, after)
    your_puts = s3.counts["put_object"]
    print(f"Your incremental_sync PUTs: {your_puts}")
    print(f"Files it uploaded: {uploaded}\n")

    assert uploaded == ["lessons/0000.html"], f"expected only the changed file, got {uploaded}"
    assert your_puts == 1, f"expected exactly 1 PUT, your code made {your_puts}"
    print("PASS -- turn 2 dropped from 10 PUTs to 1. That's the Lesson 6 fix, done by you.")


if __name__ == "__main__":
    main()

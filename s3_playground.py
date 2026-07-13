"""S3 four-verbs playground — Lesson 2 of the S3 course.

Runs entirely offline: `moto` fakes AWS S3 in memory, so this needs
NO AWS account and costs nothing. `moto[s3]` is already in requirements.txt.

    python s3_playground.py

Exercise (from the lesson): add a second key (…/0002.html), re-run,
and confirm the LIST step now shows TWO keys.
"""

import boto3
from moto import mock_aws


@mock_aws
def main():
    s3 = boto3.client("s3", region_name="us-east-1")
    bucket = "demo-bucket"

    # create_bucket needs LocationConstraint for every region except us-east-1
    s3.create_bucket(
        Bucket=bucket,
        CreateBucketConfiguration={"LocationConstraint": "us-east-1"},
    )

    key = "workspaces/demo/lessons/0001.html"

    # WRITE ---------------------------------------------------------------
    s3.put_object(Bucket=bucket, Key=key, Body=b"<h1>v1</h1>")
    print("after write: ", s3.get_object(Bucket=bucket, Key=key)["Body"].read())

    # UPDATE (== put to the same key, full overwrite) ---------------------
    s3.put_object(Bucket=bucket, Key=key, Body=b"<h1>v2</h1>")
    print("after update:", s3.get_object(Bucket=bucket, Key=key)["Body"].read())

    # LIST ----------------------------------------------------------------
    resp = s3.list_objects_v2(Bucket=bucket, Prefix="workspaces/demo/")
    print("keys:        ", [o["Key"] for o in resp.get("Contents", [])])

    # EXISTS (head_object raises if the key is missing) -------------------
    head = s3.head_object(Bucket=bucket, Key=key)
    print("exists, size:", head["ContentLength"], "bytes")

    # DELETE --------------------------------------------------------------
    s3.delete_object(Bucket=bucket, Key=key)
    resp = s3.list_objects_v2(Bucket=bucket, Prefix="workspaces/demo/")
    print("after delete:", resp.get("Contents", []))


if __name__ == "__main__":
    main()

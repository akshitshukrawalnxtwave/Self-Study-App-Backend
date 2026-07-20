from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

import boto3


class Command(BaseCommand):
    help = "Configure S3 CORS for workspace presigned GET URLs."

    def handle(self, *args, **options):
        bucket = settings.AWS_S3_BUCKET_NAME
        if not bucket:
            raise CommandError("AWS_S3_BUCKET_NAME is required")

        origins = settings.CORS_ALLOWED_ORIGINS
        if not origins:
            raise CommandError("CORS_ALLOWED_ORIGINS must contain at least one origin")

        client = boto3.client(
            "s3",
            region_name=settings.AWS_S3_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
        )
        client.put_bucket_cors(
            Bucket=bucket,
            CORSConfiguration={
                "CORSRules": [
                    {
                        "AllowedOrigins": origins,
                        "AllowedMethods": ["GET", "HEAD"],
                        "AllowedHeaders": ["*"],
                        "ExposeHeaders": [
                            "Content-Type",
                            "Content-Length",
                            "ETag",
                            "Last-Modified",
                        ],
                        "MaxAgeSeconds": 3600,
                    }
                ]
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Configured CORS for s3://{bucket} with origins: {', '.join(origins)}"
            )
        )

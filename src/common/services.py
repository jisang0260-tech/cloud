from typing import Optional

import boto3

from common.config import settings


class StorageService:
    def __init__(self) -> None:
        self.client = boto3.client("s3")

    def generate_presigned_url(self, bucket: str, key: Optional[str]) -> Optional[str]:
        if not key:
            return None
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=settings.presigned_url_expires,
        )

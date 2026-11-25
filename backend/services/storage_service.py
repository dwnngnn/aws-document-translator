import logging
from typing import Optional
import boto3
from botocore.exceptions import ClientError
from utils import aws_config

logger = logging.getLogger(__name__)

class StorageService:

    def __init__(self, s3_client=None, bucket_name=None):

        self.s3_client = s3_client or boto3.client(
            "s3",
            region_name=aws_config.AWS_REGION
        )
        self.bucket_name = bucket_name or aws_config.S3_BUCKET_NAME

    def generate_upload_url(
        self,
        key: str,
        content_type: str,
        expiration: int = 900
    ) -> str:
        
        try:
            url = self.s3_client.generate_presigned_url(
                ClientMethod="put_object",
                Params={
                    "Bucket": self.bucket_name,
                    "Key": key,
                    "ContentType": content_type
                },
                ExpiresIn=expiration
            )
            return url
        except ClientError:
            logger.exception("Không thể tạo upload URL")
            raise

    def generate_download_url(
        self,
        key: str,
        expiration: int = 900
    ) -> str:

        try:
            url = self.s3_client.generate_presigned_url(
                ClientMethod="get_object",
                Params={
                    "Bucket": self.bucket_name,
                    "Key": key
                },
                ExpiresIn=expiration
            )
            return url
        except ClientError:
            logger.exception("Không thể tạo download URL")
            raise

    def get_object_metadata(self, key: str) -> dict:
    
        try:
            response = self.s3_client.head_object(
                Bucket=self.bucket_name,
                Key=key
            )
            return {
                "size_bytes": response.get("ContentLength", 0),
                "content_type": response.get("ContentType"),
                "last_modified": response.get("LastModified")
            }

        except ClientError:
            logger.exception("Không thể lấy metadata của file: %s", key)
            raise

    def find_translated_file(self, prefix: str) -> Optional[str]:

        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix
            )

            objects = response.get("Contents", [])

            files = [
                obj for obj in objects
                if obj["Key"] != prefix
            ]

            if not files:
                return None

            latest_file = max(
                files,
                key=lambda obj: obj["LastModified"]
            )
            return latest_file["Key"]

        except ClientError:
            logger.exception("Không thể tìm file đã dịch: %s", prefix)
            raise

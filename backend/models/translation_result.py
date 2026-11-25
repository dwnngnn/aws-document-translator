"""
TranslationResult - kết quả của một yêu cầu dịch.

- Dịch văn bản  : dùng `translated_text`.
- Dịch tài liệu : dùng `translated_s3_key` (đường dẫn file đã dịch trên S3).
"""
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


def _now() -> str:
    # DynamoDB không có kiểu datetime nên lưu dạng chuỗi ISO 8601 (UTC).
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TranslationResult:
    request_id: str
    translated_text: str = ""
    translated_s3_key: str = ""
    result_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    completed_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)

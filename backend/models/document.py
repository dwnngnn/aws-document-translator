from dataclasses import dataclass
from typing import Optional

@dataclass
class Document:
    document_id: str
    request_id: str
    file_name: str
    file_type: str
    content_type: str
    file_size_bytes: int
    input_s3_key: str
    output_s3_prefix: str
    translate_job_id: Optional[str] = None
    created_at: Optional[str] = None

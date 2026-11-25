"""
Hàm tiện ích về tên file, định dạng và S3 key.

Quy ước S3 key (cả nhóm dùng chung):
    input/{user_id}/{request_id}/{file_name}   <- file người dùng upload
    output/{user_id}/{request_id}/...          <- Amazon Translate ghi kết quả vào đây
"""
import re

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB, khớp với frontend

# Các định dạng Amazon Translate hỗ trợ cho dịch tài liệu (batch job).
CONTENT_TYPES = {
    "txt": "text/plain",
    "html": "text/html",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

INPUT_ROOT = "input"
OUTPUT_ROOT = "output"


def safe_name(value, default="file") -> str:
    """Chỉ giữ chữ, số, '.', '_', '-' để không ai chèn được '/' hay '..' vào S3 key."""
    cleaned = re.sub(r"[^\w.-]", "_", str(value or "")).lstrip(".")
    return cleaned or default


def get_extension(file_name: str) -> str:
    return file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""


def get_content_type(file_name: str):
    """Trả về content type, hoặc None nếu định dạng không được hỗ trợ."""
    return CONTENT_TYPES.get(get_extension(file_name))


def count_words(text: str) -> int:
    return len(text.split())


def build_input_prefix(user_id: str, request_id: str) -> str:
    return f"{INPUT_ROOT}/{user_id}/{request_id}/"


def build_input_key(user_id: str, request_id: str, file_name: str) -> str:
    return build_input_prefix(user_id, request_id) + file_name


def build_output_prefix(user_id: str, request_id: str) -> str:
    return f"{OUTPUT_ROOT}/{user_id}/{request_id}/"


def parse_input_key(key: str):
    """'input/u1/r1/a.docx' -> ('u1', 'r1'). Không đúng dạng thì trả về None."""
    parts = key.split("/")
    if len(parts) == 4 and parts[0] == INPUT_ROOT and all(parts):
        return parts[1], parts[2]
    return None

"""
lambda_function.py - điểm vào duy nhất của backend.

Lambda này nhận 2 loại event:
    - API Gateway : frontend gọi API  -> handle_api_event()
    - S3          : file upload xong  -> handle_s3_event()

File này chỉ làm 3 việc: nhận diện loại event, đọc dữ liệu, đổi lỗi thành mã HTTP.
Toàn bộ logic nằm ở OrchestrationService.

API:
    POST /translate-text               body: text, source_language, target_language
    POST /documents/upload-url         body: file_name, file_size_bytes, source_language, target_language
    GET  /requests/{id}/status
    GET  /requests/{id}/download-url
    GET  /history
"""
import base64
import json
import logging
import re
from urllib.parse import unquote_plus

from services.history_service import HistoryService
from services.orchestration_service import (
    NotFoundError, NotReadyError, OrchestrationService, ValidationError,
)
from services.storage_service import StorageService
from services.translate_service import TranslateService
from utils import response_helper

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_orchestrator = None


def get_orchestrator() -> OrchestrationService:
    """Tạo 1 lần cho mỗi container Lambda, các lần gọi sau dùng lại (nhanh hơn)."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = OrchestrationService(StorageService(), TranslateService(), HistoryService())
    return _orchestrator


def lambda_handler(event, context):
    records = event.get("Records") or []
    if records and records[0].get("eventSource") == "aws:s3":
        return handle_s3_event(event)
    return handle_api_event(event)


# ----------------------------------------------------------------------
# Event từ S3: file đã nằm trong input/ -> bắt đầu dịch
# ----------------------------------------------------------------------
def handle_s3_event(event):
    results = []
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])  # S3 mã hóa khoảng trắng thành '+'
        try:
            results.append(get_orchestrator().process_uploaded_document(bucket, key))
        except Exception:
            # Ghi log và đi tiếp; raise sẽ làm Lambda retry cả batch.
            logger.exception("Lỗi xử lý S3 object: %s", key)
            results.append({"key": key, "error": True})
    return {"processed": results}


# ----------------------------------------------------------------------
# Event từ API Gateway
# ----------------------------------------------------------------------
def handle_api_event(event):
    method = (event.get("httpMethod")                                   # REST API (v1)
              or event.get("requestContext", {}).get("http", {}).get("method", "")).upper()  # HTTP API (v2)
    path = (event.get("path") or event.get("rawPath") or "").rstrip("/")

    if method == "OPTIONS":  # trình duyệt gửi trước để kiểm tra CORS
        return response_helper.success({})

    try:
        body = _parse_body(event)
        query = event.get("queryStringParameters") or {}
        user_id = _get_user_id(event, body, query)
        service = get_orchestrator()

        if method == "POST" and path.endswith("/translate-text"):
            return response_helper.success(service.translate_text({**body, "user_id": user_id}))

        if method == "POST" and path.endswith("/documents/upload-url"):
            return response_helper.success(service.create_upload_request({**body, "user_id": user_id}), 201)

        if method == "GET" and path.endswith("/history"):
            return response_helper.success(service.get_history(user_id))

        match = re.search(r"/requests/([^/]+)/(status|download-url)$", path)
        if method == "GET" and match:
            request_id, action = match.groups()
            if action == "status":
                return response_helper.success(service.get_translation_status(user_id, request_id))
            return response_helper.success(service.get_download_url(user_id, request_id))

        return response_helper.error("Không tìm thấy API", 404)

    except ValidationError as exc:
        return response_helper.error(str(exc), 400)
    except NotFoundError as exc:
        return response_helper.error(str(exc), 404)
    except NotReadyError as exc:
        return response_helper.error(str(exc), 409)
    except Exception:
        logger.exception("Lỗi không mong đợi")
        return response_helper.error("Lỗi hệ thống, vui lòng thử lại sau", 500)


def _parse_body(event) -> dict:
    raw = event.get("body")
    if not raw:
        return {}
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValidationError("Body phải là JSON hợp lệ")
    if not isinstance(data, dict):
        raise ValidationError("Body phải là một JSON object")
    return data


def _get_user_id(event, body, query) -> str:
    """Ưu tiên user đã đăng nhập (Cognito); chưa có auth thì lấy từ header/body/query."""
    authorizer = event.get("requestContext", {}).get("authorizer") or {}
    claims = authorizer.get("claims") or authorizer.get("jwt", {}).get("claims") or {}
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    return (claims.get("sub") or headers.get("x-user-id")
            or body.get("user_id") or query.get("user_id") or "anonymous")

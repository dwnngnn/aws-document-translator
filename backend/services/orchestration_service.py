"""
OrchestrationService - "nhạc trưởng" của backend.

Service này KHÔNG gọi AWS trực tiếp. Nó chỉ quyết định thứ tự gọi:
    StorageService   (S3)         -> cấp link upload/download, tìm file đã dịch
    TranslateService (Translate)  -> dịch văn bản, chạy job dịch tài liệu
    HistoryService   (DynamoDB)   -> lưu và cập nhật lịch sử + trạng thái

Luồng dịch tài liệu (3 pha, tương ứng 3 lần Lambda được gọi):
    1. create_upload_request()       API  : tạo request WAITING_UPLOAD, trả link upload
    2. process_uploaded_document()   S3   : file upload xong -> UPLOADED -> SUBMITTED
    3. get_translation_status()      API  : frontend hỏi, đồng bộ trạng thái job -> COMPLETED
       get_download_url()            API  : trả link tải bản dịch
"""
import logging
import uuid
from datetime import datetime, timezone

from models.document import Document
from models.translation_request import TranslationRequest, TranslationStatus
from models.translation_result import TranslationResult
from utils import aws_config, file_helper

logger = logging.getLogger(__name__)

S = TranslationStatus

UPLOAD_URL_EXPIRES = 900      # 15 phút
DOWNLOAD_URL_EXPIRES = 900

# Nhóm trạng thái dùng để quyết định hành động
JOB_RUNNING = {S.SUBMITTED.value, S.IN_PROGRESS.value}            # cần hỏi lại Translate
JOB_DONE = {S.COMPLETED.value, S.COMPLETED_WITH_ERROR.value}      # có file để tải

# Trạng thái job của Amazon Translate -> trạng thái của hệ thống mình
JOB_STATUS_MAP = {
    "SUBMITTED": S.SUBMITTED,
    "IN_PROGRESS": S.IN_PROGRESS,
    "COMPLETED": S.COMPLETED,
    "COMPLETED_WITH_ERROR": S.COMPLETED_WITH_ERROR,
    "FAILED": S.FAILED,
    "STOP_REQUESTED": S.IN_PROGRESS,   # đang dừng, coi như chưa xong
    "STOPPED": S.STOPPED,
}


# ---- Lỗi nghiệp vụ: lambda_function.py đổi thành mã HTTP tương ứng ----
class ValidationError(Exception):
    """Dữ liệu đầu vào sai -> HTTP 400."""


class NotFoundError(Exception):
    """Không có request / file -> HTTP 404."""


class NotReadyError(Exception):
    """Bản dịch chưa xong -> HTTP 409."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _clean_user_id(user_id) -> str:
    return file_helper.safe_name(user_id, default="anonymous")


class OrchestrationService:
    def __init__(self, storage_service, translate_service, history_service):
        self.storage_service = storage_service
        self.translate_service = translate_service
        self.history_service = history_service

    # ------------------------------------------------------------------
    # 1. DỊCH VĂN BẢN (đồng bộ: gọi xong có kết quả ngay)
    # ------------------------------------------------------------------
    def translate_text(self, data: dict) -> dict:
        now = _now()
        request = TranslationRequest(
            request_id=_new_id(),
            user_id=_clean_user_id(data.get("user_id")),
            request_type="TEXT",
            input_text=(data.get("text") or "").strip(),
            source_language=data.get("source_language") or "auto",
            target_language=data.get("target_language") or "",
            status=S.IN_PROGRESS,
            created_at=now,
            updated_at=now,
        )
        self._validate(request)

        try:
            translated = self.translate_service.translate_text(
                request.input_text, request.source_language, request.target_language
            )
        except Exception as exc:
            logger.exception("Dịch văn bản thất bại")
            request.status, request.error_message, request.updated_at = S.FAILED, str(exc), _now()
            self.history_service.save_history(self._to_item(request))
            raise

        result = TranslationResult(request_id=request.request_id, translated_text=translated)
        request.status, request.updated_at = S.COMPLETED, result.completed_at
        self.history_service.save_history(self._to_item(request, result=result))

        return {
            "request_id": request.request_id,
            "status": S.COMPLETED.value,
            "translated_text": translated,
            "word_count": file_helper.count_words(request.input_text),
        }

    # ------------------------------------------------------------------
    # 2. DỊCH TÀI LIỆU - pha 1: frontend xin link upload
    # ------------------------------------------------------------------
    def create_upload_request(self, data: dict) -> dict:
        user_id = _clean_user_id(data.get("user_id"))
        file_name = file_helper.safe_name(data.get("file_name"))
        content_type = file_helper.get_content_type(file_name)
        size = int(data.get("file_size_bytes") or 0)
        source = data.get("source_language") or ""

        if not content_type:
            raise ValidationError("Định dạng tệp chưa được hỗ trợ")
        if size <= 0 or size > file_helper.MAX_FILE_SIZE_BYTES:
            raise ValidationError("Dung lượng tệp phải từ 1 byte đến 10 MB")
        if source in ("", "auto"):
            # Dịch tài liệu (batch job) của Amazon Translate không tự nhận diện ngôn ngữ nguồn.
            raise ValidationError("Dịch tài liệu cần chọn cụ thể ngôn ngữ nguồn")

        now = _now()
        request_id = _new_id()
        request = TranslationRequest(
            request_id=request_id,
            user_id=user_id,
            request_type="DOCUMENT",
            document_id=_new_id(),
            source_language=source,
            target_language=data.get("target_language") or "",
            status=S.WAITING_UPLOAD,
            created_at=now,
            updated_at=now,
        )
        self._validate(request)

        document = Document(
            document_id=request.document_id,
            request_id=request_id,
            file_name=file_name,
            file_type=file_helper.get_extension(file_name),
            content_type=content_type,
            file_size_bytes=size,
            input_s3_key=file_helper.build_input_key(user_id, request_id, file_name),
            output_s3_prefix=file_helper.build_output_prefix(user_id, request_id),
            created_at=now,
        )

        upload_url = self.storage_service.generate_upload_url(
            document.input_s3_key, content_type, UPLOAD_URL_EXPIRES
        )
        self.history_service.save_history(self._to_item(request, document=document))

        return {
            "request_id": request_id,
            "document_id": document.document_id,
            "status": S.WAITING_UPLOAD.value,
            "upload_url": upload_url,
            "upload_method": "PUT",
            "upload_headers": {"Content-Type": content_type},  # frontend phải gửi đúng header này
            "expires_in": UPLOAD_URL_EXPIRES,
        }

    # ------------------------------------------------------------------
    # 3. DỊCH TÀI LIỆU - pha 2: S3 báo "upload xong" -> khởi động job dịch
    # ------------------------------------------------------------------
    def process_uploaded_document(self, bucket: str, key: str) -> dict:
        parsed = file_helper.parse_input_key(key)
        if not parsed:
            logger.info("Bỏ qua object ngoài input/: %s", key)
            return {"skipped": True, "key": key}

        user_id, request_id = parsed
        item = self._get_item(user_id, request_id)

        # S3 có thể gửi trùng event; chỉ xử lý khi đang chờ upload.
        if item["status"] != S.WAITING_UPLOAD.value:
            return {"skipped": True, "request_id": request_id, "status": item["status"]}

        item = self._update(item, status=S.UPLOADED)
        try:
            # Presigned URL không giới hạn được dung lượng, nên kiểm tra lại ở đây.
            meta = self.storage_service.get_object_metadata(key)
            if meta.get("size_bytes", 0) > file_helper.MAX_FILE_SIZE_BYTES:
                raise ValueError("Tệp vượt quá 10 MB")

            # Translate đọc cả "thư mục" input của request này và ghi kết quả vào thư mục output.
            job_id = self.translate_service.start_document_job(
                input_uri=f"s3://{bucket}/{file_helper.build_input_prefix(user_id, request_id)}",
                output_uri=f"s3://{bucket}/{item['output_s3_prefix']}",
                source_language=item["source_language"],
                target_language=item["target_language"],
                content_type=item["content_type"],
                data_access_role_arn=aws_config.TRANSLATE_ROLE_ARN,
            )
        except Exception as exc:
            # Không raise: raise sẽ khiến Lambda tự retry S3 event và job bị tạo lặp.
            logger.exception("Không khởi động được job dịch")
            self._update(item, status=S.FAILED, error_message=str(exc))
            return {"request_id": request_id, "status": S.FAILED.value}

        self._update(item, status=S.SUBMITTED, translate_job_id=job_id)
        return {"request_id": request_id, "status": S.SUBMITTED.value, "job_id": job_id}

    # ------------------------------------------------------------------
    # 4. DỊCH TÀI LIỆU - pha 3: frontend hỏi tiến trình / tải kết quả
    # ------------------------------------------------------------------
    def get_translation_status(self, user_id: str, request_id: str) -> dict:
        item = self._get_item(user_id, request_id)
        if item["status"] in JOB_RUNNING and item.get("translate_job_id"):
            item = self._sync_job_status(item)
        return {
            "request_id": request_id,
            "status": item["status"],
            "error_message": item.get("error_message"),
            "download_ready": item["status"] in JOB_DONE,
            "updated_at": item.get("updated_at"),
        }

    def get_download_url(self, user_id: str, request_id: str) -> dict:
        item = self._get_item(user_id, request_id)
        if item["request_type"] != "DOCUMENT":
            raise ValidationError("Chỉ yêu cầu dịch tài liệu mới có file để tải")
        if item["status"] not in JOB_DONE:
            raise NotReadyError("Bản dịch chưa hoàn tất")

        key = item.get("translated_s3_key") or self.storage_service.find_translated_file(
            item["output_s3_prefix"]
        )
        if not key:
            raise NotFoundError("Không tìm thấy file đã dịch")
        if not item.get("translated_s3_key"):
            self._update(item, translated_s3_key=key)

        return {
            "request_id": request_id,
            "file_name": key.rsplit("/", 1)[-1],
            "download_url": self.storage_service.generate_download_url(key, DOWNLOAD_URL_EXPIRES),
            "expires_in": DOWNLOAD_URL_EXPIRES,
        }

    def get_history(self, user_id: str) -> dict:
        items = self.history_service.get_history(_clean_user_id(user_id))
        items.sort(key=lambda i: i.get("created_at", ""), reverse=True)  # mới nhất lên đầu
        return {"items": items}

    # ------------------------------------------------------------------
    # Hàm nội bộ
    # ------------------------------------------------------------------
    def _validate(self, request: TranslationRequest) -> None:
        if not request.validate_request():
            raise ValidationError(request.error_message or "Yêu cầu không hợp lệ")

    def _get_item(self, user_id: str, request_id: str) -> dict:
        item = self.history_service.get_history_item(_clean_user_id(user_id), request_id)
        if not item:
            raise NotFoundError("Không tìm thấy yêu cầu dịch")
        return item

    def _update(self, item: dict, **changes) -> dict:
        """Cập nhật history và trả về item mới (giá trị None bị bỏ qua)."""
        changes = {
            k: (v.value if isinstance(v, TranslationStatus) else v)
            for k, v in changes.items() if v is not None
        }
        changes["updated_at"] = _now()
        self.history_service.update_history(item["user_id"], item["request_id"], changes)
        return {**item, **changes}

    def _sync_job_status(self, item: dict) -> dict:
        """Hỏi Amazon Translate trạng thái job và ghi lại vào history nếu có thay đổi."""
        job = self.translate_service.describe_document_job(item["translate_job_id"])
        new_status = JOB_STATUS_MAP.get(job.get("status"))
        if new_status is None or new_status.value == item["status"]:
            return item

        changes = {"status": new_status}
        if new_status.value in JOB_DONE:
            key = self.storage_service.find_translated_file(item["output_s3_prefix"])
            result = TranslationResult(request_id=item["request_id"], translated_s3_key=key or "")
            changes.update(result_id=result.result_id, completed_at=result.completed_at,
                           translated_s3_key=key)
        if new_status in (S.FAILED, S.STOPPED, S.COMPLETED_WITH_ERROR):
            changes["error_message"] = job.get("message")
        return self._update(item, **changes)

    def _to_item(self, request, document=None, result=None) -> dict:
        """Gộp request (+ document, result) thành 1 dòng phẳng để lưu DynamoDB."""
        item = {
            "user_id": request.user_id,            # partition key
            "request_id": request.request_id,      # sort key
            "request_type": request.request_type,
            "source_language": request.source_language,
            "target_language": request.target_language,
            "status": request.status.value,
            "error_message": request.error_message,
            "created_at": request.created_at,
            "updated_at": request.updated_at,
        }
        if request.request_type == "TEXT":
            item["input_text"] = request.input_text
            item["word_count"] = file_helper.count_words(request.input_text)
        if document:
            item.update(
                document_id=document.document_id, file_name=document.file_name,
                file_type=document.file_type, content_type=document.content_type,
                file_size_bytes=document.file_size_bytes, input_s3_key=document.input_s3_key,
                output_s3_prefix=document.output_s3_prefix,
            )
        if result:
            item.update(result.to_dict())
        return {k: v for k, v in item.items() if v not in (None, "")}

import logging
import re
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

AUTO_DETECT = "auto"

# Mã ngôn ngữ của Amazon Translate: 'vi', 'en', 'zh-TW', 'pt-PT', 'fr-CA'...
_LANG_PATTERN = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


def _normalize_language(code):
    """Chuẩn hóa mã ngôn ngữ: 'ZH_tw' -> 'zh-TW', ' EN ' -> 'en', '' -> ''."""
    if not isinstance(code, str):
        return ""
    code = code.strip().replace("_", "-")
    if not code:
        return ""
    if code.lower() == AUTO_DETECT:
        return AUTO_DETECT
    parts = code.split("-", 1)
    base = parts[0].lower()
    if len(parts) == 2:
        region = parts[1].upper() if len(parts[1]) == 2 else parts[1].title()
        return "{}-{}".format(base, region)
    return base


def _get_field(obj, name):
    """Đọc field từ dict hoặc object (duck typing cho Document của Trang)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


class TranslationRequest:
    SUPPORTED_FILE_TYPES = ("txt", "docx")
    MAX_TEXT_BYTES = 500000                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        

    def __init__(self, userId="", requestId="", inputText="",
                 sourceLanguage=AUTO_DETECT, targetLanguage="",
                 createdAt=None, document=None):
        self.userId = userId.strip() if isinstance(userId, str) else userId
        if isinstance(requestId, str) and requestId.strip():
            self.requestId = requestId.strip()
        elif requestId:
            self.requestId = requestId
        else:
            self.requestId = str(uuid.uuid4())
        self.inputText = "" if inputText is None else inputText
        self.sourceLanguage = _normalize_language(sourceLanguage) or AUTO_DETECT
        self.targetLanguage = _normalize_language(targetLanguage)
        self.createdAt = createdAt or datetime.now(timezone.utc)
        self.document = document
        self.errors = []

    @classmethod
    def from_dict(cls, data):
        """Tạo request từ dict (body API Gateway đã json.loads, hoặc dict Ngân dựng
        từ S3 event + metadata). Key theo camelCase của class model."""
        data = data or {}
        return cls(
            userId=data.get("userId", ""),
            requestId=data.get("requestId", ""),
            inputText=data.get("inputText", ""),
            sourceLanguage=data.get("sourceLanguage", AUTO_DETECT),
            targetLanguage=data.get("targetLanguage", ""),
            document=data.get("document"),
        )

    def to_dict(self):
        """Dict để log / lưu lịch sử (không chứa nội dung inputText)."""
        doc = None
        if self.document is not None:
            doc = {k: _get_field(self.document, k)
                   for k in ("documentId", "fileName", "fileType", "s3Key")}
        return {
            "userId": self.userId,
            "requestId": self.requestId,
            "sourceLanguage": self.sourceLanguage,
            "targetLanguage": self.targetLanguage,
            "createdAt": self.createdAt.isoformat(),
            "inputTextLength": len(self.inputText) if isinstance(self.inputText, str) else 0,
            "document": doc,
        }

    def hasDocument(self):
        """True nếu là yêu cầu dịch tài liệu (để Ngân phân luồng)."""
        return self.document is not None

    def get_errors(self):
        return list(self.errors)

    def validateRequest(self):
        """Kiểm tra dữ liệu đầu vào. Trả True/False; chi tiết lỗi ở self.errors."""
        self.errors = []

        # userId, requestId 
        if not isinstance(self.userId, str) or not self.userId:
            self.errors.append("Thiếu userId.")
        if not isinstance(self.requestId, str) or not self.requestId:
            self.errors.append("Thiếu requestId.")

        # Nội dung
        text_ok = isinstance(self.inputText, str) and self.inputText.strip() != ""
        if self.inputText and not isinstance(self.inputText, str):
            self.errors.append("inputText phải là chuỗi.")
        has_doc = self.document is not None
        if text_ok and has_doc:
            self.errors.append("Chỉ gửi inputText hoặc document, không gửi cả hai.")
        elif not text_ok and not has_doc:
            self.errors.append("Thiếu nội dung: cần inputText không rỗng hoặc document.")
        elif text_ok:
            if len(self.inputText.encode("utf-8")) > self.MAX_TEXT_BYTES:
                self.errors.append(
                    "inputText vượt giới hạn {} bytes.".format(self.MAX_TEXT_BYTES))
        else:
            self._validate_document()

        # Ngôn ngữ
        if not self.targetLanguage:
            self.errors.append("Thiếu targetLanguage.")
        elif self.targetLanguage == AUTO_DETECT or not _LANG_PATTERN.match(self.targetLanguage):
            self.errors.append("targetLanguage không hợp lệ: '{}'.".format(self.targetLanguage))
        if self.sourceLanguage != AUTO_DETECT and not _LANG_PATTERN.match(self.sourceLanguage):
            self.errors.append("sourceLanguage không hợp lệ: '{}'.".format(self.sourceLanguage))
        if self.targetLanguage and self.sourceLanguage == self.targetLanguage:
            self.errors.append("sourceLanguage và targetLanguage không được trùng nhau.")

        if self.errors:
            logger.warning("Request %s không hợp lệ: %s", self.requestId, self.errors)
        return not self.errors

    def _validate_document(self):
        s3_key = _get_field(self.document, "s3Key")
        file_name = _get_field(self.document, "fileName")
        file_type = _get_field(self.document, "fileType")
        if not s3_key:
            self.errors.append("document thiếu s3Key.")
        if not file_name:
            self.errors.append("document thiếu fileName.")

        ext = ""
        if isinstance(file_type, str) and file_type and "/" not in file_type:
            ext = file_type.strip().lstrip(".").lower()
        elif isinstance(file_name, str) and "." in file_name:
            ext = file_name.rsplit(".", 1)[1].lower()
        if ext not in self.SUPPORTED_FILE_TYPES:
            self.errors.append("Định dạng file '{}' chưa được hỗ trợ (chỉ {}).".format(
                ext or "?", ", ".join(self.SUPPORTED_FILE_TYPES)))
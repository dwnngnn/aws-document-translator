import logging
import os
import re

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

AUTO_DETECT = "auto"
_LANG_PATTERN = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")

_SENTENCE_PATTERN = re.compile(r".*?[.!?。！？…]+[\"')\]]*\s*|.+$", re.S)

_AWS_ERROR_MAP = {
    "TextSizeLimitExceededException": ("TEXT_TOO_LARGE", False),
    "UnsupportedLanguagePairException": ("UNSUPPORTED_LANGUAGE_PAIR", False),
    "DetectedLanguageLowConfidenceException": ("LOW_CONFIDENCE", False),
    "InvalidRequestException": ("INVALID_REQUEST", False),
    "TooManyRequestsException": ("THROTTLED", True),
    "ThrottlingException": ("THROTTLED", True),
    "ServiceUnavailableException": ("SERVICE_UNAVAILABLE", True),
    "InternalServerException": ("SERVICE_UNAVAILABLE", True),
    "AccessDeniedException": ("ACCESS_DENIED", False),
}


class TranslateServiceError(Exception):

    def __init__(self, error_code, message, retryable=False):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.retryable = retryable


def _normalize_language(code):
    """'ZH_tw' -> 'zh-TW', ' EN ' -> 'en'. Trả '' nếu không phải chuỗi/rỗng."""
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


def _utf8_len(s):
    return len(s.encode("utf-8"))


class TranslateService:
    MAX_BYTES_PER_REQUEST = 10000  
    CHUNK_BYTES = 9000            

    def __init__(self, translateClient=None, regionName=None):
        """translateClient: truyền client giả khi unit test; mặc định tạo boto3 client."""
        if translateClient is None:
            cfg = Config(retries={"max_attempts": 5, "mode": "standard"},
                         connect_timeout=5, read_timeout=30)
            translateClient = boto3.client(
                "translate",
                region_name=regionName or os.environ.get("AWS_REGION"),
                config=cfg)
        self._translateClient = translateClient

    def translateText(self, text, sourceLanguage, targetLanguage):
        """Dịch văn bản (ngắn hoặc dài).

        Trả về dict:
            translatedText, sourceLanguage (đã resolve nếu auto), targetLanguage, chunkCount
        Raise TranslateServiceError khi lỗi.
        """
        if not isinstance(text, str) or not text.strip():
            raise TranslateServiceError("EMPTY_TEXT", "Văn bản cần dịch đang rỗng.")

        src = _normalize_language(sourceLanguage) or AUTO_DETECT
        tgt = _normalize_language(targetLanguage)
        if not tgt or tgt == AUTO_DETECT or not _LANG_PATTERN.match(tgt):
            raise TranslateServiceError(
                "INVALID_LANGUAGE", "targetLanguage không hợp lệ: '{}'.".format(targetLanguage))
        if src != AUTO_DETECT and not _LANG_PATTERN.match(src):
            raise TranslateServiceError(
                "INVALID_LANGUAGE", "sourceLanguage không hợp lệ: '{}'.".format(sourceLanguage))
        if src == tgt:
            raise TranslateServiceError(
                "SAME_LANGUAGE", "Ngôn ngữ nguồn và đích trùng nhau: '{}'.".format(src))

        chunks = self._split_text(text, self.CHUNK_BYTES)
        logger.info("translateText: %d ký tự, %d đoạn, %s -> %s",
                    len(text), len(chunks), src, tgt)

        parts = []
        effective_src = src
        for idx, chunk in enumerate(chunks, 1):
            if not chunk.strip():          
                parts.append(chunk)
                continue
            core = chunk.strip()
            lead = chunk[:len(chunk) - len(chunk.lstrip())]
            trail = chunk[len(chunk.rstrip()):]
            translated, detected = self._translate_chunk(core, effective_src, tgt, idx, len(chunks))
            if effective_src == AUTO_DETECT and detected:
                effective_src = detected
            parts.append(lead + translated + trail)   
        return {
            "translatedText": "".join(parts),
            "sourceLanguage": effective_src,
            "targetLanguage": tgt,
            "chunkCount": len(chunks),
        }

    def detectLanguage(self, text):
        """Phát hiện ngôn ngữ của văn bản, trả về mã như 'en', 'vi'."""
        if not isinstance(text, str) or not text.strip():
            raise TranslateServiceError("EMPTY_TEXT", "Văn bản cần phát hiện ngôn ngữ đang rỗng.")
        sample = self._split_text(text.strip(), 1000)[0].strip()
        last_error = None 
        for probe_target in ("en", "vi"):
            try:
                _, detected = self._translate_chunk(sample, AUTO_DETECT, probe_target, 1, 1)
                if detected:
                    return detected
            except TranslateServiceError as exc:
                last_error = exc
        raise last_error or TranslateServiceError(
            "DETECT_FAILED", "Không phát hiện được ngôn ngữ.")

    def _translate_chunk(self, chunk, src, tgt, idx, total):
        """Gọi Amazon Translate cho 1 đoạn. Trả (translated, detectedSourceLanguage)."""
        try:
            resp = self._translateClient.translate_text(
                Text=chunk, SourceLanguageCode=src, TargetLanguageCode=tgt)
        except ClientError as exc:
            aws_code = exc.response.get("Error", {}).get("Code", "")
            code, retryable = _AWS_ERROR_MAP.get(aws_code, ("AWS_ERROR", False))
            logger.error("Translate lỗi ở đoạn %d/%d: %s", idx, total, aws_code)
            raise TranslateServiceError(
                code, "Amazon Translate lỗi ({}) ở đoạn {}/{}.".format(aws_code, idx, total),
                retryable) from exc
        except BotoCoreError as exc:
            logger.error("Lỗi kết nối Translate ở đoạn %d/%d: %s", idx, total, exc)
            raise TranslateServiceError(
                "AWS_CONNECTION_ERROR", "Không kết nối được Amazon Translate.", True) from exc
        return resp["TranslatedText"], resp.get("SourceLanguageCode")

    def _split_text(self, text, max_bytes):
        """Cắt text thành các đoạn <= max_bytes (UTF-8), giữ nguyên thứ tự và ký tự
        xuống dòng. Ưu tiên cắt theo dòng, rồi theo câu, cuối cùng cắt cứng theo byte
        (không cắt giữa ký tự đa byte)."""
        units = []
        for line in text.splitlines(keepends=True):
            if _utf8_len(line) <= max_bytes:
                units.append(line)
                continue
            for sentence in _SENTENCE_PATTERN.findall(line):
                if _utf8_len(sentence) <= max_bytes:
                    units.append(sentence)
                else:
                    units.extend(self._hard_split(sentence, max_bytes))

        chunks, current, current_bytes = [], [], 0
        for unit in units:
            size = _utf8_len(unit)
            if current and current_bytes + size > max_bytes:
                chunks.append("".join(current))
                current, current_bytes = [], 0
            current.append(unit)
            current_bytes += size
        if current:
            chunks.append("".join(current))
        return chunks or [""]

    @staticmethod
    def _hard_split(s, max_bytes):
        """Cắt cứng theo byte, lùi về ranh giới ký tự UTF-8."""
        data = s.encode("utf-8")
        out, i = [], 0
        while i < len(data):
            j = min(i + max_bytes, len(data))
            while j < len(data) and (data[j] & 0xC0) == 0x80:
                j -= 1
            out.append(data[i:j].decode("utf-8"))
            i = j
        return out

    translate_text = translateText
    detect_language = detectLanguage
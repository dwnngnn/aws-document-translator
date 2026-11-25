# Quy ước backend (bắt buộc tuân thủ)

Code của Ngân (`lambda_function.py`, `orchestration_service.py`) đã được viết và chạy thử theo đúng các quy ước dưới đây.
Mọi file khác phải **khớp tên, tham số, kiểu trả về**. Nếu cần đổi, báo Ngân trước.

## 1. Quy ước chung

| Mục | Quy ước |
| --- | --- |
| Python | 3.12, import theo gốc `backend/`: `from models.x import ...`, `from services.x import ...`, `from utils import aws_config` |
| Khởi tạo service | Mọi service **không nhận tham số** (`StorageService()`), tự đọc cấu hình từ `utils/aws_config.py` |
| ID | `uuid.uuid4().hex` (32 ký tự, không có dấu gạch) |
| Thời gian | Chuỗi ISO 8601 UTC: `datetime.now(timezone.utc).isoformat()` (DynamoDB không có kiểu datetime) |
| Mã ngôn ngữ | Mã của Amazon Translate: `vi, en, ja, ko, zh, fr, de, es`. `auto` chỉ dùng cho dịch văn bản |
| Lỗi | Service **không nuốt lỗi**: để `ClientError` của boto3 nổi lên, `OrchestrationService` sẽ xử lý |
| `user_id` | Đã được làm sạch bằng `file_helper.safe_name()` trước khi vào service (chỉ còn chữ, số, `.`, `_`, `-`) |
| Biến môi trường | Chỉ đọc trong `utils/aws_config.py`, nơi khác import từ đó |

### Trạng thái (`TranslationStatus`)

Là `class TranslationStatus(str, Enum)`, **giá trị chuỗi trùng với tên**, nằm trong `models/translation_request.py`:

`WAITING_UPLOAD, UPLOADED, SUBMITTED, IN_PROGRESS, COMPLETED, COMPLETED_WITH_ERROR, FAILED, STOPPED`

### Quy ước S3 key

```
input/{user_id}/{request_id}/{file_name}    file người dùng upload (có đúng 4 đoạn)
output/{user_id}/{request_id}/              Amazon Translate ghi kết quả vào đây
```

Tạo key bằng hàm trong `utils/file_helper.py` (`build_input_key`, `build_input_prefix`, `build_output_prefix`), không tự ghép chuỗi.

### Cấu hình AWS cần thống nhất

- S3 event trigger **chỉ cho prefix `input/`** (nếu không sẽ bị lặp vô hạn do file kết quả ghi vào `output/`).
- DynamoDB: partition key `user_id` (S), sort key `request_id` (S).
- Định dạng tài liệu hỗ trợ: `txt, html, docx, pptx, xlsx`, tối đa 10 MB (xem `file_helper.py`).

---

## 2. Trâm

### `models/translation_request.py`

```python
class TranslationStatus(str, Enum): ...   # xem mục 1

@dataclass
class TranslationRequest:
    request_id: str
    user_id: str
    request_type: str                 # "TEXT" hoặc "DOCUMENT"
    source_language: str
    target_language: str
    status: TranslationStatus = TranslationStatus.WAITING_UPLOAD
    input_text: str = ""
    document_id: str = None
    error_message: str = None
    created_at: str = None
    updated_at: str = None

    def validate_request(self) -> bool: ...
```

`validate_request()` trả `True/False`, khi sai phải **gán lý do (tiếng Việt) vào `self.error_message`**. Quy tắc:

- `TEXT`: `input_text` không rỗng và tối đa 5000 ký tự.
- `DOCUMENT`: không kiểm tra `input_text`, cần có `document_id`.
- Cả hai: `target_language` có trong danh sách hỗ trợ, `target_language` khác `source_language`.

### `services/translate_service.py`

```python
translate_text(text, source_language, target_language) -> str
start_document_job(input_uri, output_uri, source_language, target_language,
                   content_type, data_access_role_arn) -> str     # trả job_id
describe_document_job(job_id) -> dict    # {"status": <JobStatus>, "message": <Message hoặc None>}
stop_document_job(job_id) -> None
```

- Gọi tham số **bằng tên** (orchestration dùng keyword argument cho `start_document_job`).
- `input_uri` là **prefix kết thúc bằng `/`** (`s3://bucket/input/u/r/`), `output_uri` cũng là prefix.
- Khi gọi `start_text_translation_job`: `TargetLanguageCodes=[target_language]` (là **list**), bắt buộc có `JobName` và `ClientToken` (dùng `uuid4().hex`).
- `describe_document_job` lấy `status` từ `TextTranslationJobProperties["JobStatus"]` và trả nguyên (`SUBMITTED, IN_PROGRESS, COMPLETED, COMPLETED_WITH_ERROR, FAILED, STOP_REQUESTED, STOPPED`).

### `tests/event-s3-upload.json`

Event S3 có `Records[0].eventSource = "aws:s3"`, `s3.bucket.name` và `s3.object.key` dạng `input/{user_id}/{request_id}/{file}` (key được URL-encode, khoảng trắng thành `+`).

---

## 3. Trang

### `models/document.py`

```python
@dataclass
class Document:
    document_id: str
    request_id: str
    file_name: str
    file_type: str            # phần mở rộng, ví dụ "docx"
    content_type: str         # MIME type
    file_size_bytes: int
    input_s3_key: str
    output_s3_prefix: str
    translate_job_id: str = None
    created_at: str = None
```

### `services/storage_service.py`

```python
generate_upload_url(key, content_type, expiration) -> str      # presigned PUT
generate_download_url(key, expiration) -> str                  # presigned GET
get_object_metadata(key) -> dict                               # {"size_bytes": int, "content_type": str}
find_translated_file(prefix) -> str | None                     # S3 key của file đã dịch
```

- Dùng `boto3.client("s3", region_name=..., config=Config(signature_version="s3v4"))` để link presigned hoạt động.
- `generate_upload_url` phải đưa `ContentType` vào `Params`: frontend sẽ `PUT` với đúng header `Content-Type` này.
- `find_translated_file`: dùng `list_objects_v2` theo `prefix`, **bỏ qua** key chứa `/details/` (thư mục báo cáo do Translate tự tạo), trả `None` nếu chưa có.
- Kết quả của Translate nằm ở `output/{user}/{request}/<mã job>/{target}.{tên file gốc}`.

### `aws-setup/iam-policy.json`

Cần **hai** role:

1. **Role của Lambda**: S3 `GetObject/PutObject/ListBucket` trên `input/*` và `output/*`; `translate:TranslateText`, `StartTextTranslationJob`, `DescribeTextTranslationJob`, `StopTextTranslationJob`; DynamoDB `PutItem/GetItem/Query/UpdateItem` trên bảng; `iam:PassRole` cho role bên dưới; ghi CloudWatch Logs.
2. **Role cho Amazon Translate** (đây là `TRANSLATE_ROLE_ARN`, trust `translate.amazonaws.com`): đọc `input/*`, ghi `output/*`.

---

## 4. Thư

### `utils/aws_config.py`

Các hằng số (đọc từ `os.environ`):

```python
AWS_REGION
S3_BUCKET
DYNAMODB_TABLE
TRANSLATE_ROLE_ARN        # orchestration_service.py import trực tiếp tên này
```

### `services/history_service.py`

```python
save_history(data: dict) -> None                           # put_item, data đã có user_id và request_id
get_history(user_id) -> list[dict]                         # query theo user_id, xử lý phân trang
get_history_item(user_id, request_id) -> dict | None
update_history(user_id, request_id, data: dict) -> None    # cập nhật đúng các key có trong data
```

- Dùng `boto3.resource("dynamodb").Table(aws_config.DYNAMODB_TABLE)`.
- `update_history` phải dùng `ExpressionAttributeNames` cho **mọi** tên thuộc tính (`#k0, #k1...`) vì `status` là từ khóa dành riêng của DynamoDB, viết thẳng sẽ lỗi.
- Trả về dict thường (số có thể là `Decimal`, `response_helper` đã xử lý).

### Các trường của một dòng lịch sử

| Nhóm | Trường |
| --- | --- |
| Chung | `user_id`, `request_id`, `request_type`, `source_language`, `target_language`, `status`, `error_message`, `created_at`, `updated_at` |
| Văn bản | `input_text`, `word_count`, `result_id`, `translated_text`, `completed_at` |
| Tài liệu | `document_id`, `file_name`, `file_type`, `content_type`, `file_size_bytes`, `input_s3_key`, `output_s3_prefix`, `translate_job_id`, `result_id`, `translated_s3_key`, `completed_at` |

Trường nào không có giá trị thì không được lưu (không có `null`), nên khi đọc phải dùng `item.get(...)`.

---

## 5. Cả nhóm: `requirements.txt`

```
boto3>=1.34
```

Lambda đã có sẵn `boto3`, file này dùng để chạy thử ở máy cá nhân. Nếu viết test, thêm `pytest` và `moto`.

## 6. Hợp đồng API với frontend (Ngân)

| API | Body / query | Trả về |
| --- | --- | --- |
| `POST /translate-text` | `text, source_language, target_language` | `request_id, status, translated_text, word_count` |
| `POST /documents/upload-url` | `file_name, file_size_bytes, source_language, target_language` | `request_id, document_id, status, upload_url, upload_method ("PUT"), upload_headers, expires_in` |
| `GET /requests/{id}/status` | | `request_id, status, error_message, download_ready, updated_at` |
| `GET /requests/{id}/download-url` | | `request_id, file_name, download_url, expires_in` |
| `GET /history` | | `{"items": [...]}` mới nhất lên đầu |

Lỗi trả về dạng `{"error": "..."}` với mã 400 (dữ liệu sai), 404 (không tìm thấy), 409 (bản dịch chưa xong), 500 (lỗi hệ thống).

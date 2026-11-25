# AWS Document Translator

Ứng dụng web dịch văn bản và tài liệu bằng Amazon Translate.
Kiến trúc: `Frontend (HTML/CSS/JS) → API Gateway → Lambda → S3 / Translate / DynamoDB`.

## Cấu trúc

```
frontend/   Giao diện (index.html, style.css, app.js)
backend/    Lambda Python (lambda_function.py + models/ services/ utils/)
aws-setup/  IAM policy, S3 CORS, schema DynamoDB
tests/      Event mẫu để test Lambda
```

## Luồng hoạt động

**Dịch văn bản** (đồng bộ)
`POST /translate-text` → Orchestration gọi Translate → lưu history → trả bản dịch.

**Dịch tài liệu** (bất đồng bộ, 3 pha)
1. `POST /documents/upload-url`: tạo request `WAITING_UPLOAD`, trả link upload (presigned PUT).
2. Frontend `PUT` file thẳng lên S3 `input/{user}/{request}/{file}`. S3 gửi event → Lambda
   chạy `process_uploaded_document` → `UPLOADED` → `SUBMITTED` (đã tạo job Translate).
3. Frontend gọi `GET /requests/{id}/status` mỗi 3–5 giây. Khi `COMPLETED` gọi
   `GET /requests/{id}/download-url` để lấy link tải bản dịch.

Trạng thái: `WAITING_UPLOAD → UPLOADED → SUBMITTED → IN_PROGRESS → COMPLETED`
(hoặc `COMPLETED_WITH_ERROR`, `FAILED`, `STOPPED`).

## API

| Method | Đường dẫn | Mô tả |
| --- | --- | --- |
| POST | `/translate-text` | Dịch văn bản |
| POST | `/documents/upload-url` | Tạo request và link upload |
| GET | `/requests/{id}/status` | Xem trạng thái dịch tài liệu |
| GET | `/requests/{id}/download-url` | Lấy link tải bản dịch |
| GET | `/history` | Lịch sử dịch của người dùng |

Danh tính người dùng lấy từ Cognito (nếu có), nếu không thì từ header `x-user-id`.

## Biến môi trường (Lambda)

`S3_BUCKET`, `DYNAMODB_TABLE`, `TRANSLATE_ROLE_ARN` (role cho Translate đọc/ghi S3), `AWS_REGION`.
Được đọc tập trung trong `backend/utils/aws_config.py`.

## Lưu ý cấu hình AWS

- **S3 event trigger** chỉ đặt cho prefix `input/`. Nếu đặt cho cả bucket, kết quả ghi vào `output/`
  sẽ kích hoạt Lambda lại và tạo vòng lặp.
- Dịch tài liệu **không hỗ trợ tự nhận diện ngôn ngữ nguồn** (chỉ dịch văn bản mới có `auto`).
- Frontend khi upload phải gửi đúng header `Content-Type` mà API trả về trong `upload_headers`.
- Bảng DynamoDB: partition key `user_id`, sort key `request_id`.

## Quy ước cho các bạn viết code

Đọc kỹ [`backend/CONTRACT.md`](backend/CONTRACT.md): tên hàm, tham số, kiểu trả về và quy ước S3/DynamoDB
mà từng người phải tuân theo.

## Phân công

| Phần | Người làm |
| --- | --- |
| README, frontend, `lambda_function.py`, `translation_result.py`, `orchestration_service.py` | Ngân |
| `translation_request.py`, `translate_service.py`, `tests/event-s3-upload.json` | Trâm |
| `document.py`, `storage_service.py`, `iam-policy.json` | Trang |
| `history_service.py`, `aws_config.py` | Thư |
| `requirements.txt` | Cả nhóm |

"""Tạo response chuẩn (JSON + CORS) cho API Gateway."""
import json
from decimal import Decimal

CORS_HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",  # TODO: đổi thành domain frontend khi deploy
    "Access-Control-Allow-Headers": "Content-Type,Authorization,x-user-id",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}


def _json_default(value):
    # DynamoDB trả số dưới dạng Decimal, json.dumps không tự xử lý được.
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return str(value)


def _build(status_code: int, payload: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(payload, ensure_ascii=False, default=_json_default),
    }


def success(data: dict, status_code: int = 200) -> dict:
    return _build(status_code, data)


def error(message: str, status_code: int = 400) -> dict:
    return _build(status_code, {"error": message})

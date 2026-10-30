"""HTTP and logging helpers for the Orders handler."""
import json
import time
from decimal import Decimal


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def plain(value):
    """Convert DynamoDB Decimals to int/float so json.dumps works."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    return value


def response(status, body, headers=None):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **(headers or {})},
        "body": json.dumps(body),
    }


def error_response(err, request_id):
    return response(
        err.status,
        {"code": err.code, "message": err.message, "requestId": request_id},
    )


def log(request_id, operation, result, started, **extra):
    print(
        json.dumps(
            {
                "requestId": request_id,
                "operation": operation,
                "result": result,
                "durationMs": round((time.monotonic() - started) * 1000, 1),
                **extra,
            }
        )
    )

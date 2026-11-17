class JsshError(Exception):
    """User-facing CLI failure. Message must never contain secrets."""


class ConfigError(JsshError):
    pass


class AuthError(JsshError):
    pass


class ApiError(JsshError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Cancelled(JsshError):
    pass

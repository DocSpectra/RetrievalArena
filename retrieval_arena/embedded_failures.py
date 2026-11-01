"""Closed retry/circuit policy. No provider exception messages are persisted."""
def classify(exc):
    name=type(exc).__name__; message=str(exc)
    # Shared transports wrap SDK errors with their class and HTTP code only.
    kinds=[name]+message.split(':')
    if 'AuthenticationError' in kinds or '401' in kinds:return 'authentication',False,True
    if 'PermissionDeniedError' in kinds or '403' in kinds:return 'permission',False,True
    if 'RateLimitError' in kinds or '429' in kinds:return 'rate_limit',True,False
    if name in ('TimeoutError','APITimeoutError') or 'APITimeoutError' in kinds:return 'timeout',True,False
    if any(k in kinds for k in ('APIConnectionError','InternalServerError','ServiceUnavailableError')):
        return 'service',True,False
    if any(k.isdigit() and 500<=int(k)<=599 for k in kinds):return 'service',True,False
    if any(k in kinds for k in ('BadRequestError','NotFoundError','UnprocessableEntityError')):
        return 'unsupported_request_or_model',False,True
    if isinstance(exc,(ValueError,UnicodeError)) or name=='ValidationError':return 'response_validation',True,False
    return 'independent_technical_failure',False,False

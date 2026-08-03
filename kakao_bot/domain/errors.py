"""Domain errors raised by Kakao bot use cases."""


class RoomNotFoundError(LookupError):
    """Raised when a room has not been discovered."""


class RoomNotApprovedError(PermissionError):
    """Raised when subscription settings are changed before room approval."""

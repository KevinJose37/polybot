"""
Orderbook sequence gap reconciliation.
"""
import structlog
from bot.orderbook.book_state import BookState

logger = structlog.get_logger(__name__)

class SequenceGapError(Exception):
    """Raised when an out-of-order delta sequence is detected."""
    pass

def check_sequence(expected: int | None, actual: int) -> None:
    """
    Checks if the actual sequence number matches expected.
    If expected is None, we accept any as the first.
    """
    if expected is not None and actual != expected:
        logger.warning("sequence_gap_detected", expected=expected, actual=actual)
        raise SequenceGapError(f"Gap detected: expected {expected}, got {actual}")

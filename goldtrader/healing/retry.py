"""Retry with exponential backoff + jitter."""
from __future__ import annotations

import functools
import random
import time
from typing import Callable, Type

from ..logging_setup import get_logger

log = get_logger("goldtrader.retry")


def with_backoff(
    exceptions: tuple[Type[BaseException], ...] = (Exception,),
    max_tries: int = 4,
    base: float = 1.0,
    cap: float = 30.0,
    jitter: float = 0.3,
):
    """Decorator: retry the wrapped call on `exceptions` with capped exp backoff.

    Deterministic-ish: jitter uses random but bounded; safe for non-cryptographic use.
    """

    def deco(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc: BaseException | None = None
            for attempt in range(1, max_tries + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:  # noqa: PERF203
                    last_exc = exc
                    if attempt == max_tries:
                        break
                    delay = min(cap, base * (2 ** (attempt - 1)))
                    delay += random.uniform(0, jitter * delay)
                    log.warning(
                        "retry",
                        fn=getattr(fn, "__name__", str(fn)),
                        attempt=attempt,
                        max_tries=max_tries,
                        delay=round(delay, 2),
                        error=str(exc),
                    )
                    time.sleep(delay)
            assert last_exc is not None
            raise last_exc

        return wrapper

    return deco

"""Wait for durable SQL work even if the calling async request is canceled."""

from __future__ import annotations

import asyncio
from functools import partial


async def durable_call(function, *args, **kwargs):
    task = asyncio.create_task(asyncio.to_thread(partial(function, *args, **kwargs)))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # to_thread is not killable: do NOT abandon a commit and advertise retry.
        await task
        raise

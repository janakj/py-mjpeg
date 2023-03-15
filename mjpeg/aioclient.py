from __future__ import annotations

import traceback
import asyncio
from collections import deque

import aiohttp

from mjpeg import aread_mjpeg_frame
from .client import Buffer


__all__ = ['AioMJPEGClient']


class AioMJPEGClient:
    """An asyncronous MJPEG streaming client.

    This is an MJPEG client implemented using :mod:`asyncio`.
    Given a URL, the streamer will open the URL as an MJPEG stream and read
    frames from it until the stream is closed or the streamer is stopped.
    When the stream closes or an error occurs, the streamer automatically
    reconnects after :attr:`reconnect_interval` until the number of attempts
    reaches :attr:`reconnect_limit` (if set).

    Use as an :term:`asynchronous context manager` is supported (in an
    :keyword:`async with` block). Note that this method does not allow for
    an existing :class:`~aiohttp.ClientSession` to be used as described in
    the :meth:`open` method.

    >>> import asyncio
    >>> from mjpeg.aioclient import AioMJPEGClient
    >>> url = 'http://{your_stream_url}'
    >>> async def run_client():
    ...     async with AioMJPEGClient(url) as client:
    ...         ...
    >>> asyncio.run(run_client())


    Use as an :term:`asynchronous iterator` is also supported (in an
    :keyword:`async for` statement). The loop will continue until the stream is
    closed.

    >>> async def run_client():
    ...     async with AioMJPEGClient(url) as client:
    ...         async for bfr in client:
    ...             # do something with the buffer
    ...             ...
    >>> asyncio.run(run_client())

    """

    url: str
    """The stream url"""

    log_interval: int|float
    """Interval between updating the :attr:`fps` estimate"""

    reconnect_interval: int|float
    """Time (in seconds) to wait before reconnecting"""

    reconnect_limit: int|None
    """Maximum number of connection attempts to make before closing.
    ``None`` will reconnect indefinitely until :meth:`close` is called.
    """

    overruns: int
    """Number of frames received with no buffer available to write to"""

    reconnects: int
    """Current number of reconnects"""

    in_overrun: bool
    """Flag indicating a buffer overrun state. This will be ``True`` if no
    write buffers are available for received frames.
    """

    frames: int
    """Total number of frames received (whether discarded or not)"""

    discarded_frames: int
    """Number of frames skipped while in the :attr:`overrun <in_overrun>` state"""

    fps: float
    """Calculated estimate of the stream framerate"""

    exc: BaseException|None
    """If an :class:`Exception` was caught while connecting to or  processing
    the stream, it will be available here. Otherwise ``None``
    """

    exc_notify: asyncio.Condition
    """A :class:`Condition <asyncio.Condition>` object that can be used to
    :term:`await` for an Exception (available as :attr:`exc`).
    Waiters will also be notified when the stream ends (for graceful shutdown).
    """

    is_open: bool
    """Flag indicating if the stream is currently open"""

    _stop_loops: bool
    """Internal variable used to control internal processing loops"""

    _incoming: deque
    """Container for :class:`.client.Buffer` objects to write received
    frames to
    """

    """Container for :class:`.client.Buffer` objects with received frame data
    """
    _outgoing: asyncio.Queue
    def __init__(
        self, 
        url: str, 
        log_interval: int|float = .5,
        reconnect_interval: int|float = 5,
        reconnect_limit: int|None = None,
    ):
        self.url = url
        self.log_interval = log_interval
        self.reconnect_interval = reconnect_interval
        self.reconnect_limit = reconnect_limit
        self._incoming = deque()
        self._outgoing = asyncio.Queue()
        self._stop_loops = False
        self.overruns = 0
        self.reconnects = 0
        self.in_overrun = False
        self.frames = 0
        self.discarded_frames = 0
        self.loop = asyncio.get_event_loop()
        self._run_task = None
        self.exc = None
        self.exc_notify = asyncio.Condition()
        self.is_open = False

    async def open(self, session: aiohttp.ClientSession|None = None):
        """Open the client and begin streaming in a background task

        Arguments:
            session: If provided, an existing :class:`aiohttp.ClientSession`
                to use. This would be recommended for multiple streams.  If
                ``None``, a new instance will be created

        Note that if *session* is provided, it will be left open. This should be
        done by the caller.

        If *session* is not provided and an instance is created however, it will
        be closed with the client.
        """
        if self.is_open or self._run_task is not None:
            raise RuntimeError('{self} already open')
        self._run_task = asyncio.create_task(self.run(session))

    async def close(self):
        """Close the client and stop any background tasks
        """
        self._stop_loops = True
        t = self._run_task
        try:
            if t is not None:
                self._run_task = None
                if asyncio.current_task() is not t:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
                    assert not self.is_open
        finally:
            await self._outgoing.put(None)

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, *args):
        await self.close()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._stop_loops:
            raise StopAsyncIteration
        buf = await self.dequeue_buffer()
        if buf is None:
            raise StopAsyncIteration
        return buf

    def request_buffers(self, length: int, count: int) -> list[Buffer]:
        """Shortcut to create :class:`~.client.Buffer` instances

        Arguments:
            length: The buffer length
            count: Number of instances to create

        """
        rv = []
        for i in range(0, count):
            rv.append(Buffer(length))
        return rv

    def enqueue_buffer(self, buf: Buffer):
        """Add a :class:`~.client.Buffer` instance to be written to
        """
        self._incoming.append(buf)

    async def dequeue_buffer(self) -> Buffer|None:
        """Block until a :class:`~.client.Buffer` is available to be read from
        and return it.

        If the stream is closed (by either the :meth:`close` method or
        :attr:`reconnect_limit`), ``None`` is returned.  This allows the caller
        to handle graceful shutdown without using timeout logic
        (such as :func:`asyncio.wait_for`).

        If successful, the returned instance will contain data received
        from the stream.

        .. note::

            This is called behind the scenes within an :keyword:`async for` loop.
            Unexpected behavior may occur if this method is called manually during
            the loop.
        """
        buf = await self._outgoing.get()
        self._outgoing.task_done()
        return buf

    def _init_fps(self):
        self.fps = 0
        self._frame = 0
        self._start = int(self.loop.time())
        self._cur = self._prev = self._start

    def _update_fps(self):
        self._frame += 1
        self._cur = int(self.loop.time())
        if self._cur >= self._prev + self.log_interval:
            self.fps = int(self._frame / self.log_interval)
            self._prev = self._cur
            self._frame = 0

    async def process_stream(self, resp: aiohttp.ClientResponse):
        reader = aiohttp.MultipartReader.from_response(resp)

        # Can be refactor to use changes in PR #2 if merged
        if reader.stream._boundary.startswith(b'----'):
            reader.stream._boundary = reader.stream._boundary[2:]

        seq = 0

        while not self._stop_loops:
            try:
                buf = self._incoming.pop()
                mem = buf.data
                length = buf.length
                self.in_overrun = False
            except IndexError:
                buf = None
                mem = None
                length = 0
                if not self.in_overrun:
                    self.overruns += 1
                    self.in_overrun = True

            try:
                part = await asyncio.wait_for(reader.next(), timeout=10)
            except asyncio.TimeoutError:
                raise

            if self._stop_loops:
                break

            if part is None:
                raise EOFError('End of stream reached')
            timestamp, clen = await aread_mjpeg_frame(part, mem, length)
            self._update_fps()
            self.frames += 1

            if buf is not None and length >= clen:
                buf.timestamp = timestamp
                buf.used = clen
                buf.seq = seq
                await self._outgoing.put(buf)
            else:
                self.discarded_frames += 1

            seq += 1

    def print_stats(self):
        print('MJPEGClient:')
        print('  URL:            : %s' % self.url)
        print('  FPS             : %d' % self.fps)
        print('  Buffer overruns : %d' % self.overruns)
        print('  Reconnects      : %d' % self.reconnects)
        print('  Total frames    : %d' % self.frames)
        print('  Discarded frames: %d' % self.discarded_frames)
        print('  Buffer queue    : %d' % len(self._incoming))

    async def run(self, session: aiohttp.ClientSession|None = None):
        self.is_open = True
        self._stop_loops = False
        self._init_fps()

        async def set_exc(exc: BaseException|None):
            async with self.exc_notify:
                self.exc = exc
                self.exc_notify.notify_all()
        
        if session is None:
            session = aiohttp.ClientSession()
            owns_session = True
        else:
            owns_session = False

        try:
            while not self._stop_loops:
                if self.exc is not None:
                    await set_exc(None)
                try:
                    async with session.get(self.url) as resp:
                        resp.raise_for_status()
                        await self.process_stream(resp)
                except EOFError as e:
                    pass
                except Exception as exc:
                    traceback.print_exc()
                    await set_exc(exc)

                if not self._stop_loops:
                    limit = self.reconnect_limit
                    if limit is not None and self.reconnects >= limit:
                        break
                    await asyncio.sleep(self.reconnect_interval)
                    self.reconnects += 1
        finally:
            self.is_open = False
            if owns_session:
                await session.close()
            await set_exc(None)

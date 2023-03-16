from __future__ import annotations

from time import time
import asyncio
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    import aiohttp

__all__ = [
    'ProtoError',
    'open_mjpeg_stream',
    'read_mjpeg_frame']


class ProtoError(Exception):
    pass


def read_header_line(stream):
    '''Read one header line within the stream.

    The headers come right after the boundary marker and usually contain
    headers like Content-Type and Content-Length which determine the type and
    length of the data portion.
    '''
    return stream.readline().decode('utf-8').strip()


def read_headers(stream, boundary):
    '''Read and return stream headers.

    Each stream data packet starts with an empty line, followed by a boundary
    marker, followed by zero or more headers, followed by an empty line,
    followed by actual data. This function reads and parses the entire header
    section. It returns a dictionary with all the headers. Header names are
    converted to lower case. Each value in the dictionary is a list of header
    fields values.
    '''
    l = read_header_line(stream)
    if l == '':
        l = read_header_line(stream)
    if l != boundary:
        raise ProtoError('Boundary string expected, but not found')

    headers = {}
    while True:
        l = read_header_line(stream)
        # An empty line indicates the end of the header section
        if l == '':
            break

        # Parse the header into lower case header name and header body
        i = l.find(':')
        if i == -1:
            raise ProtoError('Invalid header line: ' + l)
        name = l[:i].lower()
        body = l[i+1:].strip()

        lst = headers.get(name, list())
        lst.append(body)
        headers[name] = lst

    return headers


def skip_data(stream, left):
    while left:
        rv = stream.read(left)
        if len(rv) == 0 and left:
            raise ProtoError('Not enough data in chunk')
        left -= len(rv)


async def askip_data(stream: aiohttp.BodyPartReader, left: int):
    buf = bytearray(range(left))
    await aread_data(buf, stream, left)


def read_data(buf, stream, length):
    '''Read the give number of bytes into an existing bytearray buffer.

    The caller must supply the memory buffer and is responsible for ensuring
    that the buffer is big enough. This function will read from the response
    object repeatedly until it has read 'length' bytes. Throws an exception if
    the response ends prematurely.
    '''
    v = memoryview(buf)[:length]
    while len(v):
        n = stream.readinto(v)
        if n == 0 and len(v):
            raise ProtoError('Not enough data in chunk')
        v = v[n:]
    return buf


async def aread_data(buf, stream: aiohttp.BodyPartReader, length: int):
    chunk_size = 8192
    if chunk_size <= length:
        while chunk_size <= length and chunk_size != 1:
            chunk_size = chunk_size // 2

    i = 0
    remaining = length
    while remaining > 0:
        if remaining < chunk_size:
            chunk_size = remaining
        data = await stream.read_chunk(chunk_size)
        n = len(data)
        remaining -= n
        if n > 0:
            buf[i:n] = data
        if n == 0 and remaining > 0:
            raise ProtoError('Not enough data in chunk')
        i += n
    return buf


def parse_content_length(headers):
    # Parse and check Content-Length. The header must be present in
    # each chunk, otherwise we wouldn't know how much data to read.
    clen = headers.get('content-length', None)
    try:
        return int(clen[0])
    except (ValueError, TypeError):
        raise ProtoError('Invalid or missing Content-Length')


def check_content_type(headers, type_):
    ctype = headers.get('content-type', None)
    if ctype is None:
        raise ProtoError('Missing Content-Type header')
    ctype = ctype[0]

    i = ctype.find(';')
    if i != -1:
        ctype = ctype[:i]

    if ctype != type_:
        raise ProtoError('Wrong Content-Type: %s' % ctype)

    return True


def open_mjpeg_stream(stream):
    '''Open an MJPEG stream.

    Given a response from urllib, ensure that all the headers are correct,
    obtain the boundary string that delimits frames and return it. Raises
    ProtoError on errors.
    '''
    if stream.status != 200:
        raise ProtoError('Invalid response from server: %d' % stream.status)
    h = stream.info()

    boundary = h.get_param('boundary', header='content-type', unquote=True)
    if boundary is None:
        raise ProtoError('Content-Type header does not provide boundary string')
    boundary = '--' + boundary

    return boundary


def read_mjpeg_frame(stream, boundary, buf, length, skip_big=True):
    '''Read one MJPEG frame from given stream.

    The stream must be a response object returned by urllib. This function
    processes exactly one frame. End of stream events are detected when the
    length of the next frame is 0. Ensures that Content-Type is present and
    set to 'image/jpeg'.

    If skip_big is set to True, frames bigger than the destination buffer are
    silently skipped. The function reads the data, but does not store it in
    the provided buffer. If the flag is set to False, a ProtoError exception
    will be raised.

    The function returns a tuple (timestamp, clen) where timestamp is the
    timestamp of the first byte of the frame and clen is the total number of
    bytes in the frame.

    To skip data when buffer is not available, simply pass buf=None, length=0,
    skip_big=True and the next frame will be silently poped from the stream
    and discarded.
    '''
    hdr = read_headers(stream, boundary)

    clen = parse_content_length(hdr)
    if clen == 0:
        raise EOFError('End of stream reached')

    if clen > length and not skip_big:
        raise ProtoError('Received chunk too big: %d' % clen)

    check_content_type(hdr, 'image/jpeg')

    timestamp = time()
    if length >= clen:
        read_data(buf, stream, clen)
    else:
        skip_data(stream, clen)

    return (timestamp, clen)

async def aread_mjpeg_frame(
    stream: aiohttp.BodyPartReader,
    buf,
    length: int,
    skip_big: bool = True
) -> tuple[float, int]:
    '''Read one MJPEG frame from given stream asynchronously.

    This function processes exactly one frame.
    End of stream events are detected when the length of the next frame is 0.
    Ensures that Content-Type is present and set to 'image/jpeg'.

    Arguments:
        stream: An :class:`aiohttp.BodyPartReader` instance produced by
            :class:`aiohttp.MultipartReader`
        buf: An instance of :class:`mjpeg.Buffer` to store the frame data in
        length: Maximum number of bytes to read
        skip_big: If ``True`` and the frame data is larger than the given *length*,
            the frame is skipped and the buffer is not written to.
            If ``False`` and frame data is larger than *length*,
            a :class:`ProtoError` is raised. (default is ``True``)

    Returns
    -------
    timestamp : float
        POSIX timestamp of the first byte of the frame
    clen : int
        Total number of bytes in the frame


    To skip data when buffer is not available, simply pass buf=None, length=0,
    skip_big=True and the next frame will be silently poped from the stream
    and discarded.
    '''

    loop = asyncio.get_event_loop()
    clen = stream._length
    assert clen is not None

    if clen == 0:
        raise EOFError('End of stream reached')

    if clen > length and not skip_big:
        raise ProtoError('Received chunk too big: %d' % clen)

    ctype = stream.headers['Content-type']
    if ctype != 'image/jpeg':
        raise ProtoError('Wrong Content-Type: %s' % ctype)

    timestamp = loop.time()

    if length >= clen:
        await aread_data(buf, stream, clen)
    else:
        await askip_data(stream, clen)

    return (timestamp, clen)

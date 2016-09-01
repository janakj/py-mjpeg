import traceback
import urllib.request
from   time        import time, sleep
from   queue       import Queue
from   threading   import Thread
from   collections import deque
from   mjpeg       import open_mjpeg_stream, read_mjpeg_frame


__all__ = ['Buffer', 'MJPEGClient']


class Buffer(object):
    '''Memory buffer object used by the MJPEG streamer.

    Attribute data contains a bytearray buffer. Attribute length contains the
    total number of bytes that can be stored in the bytearray. This value can
    also be obtained via len(bytearray).

    Attribute used is set to the number of bytes currently stored in the
    buffer. Obviously, used <= length.

    Attribute timestamp is set to the timestamp (obtained via time.time()) of
    the first byte in the buffer. For outgoing data the timestamp indicates to
    the sender that the frame is not to be sent before this time.

    The attribute sequence contains the sequence number of the frame within a
    stream. The number is set to 0 when a new stream is opened and incremented
    for each frame, even if frames are skipped. This is useful for detecting
    buffer overruns.
    '''
    def __init__(self, length):
        self.length = length
        self.used = 0
        self.timestamp = 0
        self.sequence = 0
        self.data = bytearray(length)



class MJPEGClient(Thread):
    '''A threaded MJPEG streaming client.

    This thread implements a MJPEG client. Given a URL, the streamer will open
    the URL as a MJPEG stream and read frames from the stream until the stream
    is closed or the streamer is stopped. When the stream closes or an error
    occurs, the streamer automatically reconnects after 'reconnect_interval'.
    '''
    def __init__(self, url, log_interval=5, reconnect_interval=5):
        Thread.__init__(self, None)
        self.url = url
        self.log_interval = log_interval
        self.reconnect_interval = reconnect_interval
        self.daemon = True
        self._incoming = deque()
        self._outgoing = Queue()

        # Keep track of the total number of overruns in this attribute
        self.overruns = 0
        # Keep track of the total number of reconnect attempts in this attribute
        self.reconnects = 0

        # When set to true, the streamer is out of buffers and is throwing away received frames.
        self.in_overrun = False

        # The total number of received frames across stream reconnects
        self.frames = 0
        # The total number of discarded frames across stream reconnects
        self.discarded_frames = 0

    def stop(self):
        self.stop = True

    def request_buffers(self, length, count):
        rv = []
        for i in range(0, count):
            rv.append(Buffer(length))
        return rv

    def enqueue_buffer(self, buf):
        self._incoming.append(buf)

    def dequeue_buffer(self, *args, **kwargs):
        buf = self._outgoing.get(*args, **kwargs)
        self._outgoing.task_done()
        return buf

    def _init_fps(self):
        self.fps = 0
        self._frame = 0
        self._start = int(time())
        self._cur = self._prev = self._start

    def _update_fps(self):
        self._frame += 1
        self._cur = int(time())
        if self._cur >= self._prev + self.log_interval:
            self.fps = int(self._frame / self.log_interval)
            self._prev = self._cur
            self._frame = 0

    def process_stream(self, stream):
        boundary = open_mjpeg_stream(stream)
        seq = 0

        while True:
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

            timestamp, clen = read_mjpeg_frame(stream, boundary, mem, length)
            self._update_fps()
            self.frames += 1

            if buf is not None and length >= clen:
                buf.timestamp = timestamp
                buf.used = clen
                buf.seq = seq
                self._outgoing.put(buf)
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

    def run(self):
        self.stop = False
        self._init_fps()

        while not self.stop:
            try:
                with urllib.request.urlopen(self.url) as s:
                    self.process_stream(s)
            except EOFError:
                pass
            except Exception as e:
                traceback.print_exc()

            sleep(self.reconnect_interval)
            self.reconnects += 1

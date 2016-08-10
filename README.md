# MJPEG Streaming Utilities for Python 3.x

This library provides utility functions for working with MJPEG streams.
MJPEG is a simple streaming protocol running on top of HTTP that is used by many existing webcams.
The library provides a threaded client and a streaming generator for Flask based servers.

The library is only compatible with Python 3.x (tested on Python 3.4 and 3.5).
It was tested with [mjpg_streamer](https://github.com/jacksonliam/mjpg-streamer).

## Client API

The library provides a simple threaded streaming client in file `mjpeg/client.py`.
The client is designed to run in a separate background thread to ensure that it can continue reading from the stream
while the main thread is blocked.
The client automatically reconnects to the server if it gets disconnected.

Here is a simple example:
```python
from mjpeg.client import MJPEGClient

url='http://example.com:8080/?action=stream'

# Create a new client thread
client = MJPEGClient(url)

# Allocate memory buffers for frames
bufs = client.request_buffers(65536, 50)
for b in bufs:
    client.enqueue_buffer(b)
    
# Start the client in a background thread
client.start()
```
To obtain frame data, the higher layer application creates a list of memory buffers via `client.request_buffers`.
Each buffer holds exactly one JPEG frame.
The application the requests the buffers to be filled by calling `client.enqueue_buffer`.
Once a buffer is enqueued, the application must no longer touch it.

To received finished frames, the application calls `client.dequeue_buffer()` repeatedly:

```python
while True:
    buf = client.dequeue_buffer()
    <do some work>
    client.enqueue_buffer(buf)
```

The call to `dequeue_buffer` is blocking.
Each buffer object provides the following attributes:

- **length**: The total number of bytes that can fit into the data portion of the buffer
- **used**: The number of bytes occupied by frame data in this buffer
- **timestamp**: The timestamp of the first byte within this buffers (obtained via time.time())
- **sequence**: Frame's sequence number
- **data**: Thea actual frame data

You can use a memory view to obtain frame data from the buffer:
```python
data = memoryview(buf.data)[:buf.used]
```

When the client runs out of buffers to store frames, it will continue receiving the stream, but any frame data will be discarded.
If the connection is disconnected or if the client detects a protocol error, it will try to reconnect the stream automatically.
If the client receives a frame that is larger than the destination buffer, the frame will be discarded.

The client provides a method called `print_stats` which can be used for debugging:
```
MJPEGClient:
  URL:            : http://example.com:8080/?action=stream
  FPS             : 30
  Buffer overruns : 2
  Reconnects      : 0
  Total frames    : 2984
  Discarded frames: 704
  Buffer queue    : 0
```

## Server API
File `mjpeg/server.py` provides a generator for Flask that can be used to generate a MJPEG stream from iterator data.

Here is a simple example which relays MJPEG streams obtained from the client:
```python
from Flask import Flask, Response
from mjpeg.server import MJPEGResponse

def relay():
    while True:
        buf = client.dequeue_buffer()
        yield memoryview(buf.data)[:buf.used]
        client.enqueue_buffer(buf)

@app.route('/')
def stream():
    return MJPEGResponse(relay())

if __name__ == '__main__':
    app = Flask(__name__)
    app.run(host='0.0.0.0', port=8080)
```


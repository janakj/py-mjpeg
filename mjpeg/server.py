from flask import Response

__all__ = ['mjpeg_generator', 'MJPEGResponse']


def mjpeg_generator(boundary, frames):
    hdr = '--%s\r\nContent-Type: image/jpeg\r\n' % boundary

    prefix = ''
    for f in frames:
        msg = prefix + hdr + 'Content-Length: %d\r\n\r\n' % len(f)
        yield msg.encode('utf-8') + f
        prefix = '\r\n'


def MJPEGResponse(it):
    boundary='herebedragons'
    return Response(mjpeg_generator(boundary, it), mimetype='multipart/x-mixed-replace;boundary=%s' % boundary)

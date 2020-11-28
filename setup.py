#!/usr/bin/env python3

import setuptools

with open('README.md', 'r') as fh:
    long_description = fh.read()

setuptools.setup(
    name = 'py-mjpeg',
    version = '1.0.0',
    author = 'Jan Janak',
    author_email = 'jan@janakj.org',
    description = 'MJPEG Streaming Tools',
    long_description = long_description,
    long_description_content_type = 'text/markdown',
    url = 'https://github.com/janakj/py-mjpeg',
    packages = setuptools.find_packages(),
    classifiers = [
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Development Status :: 5 - Production/Stable'
    ],
    python_requires='>=3'
)

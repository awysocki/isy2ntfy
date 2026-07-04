#!/bin/sh
set -e

if [ -f requirements.txt ]; then
  /usr/local/bin/python3 -m pip install -r requirements.txt
fi

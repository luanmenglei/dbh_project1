#!/bin/sh
set -eu
umask 077
ulimit -n 65536
export LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:/data1/ycx/filmbench/runtime/postgres/usr/lib/x86_64-linux-gnu
export LANG=C.UTF-8
exec python3 /data1/ycx/filmbench/supplement-20261015/scripts/manage_person_index.py --libpq /data1/ycx/filmbench/runtime/postgres/usr/lib/x86_64-linux-gnu/libpq.so --dsn-env POSTGRES_DSN "$@"

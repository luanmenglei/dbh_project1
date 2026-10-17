#!/bin/sh
set -eu
umask 077
ulimit -n 65536
export LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:/srv/benchmark/filmbench/runtime/opengauss/usr/opengauss/lib
export LANG=C.UTF-8
exec python3 /srv/benchmark/filmbench/supplement-20261015/scripts/manage_person_index.py --libpq /srv/benchmark/filmbench/runtime/opengauss/usr/opengauss/lib/libpq.so --dsn-env OPENGAUSS_DSN "$@"

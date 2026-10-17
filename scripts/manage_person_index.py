#!/usr/bin/env python3
"""Change only the person index using the backend's matching libpq SDK."""
import argparse
import ctypes
import json
import os
import re


def statements(schema, present):
    if not re.fullmatch(r'filmbench_s[1-9][0-9]*', schema):
        raise ValueError('Schema must be filmbench_sN')
    sql = ['BEGIN', 'SET statement_timeout=600000', 'SET search_path TO '+schema+',pg_catalog',
           'DROP INDEX IF EXISTS credits_person_idx']
    if present:
        sql.append('CREATE INDEX credits_person_idx ON credits(peopleid, movieid)')
    return sql + ['COMMIT']


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--libpq', required=True)
    p.add_argument('--dsn-env', required=True)
    p.add_argument('--schema', required=True)
    p.add_argument('--present', type=int, choices=[0, 1], required=True)
    a = p.parse_args()
    commands = statements(a.schema, a.present)
    lib = ctypes.CDLL(a.libpq)
    for name, args, result in [
        ('PQconnectdb', [ctypes.c_char_p], ctypes.c_void_p),
        ('PQstatus', [ctypes.c_void_p], ctypes.c_int),
        ('PQerrorMessage', [ctypes.c_void_p], ctypes.c_char_p),
        ('PQexec', [ctypes.c_void_p, ctypes.c_char_p], ctypes.c_void_p),
        ('PQresultStatus', [ctypes.c_void_p], ctypes.c_int),
        ('PQresultErrorMessage', [ctypes.c_void_p], ctypes.c_char_p),
        ('PQgetvalue', [ctypes.c_void_p, ctypes.c_int, ctypes.c_int], ctypes.c_char_p),
        ('PQclear', [ctypes.c_void_p], None), ('PQfinish', [ctypes.c_void_p], None),
    ]:
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = args, result
    connection = lib.PQconnectdb(os.environ[a.dsn_env].encode())
    try:
        if not connection or lib.PQstatus(connection) != 0:
            raise RuntimeError('Index-control connection failed')
        for sql in commands:
            result = lib.PQexec(connection, sql.encode())
            try:
                if not result or lib.PQresultStatus(result) != 1:
                    raise RuntimeError('Index DDL failed: '+(lib.PQresultErrorMessage(result).decode() if result else 'empty result'))
            finally:
                if result:
                    lib.PQclear(result)
        result = lib.PQexec(connection, ("SELECT count(*) FROM pg_indexes WHERE schemaname='"+
                                        a.schema+"' AND indexname='credits_person_idx'").encode())
        try:
            if not result or lib.PQresultStatus(result) != 2 or int(lib.PQgetvalue(result, 0, 0)) != a.present:
                raise RuntimeError('Person index state mismatch')
        finally:
            if result:
                lib.PQclear(result)
        print(json.dumps(dict(schema=a.schema, person_index_present=bool(a.present))))
    finally:
        if connection:
            lib.PQfinish(connection)


if __name__ == '__main__':
    main()

"""Unit tests: migrate._split_statements — the no-transaction SQL splitter."""

from migrate import _split_statements


def test_simple_statements():
    sql = "CREATE TABLE a (x int);\nSELECT 1;\n"
    assert _split_statements(sql) == ["CREATE TABLE a (x int)", "SELECT 1"]


def test_semicolon_inside_string_literal():
    sql = "INSERT INTO t VALUES ('a;b');SELECT 2;"
    stmts = _split_statements(sql)
    assert stmts == ["INSERT INTO t VALUES ('a;b')", "SELECT 2"]


def test_dollar_quoted_body_preserved():
    sql = "CREATE FUNCTION f() RETURNS int AS $$ SELECT 1; $$ LANGUAGE sql;SELECT 3;"
    stmts = _split_statements(sql)
    assert len(stmts) == 2
    assert "SELECT 1;" in stmts[0]


def test_line_comments_kept_with_statement():
    sql = "-- migrate:no-transaction\nSELECT 1; -- trailing; not a split\nSELECT 2;"
    stmts = _split_statements(sql)
    assert len(stmts) == 2
    assert stmts[1].startswith("-- trailing") or "SELECT 2" in stmts[1]


def test_real_cagg_migration_splits_cleanly():
    sql = open("migrations/002_continuous_aggregate.sql", encoding="utf-8").read()
    stmts = _split_statements(sql)
    assert len(stmts) == 3  # CREATE MV, ALTER MV, SELECT add_policy

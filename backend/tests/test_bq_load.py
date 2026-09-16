"""The MERGE that merge_dimension generates.

No BigQuery here - the client and the statement runner are stubbed, and what
is asserted is the SQL text. That is the right target: the dimension tables are
written by several scripts that each own different columns, so the difference
between `T.c = S.c` and `T.c = COALESCE(T.c, S.c)` decides whether a free value
from one source silently overwrites a deliberate one from another. That
distinction is invisible at runtime and permanent once it has happened.
"""
import types

import pytest

from app.core import bq_load


class _FakeJob:
    def result(self):
        return None


class _FakeField:
    def __init__(self, name, field_type):
        self.name = name
        self.field_type = field_type


class _FakeTable:
    """Stands in for the target table's declared schema.

    formed_year is INT64 on purpose - it is the column that broke production
    when autodetect saw a batch of nothing but nulls and called it STRING.
    """

    schema = [
        _FakeField("artist_name", "STRING"),
        _FakeField("mbid", "STRING"),
        _FakeField("match_name", "STRING"),
        _FakeField("formed_year", "INTEGER"),
    ]


class _FakeClient:
    def __init__(self):
        self.loads = []

    def get_table(self, table):
        return _FakeTable()

    def load_table_from_json(self, rows, table, job_config=None):
        self.loads.append((rows, table, job_config))
        return _FakeJob()


@pytest.fixture
def captured(monkeypatch):
    """Runs merge_dimension against stubs and hands back the SQL it issued."""
    seen = {}
    fake_bigquery = types.SimpleNamespace(
        LoadJobConfig=lambda **kwargs: None,
        SourceFormat=types.SimpleNamespace(NEWLINE_DELIMITED_JSON="NDJSON"),
        WriteDisposition=types.SimpleNamespace(
            WRITE_TRUNCATE="TRUNCATE", WRITE_APPEND="APPEND"
        ),
        CreateDisposition=types.SimpleNamespace(
            CREATE_NEVER="CREATE_NEVER", CREATE_IF_NEEDED="CREATE_IF_NEEDED"
        ),
        SchemaField=lambda name, field_type, mode=None: (name, field_type, mode),
    )
    monkeypatch.setattr(bq_load, "_require_bigquery", lambda: fake_bigquery)
    monkeypatch.setattr(bq_load, "get_client", lambda: _FakeClient())
    monkeypatch.setattr(bq_load, "dataset_id", lambda: "proj.ds")
    monkeypatch.setattr(bq_load, "run_statement", lambda sql: seen.update(sql=sql))
    return seen


ROWS = [{"artist_name": "BTS", "mbid": "abc"}]


class TestMergeDimension:
    def test_update_columns_assign_directly(self, captured):
        bq_load.merge_dimension("artists", "artist_name", ROWS, update_columns=["mbid"])
        assert "T.mbid = S.mbid" in captured["sql"]
        assert "COALESCE" not in captured["sql"]

    def test_fill_columns_only_write_where_null(self, captured):
        # The Last.fm mbid case: take it when nothing is there, never over the
        # id the enrichment stage established by an explicit lookup.
        bq_load.merge_dimension("artists", "artist_name", ROWS, fill_columns=["mbid"])
        assert "T.mbid = COALESCE(T.mbid, S.mbid)" in captured["sql"]

    def test_both_kinds_combine(self, captured):
        bq_load.merge_dimension(
            "artists",
            "artist_name",
            ROWS,
            update_columns=["match_name"],
            fill_columns=["mbid"],
        )
        sql = captured["sql"]
        assert "T.match_name = S.match_name" in sql
        assert "T.mbid = COALESCE(T.mbid, S.mbid)" in sql
        assert sql.count("WHEN MATCHED THEN UPDATE SET") == 1

    def test_neither_is_insert_only(self, captured):
        # Existing rows must be left entirely alone - this is how `artists`
        # gains new names every run without disturbing enrichment columns.
        bq_load.merge_dimension("artists", "artist_name", ROWS)
        assert "WHEN MATCHED" not in captured["sql"]
        assert "WHEN NOT MATCHED THEN INSERT" in captured["sql"]

    def test_no_rows_issues_no_statement(self, captured):
        assert bq_load.merge_dimension("artists", "artist_name", []) == 0
        assert "sql" not in captured

    def test_insert_lists_every_column_of_the_first_row(self, captured):
        bq_load.merge_dimension("artists", "artist_name", ROWS)
        assert "INSERT (artist_name, mbid)" in captured["sql"]


class TestAppendRowsNeverCreatesTheTable:
    """A load job against a missing table CREATES it, inferring a schema.

    That is not hypothetical: it is how mb_artists and mb_artist_names came to
    exist in production carrying neither the PRIMARY KEY nor the FOREIGN KEY
    schema.sql declares for them, which broke run_init_bq weeks later with
    "Table ...mb_artists does not have Primary Key constraints". The CREATE
    statements had been no-ops the entire time and nothing said so.

    CREATE_NEVER makes a missing table a loud failure pointing at run_init_bq,
    rather than a quiet success that invents a schema from one batch of rows.
    """

    def test_create_disposition_is_create_never(self, monkeypatch):
        seen = {}
        fake_bigquery = types.SimpleNamespace(
            LoadJobConfig=lambda **kwargs: seen.update(kwargs) or "config",
            SourceFormat=types.SimpleNamespace(NEWLINE_DELIMITED_JSON="NDJSON"),
            WriteDisposition=types.SimpleNamespace(
                WRITE_TRUNCATE="TRUNCATE", WRITE_APPEND="APPEND"
            ),
            CreateDisposition=types.SimpleNamespace(
                CREATE_NEVER="CREATE_NEVER", CREATE_IF_NEEDED="CREATE_IF_NEEDED"
            ),
        )
        monkeypatch.setattr(bq_load, "_require_bigquery", lambda: fake_bigquery)
        monkeypatch.setattr(bq_load, "get_client", lambda: _FakeClient())
        monkeypatch.setattr(bq_load, "dataset_id", lambda: "proj.ds")

        assert bq_load.append_rows("chart_entries", ROWS) == 1
        assert seen["create_disposition"] == "CREATE_NEVER"
        # Paired with autodetect=False for the same reason: the warehouse
        # schema is declared in one place, and neither flag lets a load job
        # quietly become the authority on it.
        assert seen["autodetect"] is False


class TestStagingSchemaComesFromTheTarget:
    """Production outage, 2026-09-15: a batch of nulls became a type error.

    merge_dimension used to load its staging table with autodetect=True.
    BigQuery infers a column of entirely NULL values as STRING, so on the
    first night MusicBrainz returned no formation year for any artist in the
    batch, `_staging_artists.formed_year` came out STRING and the MERGE into
    an INT64 column failed. Nothing in the code had changed - the DATA had.

    That is the general hazard of schema inference over a sample: the type of
    a table becomes a function of the rows that happened to arrive, so an
    unlucky batch is a type error rather than a null column, and the unlucky
    batch arrives eventually. The declared schema is the only stable source
    of types, and it is what the MERGE is checked against.
    """

    ALL_NULL_YEARS = [
        {"artist_name": "BTS", "formed_year": None},
        {"artist_name": "aespa", "formed_year": None},
    ]

    def _config(self, client):
        assert len(client.loads) == 1
        return client.loads[0][2]

    def test_types_are_read_from_the_target_not_the_batch(self, monkeypatch):
        client = _FakeClient()
        self._install(monkeypatch, client)
        bq_load.merge_dimension("artists", "artist_name", self.ALL_NULL_YEARS)
        schema = self._config(client).schema
        assert ("formed_year", "INTEGER", "NULLABLE") in schema
        assert ("artist_name", "STRING", "NULLABLE") in schema

    def test_autodetect_is_off(self, monkeypatch):
        client = _FakeClient()
        self._install(monkeypatch, client)
        bq_load.merge_dimension("artists", "artist_name", self.ALL_NULL_YEARS)
        assert self._config(client).autodetect is False

    def test_staging_columns_are_nullable_even_if_the_target_is_not(self, monkeypatch):
        # A staging batch carries only the columns its writer owns, so every
        # other value is absent. Copying a REQUIRED mode across would reject
        # the load before the MERGE could apply the target's own constraints.
        client = _FakeClient()
        self._install(monkeypatch, client)
        bq_load.merge_dimension("artists", "artist_name", self.ALL_NULL_YEARS)
        assert all(field[2] == "NULLABLE" for field in self._config(client).schema)

    def test_a_column_the_target_lacks_fails_loudly(self, monkeypatch):
        # Better than a load that succeeds and a MERGE that fails thirty
        # seconds later on an unrecognised name - which is how the missing
        # match_name column presented on 2026-09-14.
        client = _FakeClient()
        self._install(monkeypatch, client)
        with pytest.raises(ValueError, match="run_init_bq"):
            bq_load.merge_dimension(
                "artists", "artist_name", [{"artist_name": "BTS", "nope": 1}]
            )

    def _install(self, monkeypatch, client):
        fake_bigquery = types.SimpleNamespace(
            LoadJobConfig=lambda **kwargs: types.SimpleNamespace(**kwargs),
            SourceFormat=types.SimpleNamespace(NEWLINE_DELIMITED_JSON="NDJSON"),
            WriteDisposition=types.SimpleNamespace(
                WRITE_TRUNCATE="TRUNCATE", WRITE_APPEND="APPEND"
            ),
            CreateDisposition=types.SimpleNamespace(
                CREATE_NEVER="CREATE_NEVER", CREATE_IF_NEEDED="CREATE_IF_NEEDED"
            ),
            SchemaField=lambda name, field_type, mode=None: (name, field_type, mode),
        )
        monkeypatch.setattr(bq_load, "_require_bigquery", lambda: fake_bigquery)
        monkeypatch.setattr(bq_load, "get_client", lambda: client)
        monkeypatch.setattr(bq_load, "dataset_id", lambda: "proj.ds")
        monkeypatch.setattr(bq_load, "run_statement", lambda sql: None)

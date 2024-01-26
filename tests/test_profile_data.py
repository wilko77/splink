import sqlite3

import numpy as np
import pandas as pd
from pyspark.sql.functions import lit
from pyspark.sql.types import StringType

from splink.database_api import DuckDBAPI, SQLiteAPI
from splink.linker import Linker
from splink.misc import ensure_is_list
from splink.profile_data import _col_or_expr_frequencies_raw_data_sql

from .basic_settings import get_settings_dict
from .decorator import mark_with_dialects_including


def generate_raw_profile_dataset(columns_to_profile, linker):
    linker._initialise_df_concat()

    column_expressions_raw = ensure_is_list(columns_to_profile)

    sql = _col_or_expr_frequencies_raw_data_sql(
        column_expressions_raw, "__splink__df_concat"
    )

    linker._enqueue_sql(sql, "__splink__df_all_column_value_frequencies")

    return linker._execute_sql_pipeline().as_pandas_dataframe()


@mark_with_dialects_including("duckdb")
def test_profile_using_duckdb():
    df = pd.read_csv("./tests/datasets/fake_1000_from_splink_demos.csv")
    df["blank"] = None
    settings_dict = get_settings_dict()
    db_api = DuckDBAPI(connection=":memory:")
    linker = Linker(df, settings_dict, database_api=db_api)

    linker.profile_columns(
        ["first_name", "surname", "first_name || surname", "concat(city, first_name)"],
        top_n=15,
        bottom_n=15,
    )
    linker.profile_columns(
        [
            "first_name",
            ["surname"],
            ["first_name", "surname"],
            ["city", "first_name", "dob"],
            ["first_name", "surname", "city", "dob"],
        ],
        top_n=15,
        bottom_n=15,
    )

    assert len(generate_raw_profile_dataset([["first_name", "blank"]], linker)) == 0


# probably dropping support for this, so won't fixup
# def test_profile_using_duckdb_no_settings():
#     df = pd.read_csv("./tests/datasets/fake_1000_from_splink_demos.csv")

#     linker = DuckDBLinker(df, connection=":memory:")

#     linker.profile_columns(
#         ["first_name", "surname",
#               "first_name || surname", "concat(city, first_name)"],
#         top_n=15,
#         bottom_n=15,
#     )
#     linker.profile_columns(
#         [
#             "first_name",
#             ["surname"],
#             ["first_name", "surname"],
#             ["city", "first_name", "dob"],
#             ["first_name", "surname", "city", "dob"],
#         ],
#         top_n=15,
#         bottom_n=15,
#     )


@mark_with_dialects_including("duckdb")
def test_profile_with_arrays_duckdb():
    dic = {
        "id": {0: 1, 1: 2, 2: 3, 3: 4},
        "forename": {0: "Juan", 1: "Sarah", 2: "Leila", 3: "Michaela"},
        "surname": {0: "Pene", 1: "Dowel", 2: "Idin", 3: "Bose"},
        "offence_code_arr": {
            0: np.nan,
            1: np.array((1, 2, 3)),
            2: np.array((1, 2, 3)),
            3: np.array((1, 2, 3)),
        },
        "lat_long": {
            0: {"lat": 22.730590, "lon": 9.388589},
            1: {"lat": 22.836322, "lon": 9.276112},
            2: {"lat": 37.770850, "lon": 95.689880},
            3: None,
        },
    }

    df = pd.DataFrame(dic)
    settings = {
        "link_type": "dedupe_only",
        "unique_id_column_name": "id",
    }
    db_api = DuckDBAPI(connection=":memory:")
    linker = Linker(df, settings, database_api=db_api)

    column_expressions = ["forename", "surname", "offence_code_arr", "lat_long"]

    linker.profile_columns(
        column_expressions,
        top_n=3,
        bottom_n=3,
    )


@mark_with_dialects_including("spark")
def test_profile_with_arrays_spark(spark, spark_api):
    settings = {
        "link_type": "dedupe_only",
        "unique_id_column_name": "id",
    }
    spark_df = spark.read.parquet("tests/datasets/arrays_df.parquet")
    spark_df.persist()

    linker = Linker(
        spark_df,
        settings,
        spark_api,
    )

    column_expressions = ["forename", "surname", "offence_code_arr", "lat_long"]

    linker.profile_columns(
        column_expressions,
        top_n=3,
        bottom_n=3,
    )


@mark_with_dialects_including("sqlite")
def test_profile_using_sqlite():
    df = pd.read_csv("./tests/datasets/fake_1000_from_splink_demos.csv")

    con = sqlite3.connect(":memory:")

    df.to_sql("fake_data_1", con, if_exists="replace")
    settings_dict = get_settings_dict()

    db_api = SQLiteAPI(con)
    linker = Linker(
        "fake_data_1",
        settings_dict,
        db_api,
    )

    linker.profile_columns(["first_name", "surname", "first_name || surname"])


# @pytest.mark.skip(reason="Uses Spark so slow and heavyweight")
@mark_with_dialects_including("spark")
def test_profile_using_spark(df_spark, spark_api):
    settings_dict = get_settings_dict()
    df_spark = df_spark.withColumn("blank", lit(None).cast(StringType()))
    linker = Linker(df_spark, settings_dict, spark_api)

    linker.profile_columns(
        ["first_name", "surname", "first_name || surname", "concat(city, first_name)"],
        top_n=15,
        bottom_n=15,
    )
    linker.profile_columns(
        [
            "first_name",
            ["surname"],
            ["first_name", "surname"],
            ["city", "first_name", "dob"],
            ["first_name", "surname", "city", "dob"],
        ],
        top_n=15,
        bottom_n=15,
    )

    assert len(generate_raw_profile_dataset([["first_name", "blank"]], linker)) == 0


@mark_with_dialects_including("duckdb")
def test_profile_null_columns(caplog):

    df = pd.DataFrame(
        [
            {"unique_id": 1, "test_1": 1, "test_2": None},
        ]
    )

    db_api = DuckDBAPI(connection=":memory:")
    linker = Linker(df, {"link_type": "dedupe_only"}, database_api=db_api)

    linker.profile_columns(["test_1", "test_2"])

    captured_logs = caplog.text

    assert (
        "Warning: No charts produced for test_2 as the column only "
        "contains null values."
    ) in captured_logs

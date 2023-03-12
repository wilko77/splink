import pandas as pd
import pytest

import splink.athena.athena_comparison_level_library as clla
import splink.athena.athena_comparison_library as cla
import splink.duckdb.duckdb_comparison_level_library as clld
import splink.duckdb.duckdb_comparison_library as cld
import splink.spark.spark_comparison_level_library as clls
import splink.spark.spark_comparison_library as cls
from splink.athena.athena_linker import AthenaLinker
from splink.duckdb.duckdb_linker import DuckDBLinker
from splink.spark.spark_linker import SparkLinker


@pytest.mark.parametrize(
    ("cl"),
    [
        pytest.param(cld, id="DuckDB Datediff Integration Tests"),
        pytest.param(cls, id="Spark Datediff Integration Tests"),
    ],
)
def test_simple_run(cl):

    print(
        cl.distance_in_km_at_thresholds(
            lat_col="lat", long_col="long", km_thresholds=[1, 5, 10]
        ).as_dict()
    )


@pytest.mark.parametrize(
    ("cl", "cll", "Linker"),
    [
        pytest.param(
            cld, clld, DuckDBLinker, id="DuckDB Distance in KM Integration Tests"
        ),
        pytest.param(
            cls, clls, SparkLinker, id="Spark Distance in KM Integration Tests"
        ),
        pytest.param(
            cla, clla, AthenaLinker, id="Athena Distance in KM Integration Tests"
        ),
    ],
)
def test_km_distance_levels(spark, cl, cll, Linker):
    df = pd.DataFrame(
        [
            {
                "unique_id": 1,
                "name": "102 Petty France",
                "lat": 51.500516,
                "long": -0.133192,
            },
            {
                "unique_id": 2,
                "name": "10 South Colonnade",
                "lat": 51.504444,
                "long": -0.021389,
            },
            {
                "unique_id": 3,
                "name": "Houses of Parliament",
                "lat": 51.499479,
                "long": -0.124809,
            },
            {
                "unique_id": 4,
                "name": "5 Wellington Place",
                "lat": 53.796105,
                "long": -1.549725,
            },
            {
                "unique_id": 5,
                "name": "102 Petty France Duplicate",
                "lat": 51.500516,
                "long": -0.133192,
            },
            {
                "unique_id": 6,
                "name": "Splink",
                "lat": 53.3338,
                "long": -6.24488,
            },
        ]
    )

    settings_cl = {
        "link_type": "dedupe_only",
        "comparisons": [
            cl.distance_in_km_at_thresholds(
                lat_col="lat", long_col="long", km_thresholds=[0, 1, 10, 300]
            )
        ],
    }

    # For testing the cll version
    km_diff = {
        "output_column_name": "km_diff",
        "comparison_levels": [
            {
                "sql_condition": "(lat_l IS NULL OR lat_r IS NULL) \n"
                "OR (long_l IS NULL OR long_r IS NULL)",
                "label_for_charts": "Null",
                "is_null_level": True,
            },
            cll.distance_in_km_level(lat_col="lat", long_col="long", km_threshold=0),
            cll.distance_in_km_level(lat_col="lat", long_col="long", km_threshold=1),
            cll.distance_in_km_level(
                lat_col="lat",
                long_col="long",
                km_threshold=10,
            ),
            cll.distance_in_km_level(
                lat_col="lat",
                long_col="long",
                km_threshold=300,
            ),
            cll.else_level(),
        ],
    }

    settings_cll = {"link_type": "dedupe_only", "comparisons": [km_diff]}

    if Linker == SparkLinker:
        df = spark.createDataFrame(df)
        df.persist()
    linker = Linker(df, settings_cl)
    cl_df_e = linker.predict().as_pandas_dataframe()
    linker = Linker(df, settings_cll)
    cll_df_e = linker.predict().as_pandas_dataframe()

    linker_outputs = {
        "cl": cl_df_e,
        "cll": cll_df_e,
    }

    # # Dict key: {size: gamma_level value}
    size_gamma_lookup = {0: 5, 1: 4, 2: 3, 3: 2, 4: 1}

    # Check gamma sizes are as expected
    for gamma, gamma_lookup in size_gamma_lookup.items():
        print(linker_outputs)
        # linker_pred = linker_outputs
        for linker_pred in linker_outputs.values():
            gamma_column_name_options = [
                "gamma_custom_long_lat",
                "gamma_custom_lat_long",
            ]  # lat and long switch unpredictably
            gamma_column_name = linker_pred.columns[
                linker_pred.columns.str.contains("|".join(gamma_column_name_options))
            ][0]
            assert sum(linker_pred[gamma_column_name] == gamma) == gamma_lookup

    # Check individual IDs are assigned to the correct gamma values
    # Dict key: {gamma_value: tuple of ID pairs}
    gamma_lookup = {
        4: [(1, 5)],
        3: [(1, 3)],
        2: [(2, 5)],
        1: [(3, 4)],
    }

    for gamma, id_pairs in gamma_lookup.items():
        for left, right in id_pairs:
            for linker_name, linker_pred in linker_outputs.items():

                print(f"Checking IDs: {left}, {right} for {linker_name}")

                gamma_column_name_options = [
                    "gamma_custom_long_lat",
                    "gamma_custom_lat_long",
                ]  # lat and long switch unpredictably
                gamma_column_name = linker_pred.columns[
                    linker_pred.columns.str.contains(
                        "|".join(gamma_column_name_options)
                    )
                ][0]
                assert (
                    linker_pred.loc[
                        (linker_pred.unique_id_l == left)
                        & (linker_pred.unique_id_r == right)
                    ][gamma_column_name].values[0]
                    == gamma
                )

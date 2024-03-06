from __future__ import annotations

import logging
import multiprocessing
from copy import deepcopy
from typing import TYPE_CHECKING, List

from .blocking import block_using_rules_sqls, blocking_rule_to_obj
from .comparison_vector_values import compute_comparison_vector_values_sql
from .expectation_maximisation import (
    compute_new_parameters_sql,
    compute_proportions_for_new_parameters,
)
from .m_u_records_to_parameters import (
    append_u_probability_to_comparison_level_trained_probabilities,
    m_u_records_to_lookup_dict,
)

# https://stackoverflow.com/questions/39740632/python-type-hinting-without-cyclic-imports
if TYPE_CHECKING:
    from .linker import Linker

logger = logging.getLogger(__name__)


def _rows_needed_for_n_pairs(n_pairs):
    # Number of pairs generated by cartesian product is
    # p(r) = r(r-1)/2, where r is input rows
    # Solve this for r
    # https://www.wolframalpha.com/input?i=Solve%5Bp%3Dr+*+%28r+-+1%29+%2F+2%2C+r%5D
    sample_rows = 0.5 * ((8 * n_pairs + 1) ** 0.5 + 1)
    return sample_rows


def _proportion_sample_size_link_only(
    row_counts_individual_dfs: List[int], max_pairs: int
):
    # total valid links is sum of pairwise product of individual row counts
    # i.e. if frame_counts are [a, b, c, d, ...],
    # total_links = a*b + a*c + a*d + ... + b*c + b*d + ... + c*d + ...
    total_links = (
        sum(row_counts_individual_dfs) ** 2
        - sum([count**2 for count in row_counts_individual_dfs])
    ) / 2
    total_nodes = sum(row_counts_individual_dfs)

    # if we scale each frame by a proportion total_links scales with the square
    # i.e. (our target) max_pairs == proportion^2 * total_links
    proportion = (max_pairs / total_links) ** 0.5
    # sample size is for df_concat_with_tf, i.e. proportion of the total nodes
    sample_size = proportion * total_nodes
    return proportion, sample_size


def estimate_u_values(linker: Linker, max_pairs, seed=None):
    logger.info("----- Estimating u probabilities using random sampling -----")

    nodes_with_tf = linker._initialise_df_concat_with_tf()

    original_settings_obj = linker._settings_obj

    training_linker = deepcopy(linker)

    training_linker._train_u_using_random_sample_mode = True

    settings_obj = training_linker._settings_obj
    settings_obj._retain_matching_columns = False
    settings_obj._retain_intermediate_calculation_columns = False
    for cc in settings_obj.comparisons:
        for cl in cc.comparison_levels:
            # TODO: ComparisonLevel: manage access
            cl._tf_adjustment_column = None

    if settings_obj._link_type in ["dedupe_only", "link_and_dedupe"]:
        sql = """
        select count(*) as count
        from __splink__df_concat_with_tf
        """

        training_linker._enqueue_sql(sql, "__splink__df_concat_count")
        dataframe = training_linker._execute_sql_pipeline([nodes_with_tf])

        result = dataframe.as_record_dict()
        dataframe.drop_table_from_database_and_remove_from_cache()
        total_nodes = result[0]["count"]
        sample_size = _rows_needed_for_n_pairs(max_pairs)
        proportion = sample_size / total_nodes

    if settings_obj._link_type == "link_only":
        sql = """
        select count(source_dataset) as count
        from __splink__df_concat_with_tf
        group by source_dataset
        """
        training_linker._enqueue_sql(sql, "__splink__df_concat_count")
        dataframe = training_linker._execute_sql_pipeline([nodes_with_tf])
        result = dataframe.as_record_dict()
        dataframe.drop_table_from_database_and_remove_from_cache()
        frame_counts = [res["count"] for res in result]

        proportion, sample_size = _proportion_sample_size_link_only(
            frame_counts, max_pairs
        )

        total_nodes = sum(frame_counts)

    if proportion >= 1.0:
        proportion = 1.0

    if sample_size > total_nodes:
        sample_size = total_nodes

    sql = f"""
    select *
    from __splink__df_concat_with_tf
    {training_linker._random_sample_sql(proportion, sample_size, seed)}
    """
    training_linker._enqueue_sql(sql, "__splink__df_concat_with_tf_sample")
    df_sample = training_linker._execute_sql_pipeline([nodes_with_tf])

    if linker._sql_dialect == "duckdb" and max_pairs > 1e4:
        br = blocking_rule_to_obj(
            {
                "blocking_rule": "1=1",
                "salting_partitions": multiprocessing.cpu_count(),
            }
        )
        settings_obj._blocking_rules_to_generate_predictions = [br]
    else:
        settings_obj._blocking_rules_to_generate_predictions = []

    sql_infos = block_using_rules_sqls(training_linker)
    for sql_info in sql_infos:
        training_linker._enqueue_sql(sql_info["sql"], sql_info["output_table_name"])

    # repartition after blocking only exists on the SparkLinker
    repartition_after_blocking = getattr(
        training_linker, "repartition_after_blocking", False
    )
    if repartition_after_blocking:
        df_blocked = training_linker._execute_sql_pipeline([df_sample])
        sample_dataframe = [df_blocked]
    else:
        sample_dataframe = [df_sample]

    sql = compute_comparison_vector_values_sql(
        settings_obj._columns_to_select_for_comparison_vector_values
    )

    training_linker._enqueue_sql(sql, "__splink__df_comparison_vectors")

    sql = """
    select *, cast(0.0 as float8) as match_probability
    from __splink__df_comparison_vectors
    """

    training_linker._enqueue_sql(sql, "__splink__df_predict")

    sql = compute_new_parameters_sql(
        estimate_without_term_frequencies=False,
        comparisons=settings_obj.comparisons,
    )
    linker._enqueue_sql(sql, "__splink__m_u_counts")
    df_params = training_linker._execute_sql_pipeline(sample_dataframe)

    param_records = df_params.as_pandas_dataframe()
    param_records = compute_proportions_for_new_parameters(param_records)
    df_params.drop_table_from_database_and_remove_from_cache()
    df_sample.drop_table_from_database_and_remove_from_cache()

    m_u_records = [
        r
        for r in param_records
        if r["output_column_name"] != "_probability_two_random_records_match"
    ]

    m_u_records_lookup = m_u_records_to_lookup_dict(m_u_records)
    for c in original_settings_obj.comparisons:
        for cl in c._comparison_levels_excluding_null:
            append_u_probability_to_comparison_level_trained_probabilities(
                cl,
                m_u_records_lookup,
                c.output_column_name,
                "estimate u by random sampling",
            )

    logger.info("\nEstimated u probabilities using random sampling")

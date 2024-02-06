from abc import ABC, abstractproperty
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from .comparison_level_creator import ComparisonLevelCreator


class SplinkDialect(ABC):
    # Stores instances of each subclass of SplinkDialect.
    _dialect_instances = {}
    # string defined by subclasses to be used in factory method from_string
    # give a dummy default value so that subclasses that fail to do this
    # don't ruin functionality for existing subclasses
    _dialect_name_for_factory = None

    # Register a subclass of SplinkDialect on its creation.
    # Whenever that subclass is called again, use the previous instance.
    def __new__(cls, *args, **kwargs):
        if cls not in cls._dialect_instances:
            instance = super(SplinkDialect, cls).__new__(cls)
            cls._dialect_instances[cls] = instance
        return cls._dialect_instances[cls]

    @abstractproperty
    def name(self):
        pass

    @property
    def sqlglot_name(self):
        return self.name

    @classmethod
    def from_string(cls, dialect_name: str):
        # list of classes which match _dialect_name_for_factory
        # should just get a single subclass, as this should be unique
        classes_from_dialect_name = [
            c
            for c in cls.__subclasses__()
            if c._dialect_name_for_factory == dialect_name
        ]
        # use sequence unpacking to catch if we duplicate
        # _dialect_name_for_factory in subclasses
        if len(classes_from_dialect_name) == 1:
            subclass = classes_from_dialect_name[0]
            return subclass()
        # error - either too many subclasses found
        if len(classes_from_dialect_name) > 1:
            classes_string = ", ".join(map(str, classes_from_dialect_name))
            error_message = (
                "Found multiple subclasses of `SplinkDialect` with "
                "lookup string `_dialect_name_for_factory` equal to "
                f"supplied value {dialect_name}: {classes_string}!"
            )
        # or _no_ subclasses found
        else:
            error_message = (
                "Could not find subclass of `SplinkDialect` with "
                f"lookup string '{dialect_name}'."
            )
        raise ValueError(error_message)

    @property
    def levenshtein_function_name(self):
        raise NotImplementedError(
            f"Backend '{self.name}' does not have a 'Levenshtein' function"
        )

    @property
    def damerau_levenshtein_function_name(self):
        raise NotImplementedError(
            f"Backend '{self.name}' does not have a 'Damerau-Levenshtein' function"
        )

    @property
    def jaro_winkler_function_name(self):
        raise NotImplementedError(
            f"Backend '{self.name}' does not have a 'Jaro-Winkler' function"
        )

    @property
    def jaro_function_name(self):
        raise NotImplementedError(
            f"Backend '{self.name}' does not have a 'Jaro' function"
        )

    @property
    def jaccard_function_name(self):
        raise NotImplementedError(
            f"Backend '{self.name}' does not have a 'Jaccard' function"
        )

    def random_sample_sql(
        self, proportion, sample_size, seed=None, table=None, unique_id=None
    ):
        raise NotImplementedError(
            f"Backend '{self.name}' needs a random_sample_sql added to its dialect"
        )

    @staticmethod
    def _wrap_in_nullif(func):
        def nullif_wrapped_function(*args, **kwargs):
            # convert empty strings to NULL
            return f"NULLIF({func(*args, **kwargs)}, '')"

        return nullif_wrapped_function

    def try_parse_date(self, name: str, date_format: str = None):
        return self._try_parse_date_raw(name, date_format)

    def _try_parse_date_raw(self, name: str, date_format: str = None):
        raise NotImplementedError(
            f"Backend '{self.name}' does not have a 'try_parse_date' function"
        )

    @final
    def regex_extract(self, name: str, pattern: str, capture_group: int = 0):
        return self._wrap_in_nullif(self._regex_extract_raw)(
            name, pattern, capture_group
        )

    def _regex_extract_raw(self, name: str, pattern: str, capture_group: int = 0):
        raise NotImplementedError(
            f"Backend '{self.name}' does not have a 'regex_extract' function"
        )

    def explode_arrays_sql(self, tbl_name, columns_to_explode, other_columns_to_retain):
        raise NotImplementedError(
            f"Unnesting blocking rules are not supported for {type(self)}"
        )


class DuckDBDialect(SplinkDialect):
    _dialect_name_for_factory = "duckdb"

    @property
    def name(self):
        return "duckdb"

    @property
    def levenshtein_function_name(self):
        return "levenshtein"

    @property
    def damerau_levenshtein_function_name(self):
        return "damerau_levenshtein"

    @property
    def jaro_function_name(self):
        return "jaro_similarity"

    @property
    def jaro_winkler_function_name(self):
        return "jaro_winkler_similarity"

    @property
    def jaccard_function_name(self):
        return "jaccard"

    @property
    def default_date_format(self):
        return "%Y-%m-%d"

    def _try_parse_date_raw(self, name: str, date_format: str = None):
        if date_format is None:
            date_format = self.default_date_format
        return f"""try_strptime({name}, '{date_format}')"""

    # TODO: this is only needed for duckdb < 0.9.0.
    # should we just ditch support for that? (only for cll - engine should still work)
    def array_intersect(self, clc: "ComparisonLevelCreator"):
        clc.col_expression.sql_dialect = self
        col = clc.col_expression
        threshold = clc.min_intersection

        # sum of individual (unique) array sizes, minus the (unique) union
        return (
            f"list_unique({col.name_l}) + list_unique({col.name_r})"
            f" - list_unique(list_concat({col.name_l}, {col.name_r}))"
            f" >= {threshold}"
        ).strip()

    def _regex_extract_raw(self, name: str, pattern: str, capture_group: int = 0):
        return f"regexp_extract({name}, '{pattern}', {capture_group})"

    # TODO: roll out to other dialects, at least for now
    @property
    def infinity_expression(self):
        return "cast('infinity' as float8)"

    def random_sample_sql(
        self, proportion, sample_size, seed=None, table=None, unique_id=None
    ):
        if proportion == 1.0:
            return ""
        percent = proportion * 100
        if seed:
            return f"USING SAMPLE bernoulli({percent}%) REPEATABLE({seed})"
        else:
            return f"USING SAMPLE {percent}% (bernoulli)"

    def explode_arrays_sql(self, tbl_name, columns_to_explode, other_columns_to_retain):
        """Generated sql that explodes one or more columns in a table"""
        columns_to_explode = columns_to_explode.copy()
        other_columns_to_retain = other_columns_to_retain.copy()
        # base case
        if len(columns_to_explode) == 0:
            return f"select {','.join(other_columns_to_retain)} from {tbl_name}"
        else:
            column_to_explode = columns_to_explode.pop()
            cols_to_select = (
                [f"unnest({column_to_explode}) as {column_to_explode}"]
                + other_columns_to_retain
                + columns_to_explode
            )
            other_columns_to_retain.append(column_to_explode)
            return f"""select {','.join(cols_to_select)}
                from ({self.explode_arrays_sql(tbl_name,columns_to_explode,other_columns_to_retain)})"""  # noqa: E501


class SparkDialect(SplinkDialect):
    _dialect_name_for_factory = "spark"

    @property
    def name(self):
        return "spark"

    @property
    def levenshtein_function_name(self):
        return "levenshtein"

    @property
    def damerau_levenshtein_function_name(self):
        return "damerau_levenshtein"

    @property
    def jaro_function_name(self):
        return "jaro_sim"

    @property
    def jaro_winkler_function_name(self):
        return "jaro_winkler"

    @property
    def jaccard_function_name(self):
        return "jaccard"

    @property
    def default_date_format(self):
        return "yyyy-MM-dd"

    def _try_parse_date_raw(self, name: str, date_format: str = None):
        if date_format is None:
            date_format = self.default_date_format
        return f"""to_date({name}, '{date_format}')"""

    def _regex_extract_raw(self, name: str, pattern: str, capture_group: int = 0):
        return f"regexp_extract({name}, '{pattern}', {capture_group})"

    @property
    def infinity_expression(self):
        return "'infinity'"

    def random_sample_sql(
        self, proportion, sample_size, seed=None, table=None, unique_id=None
    ):
        if proportion == 1.0:
            return ""
        percent = proportion * 100
        if seed:
            return f" ORDER BY rand({seed}) LIMIT {round(sample_size)}"
        else:
            return f" TABLESAMPLE ({percent} PERCENT) "

    def explode_arrays_sql(self, tbl_name, columns_to_explode, other_columns_to_retain):
        """Generated sql that explodes one or more columns in a table"""
        columns_to_explode = columns_to_explode.copy()
        other_columns_to_retain = other_columns_to_retain.copy()
        if len(columns_to_explode) == 0:
            return f"select {','.join(other_columns_to_retain)} from {tbl_name}"
        else:
            column_to_explode = columns_to_explode.pop()
            cols_to_select = (
                [f"explode({column_to_explode}) as {column_to_explode}"]
                + other_columns_to_retain
                + columns_to_explode
            )
        return f"""select {','.join(cols_to_select)}
                from ({self.explode_arrays_sql(tbl_name,columns_to_explode,other_columns_to_retain+[column_to_explode])})"""  # noqa: E501


class SQLiteDialect(SplinkDialect):
    _dialect_name_for_factory = "sqlite"

    @property
    def name(self):
        return "sqlite"

    # SQLite does not natively support string distance functions.
    # However, sqlite UDFs are registered automatically by Splink
    @property
    def levenshtein_function_name(self):
        return "levenshtein"

    @property
    def damerau_levenshtein_function_name(self):
        return "damerau_levenshtein"

    @property
    def jaro_function_name(self):
        return "jaro_sim"

    @property
    def jaro_winkler_function_name(self):
        return "jaro_winkler"

    @property
    def infinity_expression(self):
        return "'infinity'"

    def random_sample_sql(
        self, proportion, sample_size, seed=None, table=None, unique_id=None
    ):
        if proportion == 1.0:
            return ""
        if seed:
            raise NotImplementedError(
                "SQLite does not support seeds in random ",
                "samples. Please remove the `seed` parameter.",
            )

        sample_size = int(sample_size)

        return f"""ORDER BY RANDOM()
            LIMIT {sample_size}
            """


class PostgresDialect(SplinkDialect):
    _dialect_name_for_factory = "postgres"

    @property
    def name(self):
        return "postgres"

    @property
    def levenshtein_function_name(self):
        return "levenshtein"

    def _regex_extract_raw(self, name: str, pattern: str, capture_group: int = 0):
        # full match - wrap pattern in parentheses so first group is whole expression
        if capture_group == 0:
            pattern = f"({pattern})"
        if capture_group > 1:
            # currently no easy way to capture non-first groups
            raise ValueError(
                "'postgres' backend does not currently support a capture_group greater "
                "than 1. To proceed you must use your own SQL expression"
            )
        return f"substring({name} from '{pattern}')"

    @property
    def default_date_format(self):
        return "YYYY-MM-DD"

    def try_parse_date(self, name: str, date_format: str = None):
        if date_format is None:
            date_format = self.default_date_format
        return f"""try_cast_date({name}, '{date_format}')"""

    def array_intersect(self, clc: "ComparisonLevelCreator"):
        clc.col_expression.sql_dialect = self
        col = clc.col_expression
        threshold = clc.min_intersection
        return f"""
        CARDINALITY(ARRAY_INTERSECT({col.name_l}, {col.name_r})) >= {threshold}
        """.strip()

    def random_sample_sql(
        self, proportion, sample_size, seed=None, table=None, unique_id=None
    ):
        if proportion == 1.0:
            return ""
        if seed:
            # TODO: we could maybe do seeds by handling it in calling function
            # need to execute setseed() in surrounding session
            raise NotImplementedError(
                "Postgres does not support seeds in random "
                "samples. Please remove the `seed` parameter."
            )

        sample_size = int(sample_size)

        return f"""ORDER BY RANDOM()
            LIMIT {sample_size}
            """

    @property
    def infinity_expression(self):
        return "'infinity'"


class AthenaDialect(SplinkDialect):
    _dialect_name_for_factory = "athena"

    @property
    def name(self):
        return "athena"

    @property
    def sqlglot_name(self):
        return "presto"

    @property
    def _levenshtein_name(self):
        return "levenshtein_distance"


_dialect_lookup = {
    "duckdb": DuckDBDialect(),
    "spark": SparkDialect(),
    "sqlite": SQLiteDialect(),
    "postgres": PostgresDialect(),
    "athena": AthenaDialect(),
}

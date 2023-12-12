import re
import string
from copy import copy
from functools import partial
from typing import Union

import sqlglot

from .dialects import SplinkDialect
from .input_column import SqlglotColumnTreeBuilder
from .sql_transform import add_suffix_to_all_column_identifiers


class ColumnExpression:
    """
    Enables transforms to be applied to a column before it's passed into a
    comparison level.

    Dialect agnostic.  Execution is delayed until the dialect is known.

    For example:
        from splink.column_expression import ColumnExpression
        col = (
            ColumnExpression("first_name")
            .lower()
            .regex_extract("^[A-Z]{1,4}")
        )

        ExactMatchLevel(col)

    Note that this will typically be created without a dialect, and the dialect
    will later be populated when the ColumnExpression is passed via a comparison
    level creator into a linker.
    """

    def __init__(self, sql_expression: str, sql_dialect: SplinkDialect = None):
        self.raw_sql_expression = sql_expression
        self.operations = []
        if sql_dialect is not None:
            self.sql_dialect: SplinkDialect = sql_dialect

    def _clone(self):
        clone = copy(self)
        clone.operations = [op for op in self.operations]
        return clone

    @staticmethod
    def instantiate_if_str(
        str_or_column_expression: Union[str, "ColumnExpression"]
    ) -> "ColumnExpression":
        if isinstance(str_or_column_expression, ColumnExpression):
            return str_or_column_expression
        elif isinstance(str_or_column_expression, str):
            return ColumnExpression(str_or_column_expression)

    def parse_input_string(self, dialect: SplinkDialect):
        """
        The input into an ColumnExpression can be
            - a column name or column reference e.g. first_name, first name
            - a sql expression e.g. UPPER(first_name), first_name || surname

        In the former case, we do not expect the user to have escaped the column name
        with identifier quotes (see also InputColumn).

        In the later case, we expect the expression to be valid sql in the dialect
        that the user will specify in their linker.
        """

        # It's difficult (possibly impossible) to find a completely general
        # algorithm that can distinguish between the two cases (col name, or sql
        # expression), since lower(first_name) could technically be a column name
        # Here I use a heuristic:
        # If there's a () or || then assume it's a sql expression
        if re.search(r"\([^)]*\)", self.raw_sql_expression):
            return self.raw_sql_expression
        elif "||" in self.raw_sql_expression:
            return self.raw_sql_expression

        # Otherwise, assume it's a column name or reference which may need quoting
        return SqlglotColumnTreeBuilder.from_raw_column_name_or_column_reference(
            self.raw_sql_expression, dialect.sqlglot_name
        ).sql

    @property
    def raw_sql_is_pure_column_or_column_reference(self):
        if re.search(r"\([^)]*\)", self.raw_sql_expression):
            return False

        if "||" in self.raw_sql_expression:
            return False
        return True

    @property
    def is_pure_column_or_column_reference(self):
        if len(self.operations) > 0:
            return False

        return self.raw_sql_is_pure_column_or_column_reference

    def apply_operations(self, name: str, dialect: SplinkDialect):
        for op in self.operations:
            name = op(name=name, dialect=dialect)
        return name

    def _lower_dialected(self, name, dialect):
        lower_sql = sqlglot.parse_one("lower(___col___)").sql(
            dialect=dialect.sqlglot_name
        )

        return lower_sql.replace("___col___", name)

    def lower(self):
        """
        Applies a lowercase transofrom to the input expression.
        """
        clone = self._clone()
        clone.operations.append(clone._lower_dialected)
        return clone

    def _substr_dialected(
        self, name: str, start: int, end: int, dialect: SplinkDialect
    ):
        substr_sql = sqlglot.parse_one(f"substring(___col___, {start}, {end})").sql(
            dialect=dialect.sqlglot_name
        )

        return substr_sql.replace("___col___", name)

    def substr(self, start: int, length: int):
        """
        Applies a substring transform to the input expression of a given length
        starting from a specified index.
        Args:
            start (int): The starting index of the substring.
            length (int): The length of the substring.
        """
        clone = self._clone()
        op = partial(clone._substr_dialected, start=start, end=length)
        clone.operations.append(op)

        return clone

    def _regex_extract_dialected(
        self,
        name: str,
        pattern: str,
        dialect: SplinkDialect,
    ) -> str:
        # TODO - add support for capture group.  This will require
        # adding dialect specific functions because sqlglot doesn't yet support the
        # position (capture group) arg.
        sql = f"regexp_extract(___col___, '{pattern}')"
        regex_extract_sql = sqlglot.parse_one(sql).sql(dialect=dialect.sqlglot_name)

        return regex_extract_sql.replace("___col___", name)

    def regex_extract(self, pattern: str, capture_group: int = 1):
        """Applies a regex extract transform to the input expression.

        Args:
            pattern (str): The regex pattern to match.
            capture_group (int): The capture group to extract from the matched pattern.

        """
        clone = self._clone()
        op = partial(
            clone._regex_extract_dialected,
            pattern=pattern,
        )
        clone.operations.append(op)

        return clone

    def _try_parse_date_dialected(
        self,
        name: str,
        dialect: SplinkDialect,
        date_format: str = None,
    ):
        return dialect.try_parse_date(name, date_format=date_format)

    def try_parse_date(self, date_format: str = None):
        clone = self._clone()
        op = partial(
            clone._try_parse_date_dialected,
            date_format=date_format,
        )
        clone.operations.append(op)

        return clone

    @property
    def name(self) -> str:
        sql_expression = self.parse_input_string(self.sql_dialect)
        return self.apply_operations(sql_expression, self.sql_dialect)

    @property
    def name_l(self) -> str:
        sql_expression = self.parse_input_string(self.sql_dialect)

        base_name = add_suffix_to_all_column_identifiers(
            sql_expression, "_l", self.sql_dialect.sqlglot_name
        )
        return self.apply_operations(base_name, self.sql_dialect)

    @property
    def name_r(self) -> str:
        sql_expression = self.parse_input_string(self.sql_dialect)
        base_name = add_suffix_to_all_column_identifiers(
            sql_expression, "_r", self.sql_dialect.sqlglot_name
        )
        return self.apply_operations(base_name, self.sql_dialect)

    @property
    def output_column_name(self) -> str:
        allowed_chars = string.ascii_letters + string.digits + "_"
        sanitised_name = "".join(
            c if c in allowed_chars else "_" for c in self.raw_sql_expression
        )
        return sanitised_name

    @property
    def label(self) -> str:
        if len(self.operations) > 0:
            return "transformed " + self.raw_sql_expression
        else:
            return self.raw_sql_expression

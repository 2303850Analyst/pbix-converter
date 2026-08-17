from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from pbix_doc.domain import DocumentationModel, FileInfo, PageInfo, ReportInfo
from pbix_doc.projector import TableBlock, project_document
from pbix_doc.semantic import read_model_metadata


def _model(
    expression: str,
    *,
    table: str = "Sales",
    model_column: str = "Amount",
    source_column: str = "amount_net",
    extra_queries: list[dict[str, object]] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        tmschema_tables=[{"Name": table, "IsHidden": False, "StorageMode": "Import"}],
        tmschema_columns=[
            {
                "TableName": table,
                "Name": model_column,
                "SourceColumn": source_column,
                "DataType": "Decimal",
            }
        ],
        schema=[],
        statistics=[],
        dax_columns=[],
        tables=[],
        dax_measures=[],
        relationships=[],
        power_query=[{"TableName": table, "Expression": expression}],
        mashup_queries=extra_queries or [],
        rls=[],
        ols=[],
        connections=[],
    )


def _source_type(model: SimpleNamespace) -> str | None:
    semantic = read_model_metadata(model, [])
    return semantic.tables[0].columns[0].native_data_type


class PowerQuerySourceTypeLineageTests(unittest.TestCase):
    def test_matches_source_column_to_direct_transform_column_types(self) -> None:
        expression = (
            "let Source = Sql.Database(\"server\", \"database\"), "
            'Typed = Table.TransformColumnTypes(Source, {{"amount_net", Currency.Type}}) '
            "in Typed"
        )

        self.assertEqual("M: Currency.Type", _source_type(_model(expression)))

    def test_resolves_type_operations_stored_in_let_variable(self) -> None:
        expression = (
            "let Source = Sql.Database(\"server\", \"database\"), "
            'Types = {{"amount_net", type nullable number}}, '
            "Typed = Table.TransformColumnTypes(Source, Types) "
            "in Typed"
        )

        self.assertEqual("M: type nullable number", _source_type(_model(expression)))

    def test_follows_rename_chain_to_final_source_column_name(self) -> None:
        expression = (
            "let Source = Sql.Database(\"server\", \"database\"), "
            'Typed = Table.TransformColumnTypes(Source, {{"raw_amount", Decimal.Type}}), '
            'Renamed = Table.RenameColumns(Typed, {{"raw_amount", "amount_net"}}) '
            "in Renamed"
        )

        self.assertEqual("M: Decimal.Type", _source_type(_model(expression)))

    def test_only_final_in_branch_contributes_type_evidence(self) -> None:
        expression = (
            "let Source = Sql.Database(\"server\", \"database\"), "
            'Loaded = Table.TransformColumnTypes(Source, {{"amount_net", Currency.Type}}), '
            'Unused = Table.TransformColumnTypes(Source, {{"amount_net", type text}}) '
            "in Loaded"
        )

        self.assertEqual("M: Currency.Type", _source_type(_model(expression)))

    def test_parenthesized_let_expression_is_resolved(self) -> None:
        expression = (
            '(let Source = Sql.Database("server", "database"), '
            'Typed = Table.TransformColumnTypes(Source, {{"amount_net", Int64.Type}}) '
            "in Typed)"
        )

        self.assertEqual("M: Int64.Type", _source_type(_model(expression)))

    def test_nested_let_binding_is_resolved(self) -> None:
        expression = (
            'let Nested = let Source = Sql.Database("server", "database"), '
            'Typed = Table.TransformColumnTypes(Source, {{"amount_net", Int64.Type}}) '
            "in Typed in Nested"
        )

        self.assertEqual("M: Int64.Type", _source_type(_model(expression)))

    def test_m_binding_and_column_identifiers_are_case_sensitive(self) -> None:
        expression = (
            "let Source = Sql.Database(\"server\", \"database\"), "
            'typed = Table.TransformColumnTypes(Source, {{"Code", type text}}), '
            'Typed = Table.TransformColumnTypes(Source, {{"Code", Int64.Type}, '
            '{"code", type text}}) '
            "in Typed"
        )

        self.assertEqual(
            "M: Int64.Type",
            _source_type(
                _model(
                    expression,
                    table="Facts",
                    model_column="Code label",
                    source_column="Code",
                )
            ),
        )

    def test_shared_query_identifiers_are_case_sensitive(self) -> None:
        upper_query = {
            "Name": "Stage",
            "Kind": "Query",
            "IsParameter": False,
            "Expression": (
                "let Source = Sql.Database(\"server\", \"database\"), "
                'Typed = Table.TransformColumnTypes(Source, {{"Code", Int64.Type}}) '
                "in Typed"
            ),
        }
        lower_query = {
            "Name": "stage",
            "Kind": "Query",
            "IsParameter": False,
            "Expression": (
                "let Source = Sql.Database(\"server\", \"database\"), "
                'Typed = Table.TransformColumnTypes(Source, {{"Code", type text}}) '
                "in Typed"
            ),
        }

        self.assertEqual(
            "M: Int64.Type",
            _source_type(
                _model(
                    "Stage",
                    table="Facts",
                    model_column="Code label",
                    source_column="Code",
                    extra_queries=[upper_query, lower_query],
                )
            ),
        )

    def test_propagates_type_from_referenced_staging_query_to_loaded_table(self) -> None:
        loaded_expression = (
            'let Source = StagingSales, Selected = Table.SelectColumns(Source, {"amount_net"}) '
            "in Selected"
        )
        staging_expression = (
            "let Source = Sql.Database(\"server\", \"database\"), "
            'Typed = Table.TransformColumnTypes(Source, {{"amount_net", Currency.Type}}) '
            "in Typed"
        )
        staging_query = {
            "Name": "StagingSales",
            "Kind": "Query",
            "IsParameter": False,
            "Expression": staging_expression,
        }

        self.assertEqual(
            "M: Currency.Type",
            _source_type(_model(loaded_expression, extra_queries=[staging_query])),
        )

    def test_loaded_partition_can_reference_same_named_mashup_query(self) -> None:
        shared_query = {
            "Name": "Sales",
            "Kind": "Query",
            "IsParameter": False,
            "Expression": (
                "let Source = Sql.Database(\"server\", \"database\"), "
                'Typed = Table.TransformColumnTypes(Source, {{"amount_net", Currency.Type}}) '
                "in Typed"
            ),
        }

        self.assertEqual(
            "M: Currency.Type",
            _source_type(_model("Sales", extra_queries=[shared_query])),
        )

    def test_parameter_with_table_name_is_not_type_evidence(self) -> None:
        parameter = {
            "Name": "Sales",
            "Kind": "Parameter",
            "IsParameter": True,
            "Expression": '{{"amount_net", type text}}',
        }
        expression = (
            "let Source = Sql.Database(\"server\", \"database\"), "
            'Typed = Table.TransformColumnTypes(Source, {{"amount_net", Currency.Type}}) '
            "in Typed"
        )

        self.assertEqual(
            "M: Currency.Type",
            _source_type(_model(expression, extra_queries=[parameter])),
        )

    def test_model_display_name_is_not_used_without_source_column(self) -> None:
        expression = (
            "let Source = Sql.Database(\"server\", \"database\"), "
            'Typed = Table.TransformColumnTypes(Source, {{"Amount", Currency.Type}}) '
            "in Typed"
        )
        model = _model(expression)
        model.tmschema_columns[0]["SourceColumn"] = None

        self.assertIsNone(_source_type(model))

    def test_resolves_single_type_operation_stored_as_pair(self) -> None:
        expression = (
            "let Source = Sql.Database(\"server\", \"database\"), "
            'Operation = {"A", Int64.Type}, '
            "Typed = Table.TransformColumnTypes(Source, {Operation}) "
            "in Typed"
        )

        self.assertEqual(
            "M: Int64.Type",
            _source_type(
                _model(
                    expression,
                    table="Facts",
                    model_column="Friendly A",
                    source_column="A",
                )
            ),
        )

    def test_ambiguous_final_branch_does_not_guess_source_type(self) -> None:
        expression = (
            "let Source = Sql.Database(\"server\", \"database\"), "
            'AsNumber = Table.TransformColumnTypes(Source, {{"amount_net", Int64.Type}}), '
            'AsText = Table.TransformColumnTypes(Source, {{"amount_net", type text}}), '
            "Loaded = if UseNumeric then AsNumber else AsText "
            "in Loaded"
        )

        self.assertIsNone(_source_type(_model(expression)))

    def test_conditional_replace_type_does_not_guess_source_type(self) -> None:
        expression = (
            "let Source = Sql.Database(\"server\", \"database\"), "
            "Loaded = Value.ReplaceType(Source, if UseNumeric then "
            "type table [Code = Int64.Type] else type table [Code = type text]) "
            "in Loaded"
        )

        self.assertIsNone(
            _source_type(
                _model(
                    expression,
                    table="Facts",
                    model_column="Code label",
                    source_column="Code",
                )
            )
        )

    def test_replace_type_accepts_nullable_table_field_type(self) -> None:
        expression = (
            'let Source = Sql.Database("server", "database"), '
            'Loaded = Value.ReplaceType(Source, type table [#"Code value" = nullable number]) '
            "in Loaded"
        )

        self.assertEqual(
            "M: nullable number",
            _source_type(
                _model(
                    expression,
                    table="Facts",
                    model_column="Code label",
                    source_column="Code value",
                )
            ),
        )

    def test_from_rows_uses_its_declared_table_type(self) -> None:
        expression = (
            'Table.FromRows({{1}}, type table [#"Code value" = Int64.Type])'
        )

        self.assertEqual(
            "M: Int64.Type",
            _source_type(
                _model(
                    expression,
                    table="Facts",
                    model_column="Code label",
                    source_column="Code value",
                )
            ),
        )

    def test_replace_value_preserves_type_from_preceding_step(self) -> None:
        expression = (
            "let Source = Sql.Database(\"server\", \"database\"), "
            'Typed = Table.TransformColumnTypes(Source, {{"Code", Int64.Type}}), '
            'Replaced = Table.ReplaceValue(Typed, null, 0, Replacer.ReplaceValue, {"Code"}) '
            "in Replaced"
        )

        self.assertEqual(
            "M: Int64.Type",
            _source_type(
                _model(
                    expression,
                    table="Facts",
                    model_column="Code label",
                    source_column="Code",
                )
            ),
        )

    def test_long_schema_preserving_lineage_keeps_declared_type(self) -> None:
        steps = [
            'Source = Sql.Database("server", "database")',
            'S0 = Table.TransformColumnTypes(Source, {{"Code", Int64.Type}})',
        ]
        steps.extend(
            f"S{index} = Table.SelectRows(S{index - 1}, each true)"
            for index in range(1, 61)
        )
        expression = f"let {', '.join(steps)} in S60"

        self.assertEqual(
            "M: Int64.Type",
            _source_type(
                _model(
                    expression,
                    table="Facts",
                    model_column="Code label",
                    source_column="Code",
                )
            ),
        )

    def test_empty_transform_column_types_preserves_base_schema(self) -> None:
        expression = (
            "let Source = Sql.Database(\"server\", \"database\"), "
            'Typed = Table.TransformColumnTypes(Source, {{"Code", Int64.Type}}), '
            "Unchanged = Table.TransformColumnTypes(Typed, {}) "
            "in Unchanged"
        )

        self.assertEqual(
            "M: Int64.Type",
            _source_type(
                _model(
                    expression,
                    table="Facts",
                    model_column="Code label",
                    source_column="Code",
                )
            ),
        )

    def test_query_definition_is_equivalent_to_expression(self) -> None:
        expression = (
            "let Source = Sql.Database(\"server\", \"database\"), "
            'Typed = Table.TransformColumnTypes(Source, {{"Code", Int64.Type}}) '
            "in Typed"
        )
        for primary in ("", float("nan")):
            with self.subTest(primary=primary):
                model = _model(
                    expression,
                    table="Facts",
                    model_column="Code label",
                    source_column="Code",
                )
                model.power_query = [
                    {
                        "TableName": "Facts",
                        "Expression": primary,
                        "QueryDefinition": expression,
                    }
                ]

                self.assertEqual("M: Int64.Type", _source_type(model))

    def test_source_column_whitespace_is_not_normalized(self) -> None:
        expression = (
            'let Source = Sql.Database("server", "database"), '
            'Typed = Table.TransformColumnTypes(Source, '
            '{{"A B", type text}, {"A  B", Int64.Type}}) '
            "in Typed"
        )

        self.assertEqual(
            "M: type text",
            _source_type(
                _model(
                    expression,
                    table="Facts",
                    model_column="A label",
                    source_column="A B",
                )
            ),
        )

    def test_unsupported_reachable_transform_reports_partial_lineage(self) -> None:
        expression = (
            "let Source = Sql.Database(\"server\", \"database\"), "
            'Typed = Table.TransformColumnTypes(Source, {{"Code", Int64.Type}}), '
            "Loaded = Table.PromoteHeaders(Typed) "
            "in Loaded"
        )
        model = _model(
            expression,
            table="Facts",
            model_column="Code label",
            source_column="Code",
        )
        issues = []

        semantic = read_model_metadata(model, issues)

        self.assertIsNone(semantic.tables[0].columns[0].native_data_type)
        self.assertIn("M_TYPE_LINEAGE_PARTIAL", [issue.code for issue in issues])

    def test_cyclic_lineage_reports_partial_without_guessing(self) -> None:
        model = _model("let A = B, B = A in A")
        issues = []

        semantic = read_model_metadata(model, issues)

        self.assertIsNone(semantic.tables[0].columns[0].native_data_type)
        self.assertIn("M_TYPE_LINEAGE_PARTIAL", [issue.code for issue in issues])

    def test_conflicting_evidence_in_reachable_transformations_does_not_guess(self) -> None:
        expression = (
            "let Source = Sql.Database(\"server\", \"database\"), "
            'AsNumber = Table.TransformColumnTypes(Source, {{"amount_net", Int64.Type}}), '
            'AsText = Table.TransformColumnTypes(Source, {{"amount_net", type text}}), '
            "Loaded = Table.Combine({AsNumber, AsText}) "
            "in Loaded"
        )

        self.assertIsNone(_source_type(_model(expression)))

    def test_projected_field_registry_uses_source_column_and_its_m_type(self) -> None:
        source_model = _model(
            (
                "let Source = Sql.Database(\"server\", \"database\"), "
                'Types = {{"amount_net", Currency.Type}}, '
                "Typed = Table.TransformColumnTypes(Source, Types) "
                "in Typed"
            )
        )
        semantic = read_model_metadata(source_model, [])
        documentation = DocumentationModel(
            file=FileInfo(Path("fixture.pbix"), 1, "0" * 64, datetime.now(timezone.utc)),
            report=ReportInfo(
                "Fixture",
                "PBIX",
                None,
                pages=[PageInfo("page", "Overview", 0, True, "REPORT", 1280, 720)],
            ),
            semantic_model=semantic,
            issues=[],
            generated_at=datetime.now(timezone.utc),
            application_version="test",
        )

        projected = project_document(documentation)
        registry = next(
            block
            for block in projected.blocks
            if isinstance(block, TableBlock) and block.headers[:2] == ("Таблица[Поле]", "Варианты названий")
        )

        self.assertEqual("amount_net", registry.rows[0][6])
        self.assertEqual("Currency.Type", registry.rows[0][7])


if __name__ == "__main__":
    unittest.main()

# Data Compass Documentation Converter — target architecture

Status: **approved target**

This architecture defines a separate local converter for Data Compass exports based on Apache Superset. It does not change the locked PBIX Converter contract.

## 1. Product contract

```text
Input:  exactly one Data Compass / Superset dashboard export in .zip format
Output: exactly one human-readable .docx documentation file
```

The application runs locally inside the corporate Windows VDI. It does not call Data Compass, Superset APIs, databases, browsers, cloud services, external LLMs, or other network services. SQL, Jinja, YAML configuration, metrics, and calculated expressions are read as metadata and never executed.

The preferred executable is a separate `DataCompassConverter.exe`. It may share neutral document primitives, compact DOCX rendering, OOXML validation, runtime guards, and atomic publication with PBIX Converter. Superset parsers, normalized entities, reference resolution, and document projection remain separate from `pbix_doc`.

## 2. Processing pipeline

```text
GUI / CLI
    -> Offline Runtime Guards
    -> ZIP Preflight Validator
    -> Safe YAML Reader
    -> Export Version / Dialect Detector
    -> Versioned Superset / Data Compass Adapters
    -> UUID Reference Resolver
    -> Normalized Compass Model
    -> Compass Exposure Policy
    -> Model Validator
    -> Data Compass Document Projector
    -> Safe Document Model
    -> Compact DOCX Renderer
    -> OOXML Validator
    -> Atomic Writer
```

`DataCompassDocumentProjector` is the only interpretation boundary. The renderer receives only the safe document model and never reads ZIP members, YAML, SQL, or the normalized Compass model directly.

## 3. Input and parsing

The ZIP validator rejects unsafe paths, absolute paths, `..`, symlinks, encrypted members, case-insensitive duplicates, unsupported data-bearing files, excessive member counts, oversized members, excessive total expansion, and suspicious compression ratios. A single outer archive directory is allowed and normalized.

The expected object families are:

```text
metadata.yaml
databases/*.yaml
datasets/<database>/*.yaml
charts/*.yaml
dashboards/*.yaml
```

YAML is loaded without arbitrary object construction or custom tags. Duplicate keys, unsafe aliases, excessive nesting, and oversized strings are rejected. Embedded JSON strings such as chart parameters, query context, dashboard layout, and metadata are parsed with independent size and depth limits.

Unknown export versions, Data Compass extensions, or visualization types produce a partial result. The parser preserves the declared `viz_type` but extracts fields only from explicitly supported semantic keys. It must never interpret arbitrary scalar values such as `true`, hashes, or numeric codes as field or display names.

## 4. Normalized model and reference graph

```text
CompassModel
├── ExportManifest
├── Databases[]
├── Datasets[]
│   ├── Columns[]
│   └── Metrics[]
├── Charts[]
├── Dashboards[]
│   ├── Tabs[]
│   ├── LayoutComponents[]
│   └── NativeFilters[]
├── Interactions[]
├── SecurityMetadata[]
└── ExtractionIssues[]
```

References are resolved in two passes by UUID and explicit exported identifiers:

```text
Database -> Dataset -> Chart -> Dashboard -> Tab / Layout
```

File names and similar visible names are never used as authoritative joins. Numeric chart IDs may be resolved only through an explicit exported mapping to UUID. Missing or conflicting references remain unresolved and force partial extraction; fuzzy name matching is forbidden.

Document-local IDs are stable for deterministic input ordering:

- `D01` — dashboard;
- `T01` — tab;
- `V001` — visualization or meaningful layout element;
- `F01` — filter;
- `DS01` — dataset;
- `M001` — metric.

## 5. PBIX-to-Data Compass document mapping

The Data Compass DOCX follows the PBIX document structure wherever an equivalent entity physically exists:

| PBIX entity | Data Compass entity | Output rule |
|---|---|---|
| Report | Dashboard | Preserve |
| Page | Dashboard tab or main screen | Preserve |
| Visual | Chart, control, Markdown, meaningful layout element | Preserve |
| Model table | Dataset | Preserve |
| Column | Dataset column | Preserve |
| DAX measure | Saved or ad-hoc metric | Render as SQL/expression metadata |
| Calculated column | Dataset calculated column | Preserve |
| Report/page/visual filter | Native, scoped, chart, or time filter | Preserve when explicitly resolved |
| Bookmark | No exported equivalent | Omit completely |
| Power Query | Dataset/database metadata | Replace with datasets and sources |
| Power BI model relationship | No standard exported equivalent | Omit completely |
| RLS/OLS | Not present in the dashboard bundle | Omit completely unless reliable security metadata is physically exported |

No empty substitute tables or placeholder sections are created for absent entities.

## 6. DOCX output contract

The final document order is:

```text
1. Об отчёте
2. Фильтры уровня дашборда
3. Реестр полей и метрик
4. Состав дашборда, grouped by dashboard tab
5. Все фильтры
6. Переходы и взаимодействия, only when present
7. Одинаковые названия — возможные различия
8. Наборы данных и источники
9. Доступы и ограничения, only when physically exported
```

There is no separate cover page. Empty conditional sections are omitted.

### 6.1 Field and metric registry

The registry follows the PBIX registry layout with eleven columns:

```text
Набор данных[Поле/метрика]
Варианты названий
Тип данных в Data Compass
Объяснение
SQL/выражение
Упоминается на вкладке
Наименование поля в источнике
Тип данных в источнике
Используется в метриках
Используется в визуализациях
Варианты агрегаций
```

Ordinary columns, calculated columns, saved metrics, and ad-hoc metrics share this registry. Missing and inapplicable cells use an em dash. Exported dataset column type is Data Compass metadata and must not be presented as a verified current physical database type. Source names and types are rendered only when explicit local metadata proves them. SQL is never executed. The registry shows only the saved expression of the relevant metric or calculated column, not the complete raw virtual-dataset query.

### 6.2 Dashboard composition

`Состав дашборда` is grouped by tab name. If the layout has no tabs, one group named `Основной экран` is used.

Each group uses:

```text
Элемент
Название
Тип и назначение
Поля и метрики
Расчёт/SQL
Контекст и фильтры
Взаимодействия
Действие/цель
```

The PBIX bookmark column is not retained. Charts, KPI cards, tables, controls, Markdown blocks, headings, and dividers are included only when they carry user-visible meaning.

### 6.3 Filters and interactions

`Все фильтры` centralizes native dashboard filters, chart filters, time filters, ad-hoc filters, cascade dependencies, scope, exclusions, operators, and saved values. Composition tables show filter context without duplicating saved literals.

`Переходы и взаимодействия` is created only for explicitly exported cross-filter edges, drill actions, tab or dashboard transitions, and safe textual URL actions. It is not a replacement for bookmarks. If no supported action exists, the section is absent.

### 6.4 Same-name comparison

Objects with the same visible name are compared by dataset, expression, aggregation, visualization filters, dashboard filters, time range, time grain, and post-processing. The document distinguishes different definitions from identical definitions used in different contexts.

### 6.5 Datasets and sources

The source section contains logical database name, dataset name, physical or virtual classification, catalog/schema/table metadata, main temporal field, column and metric counts, consuming visualizations, and the presence of a virtual query. It does not reproduce credentials or raw connection definitions.

## 7. Exposure and security policy

Connection URIs, usernames, passwords, tokens, OAuth data, `encrypted_extra`, `masked_encrypted_extra`, SSH parameters, private keys, certificates, and credential-shaped nested values are excluded before the normalized model is created. User, owner, editor, and viewer identities are not rendered.

SQL and Jinja are not executed. Filter literals may be rendered only from saved configuration, subject to the same bounded-value policy used for PBIX documentation. No database rows, query results, CSV, or Parquet data enter the document.

Before atomic publication, the DOCX is validated to contain no macros, OLE/ActiveX objects, remote templates, external relationships, external hyperlinks, automatic fields, unsafe package members, or surviving credential-shaped values. Failure, cancellation, cleanup failure, validation failure, or exposure-policy failure leaves any existing DOCX unchanged.

## 8. Completeness and testing

The converter uses `OK`, `PARTIAL`, and `FAILED` internally. Unsupported chart parameters, unresolved UUIDs, or unknown extensions produce `PARTIAL` when meaningful documentation remains. A malformed archive, unsafe YAML, surviving secret, or missing dashboard produces `FAILED` and no publication.

Implementation support cannot be described as complete until tests cover sanitized or synthetic examples of `metadata`, database, physical and virtual datasets, representative chart types, dashboard layout, native filters, and interactions. Security fixtures must cover ZIP path attacks, compression limits, duplicate YAML keys, aliases, secrets in nested configuration, damaged UUID references, unknown visualization types, temporary-file cleanup, OOXML rejection, and preservation of an existing DOCX on failure.

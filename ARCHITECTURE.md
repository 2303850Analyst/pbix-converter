# PBIX Documentation CLI — locked architecture baseline

Status: **approved and locked**
Baseline version: **2.1**
Change rule: this document changes only after explicit user approval.

## 1. Product contract

The program is a Python application developed in PyCharm and executed inside a corporate Citrix VDI Windows session.

```text
Input:  exactly one Power BI report in .pbix format
Output: exactly one human-readable .docx documentation file
```

Runtime requirements:

- offline processing inside the VDI trust boundary;
- no Power BI Service or Fabric API;
- no external LLM;
- no network requirement;
- no mandatory Power BI Desktop;
- no execution of DAX, M, SQL, custom visual code, or user scripts;
- no persistent extraction folders, JSON dumps, CSV files, or table rows;
- local temporary files are allowed and removed after processing.

### 1.1 VDI_OFFLINE_RUNTIME policy

"Local" means inside the remote Windows session, not on the user's physical endpoint. The browser and Citrix Workspace provide access to the VDI desktop; the application process and PBIX processing run in that Windows environment.

The application:

- accepts only paths explicitly selected by the user in the VDI Explorer or passed to the CLI;
- does not impose hard-coded drive, root-folder, UNC, mapped-drive, or reparse-point restrictions;
- may use any corporate filesystem location exposed and authorized by the VDI environment;
- does not create HTTP endpoints, make HTTP requests, open sockets, send webhooks, use telemetry, invoke cloud APIs, or contain application logic that launches external processes;
- does not invoke Git, package managers, browsers, Yandex Disk, or other sync clients at runtime;
- installs process-wide Python audit guards at the production entry point, before GUI, controller, parser, or third-party analysis imports;
- treats every runtime-policy violation as a fatal error that cannot be downgraded to partial extraction;
- produces no permanent logs and does not include the absolute source path in the DOCX;
- creates its extraction workspace beside the user-selected output and verifies its removal before publication; a cleanup failure is fatal and prevents DOCX publication, while abandoned application-owned workspaces are retried on a later run;
- keeps the atomic output temporary file beside the final DOCX.

`NetworkEgressGuard` rejects Python socket creation, connection, DNS/service resolution, datagram sending, and `urllib` requests. Its purpose is to make accidental application or dependency network access fail closed. `ExternalProcessGuard` is a separate policy that rejects process-launch audit events so application code or a dependency cannot delegate transmission to another executable. Both violations use a dedicated fatal exit path.

The audit guards are application-level regression controls, not a sandbox. They do not govern native DLL networking that bypasses CPython audit events, code executed before the production entry point, other processes, Citrix channels, or filesystem access to a user-selected UNC/mapped path. A selected network path may cause Windows to use SMB; this is an explicit user-directed filesystem operation, not autonomous application egress. Citrix clipboard, client-drive, USB, printing, session recording, network policies, firewall, and AppLocker/WDAC are controlled by VDI administrators and remain the authoritative outer enforcement layer.

Development may use Git and synchronized development folders, but real corporate PBIX files must not be processed there. The preferred VDI release is a frozen Windows x64 folder containing `PBIXConverter.exe`, an embedded CPython runtime, pinned dependencies, and checksums. It contains no `.git`, source report, package manager, or updater and performs no download during execution. The wheelhouse-based offline package remains available as a fallback delivery format.

## 2. Processing pipeline

```text
CLI / Controller
    -> Network Egress Guard
    -> External Process Guard
    -> Preflight Validator
    -> PBIX Container Reader
       -> Report Format Detector
          -> PBIR Parser
          -> Legacy Layout Parser
       -> PBIXRay Semantic Model Adapter
          -> Optional Data Profiler
    -> Normalizer
    -> Field Resolver
    -> Value Exposure Policy
    -> Validation Layer
    -> Document Projector
    -> Safe Document Model
    -> Deterministic DOCX Renderer
    -> OOXML Validator
    -> Atomic Writer
```

`DocumentProjector` receives the normalized and policy-safe domain model and derives the field registry, page-grouped composition, unified bookmark/navigation and filter registers, same-name comparison groups, explicit context evidence, and document-local references. The DOCX renderer receives only `SafeDocumentModel`. It must not access raw PBIX members, raw report JSON, DataFrames containing table rows, the normalized PBIX model, or unsanitized values.

Immediately after the compact report overview, the projector emits a single field registry. It combines every locally discovered semantic column, measure, calculated column, report field reference, filter reference, visual usage, aggregation, and DAX dependency. NativeQuery and Power Query are never executed. Their locally stored definitions may enter the safe document model and DOCX as explicitly approved metadata code after recognized credentials, tokens, API keys, client secrets, and SAS signatures are replaced with `<СКРЫТО>`.

## 3. Input handling

The preflight layer verifies:

- `.pbix` extension;
- existence and readability;
- output path differs from input;
- supported container signature;
- duplicate or suspicious archive members;
- configured size and decompression limits;
- protected, encrypted, corrupt, or unsupported input state.

Input and output paths are user-controlled and are not limited to named disks or directories. This is intentional because the VDI may expose local profile storage, mapped corporate drives, or approved network folders. The application never scans unrelated locations and never chooses an external destination itself.

The container is opened read-only. Member paths are normalized for separators and comparison while original names are retained for evidence paths.

## 4. Report adapters

### 4.1 PBIR adapter

The PBIR adapter reads versioned report, page, visual, bookmark, and mobile-layout JSON. Parsing is selected by `$schema` and `version.json`. Unknown schema versions produce a partial result rather than an assumed successful parse.

### 4.2 Legacy Layout adapter

The Legacy adapter reads `Report/Layout`, including UTF-16LE content and recursively encoded JSON strings. Semantic expressions are interpreted by an AST-aware parser with source-alias resolution. A generic JSON walker is only a discovery fallback and its findings have reduced confidence until resolved.

Both adapters return the same domain entities: `Report`, `Page`, `Visual`, `FieldUsage`, `Filter`, `Action`, and `ExtractionIssue`.

Bookmarks and navigation are mandatory extraction targets. The adapters preserve:

- bookmark ID, display name, order, and group;
- the page associated with the bookmark;
- saved-state scope (`data`, `display`, `current page`, or their available equivalents);
- affected visuals without exposing saved data literals;
- buttons, shapes, images, and navigators that activate the bookmark;
- page navigation, bookmark navigation, drillthrough, and report-tooltip targets.

## 5. Semantic model adapter

PBIXRay is isolated behind `SemanticModelAdapter`. Its version is pinned and covered by adapter contract tests.

The adapter may expose:

- tables and columns;
- data types and model properties;
- measures and DAX;
- calculated columns and tables;
- hierarchies;
- relationships;
- Power Query metadata;
- RLS and OLS definitions;
- connection metadata;
- model status.

For every locally available column, the adapter also normalizes the Power BI data type, display format, default summarization, `SourceColumn`, and the type explicitly declared for that source field in the returned Power Query result. The static resolver evaluates only the final `let … in` branch, uses case-sensitive M identifiers, follows constant operation lists, renames, and referenced staging queries, and matches the resulting field to the exact model `SourceColumn`. Supported evidence includes `Table.TransformColumnTypes`, `Table.TransformColumns`, typed `Table.AddColumn`, and unambiguous `type table` schemas. SQL `CAST`/`CONVERT` is not used for this registry column because it does not prove the final Power Query output type. Missing type evidence renders as an em dash; conditional, conflicting, or unsupported reachable lineage also records partial extraction. The adapter must not execute M/SQL or infer a source name or type from business values.

Supported model statuses:

```text
EMBEDDED
THIN_REPORT
LIVE_CONNECTION
NO_MODEL
PARTIAL
FAILED
```

A missing embedded model must not prevent report-layout documentation.

## 6. Data profiler

Data profiling is a separate, optional stage. It may read eligible embedded columns to calculate display-safe examples but must never persist or render raw rows.

Default policy:

| Column kind | Output examples |
|---|---|
| Categorical text | Up to five most frequent values |
| Boolean | Up to two values |
| Numeric, including low-cardinality numeric category | Hidden |
| Continuous numeric | Hidden |
| Date/time | Hidden by default |
| ID, key, GUID, account, phone, email | Hidden |
| Measure | Not evaluated |
| Remote/thin model column | Not available locally |

Exact top values are calculated only for eligible columns. Large eligible columns are processed in bounded chunks. Frequency counts are not rendered by default. Ties are ordered deterministically.

## 7. Value exposure policy

`ValueExposurePolicy` is the sole authority for values in the normalized output model.

Default behavior:

```text
Raw table rows:                  never output
Measure results:                never calculated or output
Saved filter literals: up to twenty distinct metadata values per object
Bookmark literals: only with a reliable qualified-field association, under the same cap
Continuous numeric values:      hidden
Identifiers and keys:           hidden
Categorical examples:           maximum five
Connection secrets:             removed
DAX definitions:                included as explicitly approved metadata code
Power Query/NativeQuery code:   included in full after secret-value redaction
```

Report object counts, coordinates, model cardinality, technical versions, and other metadata are not treated as business fact values.

Filter and bookmark literals are read only from report metadata, normalized by the report adapter, and capped before reaching the projector. Bookmark literals are user-facing only when they can be associated with a qualified field. They never come from table-row profiling. Disallowed column values are discarded before the normalized model reaches the projector.

## 8. Normalized domain model

```text
DocumentationModel
├── FileInfo
├── ExtractionSummary
├── Report
│   ├── report_filters[]
│   ├── bookmarks[]
│   ├── navigation_edges[]
│   └── pages[]
│       ├── page_filters[]
│       ├── field_usages[]
│       └── visuals[]
│           ├── layout
│           ├── fields[]
│           ├── filters[]
│           └── actions[]
├── SemanticModel
│   ├── tables[]
│   │   └── columns[]
│   │       └── safe profile
│   ├── measures[]
│   ├── relationships[]
│   ├── hierarchies[]
│   ├── power_query[]
│   ├── rls[]
│   └── ols[]
└── ExtractionIssue[]
```

Every field use retains its report scope, usage type, page/visual ID, role, aggregation, evidence path, and resolution status.

Each normalized semantic column may additionally retain `format_string`, `source_column`, `native_data_type`, and `summarize_by`. These are metadata only. A normalized query may retain its full secret-redacted M/NativeQuery definitions, connector classes, server/database/path metadata, and secret-redacted connection strings. Recognized passwords, tokens, API keys, client secrets, account keys, authorization values, URL passwords, and SAS signatures must never enter the safe document model in clear text.

### 8.1 Document projection and evidence taxonomy

`DocumentProjector` is a deterministic interpretation boundary. It may derive only facts supported by normalized metadata and produces:

- short stable document-local IDs for pages, visuals, bookmarks, and navigation edges;
- usage locations and explicit report/page/visual/bookmark context evidence;
- page-grouped composition tables for visuals, controls, and bookmarks;
- complete bookmark and filter registers;
- groups of identical visible titles used with different semantic objects or contexts;
- incoming and outgoing navigation indexes and direct user journeys;
- `PARTIAL`, `UNRESOLVED`, and overflow markers.

The field registry is mandatory and appears immediately after `Об отчёте`. Its columns are fixed:

```text
Таблица[Поле]
Варианты названий
Тип данных в PBI
Объяснение
DAX
Упоминается на листе
Наименование поля таблицы в источнике
Тип данных в источнике
Используется в DAX
Используется в визуалах
Варианты агрегаций
```

The first header is rendered as `Таблица[Поле]`. The registry includes model objects even when they are unused by a visual, except service objects whose table name starts with `Local`, `DateTableTemplate`, `H$`, `R$`, or `U$`. The PBI type cell contains only the normalized type value, without object-kind or display-format prefixes; numeric TOM type codes are mapped to their names. Cross-references use the document-local page and visual IDs together with their typed human-readable names. DAX expressions use the normal token-aware sanitization policy, and the dependency column lists formula object names rather than evaluated results. The explanation column is intentionally empty and is never populated automatically. Missing or inapplicable type/source cells contain an em dash. Numeric aggregation enums are normalized and deduplicated into comma-separated names such as `По умолчанию, SUM, COUNT` or `Без агрегации`; bare enum numbers are not user-facing output.

Immediately after the field registry, the projector emits `Состав листа` as one titled group per report page. Each page group contains a table with the fixed columns:

```text
Элемент
Название
Тип и назначение
Поля и показатели
Расчёт / DAX
Контекст и фильтры
Связанные закладки
Действие / переход
```

The page name and ID are carried by the group heading, so the repeated `Лист` column is omitted. The `Элемент` cell is the sole place where a bare local ID is sufficient. The composition groups are followed by the unified `Переходы и действия (Все закладки)` register and the complete `Все фильтры` register.

In mixed text every reference contains the explicit entity type, a bold human-readable name in guillemets, and the local ID: `лист «Обзор» (P01)`, `закладка «Исходный вид» (B01)`, or `кнопка «Сбросить представление» (V010)`. A dedicated column may omit the repeated type word but retains the quoted name and ID. Technical references such as `Sales[Region]` remain unquoted. Human-readable model-table, query, role, page, bookmark, and visual names are quoted. The wording remains complete when Confluence strips bold formatting.

Missing visual titles and technical hash/UUID titles are replaced everywhere with `Без названия N`, where `N` is derived from the stable document-local visual ID (`V014` -> `Без названия 14`). Technical titles are not treated as visible names and therefore do not create groups in the same-name comparison section. Bookmark rows include the associated page, captured scope, affected elements, and controls that invoke them when those links are available in metadata. Bookmark saved literals are not presented as field values unless metadata provides a reliable field association.

The comparison taxonomy is fixed:

```text
DEFINITION_DIFFERENCE       different semantic source, DAX, dependency, role, or aggregation
POTENTIAL_CONTEXT_INFLUENCE different discovered report/page/visual/bookmark context
UNRESOLVED                  available metadata is insufficient
```

These statuses describe structural metadata. They never assert that two evaluated values are different or that a structural difference is the proven cause of a business result.

DAX is metadata code explicitly permitted by `ValueExposurePolicy`. A token-aware sanitizer removes comments and string literals while preserving structural identifiers, operators, functions, and numeric constants. Dependencies are marked with extraction status. If tokenization is malformed or safe rendering cannot be established, the formula is omitted and marked `PARTIAL`.

Document budgets limit DAX length, dependency count, table rows, visual count, and total OOXML package size. Any truncation is deterministic and rendered with an explicit overflow marker.

## 9. Bookmarks and navigation graph

The DOCX must make page relationships understandable in both directions.

For every bookmark, output:

- name, ID, group, and order;
- associated page;
- saved-state scope;
- affected visual IDs or an aggregate description;
- every report element that activates the bookmark;
- whether an entry point was found in report metadata.

For every navigation edge, output:

```text
Source page -> source visual -> action type -> target object -> related target page
```

Supported edge categories include:

- page navigation;
- bookmark navigation;
- bookmark navigator;
- page navigator;
- drillthrough;
- report-page tooltip;
- back action when its target is available;
- web URL as an external target without treating it as a report page; only scheme, host, port, and path may enter the safe model, while credentials, query parameters, and fragments are removed.

The processor builds a directed navigation graph and derives outgoing and incoming connections for each page. It reports missing bookmark/page targets, bookmarks with no discovered report entry point, links to hidden pages, and navigation cycles as diagnostics. A bookmark without a discovered button is not automatically invalid because it can still be used from the bookmark pane or other Power BI state.

Saved slicer/filter values inside bookmarks may be shown only when report metadata reliably associates them with a qualified field, under the same twenty-value cap. Raw table rows and evaluated measure results remain prohibited.

## 10. RLS and OLS output

The RLS/OLS section is mandatory in the DOCX. It documents locally readable model definitions without a provisional or placeholder label. It does not claim current Power BI Service assignments or the effective access of a particular user.

Minimum output:

- whether RLS metadata was found;
- whether OLS metadata was found;
- role names, affected model objects, and locally readable expressions when available;
- model availability (`EMBEDDED`, `DIRECTQUERY`, `THIN/LIVE`, or unknown);
- no login, UPN, account, or role-member values.

Detailed dynamic-RLS topology analysis depends on the metadata available inside the selected PBIX.

RLS documentation includes locally available role definitions, affected tables, DAX filter expressions, state, and metadata permission. It does not claim which real users are assigned to roles in Power BI Service.

The analyzer must distinguish at least two RLS patterns:

```text
STATIC_ROLE_FILTER
DYNAMIC_IDENTITY_FILTER
DYNAMIC_TABLE_MAPPING
UNKNOWN
```

For dynamic table mapping, the DOCX additionally documents:

- the security/access table;
- identity columns such as login, UPN, or account name;
- access-scope columns such as region, department, company, or entity key;
- identity functions found in DAX, such as `USERPRINCIPALNAME()` or `USERNAME()`;
- other supported identity functions found in DAX, including `USEROBJECTID()` and `CUSTOMDATA()` when present;
- relationships through which the security filter reaches dimensions and fact tables;
- whether bidirectional security propagation is declared in model metadata;
- any ambiguity, disconnected path, or inactive relationship.

Login/UPN values from the mapping table are identifiers and must not be printed as examples. Aggregate metadata such as column cardinality may be shown under the normal value policy.

When mapping rows are locally embedded and security profiling is enabled, the analyzer may calculate non-identifying quality summaries without retaining identities:

- blank identity count;
- duplicate identity/scope mapping count;
- identity format classification (`UPN-like`, `DOMAIN\\login`, object-ID-like, or mixed);
- orphaned access keys that do not resolve to the secured dimension;
- inactive, ambiguous, or disconnected security paths.

These checks never print an actual login. A likely mismatch between the identity function and the detected format is a warning, not a claim about effective access.

Availability depends on the model mode:

- embedded Import model: role definitions, topology, and allowed aggregate quality checks can be available;
- DirectQuery/composite model: definitions and topology can be available, while source rows and login quality statistics are normally unavailable without executing the source query, which this application does not do;
- thin/live report: the remote model's RLS/OLS definitions and mapping table can be absent from the PBIX, so the section is marked `NOT_AVAILABLE` or `PARTIAL`.

Power BI Service can still assign users or groups to the generic dynamic role. Those current service-side assignments are not reliably available from a local PBIX. The local document therefore describes how an authenticated identity would be mapped, not who currently has access in the service.

OLS documentation includes role, protected table or column, object scope, and permission. It documents model definitions, not the effective permissions of a particular signed-in user.

OLS is separate from a login mapping table: native OLS hides tables or columns through role object permissions. A mapping table that limits rows is RLS even when it contains columns named `Role`, `Profile`, or `AccessLevel`. If no native OLS metadata exists, the OLS section states that no OLS definitions were found.

If the PBIX does not contain the required model metadata, the section is marked `NOT_AVAILABLE` or `PARTIAL`.

## 11. Internal unresolved-reference handling

An unresolved reference is a field-like report reference that was discovered but could not be mapped with sufficient confidence to a semantic-model object. It remains available to validation and console diagnostics but is not rendered in the final DOCX.

Typical causes:

- thin/live report without a local model;
- custom visual metadata with a proprietary binding shape;
- field parameter or dynamic expression not fully understood;
- source alias that cannot be reconstructed;
- model object missing or renamed;
- a newer unsupported PBIR expression version;
- partial model extraction.

Unresolved does not automatically mean that the report is broken. Each entry contains the discovered name, location, reason, and evidence path.

## 12. Diagnostics

Diagnostics describe extraction quality, not Power BI business results. They include:

- component status (`OK`, `PARTIAL`, `NOT_AVAILABLE`, `FAILED`);
- counts of pages, visuals, field references, and resolved references;
- detected formats and versions;
- skipped or unsupported structures;
- bookmark groups, navigation edges, missing targets, and hidden-page transitions;
- detected RLS pattern and security propagation paths;
- redacted value categories;
- profiling omissions;
- nonfatal parser/model warnings;
- critical read or write errors;
- application and dependency versions.

Diagnostics are printed to the console but the internal extraction-diagnostics table is not included in the final DOCX. No permanent log file is created by default. GUI and CLI errors contain a concise message without a traceback. Unresolved-reference evidence paths in the DOCX refer only to internal PBIX members, not to absolute Windows paths.

## 13. DOCX output contract

```text
1. Compact report overview and extraction completeness
2. Complete field registry
3. Page composition grouped by page
4. Unified bookmark, action, and navigation register
5. Complete filter register, including report-level filters
6. Same visible name — possible structural or context differences
7. Semantic model and relationships
8. Power Query and sources
9. RLS and OLS
```

The document starts with the compact report overview; no separate cover page is emitted.

`Состав листа` immediately follows the field registry. Each page has its own heading and an eight-column table: element, name, type/purpose, fields/measures, calculation/DAX, context/filters, related bookmarks, and action/target. Visual filter context is rendered only as scope plus qualified field (`Фильтр листа по полю Table[Field]`); saved literals are centralized in `Все фильтры`. Filter literals are extracted from the semantic condition subtree rather than from the whole visual configuration, and known condition operators are rendered in user-facing language. The `Элемент` column contains only the local ID. The following `Переходы и действия (Все закладки)` table is the single navigation register. Bookmark rows document associated pages, entry points, affected elements, saved scope, and user-visible effect. Non-bookmark rows document page, Tooltip, drillthrough, and other navigation targets; bookmark-navigation edges are not duplicated. `Все фильтры` documents report, page, and visual filters with their qualified fields, condition/type, saved values, scope of application, related bookmarks, and extraction status.

Power Query discovery reads both PBIXRay `power_query` and optional DataMashup `mashup_queries` metadata. The safe model retains query name, query kind, recognized connector class, server/database/path metadata, secret-redacted connection strings, and complete secret-redacted M/NativeQuery definitions. The DOCX renders the connection string and every available M definition together in the `Строка подключения` cell of the corresponding query row. These definitions are copied as metadata and are never evaluated. Output is still bounded by the validated OOXML package limits; no application network request or external process is introduced.

The DOCX does not expose the PBIX SHA-256, component/library versions, or the internal value-profile name. Calculation DAX remains available in the field registry and page composition; no separate KPI/calculation catalog is emitted.

The DOCX uses the `compact_reference_guide` design preset with an Arial 10 pt body, built-in `Heading 1`–`Heading 3`, a monospaced code style for DAX, and native Word tables. All sections use A4 landscape orientation, narrow margins, compact paragraph spacing, and reduced cell padding. Ordinary tables expand to the available landscape width; the field registry remains an 11-column compact table and page composition uses eight columns. All tables have explicit fixed geometry, repeated header rows, and no merged or nested cells. Registry and page-composition rows may continue across pages when their sanitized DAX is long. Color is never the sole carrier of meaning. Critical navigation does not depend on Word hyperlinks surviving Confluence import.

The package is macro-free OOXML. Before publication, `OOXMLValidator` rejects macros, OLE/ActiveX or embedded objects, remote templates, external relationships, external hyperlinks, automatic fields, unsafe member paths, duplicate members, invalid XML, disallowed control characters, and package or part size violations. The writer uses a same-directory temporary file, validates it, fsyncs it, and performs atomic replacement where the filesystem supports it.

The report overview contains the PBIX filename, report format, generation time, extraction completeness, object counts, resolved-field count, and semantic-model availability. It does not contain the absolute input path, file hash, component versions, or internal policy names.

The DOCX is intended for manual import through the built-in Word import in Atlassian Confluence Data Center. Exact fonts, widths, page breaks, and internal Word navigation may be restyled by Confluence; the heading hierarchy, visible document-local IDs, native tables, and explicit textual destinations are the durable contract.

## 14. Exit behavior

```text
0 = complete successful extraction
2 = usable partial documentation was produced
1 = input could not be read
3 = output could not be written
4 = a VDI runtime policy blocked a network or external-process operation
```

Partial output is preferred whenever at least one meaningful subsystem succeeded.

## 15. Project layout

```text
src/pbix_doc/
├── __main__.py
├── bootstrap.py
├── cli.py
├── gui.py
├── pipeline.py
├── runtime.py
├── container.py
├── report.py
├── semantic.py
├── domain.py
├── policy.py
├── projector.py
├── ooxml.py
└── renderer.py

tests/
└── test_converter.py

scripts/
├── build_offline_release.ps1
├── build_windows_exe.ps1
├── common.ps1
├── install_offline.ps1
├── setup_offline.ps1
├── run_pbix_doc.ps1
└── start_pbix_doc.cmd
```

Direct production dependencies are pinned in `pyproject.toml`; the complete tested transitive set and permitted wheel hashes are pinned in `requirements.lock` for Windows x64 and CPython 3.11–3.13. Each release archive targets one CPython minor version. The build backend is separately pinned by version and hash in `requirements-build.lock`. The release builder downloads only hash-matching binary wheels on a separate build machine, builds the application wheel in an isolated environment, and emits `SHA256SUMS`. VDI setup verifies the manifest, creates a local `.venv`, and uses pip with `--no-index` and the bundled wheelhouse.

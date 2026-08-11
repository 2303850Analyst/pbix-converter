# Project instructions

## Locked architecture

- `ARCHITECTURE.md` is the approved architecture baseline for this project.
- Treat the baseline as locked. Do not change its input/output contract, local-only execution model, layer boundaries, partial-result behavior, or value-exposure policy unless the user explicitly approves the change.
- The application accepts one `.pbix` file and produces one `.docx` file intended for manual import into Atlassian Confluence Data Center.
- Power BI Desktop, Power BI Service, Fabric APIs, cloud APIs, external LLMs, and browser automation are not runtime dependencies.
- Raw table rows must never be written to the output or to persistent intermediate files.
- Output examples are limited by `ValueExposurePolicy`; the default is at most five popular categorical values and no continuous numeric values.
- Parsers and model readers must produce the normalized domain model. `DocumentProjector` is the only layer that derives user-facing KPI lineage, context comparisons, and navigation indexes. The DOCX renderer must never read raw PBIX parts, raw table rows, or the normalized PBIX model directly.
- Unknown or unsupported metadata must be reported as partial extraction or an unresolved reference, never silently discarded or presented as resolved.
- Bookmark and navigation documentation is mandatory. Preserve bookmark groups, associated pages, triggering visuals, page/bookmark/tooltip targets, and both outgoing and incoming page relationships.
- A complete field registry is mandatory immediately after report-level filters. Keep its approved 11 columns and order: field, aliases, PBI data type, blank developer explanation, DAX, pages, source name, source type, DAX usage, visual usage, and aggregation variants. Omit `Local*` and `DateTableTemplate_*` service objects. Derive source names/types only from local metadata without executing or rendering raw M/SQL; use an em dash for unavailable or inapplicable user-facing cells. Render aggregation variants as comma-separated names, never numeric codes.
- A single `Состав листа` table is mandatory immediately after the field registry. Render one row per visual, control, or bookmark with the approved columns: page, element, name, type/purpose, fields/measures, calculation/DAX, context/filters, related bookmarks, and action/target. A bare local ID is allowed only in the `Элемент` column; every other cross-reference must use `Human-readable name (ID)`.
- Saved filter and bookmark literals may be rendered from report metadata, capped at twenty distinct values per object. This permission does not change the raw-row or column-example policy.
- The DOCX uses compact landscape sections throughout; ordinary tables expand to the available landscape width.
- RLS/OLS documentation renders locally extracted role definitions, affected model objects, DAX filters, and permissions without a provisional or placeholder label. Never claim current Power BI Service user assignments or render login/UPN values.
- The final DOCX omits unresolved references, the internal extraction-diagnostics table, the technical-ID legend, and the final aggregate-status banner. These diagnostics remain available to the application and console.
- Keep the permanent output to a single macro-free `.docx`. Temporary local files are allowed only when required for processing and must be removed after use.
- The DOCX package must contain no macros, OLE/ActiveX objects, remote templates, external relationships, external hyperlinks, or automatic fields. Validate the OOXML package before atomic publication.

## Testing

- Never describe extraction support as complete without representative PBIX fixtures and passing tests.
- Maintain unit tests, integration fixtures, and golden DOCX outputs, including structural OOXML checks and rendered-page review for representative deliverables.
- A synthetic fixture must always be labeled as synthetic and must never be represented as a real or validated PBIX file.

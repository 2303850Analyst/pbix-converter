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
- A complete field registry is mandatory immediately after report-level filters. Keep its approved 11 columns and order: field, aliases, PBI data type, blank developer explanation, DAX, pages, source name, source type, DAX usage, visual usage, and aggregation variants. Omit rows whose first-cell value starts with `Local`. Derive source names/types only from local metadata without executing or rendering raw M/SQL, and explain `Не применимо` / `NOT_AVAILABLE` in the affected source cells.
- RLS/OLS is a formal placeholder until the first production PBIX run. Never claim complete corporate-security coverage or render login/UPN values; revise the detailed security contract only from observed production metadata and explicit user approval.
- Keep the permanent output to a single macro-free `.docx`. Temporary local files are allowed only when required for processing and must be removed after use.
- The DOCX package must contain no macros, OLE/ActiveX objects, remote templates, external relationships, external hyperlinks, or automatic fields. Validate the OOXML package before atomic publication.

## Testing

- Never describe extraction support as complete without representative PBIX fixtures and passing tests.
- Maintain unit tests, integration fixtures, and golden DOCX outputs, including structural OOXML checks and rendered-page review for representative deliverables.
- A synthetic fixture must always be labeled as synthetic and must never be represented as a real or validated PBIX file.

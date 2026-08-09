# Project instructions

## Locked architecture

- `ARCHITECTURE.md` is the approved architecture baseline for this project.
- Treat the baseline as locked. Do not change its input/output contract, local-only execution model, layer boundaries, partial-result behavior, or value-exposure policy unless the user explicitly approves the change.
- The application accepts one `.pbix` file and produces one `.txt` file.
- Power BI Desktop, Power BI Service, Fabric APIs, cloud APIs, external LLMs, and browser automation are not runtime dependencies.
- Raw table rows must never be written to the output or to persistent intermediate files.
- Output examples are limited by `ValueExposurePolicy`; the default is at most five popular categorical values and no continuous numeric values.
- Parsers and model readers must produce the normalized domain model. The TXT renderer must never read raw PBIX parts or raw table rows.
- Unknown or unsupported metadata must be reported as partial extraction or an unresolved reference, never silently discarded or presented as resolved.
- Bookmark and navigation documentation is mandatory. Preserve bookmark groups, associated pages, triggering visuals, page/bookmark/tooltip targets, and both outgoing and incoming page relationships.
- RLS/OLS is a formal placeholder until the first production PBIX run. Never claim complete corporate-security coverage or render login/UPN values; revise the detailed security contract only from observed production metadata and explicit user approval.
- Keep the default permanent output to a single `.txt`. Temporary local files are allowed only when required for processing and must be removed after use.

## Testing

- Never describe extraction support as complete without representative PBIX fixtures and passing tests.
- Maintain unit tests, integration fixtures, and golden TXT outputs.
- A synthetic fixture must always be labeled as synthetic and must never be represented as a real or validated PBIX file.

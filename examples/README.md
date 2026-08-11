# Synthetic PBIX fixture

`synthetic_representative.pbix` is a deterministic ZIP/PBIR fixture for the complete local conversion path. It contains:

- three pages, including a hidden tooltip page;
- standard visuals, slicers, buttons and one synthetic custom visual;
- two visuals named `Выручка` with different semantic bindings and page context;
- report, page and visual filters with saved values used to verify metadata rendering;
- grouped bookmarks, page navigation, bookmark navigation and tooltip navigation.

The fixture is synthetic and contains no binary `DataModel`. Expected semantic status: `NO_MODEL`; expected overall status: `PARTIAL`. It is not claimed to be openable by Power BI Desktop.

Rebuild and convert:

```powershell
python examples\build_synthetic_pbix.py
python main.py examples\synthetic_representative.pbix examples\synthetic_representative_documentation.docx
```

`synthetic_representative_documentation.docx` is the reviewed golden example intended for manual import into Atlassian Confluence Data Center.
Its field registry is populated from the report bindings and demonstrates both `SUM` and `COUNT`. Missing PBI type and source metadata is rendered as an em dash because this synthetic fixture intentionally has no embedded `DataModel`.
The `Состав листа` section is grouped by page and followed by the complete bookmark and filter registers. Cross-references use an explicit entity type, a quoted human-readable name, and a local ID, for example `лист «Обзор» (P01)`.

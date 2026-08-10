# Synthetic PBIX fixture

`synthetic_representative.pbix` is a deterministic ZIP/PBIR fixture for the complete local conversion path. It contains:

- three pages, including a hidden tooltip page;
- standard visuals, slicers, buttons and one synthetic custom visual;
- two visuals named `Выручка` with different semantic bindings and page context;
- report, page and visual filters with values that must remain hidden;
- grouped bookmarks, page navigation, bookmark navigation and tooltip navigation.

The fixture is synthetic and contains no binary `DataModel`. Expected semantic status: `NO_MODEL`; expected overall status: `PARTIAL`. It is not claimed to be openable by Power BI Desktop.

Rebuild and convert:

```powershell
python examples\build_synthetic_pbix.py
python main.py examples\synthetic_representative.pbix examples\synthetic_representative_documentation.docx
```

`synthetic_representative_documentation.docx` is the reviewed golden example intended for manual import into Atlassian Confluence Data Center.
Its field registry is populated from the report bindings and demonstrates both `SUM` and `COUNT`. PBI type and source cells explain `NOT_AVAILABLE` because this synthetic fixture intentionally has no embedded `DataModel`.

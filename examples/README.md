# Representative report fixture

`representative_report_fixture.json` is a synthetic normalized report fixture. It is intentionally small but covers representative report features:

- three pages, including a hidden tooltip page;
- bookmark navigation, page navigation, a report-page tooltip link, and the derived incoming/outgoing page graph;
- standard data visuals, slicers, navigation, static text, and one custom visual;
- report, page, and visual filters;
- columns, measures, relationships, DAX, and Power Query summaries;
- categorical value examples with numeric and identifier values suppressed;
- dynamic RLS through a user-access mapping table and a separate OLS definition;
- one deliberately unresolved dynamic/custom binding;
- extraction diagnostics.

It is **not a PBIX file** and must not be used to claim PBIX compatibility. Power BI Desktop is not installed in the current environment, so a real generated PBIX cannot be created and verified here.

The RLS/OLS interpretation in this fixture is a formal placeholder. Its final schema will be adjusted only after the first production PBIX run.

`representative_report_documentation.txt` is generated from the fixture by:

```powershell
python examples/render_fixture.py
```

The fixture and its output serve as an early output-contract example. They will later be supplemented, not replaced, by real PBIX integration fixtures.

`synthetic_representative.pbix` is a deterministic ZIP/PBIR fixture for an end-to-end converter run. It contains three pages, eleven visuals, two grouped bookmarks, page/bookmark/tooltip navigation, fields, aggregations, and hidden filter literals. It intentionally has no binary `DataModel`, so the expected semantic-model status is `NO_MODEL` and the overall result is `PARTIAL`.

Rebuild and convert it with:

```powershell
python examples/build_synthetic_pbix.py
python main.py examples/synthetic_representative.pbix examples/synthetic_representative_documentation.txt
```

The `.pbix` extension makes it selectable by the application, but this fixture is not claimed to be openable by Power BI Desktop. A Desktop-compatible PBIX must be produced by Power BI Desktop from a valid report plus semantic model.

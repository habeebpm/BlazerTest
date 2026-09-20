# SmartDDR — DDR Developer page (modernized)

This folder contains a modernized rewrite of the `DDR_Developer.aspx` /
`DDR_Developer.aspx.vb` WebForms page (part of the larger "SmarTagsASP"
solution). It's unrelated to the trading EA in the rest of this repo — it
was added here at the user's request as a standalone module.

It is **not** a runnable project on its own: it assumes it's dropped into
the existing `SmarTagsASP` Web Forms application, which provides:

- The `ST_Common` helper class (`ST_Common.WorleyDataConnString`,
  `ST_Common.GetSingleDataRow`, `ST_Common.GetScalarValue`).
- A `Web.config` connection string named `ACAD_DATAConn1`.
- `Index.aspx` (target of the "Home" button).
- The `CTD_MASTER`, `CTD_DDR_DISC`, `Project_Info`, `SDDR_CTD_VIEW`,
  `SDDR_CTD_DDR_001`, `SMARTDDR01`, `[OPS-Z02_PLIP]` and `SPO_PLIP` tables
  referenced by the queries.
- .NET Framework 4.7+ (uses VB `?.` null-conditional operators and
  `ValueTuple` function return types — add the `System.ValueTuple` NuGet
  package if targeting an older Framework).

## What changed vs. the original

### Bugs fixed
- **Duplicate event wiring**: several handlers (`btnAddDDR_Click`,
  `btnSaveAll_Click`, `DDR1`'s click, `CSV_Upload_Click`, and three
  `RowDataBound` handlers) were wired both via a markup `OnClick`/
  `OnRowDataBound` attribute *and* a code-behind `Handles` clause, so each
  would have fired twice per postback (e.g. every "Add DDR Line" click
  would have added two rows). Each event now has exactly one wiring path.
- **Invalid markup**: the PLIP search `LinkButton` had `OnRowCommand`,
  an event `LinkButton` doesn't have (`RowCommand` belongs to `GridView`).
  It now just carries `CommandName`/`CommandArgument` and bubbles to
  `gvPLIPSearch`'s own `OnRowCommand`, which was missing.
- **No delete handler**: DDR row "Delete" buttons had a `CommandName` but
  `grdDDREntry` had no `OnRowCommand`, so deleting silently did nothing.
  Implemented `grdDDREntry_RowCommand` (deletes from the DB for saved
  rows, or just drops the in-memory row for unsaved ones).
- **No update path**: `btnSaveAll_Click` only ever inserted rows, so
  editing an existing DDR line and saving was a no-op. Existing rows are
  now updated.
- **Wrong session value**: `CTD_Grid_RowDataBound` stored `Del_Item_Ref`
  into `Session("Deliverable")` (used elsewhere to filter by deliverable
  title) — a copy/paste index mistake. That dead `RefSource` data source
  (never bound to any control) was removed along with it, and the
  correctly-used `Session("Del_Ref")` is now read from the `Del_Item_Ref`
  field by name instead of a magic cell index.
- **Fragile `Cells(n)` indexing**: several handlers read/wrote
  `GridViewRow.Cells(index)` by position, which silently breaks if a
  column is reordered (and disagreed with itself once a hidden
  `Visible="false"` template column is accounted for — that column does
  not get a cell at all, which had shifted every later index). Replaced
  with named-field access (`DataRowView("ColumnName")`) or dedicated
  `Label` controls (`lblCriticality`, `lblHoReq`) found via `FindControl`.
- **Duplicate CTD hyperlink**: `ctdGrid_RowDataBound` manually injected a
  second `HyperLink` into a cell that already had one declared in the
  `TemplateField`. Removed the redundant manual one.
- **Non-numeric length checks**: `txtArea.Text.Length = "6"` compared an
  `Integer` to the string `"6"`; changed to `.Trim().Length = 6`.
- **Parameterless event handlers**: `Home_Go`, `CTD_Prev`, `CTD_Next`, and
  `btnCTDSearch_Click` were declared with no parameters but referenced
  from `OnClick` attributes, which requires an `(Object, EventArgs)`
  signature. Fixed.
- **Missing `enctype`**: the CSV upload feature needs
  `enctype="multipart/form-data"` on the `<form>` tag; added.
- **SQL injection**: several lookups built SQL strings by concatenating
  textbox/session values directly (PLIP ID, area/document prefix, the
  PLIP search keyword assigned straight into `PLIPSource.SelectCommand`
  at runtime). All of these are now parameterized — either via
  `SqlParameter` in ADO.NET helper calls, or via a `ControlParameter` on
  `PLIPSource` instead of rebuilding its SQL text on every keystroke.
- **Unsafe error dialogs**: `ex.Message` was spliced into a
  `RegisterStartupScript` JS string with a naive `.Replace("'", "")`,
  which is not a safe way to escape into JavaScript. Replaced with a
  `ShowToast` helper that uses `HttpUtility.JavaScriptStringEncode`.

### Removed dead / non-functional code
- `PopulateAreaCombo`, `SetDDLValue`, `CTD_Grid_SelectedIndexChanged`,
  `ctd_ddr_match_SelectedIndexChanged`, `txtPLIPSearch_TextChanged`, and
  the unused `RefSource` / `ProjectInfoDS` / `PROJSOURCE` data sources —
  none were wired to anything.
- The redundant "Add_DDR" button, which duplicated "Add DDR Line" without
  its own behavior.
- Unused `Imports` (`System.Windows.Forms.MonthCalendar`, four
  `DocumentFormat.OpenXml.*` namespaces) that pulled in dependencies the
  page never used.

### Completed features that were previously stubs
- **PLIP search drawer** now actually writes the selected PLIP back into
  the originating DDR row (and refreshes its Critical/PLIP_HO/AFC-APP
  fields), instead of only logging the id.
- **CTD prev/next** navigation is wired to two buttons in a new context
  bar and shows a toast when there's nothing further in that direction.
- **"DDR + Activity" (`DDR1`)** button now adds one document row and one
  activity row in a single click, instead of only showing a loading
  overlay.
- **CSV Template / CSV Upload** are fully implemented: "Download CSV
  Template" streams a template with the expected columns
  (`Document_No, Document_Title, Man_Hours, RAMZ_ID, PLIP_ID, Software,
  Remarks, HO_STATUS`); "Upload CSV" parses an uploaded file (a small
  quoted-comma-aware parser), validates each row, and inserts them in a
  single transaction, reporting the imported count or a per-row error
  list.
- **Delete** removes the row from the database (or just from the pending
  in-memory table for a not-yet-saved row) instead of doing nothing.

### UI modernization
- CSS custom properties with a `prefers-color-scheme: dark` variant and
  a `data-theme` override hook.
- Responsive layout: the fixed sidebar becomes an off-canvas panel with a
  hamburger toggle under ~900px.
- A context bar above the grids replaces tiny always-on sidebar labels,
  and the previous/next CTD controls live there.
- A toast notification stack (`showToast(type, message)`) replaces
  `alert()` for save/delete/import feedback; message text always comes
  through the server via `HttpUtility.JavaScriptStringEncode`.
- An accessible `<dialog>`-based confirmation modal replaces
  `window.confirm()` for the delete action, driven by a small
  `confirmAction(el, message)` helper that captures the LinkButton's
  `__doPostBack` href, shows the dialog, and only replays it if the user
  confirms.
- A validation summary panel lists every problem across all DDR rows at
  once (client-side highlighting via `validateAllDdrRows()`, mirrored by
  server-side validation in `btnSaveAll_Click` so nothing relies on
  JavaScript alone).
- Empty-state messaging for grids with no rows yet.
- Sticky, scrollable grid headers; grid width no longer forces a fixed
  1700px—unused rules were consolidated.

## Known limitations / follow-ups worth doing next
- `NavigateToAdjacentCtd` still hardcodes `Discipline = '13. Process'`
  and `Project_No = '40087'`, exactly as the original did. That's almost
  certainly meant to be parameterized by the current project/discipline
  rather than pinned to one project — flagged here rather than guessed at,
  since the correct scoping wasn't specified.
- CSV upload assumes a fixed column order matching the template; a
  header-name-matched importer would be more forgiving of edited templates.
- No automated tests are included (WebForms + SQL Server makes that
  nontrivial without the real database); changes were reviewed by hand
  against the original control/event graph, not compiled or run.

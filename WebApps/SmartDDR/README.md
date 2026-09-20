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
- **"DDR + Activity" silently turned the activity row into a normal
  document row**: `grdDDREntry_RowDataBound`'s "smart defaults" for a new,
  unsaved row unconditionally generated and wrote a document number into
  `txtDocumentNo`, overwriting the `"ACTIVITY"` sentinel that
  `AddBlankDdrRow(dt, "ACTIVITY")` had just set — and the markup's
  `Enabled`/`Text` bindings for the PLIP and Area fields, and whether
  `Save All` would store `"ACTIVITY"` at all, all key off that exact
  value. `grdDDREntry_RowDataBound` now checks for it first and returns
  before running the PLIP/document defaults, leaving the row untouched.
- **"Prev" jumped to the very first CTD instead of the previous one**:
  `NavigateToAdjacentCtd`'s query always sorted `ORDER BY CTD_ID ASC`
  (inherited from the original page, which had the same bug in its own
  `CTD_Prev`), so `CTD_ID < @CurrentID ORDER BY CTD_ID ASC` returned the
  *smallest* ID below the current one rather than the *largest* — i.e.
  the first record in the list, not the adjacent one. "Prev" now sorts
  `DESC` while "Next" keeps `ASC`.
- **"Add DDR Line" then "Save All" duplicated the existing rows**:
  `grdDDREntry` was bound two competing ways — declaratively via
  `DataSourceID="DDR_Entry_Source"` in the markup, and manually via
  `grdDDREntry.DataSource = <in-memory DataTable>` in code whenever a row
  was added/removed before saving. Toggling between the two across
  postbacks let the framework's own auto-rebind-on-postback behavior for
  `DataSourceID`-bound controls win at some point between the "Add DDR
  Line" and "Save All" postbacks, resetting `DataKeys` back to the
  DB-bound state — which made `btnSaveAll_Click` see every row's `DDR_ID`
  as `0` and insert already-saved rows again instead of updating them.
  `grdDDREntry` is no longer bound via `DataSourceID` at all (the
  now-unused `DDR_Entry_Source` `SqlDataSource` was removed); a single
  `LoadDdrGrid()` helper is the only thing that ever (re)binds it, on
  initial load and after every save/delete/CSV-import, and adding or
  removing a row always work from that grid's own current in-memory
  state via `GetCurrentGridData()`.
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
- **Misleading CSV error line numbers**: blank lines were stripped
  (`StringSplitOptions.RemoveEmptyEntries`) before indexing into the
  file, so a `"Line N"` error could point at the wrong physical line
  once any blank line preceded it. Blank lines are now skipped
  individually inside the loop instead, keeping line numbers accurate;
  a CSV with only blank data rows now reports "no data rows" instead of
  silently reporting 0 imported with no explanation.

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
- **CSV Export / CSV Upload** are fully implemented, scoped to exactly
  seven fields on `CTD_DDR_DISC` — `CTD_ID, Ramz_ID, PLIP_ID, Document_No,
  Document_Title, Man_Hours, HO_Status` — by requirement; `Software`,
  `Disc_Remarks`, `Criticality` and `HO_REQ` are never read or written by
  either direction. "Export CSV" streams the current CTD's saved DDR
  lines in that column order (just the header row if none are saved yet,
  which doubles as an import template). "Upload CSV" parses an uploaded
  file (a small quoted-comma-aware parser), validates each row (CTD_ID —
  defaulting to the CTD currently open on the page when left blank —
  Document_No, Document_Title and Ramz_ID are required; Man_Hours must be
  numeric; HO_Status must be blank, AFC or APP), and inserts them in a
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
- **Sidebar redesign**: the general-purpose actions (PLIP Search, Export
  CSV, Upload CSV) used to live in a toolbar above the CTD grid in the
  main content, mixed in with page-specific controls. They now live in
  the sidebar as a proper navigation list — grouped into "Navigation",
  "CTD / DDR Hours", "Quick Actions" and "Import / Export" sections with
  small uppercase labels — alongside Back/Home/"DDR + Activity", which
  used to be a visually inconsistent mix of gradient pill buttons and ad
  hoc white "smart-card" boxes. All of these are now one consistent
  `.nav-item` row style (icon + label, flat, hover highlight) with
  lightweight inline SVG icons instead of emoji, which reads as a single
  coherent navigation rail rather than a handful of unrelated widgets.
  The CTD/DDR hours mini-table keeps its own white `.stat-card` since
  it's data, not an action. The old toolbar this replaced (and its
  now-dead `.header` CSS) was removed from the main content, along with
  the sidebar's leftover "Session context ... See context bar above the
  grids →" filler text — the same project/discipline/ref values are
  already shown properly in the main content's context bar; the sidebar
  now just holds the underlying state labels with no visible text.
  Because `Plip_Search`/`CSV_Template`/`CSV_Upload`/`DDR1`/`btnHome`
  needed to render an icon *and* a label inside one clickable element —
  something `asp:Button` can't do, since it renders as a childless
  `<input type="submit">` — they were changed from `asp:Button` to
  `asp:LinkButton` (which renders as an `<a>` and accepts child markup);
  this is a same-signature swap (`LinkButton.Click` uses the same
  `EventHandler` delegate as `Button.Click`), so no code-behind changes
  were needed beyond updating the designer file's field types to match.

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

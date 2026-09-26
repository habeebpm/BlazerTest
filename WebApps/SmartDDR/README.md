# SmartDDR — DDR Developer page (modernized)

This folder contains a modernized rewrite of the `DDR_DeveloperV3.aspx` /
`DDR_DeveloperV3.aspx.vb` WebForms page (part of the larger "SmarTagsASP"
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

## New features (beyond the original page)

- **Allocate Hours** (`btnAllocateHours` / `btnAllocateHours_Click`) — splits
  the CTD's total hours evenly across every current DDR row, rounded to 2
  decimal places, with the *first* row absorbing whatever rounding
  remainder is left over so the DDR total matches the CTD total exactly.
  Edits the rendered `txtHours` boxes in place; still requires Save All to
  persist. Lives in the sidebar's "Quick Actions" section.
- **Match flag on the CTD/DDR Hours grid** — the sidebar's `ctd_ddr_match`
  grid now has a flag column: 🚩 when a CTD's `CTD Hrs` and `DDR Hrs` don't
  match, ✅ when they do. (There's no widely-supported "green flag" glyph in
  Unicode, so a check mark stands in for "matching" — flagged here in case
  a specific icon/asset is preferred instead.)
- **DUM01–DUM99, then DU100+ instead of a flat "DUMMY"** — whenever the
  page guesses a new document number for an unsaved row, the 4th
  `-`-delimited segment used to always be the literal string `"DUMMY"`. It
  now increments: `GetCurrentMaxDummyNumber()` scans every Document_No
  currently in `grdDDREntry` (saved and unsaved rows alike) for that
  segment matching `DUM\d\d` or `DU\d\d\d`, and `GetNextDummySuffix()`
  hands out one past the highest number found — `DUM01`, `DUM02`, ...
  `DUM99`, `DU100`, `DU101`, ... This is self-correcting across postbacks
  and CTDs since it always re-scans the live grid rather than keeping
  separate counter state.
- **DDR Multiplier** — opens a drawer (`multiplierDrawer`) with a row
  picker (`ddlMultiplyTargetRow`) and two modes, driven by
  `rblMultiplyMode` and a copy count `txtMultiplyCount` (1–50). It can be
  opened two ways: the sidebar's "Quick Actions" shortcut
  (`btnOpenMultiplier`, opens with no row pre-selected — the picker
  defaults to the first row) or each row's own "×N" action next to its
  Delete button (`btnMultiplyRow`, pre-selects that row in the picker).
  `PopulateMultiplyRowPicker()` fills the dropdown from the grid's
  currently rendered rows either way, so both entry points share the same
  targeting UI:
  - **Mode A** (`btnApplyMultiplier_Click`, `useDumSequence = True`) —
    creates N copies of the row, each getting the next DUM/DU sequence
    number via the same numbering scheme above (computed once up front,
    then incremented per copy — `GetCurrentMaxDummyNumber()` can't be
    re-queried mid-loop since the new rows aren't bound to the grid yet).
  - **Mode B** — creates N copies with the trailing digit run of
    Document_No incremented once per copy (`IncrementTrailingDigits`,
    e.g. `...-0001` → `...-0002` → `...-0003`, preserving the original
    digit width).
  Both modes copy every other field (PLIP, RAMZ, Title, Hours, Software,
  Remarks, Criticality, PLIP_HO, AFC/APP) verbatim from the source row,
  which itself is left unchanged.
- **KPI scorecard** — a row of stat tiles above the CTD card (CTD Hours,
  DDR Hours, Variance, DDR Line Items, Match Status), refreshed by
  `UpdateKpiScorecard()` on every grid change (it's called from
  `RebindTempGrid`, the single choke point every add/delete/save/multiply
  path already goes through). Variance and Match Status are colored
  green/red via the page's existing `--success`/`--danger` tokens, kept
  consistent with the rest of the page's palette rather than introducing
  a second one.
- **PLIP detail footer drawer** — whenever a PLIP is picked for a DDR row,
  either by selecting a result in the PLIP search drawer
  (`gvPLIPSearch_RowCommand`) or by typing a PLIP ID directly into a row's
  PLIP box (`txtPLIP_TextChanged`), a bottom-anchored footer drawer
  (`plipFooterDrawer`) slides up showing that PLIP's Title, Doc Type,
  Critical, Required HO Status and DCAF — the same fields as the PLIP
  Drawer's own results columns, queried from `SPO_PLIP` by
  `ShowPlipDetailFooter()`. It closes itself if the typed/selected ID
  doesn't resolve to an active PLIP, and stays independent of the
  PLIP/CTD/Multiplier popovers (no shared overlay), so it can stay open
  alongside the search drawer while picking.
- **Denser layout** — trimmed padding/margins across the sidebar, context
  bar, cards and grid container so more content fits without scrolling.
- **Anchored PLIP/CTD/Multiplier drawers** — the search drawers no longer
  slide in from the right edge of the viewport covering its full height;
  they're now a small popover (`positionDrawerNear()` in the page script)
  that opens directly under whichever button triggered it — the sidebar's
  global PLIP search, or a specific row's search/multiply icon, via its
  captured `ClientID` passed into the registered startup script that
  reopens the drawer after each postback. The backdrop overlay is now
  transparent (click-outside-to-close only) instead of a dark blur, so the
  triggering row and surrounding page stay visible while the drawer is
  open.

- **User Guide drawer** — a **User Guide** button in a new sidebar "Help"
  section (plus a `?` button in the context bar and in the mobile top bar,
  and the **F1** key) opens `Help/DDR-Developer-Guide.html` in a drawer that
  slides over the left half of the screen (full width below ~900px). It has
  no overlay, so the grid on the right stays usable while following the
  steps, and it sits below the PLIP/CTD/Multiplier popovers so those still
  open on top. The guide is loaded into an `<iframe>` only on first open.
  Because every WebForms postback reloads the page, the open/closed state
  and the guide's scroll position are kept in `sessionStorage` (per browser
  tab) and restored after each postback. Close with ✖ or **Esc**; "New tab"
  opens the guide full size. Client-side only — no server controls or
  code-behind changes. **Deployment:** include `Help/DDR-Developer-Guide.html`
  and `Help/img/*.jpg` in the web project (Build Action = Content) so they
  are published with the page.

## What changed vs. the original

### Bugs fixed
- **Page crashed with "Databinding methods such as Eval(), XPath(), and
  Bind() can only be used in the context of a databound control."**:
  `ddlArea` (the Area dropdown in the DDR grid) had both its own
  `DataSourceID="AreaSource"` *and* an `Eval()`-based `Enabled` attribute
  declared directly on it. A control with its own `DataSourceID` defers
  its own `DataBind()` (and the evaluation of any `<%# %>` expression on
  it) to `PreRender`, by which point the GridView row's `DataItem`
  context that `Eval()` needs is gone — so it throws, and takes the whole
  page down with it. This existed in the originally uploaded file too;
  it just never got exercised until the page was actually run. Removed
  the `Enabled='<%# %>'` attribute from the markup and set
  `ddlArea.Enabled` in `grdDDREntry_RowDataBound` instead (for every row,
  not just unsaved ones — a saved `"ACTIVITY"` row needs it disabled
  too). A follow-up audit (after this crash was reported from an actual
  run) exhaustively scanned every `DataSourceID`-bound control in the
  markup for the same combination — `ddlArea` was the only one; the rest
  either have no `<%# %>` expression on themselves (`ddlRamz`) or are
  ordinary `GridView`s using `Eval()` inside their own row templates,
  which is the normal, safe pattern.
- **Manually switching a row's Type dropdown to ACTIVITY didn't actually
  mark it as one**: `ddlType_SelectedIndexChanged`'s `"ACTIVITY"` case
  disabled the PLIP/Area fields but never set `Document_No` to
  `"ACTIVITY"` — the literal value every `Enabled`/`Text` binding (and
  the save logic) actually keys off. Only rows added via the "DDR +
  Activity" button got it right. Fixed to set it there too, and to clear
  it back out if the user switches the type away from ACTIVITY again.
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
- **CSV Template / CSV Upload** are fully implemented, scoped to exactly
  seven fields on `CTD_DDR_DISC` — `CTD_ID, Ramz_ID, PLIP_ID, Document_No,
  Document_Title, Man_Hours, HO_Status` — by requirement; `Software`,
  `Disc_Remarks`, `Criticality` and `HO_REQ` are never read or written by
  either direction. "CSV Template" downloads just that header row (no
  DDR line data — an earlier version streamed the CTD's actual saved
  rows too, but that was more than wanted, so it's back to a blank
  template only). "Upload CSV" parses an uploaded file (a small
  quoted-comma-aware parser), validates each row (CTD_ID — defaulting to
  the CTD currently open on the page when left blank — Document_No,
  Document_Title and Ramz_ID are required; Man_Hours must be numeric;
  HO_Status must be blank, AFC or APP), and inserts them in a single
  transaction, reporting the imported count or a per-row error list.
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
  The sidebar's "Quick Actions" section now holds **Allocate Hours**,
  **PLIP Search** and **DDR Multiplier** as global shortcuts (each
  converted from `asp:Button` to `asp:LinkButton` for the same
  icon+label reason above); **"DDR + Activity" (`DDR1`)** moved back out
  to the main content's "DDR Line Items" toolbar next to Add DDR Line/
  Save All, since it acts on that grid specifically rather than being a
  page-global action.

## Business rules (added)
- **PLIP ID is mandatory for new documents.** Every unsaved, non-activity
  line must carry a PLIP that is active in `SPO_PLIP` (`Active_YN='YES'`).
  Enforced in `btnSaveAll_Click` (server, authoritative), in
  `validateAllDdrRows()` (client, rows marked `data-new="1"`) and in
  `CSV_Upload_Click` for every non-`ACTIVITY` row. Lines saved before the
  rule are not blocked. Activity lines never need a PLIP.
- **Dummy documents follow the `DU%` pattern.** A dummy serial is one
  `-`-separated segment `DUM01`..`DUM99`, then `DU100`+
  (`FormatDummySuffix`, `DummySerialRx`). New documents (Save All and CSV)
  are rejected if their number still uses the legacy `DUMMY` segment or
  `XXX` placeholders, or still starts with the `AAA-UU` area placeholder.
  SmartDDR v3's *Dummy Serials* check lists `DU` serials (plus legacy
  DUMMY/XXX) so they can be replaced later.
- Document numbers must be unique across the lines of a CTD (Save All) and
  within a CSV file.

## Deep-debug fixes (this revision)
- **Smart defaults no longer overwrite user input.** They used to run in
  `grdDDREntry_RowDataBound` for *every* unsaved row on *every* rebind, so
  clicking Add DDR Line reset the PLIP/title/document number already typed
  on earlier unsaved rows and wiped Multiplier copies. Defaults are now
  computed once, into the new row, in `AddBlankDdrRow`/`ApplyNewRowDefaults`;
  an unsaved row's Type is kept in a `DocType` column.
- **PLIP search picks now reach the grid.** `lnkPLIP` sat in an
  UpdatePanel while the grid is outside it, so the pick never rendered and
  was reverted on the next postback. `gvPLIPSearch_RowCreated` registers
  each link as a full postback control.
- **Dummy numbering found by segment, not position.** The default number is
  `AAA-UU-` + a history pattern, so the DUM segment was not the 4th segment
  of the full number; numbering restarted at DUM01 and Multiplier mode A
  overwrote the discipline segment. `MaxDummyNumber` scans every segment;
  `SetSerialSegment` writes into the existing DU/DUMMY/XXX/MASTR segment.
- **CTD context is loaded before the grid binds** (`LoadCtdContext`), so
  the RAMZ/Area lists and defaults use the right project; it is kept per
  page in hidden labels instead of Session (two tabs no longer mix).
- **RAMZ/Area lists** are loaded once per request and filled in code with a
  blank first item (a new row no longer silently takes the first RAMZ; a
  saved value outside the list no longer crashes). Picking an Area applies
  it to the document number (`ddlArea_SelectedIndexChanged`).
- **`ddlStatus`** no longer binds `SelectedValue` in markup (a value outside
  "", AFC, APP crashed the page); set in code, tolerant of padding/case.
- **Previous / Next CTD** now step through the open CTD's own project and
  discipline (was hard-coded to 40087 / 13. Process).
- **CSV Template** download no longer has the page HTML appended
  (`Response.SuppressContent`).
- **Save All** checks `Page.IsValid`, rejects negative hours and duplicate
  document numbers; row buttons no longer trigger validators
  (`CausesValidation="False"`), and the UPDATE now writes CRITICALITY/HO_REQ.
- **Deleting a saved line / importing a CSV keeps unsaved work**
  (`ReloadKeepingEdits`), and the sidebar CTD/DDR hours refresh after save,
  delete and import.
- **Sidebar PLIP Search** clears the target row first (browse only) and the
  toast says when nothing was applied.
- **CSV import**: header row optional; UTF-8 (with/without BOM) or
  Windows-1252; CTD_ID must belong to the open project; RAMZ must be one of
  the project's; PLIP and DU rules as above.
- **Encoding / XSS**: context labels are `Visible="false"` (state only);
  grid labels and search links use `<%#: %>`.
- **Compile error**: `For Each err In errors` collided with VB's `Err`
  object (BC30068); renamed.
- **Client validation** found nothing in ASP.NET 4's default ClientIDMode
  (`[id$='txtDocumentNo']` never matches `grdDDREntry_txtDocumentNo_0`);
  it now uses class hooks (`js-docno`, `js-title`, `js-ramz`, `js-plip`).
- `loaderOverlay` moved out of the sidebar (it was trapped off-screen by the
  sidebar's transform on phones). `GetPlipStatus` reads `SPO_PLIP` like the
  search, the footer and the active-PLIP rule.

## Known limitations / follow-ups worth doing next
- CSV upload assumes a fixed column order matching the template; a
  header-name-matched importer would be more forgiving of edited templates.
  Quoted CSV values can't contain line breaks.
- The default document number still comes from the most common pattern for
  the PLIP on projects starting with the same character, as in the original.
- Verified by compiling the code-behind against the .NET Framework 4.7.2
  reference assemblies (Option Strict Off and On) and by unit tests of the
  numbering / validation / CSV helpers; not run against the real database.

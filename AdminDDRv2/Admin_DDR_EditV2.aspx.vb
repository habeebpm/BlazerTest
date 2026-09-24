Imports System.Collections.Generic
Imports System.Data
Imports System.Data.SqlClient
Imports System.Globalization
Imports System.IO
Imports System.Linq
Imports System.Text
Imports System.Text.RegularExpressions
Imports System.Web
Imports System.Web.Services
Imports System.Web.UI
Imports System.Web.UI.WebControls
Imports OX = DocumentFormat.OpenXml
Imports OXP = DocumentFormat.OpenXml.Packaging
Imports S = DocumentFormat.OpenXml.Spreadsheet

' Admin DDR editor, v2.
' The project's rows are read once into a DataTable cached in Session; discipline switches,
' search, Excel-like column filters, sorting and exports all work on that cache. Edits stay
' pending in the page (like Excel) until Save all sends them to the SaveAll page method, which
' writes them in one transaction. A CSV / Excel file can be uploaded: PreviewUpload returns the
' cells that differ and the page stages them for review before Save all.
Public Class Admin_DDR_EditV2
    Inherits System.Web.UI.Page

#Region "Column catalogue"

    Private Enum ColKind
        Key         ' read-only identifier
        Text        ' editable text
        Number      ' editable decimal >= 0 (Man_Hours)
        Start       ' editable, 0 or 0.1 only (PLANNED)
        Percent     ' read-only fraction shown as a progress bar
        Status      ' read-only, shown as a percentage when numeric
    End Enum

    Private NotInheritable Class ColDef
        Public ReadOnly Field As String
        Public ReadOnly Header As String
        Public ReadOnly Kind As ColKind
        Public ReadOnly Width As Integer        ' column width in px (fixed table layout)
        Public ReadOnly Filter As Boolean
        Public ReadOnly Center As Boolean

        Public Sub New(field As String, header As String, kind As ColKind, width As Integer, filter As Boolean, center As Boolean)
            Me.Field = field
            Me.Header = header
            Me.Kind = kind
            Me.Width = width
            Me.Filter = filter
            Me.Center = center
        End Sub

        Public ReadOnly Property Editable As Boolean
            Get
                Return Kind = ColKind.Text OrElse Kind = ColKind.Number OrElse Kind = ColKind.Start
            End Get
        End Property
    End Class

    ' Same columns, order and editable cells as the original Admin_DDR_Edit markup.
    ' The "HO_REQ" column edits [HO_STATUS], exactly as before.
    Private Shared ReadOnly Cols As ColDef() = {
        New ColDef("DDR_ID", "ID", ColKind.Key, 66, False, True),
        New ColDef("CTD_ID", "CTD ID", ColKind.Key, 72, False, True),
        New ColDef("RAMZ_ID", "RAMZ ID", ColKind.Text, 124, True, False),
        New ColDef("Document_No", "Document No", ColKind.Text, 224, True, False),
        New ColDef("PLIP_ID", "PLIP ID", ColKind.Text, 112, True, False),
        New ColDef("SOFTWARE", "Software", ColKind.Text, 100, False, False),
        New ColDef("Document_Title", "Document Title", ColKind.Text, 480, True, False),
        New ColDef("Disc_Remarks", "Remarks", ColKind.Text, 170, False, False),
        New ColDef("Man_Hours", "Man Hours", ColKind.Number, 116, False, True),
        New ColDef("Prim_ID", "Prim ID", ColKind.Text, 92, False, True),
        New ColDef("HO_STATUS", "HO_REQ", ColKind.Text, 124, True, True),
        New ColDef("PLANNED", "START", ColKind.Start, 82, False, True),
        New ColDef("PERC", "Progress%", ColKind.Percent, 104, False, True),
        New ColDef("STATUS", "Status", ColKind.Status, 90, False, True)
    }

    Private Const RequireAdmin As Boolean = True
    Private Const SessionPrefix As String = "ADDR2_"
    Private Const MaxCachedPages As Integer = 4
    Private Const DeletedTag As String = " (DELETED)"

    ' "DELETED", "deleted", "DELETED - superseded by ..." all retire the document.
    Private Shared ReadOnly DeletedRemark As New Regex("^\s*DELETED\b", RegexOptions.IgnoreCase Or RegexOptions.Compiled)
    Private Shared ReadOnly KeyPattern As New Regex("^[0-9a-f]{32}$", RegexOptions.Compiled)
    Private Shared ReadOnly InvalidXmlChars As New Regex("[\x00-\x08\x0B\x0C\x0E-\x1F]", RegexOptions.Compiled)

    ' Activity rows are not documents; rows without a document number are still shown so they can be fixed.
    Private Shared ReadOnly LoadSql As String =
        "SELECT " & String.Join(", ", Cols.Select(Function(c) "D.[" & c.Field & "]")) & ",
                ISNULL(M.[Discipline], '') AS [Discipline]
         FROM [DDR_DISC_PERC] D
         OUTER APPLY (SELECT TOP 1 C.[Discipline] FROM [CTD_MASTER] C WHERE C.[CTD_ID] = D.[CTD_ID]) M
         WHERE D.[PROJECT_NO] = @P
           AND (D.[Document_No] IS NULL OR D.[Document_No] NOT LIKE '%CTIVIT%')
         ORDER BY D.[DDR_ID]"

    Private Shared ReadOnly RowsSql As String =
        "SELECT " & String.Join(", ", Cols.Select(Function(c) "[" & c.Field & "]")) & " FROM [DDR_DISC_PERC]"

#End Region

#Region "Per-request state"

    Private _userName As String = ""
    Private _denied As Boolean
    Private _suppressRender As Boolean
    Private _loadFailed As Boolean

    Private ReadOnly Property SelectedProject As String
        Get
            Return If(DDLPROJNO.SelectedValue, "").Trim()
        End Get
    End Property

    Private ReadOnly Property SelectedDiscipline As String
        Get
            Return If(DDLDISCIPLINE.SelectedValue, "").Trim()
        End Get
    End Property

    ' One cache entry per open page, so two browser tabs do not overwrite each other's data.
    Private ReadOnly Property GridKey As String
        Get
            Dim key As String = TryCast(ViewState("GridKey"), String)
            If String.IsNullOrEmpty(key) Then
                key = Guid.NewGuid().ToString("N")
                ViewState("GridKey") = key
                RegisterGridKey(Session, key)
            End If
            Return key
        End Get
    End Property

    Private Property GridData As DataTable
        Get
            Return CachedTable(Session, GridKey)
        End Get
        Set(value As DataTable)
            If value Is Nothing Then
                Session.Remove(SessionPrefix & "GRID_" & GridKey)
            Else
                Session(SessionPrefix & "GRID_" & GridKey) = value
            End If
        End Set
    End Property

    Private Shared Function CachedTable(session As System.Web.SessionState.HttpSessionState, key As String) As DataTable
        If session Is Nothing OrElse String.IsNullOrEmpty(key) Then Return Nothing
        Return TryCast(session(SessionPrefix & "GRID_" & key), DataTable)
    End Function

    Private Shared Sub RegisterGridKey(session As System.Web.SessionState.HttpSessionState, key As String)
        Dim keys As List(Of String) = TryCast(session(SessionPrefix & "KEYS"), List(Of String))
        If keys Is Nothing Then
            keys = New List(Of String)()
            session(SessionPrefix & "KEYS") = keys
        End If
        keys.Add(key)
        While keys.Count > MaxCachedPages
            session.Remove(SessionPrefix & "GRID_" & keys(0))
            keys.RemoveAt(0)
        End While
    End Sub

#End Region

#Region "Page lifecycle"

    Protected Sub Page_Load(sender As Object, e As EventArgs) Handles Me.Load
        _userName = CurrentUserName(Context)
        lblUser.Text = HttpUtility.HtmlEncode(If(_userName.Length > 0, _userName, "Guest"))

        If Not IsAdminUser(Context, _userName) Then
            _denied = True
            phApp.Visible = False
            pnlDenied.Visible = True
            Return
        End If

        If Not IsPostBack Then InitialLoad()
    End Sub

    Private Sub InitialLoad()
        Dim savedProject As String = ""
        Dim savedDisc As String = ""

        If _userName.Length > 0 Then
            Try
                Dim pref As String = CStr(ST_Common.GetUserProjectDisc(_userName))
                If Not String.IsNullOrEmpty(pref) Then
                    Dim parts() As String = pref.Split("~"c)
                    savedProject = parts(0).Trim()
                    If parts.Length > 1 Then savedDisc = parts(1).Trim()
                End If
            Catch ex As Exception
                Trace.Warn("AdminDDR", "Could not read the user preference", ex)
            End Try
        End If

        ' Deep links: Admin_DDR_EditV2.aspx?project=XXXX&disc=...
        Dim qsProject As String = Request.QueryString("project")
        If Not String.IsNullOrWhiteSpace(qsProject) Then savedProject = qsProject.Trim()
        Dim qsDisc As String = Request.QueryString("disc")
        If Not String.IsNullOrWhiteSpace(qsDisc) Then savedDisc = qsDisc.Trim()

        BindProjects(savedProject)
        LoadData()
        BindDisciplines(savedDisc)
        UpdateProjectInfo()
    End Sub

    Private Sub Page_PreRender(sender As Object, e As EventArgs) Handles Me.PreRender
        If _denied OrElse _suppressRender Then Return

        hfKey.Value = GridKey
        hfExportIds.Value = ""

        Dim dt As DataTable = EnsureData()
        If dt Is Nothing Then
            Dim msg As String = If(SelectedProject.Length = 0, "Select a project to start editing.",
                                   If(_loadFailed, "The DDR could not be loaded - see the message above.", "No DDR rows found."))
            litGrid.Text = "<div class=""empty-msg"" style=""padding:48px 16px;text-align:center;color:#64748B"">" &
                           HttpUtility.HtmlEncode(msg) & "</div>"
            Return
        End If
        litGrid.Text = BuildGridHtml(RowsForDiscipline(dt))
    End Sub

#End Region

#Region "Selectors"

    Private Sub BindProjects(Optional preferred As String = Nothing)
        Dim keep As String = If(preferred, DDLPROJNO.SelectedValue)
        DDLPROJNO.Items.Clear()
        Try
            Dim dt As DataTable = QueryTable(
                "SELECT M.[Project_No], T.[PROJECT_TITLE] AS [Title]
                 FROM (SELECT DISTINCT [Project_No] FROM [CTD_Master] WHERE [Project_No] IS NOT NULL) M
                 OUTER APPLY (SELECT TOP 1 I.[PROJECT_TITLE] FROM [PROJECT_INFO] I WHERE I.[PROJECT_NO] = M.[Project_No]) T
                 ORDER BY M.[Project_No]")
            For Each r As DataRow In dt.Rows
                Dim no As String = Convert.ToString(r("Project_No")).Trim()
                If no.Length = 0 OrElse DDLPROJNO.Items.FindByValue(no) IsNot Nothing Then Continue For
                Dim title As String = Convert.ToString(r("Title")).Trim()
                DDLPROJNO.Items.Add(New ListItem(If(title.Length > 0, no & " · " & Truncate(title, 40), no), no))
            Next
        Catch ex As Exception
            ShowMessage("Could not load the project list: " & ex.Message, "error")
        End Try
        SelectValue(DDLPROJNO, keep)
    End Sub

    ' Disciplines come from the cached rows: no extra query, and only disciplines that have DDR rows.
    Private Sub BindDisciplines(Optional preferred As String = Nothing)
        Dim keep As String = If(preferred, DDLDISCIPLINE.SelectedValue)
        DDLDISCIPLINE.Items.Clear()
        DDLDISCIPLINE.Items.Add(New ListItem("All", "All"))

        Dim dt As DataTable = GridData
        If dt IsNot Nothing Then
            Dim discs As IEnumerable(Of IGrouping(Of String, String)) = dt.Rows.Cast(Of DataRow)().
                Select(Function(r) Convert.ToString(r("Discipline")).Trim()).
                Where(Function(d) d.Length > 0).
                GroupBy(Function(d) d, StringComparer.OrdinalIgnoreCase).
                OrderBy(Function(g) DisciplineOrder(g.Key)).
                ThenBy(Function(g) g.Key, StringComparer.OrdinalIgnoreCase)
            For Each g As IGrouping(Of String, String) In discs
                DDLDISCIPLINE.Items.Add(New ListItem(g.Key & "  (" & g.Count().ToString("N0", CultureInfo.CurrentCulture) & ")", g.Key))
            Next
        End If
        SelectValue(DDLDISCIPLINE, keep, "All")
    End Sub

    ' "01.Civil", "2.Mech" ... sort by their numeric prefix, the rest last.
    Private Shared Function DisciplineOrder(d As String) As Integer
        Dim m As Match = Regex.Match(d, "^\d+")
        Dim n As Integer
        If m.Success AndAlso Integer.TryParse(m.Value, n) Then Return n
        Return Integer.MaxValue
    End Function

    Private Shared Sub SelectValue(ddl As DropDownList, value As String, Optional fallback As String = Nothing)
        Dim li As ListItem = If(String.IsNullOrEmpty(value), Nothing, ddl.Items.FindByValue(value))
        If li Is Nothing AndAlso Not String.IsNullOrEmpty(value) Then
            li = ddl.Items.Cast(Of ListItem)().FirstOrDefault(Function(x) x.Value.Equals(value, StringComparison.OrdinalIgnoreCase))
        End If
        If li Is Nothing AndAlso fallback IsNot Nothing Then li = ddl.Items.FindByValue(fallback)
        ddl.ClearSelection()
        If li IsNot Nothing Then
            li.Selected = True
        ElseIf ddl.Items.Count > 0 Then
            ddl.Items(0).Selected = True
        End If
    End Sub

    Private Sub UpdateProjectInfo()
        Dim proj As String = SelectedProject
        LblInfo.Text = ""
        LblInfo.ToolTip = ""
        If proj.Length = 0 Then Return
        Try
            Dim dt As DataTable = QueryTable(
                "SELECT TOP 1 [PROJECT_TITLE], [RAMZ_ID] FROM [PROJECT_INFO] WHERE [PROJECT_NO] = @P",
                P("@P", proj))
            Dim title As String = ""
            If dt.Rows.Count > 0 Then
                title = Convert.ToString(dt.Rows(0)(0)).Trim()
                Dim ramz As String = Convert.ToString(dt.Rows(0)(1)).Trim()
                If ramz.Length > 0 Then LblInfo.ToolTip = "RAMZ ID: " & ramz
            End If
            LblInfo.Text = HttpUtility.HtmlEncode(If(title.Length > 0, proj & " – " & title, proj))
        Catch ex As Exception
            LblInfo.Text = HttpUtility.HtmlEncode(proj)
            Trace.Warn("AdminDDR", "Project info lookup failed", ex)
        End Try
    End Sub

    Private Sub SaveUserPreference()
        If _userName.Length = 0 OrElse SelectedProject.Length = 0 Then Return
        Const sql As String =
            "IF EXISTS (SELECT 1 FROM [ST_USERS] WHERE [USER_NAME] = @U)
                 UPDATE [ST_USERS] SET [LATEST_PROJECT] = @P, [LATEST_DISC] = @D WHERE [USER_NAME] = @U
             ELSE
                 INSERT INTO [ST_USERS] ([USER_NAME],[P_GROUP],[PROJECT_NO],[ROLE_ASSIGNED],[LATEST_PROJECT],[LATEST_DISC])
                 VALUES (@U, 0, @P, @D, @P, @D)"
        Try
            ExecNonQuery(sql, P("@U", _userName), P("@P", SelectedProject), P("@D", SelectedDiscipline))
        Catch ex As Exception
            Trace.Warn("AdminDDR", "Saving the user preference failed", ex)
        End Try
    End Sub

    Private Sub DDLPROJNO_SelectedIndexChanged(sender As Object, e As EventArgs) Handles DDLPROJNO.SelectedIndexChanged
        LoadData()
        BindDisciplines()
        UpdateProjectInfo()
        SaveUserPreference()
    End Sub

    ' Served from the cache: no database round trip.
    Private Sub DDLDISCIPLINE_SelectedIndexChanged(sender As Object, e As EventArgs) Handles DDLDISCIPLINE.SelectedIndexChanged
        SaveUserPreference()
    End Sub

    Private Sub btnReload_Click(sender As Object, e As EventArgs) Handles btnReload.Click
        LoadData()
        BindDisciplines()
        If Not _loadFailed Then
            Dim dt As DataTable = GridData
            ShowMessage(String.Format(CultureInfo.CurrentCulture, "Reloaded {0:N0} rows from the database.",
                                      If(dt Is Nothing, 0, dt.Rows.Count)), "success")
        End If
    End Sub

#End Region

#Region "Data cache"

    Private Sub LoadData()
        _loadFailed = False
        GridData = Nothing
        If SelectedProject.Length = 0 Then Return
        Try
            Dim dt As DataTable = QueryTable(LoadSql, P("@P", SelectedProject))
            dt.ExtendedProperties("Project") = SelectedProject
            Try
                dt.PrimaryKey = {dt.Columns("DDR_ID")}   ' fast Rows.Find for edits
            Catch ex As Exception
                Trace.Warn("AdminDDR", "DDR_ID is not unique in DDR_DISC_PERC; using a linear lookup", ex)
            End Try
            GridData = dt
        Catch ex As Exception
            _loadFailed = True
            ShowMessage("Could not load the DDR for " & SelectedProject & ": " & ex.Message, "error")
        End Try
    End Sub

    ' Reloads silently when the session expired or the cache belongs to another project.
    Private Function EnsureData() As DataTable
        Dim dt As DataTable = GridData
        Dim cachedProject As String = If(dt Is Nothing, Nothing, TryCast(dt.ExtendedProperties("Project"), String))
        If (dt Is Nothing OrElse Not String.Equals(cachedProject, SelectedProject, StringComparison.OrdinalIgnoreCase)) AndAlso
           Not _loadFailed AndAlso SelectedProject.Length > 0 Then
            LoadData()
            dt = GridData
        End If
        Return dt
    End Function

    Private Function RowsForDiscipline(dt As DataTable) As List(Of DataRow)
        Dim disc As String = SelectedDiscipline
        If disc.Length = 0 OrElse disc.Equals("All", StringComparison.OrdinalIgnoreCase) Then
            Return dt.Rows.Cast(Of DataRow)().ToList()
        End If
        Return dt.Rows.Cast(Of DataRow)().
            Where(Function(r) String.Equals(Convert.ToString(r("Discipline")).Trim(), disc, StringComparison.OrdinalIgnoreCase)).
            ToList()
    End Function

    Private Shared Function FindRow(dt As DataTable, ddrId As Integer) As DataRow
        If dt Is Nothing Then Return Nothing
        If dt.PrimaryKey.Length = 1 Then
            Try
                Return dt.Rows.Find(Convert.ChangeType(ddrId, dt.PrimaryKey(0).DataType, CultureInfo.InvariantCulture))
            Catch ex As Exception
                ' Unusual key type: fall through to the linear search.
            End Try
        End If
        For Each r As DataRow In dt.Rows
            Dim n As Double
            If TryGetNumber(r("DDR_ID"), n) AndAlso n = ddrId Then Return r
        Next
        Return Nothing
    End Function

#End Region

#Region "Grid rendering"

    ' The grid is written straight to HTML: no server controls per cell and no ViewState,
    ' which keeps large projects fast to render and to post back. Per-column settings
    ' (width, alignment, kind, max length) live on the header, the <colgroup> and one
    ' style block, so each row carries little more than its values. The fixed table
    ' layout means the browser never measures the cells, which keeps filtering fast.
    Private Function BuildGridHtml(rows As List(Of DataRow)) As String
        Dim sb As New StringBuilder(8192 + rows.Count * 700)
        sb.Append("<style>")
        For i As Integer = 0 To Cols.Length - 1
            Dim c As ColDef = Cols(i)
            If Not c.Editable Then Continue For
            Dim nth As String = (i + 1).ToString(CultureInfo.InvariantCulture)
            If c.Center Then sb.Append("#ddrGrid td:nth-child(").Append(nth).Append("){text-align:center}")
            If c.Field = "Disc_Remarks" Then
                sb.Append("#ddrGrid tr.row-deleted td:nth-child(").Append(nth).Append("){text-decoration:none;font-weight:700}")
            End If
        Next
        sb.Append("</style>")

        Dim tableWidth As Integer = Cols.Sum(Function(c) c.Width)
        sb.Append("<table id=""ddrGrid"" class=""grid grid-edit"" spellcheck=""false"" style=""width:").
           Append(tableWidth.ToString(CultureInfo.InvariantCulture)).Append("px""><colgroup>")
        For Each c As ColDef In Cols
            sb.Append("<col style=""width:").Append(c.Width.ToString(CultureInfo.InvariantCulture)).Append("px"" />")
        Next
        sb.Append("</colgroup><thead><tr>")
        For Each c As ColDef In Cols
            Dim cls As New List(Of String)()
            If c.Center Then cls.Add("c")
            If c.Editable Then cls.Add("ed")
            sb.Append("<th scope=""col"" data-f=""").Append(c.Field).Append("""")
            If cls.Count > 0 Then sb.Append(" class=""").Append(String.Join(" ", cls)).Append("""")
            If c.Editable Then
                sb.Append(" data-k=""").Append(If(c.Kind = ColKind.Number, "n", If(c.Kind = ColKind.Start, "s", "t"))).Append("""")
                Dim maxLen As Integer = If(c.Kind = ColKind.Text, ColumnMaxLength(c.Field), 0)
                If maxLen > 0 Then sb.Append(" data-max=""").Append(maxLen.ToString(CultureInfo.InvariantCulture)).Append("""")
            End If
            sb.Append("><span class=""th-in""><span class=""th-label"" title=""Click to sort"">").Append(HttpUtility.HtmlEncode(c.Header))
            If c.Editable Then sb.Append("<span class=""ed-mark"" title=""Editable"">✎</span>")
            sb.Append("</span>")
            If c.Filter Then
                sb.Append("<button type=""button"" class=""fbtn"" data-f=""").Append(c.Field).
                   Append(""" title=""Filter ").Append(HttpUtility.HtmlAttributeEncode(c.Header)).
                   Append(""" aria-label=""Filter ").Append(HttpUtility.HtmlAttributeEncode(c.Header)).Append(""">▼</button>")
            End If
            sb.Append("</span></th>")
        Next
        sb.Append("</tr></thead><tbody>")

        If rows.Count = 0 Then
            sb.Append("<tr class=""empty""><td colspan=""").Append(Cols.Length).Append(""">No DDR rows for the current selection.</td></tr>")
        End If

        For Each r As DataRow In rows
            sb.Append("<tr data-id=""").Append(HttpUtility.HtmlAttributeEncode(InputText(r("DDR_ID"), ColKind.Key))).Append("""")
            If IsDeletedRow(r) Then sb.Append(" class=""row-deleted""")
            sb.Append(">")
            For Each c As ColDef In Cols
                AppendCell(sb, c, r(c.Field))
            Next
            sb.Append("</tr>")
        Next
        sb.Append("</tbody></table>")
        Return sb.ToString()
    End Function

    Private Shared Sub AppendCell(sb As StringBuilder, c As ColDef, value As Object)
        Select Case c.Kind
            Case ColKind.Text, ColKind.Number, ColKind.Start
                ' Plain text: the page drops a single shared editor into the cell when it is clicked.
                ' The column (field, kind, max length) comes from the header cell.
                sb.Append("<td class=""e"">").Append(HttpUtility.HtmlEncode(InputText(value, c.Kind))).Append("</td>")
            Case ColKind.Percent
                sb.Append("<td class=""ro c"" data-n=""").Append(HttpUtility.HtmlAttributeEncode(InputText(value, c.Kind))).Append(""">")
                sb.Append(ReadOnlyHtml(value, c.Kind)).Append("</td>")
            Case Else
                sb.Append("<td class=""ro").Append(If(c.Center, " c", "")).Append(""">")
                sb.Append(ReadOnlyHtml(value, c.Kind)).Append("</td>")
        End Select
    End Sub

    ' The text an input holds (or a read-only cell's raw value).
    Private Shared Function InputText(value As Object, kind As ColKind) As String
        If value Is Nothing OrElse Convert.IsDBNull(value) Then Return ""
        Dim n As Double
        Select Case kind
            Case ColKind.Number, ColKind.Start, ColKind.Percent
                If TryGetNumber(value, n) Then Return n.ToString("0.####", CultureInfo.InvariantCulture)
            Case ColKind.Key
                If IsNumericValue(value) AndAlso TryGetNumber(value, n) Then Return n.ToString("0", CultureInfo.InvariantCulture)
        End Select
        Return Convert.ToString(value, CultureInfo.InvariantCulture).Trim()
    End Function

    Private Shared Function ReadOnlyHtml(value As Object, kind As ColKind) As String
        If value Is Nothing OrElse Convert.IsDBNull(value) Then Return ""
        Dim n As Double
        Select Case kind
            Case ColKind.Percent
                If TryGetNumber(value, n) Then Return ProgressBarHtml(n * 100)
        End Select
        Return HttpUtility.HtmlEncode(ReadOnlyText(value, kind))
    End Function

    ' Read-only value as plain text (STATUS: a percentage when numeric).
    Private Shared Function ReadOnlyText(value As Object, kind As ColKind) As String
        Dim n As Double
        If kind = ColKind.Status AndAlso IsNumericValue(value) AndAlso TryGetNumber(value, n) Then
            Return (n * 100).ToString("0.00", CultureInfo.InvariantCulture) & "%"
        End If
        Return InputText(value, kind)
    End Function

    Private Shared Function ProgressBarHtml(pct As Double) As String
        Dim width As Double = Math.Max(0, Math.Min(100, pct))
        Dim cls As String = If(pct >= 100, "pb-good", If(pct >= 50, "pb-ok", If(pct >= 20, "pb-warn", "pb-bad")))
        Return "<span class=""pbar""><span class=""pbar-val"">" & pct.ToString("0.00", CultureInfo.InvariantCulture) &
               "%</span><span class=""pbar-track""><span class=""pbar-fill " & cls & """ style=""width:" &
               width.ToString("0.#", CultureInfo.InvariantCulture) & "%""></span></span></span>"
    End Function

    Private Shared Function IsDeletedRow(r As DataRow) As Boolean
        Dim remark As String = Convert.ToString(r("Disc_Remarks"))
        Dim title As String = Convert.ToString(r("Document_Title"))
        Return DeletedRemark.IsMatch(remark) OrElse title.IndexOf(DeletedTag.Trim(), StringComparison.OrdinalIgnoreCase) >= 0
    End Function

#End Region

#Region "Save all (page method)"

    ' One edited cell, as sent by the page (and as returned by the upload preview).
    Public NotInheritable Class CellChange
        Public Property Id As Integer
        Public Property Field As String
        Public Property Value As String
    End Class

    Public NotInheritable Class CellError
        Public Property Id As Integer
        Public Property Field As String
        Public Property Message As String
    End Class

    ' Fresh values of one saved row: editable cells as edit text, PERC as a fraction, STATUS as shown.
    Public NotInheritable Class RowResult
        Public Property Id As Integer
        Public Property Deleted As Boolean
        Public Property Values As New Dictionary(Of String, String)()
    End Class

    Public NotInheritable Class SaveAllResult
        Public Property Ok As Boolean
        Public Property Message As String = ""
        Public Property Level As String = "success"
        Public Property Errors As New List(Of CellError)()
        Public Property Rows As New List(Of RowResult)()
    End Class

    Private Const MaxChangesPerSave As Integer = 20000

    Private Shared Function SaveFailed(message As String) As SaveAllResult
        Return New SaveAllResult() With {.Ok = False, .Message = message, .Level = "error"}
    End Function

    ' Saves every pending cell in ONE transaction: either all changes are written or none.
    ' Called from the page with PageMethods.SaveAll(key, [{Id, Field, Value}, ...]).
    <WebMethod(EnableSession:=True)>
    Public Shared Function SaveAll(key As String, changes As List(Of CellChange)) As SaveAllResult
        Dim ctx As HttpContext = HttpContext.Current
        Dim currentId As Integer = 0
        Try
            If Not IsAdminUser(ctx, CurrentUserName(ctx)) Then Return SaveFailed("You do not have permission to edit the DDR.")
            If changes Is Nothing OrElse changes.Count = 0 Then
                Return New SaveAllResult() With {.Ok = True, .Message = "There are no changes to save.", .Level = "info"}
            End If
            If changes.Count > MaxChangesPerSave Then
                Return SaveFailed(String.Format(CultureInfo.InvariantCulture, "Too many changes in one save (max {0:N0}).", MaxChangesPerSave))
            End If

            ' 1. Validate everything first; nothing is written when any cell is invalid.
            Dim result As New SaveAllResult()
            Dim perRow As New Dictionary(Of Integer, Dictionary(Of String, SqlParameter))()
            Dim order As New List(Of Integer)()
            For Each ch As CellChange In changes
                If ch Is Nothing Then Continue For
                Dim col As ColDef = Cols.FirstOrDefault(Function(c) c.Editable AndAlso c.Field.Equals(If(ch.Field, ""), StringComparison.OrdinalIgnoreCase))
                Dim problem As String = Nothing
                Dim prm As SqlParameter = Nothing
                If col Is Nothing Then
                    problem = "This column cannot be edited."
                ElseIf ch.Id <= 0 Then
                    problem = "Unknown DDR row."
                Else
                    prm = ToParameter(col, If(ch.Value, "").Trim(), problem)
                End If
                If problem IsNot Nothing Then
                    result.Errors.Add(New CellError() With {.Id = ch.Id, .Field = If(col Is Nothing, ch.Field, col.Field), .Message = problem})
                    Continue For
                End If
                Dim fields As Dictionary(Of String, SqlParameter) = Nothing
                If Not perRow.TryGetValue(ch.Id, fields) Then
                    fields = New Dictionary(Of String, SqlParameter)(StringComparer.OrdinalIgnoreCase)
                    perRow(ch.Id) = fields
                    order.Add(ch.Id)
                End If
                fields(col.Field) = prm   ' the last edit of a cell wins
            Next
            If result.Errors.Count > 0 Then
                result.Ok = False
                result.Level = "error"
                result.Message = String.Format(CultureInfo.InvariantCulture,
                    "Nothing was saved: {0} cell(s) have invalid values (marked in red). {1}",
                    result.Errors.Count, result.Errors(0).Message)
                Return result
            End If

            ' 2. Write all rows in one transaction.
            Dim titleMax As Integer = ColumnMaxLength("Document_Title")
            Dim retired As New HashSet(Of Integer)()
            Dim fresh As New DataTable()
            Using con As New SqlConnection(ConnString())
                con.Open()
                Using tx As SqlTransaction = con.BeginTransaction()
                    Try
                        For Each id As Integer In order
                            currentId = id
                            Dim fields As Dictionary(Of String, SqlParameter) = perRow(id)
                            Using cmd As SqlCommand = BuildRowUpdate(con, tx, id, fields, titleMax, retired)
                                If cmd.ExecuteNonQuery() = 0 Then
                                    tx.Rollback()
                                    Dim r As SaveAllResult = SaveFailed("Nothing was saved: DDR row " & id.ToString(CultureInfo.InvariantCulture) & " no longer exists. Reload the page.")
                                    r.Errors.Add(New CellError() With {.Id = id, .Field = fields.Keys.First(), .Message = "This row no longer exists."})
                                    Return r
                                End If
                            End Using
                        Next
                        currentId = 0
                        tx.Commit()
                    Catch
                        Try
                            tx.Rollback()
                        Catch
                            ' The connection may already be broken; the transaction is gone either way.
                        End Try
                        Throw
                    End Try
                End Using

                ' 3. Read the saved rows back (PERC / STATUS are recalculated by the view).
                For start As Integer = 0 To order.Count - 1 Step 1000
                    Dim chunk As List(Of Integer) = order.Skip(start).Take(1000).ToList()
                    Using cmd As New SqlCommand()
                        cmd.Connection = con
                        cmd.CommandTimeout = 120
                        Dim names As New List(Of String)()
                        For i As Integer = 0 To chunk.Count - 1
                            Dim n As String = "@I" & i.ToString(CultureInfo.InvariantCulture)
                            names.Add(n)
                            cmd.Parameters.Add(New SqlParameter(n, SqlDbType.Int) With {.Value = chunk(i)})
                        Next
                        cmd.CommandText = RowsSql & " WHERE [DDR_ID] IN (" & String.Join(",", names) & ")"
                        Using da As New SqlDataAdapter(cmd)
                            da.Fill(fresh)
                        End Using
                    End Using
                Next
            End Using

            ' 4. Keep the session cache in step, and send the fresh values back.
            Dim dt As DataTable = If(KeyPattern.IsMatch(If(key, "")), CachedTable(ctx.Session, key), Nothing)
            Dim freshById As New Dictionary(Of Integer, DataRow)()
            For Each fr As DataRow In fresh.Rows
                Dim n As Double
                If TryGetNumber(fr("DDR_ID"), n) Then freshById(CInt(n)) = fr
            Next
            For Each id As Integer In order
                Dim cached As DataRow = FindRow(dt, id)
                Dim source As DataRow = Nothing
                freshById.TryGetValue(id, source)
                If cached IsNot Nothing AndAlso source IsNot Nothing Then
                    For Each c As ColDef In Cols
                        If c.Kind = ColKind.Key Then Continue For
                        Try
                            cached(c.Field) = source(c.Field)
                        Catch ex As Exception
                            ' Type mismatch between the view and the cached column: keep the cached value.
                        End Try
                    Next
                End If
                Dim shown As DataRow = If(source, cached)
                If shown Is Nothing Then Continue For   ' e.g. the row no longer matches the view
                Dim rr As New RowResult() With {.Id = id, .Deleted = IsDeletedRow(shown)}
                For Each c As ColDef In Cols
                    If c.Kind = ColKind.Key Then Continue For
                    rr.Values(c.Field) = If(c.Kind = ColKind.Status, ReadOnlyText(shown(c.Field), c.Kind), InputText(shown(c.Field), c.Kind))
                Next
                result.Rows.Add(rr)
            Next

            Dim cellCount As Integer = perRow.Values.Sum(Function(f) f.Count)
            result.Ok = True
            result.Message = String.Format(CultureInfo.CurrentCulture, "Saved {0:N0} change(s) in {1:N0} row(s).", cellCount, order.Count)
            If retired.Count > 0 Then
                result.Message &= String.Format(CultureInfo.CurrentCulture, " {0:N0} document(s) marked DELETED (title + Man Hours = 0).", retired.Count)
            End If
            Dim dups As List(Of String) = DuplicateDocumentNos(dt, perRow)
            If dups.Count > 0 Then
                result.Level = "warn"
                result.Message &= " Note: document number(s) used by more than one row: " & String.Join(", ", dups) & "."
            End If
            Return result

        Catch ex As SqlException
            Dim r As SaveAllResult = SaveFailed("Nothing was saved - database error" &
                If(currentId > 0, " on DDR row " & currentId.ToString(CultureInfo.InvariantCulture), "") & ": " & ex.Message)
            If currentId > 0 Then r.Errors.Add(New CellError() With {.Id = currentId, .Field = "", .Message = ex.Message})
            Return r
        Catch ex As Exception
            Return SaveFailed("Nothing was saved: " & ex.Message)
        End Try
    End Function

    ' Validates / converts one value. Returns Nothing and sets problem when it is invalid.
    Private Shared Function ToParameter(col As ColDef, text As String, ByRef problem As String) As SqlParameter
        Select Case col.Kind
            Case ColKind.Number
                Dim d As Decimal
                If text.Length = 0 Then
                    d = 0D
                ElseIf Not TryParseDecimal(text, d) OrElse d < 0D OrElse d >= 1000000D Then
                    problem = "Man Hours must be a number between 0 and 999,999."
                    Return Nothing
                End If
                Return New SqlParameter() With {.SqlDbType = SqlDbType.Decimal, .Precision = 18, .Scale = 4, .Value = d}
            Case ColKind.Start
                Dim d As Decimal
                If text.Length = 0 Then
                    d = 0D
                ElseIf Not TryParseDecimal(text, d) OrElse (d <> 0D AndAlso d <> 0.1D) Then
                    problem = "START accepts only 0 or 0.1."
                    Return Nothing
                End If
                Return New SqlParameter() With {.SqlDbType = SqlDbType.Decimal, .Precision = 18, .Scale = 4, .Value = d}
            Case Else
                Dim maxLen As Integer = ColumnMaxLength(col.Field)
                If maxLen > 0 AndAlso text.Length > maxLen Then
                    problem = String.Format(CultureInfo.InvariantCulture, "{0} can hold at most {1} characters.", col.Header, maxLen)
                    Return Nothing
                End If
                Return New SqlParameter() With {.SqlDbType = SqlDbType.NVarChar, .Size = If(maxLen > 0, maxLen, -1), .Value = text}
        End Select
    End Function

    ' One UPDATE per row with every changed column. Typing DELETED in Remarks retires the
    ' document: " (DELETED)" is appended to the (new or current) title and Man_Hours = 0.
    Private Shared Function BuildRowUpdate(con As SqlConnection, tx As SqlTransaction, id As Integer,
                                           fields As Dictionary(Of String, SqlParameter), titleMax As Integer,
                                           retired As HashSet(Of Integer)) As SqlCommand
        Dim cmd As New SqlCommand() With {.Connection = con, .Transaction = tx, .CommandTimeout = 60}
        Dim sets As New List(Of String)()
        Dim i As Integer = 0
        Dim titleExpr As String = "[Document_Title]"
        Dim remark As SqlParameter = Nothing
        fields.TryGetValue("Disc_Remarks", remark)
        Dim retire As Boolean = remark IsNot Nothing AndAlso DeletedRemark.IsMatch(Convert.ToString(remark.Value))

        For Each kv As KeyValuePair(Of String, SqlParameter) In fields
            Dim name As String = "@V" & i.ToString(CultureInfo.InvariantCulture)
            i += 1
            kv.Value.ParameterName = name
            cmd.Parameters.Add(kv.Value)
            If retire AndAlso kv.Key = "Document_Title" Then
                titleExpr = name                          ' title typed in the same save
            ElseIf retire AndAlso kv.Key = "Man_Hours" Then
                ' The DELETED rule wins over a typed Man Hours value.
            Else
                sets.Add("[" & kv.Key & "] = " & name)
            End If
        Next
        If retire Then
            sets.Add("[Document_Title] = CASE WHEN ISNULL(" & titleExpr & ", '') LIKE '%(DELETED)%' THEN " & titleExpr &
                     " ELSE LTRIM(LEFT(RTRIM(ISNULL(" & titleExpr & ", '')), @ROOM) + '" & DeletedTag & "') END")
            sets.Add("[Man_Hours] = 0")
            cmd.Parameters.Add(New SqlParameter("@ROOM", SqlDbType.Int) With {
                .Value = If(titleMax > DeletedTag.Length, titleMax - DeletedTag.Length, 100000)})
            retired.Add(id)
        End If
        cmd.Parameters.Add(New SqlParameter("@ID", SqlDbType.Int) With {.Value = id})
        cmd.CommandText = "SET NOCOUNT OFF; UPDATE [CTD_DDR_DISC] SET " & String.Join(", ", sets) & " WHERE [DDR_ID] = @ID"
        Return cmd
    End Function

    Private Shared Function DuplicateDocumentNos(dt As DataTable, perRow As Dictionary(Of Integer, Dictionary(Of String, SqlParameter))) As List(Of String)
        Dim result As New List(Of String)()
        If dt Is Nothing Then Return result
        Dim changed As New HashSet(Of String)(StringComparer.OrdinalIgnoreCase)
        For Each fields As Dictionary(Of String, SqlParameter) In perRow.Values
            Dim p As SqlParameter = Nothing
            If fields.TryGetValue("Document_No", p) Then
                Dim v As String = Convert.ToString(p.Value).Trim()
                If v.Length > 0 Then changed.Add(v)
            End If
        Next
        If changed.Count = 0 Then Return result
        Dim counts As New Dictionary(Of String, Integer)(StringComparer.OrdinalIgnoreCase)
        For Each r As DataRow In dt.Rows
            Dim v As String = Convert.ToString(r("Document_No")).Trim()
            If changed.Contains(v) Then
                Dim n As Integer
                counts.TryGetValue(v, n)
                counts(v) = n + 1
            End If
        Next
        Return counts.Where(Function(kv) kv.Value > 1).Select(Function(kv) kv.Key).Take(5).ToList()
    End Function

#End Region

#Region "Upload (CSV / Excel) - preview only"

    Public NotInheritable Class UploadResult
        Public Property Ok As Boolean
        Public Property Message As String = ""
        Public Property Changes As New List(Of CellChange)()
        Public Property FileRows As Integer
        Public Property ChangedRows As Integer
        Public Property NotFound As New List(Of String)()
        Public Property NotFoundCount As Integer
        Public Property NoIdCount As Integer
        Public Property DuplicateIds As Integer
        Public Property Columns As New List(Of String)()
        Public Property Ignored As New List(Of String)()
    End Class

    Private Const MaxUploadBytes As Integer = 8 * 1024 * 1024

    Private Shared Function UploadFailed(message As String) As UploadResult
        Return New UploadResult() With {.Ok = False, .Message = message}
    End Function

    ' Reads an uploaded CSV / XLSX (base64) and returns the cells that differ from the database.
    ' Nothing is written here: the page stages the changes and the user reviews them and
    ' clicks Save all.
    <WebMethod(EnableSession:=True)>
    Public Shared Function PreviewUpload(key As String, fileName As String, base64 As String) As UploadResult
        Dim ctx As HttpContext = HttpContext.Current
        Try
            If Not IsAdminUser(ctx, CurrentUserName(ctx)) Then Return UploadFailed("You do not have permission to edit the DDR.")
            Dim dt As DataTable = If(KeyPattern.IsMatch(If(key, "")), CachedTable(ctx.Session, key), Nothing)
            If dt Is Nothing Then Return UploadFailed("Your session has expired. Reload the page, then upload the file again.")

            Dim bytes As Byte()
            Try
                bytes = Convert.FromBase64String(If(base64, ""))
            Catch ex As FormatException
                Return UploadFailed("The file could not be read.")
            End Try
            If bytes.Length = 0 Then Return UploadFailed("The file is empty.")
            If bytes.Length > MaxUploadBytes Then Return UploadFailed("The file is too large (max 8 MB).")

            Dim ext As String = Path.GetExtension(If(fileName, "")).ToLowerInvariant()
            Dim table As List(Of String())
            If ext = ".xlsx" OrElse ext = ".xlsm" Then
                table = ReadXlsx(bytes)
            ElseIf ext = ".csv" OrElse ext = ".txt" Then
                table = ReadCsv(bytes)
            Else
                Return UploadFailed("Upload a .csv or .xlsx file (for example one downloaded from this page).")
            End If
            Return BuildPreview(dt, table)

        Catch ex As Exception
            Return UploadFailed("The file could not be read: " & ex.Message)
        End Try
    End Function

    Private Shared Function BuildPreview(dt As DataTable, table As List(Of String())) As UploadResult
        Dim result As New UploadResult() With {.Ok = True}

        ' Header: the first row with any text. Columns match by header or field name,
        ' ignoring case, spaces and underscores ("Man Hours" = "MAN_HOURS").
        Dim headerIndex As Integer = table.FindIndex(Function(r) r.Any(Function(c) Not String.IsNullOrWhiteSpace(c)))
        If headerIndex < 0 Then Return UploadFailed("The file has no rows.")
        Dim header As String() = table(headerIndex)
        Dim idCol As Integer = -1
        Dim map As New List(Of KeyValuePair(Of Integer, ColDef))()
        Dim seen As New HashSet(Of String)(StringComparer.OrdinalIgnoreCase)
        For i As Integer = 0 To header.Length - 1
            Dim h As String = NormalizeHeader(header(i))
            If h.Length = 0 Then Continue For
            Dim col As ColDef = Cols.FirstOrDefault(Function(c) NormalizeHeader(c.Header) = h OrElse NormalizeHeader(c.Field) = h)
            If col Is Nothing AndAlso (h = "DDRID") Then col = Cols(0)
            If col Is Nothing Then
                result.Ignored.Add(header(i).Trim() & If(h = "DISCIPLINE", " (read-only)", " (unknown column)"))
            ElseIf col.Field = "DDR_ID" Then
                If idCol < 0 Then idCol = i
            ElseIf Not col.Editable Then
                result.Ignored.Add(header(i).Trim() & " (read-only)")
            ElseIf seen.Add(col.Field) Then
                map.Add(New KeyValuePair(Of Integer, ColDef)(i, col))
                result.Columns.Add(col.Header)
            Else
                result.Ignored.Add(header(i).Trim() & " (duplicate column)")
            End If
        Next
        If idCol < 0 Then Return UploadFailed("The file needs an ""ID"" (DDR_ID) column - use the CSV / Excel downloaded from this page.")
        If map.Count = 0 Then Return UploadFailed("The file has no editable columns (RAMZ ID, Document No, Title, Remarks ...).")

        Dim idsSeen As New HashSet(Of Integer)()
        Dim changedRows As New HashSet(Of Integer)()
        Dim lastChange As New Dictionary(Of String, CellChange)()   ' id|field -> change (a later duplicate row wins)
        For r As Integer = headerIndex + 1 To table.Count - 1
            Dim cells As String() = table(r)
            If Not cells.Any(Function(c) Not String.IsNullOrWhiteSpace(c)) Then Continue For
            result.FileRows += 1
            Dim idText As String = CleanUploadValue(If(idCol < cells.Length, cells(idCol), ""))
            Dim idNum As Double
            If Not Double.TryParse(idText, NumberStyles.Float, CultureInfo.InvariantCulture, idNum) OrElse idNum <= 0 OrElse idNum <> Math.Floor(idNum) Then
                result.NoIdCount += 1
                Continue For
            End If
            Dim id As Integer = CInt(idNum)
            If Not idsSeen.Add(id) Then result.DuplicateIds += 1
            Dim row As DataRow = FindRow(dt, id)
            If row Is Nothing Then
                result.NotFoundCount += 1
                If result.NotFound.Count < 10 Then result.NotFound.Add(id.ToString(CultureInfo.InvariantCulture))
                Continue For
            End If
            For Each kv As KeyValuePair(Of Integer, ColDef) In map
                If kv.Key >= cells.Length Then Continue For    ' short row: leave the cell alone
                Dim col As ColDef = kv.Value
                Dim fileValue As String = CleanUploadValue(cells(kv.Key))
                Dim current As String = InputText(row(col.Field), col.Kind)
                Dim k As String = id.ToString(CultureInfo.InvariantCulture) & "|" & col.Field
                If SameValue(fileValue, current, col.Kind) Then
                    lastChange.Remove(k)
                Else
                    lastChange(k) = New CellChange() With {.Id = id, .Field = col.Field, .Value = fileValue}
                End If
            Next
        Next
        result.Changes = lastChange.Values.ToList()
        result.ChangedRows = result.Changes.Select(Function(c) c.Id).Distinct().Count()
        Return result
    End Function

    Private Shared Function NormalizeHeader(h As String) As String
        Return Regex.Replace(If(h, ""), "[\s_\-\.]+", "").ToUpperInvariant()
    End Function

    ' Undo the formula-injection apostrophe the CSV export adds ('=..., '+..., '-..., '@...).
    Private Shared Function CleanUploadValue(value As String) As String
        Dim v As String = If(value, "").Trim()
        If v.Length > 1 AndAlso v(0) = "'"c AndAlso "=+-@".IndexOf(v(1)) >= 0 Then v = v.Substring(1)
        Return v
    End Function

    ' Spreadsheet round trips change the look of numbers (12.5000 / 12.5, 0.1 / .1, 00123 / 123):
    ' treat numerically equal values as unchanged so only real edits are staged.
    Private Shared Function SameValue(fileValue As String, current As String, kind As ColKind) As Boolean
        If String.Equals(fileValue, current, StringComparison.Ordinal) Then Return True
        Dim a, b As Double
        Dim aNum As Boolean = Double.TryParse(fileValue, NumberStyles.Float, CultureInfo.InvariantCulture, a)
        Dim bNum As Boolean = Double.TryParse(current, NumberStyles.Float, CultureInfo.InvariantCulture, b)
        If kind = ColKind.Number OrElse kind = ColKind.Start Then
            If fileValue.Length = 0 Then aNum = True : a = 0     ' blank saves as 0
            If current.Length = 0 Then bNum = True : b = 0
        End If
        Return aNum AndAlso bNum AndAlso Math.Abs(a - b) < 0.000001
    End Function

    ' CSV: UTF-8 / UTF-16 (BOM) or ANSI (Excel's "CSV (Comma delimited)"); comma, semicolon or tab.
    Private Shared Function ReadCsv(bytes As Byte()) As List(Of String())
        Dim text As String
        If bytes.Length >= 3 AndAlso bytes(0) = &HEF AndAlso bytes(1) = &HBB AndAlso bytes(2) = &HBF Then
            text = Encoding.UTF8.GetString(bytes, 3, bytes.Length - 3)
        ElseIf bytes.Length >= 2 AndAlso bytes(0) = &HFF AndAlso bytes(1) = &HFE Then
            text = Encoding.Unicode.GetString(bytes, 2, bytes.Length - 2)
        ElseIf bytes.Length >= 2 AndAlso bytes(0) = &HFE AndAlso bytes(1) = &HFF Then
            text = Encoding.BigEndianUnicode.GetString(bytes, 2, bytes.Length - 2)
        Else
            Try
                text = New UTF8Encoding(False, True).GetString(bytes)
            Catch ex As DecoderFallbackException
                ' Excel's "CSV (Comma delimited)" is ANSI: Windows-1252 on Western-language PCs.
                text = Encoding.GetEncoding(1252).GetString(bytes)
            End Try
        End If

        ' Delimiter: whichever of , ; TAB occurs most in the header line.
        Dim firstLine As String = text.Split({ControlChars.Lf}, 2, StringSplitOptions.None)(0)
        Dim delim As Char = ","c
        Dim best As Integer = firstLine.Count(Function(ch) ch = ","c)
        For Each cand As Char In {";"c, ControlChars.Tab}
            Dim n As Integer = firstLine.Count(Function(ch) ch = cand)
            If n > best Then best = n : delim = cand
        Next

        Dim rows As New List(Of String())()
        Dim cur As New List(Of String)()
        Dim field As New StringBuilder()
        Dim inQuotes As Boolean = False
        Dim i As Integer = 0
        While i < text.Length
            Dim ch As Char = text(i)
            If inQuotes Then
                If ch = """"c Then
                    If i + 1 < text.Length AndAlso text(i + 1) = """"c Then
                        field.Append(""""c)
                        i += 1
                    Else
                        inQuotes = False
                    End If
                Else
                    field.Append(ch)
                End If
            ElseIf ch = """"c AndAlso field.Length = 0 Then
                inQuotes = True
            ElseIf ch = delim Then
                cur.Add(field.ToString())
                field.Clear()
            ElseIf ch = ControlChars.Cr OrElse ch = ControlChars.Lf Then
                If ch = ControlChars.Cr AndAlso i + 1 < text.Length AndAlso text(i + 1) = ControlChars.Lf Then i += 1
                cur.Add(field.ToString())
                field.Clear()
                rows.Add(cur.ToArray())
                cur.Clear()
            Else
                field.Append(ch)
            End If
            i += 1
        End While
        If field.Length > 0 OrElse cur.Count > 0 Then
            cur.Add(field.ToString())
            rows.Add(cur.ToArray())
        End If
        Return rows
    End Function

    ' XLSX: first worksheet; shared / inline strings and numbers (as plain invariant text).
    Private Shared Function ReadXlsx(bytes As Byte()) As List(Of String())
        Dim rows As New List(Of String())()
        Using ms As New MemoryStream(bytes)
            Using doc As OXP.SpreadsheetDocument = OXP.SpreadsheetDocument.Open(ms, False)
                Dim wb As OXP.WorkbookPart = doc.WorkbookPart
                Dim sheet As S.Sheet = wb.Workbook.Descendants(Of S.Sheet)().FirstOrDefault()
                If sheet Is Nothing Then Return rows
                Dim ws As OXP.WorksheetPart = CType(wb.GetPartById(sheet.Id.Value), OXP.WorksheetPart)
                Dim sharedStrings As List(Of String) = Nothing
                If wb.SharedStringTablePart IsNot Nothing Then
                    sharedStrings = wb.SharedStringTablePart.SharedStringTable.Elements(Of S.SharedStringItem)().Select(Function(x) x.InnerText).ToList()
                End If
                Dim expected As Integer = 1
                For Each xr As S.Row In ws.Worksheet.Descendants(Of S.Row)()
                    ' Keep row positions (blank rows are skipped by the caller).
                    Dim rowNo As Integer = If(xr.RowIndex IsNot Nothing, CInt(xr.RowIndex.Value), expected)
                    While expected < rowNo
                        rows.Add(New String() {})
                        expected += 1
                    End While
                    expected = rowNo + 1
                    Dim cells As New List(Of String)()
                    For Each c As S.Cell In xr.Elements(Of S.Cell)()
                        Dim idx As Integer = If(c.CellReference IsNot Nothing, ColumnIndex(c.CellReference.Value), cells.Count)
                        While cells.Count < idx
                            cells.Add("")
                        End While
                        Dim value As String = XlsxCellText(c, sharedStrings)
                        If cells.Count = idx Then cells.Add(value) Else cells(idx) = value
                    Next
                    rows.Add(cells.ToArray())
                Next
            End Using
        End Using
        Return rows
    End Function

    Private Shared Function XlsxCellText(c As S.Cell, sharedStrings As List(Of String)) As String
        If c.DataType IsNot Nothing Then
            If c.DataType.Value = S.CellValues.SharedString Then
                Dim n As Integer
                If c.CellValue IsNot Nothing AndAlso sharedStrings IsNot Nothing AndAlso
                   Integer.TryParse(c.CellValue.Text, NumberStyles.Integer, CultureInfo.InvariantCulture, n) AndAlso n >= 0 AndAlso n < sharedStrings.Count Then
                    Return sharedStrings(n)
                End If
                Return ""
            ElseIf c.DataType.Value = S.CellValues.InlineString Then
                Return If(c.InlineString Is Nothing, "", c.InlineString.InnerText)
            ElseIf c.DataType.Value = S.CellValues.Boolean Then
                Return If(c.CellValue IsNot Nothing AndAlso c.CellValue.Text = "1", "TRUE", "FALSE")
            ElseIf c.DataType.Value <> S.CellValues.Number Then
                Return If(c.CellValue Is Nothing, "", c.CellValue.Text)   ' formula string result, error ...
            End If
        End If
        If c.CellValue Is Nothing Then Return ""
        ' Numbers: Excel stores binary doubles (0.1 -> 0.10000000000000001); write them back plainly.
        Dim d As Double
        If Double.TryParse(c.CellValue.Text, NumberStyles.Float, CultureInfo.InvariantCulture, d) Then
            Return Math.Round(d, 10).ToString("0.##########", CultureInfo.InvariantCulture)
        End If
        Return c.CellValue.Text
    End Function

    ' "AB12" -> 27 (zero-based column index).
    Private Shared Function ColumnIndex(reference As String) As Integer
        Dim n As Integer = 0
        For Each ch As Char In If(reference, "").ToUpperInvariant()
            If ch < "A"c OrElse ch > "Z"c Then Exit For
            n = n * 26 + (AscW(ch) - AscW("A"c) + 1)
        Next
        Return Math.Max(n - 1, 0)
    End Function

#End Region

#Region "Value helpers"

    Private Shared Function TryParseDecimal(text As String, ByRef result As Decimal) As Boolean
        Const styles As NumberStyles = NumberStyles.AllowDecimalPoint Or NumberStyles.AllowLeadingWhite Or NumberStyles.AllowTrailingWhite
        Return Decimal.TryParse(text, styles, CultureInfo.InvariantCulture, result) OrElse
               Decimal.TryParse(text, styles, CultureInfo.CurrentCulture, result)
    End Function

    ' Column sizes of CTD_DDR_DISC, read once per application: feeds maxlength on the inputs and
    ' the server-side length check, so over-long text gets a clear message instead of a SQL error.
    Private Shared _colLengths As Dictionary(Of String, Integer)
    Private Shared ReadOnly ColLengthsLock As New Object()

    Private Shared Function ColumnMaxLength(field As String) As Integer
        Dim map As Dictionary(Of String, Integer) = _colLengths
        If map Is Nothing Then
            SyncLock ColLengthsLock
                If _colLengths Is Nothing Then
                    Dim m As New Dictionary(Of String, Integer)(StringComparer.OrdinalIgnoreCase)
                    Try
                        Dim dt As DataTable = QueryTable(
                            "SELECT [COLUMN_NAME], [CHARACTER_MAXIMUM_LENGTH] FROM INFORMATION_SCHEMA.COLUMNS
                             WHERE [TABLE_NAME] = 'CTD_DDR_DISC' AND [CHARACTER_MAXIMUM_LENGTH] IS NOT NULL")
                        For Each r As DataRow In dt.Rows
                            m(Convert.ToString(r(0))) = Convert.ToInt32(r(1), CultureInfo.InvariantCulture)
                        Next
                        _colLengths = m
                    Catch ex As Exception
                        Return 0   ' unknown: let SQL Server enforce it, and try again next time
                    End Try
                End If
                map = _colLengths
            End SyncLock
        End If
        Dim n As Integer
        Return If(map.TryGetValue(field, n) AndAlso n > 0, n, 0)
    End Function

#End Region

#Region "Security"

    Private Shared Function CurrentUserName(ctx As HttpContext) As String
        Dim name As String = ""
        If ctx IsNot Nothing AndAlso ctx.User IsNot Nothing AndAlso ctx.User.Identity IsNot Nothing Then
            name = If(ctx.User.Identity.Name, "")
        End If
        Dim slash As Integer = name.LastIndexOf("\"c)
        If slash >= 0 Then name = name.Substring(slash + 1)   ' strip "WORLEY\" (any domain)
        Return name.Trim()
    End Function

    ' Checked on page load AND on every save; the answer is cached for the session.
    Private Shared Function IsAdminUser(ctx As HttpContext, user As String) As Boolean
        If Not RequireAdmin Then Return True
        If String.IsNullOrEmpty(user) OrElse ctx Is Nothing OrElse ctx.Session Is Nothing Then Return False
        Dim cacheKey As String = SessionPrefix & "ADMIN_" & user.ToUpperInvariant()
        Dim cached As Object = ctx.Session(cacheKey)
        If TypeOf cached Is Boolean Then Return DirectCast(cached, Boolean)
        Try
            Dim ok As Boolean = CBool(ST_Common.IsAdmin(user))
            ctx.Session(cacheKey) = ok
            Return ok
        Catch ex As Exception
            ctx.Trace.Warn("AdminDDR", "IsAdmin check failed", ex)
            Return False   ' not cached: the next request tries again
        End Try
    End Function

#End Region

#Region "Export"

    Private Sub btnExcel_Click(sender As Object, e As EventArgs) Handles btnExcel.Click
        Dim dt As DataTable = GetExportTable()
        If dt Is Nothing OrElse dt.Rows.Count = 0 Then
            ShowMessage("There are no rows on screen to export.", "warn")
            Return
        End If
        Dim name As String = ExportFileName()
        SendFile(BuildXlsx(dt, "DDR"), name & ".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    End Sub

    Private Sub btnCSV_Click(sender As Object, e As EventArgs) Handles btnCSV.Click
        Dim dt As DataTable = GetExportTable()
        If dt Is Nothing OrElse dt.Rows.Count = 0 Then
            ShowMessage("There are no rows on screen to export.", "warn")
            Return
        End If
        SendFile(BuildCsv(dt), ExportFileName() & ".csv", "text/csv")
    End Sub

    ' Exports exactly the rows on screen (discipline, search and column filters), in the on-screen order.
    Private Function GetExportTable() As DataTable
        Dim dt As DataTable = EnsureData()
        If dt Is Nothing Then Return Nothing

        Dim rows As List(Of DataRow)
        Dim posted As String = If(hfExportIds.Value, "").Trim()
        If posted.Length = 0 Then
            rows = RowsForDiscipline(dt)          ' script did not run: fall back to the discipline rows
        ElseIf posted = "-" Then
            rows = New List(Of DataRow)()         ' every row is filtered out
        Else
            Dim byId As New Dictionary(Of Integer, DataRow)()
            For Each r As DataRow In dt.Rows
                Dim n As Double
                If TryGetNumber(r("DDR_ID"), n) AndAlso Not byId.ContainsKey(CInt(n)) Then byId(CInt(n)) = r
            Next
            rows = New List(Of DataRow)()
            For Each part As String In posted.Split(","c)
                Dim id As Integer
                Dim r As DataRow = Nothing
                If Integer.TryParse(part, NumberStyles.Integer, CultureInfo.InvariantCulture, id) AndAlso byId.TryGetValue(id, r) Then rows.Add(r)
            Next
        End If

        Dim out As New DataTable("DDR")
        For Each c As ColDef In Cols
            out.Columns.Add(c.Header, If(c.Kind = ColKind.Key OrElse c.Kind = ColKind.Number OrElse c.Kind = ColKind.Start,
                                         dt.Columns(c.Field).DataType, GetType(Object)))
            If c.Field = "CTD_ID" Then out.Columns.Add("Discipline", GetType(String))
        Next
        For Each r As DataRow In rows
            Dim values As New List(Of Object)()
            For Each c As ColDef In Cols
                values.Add(ExportValue(r(c.Field), c.Kind))
                If c.Field = "CTD_ID" Then values.Add(r("Discipline"))
            Next
            out.Rows.Add(values.ToArray())
        Next
        Return out
    End Function

    ' Progress fractions are exported as the percentage shown on screen (0.25 -> 25).
    Private Shared Function ExportValue(value As Object, kind As ColKind) As Object
        Dim n As Double
        If (kind = ColKind.Percent OrElse kind = ColKind.Status) AndAlso IsNumericValue(value) AndAlso TryGetNumber(value, n) Then
            Return Math.Round(n * 100, 2)
        End If
        Return value
    End Function

    Private Function ExportFileName() As String
        Dim name As String = SelectedProject & "_DDR_ADMIN"
        Dim disc As String = SelectedDiscipline
        If disc.Length > 0 AndAlso Not disc.Equals("All", StringComparison.OrdinalIgnoreCase) Then name &= "_" & disc
        Return SafeFileName(name & "_" & DateTime.Now.ToString("yyyyMMdd", CultureInfo.InvariantCulture))
    End Function

    Private Sub SendFile(content As Byte(), fileName As String, contentType As String)
        _suppressRender = True
        Response.Clear()
        Response.ContentType = contentType
        Response.AddHeader("Content-Disposition",
                           "attachment; filename=""" & fileName & """; filename*=UTF-8''" & Uri.EscapeDataString(fileName))
        Response.BinaryWrite(content)
        Response.Flush()
        Response.SuppressContent = True
        Context.ApplicationInstance.CompleteRequest()
    End Sub

    Private Shared Function BuildCsv(dt As DataTable) As Byte()
        Dim sb As New StringBuilder()
        sb.AppendLine(String.Join(",", dt.Columns.Cast(Of DataColumn)().Select(Function(c) CsvField(c.ColumnName))))
        For Each r As DataRow In dt.Rows
            sb.AppendLine(String.Join(",", r.ItemArray.Select(Function(v) CsvField(ExportText(v), IsNumericValue(v)))))
        Next
        Dim utf8 As New UTF8Encoding(True)
        Return utf8.GetPreamble().Concat(utf8.GetBytes(sb.ToString())).ToArray()
    End Function

    Private Shared Function CsvField(value As String, Optional isNumber As Boolean = False) As String
        If String.IsNullOrEmpty(value) Then Return ""
        ' Stop spreadsheet formula injection from text values (real numbers may start with "-").
        If Not isNumber AndAlso ("=+-@" & vbTab & vbCr).IndexOf(value(0)) >= 0 Then value = "'" & value
        If value.IndexOfAny({","c, """"c, ControlChars.Cr, ControlChars.Lf}) >= 0 Then
            Return """" & value.Replace("""", """""") & """"
        End If
        Return value
    End Function

    Private Shared Function ExportText(value As Object) As String
        If value Is Nothing OrElse Convert.IsDBNull(value) Then Return ""
        If TypeOf value Is DateTime Then
            Return DirectCast(value, DateTime).ToString("dd-MMM-yyyy", CultureInfo.InvariantCulture)
        End If
        Dim f As IFormattable = TryCast(value, IFormattable)
        If f IsNot Nothing AndAlso IsNumericValue(value) Then Return f.ToString(Nothing, CultureInfo.InvariantCulture)
        Return Convert.ToString(value, CultureInfo.CurrentCulture)
    End Function

    Private Shared Function BuildXlsx(dt As DataTable, title As String) As Byte()
        Dim sheetName As String = SafeSheetName(title)
        Dim colCount As Integer = dt.Columns.Count

        Using ms As New MemoryStream()
            Using doc As OXP.SpreadsheetDocument = OXP.SpreadsheetDocument.Create(ms, OX.SpreadsheetDocumentType.Workbook)
                Dim wbPart As OXP.WorkbookPart = doc.AddWorkbookPart()
                wbPart.Workbook = New S.Workbook()

                Dim stylesPart As OXP.WorkbookStylesPart = wbPart.AddNewPart(Of OXP.WorkbookStylesPart)()
                stylesPart.Stylesheet = BuildStylesheet()
                stylesPart.Stylesheet.Save()

                Dim wsPart As OXP.WorksheetPart = wbPart.AddNewPart(Of OXP.WorksheetPart)()
                Dim sheetData As New S.SheetData()
                Dim widths(Math.Max(colCount - 1, 0)) As Integer

                Dim header As New S.Row() With {.RowIndex = 1UI}
                For c As Integer = 0 To colCount - 1
                    Dim name As String = dt.Columns(c).ColumnName
                    header.AppendChild(TextCell(name, 1UI))
                    widths(c) = name.Length
                Next
                sheetData.AppendChild(header)

                Dim rowIndex As UInteger = 1UI
                For Each dr As DataRow In dt.Rows
                    rowIndex += 1UI
                    Dim xr As New S.Row() With {.RowIndex = rowIndex}
                    For c As Integer = 0 To colCount - 1
                        Dim shownLength As Integer = 0
                        xr.AppendChild(ValueCell(dr(c), shownLength))
                        widths(c) = Math.Max(widths(c), shownLength)
                    Next
                    sheetData.AppendChild(xr)
                Next

                ' Single-child elements are added with AppendChild to avoid the SDK's
                ' params / IEnumerable constructor overload ambiguity.
                Dim ws As New S.Worksheet()
                Dim sheetView As New S.SheetView() With {.WorkbookViewId = 0UI}
                sheetView.AppendChild(New S.Pane() With {
                    .VerticalSplit = 1.0R,
                    .TopLeftCell = "A2",
                    .ActivePane = S.PaneValues.BottomLeft,
                    .State = S.PaneStateValues.Frozen})
                Dim sheetViews As New S.SheetViews()
                sheetViews.AppendChild(sheetView)
                ws.AppendChild(sheetViews)

                If colCount > 0 Then
                    Dim columns As New S.Columns()
                    For c As Integer = 0 To colCount - 1
                        columns.AppendChild(New S.Column() With {
                            .Min = CUInt(c + 1), .Max = CUInt(c + 1),
                            .Width = CDbl(Math.Min(Math.Max(widths(c), 8), 60) + 2),
                            .CustomWidth = True})
                    Next
                    ws.AppendChild(columns)
                End If

                ws.AppendChild(sheetData)

                Dim lastCol As String = ColumnLetter(Math.Max(colCount, 1))
                If colCount > 0 Then
                    ws.AppendChild(New S.AutoFilter() With {.Reference = "A1:" & lastCol & rowIndex.ToString(CultureInfo.InvariantCulture)})
                End If
                wsPart.Worksheet = ws
                wsPart.Worksheet.Save()

                Dim sheets As New S.Sheets()
                sheets.AppendChild(New S.Sheet() With {.Id = wbPart.GetIdOfPart(wsPart), .SheetId = 1UI, .Name = sheetName})
                wbPart.Workbook.AppendChild(sheets)
                If colCount > 0 Then
                    Dim definedNames As New S.DefinedNames()
                    definedNames.AppendChild(New S.DefinedName("'" & sheetName.Replace("'", "''") & "'!$A$1:$" & lastCol & "$" & rowIndex.ToString(CultureInfo.InvariantCulture)) With {
                        .Name = "_xlnm._FilterDatabase", .LocalSheetId = 0UI, .Hidden = True})
                    wbPart.Workbook.AppendChild(definedNames)
                End If
                wbPart.Workbook.Save()
            End Using
            Return ms.ToArray()
        End Using
    End Function

    Private Shared Function BuildStylesheet() As S.Stylesheet
        Dim numberFormats As New S.NumberingFormats(
            New S.NumberingFormat() With {.NumberFormatId = 164UI, .FormatCode = "dd-mmm-yyyy"}) With {.Count = 1UI}

        Dim fonts As New S.Fonts(
            New S.Font(New S.FontSize() With {.Val = 11.0R}, New S.FontName() With {.Val = "Calibri"}),
            New S.Font(New S.Bold(), New S.Color() With {.Rgb = "FFFFFFFF"}, New S.FontSize() With {.Val = 11.0R}, New S.FontName() With {.Val = "Calibri"})) With {.Count = 2UI}

        Dim fills As New S.Fills(
            New S.Fill() With {.PatternFill = New S.PatternFill() With {.PatternType = S.PatternValues.None}},
            New S.Fill() With {.PatternFill = New S.PatternFill() With {.PatternType = S.PatternValues.Gray125}},
            New S.Fill() With {.PatternFill = New S.PatternFill(New S.ForegroundColor() With {.Rgb = "FF1B3B3B"}, New S.BackgroundColor() With {.Indexed = 64UI}) With {.PatternType = S.PatternValues.Solid}}) With {.Count = 3UI}

        Dim borders As New S.Borders() With {.Count = 1UI}
        borders.AppendChild(New S.Border(New S.LeftBorder(), New S.RightBorder(), New S.TopBorder(), New S.BottomBorder(), New S.DiagonalBorder()))

        Dim cellFormats As New S.CellFormats(
            New S.CellFormat() With {.NumberFormatId = 0UI, .FontId = 0UI, .FillId = 0UI, .BorderId = 0UI},
            New S.CellFormat() With {.NumberFormatId = 0UI, .FontId = 1UI, .FillId = 2UI, .BorderId = 0UI, .ApplyFont = True, .ApplyFill = True},
            New S.CellFormat() With {.NumberFormatId = 164UI, .FontId = 0UI, .FillId = 0UI, .BorderId = 0UI, .ApplyNumberFormat = True}) With {.Count = 3UI}

        Return New S.Stylesheet(numberFormats, fonts, fills, borders, cellFormats)
    End Function

    Private Shared Function TextCell(text As String, Optional styleIndex As UInteger = 0UI) As S.Cell
        Dim clean As String = InvalidXmlChars.Replace(If(text, ""), "")
        If clean.Length > 32767 Then clean = clean.Substring(0, 32767)
        Dim cell As New S.Cell() With {.DataType = S.CellValues.InlineString}
        If styleIndex <> 0UI Then cell.StyleIndex = styleIndex
        Dim inline As New S.InlineString()
        inline.AppendChild(New S.Text(clean) With {.Space = OX.SpaceProcessingModeValues.Preserve})
        cell.AppendChild(inline)
        Return cell
    End Function

    Private Shared Function ValueCell(value As Object, ByRef shownLength As Integer) As S.Cell
        shownLength = 0
        If value Is Nothing OrElse Convert.IsDBNull(value) Then Return New S.Cell()

        If TypeOf value Is DateTime Then
            Dim d As DateTime = DirectCast(value, DateTime)
            shownLength = 11
            Return New S.Cell() With {
                .CellValue = New S.CellValue(d.ToOADate().ToString(CultureInfo.InvariantCulture)),
                .StyleIndex = 2UI}
        End If

        If TypeOf value Is Boolean Then
            shownLength = 5
            Return New S.Cell() With {.CellValue = New S.CellValue(If(DirectCast(value, Boolean), "1", "0")), .DataType = S.CellValues.Boolean}
        End If

        If IsNumericValue(value) Then
            Dim dbl As Double = Convert.ToDouble(value, CultureInfo.InvariantCulture)
            If Not Double.IsNaN(dbl) AndAlso Not Double.IsInfinity(dbl) Then
                ' Not named "s": VB is case-insensitive and a local "s" would hide the S (Spreadsheet) alias.
                Dim numText As String = dbl.ToString("R", CultureInfo.InvariantCulture)
                shownLength = Math.Min(numText.Length, 15)
                Return New S.Cell() With {.CellValue = New S.CellValue(numText), .DataType = S.CellValues.Number}
            End If
        End If

        Dim text As String = Convert.ToString(value, CultureInfo.CurrentCulture)
        shownLength = Math.Min(text.Length, 60)
        Return TextCell(text)
    End Function

    Private Shared Function ColumnLetter(index As Integer) As String
        Dim letters As String = ""
        While index > 0
            Dim m As Integer = (index - 1) Mod 26
            letters = ChrW(65 + m) & letters
            index = (index - 1) \ 26
        End While
        Return letters
    End Function

    Private Shared Function SafeSheetName(name As String) As String
        Dim clean As String = Regex.Replace(If(name, ""), "[\[\]\:\*\?/\\']", "_").Trim()
        If clean.Length = 0 Then clean = "DDR"
        Return If(clean.Length > 31, clean.Substring(0, 31), clean)
    End Function

    Private Shared Function SafeFileName(name As String) As String
        Dim clean As String = Regex.Replace(If(name, ""), "[^\w\-\.]+", "_").Trim("_"c)
        Return If(clean.Length = 0, "DDR_ADMIN", clean)
    End Function

#End Region

#Region "Data access"

    Private Shared Function ConnString() As String
        Return CStr(ST_Common.WorleyDataConnString)
    End Function

    Private Shared Function P(name As String, value As String) As SqlParameter
        ' VARCHAR: an NVARCHAR parameter against a VARCHAR column forces an implicit
        ' conversion of the column and can turn index seeks into scans.
        Dim prm As New SqlParameter(name, SqlDbType.VarChar, 255)
        If value Is Nothing Then
            prm.Value = DBNull.Value
        Else
            prm.Value = value
        End If
        Return prm
    End Function

    Private Shared Function QueryTable(sql As String, ParamArray prms As SqlParameter()) As DataTable
        Using con As New SqlConnection(ConnString())
            Using cmd As New SqlCommand(sql, con)
                cmd.CommandTimeout = 180
                If prms IsNot Nothing Then cmd.Parameters.AddRange(prms)
                Using da As New SqlDataAdapter(cmd)
                    Dim dt As New DataTable()
                    da.Fill(dt)
                    Return dt
                End Using
            End Using
        End Using
    End Function

    Private Shared Function ExecNonQuery(sql As String, ParamArray prms As SqlParameter()) As Integer
        Using con As New SqlConnection(ConnString())
            ' NOCOUNT OFF so the affected-row count is reported even if the server default is ON.
            Using cmd As New SqlCommand("SET NOCOUNT OFF; " & sql, con)
                cmd.CommandTimeout = 180
                If prms IsNot Nothing Then cmd.Parameters.AddRange(prms)
                con.Open()
                Return cmd.ExecuteNonQuery()
            End Using
        End Using
    End Function

#End Region

#Region "Helpers"

    Private Sub ShowMessage(text As String, Optional kind As String = "info")
        lblMessage.Text = HttpUtility.HtmlEncode(text)
        lblMessage.CssClass = "toast toast-" & kind
        lblMessage.Visible = True
    End Sub

    Private Shared Function IsNumericValue(value As Object) As Boolean
        Return TypeOf value Is Byte OrElse TypeOf value Is SByte OrElse TypeOf value Is Short OrElse
               TypeOf value Is UShort OrElse TypeOf value Is Integer OrElse TypeOf value Is UInteger OrElse
               TypeOf value Is Long OrElse TypeOf value Is ULong OrElse TypeOf value Is Single OrElse
               TypeOf value Is Double OrElse TypeOf value Is Decimal
    End Function

    Private Shared Function TryGetNumber(value As Object, ByRef result As Double) As Boolean
        result = 0
        If value Is Nothing OrElse Convert.IsDBNull(value) Then Return False
        If IsNumericValue(value) Then
            result = Convert.ToDouble(value, CultureInfo.InvariantCulture)
            If Double.IsNaN(result) OrElse Double.IsInfinity(result) Then
                result = 0
                Return False
            End If
            Return True
        End If
        Dim str As String = TryCast(value, String)
        If str Is Nothing Then Return False
        str = str.Replace("%", "").Trim()
        If str.Length = 0 Then Return False
        Const styles As NumberStyles = NumberStyles.Float Or NumberStyles.AllowThousands
        If Double.TryParse(str, styles, CultureInfo.InvariantCulture, result) Then Return True
        If Double.TryParse(str, styles, CultureInfo.CurrentCulture, result) Then Return True
        result = 0
        Return False
    End Function

    Private Shared Function Truncate(str As String, maxLength As Integer) As String
        If str Is Nothing OrElse str.Length <= maxLength Then Return str
        Return str.Substring(0, maxLength - 1) & "…"
    End Function

#End Region

End Class

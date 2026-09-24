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
' search, Excel-like column filters, sorting and exports all work on that cache. Each edited
' cell is saved on its own through the SaveCell page method (no postback, no grid rebind).
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
        New ColDef("HO_STATUS", "HO_REQ", ColKind.Text, 112, True, True),
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

    Private Shared ReadOnly RowSql As String =
        "SELECT " & String.Join(", ", Cols.Select(Function(c) "[" & c.Field & "]")) & "
         FROM [DDR_DISC_PERC] WHERE [DDR_ID] = @ID"

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
            Case ColKind.Status
                If IsNumericValue(value) AndAlso TryGetNumber(value, n) Then
                    Return (n * 100).ToString("0.00", CultureInfo.InvariantCulture) & "%"
                End If
        End Select
        Return HttpUtility.HtmlEncode(InputText(value, kind))
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

#Region "Cell save (page method)"

    Public NotInheritable Class SaveResult
        Public Property Ok As Boolean
        Public Property Message As String = ""
        Public Property Level As String = "success"
        Public Property Deleted As Boolean
        Public Property Values As New Dictionary(Of String, String)()
        Public Property Html As New Dictionary(Of String, String)()
    End Class

    Private Shared Function Failed(message As String) As SaveResult
        Return New SaveResult() With {.Ok = False, .Message = message, .Level = "error"}
    End Function

    ' Saves one cell. Called from the page with PageMethods.SaveCell(key, ddrId, field, value).
    <WebMethod(EnableSession:=True)>
    Public Shared Function SaveCell(key As String, ddrId As Integer, field As String, value As String) As SaveResult
        Dim ctx As HttpContext = HttpContext.Current
        Try
            Dim user As String = CurrentUserName(ctx)
            If Not IsAdminUser(ctx, user) Then Return Failed("You do not have permission to edit the DDR.")

            Dim col As ColDef = Cols.FirstOrDefault(Function(c) c.Editable AndAlso c.Field.Equals(If(field, ""), StringComparison.OrdinalIgnoreCase))
            If col Is Nothing Then Return Failed("This column cannot be edited.")
            If ddrId <= 0 Then Return Failed("Unknown DDR row.")

            Dim text As String = If(value, "").Trim()
            Dim prmValue As SqlParameter
            Select Case col.Kind
                Case ColKind.Number
                    Dim d As Decimal
                    If text.Length = 0 Then
                        d = 0D
                    ElseIf Not TryParseDecimal(text, d) OrElse d < 0D OrElse d >= 1000000D Then
                        Return Failed("Man Hours must be a number between 0 and 999,999.")
                    End If
                    prmValue = New SqlParameter("@V", SqlDbType.Decimal) With {.Precision = 18, .Scale = 4, .Value = d}
                Case ColKind.Start
                    Dim d As Decimal
                    If text.Length = 0 Then
                        d = 0D
                    ElseIf Not TryParseDecimal(text, d) OrElse (d <> 0D AndAlso d <> 0.1D) Then
                        Return Failed("START accepts only 0 or 0.1.")
                    End If
                    prmValue = New SqlParameter("@V", SqlDbType.Decimal) With {.Precision = 18, .Scale = 4, .Value = d}
                Case Else
                    Dim maxLen As Integer = ColumnMaxLength(col.Field)
                    If maxLen > 0 AndAlso text.Length > maxLen Then
                        Return Failed(String.Format(CultureInfo.InvariantCulture, "{0} can hold at most {1} characters.", col.Header, maxLen))
                    End If
                    prmValue = New SqlParameter("@V", SqlDbType.NVarChar, If(maxLen > 0, maxLen, -1)) With {.Value = text}
            End Select

            ' Typing DELETED in Remarks retires the document: title gets "(DELETED)", Man_Hours = 0.
            Dim retire As Boolean = col.Field = "Disc_Remarks" AndAlso DeletedRemark.IsMatch(text)
            Dim sql As New StringBuilder("SET NOCOUNT OFF; UPDATE [CTD_DDR_DISC] SET [")
            sql.Append(col.Field).Append("] = @V")
            If retire Then
                sql.Append(", [Document_Title] = CASE WHEN ISNULL([Document_Title], '') LIKE '%(DELETED)%' THEN [Document_Title]
                                  ELSE LTRIM(LEFT(RTRIM(ISNULL([Document_Title], '')), @ROOM) + '").Append(DeletedTag).Append("') END,
                               [Man_Hours] = 0")
            End If
            sql.Append(" WHERE [DDR_ID] = @ID")

            Dim fresh As DataTable
            Using con As New SqlConnection(ConnString())
                con.Open()
                Using cmd As New SqlCommand(sql.ToString(), con)
                    cmd.CommandTimeout = 60
                    cmd.Parameters.Add(prmValue)
                    cmd.Parameters.Add(New SqlParameter("@ID", SqlDbType.Int) With {.Value = ddrId})
                    If retire Then
                        Dim titleMax As Integer = ColumnMaxLength("Document_Title")
                        cmd.Parameters.Add(New SqlParameter("@ROOM", SqlDbType.Int) With {
                            .Value = If(titleMax > DeletedTag.Length, titleMax - DeletedTag.Length, 100000)})
                    End If
                    If cmd.ExecuteNonQuery() = 0 Then
                        Return Failed("DDR row " & ddrId.ToString(CultureInfo.InvariantCulture) & " no longer exists. Reload the page.")
                    End If
                End Using
                Using cmd As New SqlCommand(RowSql, con)
                    cmd.CommandTimeout = 60
                    cmd.Parameters.Add(New SqlParameter("@ID", SqlDbType.Int) With {.Value = ddrId})
                    fresh = New DataTable()
                    Using da As New SqlDataAdapter(cmd)
                        da.Fill(fresh)
                    End Using
                End Using
            End Using

            ' Keep the session cache in step so filters, totals and exports see the change.
            Dim dt As DataTable = If(KeyPattern.IsMatch(If(key, "")), CachedTable(ctx.Session, key), Nothing)
            Dim cached As DataRow = FindRow(dt, ddrId)
            Dim source As DataRow = If(fresh.Rows.Count > 0, fresh.Rows(0), Nothing)
            If cached IsNot Nothing Then
                If source IsNot Nothing Then
                    For Each c As ColDef In Cols
                        If c.Kind = ColKind.Key Then Continue For
                        Try
                            cached(c.Field) = source(c.Field)
                        Catch ex As Exception
                            ' Type mismatch between the view and the cached column: leave the cached value.
                        End Try
                    Next
                Else
                    Try
                        cached(col.Field) = If(col.Kind = ColKind.Text, CObj(text), prmValue.Value)
                    Catch ex As Exception
                    End Try
                End If
            End If

            Dim result As New SaveResult() With {.Ok = True}
            Dim shown As DataRow = If(source, cached)
            If shown IsNot Nothing Then
                For Each c As ColDef In Cols
                    Dim v As Object = shown(c.Field)
                    result.Values(c.Field) = InputText(v, c.Kind)
                    If c.Kind = ColKind.Percent OrElse c.Kind = ColKind.Status Then result.Html(c.Field) = ReadOnlyHtml(v, c.Kind)
                Next
                result.Deleted = IsDeletedRow(shown)
            Else
                result.Values(col.Field) = InputText(prmValue.Value, col.Kind)
                result.Deleted = retire
            End If

            If retire Then
                result.Message = "Marked as DELETED: the title now ends with (DELETED) and Man Hours is 0."
                result.Level = "info"
            ElseIf col.Field = "Document_No" AndAlso text.Length > 0 AndAlso dt IsNot Nothing Then
                Dim others As List(Of String) = dt.Rows.Cast(Of DataRow)().
                    Where(Function(r) String.Equals(Convert.ToString(r("Document_No")).Trim(), text, StringComparison.OrdinalIgnoreCase) AndAlso
                                      r IsNot cached).
                    Select(Function(r) InputText(r("DDR_ID"), ColKind.Key)).
                    Take(5).ToList()
                If others.Count > 0 Then
                    result.Message = "Saved. Note: document " & text & " is also used by DDR row(s) " & String.Join(", ", others) & "."
                    result.Level = "warn"
                End If
            End If
            Return result

        Catch ex As SqlException
            Return Failed("Database error - the change was not saved: " & ex.Message)
        Catch ex As Exception
            Return Failed("The change was not saved: " & ex.Message)
        End Try
    End Function

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

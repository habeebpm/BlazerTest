Imports System.Collections.Generic
Imports System.Data
Imports System.Data.SqlClient
Imports System.Globalization
Imports System.IO
Imports System.Linq
Imports System.Text
Imports System.Text.RegularExpressions
Imports System.Web
Imports System.Web.UI
Imports System.Web.UI.WebControls
Imports OX = DocumentFormat.OpenXml
Imports OXP = DocumentFormat.OpenXml.Packaging
Imports S = DocumentFormat.OpenXml.Spreadsheet

Public Class SmartDDRv3
    Inherits System.Web.UI.Page

#Region "View catalogue"

    Private NotInheritable Class ViewDef
        Public ReadOnly Title As String
        Public ReadOnly FileSuffix As String
        Public ReadOnly IsGroup As Boolean
        Public ReadOnly HasFooter As Boolean
        Public ReadOnly Section As String

        Public Sub New(section As String, title As String, fileSuffix As String, isGroup As Boolean, hasFooter As Boolean)
            Me.Section = section
            Me.Title = title
            Me.FileSuffix = fileSuffix
            Me.IsGroup = isGroup
            Me.HasFooter = hasFooter
        End Sub
    End Class

    ' Key = CommandArgument of the sidebar LinkButtons.
    Private Shared ReadOnly Views As New Dictionary(Of String, ViewDef)(StringComparer.OrdinalIgnoreCase) From {
        {"G_INFO", New ViewDef("Project Groups", "Projects Info", "INFO_ALL", True, True)},
        {"G_M75", New ViewDef("Project Groups", "M75 AFC Status (all projects)", "AFC_ALL", True, True)},
        {"G_AFC_IFR", New ViewDef("Project Groups", "AFC/IFR Status (all projects)", "AFC_IFR_ALL", True, True)},
        {"G_PLIP", New ViewDef("Project Groups", "DDR PLIP Status", "PLIP_STATUS_ALL", True, False)},
        {"G_DDR_ACT", New ViewDef("Project Groups", "DDR + Activity (all projects)", "DDR_ACT_ALL", True, False)},
        {"CTD", New ViewDef("Projects", "Approved CTD", "CTD", False, True)},
        {"DDR_ACT", New ViewDef("Projects", "DDR + Activity", "DDR_ACT", False, False)},
        {"ACT", New ViewDef("Projects", "Activities", "ACT", False, True)},
        {"CTD_DDR", New ViewDef("Check Reports", "CTD vs DDR", "CTD_DDR_SUMMARY", False, True)},
        {"CTD_DOC", New ViewDef("Check Reports", "CTD vs DOC", "CTD_DDR_DOC_HOURS", False, False)},
        {"DUMMY", New ViewDef("Check Reports", "Dummy Serials", "DOCNO_DUMMY", False, False)},
        {"DDR_PDO", New ViewDef("Projects", "DDR Format (PDO)", "DDR_STD", False, False)},
        {"DDR_EPR", New ViewDef("Progress & Status", "DDR + EPR", "DDREPR", False, True)},
        {"EPR", New ViewDef("Progress & Status", "EPR", "EPR", False, True)},
        {"M75", New ViewDef("Progress & Status", "M75 AFC Status", "M75_AFC", False, True)},
        {"AFC_IFR", New ViewDef("Progress & Status", "AFC/IFR Status", "AFC_IFR", False, True)},
        {"ACON_ALL", New ViewDef("References", "Aconex - All Revisions", "ACON_REF", False, False)},
        {"ACON_LATEST", New ViewDef("References", "Aconex - Latest Revision", "ACON_LATEST_REV", False, False)},
        {"ACON_MISS", New ViewDef("Check Reports", "In Aconex, Not in DDR", "DOC_IN_ACONEX_NOT_IN_DDR", False, False)},
        {"DDR_MISSED", New ViewDef("Check Reports", "DDR Missed (ready to push)", "TODDR", False, False)}
    }

    Private Const DefaultView As String = "CTD"
    Private Const MaxCachedPages As Integer = 4

    ' Sort disciplines by their numeric prefix ("01.Civil", "2.Mech" ...), non-numeric last.
    Private Const DiscOrderSql As String = "ISNULL(TRY_CAST(NULLIF(LEFT([Discipline], PATINDEX('%[^0-9]%', [Discipline] + ' ') - 1), '') AS INT), 999)"

#End Region

#Region "Formatting rules"

    Private Shared ReadOnly CenterCols As String() = {
        "Project_No", "REV", "STATUS", "PLIP", "DCAF", "CRITICALITY", "PRIMAVERA", "_PLN", "_ACT", "Final", "Estd",
        "Used", "HOURS", "HRS", "%", "PROGRESS", "Plan", "Balance", "Actual", "Quantity", "Qty", "AFC", "IFA", "AFT",
        "IFR", "CAN", "IFH", "App", "AFD", "IDC", "NS", "AFX", "ADH", "AFP", "IFI"}

    Private Shared ReadOnly PercentCols As String() = {
        "START", "IDC", "IFR", "RCC", "APP", "AFC", "TOTAL", "PROGRESS", "Completion%", "Actual%",
        "ProgressPercent", "Progress%", "AFC%"}

    ' Percent columns that the SQL already multiplies by 100.
    Private Shared ReadOnly AlreadyPercentCols As String() = {"Actual%", "AFC%"}

    Private Shared ReadOnly RoundCols As String() = {
        "USED HOURS", "HOURS", "CTD Hours", "DDR Hours", "Earned Hours", "CTD Hrs", "DDR Hrs",
        "Estimated Hours", "Man_Hours", "Estd Hours"}

    Private Shared ReadOnly DateHintCols As String() = {"Date", "_PLN", "_ACT"}
    Private Shared ReadOnly DoneDateCols As String() = {"APP_ACT", "AFC_ACT"}
    Private Shared ReadOnly CheckCols As String() = {"DOCUMENT_NO", "PRIMAVERA_ID", "STATUS_REQD"}
    ' Dummy (placeholder) document serials created by DDR Developer: a "-"-separated
    ' segment DUM01..DUM99 or DU100+ (i.e. LIKE 'DU%'). Legacy placeholders DUMMY / XXX
    ' are still recognised so older lines keep showing up in the Dummy Serials check.
    Private Shared ReadOnly DummySerialRx As New Regex("(^|-)(DUM\d{2}|DU\d{3,}|DUMMY)(-|$)|XXX", RegexOptions.IgnoreCase Or RegexOptions.Compiled)
    Private Shared ReadOnly CtdHoursNames As String() = {"CTD Hrs", "CTD Hours", "CTD_Hrs", "CTD_HOURS"}
    Private Shared ReadOnly DdrHoursNames As String() = {"DDR Hrs", "DDR Hours", "DDR_Hrs", "DDR_HOURS"}

    Private Shared ReadOnly M75DrillCols As String(,) = {
        {"AFC_AFX_Actual", "AFC"},
        {"ADH_Actual", "ADH"},
        {"Balance", "BALANCE"}}

    Private Shared ReadOnly InvalidXmlChars As New Regex("[\x00-\x08\x0B\x0C\x0E-\x1F]", RegexOptions.Compiled)

#End Region

#Region "Per-request state"

    Private _userName As String = ""
    Private _isAdmin As Boolean
    Private _boundView As DataView
    Private _gridBound As Boolean
    Private _fetchFailed As Boolean
    Private _suppressRender As Boolean
    Private _projectFilterApplied As Boolean
    Private _discFilterNote As String = ""
    Private ReadOnly _percentScale As New Dictionary(Of String, Double)(StringComparer.OrdinalIgnoreCase)

    Private Property CurrentView As String
        Get
            Return If(TryCast(ViewState("View"), String), "")
        End Get
        Set(value As String)
            ViewState("View") = value
        End Set
    End Property

    ' In portfolio views, the project drop-down narrows the rows to one project.
    Private Property GroupProjectFilter As String
        Get
            Return If(TryCast(ViewState("GroupProjectFilter"), String), "")
        End Get
        Set(value As String)
            ViewState("GroupProjectFilter") = value
        End Set
    End Property

    Private ReadOnly Property SelectedProject As String
        Get
            Return If(DDLPROJNO.SelectedValue, "").Trim()
        End Get
    End Property

    ' One key per open page, so two browser tabs do not overwrite each other's data.
    Private ReadOnly Property GridKey As String
        Get
            Dim key As String = TryCast(ViewState("GridKey"), String)
            If String.IsNullOrEmpty(key) Then
                key = Guid.NewGuid().ToString("N")
                ViewState("GridKey") = key
                RegisterGridKey(key)
            End If
            Return key
        End Get
    End Property

    Private Property GridData As DataTable
        Get
            Return TryCast(Session("SDDR_GRID_" & GridKey), DataTable)
        End Get
        Set(value As DataTable)
            If value Is Nothing Then
                Session.Remove("SDDR_GRID_" & GridKey)
            Else
                Session("SDDR_GRID_" & GridKey) = value
            End If
        End Set
    End Property

    Private Property DrawerData As DataTable
        Get
            Return TryCast(Session("SDDR_DRAWER_" & GridKey), DataTable)
        End Get
        Set(value As DataTable)
            Session("SDDR_DRAWER_" & GridKey) = value
        End Set
    End Property

    Private Sub RegisterGridKey(key As String)
        Dim keys As List(Of String) = TryCast(Session("SDDR_KEYS"), List(Of String))
        If keys Is Nothing Then
            keys = New List(Of String)()
            Session("SDDR_KEYS") = keys
        End If
        keys.Add(key)
        While keys.Count > MaxCachedPages
            Dim oldKey As String = keys(0)
            keys.RemoveAt(0)
            Session.Remove("SDDR_GRID_" & oldKey)
            Session.Remove("SDDR_DRAWER_" & oldKey)
        End While
    End Sub

#End Region

#Region "Page lifecycle"

    Protected Sub Page_Load(sender As Object, e As EventArgs) Handles Me.Load
        _userName = GetUserName()
        If _userName.Length > 0 Then
            Try
                _isAdmin = CBool(ST_Common.IsAdmin(_userName))
            Catch ex As Exception
                _isAdmin = False
                Trace.Warn("SmartDDR", "IsAdmin check failed", ex)
            End Try
        End If

        lblUser.Text = HttpUtility.HtmlEncode(If(_userName.Length > 0, _userName, "Guest"))
        lblUser.ToolTip = _userName
        lnkAdmin.Visible = _isAdmin

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
                Trace.Warn("SmartDDR", "Could not read the user preference", ex)
            End Try
        End If

        ' Deep links: SmartDDRv3.aspx?project=XXXX&view=M75&disc=...
        Dim qsProject As String = Request.QueryString("project")
        If Not String.IsNullOrWhiteSpace(qsProject) Then savedProject = qsProject.Trim()
        Dim qsDisc As String = Request.QueryString("disc")
        If Not String.IsNullOrWhiteSpace(qsDisc) Then savedDisc = qsDisc.Trim()

        SelectGroupFor(savedProject)
        BindProjects(savedProject)
        BindDisciplines(savedDisc)
        UpdateProjectInfo()

        Dim startView As String = NormalizeViewKey(Request.QueryString("view"))
        LoadView(If(startView, DefaultView))
    End Sub

    Private Sub Page_PreRender(sender As Object, e As EventArgs) Handles Me.PreRender
        If _suppressRender Then Return

        ' The report grid keeps no ViewState: rebuild it from the cached data on full postbacks.
        If Not ScriptManager1.IsInAsyncPostBack AndAlso Not _gridBound AndAlso CurrentView.Length > 0 Then
            ApplyFilters()
        End If

        For Each lb As LinkButton In NavButtons()
            lb.CssClass = If(String.Equals(lb.CommandArgument, CurrentView, StringComparison.OrdinalIgnoreCase),
                             "nav-item active", "nav-item")
        Next

        Dim def As ViewDef = Nothing
        If Views.TryGetValue(CurrentView, def) Then Page.Title = "SmartDDR · " & def.Title

        lnkDeepLink.NavigateUrl = BuildDeepLink()
        Dim dashUrl As String = "SmartDDRDashboard.aspx?group=" & HttpUtility.UrlEncode(ddlProjectGroup.SelectedValue) &
                                If(SelectedProject.Length > 0, "&project=" & HttpUtility.UrlEncode(SelectedProject), "")
        lnkDashboard.NavigateUrl = dashUrl
        lnkDashboardTop.NavigateUrl = dashUrl
        Push.Visible = SelectedProject.Length > 0
    End Sub

    ' Runs after every control's own PreRender, so each grid's final rows get <thead>/<tfoot>.
    Private Sub Page_PreRenderComplete(sender As Object, e As EventArgs) Handles Me.PreRenderComplete
        If _suppressRender Then Return
        SetTableSections(MyCommonGrid)
        SetTableSections(grdDrawer)
    End Sub

    Private Function NavButtons() As LinkButton()
        Return {Project_Summary, M75_AFC_ALL, IFR_ALL, PLIP_STATUS, DDR_ACT_ALL,
                CTD, DDR1, ACTIVITY, CTD_DDR, CTD_DOC, DDR_DUMMY, DDR2, DDR_EPR, EPR, M75_AFC, IFR,
                Aconex1, Aconex2, ACON_MISS, PushtoDDR}
    End Function

    Private Shared Sub SetTableSections(gv As GridView)
        If gv.HeaderRow IsNot Nothing Then gv.HeaderRow.TableSection = TableRowSection.TableHeader
        If gv.ShowFooter AndAlso gv.FooterRow IsNot Nothing AndAlso gv.FooterRow.Visible Then
            gv.FooterRow.TableSection = TableRowSection.TableFooter
        End If
    End Sub

#End Region

#Region "Selectors (group / project / discipline)"

    Private Sub SelectGroupFor(project As String)
        If String.IsNullOrEmpty(project) Then Return
        For Each li As ListItem In ddlProjectGroup.Items
            If project.StartsWith(li.Value, StringComparison.OrdinalIgnoreCase) Then
                ddlProjectGroup.ClearSelection()
                li.Selected = True
                Exit For
            End If
        Next
    End Sub

    Private Sub BindProjects(Optional preferred As String = Nothing)
        Dim keep As String = If(preferred, DDLPROJNO.SelectedValue)
        DDLPROJNO.Items.Clear()
        Try
            Dim dt As DataTable = QueryTable(
                "SELECT M.[Project_No], T.[PROJECT_TITLE] AS [Title]
                 FROM (SELECT DISTINCT [Project_No] FROM [CTD_Master] WHERE [Project_No] LIKE @G + '%') M
                 OUTER APPLY (SELECT TOP 1 I.[PROJECT_TITLE] FROM [PROJECT_INFO] I WHERE I.[PROJECT_NO] = M.[Project_No]) T
                 ORDER BY M.[Project_No]",
                P("@G", ddlProjectGroup.SelectedValue))
            For Each r As DataRow In dt.Rows
                Dim no As String = Convert.ToString(r("Project_No")).Trim()
                If no.Length = 0 Then Continue For
                Dim title As String = Convert.ToString(r("Title")).Trim()
                DDLPROJNO.Items.Add(New ListItem(If(title.Length > 0, no & " · " & Truncate(title, 40), no), no))
            Next
        Catch ex As Exception
            ShowMessage("Could not load the project list: " & ex.Message, "error")
        End Try
        SelectValue(DDLPROJNO, keep)
    End Sub

    Private Sub BindDisciplines(Optional preferred As String = Nothing)
        Dim keep As String = If(preferred, DDLDISCIPLINE.SelectedValue)
        DDLDISCIPLINE.Items.Clear()
        DDLDISCIPLINE.Items.Add(New ListItem("All", "All"))
        If SelectedProject.Length > 0 Then
            Try
                Dim dt As DataTable = QueryTable(
                    "SELECT [Discipline] FROM (
                         SELECT DISTINCT [Discipline] FROM [CTD_MASTER]
                         WHERE [PROJECT_NO] = @P AND [Discipline] IS NOT NULL AND LTRIM(RTRIM([Discipline])) <> ''
                     ) t
                     ORDER BY " & DiscOrderSql & ", [Discipline]",
                    P("@P", SelectedProject))
                For Each r As DataRow In dt.Rows
                    Dim d As String = Convert.ToString(r(0))
                    If DDLDISCIPLINE.Items.FindByValue(d) Is Nothing Then DDLDISCIPLINE.Items.Add(New ListItem(d, d))
                Next
            Catch ex As Exception
                ShowMessage("Could not load disciplines: " & ex.Message, "error")
            End Try
        End If
        SelectValue(DDLDISCIPLINE, keep, "All")
    End Sub

    Private Shared Sub SelectValue(ddl As DropDownList, value As String, Optional fallback As String = Nothing)
        Dim li As ListItem = If(String.IsNullOrEmpty(value), Nothing, ddl.Items.FindByValue(value))
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
            Trace.Warn("SmartDDR", "Project info lookup failed", ex)
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
            ExecNonQuery(sql, P("@U", _userName), P("@P", SelectedProject), P("@D", DDLDISCIPLINE.SelectedValue))
        Catch ex As Exception
            Trace.Warn("SmartDDR", "Saving the user preference failed", ex)
        End Try
    End Sub

    Private Sub ddlProjectGroup_SelectedIndexChanged(sender As Object, e As EventArgs) Handles ddlProjectGroup.SelectedIndexChanged
        GroupProjectFilter = ""
        BindProjects()
        BindDisciplines()
        UpdateProjectInfo()
        SaveUserPreference()
        LoadView(If(CurrentView.Length > 0, CurrentView, DefaultView))
    End Sub

    Private Sub DDLPROJNO_SelectedIndexChanged(sender As Object, e As EventArgs) Handles DDLPROJNO.SelectedIndexChanged
        BindDisciplines()
        UpdateProjectInfo()
        SaveUserPreference()
        If IsGroupView(CurrentView) Then
            ' Portfolio report: narrow the rows to the chosen project instead of leaving the report.
            GroupProjectFilter = SelectedProject
            ApplyFilters()
        Else
            ' Project report: show the same report for the new project.
            LoadView(If(CurrentView.Length > 0, CurrentView, DefaultView))
        End If
    End Sub

    Private Sub DDLDISCIPLINE_SelectedIndexChanged(sender As Object, e As EventArgs) Handles DDLDISCIPLINE.SelectedIndexChanged
        SaveUserPreference()
        ApplyFilters()
    End Sub

    Private Sub btnSearch_Click(sender As Object, e As EventArgs) Handles btnSearch.Click
        ApplyFilters()
    End Sub

    Private Sub btnClearFilter_Click(sender As Object, e As EventArgs) Handles btnClearFilter.Click
        txtSearch.Text = ""
        GroupProjectFilter = ""
        ApplyFilters()
    End Sub

#End Region

#Region "Loading and filtering reports"

    Private Sub Nav_Command(sender As Object, e As CommandEventArgs) Handles _
        Project_Summary.Command, M75_AFC_ALL.Command, IFR_ALL.Command, PLIP_STATUS.Command, DDR_ACT_ALL.Command,
        CTD.Command, DDR1.Command, ACTIVITY.Command, CTD_DDR.Command, CTD_DOC.Command,
        DDR_DUMMY.Command, DDR2.Command, DDR_EPR.Command, EPR.Command, M75_AFC.Command, IFR.Command,
        Aconex1.Command, Aconex2.Command, ACON_MISS.Command, PushtoDDR.Command

        LoadView(Convert.ToString(e.CommandArgument))
    End Sub

    Private Sub LoadView(requested As String)
        Dim key As String = NormalizeViewKey(requested)
        If key Is Nothing Then Return
        Dim def As ViewDef = Views(key)

        If Not def.IsGroup AndAlso SelectedProject.Length = 0 Then
            ShowMessage("Select a project first.", "warn")
            GridData = Nothing
            _fetchFailed = True
            lblRowCount.Text = ""
            Return
        End If

        If Not key.Equals(CurrentView, StringComparison.OrdinalIgnoreCase) Then
            txtSearch.Text = ""
            GroupProjectFilter = ""
        End If
        CurrentView = key
        lblSection.Text = HttpUtility.HtmlEncode(def.Section)
        lblContext.Text = HttpUtility.HtmlEncode(def.Title)
        ViewState("FileName") = BuildFileName(def)

        _fetchFailed = False
        Try
            GridData = FetchData(key)
        Catch ex As Exception
            GridData = Nothing
            _fetchFailed = True
            ShowMessage(String.Format("Could not load ""{0}"": {1}", def.Title, ex.Message), "error")
        End Try
        ApplyFilters()
    End Sub

    Private Function FetchData(key As String) As DataTable
        Dim proj As SqlParameter = P("@P", SelectedProject)
        Dim grp As SqlParameter = P("@G", ddlProjectGroup.SelectedValue)

        Select Case key
            ' ---------------- Portfolio (project group) ----------------
            Case "G_INFO"
                Return QueryTable(
                    "SELECT [Project_No] AS [Project], [PROJECT_TITLE] AS [Project Title], [Project_Engineer],
                            [Estimated Hours], [Earned Hours], [Progress %] AS [Progress%],
                            [Planned_AFC] AS [AFC PLAN], [AFC_AFX_Actual] AS [AFC/AFX], [ADH_Actual] AS [ADH],
                            [Balance], [Actual%] AS [AFC%]
                     FROM [ACAD_DATA].[dbo].[SDDR_PROJECT_SUMMARY]
                     WHERE [Estimated Hours] IS NOT NULL AND [Project_No] LIKE @G + '%'
                     ORDER BY [Project_No]", grp)

            Case "G_M75"
                Return QueryTable(
                    "SELECT [Project_No], " & M75Columns() & "
                     FROM [ACAD_DATA].[dbo].[SDDR_ACON_AFC_STATUS_01]
                     WHERE [Project_No] LIKE @G + '%'
                     ORDER BY [Project_No], " & DiscOrderSql, grp)

            Case "G_AFC_IFR"
                Return QueryTable(
                    "SELECT [Project_No], [Discipline], [AFC Plan], [AFC Actual], [AFC Balance],
                            [IFR Plan], [IFR Actual], [IFR Balance]
                     FROM [ACAD_DATA].[dbo].[SDDR_AFC_IFR_STATUS]
                     WHERE [Project_No] LIKE @G + '%'
                     ORDER BY [Project_No], " & DiscOrderSql, grp)

            Case "G_PLIP"
                Return QueryProc("dbo.DDRStatusMatrix",
                                 P("@Project_No", ddlProjectGroup.SelectedValue.Trim()),
                                 P("@Discipline", ""))

            Case "G_DDR_ACT"
                Return QueryTable(
                    "SELECT " & DdrActColumns() & " FROM [SDDR_ACON_DDR_EPR]
                     WHERE [Project_No] LIKE @G + '%' AND [Document_No] IS NOT NULL
                     ORDER BY [Project_No], [Discipline], [Document_No]", grp)

            ' ---------------- Project ----------------
            Case "CTD"
                Return QueryTable(
                    "SELECT [CTD_ID], [Discipline], [Del_Item_Ref], [Deliverable],
                            [CTD Hrs], [DDR Hrs], [CTD Qty], [DDR Qty]
                     FROM [ACAD_DATA].[dbo].[SDDR_CTD_VIEW]
                     WHERE [Project_No] = @P
                     ORDER BY " & DiscOrderSql & ", [Del_Item_Ref]", proj)

            Case "DDR_ACT"
                Return QueryTable(
                    "SELECT " & DdrActColumns() & " FROM [SDDR_ACON_DDR_EPR]
                     WHERE [Project_No] = @P AND [Document_No] IS NOT NULL
                     ORDER BY [Discipline], [Document_No]", proj)

            Case "ACT"
                Return QueryTable(
                    "SELECT [DDR_ID], [Discipline], [Document_No] AS [Category], [Document_Title] AS [Activity],
                            [Man_Hours] AS [Estd Hours], [ACTUAL] AS [Used %]
                     FROM [ACAD_DATA].[dbo].[CTD_DDR_ACTIVITY_VIEW]
                     WHERE [Project_No] = @P
                     ORDER BY [Discipline], [Document_No]", proj)

            Case "CTD_DDR"
                Return QueryTable(
                    "SELECT * FROM [CTD_DDR_SUMMARY] WHERE [Project_No] = @P
                     ORDER BY TRY_CAST(REPLACE(LEFT([Discipline], 2), '.', '') AS INT), [Discipline]", proj)

            Case "CTD_DOC"
                Return QueryTable(
                    "SELECT * FROM [CTD_DDR_MATCH] WHERE [Project_No] = @P
                     ORDER BY TRY_CAST(REPLACE(LEFT([Discipline], 2), '.', '') AS INT), [Discipline]", proj)

            Case "DUMMY"
                Return QueryTable(
                    "SELECT * FROM [DDR_DISC_PLANT]
                     WHERE [CTD_ID] IN (SELECT [CTD_ID] FROM [CTD_MASTER] WHERE [PROJECT_NO] = @P)
                       AND ([DOCUMENT_NO] LIKE 'DU[M0-9][0-9][0-9]%' OR [DOCUMENT_NO] LIKE '%-DU[M0-9][0-9][0-9]%'
                            OR [DOCUMENT_NO] LIKE '%XXX%' OR [DOCUMENT_NO] LIKE '%DUMMY%')", proj)

            Case "DDR_PDO"
                Return QueryTable(
                    "SELECT B.DC,
                            A.RAMZ_ID AS [RAMZ ID], A.Document_No AS [Document/Drawing No],
                            A.Curr_Rev AS [Current Revision], A.Doc_Status AS [Document Status],
                            A.PLIP_ID AS [PLIP ID], A.DCAF_NO, A.SOFTWARE, A.CRITICALITY,
                            A.Document_Title AS [Title], A.PRIMAVERA_ID, A.[PLN%], A.[ACT%],
                            A.IDC_PLN, A.IDC_ACT, A.IFR_PLN, A.IFR1_ACT, A.RCC_PLN, A.RCC1_ACT,
                            A.IFR2_PLN, A.IFR2_ACT, A.RCC2_PLN, A.RCC2_ACT, A.APP_PLN, A.APP_ACT,
                            A.AFC_PLN, A.AFC_ACT,
                            CASE WHEN C.Required_for_Final_Handover = '1' THEN 'Yes'
                                 WHEN C.Required_for_Final_Handover = '0' THEN 'No'
                            END AS [Required_for_Final_Handover],
                            C.Required_Handover_Status,
                            A.REMARKS
                     FROM SDDR_ACON_DDR_EPR A
                     LEFT JOIN DISC_CODES B ON A.DISCIPLINE = B.DISCIPLINE
                     LEFT JOIN SPO_PLIP C ON A.PLIP_ID = C.PLIP_ID
                     WHERE A.DOCUMENT_NO IS NOT NULL
                       AND A.DOCUMENT_NO NOT LIKE '%ACTIVI%'
                       AND A.[Project_No] = @P
                     ORDER BY B.DC, A.Document_No", proj)

            Case "DDR_EPR"
                Return QueryTable(
                    "SELECT DDR_ID, Project_No, RAMZ_ID, Discipline, Document_No, PLIP_ID, DCAF_NO, SOFTWARE,
                            CRITICALITY, Document_Title, PRIMAVERA_ID, Hours AS Man_Hours,
                            STATUS_REQD AS HAND_OVER_REQ, IDC_PLN, IDC_ACT, IFR_PLN, IFR1_ACT, RCC_PLN, RCC1_ACT,
                            IFR2_PLN, IFR2_ACT, RCC2_PLN, RCC2_ACT, APP_PLN, APP_ACT, AFC_PLN, AFC_ACT,
                            CURR_STATUS AS STATUS, Hours, START, IDC, IFR, RCC, APP, AFC, TOTAL, [Used Hours]
                     FROM SDDR_ACON_DDR_EPR
                     WHERE [Project_No] = @P
                     ORDER BY Discipline, Document_No", proj)

            Case "EPR"
                Return QueryTable(
                    "SELECT [DDR_ID], [Discipline], [Document_No], [PLIP_ID], [PRIMAVERA_ID], [Hours],
                            [START], [IDC], [IFR], [RCC], [APP], [AFC], [TOTAL], [Used Hours]
                     FROM [SDDR_ACON_DDR_EPR]
                     WHERE [Project_No] = @P
                     ORDER BY [Discipline], [Document_No]", proj)

            Case "M75"
                Return QueryTable(
                    "SELECT " & M75Columns() & "
                     FROM [ACAD_DATA].[dbo].[SDDR_ACON_AFC_STATUS_01]
                     WHERE [Project_No] = @P
                     ORDER BY " & DiscOrderSql, proj)

            Case "AFC_IFR"
                Return QueryTable(
                    "SELECT [Discipline], [AFC Plan], [AFC Actual], [AFC Balance],
                            [IFR Plan], [IFR Actual], [IFR Balance]
                     FROM [ACAD_DATA].[dbo].[SDDR_AFC_IFR_STATUS]
                     WHERE [Project_No] = @P
                     ORDER BY " & DiscOrderSql, proj)

            Case "ACON_ALL"
                Return QueryTable("SELECT * FROM [PDC_ACON_FOR_DDR] WHERE [Project_No] = @P", proj)

            Case "ACON_LATEST"
                Return QueryTable("SELECT * FROM [PDC_ACON_LATEST_DATE] WHERE [Project_No] = @P", proj)

            Case "ACON_MISS"
                Return QueryTable(
                    "SELECT * FROM [PDC_ACON_LATEST_DATE]
                     WHERE [Project_No] = @P
                       AND LEFT([Document_No], 32) NOT IN (
                           SELECT DISTINCT LEFT(F.[Document_No], 32)
                           FROM [SDDR_SPO_PDC_07_DDR_FINAL] F
                           WHERE F.[Project_No] = @P AND F.[Document_No] IS NOT NULL)
                       AND [Discipline] NOT LIKE 'ZV%'
                     ORDER BY [DATE_MODIFIED] DESC", proj)

            Case "DDR_MISSED"
                Return QueryTable(MissedDocsSql(forInsert:=False, singleDoc:=False), proj)

            Case Else
                Return Nothing
        End Select
    End Function

    Private Shared Function DdrActColumns() As String
        Return "[DDR_ID],[Project_No],[Discipline],[Document_No],[RAMZ_ID],[Curr_Rev],[Doc_Status],[PLIP_ID],[DCAF_NO],
                [SOFTWARE],[CRITICALITY],[Document_Title],[PRIMAVERA_ID],[PLN%],[ACT%],[IDC_PLN],[IDC_ACT],[IFR_PLN],
                [IFR1_ACT],[RCC_PLN],[RCC1_ACT],[IFR2_PLN],[IFR2_ACT],[RCC2_PLN],[RCC2_ACT],[APP_PLN],[APP_ACT],
                [AFC_PLN],[AFC_ACT],[STATUS_REQD],[CURR_STATUS],[REMARKS]"
    End Function

    Private Shared Function M75Columns() As String
        Return "[Discipline],
                [AFC_PLN_TOTAL] AS [Planned_AFC],
                [AFCX_COUNT] AS [AFC_AFX_Actual],
                [ADH_COUNT] AS [ADH_Actual],
                [AFC_PLN_TOTAL] - ([AFCX_COUNT] + [ADH_COUNT]) AS [Balance],
                CAST(ROUND(CAST(([AFCX_COUNT] + [ADH_COUNT]) AS DECIMAL(10,2)) / NULLIF([AFC_PLN_TOTAL], 0) * 100, 2) AS DECIMAL(10,2)) AS [Actual%]"
    End Function

    ' Aconex documents that are not in the DDR yet but can be matched to a CTD line.
    Private Shared Function MissedDocsSql(forInsert As Boolean, singleDoc As Boolean) As String
        Dim sb As New StringBuilder()
        sb.Append(
            "SELECT C.CTD_ID, A.RAMZ_ID, LEFT(A.Document_No, 32) AS DOCUMENT_NO, C.PLIP_ID, C.SOFTWARE,
                    A.TITLE AS DOCUMENT_TITLE, C.CRITICALITY, C.HO_STATUS, 0 AS MAN_HOURS
             FROM [ACAD_DATA].[dbo].[PDC_ACON_LATEST_DATE] A
             OUTER APPLY (
                 SELECT TOP 1 D.CTD_ID, D.PLIP_ID, D.HO_STATUS, D.SOFTWARE, D.CRITICALITY
                 FROM [ACAD_DATA].[dbo].[SDDR_CTD_DDR_001] D
                 WHERE D.DOCUMENT_NO LIKE '%' + RIGHT(LEFT(A.Document_No, 21), 7) + '%'
                   AND D.Project_No = @P
                   AND D.HO_STATUS IS NOT NULL
                 GROUP BY D.CTD_ID, D.PLIP_ID, D.HO_STATUS, D.SOFTWARE, D.CRITICALITY
                 ORDER BY COUNT(*) DESC
             ) C
             WHERE A.Project_No = @P
               AND C.CTD_ID IS NOT NULL
               AND A.Document_No IS NOT NULL
               AND LEFT(A.Document_No, 32) NOT IN (
                   SELECT DISTINCT LEFT(F.Document_No, 32)
                   FROM [ACAD_DATA].[dbo].[SDDR_SPO_PDC_07_DDR_FINAL] F
                   WHERE F.Project_No = @P AND F.Document_No IS NOT NULL)
               AND A.DISCIPLINE NOT LIKE 'ZV%'
               AND A.Status NOT LIKE '%Longer%'")
        If singleDoc Then sb.Append(" AND LEFT(A.Document_No, 32) = @DOC")
        If Not forInsert Then
            sb.Append(" ORDER BY A.DATE_MODIFIED DESC")
            Return sb.ToString()
        End If

        ' Insert: never re-insert what is already there (double clicks / re-runs), and
        ' insert each 32-char document number once even if Aconex lists it twice.
        sb.Append(" AND NOT EXISTS (SELECT 1 FROM CTD_DDR_DISC X
                                    WHERE X.CTD_ID = C.CTD_ID AND LEFT(X.DOCUMENT_NO, 32) = LEFT(A.Document_No, 32))")
        Dim inner As String = sb.ToString().Replace(
            "0 AS MAN_HOURS",
            "0 AS MAN_HOURS, ROW_NUMBER() OVER (PARTITION BY LEFT(A.Document_No, 32) ORDER BY A.DATE_MODIFIED DESC) AS RN")
        Return "SELECT CTD_ID, RAMZ_ID, DOCUMENT_NO, PLIP_ID, SOFTWARE, DOCUMENT_TITLE, CRITICALITY, HO_STATUS, MAN_HOURS FROM (" &
               inner & ") Q WHERE Q.RN = 1"
    End Function

    Private Sub ApplyFilters()
        _gridBound = True

        Dim dt As DataTable = EnsureGridData()
        ComputePercentScales(dt)
        _boundView = If(dt Is Nothing, Nothing, BuildFilteredView(dt))

        Dim def As ViewDef = Nothing
        Views.TryGetValue(CurrentView, def)
        MyCommonGrid.ShowFooter = def IsNot Nothing AndAlso def.HasFooter AndAlso
                                  _boundView IsNot Nothing AndAlso _boundView.Count > 0
        MyCommonGrid.DataSource = _boundView
        MyCommonGrid.DataBind()

        Dim total As Integer = If(dt Is Nothing, 0, dt.Rows.Count)
        Dim shown As Integer = If(_boundView Is Nothing, 0, _boundView.Count)
        lblRowCount.Text = If(shown = total,
                              String.Format("{0:N0} rows", shown),
                              String.Format("{0:N0} of {1:N0} rows", shown, total)) & _discFilterNote

        lblFilterChip.Visible = _projectFilterApplied
        lblFilterChip.Text = "Project: " & HttpUtility.HtmlEncode(GroupProjectFilter)
    End Sub

    Private Function EnsureGridData() As DataTable
        Dim dt As DataTable = GridData
        If dt Is Nothing AndAlso Not _fetchFailed AndAlso CurrentView.Length > 0 Then
            ' Session expired or was recycled: reload the current report.
            Try
                dt = FetchData(CurrentView)
                GridData = dt
            Catch ex As Exception
                _fetchFailed = True
                ShowMessage("Could not reload the report: " & ex.Message, "error")
            End Try
        End If
        Return dt
    End Function

    Private Function BuildFilteredView(dt As DataTable) As DataView
        Dim dv As New DataView(dt)
        Dim clauses As New List(Of String)()
        _projectFilterApplied = False
        _discFilterNote = ""

        Dim disc As String = If(DDLDISCIPLINE.SelectedValue, "").Trim()
        Dim discCol As String = FindColumn(dt, "Discipline")
        Dim discExact As String = Nothing
        Dim discLoose As String = Nothing
        If disc.Length > 0 AndAlso Not disc.Equals("All", StringComparison.OrdinalIgnoreCase) Then
            If discCol Is Nothing Then
                _discFilterNote = " · discipline filter n/a for this report"
            Else
                ' Exact match first ("1.Civil" must not pull in "11.Civil"); fall back to
                ' "contains" only when a report spells disciplines differently.
                discExact = String.Format("TRIM(Convert({0}, 'System.String')) = '{1}'", ColRef(discCol), disc.Replace("'", "''"))
                discLoose = String.Format("Convert({0}, 'System.String') LIKE '%{1}%'", ColRef(discCol), EscapeLike(disc))
            End If
        End If

        If IsGroupView(CurrentView) AndAlso GroupProjectFilter.Length > 0 Then
            Dim projCol As String = If(FindColumn(dt, "Project_No"), FindColumn(dt, "Project"))
            If projCol IsNot Nothing Then
                clauses.Add(String.Format("TRIM(Convert({0}, 'System.String')) = '{1}'", ColRef(projCol), GroupProjectFilter.Replace("'", "''")))
                _projectFilterApplied = True
            End If
        End If

        Dim q As String = txtSearch.Text.Trim()
        If q.Length > 0 Then
            Dim ors As List(Of String) = dt.Columns.Cast(Of DataColumn)().
                Where(Function(c) c.DataType IsNot GetType(Byte())).
                Select(Function(c) String.Format("Convert({0}, 'System.String') LIKE '%{1}%'", ColRef(c.ColumnName), EscapeLike(q))).
                ToList()
            If ors.Count > 0 Then clauses.Add("(" & String.Join(" OR ", ors) & ")")
        End If

        Try
            If discExact Is Nothing Then
                dv.RowFilter = String.Join(" AND ", clauses)
            Else
                dv.RowFilter = String.Join(" AND ", clauses.Concat({discExact}))
                If dv.Count = 0 Then dv.RowFilter = String.Join(" AND ", clauses.Concat({discLoose}))
            End If
        Catch ex As Exception
            dv.RowFilter = ""
            ShowMessage("The filter could not be applied: " & ex.Message, "warn")
        End Try
        Return dv
    End Function

    ' Decide per column whether values are fractions (0..1) or already percentages.
    Private Sub ComputePercentScales(dt As DataTable)
        _percentScale.Clear()
        If dt Is Nothing Then Return
        For Each col As DataColumn In dt.Columns
            If Not UsesPercentBar(col) Then Continue For
            If MatchesAny(col.ColumnName, AlreadyPercentCols, False) Then
                _percentScale(col.ColumnName) = 1
                Continue For
            End If
            Dim maxAbs As Double = 0
            Dim n As Double
            For Each r As DataRow In dt.Rows
                If TryGetNumber(r(col), n) Then maxAbs = Math.Max(maxAbs, Math.Abs(n))
            Next
            _percentScale(col.ColumnName) = If(maxAbs <= 1, 100, 1)
        Next
    End Sub

    ' Status names such as AFC / IFR are also used for document COUNTS (e.g. the PLIP
    ' matrix). Only treat them as percentages when they hold fractional weights.
    Private Function UsesPercentBar(col As DataColumn) As Boolean
        If CurrentView = "G_PLIP" Then Return False
        If Not MatchesAny(col.ColumnName, PercentCols, False) Then Return False
        If col.ColumnName.Contains("%") Then Return True
        Return Not IsIntegerType(col.DataType)
    End Function

    Private Shared Function IsIntegerType(t As Type) As Boolean
        Return t Is GetType(Byte) OrElse t Is GetType(Short) OrElse t Is GetType(Integer) OrElse t Is GetType(Long) OrElse
               t Is GetType(SByte) OrElse t Is GetType(UShort) OrElse t Is GetType(UInteger) OrElse t Is GetType(ULong)
    End Function

    Private Function PercentScale(colName As String) As Double
        Dim scale As Double
        Return If(_percentScale.TryGetValue(colName, scale), scale, 1)
    End Function

#End Region

#Region "Grid formatting"

    Private Sub MyCommonGrid_RowDataBound(sender As Object, e As GridViewRowEventArgs) Handles MyCommonGrid.RowDataBound
        If _boundView Is Nothing Then Return
        If e.Row.RowType = DataControlRowType.DataRow Then
            Dim drv As DataRowView = TryCast(e.Row.DataItem, DataRowView)
            If drv IsNot Nothing Then FormatDataRow(e.Row, drv)
        ElseIf e.Row.RowType = DataControlRowType.Footer Then
            FormatFooter(e.Row)
        End If
    End Sub

    Private Sub FormatDataRow(row As GridViewRow, drv As DataRowView)
        Dim dt As DataTable = drv.DataView.Table
        Dim n As Integer = Math.Min(dt.Columns.Count, row.Cells.Count)
        Dim isDone As Boolean = False
        Dim isPartial As Boolean = False
        Dim isDeleted As Boolean = False
        Dim isDuplicate As Boolean = False
        Dim number As Double

        For i As Integer = 0 To n - 1
            Dim colName As String = dt.Columns(i).ColumnName
            Dim value As Object = drv(i)
            Dim hasValue As Boolean = value IsNot Nothing AndAlso Not Convert.IsDBNull(value)
            Dim cell As TableCell = row.Cells(i)

            If MatchesAny(colName, CenterCols, True) Then AddClass(cell, "c")

            If UsesPercentBar(dt.Columns(i)) Then
                If hasValue AndAlso TryGetNumber(value, number) Then
                    Dim pct As Double = Math.Round(number * PercentScale(colName), 2)
                    cell.Text = ProgressBarHtml(pct)
                    AddClass(cell, "c")
                    If Math.Abs(pct - 100) < 0.005 Then isDone = True
                End If
            ElseIf MatchesAny(colName, RoundCols, False) Then
                If hasValue AndAlso TryGetNumber(value, number) Then cell.Text = number.ToString("F2", CultureInfo.CurrentCulture)
            End If

            Dim dateValue As DateTime
            If TryGetDate(value, colName, dateValue) Then
                cell.Text = FormatDate(dateValue)
                AddClass(cell, "c")
                If MatchesAny(colName, DoneDateCols, False) Then isDone = True
            ElseIf TypeOf value Is DateTime AndAlso IsNoDate(DirectCast(value, DateTime)) Then
                cell.Text = "&nbsp;"
            End If

            If MatchesAny(colName, CheckCols, False) Then
                Dim text As String = If(hasValue, Convert.ToString(value).Trim(), "")
                If text.Length < 2 OrElse DummySerialRx.IsMatch(text) Then AddClass(cell, "cell-warn")
            End If

            If hasValue AndAlso colName.Equals("CTD_ID", StringComparison.OrdinalIgnoreCase) Then
                cell.Text = String.Format("<a class=""cell-link"" href=""DDR_DISC_ENTRY.aspx?CTDID={0}"" title=""Open CTD entry"">{1}</a>",
                                          HttpUtility.UrlEncode(Convert.ToString(value).Trim()), cell.Text)
            End If

            If colName.Equals("IS_DUPLICATE", StringComparison.OrdinalIgnoreCase) AndAlso IsTruthy(value) Then isDuplicate = True
            If hasValue AndAlso TypeOf value Is String AndAlso
               DirectCast(value, String).IndexOf("DELETED", StringComparison.OrdinalIgnoreCase) >= 0 Then isDeleted = True
        Next

        ' ---- Report-specific rules ----
        Select Case CurrentView
            Case "ACT"
                Dim used As Double
                If TryGetNumber(GetValue(drv, "Used %"), used) Then
                    If Math.Abs(used - 100) < 0.005 Then
                        isDone = True
                    ElseIf used > 0 Then
                        isPartial = True
                    End If
                End If
                Dim idIdx As Integer = ColIndex(dt, "DDR_ID")
                If idIdx >= 0 AndAlso idIdx < n Then
                    row.Cells(idIdx).Text = String.Format("<a class=""cell-link"" href=""Activity_DDR_Manager.aspx?Project_No={0}"" title=""Open activity manager"">{1}</a>",
                                                          HttpUtility.UrlEncode(SelectedProject), row.Cells(idIdx).Text)
                End If

            Case "G_PLIP"
                For c As Integer = 3 To n - 1
                    AddClass(row.Cells(c), "c")
                    If TryGetNumber(drv(c), number) AndAlso number > 0 Then AddClass(row.Cells(c), "cell-hl")
                Next

            Case "CTD"
                If ColIndex(dt, "CTD Hrs") >= 0 AndAlso ColIndex(dt, "DDR Hrs") >= 0 Then
                    Dim ctdHrs, ddrHrs As Double
                    TryGetNumber(GetValue(drv, "CTD Hrs"), ctdHrs)
                    TryGetNumber(GetValue(drv, "DDR Hrs"), ddrHrs)
                    If Math.Abs(ctdHrs - ddrHrs) < 0.005 Then isDone = True
                End If

            Case "CTD_DDR"
                Dim a As Integer = ColOrIndex(dt, CtdHoursNames, 2)
                Dim b As Integer = ColOrIndex(dt, DdrHoursNames, 3)
                If a >= 0 AndAlso b >= 0 Then
                    Dim ctdHrs, ddrHrs As Double
                    TryGetNumber(drv(a), ctdHrs)
                    TryGetNumber(drv(b), ddrHrs)
                    If Math.Abs(ctdHrs - ddrHrs) < 0.005 Then isDone = True
                End If

            Case "M75", "G_M75"
                Dim project As String = If(ColIndex(dt, "Project_No") >= 0, Convert.ToString(GetValue(drv, "Project_No")), SelectedProject)
                Dim discipline As String = Convert.ToString(GetValue(drv, "Discipline"))
                For m As Integer = 0 To M75DrillCols.GetUpperBound(0)
                    Dim idx As Integer = ColIndex(dt, M75DrillCols(m, 0))
                    If idx >= 0 AndAlso idx < n AndAlso TryGetNumber(drv(idx), number) AndAlso number <> 0 Then
                        row.Cells(idx).Text = DrillLinkHtml(row.Cells(idx).Text, project, discipline, M75DrillCols(m, 1))
                    End If
                Next

            Case "DDR_MISSED"
                Dim docIdx As Integer = ColIndex(dt, "DOCUMENT_NO")
                Dim doc As String = Convert.ToString(GetValue(drv, "DOCUMENT_NO")).Trim()
                If docIdx >= 0 AndAlso docIdx < n AndAlso doc.Length > 0 Then
                    row.Cells(docIdx).Text &= "<button type=""button"" class=""row-btn js-push"" data-arg=""" &
                                              HttpUtility.HtmlAttributeEncode(doc) & """ title=""Insert this document into the DDR"">⇪ Push</button>"
                End If
        End Select

        Dim classes As New List(Of String)()
        If isDone Then classes.Add("row-done")
        If isPartial AndAlso Not isDone Then classes.Add("row-partial")
        If isDeleted Then classes.Add("row-deleted")
        If isDuplicate Then classes.Add("row-dup")
        If classes.Count > 0 Then row.CssClass = String.Join(" ", classes)
    End Sub

    Private Sub FormatFooter(row As GridViewRow)
        Dim dt As DataTable = _boundView.Table

        Select Case CurrentView
            Case "CTD"
                Dim ctdHrs As Double = SumOf("CTD Hrs")
                Dim ddrHrs As Double = SumOf("DDR Hrs")
                SetFooter(row, "CTD Hrs", Fmt2(ctdHrs))
                SetFooter(row, "DDR Hrs", Fmt2(ddrHrs))
                SetFooter(row, "CTD Qty", Fmt0(SumOf("CTD Qty")))
                SetFooter(row, "DDR Qty", Fmt0(SumOf("DDR Qty")))
                SetFooter(row, "Discipline", ProgressBarHtml(PercentOf(ddrHrs, ctdHrs)))

            Case "CTD_DDR"
                Dim a As Integer = ColOrIndex(dt, CtdHoursNames, 2)
                Dim b As Integer = ColOrIndex(dt, DdrHoursNames, 3)
                If a >= 0 AndAlso b >= 0 Then
                    Dim ctdHrs As Double = SumAt(a)
                    Dim ddrHrs As Double = SumAt(b)
                    SetFooterAt(row, a, Fmt2(ctdHrs))
                    SetFooterAt(row, b, Fmt2(ddrHrs))
                    SetFooterAt(row, Math.Max(a, b) + 1, ProgressBarHtml(PercentOf(ddrHrs, ctdHrs)))
                End If

            Case "DDR_EPR", "EPR"
                Dim estd As Double = SumOf("Hours")
                Dim earned As Double = SumOf("Used Hours")
                Dim pct As Double = PercentOf(earned, estd)
                SetFooter(row, "Hours", Fmt2(estd))
                SetFooter(row, "Man_Hours", Fmt2(SumOf("Man_Hours")))
                SetFooter(row, "Used Hours", Fmt2(earned))
                SetFooter(row, "TOTAL", ProgressBarHtml(pct))
                ' Wide table: repeat the headline figures near the left edge.
                SetFooter(row, "Discipline", "Estd " & Fmt2(estd) & " h · Earned " & Fmt2(earned) & " h")
                SetFooter(row, "Document_No", ProgressBarHtml(pct))

            Case "M75", "G_M75"
                Dim planned As Double = SumOf("Planned_AFC")
                Dim afcx As Double = SumOf("AFC_AFX_Actual")
                Dim adh As Double = SumOf("ADH_Actual")
                SetFooter(row, "Planned_AFC", Fmt0(planned))
                SetFooter(row, "AFC_AFX_Actual", Fmt0(afcx))
                SetFooter(row, "ADH_Actual", Fmt0(adh))
                SetFooter(row, "Balance", Fmt0(SumOf("Balance")))
                SetFooter(row, "Actual%", ProgressBarHtml(PercentOf(afcx + adh, planned)))

            Case "AFC_IFR", "G_AFC_IFR"
                For Each c As String In {"AFC Plan", "AFC Actual", "AFC Balance", "IFR Plan", "IFR Actual", "IFR Balance"}
                    SetFooter(row, c, Fmt0(SumOf(c)))
                Next

            Case "G_INFO"
                Dim estd As Double = SumOf("Estimated Hours")
                Dim earned As Double = SumOf("Earned Hours")
                Dim plan As Double = SumOf("AFC PLAN")
                Dim afcx As Double = SumOf("AFC/AFX")
                Dim adh As Double = SumOf("ADH")
                SetFooter(row, "Estimated Hours", Fmt2(estd))
                SetFooter(row, "Earned Hours", Fmt2(earned))
                SetFooter(row, "Progress%", ProgressBarHtml(PercentOf(earned, estd)))
                SetFooter(row, "AFC PLAN", Fmt0(plan))
                SetFooter(row, "AFC/AFX", Fmt0(afcx))
                SetFooter(row, "ADH", Fmt0(adh))
                SetFooter(row, "Balance", Fmt0(SumOf("Balance")))
                SetFooter(row, "AFC%", ProgressBarHtml(PercentOf(afcx + adh, plan)))

            Case "ACT"
                SetFooter(row, "Estd Hours", Fmt2(SumOf("Estd Hours")))
        End Select

        If row.Cells.Count > 0 AndAlso IsBlankCell(row.Cells(0)) Then row.Cells(0).Text = "Total"
    End Sub

    Private Sub SetFooter(row As GridViewRow, colName As String, html As String)
        SetFooterAt(row, ColIndex(_boundView.Table, colName), html)
    End Sub

    Private Shared Sub SetFooterAt(row As GridViewRow, idx As Integer, html As String)
        If idx < 0 OrElse idx >= row.Cells.Count Then Return
        row.Cells(idx).Text = html
        AddClass(row.Cells(idx), "c")
    End Sub

    Private Function SumOf(colName As String) As Double
        Return SumAt(ColIndex(_boundView.Table, colName))
    End Function

    Private Function SumAt(idx As Integer) As Double
        If idx < 0 Then Return 0
        Dim total As Double = 0
        Dim n As Double
        For Each r As DataRowView In _boundView
            If TryGetNumber(r(idx), n) Then total += n
        Next
        Return total
    End Function

    Private Shared Function PercentOf(part As Double, whole As Double) As Double
        Return If(whole > 0, Math.Round(part / whole * 100, 2), 0)
    End Function

    Private Shared Function ProgressBarHtml(pct As Double) As String
        Dim width As Double = Math.Max(0, Math.Min(pct, 100))
        Dim cls As String = If(pct >= 90, "pb-good", If(pct >= 70, "pb-ok", If(pct >= 50, "pb-warn", "pb-bad")))
        Return String.Format(CultureInfo.InvariantCulture,
            "<div class=""pbar""><span class=""pbar-val"">{0:F2}%</span><span class=""pbar-track""><span class=""pbar-fill {1}"" style=""width:{2:0.##}%""></span></span></div>",
            pct, cls, width)
    End Function

    Private Shared Function DrillLinkHtml(cellHtml As String, project As String, discipline As String, metric As String) As String
        Dim arg As String = String.Join("|", project, discipline, metric)
        Return "<a href=""#"" class=""drill js-drill"" data-arg=""" & HttpUtility.HtmlAttributeEncode(arg) &
               """ title=""Show the documents behind this number"">" & cellHtml & "</a>"
    End Function

    Private Sub grdDrawer_RowDataBound(sender As Object, e As GridViewRowEventArgs) Handles grdDrawer.RowDataBound
        If e.Row.RowType <> DataControlRowType.DataRow Then Return
        Dim drv As DataRowView = TryCast(e.Row.DataItem, DataRowView)
        If drv Is Nothing Then Return
        Dim dt As DataTable = drv.DataView.Table
        For i As Integer = 0 To Math.Min(dt.Columns.Count, e.Row.Cells.Count) - 1
            Dim d As DateTime
            If TryGetDate(drv(i), dt.Columns(i).ColumnName, d) Then
                e.Row.Cells(i).Text = FormatDate(d)
                AddClass(e.Row.Cells(i), "c")
            ElseIf TypeOf drv(i) Is DateTime Then
                e.Row.Cells(i).Text = "&nbsp;"
            End If
        Next
    End Sub

#End Region

#Region "M75 drill-down drawer"

    Private Sub btnDrill_Click(sender As Object, e As EventArgs) Handles btnDrill.Click
        Dim parts() As String = hfRowArg.Value.Split("|"c)
        hfRowArg.Value = ""
        If parts.Length < 3 Then Return
        Dim project As String = parts(0)
        Dim metric As String = parts(parts.Length - 1)
        Dim discipline As String = String.Join("|", parts, 1, parts.Length - 2)
        LoadDrawerDetails(project, discipline, metric)
    End Sub

    Private Sub LoadDrawerDetails(project As String, discipline As String, metric As String)
        Dim condition As String
        Dim label As String
        Select Case metric.ToUpperInvariant()
            Case "AFC"
                condition = "[CURR_STATUS] IN ('AFC','AFX')"
                label = "AFC / AFX issued"
            Case "ADH"
                condition = "[CURR_STATUS] = 'ADH'"
                label = "ADH issued"
            Case "BALANCE"
                condition = "[AFC_PLN] IS NOT NULL AND ISNULL([CURR_STATUS], '') NOT IN ('AFC','AFX','ADH')"
                label = "Balance – planned, not yet AFC/AFX/ADH"
            Case Else
                Return
        End Select

        lblDrawerTitle.Text = HttpUtility.HtmlEncode(discipline & " · " & label)
        ViewState("DrawerFile") = project & "_" & discipline & "_" & metric & "_DOCS"

        Try
            Dim dt As DataTable = QueryTable(
                "SELECT [Document_No], [RAMZ_ID], [Document_Title], [Curr_Rev], [STATUS_REQD], [CURR_STATUS], [AFC_PLN], [AFC_ACT]
                 FROM [ACAD_DATA].[dbo].[SDDR_ACON_DDR_EPR]
                 WHERE [Project_No] = @P AND [Discipline] = @D AND " & condition & "
                 ORDER BY [Document_No]",
                P("@P", project), P("@D", discipline))
            DrawerData = dt
            grdDrawer.DataSource = dt
            grdDrawer.DataBind()
            lblDrawerCount.Text = HttpUtility.HtmlEncode(String.Format("Project {0} · {1:N0} document(s)", project, dt.Rows.Count))
        Catch ex As Exception
            DrawerData = Nothing
            lblDrawerCount.Text = HttpUtility.HtmlEncode("Could not load the documents: " & ex.Message)
        End Try

        pnlDetails.Visible = True
    End Sub

    Private Sub btnDrawerCsv_Click(sender As Object, e As EventArgs) Handles btnDrawerCsv.Click
        Dim dt As DataTable = DrawerData
        If dt Is Nothing OrElse dt.Rows.Count = 0 Then
            ShowMessage("There are no documents to export.", "warn")
            Return
        End If
        SendFile(BuildCsv(dt), SafeFileName(If(TryCast(ViewState("DrawerFile"), String), "SmartDDR_DOCS")) & ".csv", "text/csv")
    End Sub

#End Region

#Region "Push missed Aconex documents into the DDR"

    Private Sub Push_Click(sender As Object, e As EventArgs) Handles Push.Click
        If SelectedProject.Length = 0 Then
            ShowMessage("Select a project first.", "warn")
            Return
        End If
        Try
            Dim count As Integer = PushMissedToDdr(Nothing)
            If count = 0 Then
                ShowMessage("Nothing to push: every matched Aconex document is already in the DDR.", "info")
            Else
                ShowMessage(String.Format("{0:N0} document(s) pushed to the DDR for {1}.", count, SelectedProject), "success")
            End If
        Catch ex As Exception
            ShowMessage("Push failed: " & ex.Message, "error")
        End Try
        LoadView("DDR_MISSED")
    End Sub

    Private Sub btnPushRow_Click(sender As Object, e As EventArgs) Handles btnPushRow.Click
        Dim doc As String = hfRowArg.Value.Trim()
        hfRowArg.Value = ""
        If doc.Length = 0 OrElse CurrentView <> "DDR_MISSED" OrElse SelectedProject.Length = 0 Then Return
        Try
            Dim count As Integer = PushMissedToDdr(doc)
            If count = 0 Then
                ShowMessage(doc & " was not pushed (already in the DDR or no longer matched).", "warn")
            Else
                ShowMessage(doc & " pushed to the DDR.", "success")
            End If
        Catch ex As Exception
            ShowMessage("Push failed: " & ex.Message, "error")
        End Try
        LoadView("DDR_MISSED")
    End Sub

    Private Function PushMissedToDdr(documentNo As String) As Integer
        Dim singleDoc As Boolean = Not String.IsNullOrEmpty(documentNo)
        Dim sql As String =
            "INSERT INTO CTD_DDR_DISC (CTD_ID, RAMZ_ID, DOCUMENT_NO, PLIP_ID, SOFTWARE, DOCUMENT_TITLE, CRITICALITY, HO_STATUS, MAN_HOURS) " &
            MissedDocsSql(forInsert:=True, singleDoc:=singleDoc)
        If singleDoc Then
            Return ExecNonQuery(sql, P("@P", SelectedProject), P("@DOC", documentNo))
        End If
        Return ExecNonQuery(sql, P("@P", SelectedProject))
    End Function

#End Region

#Region "Export"

    Private Sub btnExcel_Click(sender As Object, e As EventArgs) Handles btnExcel.Click
        Dim dt As DataTable = GetExportTable()
        If dt Is Nothing OrElse dt.Rows.Count = 0 Then
            ShowMessage("There is no data to export for the current selection.", "warn")
            Return
        End If
        Dim name As String = ExportFileName()
        SendFile(BuildXlsx(dt, name), name & ".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    End Sub

    Private Sub btnCSV_Click(sender As Object, e As EventArgs) Handles btnCSV.Click
        Dim dt As DataTable = GetExportTable()
        If dt Is Nothing OrElse dt.Rows.Count = 0 Then
            ShowMessage("There is no data to export for the current selection.", "warn")
            Return
        End If
        SendFile(BuildCsv(dt), ExportFileName() & ".csv", "text/csv")
    End Sub

    ' Exports honour the discipline / project / search filters that are on screen.
    Private Function GetExportTable() As DataTable
        Dim dt As DataTable = EnsureGridData()
        If dt Is Nothing Then Return Nothing
        Return BuildFilteredView(dt).ToTable()
    End Function

    Private Function ExportFileName() As String
        Dim name As String = If(TryCast(ViewState("FileName"), String), "SmartDDR")
        Dim disc As String = If(DDLDISCIPLINE.SelectedValue, "")
        If disc.Length > 0 AndAlso Not disc.Equals("All", StringComparison.OrdinalIgnoreCase) Then name &= "_" & disc
        If _projectFilterApplied Then name &= "_" & GroupProjectFilter
        Return SafeFileName(name)
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
            Dim d As DateTime = DirectCast(value, DateTime)
            Return If(IsNoDate(d), "", FormatDate(d))
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
            New S.NumberingFormat() With {.NumberFormatId = 164UI, .FormatCode = "dd-mmm-yyyy"},
            New S.NumberingFormat() With {.NumberFormatId = 165UI, .FormatCode = "dd-mmm-yyyy hh:mm"}) With {.Count = 2UI}

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
            New S.CellFormat() With {.NumberFormatId = 164UI, .FontId = 0UI, .FillId = 0UI, .BorderId = 0UI, .ApplyNumberFormat = True},
            New S.CellFormat() With {.NumberFormatId = 165UI, .FontId = 0UI, .FillId = 0UI, .BorderId = 0UI, .ApplyNumberFormat = True}) With {.Count = 4UI}

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
            If IsNoDate(d) Then Return New S.Cell()
            shownLength = 11
            Return New S.Cell() With {
                .CellValue = New S.CellValue(d.ToOADate().ToString(CultureInfo.InvariantCulture)),
                .StyleIndex = If(d.TimeOfDay = TimeSpan.Zero, 2UI, 3UI)}
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
        Dim s As String = Regex.Replace(If(name, ""), "[\[\]\:\*\?/\\']", "_").Trim()
        If s.Length = 0 Then s = "SmartDDR"
        Return If(s.Length > 31, s.Substring(0, 31), s)
    End Function

    Private Shared Function SafeFileName(name As String) As String
        Dim s As String = Regex.Replace(If(name, ""), "[^\w\-\.]+", "_").Trim("_"c)
        Return If(s.Length = 0, "SmartDDR", s)
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

    Private Shared Function QueryProc(procName As String, ParamArray prms As SqlParameter()) As DataTable
        Using con As New SqlConnection(ConnString())
            Using cmd As New SqlCommand(procName, con)
                cmd.CommandType = CommandType.StoredProcedure
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

    Private Function GetUserName() As String
        Dim name As String = ""
        If User IsNot Nothing AndAlso User.Identity IsNot Nothing Then name = If(User.Identity.Name, "")
        Dim slash As Integer = name.LastIndexOf("\"c)
        If slash >= 0 Then name = name.Substring(slash + 1)   ' strip "WORLEY\" (any domain)
        Return name.Trim()
    End Function

    Private Shared Function NormalizeViewKey(key As String) As String
        If String.IsNullOrWhiteSpace(key) Then Return Nothing
        Dim found As String = Views.Keys.FirstOrDefault(Function(k) k.Equals(key.Trim(), StringComparison.OrdinalIgnoreCase))
        Return found
    End Function

    Private Shared Function IsGroupView(key As String) As Boolean
        Dim def As ViewDef = Nothing
        Return Not String.IsNullOrEmpty(key) AndAlso Views.TryGetValue(key, def) AndAlso def.IsGroup
    End Function

    Private Function BuildFileName(def As ViewDef) As String
        Dim prefix As String
        If def.IsGroup Then
            prefix = If(ddlProjectGroup.SelectedItem IsNot Nothing, ddlProjectGroup.SelectedItem.Text, "ALL")
        Else
            prefix = SelectedProject
        End If
        Return prefix & "_" & def.FileSuffix
    End Function

    Private Function BuildDeepLink() As String
        Dim url As New StringBuilder(Request.Url.GetLeftPart(UriPartial.Path))
        url.Append("?view=").Append(HttpUtility.UrlEncode(If(CurrentView.Length > 0, CurrentView, DefaultView)))
        If SelectedProject.Length > 0 Then url.Append("&project=").Append(HttpUtility.UrlEncode(SelectedProject))
        Dim disc As String = If(DDLDISCIPLINE.SelectedValue, "")
        If disc.Length > 0 AndAlso Not disc.Equals("All", StringComparison.OrdinalIgnoreCase) Then
            url.Append("&disc=").Append(HttpUtility.UrlEncode(disc))
        End If
        Return url.ToString()
    End Function

    Private Sub ShowMessage(text As String, Optional kind As String = "info")
        lblMessage.Text = HttpUtility.HtmlEncode(text)
        lblMessage.CssClass = "toast toast-" & kind
        lblMessage.Visible = True
    End Sub

    Private Shared Sub AddClass(ctrl As WebControl, cssClass As String)
        If String.IsNullOrEmpty(ctrl.CssClass) Then
            ctrl.CssClass = cssClass
        ElseIf Not (" " & ctrl.CssClass & " ").Contains(" " & cssClass & " ") Then
            ctrl.CssClass &= " " & cssClass
        End If
    End Sub

    Private Shared Function IsBlankCell(cell As TableCell) As Boolean
        Dim t As String = If(cell.Text, "").Trim()
        Return t.Length = 0 OrElse t = "&nbsp;"
    End Function

    Private Shared Function MatchesAny(name As String, list As String(), contains As Boolean) As Boolean
        For Each x As String In list
            If contains Then
                If name.IndexOf(x, StringComparison.OrdinalIgnoreCase) >= 0 Then Return True
            ElseIf name.Equals(x, StringComparison.OrdinalIgnoreCase) Then
                Return True
            End If
        Next
        Return False
    End Function

    Private Shared Function FindColumn(dt As DataTable, name As String) As String
        Dim idx As Integer = ColIndex(dt, name)
        Return If(idx >= 0, dt.Columns(idx).ColumnName, Nothing)
    End Function

    Private Shared Function ColIndex(dt As DataTable, name As String) As Integer
        For i As Integer = 0 To dt.Columns.Count - 1
            If dt.Columns(i).ColumnName.Equals(name, StringComparison.OrdinalIgnoreCase) Then Return i
        Next
        Return -1
    End Function

    Private Shared Function ColOrIndex(dt As DataTable, names As String(), fallback As Integer) As Integer
        For Each n As String In names
            Dim idx As Integer = ColIndex(dt, n)
            If idx >= 0 Then Return idx
        Next
        Return If(fallback < dt.Columns.Count, fallback, -1)
    End Function

    Private Shared Function GetValue(drv As DataRowView, colName As String) As Object
        Dim idx As Integer = ColIndex(drv.DataView.Table, colName)
        Return If(idx >= 0, drv(idx), Nothing)
    End Function

    ' DataView expression escaping.
    Private Shared Function ColRef(colName As String) As String
        Return "[" & colName.Replace("\", "\\").Replace("]", "\]") & "]"
    End Function

    Private Shared Function EscapeLike(value As String) As String
        Dim sb As New StringBuilder(value.Length + 8)
        For Each ch As Char In value
            Select Case ch
                Case "*"c, "%"c, "["c, "]"c
                    sb.Append("["c).Append(ch).Append("]"c)
                Case "'"c
                    sb.Append("''")
                Case Else
                    sb.Append(ch)
            End Select
        Next
        Return sb.ToString()
    End Function

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
        Dim s As String = TryCast(value, String)
        If s Is Nothing Then Return False
        s = s.Replace("%", "").Trim()
        If s.Length = 0 Then Return False
        Const styles As NumberStyles = NumberStyles.Float Or NumberStyles.AllowThousands
        If Double.TryParse(s, styles, CultureInfo.CurrentCulture, result) Then Return True
        If Double.TryParse(s, styles, CultureInfo.InvariantCulture, result) Then Return True
        result = 0
        Return False
    End Function

    ' SQL Server turns '' into 1900-01-01, so anything that early means "no date".
    Private Shared Function IsNoDate(d As DateTime) As Boolean
        Return d.Year <= 1900
    End Function

    Private Shared Function TryGetDate(value As Object, colName As String, ByRef result As DateTime) As Boolean
        result = DateTime.MinValue
        If value Is Nothing OrElse Convert.IsDBNull(value) Then Return False
        If TypeOf value Is DateTime Then
            result = DirectCast(value, DateTime)
            Return Not IsNoDate(result)
        End If
        If TypeOf value Is DateTimeOffset Then
            result = DirectCast(value, DateTimeOffset).DateTime
            Return Not IsNoDate(result)
        End If
        ' Some views return dates as text: only parse those in date-like columns.
        Dim s As String = TryCast(value, String)
        If s Is Nothing OrElse Not MatchesAny(colName, DateHintCols, True) Then Return False
        s = s.Trim()
        If s.Length < 6 OrElse s.IndexOfAny({"-"c, "/"c, " "c}) < 0 Then Return False
        Return DateTime.TryParse(s, CultureInfo.CurrentCulture, DateTimeStyles.None, result) AndAlso Not IsNoDate(result)
    End Function

    Private Shared Function FormatDate(d As DateTime) As String
        Return d.ToString(If(d.TimeOfDay = TimeSpan.Zero, "dd-MMM-yyyy", "dd-MMM-yyyy HH:mm"), CultureInfo.InvariantCulture)
    End Function

    Private Shared Function IsTruthy(value As Object) As Boolean
        If value Is Nothing OrElse Convert.IsDBNull(value) Then Return False
        If TypeOf value Is Boolean Then Return DirectCast(value, Boolean)
        Dim n As Double
        If TryGetNumber(value, n) Then Return n <> 0
        Dim s As String = Convert.ToString(value).Trim()
        Return s.Equals("true", StringComparison.OrdinalIgnoreCase) OrElse
               s.Equals("yes", StringComparison.OrdinalIgnoreCase) OrElse
               s.Equals("y", StringComparison.OrdinalIgnoreCase)
    End Function

    Private Shared Function Truncate(s As String, maxLength As Integer) As String
        If s Is Nothing OrElse s.Length <= maxLength Then Return s
        Return s.Substring(0, maxLength - 1) & "…"
    End Function

    Private Shared Function Fmt2(d As Double) As String
        Return d.ToString("N2", CultureInfo.CurrentCulture)
    End Function

    Private Shared Function Fmt0(d As Double) As String
        Return d.ToString("N0", CultureInfo.CurrentCulture)
    End Function

#End Region

End Class

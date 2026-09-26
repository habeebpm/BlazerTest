Imports System.Collections.Generic
Imports System.Configuration
Imports System.Data
Imports System.Data.SqlClient
Imports System.Globalization
Imports System.IO
Imports System.Text.RegularExpressions
Imports System.Web
Imports System.Web.Script.Serialization
Imports System.Web.UI

' SmartDDR Dashboard - DDR + EPR and M75 AFC analytics.
'
' The page itself is a static shell; all analysis runs in the browser on data fetched
' from this code-behind as JSON.
'
'   GET  ?api=projects&group=3
'   GET  ?api=data&project=3017            (or &group=3 for every project in the group)
'   GET  ?api=blrows&project=3017&name=BL1 (rows of one baseline)
'   GET  ?api=aconex&project=3017&doc=...  (Aconex record + link for one document)
'   GET  ?api=views                        (saved views: own + shared)
'   POST ?api=saveview | deleteview | snapshot | baseline      (header X-SDDR: 1)
'
' History / baseline / saved-view features need SmartDDRDashboard_Setup.sql to have been run;
' until then the page works without them and says so.
Public Class SmartDDRDashboard
    Inherits System.Web.UI.Page

    Private Const MaxGroupRows As Integer = 150000
    Private Const AconexUrlSetting As String = "SmartDDR.AconexDocUrl"

    ' Group = project-number prefix, project = project number. Letters/digits only (plus - _ . / space for
    ' projects) so that SQL LIKE wildcards (% [ ]) can never widen a query to every project.
    Private Shared ReadOnly GroupPattern As New Regex("^[0-9A-Za-z]{1,10}$", RegexOptions.Compiled)
    Private Shared ReadOnly ProjectPattern As New Regex("^[0-9A-Za-z][0-9A-Za-z_\-\./ ]{0,39}$", RegexOptions.Compiled)
    Private Shared ReadOnly NamePattern As New Regex("^[^<>""'\\]{1,60}$", RegexOptions.Compiled)

    Private _isApi As Boolean
    Private _user As String = ""
    Private _admin As Boolean

    Protected Sub Page_Load(sender As Object, e As EventArgs) Handles Me.Load
        Dim api As String = If(Request.QueryString("api"), "").Trim().ToLowerInvariant()
        If api.Length = 0 Then Return   ' normal page render
        _isApi = True
        _user = CurrentUser()
        _admin = IsAdminUser(_user)

        Try
            Select Case api
                Case "projects"
                    WriteJson(200, GetProjects(ValidGroup(Request.QueryString("group"), required:=True)))
                Case "data"
                    Dim project As String = ValidProject(Request.QueryString("project"))
                    Dim group As String = ValidGroup(Request.QueryString("group"), required:=False)
                    If project.Length = 0 AndAlso group.Length = 0 Then Throw New ArgumentException("Pass a project or a group.")
                    WriteData(project, group)
                Case "blrows"
                    WriteJson(200, GetBaselineRows(ScopeFromQuery(), ValidName(Request.QueryString("name"))))
                Case "aconex"
                    WriteJson(200, GetAconex(ValidProject(Request.QueryString("project")), If(Request.QueryString("doc"), "").Trim()))
                Case "views"
                    WriteJson(200, GetViews())
                Case "saveview"
                    RequirePost()
                    WriteJson(200, SaveView())
                Case "deleteview"
                    RequirePost()
                    WriteJson(200, DeleteView())
                Case "snapshot"
                    RequirePost() : RequireAdmin()
                    Dim project As String = ValidProject(Request.QueryString("project"))
                    ExecProc("dbo.usp_SDDR_Dash_Snapshot", P("@Project", If(project.Length > 0, project, Nothing)))
                    WriteJson(200, New Dictionary(Of String, Object) From {{"ok", True}})
                Case "baseline"
                    RequirePost() : RequireAdmin()
                    Dim project As String = ValidProject(Request.QueryString("project"))
                    If project.Length = 0 Then Throw New ArgumentException("Select a single project to capture a baseline.")
                    Dim name As String = ValidName(FormValue("name"))
                    ExecProc("dbo.usp_SDDR_Dash_Baseline", P("@Project", project), P("@Name", name), P("@User", _user))
                    WriteJson(200, New Dictionary(Of String, Object) From {{"ok", True}})
                Case Else
                    Throw New ArgumentException("Unknown api '" & api & "'.")
            End Select
        Catch ex As ArgumentException
            WriteJson(400, ErrorPayload(ex.Message))
        Catch ex As UnauthorizedAccessException
            WriteJson(403, ErrorPayload(ex.Message))
        Catch ex As SqlException When ex.Number = 208 OrElse ex.Number = 2812
            ' 208 = invalid object name, 2812 = stored procedure not found: setup script not run yet.
            WriteJson(501, ErrorPayload("This feature needs the SmartDDR dashboard database objects (run SmartDDRDashboard_Setup.sql)."))
        Catch ex As Exception
            ' Don't leak SQL / server details to the browser; keep them in the trace.
            Trace.Warn("SmartDDRDashboard", "API '" & api & "' failed", ex)
            WriteJson(500, ErrorPayload("The request could not be completed. Please try again or contact the SmartDDR administrator."))
        End Try
    End Sub

    ' API calls return JSON only: skip rendering the HTML shell.
    Protected Overrides Sub Render(writer As HtmlTextWriter)
        If Not _isApi Then MyBase.Render(writer)
    End Sub

    ' ------------------------------------------------------------------ identity / guards

    Private Function CurrentUser() As String
        Dim name As String = ""
        If User IsNot Nothing AndAlso User.Identity IsNot Nothing Then name = If(User.Identity.Name, "")
        Dim slash As Integer = name.LastIndexOf("\"c)
        If slash >= 0 Then name = name.Substring(slash + 1)
        Return name.Trim()
    End Function

    Private Shared Function IsAdminUser(userName As String) As Boolean
        If userName.Length = 0 Then Return False
        Try
            Return CBool(ST_Common.IsAdmin(userName))
        Catch
            Return False
        End Try
    End Function

    ' Mutating calls must be POSTs carrying a custom header; a cross-site form cannot add one (CSRF guard).
    Private Sub RequirePost()
        If Not String.Equals(Request.HttpMethod, "POST", StringComparison.OrdinalIgnoreCase) OrElse Request.Headers("X-SDDR") <> "1" Then
            Throw New ArgumentException("This action must be sent as a POST from the dashboard.")
        End If
    End Sub

    Private Sub RequireAdmin()
        If Not _admin Then Throw New UnauthorizedAccessException("Only SmartDDR administrators can do this.")
    End Sub

    Private Function FormValue(key As String) As String
        ' Unvalidated: saved-view JSON legitimately contains characters request validation rejects.
        Return If(Request.Unvalidated.Form(key), "")
    End Function

    ' ------------------------------------------------------------------ validation

    Private Shared Function ValidGroup(value As String, required As Boolean) As String
        Dim v As String = If(value, "").Trim()
        If v.Length = 0 Then
            If required Then Throw New ArgumentException("A project group is required.")
            Return ""
        End If
        If Not GroupPattern.IsMatch(v) Then Throw New ArgumentException("Invalid project group.")
        Return v
    End Function

    Private Shared Function ValidProject(value As String) As String
        Dim v As String = If(value, "").Trim()
        If v.Length > 0 AndAlso Not ProjectPattern.IsMatch(v) Then Throw New ArgumentException("Invalid project number.")
        Return v
    End Function

    Private Shared Function ValidName(value As String) As String
        Dim v As String = If(value, "").Trim()
        If Not NamePattern.IsMatch(v) Then Throw New ArgumentException("Enter a name of up to 60 characters (no < > "" ' \).")
        Return v
    End Function

    Private Structure Scope
        Public ByProject As Boolean
        Public Value As String
        Public ReadOnly Property Sql As String
            Get
                Return If(ByProject, "[Project_No] = @S", "[Project_No] LIKE @S + '%'")
            End Get
        End Property
    End Structure

    Private Function ScopeFromQuery() As Scope
        Dim project As String = ValidProject(Request.QueryString("project"))
        Dim group As String = ValidGroup(Request.QueryString("group"), required:=False)
        If project.Length = 0 AndAlso group.Length = 0 Then Throw New ArgumentException("Pass a project or a group.")
        Return New Scope With {.ByProject = project.Length > 0, .Value = If(project.Length > 0, project, group)}
    End Function

    ' ------------------------------------------------------------------ projects

    Private Shared Function GetProjects(group As String) As Object
        Dim list As New List(Of Object)()
        Dim dt As DataTable = QueryTable(
            "SELECT M.[Project_No], T.[PROJECT_TITLE] AS [Title]
             FROM (SELECT DISTINCT [Project_No] FROM [CTD_Master] WHERE [Project_No] LIKE @G + '%') M
             OUTER APPLY (SELECT TOP 1 I.[PROJECT_TITLE] FROM [PROJECT_INFO] I WHERE I.[PROJECT_NO] = M.[Project_No]) T
             ORDER BY M.[Project_No]",
            P("@G", group))
        For Each r As DataRow In dt.Rows
            Dim no As String = Convert.ToString(r(0)).Trim()
            If no.Length > 0 Then
                list.Add(New Dictionary(Of String, Object) From {{"no", no}, {"title", Convert.ToString(r(1)).Trim()}})
            End If
        Next
        Return list
    End Function

    ' ------------------------------------------------------------------ data

    Private Shared ReadOnly DdrColumns As String() = {
        "DDR_ID", "Project_No", "Discipline", "Document_No", "RAMZ_ID", "PLIP_ID", "DCAF_NO", "SOFTWARE",
        "CRITICALITY", "Document_Title", "PRIMAVERA_ID", "Curr_Rev", "Doc_Status", "STATUS_REQD", "CURR_STATUS",
        "REMARKS", "Hours", "Used Hours", "PLN%", "ACT%", "START", "IDC", "IFR", "RCC", "APP", "AFC", "TOTAL",
        "IDC_PLN", "IDC_ACT", "IFR_PLN", "IFR1_ACT", "RCC_PLN", "RCC1_ACT", "IFR2_PLN", "IFR2_ACT",
        "RCC2_PLN", "RCC2_ACT", "APP_PLN", "APP_ACT", "AFC_PLN", "AFC_ACT"}

    Private Const BaselineCols As String = "[DDR_ID], [Document_No], [Project_No], [IDC_PLN], [IFR_PLN], [RCC_PLN], [IFR2_PLN], [RCC2_PLN], [APP_PLN], [AFC_PLN]"

    ' Queries first (so any SQL error still becomes a clean JSON error), then streams the rows one at
    ' a time - a whole group can be 100k+ rows and must not be materialised as one giant string.
    Private Sub WriteData(project As String, group As String)
        Dim sc As New Scope With {.ByProject = project.Length > 0, .Value = If(project.Length > 0, project, group)}

        Dim colList As New List(Of String)()
        For Each c As String In DdrColumns
            colList.Add("[" & c & "]")
        Next

        Dim ddr As DataTable = QueryTable(
            "SELECT TOP (" & MaxGroupRows.ToString(CultureInfo.InvariantCulture) & ") " & String.Join(", ", colList) & "
             FROM [SDDR_ACON_DDR_EPR]
             WHERE " & sc.Sql & " AND [Document_No] IS NOT NULL
             ORDER BY [Project_No], [Discipline], [Document_No]", P("@S", sc.Value))

        Dim m75 As DataTable = QueryTable(
            "SELECT [Project_No], [Discipline], [AFC_PLN_TOTAL], [AFCX_COUNT], [ADH_COUNT]
             FROM [ACAD_DATA].[dbo].[SDDR_ACON_AFC_STATUS_01]
             WHERE " & sc.Sql, P("@S", sc.Value))

        ' History + baseline (optional - the setup script may not have been run yet).
        Dim historyOk As Boolean = True
        Dim snaps As DataTable = Nothing, baselines As DataTable = Nothing, blRows As DataTable = Nothing
        Dim blName As String = ""
        Try
            snaps = QueryTable(
                "SELECT [SnapDate], [Discipline],
                        SUM([Docs]) AS [Docs], SUM([Complete]) AS [Complete], SUM([Pending]) AS [Pending],
                        SUM([Overdue]) AS [Overdue], SUM([Due14]) AS [Due14], SUM([NotStarted]) AS [NotStarted],
                        SUM([EstHours]) AS [EstHours], SUM([EarnedHours]) AS [EarnedHours], SUM([PlannedHours]) AS [PlannedHours],
                        SUM([M75Plan]) AS [M75Plan], SUM([M75Done]) AS [M75Done]
                 FROM [dbo].[SDDR_DASH_SNAPSHOT]
                 WHERE " & sc.Sql & " AND [SnapDate] >= DATEADD(DAY, -730, CAST(GETDATE() AS date))
                 GROUP BY [SnapDate], [Discipline]
                 ORDER BY [SnapDate], [Discipline]", P("@S", sc.Value))
            baselines = QueryTable(
                "SELECT [BaselineName], MAX([CapturedAt]) AS [CapturedAt], MAX([CapturedBy]) AS [CapturedBy], COUNT(*) AS [Docs]
                 FROM [dbo].[SDDR_DASH_BASELINE]
                 WHERE " & sc.Sql & "
                 GROUP BY [BaselineName]
                 ORDER BY MAX([CapturedAt]) DESC", P("@S", sc.Value))
            If baselines.Rows.Count > 0 Then
                blName = Convert.ToString(baselines.Rows(0)("BaselineName"))
                blRows = QueryTable("SELECT " & BaselineCols & " FROM [dbo].[SDDR_DASH_BASELINE] WHERE " & sc.Sql & " AND [BaselineName] = @B",
                                    P("@S", sc.Value), P("@B", blName))
            End If
        Catch ex As SqlException When ex.Number = 208
            historyOk = False
        End Try

        Dim ser As JavaScriptSerializer = NewSerializer()
        BeginJson(200)
        Dim w As TextWriter = Response.Output
        w.Write("{""scope"":") : w.Write(ser.Serialize(If(sc.ByProject, "project", "group")))
        w.Write(",""key"":") : w.Write(ser.Serialize(sc.Value))
        w.Write(",""user"":") : w.Write(ser.Serialize(_user))
        w.Write(",""admin"":") : w.Write(If(_admin, "true", "false"))
        w.Write(",""aconexLink"":") : w.Write(If(String.IsNullOrWhiteSpace(ConfigurationManager.AppSettings(AconexUrlSetting)), "false", "true"))
        w.Write(",""generated"":") : w.Write(ser.Serialize(DateTime.Now.ToString("yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture)))
        w.Write(",""truncated"":") : w.Write(If(ddr.Rows.Count >= MaxGroupRows, "true", "false"))
        w.Write(",""cols"":") : w.Write(ser.Serialize(ColumnNames(ddr)))
        w.Write(",""rows"":") : WriteRows(w, ser, ddr)
        w.Write(",""m75cols"":") : w.Write(ser.Serialize(ColumnNames(m75)))
        w.Write(",""m75rows"":") : WriteRows(w, ser, m75)
        w.Write(",""history"":") : w.Write(If(historyOk, "true", "false"))
        If historyOk Then
            w.Write(",""snapcols"":") : w.Write(ser.Serialize(ColumnNames(snaps)))
            w.Write(",""snaprows"":") : WriteRows(w, ser, snaps)
            w.Write(",""baselines"":") : WriteObjects(w, ser, baselines)
            w.Write(",""baseline"":") : w.Write(ser.Serialize(blName))
            w.Write(",""blcols"":") : w.Write(If(blRows Is Nothing, "[]", ser.Serialize(ColumnNames(blRows))))
            w.Write(",""blrows"":") : If blRows Is Nothing Then w.Write("[]") Else WriteRows(w, ser, blRows)
        End If
        w.Write("}")
        EndJson()
    End Sub

    Private Function GetBaselineRows(sc As Scope, name As String) As Object
        Dim dt As DataTable = QueryTable("SELECT " & BaselineCols & " FROM [dbo].[SDDR_DASH_BASELINE] WHERE " & sc.Sql & " AND [BaselineName] = @B",
                                         P("@S", sc.Value), P("@B", name))
        Return New Dictionary(Of String, Object) From {{"baseline", name}, {"blcols", ColumnNames(dt)}, {"blrows", RowsOf(dt)}}
    End Function

    ' ------------------------------------------------------------------ Aconex

    ' Returns the latest Aconex register record for a document plus an optional deep link built from the
    ' web.config appSetting "SmartDDR.AconexDocUrl", e.g.
    '   https://ae1.aconex.com/Logon?returnUrl=/Document?docNo={Document_No}
    ' Any {Column_Name} of PDC_ACON_LATEST_DATE can be used as a placeholder.
    Private Shared Function GetAconex(project As String, doc As String) As Object
        If project.Length = 0 OrElse doc.Length = 0 OrElse doc.Length > 200 Then Throw New ArgumentException("Project and document number are required.")
        Dim dt As DataTable = QueryTable(
            "SELECT TOP 1 * FROM [PDC_ACON_LATEST_DATE]
             WHERE [Project_No] = @P AND LEFT([Document_No], 32) = LEFT(@D, 32)
             ORDER BY [DATE_MODIFIED] DESC", P("@P", project), P("@D", doc))
        Dim record As New List(Of Object)()
        Dim url As String = ""
        Dim template As String = ConfigurationManager.AppSettings(AconexUrlSetting)
        If dt.Rows.Count > 0 Then
            Dim r As DataRow = dt.Rows(0)
            For Each c As DataColumn In dt.Columns
                Dim v As Object = ToJsonValue(r(c))
                If v IsNot Nothing Then record.Add(New Object() {c.ColumnName, v})
            Next
            If Not String.IsNullOrWhiteSpace(template) Then
                url = template
                For Each c As DataColumn In dt.Columns
                    url = url.Replace("{" & c.ColumnName & "}", HttpUtility.UrlEncode(Convert.ToString(ToJsonValue(r(c)), CultureInfo.InvariantCulture)))
                Next
            End If
        ElseIf Not String.IsNullOrWhiteSpace(template) Then
            url = template.Replace("{Document_No}", HttpUtility.UrlEncode(doc)).Replace("{Project_No}", HttpUtility.UrlEncode(project))
        End If
        ' Only hand out http(s) links.
        If Not (url.StartsWith("https://", StringComparison.OrdinalIgnoreCase) OrElse url.StartsWith("http://", StringComparison.OrdinalIgnoreCase)) Then url = ""
        Return New Dictionary(Of String, Object) From {{"found", dt.Rows.Count > 0}, {"record", record}, {"url", url}}
    End Function

    ' ------------------------------------------------------------------ saved views

    Private Function GetViews() As Object
        Dim dt As DataTable = QueryTable(
            "SELECT [ViewId], [Name], [Scope], [StateJson], [Owner], [IsShared], [UpdatedAt]
             FROM [dbo].[SDDR_DASH_VIEWS]
             WHERE [IsShared] = 1 OR [Owner] = @U
             ORDER BY [Name]", P("@U", _user))
        Dim list As New List(Of Object)()
        For Each r As DataRow In dt.Rows
            Dim owner As String = Convert.ToString(r("Owner"))
            list.Add(New Dictionary(Of String, Object) From {
                {"id", Convert.ToInt32(r("ViewId"), CultureInfo.InvariantCulture)}, {"name", Convert.ToString(r("Name"))},
                {"scope", Convert.ToString(r("Scope"))}, {"state", Convert.ToString(r("StateJson"))},
                {"owner", owner}, {"shared", CBool(r("IsShared"))},
                {"mine", String.Equals(owner, _user, StringComparison.OrdinalIgnoreCase)},
                {"canDelete", _admin OrElse String.Equals(owner, _user, StringComparison.OrdinalIgnoreCase)}})
        Next
        Return list
    End Function

    Private Function SaveView() As Object
        If _user.Length = 0 Then Throw New UnauthorizedAccessException("Sign in to save views.")
        Dim name As String = ValidName(FormValue("name"))
        Dim scopeText As String = FormValue("scope").Trim()
        If scopeText.Length > 60 Then scopeText = scopeText.Substring(0, 60)
        Dim state As String = FormValue("state")
        If state.Length = 0 OrElse state.Length > 20000 Then Throw New ArgumentException("The view is empty or too large.")
        Try
            Dim test As Object = NewSerializer().DeserializeObject(state)   ' must be valid JSON
        Catch ex As Exception
            Throw New ArgumentException("The view state is not valid.")
        End Try
        Dim isShared As Boolean = FormValue("shared") = "1"
        ExecNonQuery(
            "MERGE [dbo].[SDDR_DASH_VIEWS] AS T
             USING (SELECT @N AS [Name], @U AS [Owner]) AS S ON T.[Name] = S.[Name] AND T.[Owner] = S.[Owner]
             WHEN MATCHED THEN UPDATE SET [Scope] = @SC, [StateJson] = @J, [IsShared] = @SH, [UpdatedAt] = SYSDATETIME()
             WHEN NOT MATCHED THEN INSERT ([Name], [Scope], [StateJson], [Owner], [IsShared], [CreatedAt], [UpdatedAt])
                                   VALUES (@N, @SC, @J, @U, @SH, SYSDATETIME(), SYSDATETIME());",
            P("@N", name), P("@U", _user), P("@SC", scopeText), NP("@J", state), New SqlParameter("@SH", SqlDbType.Bit) With {.Value = isShared})
        Return New Dictionary(Of String, Object) From {{"ok", True}}
    End Function

    Private Function DeleteView() As Object
        Dim id As Integer
        If Not Integer.TryParse(FormValue("id"), id) Then Throw New ArgumentException("Invalid view.")
        Dim n As Integer = ExecNonQuery(
            "DELETE FROM [dbo].[SDDR_DASH_VIEWS] WHERE [ViewId] = @ID AND ([Owner] = @U OR @A = 1)",
            New SqlParameter("@ID", SqlDbType.Int) With {.Value = id}, P("@U", _user), New SqlParameter("@A", SqlDbType.Bit) With {.Value = _admin})
        If n = 0 Then Throw New UnauthorizedAccessException("You can only delete your own views.")
        Return New Dictionary(Of String, Object) From {{"ok", True}}
    End Function

    ' ------------------------------------------------------------------ JSON helpers

    Private Shared Function ColumnNames(dt As DataTable) As List(Of String)
        Dim names As New List(Of String)()
        For Each c As DataColumn In dt.Columns
            names.Add(c.ColumnName)
        Next
        Return names
    End Function

    ' Rows as compact arrays, one row serialised at a time.
    Private Shared Sub WriteRows(w As TextWriter, ser As JavaScriptSerializer, dt As DataTable)
        Dim n As Integer = dt.Columns.Count
        Dim buf(Math.Max(n - 1, 0)) As Object
        If n = 0 Then ReDim buf(-1)
        w.Write("["c)
        For i As Integer = 0 To dt.Rows.Count - 1
            Dim r As DataRow = dt.Rows(i)
            For c As Integer = 0 To n - 1
                buf(c) = ToJsonValue(r(c))
            Next
            If i > 0 Then w.Write(","c)
            w.Write(ser.Serialize(buf))
        Next
        w.Write("]"c)
    End Sub

    Private Shared Function RowsOf(dt As DataTable) As List(Of Object())
        Dim rows As New List(Of Object())(dt.Rows.Count)
        For Each r As DataRow In dt.Rows
            Dim arr(dt.Columns.Count - 1) As Object
            For i As Integer = 0 To dt.Columns.Count - 1
                arr(i) = ToJsonValue(r(i))
            Next
            rows.Add(arr)
        Next
        Return rows
    End Function

    ' Small tables as an array of {column: value} objects.
    Private Shared Sub WriteObjects(w As TextWriter, ser As JavaScriptSerializer, dt As DataTable)
        Dim list As New List(Of Object)()
        For Each r As DataRow In dt.Rows
            Dim o As New Dictionary(Of String, Object)()
            For Each c As DataColumn In dt.Columns
                o(c.ColumnName) = ToJsonValue(r(c))
            Next
            list.Add(o)
        Next
        w.Write(ser.Serialize(list))
    End Sub

    ' Dates -> "yyyy-MM-dd" (SQL's 1900-01-01 "blank" -> null), numbers stay numbers, text is trimmed.
    Private Shared Function ToJsonValue(v As Object) As Object
        If v Is Nothing OrElse Convert.IsDBNull(v) Then Return Nothing
        If TypeOf v Is DateTime Then
            Dim d As DateTime = DirectCast(v, DateTime)
            If d.Year <= 1900 Then Return Nothing
            Return d.ToString(If(d.TimeOfDay = TimeSpan.Zero, "yyyy-MM-dd", "yyyy-MM-dd HH:mm"), CultureInfo.InvariantCulture)
        End If
        If TypeOf v Is DateTimeOffset Then
            Dim dto As DateTimeOffset = DirectCast(v, DateTimeOffset)
            If dto.Year <= 1900 Then Return Nothing
            Return dto.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)
        End If
        If TypeOf v Is Decimal OrElse TypeOf v Is Double OrElse TypeOf v Is Single Then
            Dim dbl As Double = Convert.ToDouble(v, CultureInfo.InvariantCulture)
            If Double.IsNaN(dbl) OrElse Double.IsInfinity(dbl) Then Return Nothing
            Return dbl
        End If
        If TypeOf v Is Boolean OrElse TypeOf v Is Integer OrElse TypeOf v Is Long OrElse TypeOf v Is Short OrElse TypeOf v Is Byte Then Return v
        If TypeOf v Is Byte() Then Return Nothing
        Dim text As String = Convert.ToString(v, CultureInfo.InvariantCulture).Trim()
        Return If(text.Length = 0, Nothing, text)
    End Function

    Private Shared Function NewSerializer() As JavaScriptSerializer
        Return New JavaScriptSerializer() With {.MaxJsonLength = Integer.MaxValue, .RecursionLimit = 16}
    End Function

    Private Shared Function ErrorPayload(message As String) As Object
        Return New Dictionary(Of String, Object) From {{"error", message}}
    End Function

    Private Sub BeginJson(status As Integer)
        Response.Clear()
        Response.StatusCode = status
        Response.TrySkipIisCustomErrors = True   ' keep our JSON error body instead of the IIS error page
        Response.ContentType = "application/json"
        Response.ContentEncoding = System.Text.Encoding.UTF8
        Response.AddHeader("X-Content-Type-Options", "nosniff")
        Response.Cache.SetCacheability(HttpCacheability.NoCache)
        Response.Cache.SetNoStore()
    End Sub

    Private Sub EndJson()
        ' Render() is suppressed for API calls, so nothing else is appended; no Flush needed.
        Context.ApplicationInstance.CompleteRequest()
    End Sub

    Private Sub WriteJson(status As Integer, payload As Object)
        BeginJson(status)
        Response.Write(NewSerializer().Serialize(payload))
        EndJson()
    End Sub

    ' ------------------------------------------------------------------ SQL helpers

    Private Shared Function P(name As String, value As String) As SqlParameter
        Dim prm As New SqlParameter(name, SqlDbType.VarChar, 255)
        prm.Value = If(value Is Nothing, CObj(DBNull.Value), CObj(value))
        Return prm
    End Function

    Private Shared Function NP(name As String, value As String) As SqlParameter
        Return New SqlParameter(name, SqlDbType.NVarChar, -1) With {.Value = value}
    End Function

    Private Shared Function QueryTable(sql As String, ParamArray prms As SqlParameter()) As DataTable
        Using con As New SqlConnection(CStr(ST_Common.WorleyDataConnString))
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
        Using con As New SqlConnection(CStr(ST_Common.WorleyDataConnString))
            Using cmd As New SqlCommand("SET NOCOUNT OFF; " & sql, con)
                cmd.CommandTimeout = 180
                If prms IsNot Nothing Then cmd.Parameters.AddRange(prms)
                con.Open()
                Return cmd.ExecuteNonQuery()
            End Using
        End Using
    End Function

    Private Shared Sub ExecProc(name As String, ParamArray prms As SqlParameter())
        Using con As New SqlConnection(CStr(ST_Common.WorleyDataConnString))
            Using cmd As New SqlCommand(name, con)
                cmd.CommandType = CommandType.StoredProcedure
                cmd.CommandTimeout = 600
                If prms IsNot Nothing Then cmd.Parameters.AddRange(prms)
                con.Open()
                cmd.ExecuteNonQuery()
            End Using
        End Using
    End Sub

End Class

Imports System.Collections.Generic
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
' The page itself is a static shell; all analysis runs in the browser on data
' fetched from this code-behind as JSON:
'   SmartDDRDashboard.aspx?api=projects&group=3
'   SmartDDRDashboard.aspx?api=data&project=3017          (one project)
'   SmartDDRDashboard.aspx?api=data&group=3               (every project in the group)
Public Class SmartDDRDashboard
    Inherits System.Web.UI.Page

    Private Const MaxGroupRows As Integer = 150000

    ' Group = project-number prefix, project = project number. Letters/digits only (plus - _ . / space for
    ' projects) so that SQL LIKE wildcards (% _ [ ]) can never widen the query to every project.
    Private Shared ReadOnly GroupPattern As New Regex("^[0-9A-Za-z]{1,10}$", RegexOptions.Compiled)
    Private Shared ReadOnly ProjectPattern As New Regex("^[0-9A-Za-z][0-9A-Za-z_\-\./ ]{0,39}$", RegexOptions.Compiled)

    Private _isApi As Boolean

    Protected Sub Page_Load(sender As Object, e As EventArgs) Handles Me.Load
        Dim api As String = If(Request.QueryString("api"), "").Trim().ToLowerInvariant()
        If api.Length = 0 Then Return   ' normal page render
        _isApi = True

        Try
            Select Case api
                Case "projects"
                    Dim group As String = ValidGroup(Request.QueryString("group"), required:=True)
                    WriteJson(200, GetProjects(group))
                Case "data"
                    Dim project As String = ValidProject(Request.QueryString("project"))
                    Dim group As String = ValidGroup(Request.QueryString("group"), required:=False)
                    If project.Length = 0 AndAlso group.Length = 0 Then Throw New ArgumentException("Pass a project or a group.")
                    WriteData(project, group)
                Case Else
                    Throw New ArgumentException("Unknown api '" & api & "'.")
            End Select
        Catch ex As ArgumentException
            WriteJson(400, ErrorPayload(ex.Message))
        Catch ex As Exception
            ' Don't leak SQL / server details to the browser; keep them in the trace.
            Trace.Warn("SmartDDRDashboard", "API '" & api & "' failed", ex)
            WriteJson(500, ErrorPayload("The data could not be loaded. Please try again or contact the SmartDDR administrator."))
        End Try
    End Sub

    ' API calls return JSON only: skip rendering the HTML shell.
    Protected Overrides Sub Render(writer As HtmlTextWriter)
        If Not _isApi Then MyBase.Render(writer)
    End Sub

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

    ' Queries first (so any SQL error still becomes a clean JSON error), then streams the rows one at
    ' a time - a whole group can be 100k+ rows and must not be materialised as one giant string.
    Private Sub WriteData(project As String, group As String)
        Dim byProject As Boolean = project.Length > 0
        Dim scopeSql As String = If(byProject, "[Project_No] = @S", "[Project_No] LIKE @S + '%'")
        Dim scopeValue As String = If(byProject, project, group)

        Dim colList As New List(Of String)()
        For Each c As String In DdrColumns
            colList.Add("[" & c & "]")
        Next

        Dim ddr As DataTable = QueryTable(
            "SELECT TOP (" & MaxGroupRows.ToString(CultureInfo.InvariantCulture) & ") " & String.Join(", ", colList) & "
             FROM [SDDR_ACON_DDR_EPR]
             WHERE " & scopeSql & " AND [Document_No] IS NOT NULL
             ORDER BY [Project_No], [Discipline], [Document_No]", P("@S", scopeValue))

        Dim m75 As DataTable = QueryTable(
            "SELECT [Project_No], [Discipline], [AFC_PLN_TOTAL], [AFCX_COUNT], [ADH_COUNT]
             FROM [ACAD_DATA].[dbo].[SDDR_ACON_AFC_STATUS_01]
             WHERE " & scopeSql, P("@S", scopeValue))

        Dim ser As JavaScriptSerializer = NewSerializer()
        BeginJson(200)
        Dim w As TextWriter = Response.Output
        w.Write("{""scope"":") : w.Write(ser.Serialize(If(byProject, "project", "group")))
        w.Write(",""key"":") : w.Write(ser.Serialize(scopeValue))
        w.Write(",""generated"":") : w.Write(ser.Serialize(DateTime.Now.ToString("yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture)))
        w.Write(",""truncated"":") : w.Write(If(ddr.Rows.Count >= MaxGroupRows, "true", "false"))
        w.Write(",""cols"":") : w.Write(ser.Serialize(ColumnNames(ddr)))
        w.Write(",""rows"":") : WriteRows(w, ser, ddr)
        w.Write(",""m75cols"":") : w.Write(ser.Serialize(ColumnNames(m75)))
        w.Write(",""m75rows"":") : WriteRows(w, ser, m75)
        w.Write("}")
        EndJson()
    End Sub

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
        Dim buf(n - 1) As Object
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

    ' Dates -> "yyyy-MM-dd" (SQL's 1900-01-01 "blank" -> null), numbers stay numbers, text is trimmed.
    Private Shared Function ToJsonValue(v As Object) As Object
        If v Is Nothing OrElse Convert.IsDBNull(v) Then Return Nothing
        If TypeOf v Is DateTime Then
            Dim d As DateTime = DirectCast(v, DateTime)
            If d.Year <= 1900 Then Return Nothing
            Return d.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)
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

    ' ------------------------------------------------------------------ JSON plumbing

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

    Private Shared Function P(name As String, value As String) As SqlParameter
        Dim prm As New SqlParameter(name, SqlDbType.VarChar, 255)
        prm.Value = If(value, "")
        Return prm
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

End Class

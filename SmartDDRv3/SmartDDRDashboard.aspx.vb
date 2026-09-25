Imports System.Collections.Generic
Imports System.Data
Imports System.Data.SqlClient
Imports System.Globalization
Imports System.Web
Imports System.Web.Script.Serialization

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

    Protected Sub Page_Load(sender As Object, e As EventArgs) Handles Me.Load
        Dim api As String = If(Request.QueryString("api"), "").Trim().ToLowerInvariant()
        If api.Length = 0 Then Return   ' normal page render

        Dim payload As Object
        Try
            Select Case api
                Case "projects"
                    payload = GetProjects(If(Request.QueryString("group"), "").Trim())
                Case "data"
                    payload = GetData(If(Request.QueryString("project"), "").Trim(),
                                      If(Request.QueryString("group"), "").Trim())
                Case Else
                    Response.StatusCode = 400
                    payload = New Dictionary(Of String, Object) From {{"error", "Unknown api '" & api & "'."}}
            End Select
        Catch ex As Exception
            Response.StatusCode = 500
            payload = New Dictionary(Of String, Object) From {{"error", ex.Message}}
        End Try

        WriteJson(payload)
    End Sub

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

    Private Shared Function GetData(project As String, group As String) As Object
        If project.Length = 0 AndAlso group.Length = 0 Then Throw New ArgumentException("Pass a project or a group.")

        Dim scopeSql As String
        Dim scopeParam As SqlParameter
        If project.Length > 0 Then
            scopeSql = "[Project_No] = @S"
            scopeParam = P("@S", project)
        Else
            scopeSql = "[Project_No] LIKE @S + '%'"
            scopeParam = P("@S", group)
        End If

        Dim colList As New List(Of String)()
        For Each c As String In DdrColumns
            colList.Add("[" & c & "]")
        Next

        Dim ddr As DataTable = QueryTable(
            "SELECT TOP (" & MaxGroupRows.ToString(CultureInfo.InvariantCulture) & ") " & String.Join(", ", colList) & "
             FROM [SDDR_ACON_DDR_EPR]
             WHERE " & scopeSql & " AND [Document_No] IS NOT NULL
             ORDER BY [Project_No], [Discipline], [Document_No]", scopeParam)

        Dim m75 As DataTable = QueryTable(
            "SELECT [Project_No], [Discipline], [AFC_PLN_TOTAL], [AFCX_COUNT], [ADH_COUNT]
             FROM [ACAD_DATA].[dbo].[SDDR_ACON_AFC_STATUS_01]
             WHERE " & scopeSql, P("@S", If(project.Length > 0, project, group)))

        Return New Dictionary(Of String, Object) From {
            {"scope", If(project.Length > 0, "project", "group")},
            {"key", If(project.Length > 0, project, group)},
            {"generated", DateTime.Now.ToString("yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture)},
            {"truncated", ddr.Rows.Count >= MaxGroupRows},
            {"cols", ColumnNames(ddr)},
            {"rows", RowsOf(ddr)},
            {"m75cols", ColumnNames(m75)},
            {"m75rows", RowsOf(m75)}}
    End Function

    Private Shared Function ColumnNames(dt As DataTable) As List(Of String)
        Dim names As New List(Of String)()
        For Each c As DataColumn In dt.Columns
            names.Add(c.ColumnName)
        Next
        Return names
    End Function

    ' Rows as compact arrays. Dates -> "yyyy-MM-dd" (SQL's 1900-01-01 "blank" -> null),
    ' numbers stay numbers, text is trimmed.
    Private Shared Function RowsOf(dt As DataTable) As List(Of Object())
        Dim rows As New List(Of Object())(dt.Rows.Count)
        Dim n As Integer = dt.Columns.Count
        For Each r As DataRow In dt.Rows
            Dim arr(n - 1) As Object
            For i As Integer = 0 To n - 1
                arr(i) = ToJsonValue(r(i))
            Next
            rows.Add(arr)
        Next
        Return rows
    End Function

    Private Shared Function ToJsonValue(v As Object) As Object
        If v Is Nothing OrElse Convert.IsDBNull(v) Then Return Nothing
        If TypeOf v Is DateTime Then
            Dim d As DateTime = DirectCast(v, DateTime)
            If d.Year <= 1900 Then Return Nothing
            Return d.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)
        End If
        If TypeOf v Is DateTimeOffset Then Return DirectCast(v, DateTimeOffset).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)
        If TypeOf v Is Decimal OrElse TypeOf v Is Double OrElse TypeOf v Is Single Then
            Dim dbl As Double = Convert.ToDouble(v, CultureInfo.InvariantCulture)
            If Double.IsNaN(dbl) OrElse Double.IsInfinity(dbl) Then Return Nothing
            Return dbl
        End If
        If TypeOf v Is Boolean OrElse TypeOf v Is Integer OrElse TypeOf v Is Long OrElse TypeOf v Is Short OrElse TypeOf v Is Byte Then Return v
        Dim text As String = Convert.ToString(v, CultureInfo.InvariantCulture).Trim()
        Return If(text.Length = 0, Nothing, text)
    End Function

    ' ------------------------------------------------------------------ plumbing

    Private Sub WriteJson(payload As Object)
        Dim serializer As New JavaScriptSerializer() With {.MaxJsonLength = Integer.MaxValue, .RecursionLimit = 16}
        Response.Clear()
        Response.TrySkipIisCustomErrors = True   ' keep our JSON error body instead of the IIS error page
        Response.ContentType = "application/json"
        Response.ContentEncoding = System.Text.Encoding.UTF8
        Response.Cache.SetCacheability(HttpCacheability.NoCache)
        Response.Write(serializer.Serialize(payload))
        Response.Flush()
        Response.SuppressContent = True
        Context.ApplicationInstance.CompleteRequest()
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

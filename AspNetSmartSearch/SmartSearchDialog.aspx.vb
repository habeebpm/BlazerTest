Imports System.Data
Imports System.Web

Namespace AspNetSmartSearch

    ''' <summary>
    ''' Generic "smart search" lookup dialog. Opened as a popup (window.open) from
    ''' any page; on Select it hands the chosen record back to the opener window
    ''' via a JS callback and closes itself. See README.md for wiring instructions.
    ''' </summary>
    Public Class SmartSearchDialog
        Inherits System.Web.UI.Page

        Protected Sub Page_Load(sender As Object, e As EventArgs) Handles Me.Load
            If Not IsPostBack Then
                ' First hit: the caller passed the parent page's control IDs and the
                ' lookup "mode" on the query string. Stash them in hidden fields so
                ' they survive this dialog's own postbacks (Search button, etc.).
                hfTargetTextClientId.Value = Request.QueryString("txt")
                hfTargetHiddenClientId.Value = Request.QueryString("hid")
                hfMode.Value = Request.QueryString("mode")

                txtSearch.Focus()

                ' Show an initial unfiltered (or empty) result set on open.
                BindResults(String.Empty)
            End If
        End Sub

        Protected Sub btnSearch_Click(sender As Object, e As EventArgs)
            BindResults(txtSearch.Text.Trim())
        End Sub

        Private Sub BindResults(searchTerm As String)
            Dim results As DataTable = GetSearchResults(hfMode.Value, searchTerm)
            gvResults.DataSource = results
            gvResults.DataBind()

            lblMessage.Text = If(results.Rows.Count = 0, "No matches found.", String.Empty)
        End Sub

        Protected Sub gvResults_RowCommand(sender As Object, e As System.Web.UI.WebControls.GridViewCommandEventArgs)
            If e.CommandName <> "SelectRow" Then Return

            Dim selectedId As String = e.CommandArgument.ToString()

            ' Re-look-up the row so we return trusted data, not anything the
            ' client could have tampered with.
            Dim results As DataTable = GetSearchResults(hfMode.Value, txtSearch.Text.Trim())
            Dim matchingRows As DataRow() = results.Select("Id = " & selectedId)
            If matchingRows.Length = 0 Then
                lblMessage.Text = "The selected record is no longer available; please search again."
                Return
            End If

            Dim selectedName As String = matchingRows(0)("Name").ToString()

            ReturnResultToParent(selectedId, selectedName)
        End Sub

        ''' <summary>
        ''' Emits a tiny startup script that calls back into the opener window with
        ''' the chosen id/name and then closes this dialog. This is the step that
        ''' actually "stores the search result in a control in the parent page" --
        ''' see smartSearch_setResult() on the parent, which writes the values into
        ''' its TextBox/HiddenField controls.
        ''' </summary>
        Private Sub ReturnResultToParent(id As String, name As String)
            Dim script As String =
                "smartSearch_returnResult(" &
                JsStringLiteral(id) & ", " &
                JsStringLiteral(name) & ");"

            ClientScript.RegisterStartupScript(Me.GetType(), "returnResult", script, True)
        End Sub

        ''' <summary>Safely quotes a value for embedding in an inline &lt;script&gt; block.</summary>
        Private Function JsStringLiteral(value As String) As String
            Return "'" & HttpUtility.JavaScriptStringEncode(If(value, String.Empty)) & "'"
        End Function

        ''' <summary>
        ''' Sample data source. Replace this with a real lookup, e.g.:
        '''
        '''   Using conn As New SqlConnection(connectionString)
        '''       Using cmd As New SqlCommand(
        '''           "SELECT Id, Name, Description FROM Customers " &
        '''           "WHERE @term = '' OR Name LIKE '%' + @term + '%'", conn)
        '''           cmd.Parameters.AddWithValue("@term", searchTerm)
        '''           Using da As New SqlDataAdapter(cmd)
        '''               Dim dt As New DataTable()
        '''               da.Fill(dt)
        '''               Return dt
        '''           End Using
        '''       End Using
        '''   End Using
        '''
        ''' `mode` lets a single dialog page serve multiple lookups (customers,
        ''' products, ...) by choosing which table/query to run.
        ''' </summary>
        Private Function GetSearchResults(mode As String, searchTerm As String) As DataTable
            Dim table As New DataTable()
            table.Columns.Add("Id", GetType(Integer))
            table.Columns.Add("Name", GetType(String))
            table.Columns.Add("Description", GetType(String))

            Dim allRows As New List(Of Object())
            Select Case mode
                Case "Product"
                    allRows.Add(New Object() {101, "Widget A", "Standard widget"})
                    allRows.Add(New Object() {102, "Widget B", "Heavy-duty widget"})
                    allRows.Add(New Object() {103, "Gadget C", "Compact gadget"})
                Case Else ' "Customer" and default
                    allRows.Add(New Object() {1, "Acme Corp", "Acme Corporation - Springfield"})
                    allRows.Add(New Object() {2, "Globex Inc", "Globex Incorporated - Metro City"})
                    allRows.Add(New Object() {3, "Initech", "Initech LLC - Austin"})
                    allRows.Add(New Object() {4, "Umbrella Co", "Umbrella Company - Raccoon City"})
            End Select

            For Each row In allRows
                Dim name = row(1).ToString()
                If String.IsNullOrEmpty(searchTerm) OrElse
                   name.IndexOf(searchTerm, StringComparison.OrdinalIgnoreCase) >= 0 Then
                    table.Rows.Add(row)
                End If
            Next

            Return table
        End Function

    End Class

End Namespace

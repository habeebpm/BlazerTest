Imports System.Data
Imports System.Data.SqlClient
Imports System.IO
Imports System.Text
Imports System.Web
Imports System.Web.UI.WebControls

Public Class DDR_Developer
    Inherits System.Web.UI.Page

#Region "Page lifecycle"

    Protected Sub Page_Load(ByVal sender As Object, ByVal e As System.EventArgs) Handles Me.Load
        If Not IsPostBack Then
            Dim ctdId As Integer
            Integer.TryParse(Request.QueryString("CTD_ID"), ctdId)

            Dim ctdRef As String = ExecScalarQuery(
                "SELECT TOP 1 DEL_ITEM_REF FROM CTD_MASTER WHERE CTD_ID = @CTD_ID",
                New SqlParameter("@CTD_ID", ctdId))

            lblContext.Text = ctdId.ToString()
            lblRef.Text = ctdRef

            LoadDdrGrid()
        End If
    End Sub

#End Region

#Region "Navigation"

    Protected Sub Home_Go(sender As Object, e As EventArgs)
        Response.Redirect("Index.aspx")
    End Sub

    Protected Sub CTD_Prev(sender As Object, e As EventArgs)
        NavigateToAdjacentCtd(goForward:=False)
    End Sub

    Protected Sub CTD_Next(sender As Object, e As EventArgs)
        NavigateToAdjacentCtd(goForward:=True)
    End Sub

    ''' <summary>
    ''' The "13. Process"/"40087" scoping mirrors the original page; adjust the
    ''' comparison values here if this list should not be discipline/project pinned.
    ''' </summary>
    Private Sub NavigateToAdjacentCtd(goForward As Boolean)
        Dim currentCtdId As Integer
        If Not Integer.TryParse(Request.QueryString("CTD_ID"), currentCtdId) OrElse currentCtdId = 0 Then
            Exit Sub
        End If

        Dim comparisonOperator As String = If(goForward, ">", "<")
        ' Next wants the smallest ID above current (ASC); Prev wants the
        ' largest ID below current (DESC) - both hard-coded to ASC previously,
        ' which made "Prev" jump to the very first matching CTD every time
        ' instead of the immediately preceding one.
        Dim orderDirection As String = If(goForward, "ASC", "DESC")
        Dim sql As String = "SELECT TOP 1 CTD_ID FROM CTD_MASTER " &
            "WHERE CTD_ID " & comparisonOperator & " @CurrentID " &
            "AND [Discipline] = '13. Process' AND [Project_No] = '40087' " &
            "ORDER BY CTD_ID " & orderDirection

        Dim nextId As String = ExecScalarQuery(sql, New SqlParameter("@CurrentID", currentCtdId))

        Dim parsedId As Integer
        If Integer.TryParse(nextId, parsedId) AndAlso parsedId > 0 Then
            Response.Redirect(Request.Path & "?CTD_ID=" & parsedId)
        Else
            ShowToast("info", "No further CTD records in that direction.")
        End If
    End Sub

    Protected Sub btnCTDSearch_Click(sender As Object, e As EventArgs)
        Dim btn As LinkButton = CType(sender, LinkButton)
        ScriptManager.RegisterStartupScript(Me, Me.GetType(), "OpenCTDDrawer",
            "openCTDDrawer(document.getElementById('" & btn.ClientID & "'));", True)
    End Sub

#End Region

#Region "CTD grid"

    Protected Sub CTD_Grid_RowDataBound(sender As Object, e As GridViewRowEventArgs)
        If e.Row.RowType <> DataControlRowType.DataRow Then Return

        Dim drv As DataRowView = TryCast(e.Row.DataItem, DataRowView)
        If drv Is Nothing Then Return

        lblProject.Text = drv("Project_No").ToString()
        lblDiscipline.Text = drv("Discipline").ToString()
        Session("Doc_Mode") = drv("Doc_Mode").ToString()
        Session("Del_Ref") = drv("Del_Item_Ref").ToString()

        litProject.Text = HttpUtility.HtmlEncode(lblProject.Text)
        litDiscipline.Text = HttpUtility.HtmlEncode(lblDiscipline.Text)
        litRef.Text = HttpUtility.HtmlEncode(lblRef.Text)
    End Sub

    Protected Sub ctd_ddr_match_RowDataBound(sender As Object, e As GridViewRowEventArgs)
        If e.Row.RowType <> DataControlRowType.DataRow Then Return

        Dim drv As DataRowView = TryCast(e.Row.DataItem, DataRowView)
        If drv Is Nothing Then Return

        Dim ctdHrs As Decimal = 0, ddrHrs As Decimal = 0
        Decimal.TryParse(drv("CTD Hrs").ToString(), ctdHrs)
        Decimal.TryParse(drv("DDR Hrs").ToString(), ddrHrs)

        Dim lblFlag As Label = TryCast(e.Row.FindControl("lblMatchFlag"), Label)
        If ctdHrs <> ddrHrs Then
            e.Row.CssClass = "badge-warn"
            If lblFlag IsNot Nothing Then lblFlag.Text = "🚩"
        Else
            ' There's no widely-supported "green flag" glyph, so a check mark
            ' stands in for "matching" alongside the red flag for "not matching".
            If lblFlag IsNot Nothing Then lblFlag.Text = "✅"
        End If
    End Sub

#End Region

#Region "PLIP search drawer"

    Protected Sub btnPLIPSearch_Click(sender As Object, e As EventArgs)
        Dim btn As LinkButton = CType(sender, LinkButton)
        Dim row As GridViewRow = CType(btn.NamingContainer, GridViewRow)
        Dim txtPLIP As TextBox = TryCast(row.FindControl("txtPLIP"), TextBox)

        hfSelectedRow.Value = row.RowIndex.ToString()
        If txtPLIP IsNot Nothing Then
            txtSearchPLIP.Text = txtPLIP.Text
        End If

        gvPLIPSearch.DataBind()
        ScriptManager.RegisterStartupScript(Me, Me.GetType(), "OpenPLIP",
            "openPLIPDrawer(document.getElementById('" & btn.ClientID & "'));", True)
    End Sub

    Protected Sub txtSearchPLIP_TextChanged(sender As Object, e As EventArgs)
        ' PLIPSource's SelectCommand is parameterized against txtSearchPLIP directly
        ' (see markup), so no dynamic SQL needs to be built here.
        gvPLIPSearch.DataBind()
    End Sub

    Protected Sub gvPLIPSearch_RowCommand(sender As Object, e As GridViewCommandEventArgs)
        If e.CommandName <> "SelectPLIP" Then Return

        Dim plipId As String = e.CommandArgument.ToString()

        Dim rowIndex As Integer
        If Integer.TryParse(hfSelectedRow.Value, rowIndex) AndAlso
           rowIndex >= 0 AndAlso rowIndex < grdDDREntry.Rows.Count Then

            Dim targetRow As GridViewRow = grdDDREntry.Rows(rowIndex)
            Dim txtPLIP As TextBox = TryCast(targetRow.FindControl("txtPLIP"), TextBox)
            If txtPLIP IsNot Nothing Then
                txtPLIP.Text = plipId
                ApplyPlipLookup(targetRow, plipId)
            End If
        End If

        ScriptManager.RegisterStartupScript(Me, Me.GetType(), "KeepDrawerOpen", "openPLIPDrawer();", True)
        ShowToast("success", "PLIP " & plipId & " applied to the selected row.")
    End Sub

    ''' <summary>
    ''' Refreshes a DDR row's Critical/PLIP_HO labels and AFC/APP status from the
    ''' PLIP master data, after either a manual PLIP edit or a drawer selection.
    ''' </summary>
    Private Sub ApplyPlipLookup(row As GridViewRow, plipId As String)
        Dim status = GetPlipStatus(plipId)

        Dim lblCriticality As Label = TryCast(row.FindControl("lblCriticality"), Label)
        Dim lblHoReq As Label = TryCast(row.FindControl("lblHoReq"), Label)
        Dim ddlStatus As DropDownList = TryCast(row.FindControl("ddlStatus"), DropDownList)

        If lblCriticality IsNot Nothing Then lblCriticality.Text = status.Critical
        If lblHoReq IsNot Nothing Then lblHoReq.Text = status.FhoStatus
        If ddlStatus IsNot Nothing Then
            ddlStatus.SelectedValue = If(status.FhoStatus = "ASB", "AFC", "APP")
        End If
    End Sub

    Protected Sub txtPLIP_TextChanged(sender As Object, e As EventArgs)
        Dim txtPLIP As TextBox = CType(sender, TextBox)
        Dim row As GridViewRow = CType(txtPLIP.NamingContainer, GridViewRow)
        ApplyPlipLookup(row, txtPLIP.Text.Trim())
    End Sub

#End Region

#Region "CTD search drawer"

    Protected Sub ctdGrid_RowDataBound(sender As Object, e As GridViewRowEventArgs)
        If e.Row.RowType <> DataControlRowType.DataRow Then Return

        Dim drv As DataRowView = TryCast(e.Row.DataItem, DataRowView)
        If drv Is Nothing Then Return

        Dim ctdHrs As Decimal = 0, ddrHrs As Decimal = 0
        Decimal.TryParse(drv("CTD Hrs").ToString(), ctdHrs)
        Decimal.TryParse(drv("DDR Hrs").ToString(), ddrHrs)

        If ctdHrs > 0 OrElse ddrHrs > 0 Then
            If ctdHrs <> ddrHrs Then
                e.Row.BackColor = System.Drawing.Color.LightYellow
            Else
                e.Row.BackColor = System.Drawing.Color.GreenYellow
            End If
        End If
        ' The CTD ID hyperlink is already rendered by the declarative
        ' TemplateField/HyperLink above; the original code additionally injected
        ' a second HyperLink into Cells(0) here, which duplicated the link.
    End Sub

#End Region

#Region "DDR entry grid: add / remove rows"

    Private Function NewDdrTempTable() As DataTable
        Dim dt As New DataTable
        dt.Columns.Add("CTD_ID", GetType(Integer))
        dt.Columns.Add("DDR_ID", GetType(Integer))
        dt.Columns.Add("PLIP_ID", GetType(String))
        dt.Columns.Add("RAMZ_ID", GetType(String))
        dt.Columns.Add("Document_No", GetType(String))
        dt.Columns.Add("Document_Title", GetType(String))
        dt.Columns.Add("Man_Hours", GetType(String))
        dt.Columns.Add("Disc_Remarks", GetType(String))
        dt.Columns.Add("CRITICALITY", GetType(String))
        dt.Columns.Add("HO_REQ", GetType(String))
        dt.Columns.Add("HO_STATUS", GetType(String))
        dt.Columns.Add("Software", GetType(String))
        Return dt
    End Function

    Private Function AddBlankDdrRow(dt As DataTable, documentNo As String) As DataRow
        Dim dr As DataRow = dt.NewRow()
        dr("CTD_ID") = Val(lblContext.Text)
        dr("DDR_ID") = 0
        dr("PLIP_ID") = ""
        dr("RAMZ_ID") = ""
        dr("Document_No") = documentNo
        dr("Document_Title") = ""
        dr("Man_Hours") = ""
        dr("Disc_Remarks") = ""
        dr("CRITICALITY") = ""
        dr("HO_REQ") = ""
        dr("HO_STATUS") = ""
        dr("Software") = ""
        dt.Rows.Add(dr)
        Return dr
    End Function

    Private Sub RebindTempGrid(dt As DataTable)
        grdDDREntry.DataSource = dt
        grdDDREntry.DataBind()
        UpdateKpiScorecard()
    End Sub

    ''' <summary>
    ''' Refreshes the KPI scorecard (CTD Hours, DDR Hours, Variance, DDR Line
    ''' Items, Match Status) from grdDDREntry's currently rendered rows. Called
    ''' from RebindTempGrid so it's always in sync after any grid change.
    ''' </summary>
    Private Sub UpdateKpiScorecard()
        Dim ddrHours As Decimal = 0
        Dim rowCount As Integer = 0

        For Each row As GridViewRow In grdDDREntry.Rows
            If row.RowType <> DataControlRowType.DataRow Then Continue For
            rowCount += 1

            Dim txtHours As TextBox = TryCast(row.FindControl("txtHours"), TextBox)
            Dim h As Decimal
            If txtHours IsNot Nothing AndAlso Decimal.TryParse(txtHours.Text.Trim(), h) Then
                ddrHours += h
            End If
        Next

        Dim ctdHours As Decimal = GetCurrentCtdTotalHours()
        Dim variance As Decimal = ctdHours - ddrHours

        litKpiCtdHours.Text = ctdHours.ToString("0.##")
        litKpiDdrHours.Text = ddrHours.ToString("0.##")
        litKpiRowCount.Text = rowCount.ToString()

        lblKpiVariance.Text = If(variance > 0, "+", "") & variance.ToString("0.##")
        lblKpiVariance.CssClass = "kpi-value " & If(variance = 0, "positive", "negative")

        If variance = 0 AndAlso rowCount > 0 Then
            lblKpiMatchStatus.Text = "✅ Balanced"
            lblKpiMatchStatus.CssClass = "kpi-value positive"
        Else
            lblKpiMatchStatus.Text = "🚩 Out of balance"
            lblKpiMatchStatus.CssClass = "kpi-value negative"
        End If
    End Sub

    ''' <summary>
    ''' Single source of truth for grdDDREntry: queries the current CTD's DDR
    ''' lines fresh and rebinds from that DataTable. grdDDREntry is never bound
    ''' via DataSourceID (see the .aspx comment history) - mixing a declarative
    ''' SqlDataSource binding with the manual "add/remove unsaved rows" DataTable
    ''' used to let the framework's own auto-rebind-on-postback behavior silently
    ''' reset DataKeys back to 0 between the "Add DDR Line" and "Save All"
    ''' postbacks, which made Save All treat already-saved rows as new inserts
    ''' and duplicate them. Loading through one explicit path on every request
    ''' removes that failure mode entirely.
    ''' </summary>
    Private Sub LoadDdrGrid()
        Dim dt As DataTable = NewDdrTempTable()
        Dim ctdId As Integer = Val(lblContext.Text)

        Using con As New SqlConnection(ST_Common.WorleyDataConnString)
            Using cmd As New SqlCommand(
                "SELECT CTD_ID, DDR_ID, PLIP_ID, RAMZ_ID, Document_No, Document_Title, Man_Hours, " &
                "Disc_Remarks, CRITICALITY, HO_REQ, HO_STATUS, Software " &
                "FROM CTD_DDR_DISC WHERE CTD_ID = @CTD_ID", con)
                cmd.Parameters.AddWithValue("@CTD_ID", ctdId)
                con.Open()
                Using rd As SqlDataReader = cmd.ExecuteReader()
                    While rd.Read()
                        Dim dr As DataRow = dt.NewRow()
                        dr("CTD_ID") = rd("CTD_ID")
                        dr("DDR_ID") = rd("DDR_ID")
                        dr("PLIP_ID") = rd("PLIP_ID").ToString()
                        dr("RAMZ_ID") = rd("RAMZ_ID").ToString()
                        dr("Document_No") = rd("Document_No").ToString()
                        dr("Document_Title") = rd("Document_Title").ToString()
                        dr("Man_Hours") = rd("Man_Hours").ToString()
                        dr("Disc_Remarks") = rd("Disc_Remarks").ToString()
                        dr("CRITICALITY") = rd("CRITICALITY").ToString()
                        dr("HO_REQ") = rd("HO_REQ").ToString()
                        dr("HO_STATUS") = rd("HO_STATUS").ToString()
                        dr("Software") = rd("Software").ToString()
                        dt.Rows.Add(dr)
                    End While
                End Using
            End Using
        End Using

        RebindTempGrid(dt)
    End Sub

    Protected Sub btnAddDDR_Click(sender As Object, e As EventArgs)
        Dim dt As DataTable = GetCurrentGridData()
        AddBlankDdrRow(dt, "")
        RebindTempGrid(dt)
    End Sub

    Protected Sub DDR1_Click(sender As Object, e As EventArgs)
        Dim dt As DataTable = GetCurrentGridData()
        AddBlankDdrRow(dt, "")
        AddBlankDdrRow(dt, "ACTIVITY")
        RebindTempGrid(dt)
        ShowToast("success", "Added a document row and an activity row.")
    End Sub

    ''' <summary>
    ''' Splits the CTD's total hours evenly across every current DDR row,
    ''' rounded to 2dp, with the first row absorbing whatever rounding
    ''' remainder is left over so the DDR total matches the CTD total exactly.
    ''' Edits the rendered rows in place - Save All still persists them.
    ''' </summary>
    Protected Sub btnAllocateHours_Click(sender As Object, e As EventArgs)
        Dim hourBoxes As New List(Of TextBox)
        For Each row As GridViewRow In grdDDREntry.Rows
            If row.RowType <> DataControlRowType.DataRow Then Continue For
            Dim txtHours As TextBox = TryCast(row.FindControl("txtHours"), TextBox)
            If txtHours IsNot Nothing Then hourBoxes.Add(txtHours)
        Next

        If hourBoxes.Count = 0 Then
            ShowToast("error", "Add DDR lines before allocating hours.")
            Return
        End If

        Dim ctdHours As Decimal = GetCurrentCtdTotalHours()
        If ctdHours <= 0 Then
            ShowToast("error", "This CTD has no hours to allocate.")
            Return
        End If

        Dim perRow As Decimal = Math.Round(ctdHours / hourBoxes.Count, 2)
        For Each box As TextBox In hourBoxes
            box.Text = perRow.ToString("0.##")
        Next

        Dim remainder As Decimal = ctdHours - (perRow * hourBoxes.Count)
        hourBoxes(0).Text = (perRow + remainder).ToString("0.##")

        UpdateKpiScorecard()
        ShowToast("success", "Allocated " & ctdHours.ToString("0.##") & " hours across " & hourBoxes.Count & " row(s).")
    End Sub

    Protected Sub grdDDREntry_RowCommand(sender As Object, e As GridViewCommandEventArgs)
        If e.CommandName <> "DeleteDDR" Then Return

        Dim ddrId As Integer
        Integer.TryParse(e.CommandArgument.ToString(), ddrId)

        If ddrId > 0 Then
            Using con As New SqlConnection(ST_Common.WorleyDataConnString)
                con.Open()
                Using cmd As New SqlCommand("DELETE FROM CTD_DDR_DISC WHERE DDR_ID = @DDR_ID", con)
                    cmd.Parameters.AddWithValue("@DDR_ID", ddrId)
                    cmd.ExecuteNonQuery()
                End Using
            End Using
            LoadDdrGrid()
            ShowToast("success", "DDR line deleted.")
        Else
            Dim linkButton As LinkButton = TryCast(e.CommandSource, LinkButton)
            Dim gridRow As GridViewRow = TryCast(linkButton?.NamingContainer, GridViewRow)
            If gridRow Is Nothing Then Return

            Dim dt As DataTable = GetCurrentGridData()
            If gridRow.RowIndex >= 0 AndAlso gridRow.RowIndex < dt.Rows.Count Then
                dt.Rows.RemoveAt(gridRow.RowIndex)
            End If
            RebindTempGrid(dt)
            ShowToast("info", "Unsaved DDR line removed.")
        End If
    End Sub

    ''' <summary>Opens the multiplier drawer targeting the row whose "&times;N" button was clicked.</summary>
    Protected Sub btnMultiplyRow_Click(sender As Object, e As EventArgs)
        Dim btn As LinkButton = CType(sender, LinkButton)
        Dim row As GridViewRow = CType(btn.NamingContainer, GridViewRow)
        Dim txtDocumentNo As TextBox = TryCast(row.FindControl("txtDocumentNo"), TextBox)

        hfMultiplyRow.Value = row.RowIndex.ToString()
        litMultiplyTarget.Text = "Row " & (row.RowIndex + 1) & ": " &
            HttpUtility.HtmlEncode(If(txtDocumentNo?.Text, "").Trim())

        ScriptManager.RegisterStartupScript(Me, Me.GetType(), "OpenMultiplier",
            "openMultiplierDrawer(document.getElementById('" & btn.ClientID & "'));", True)
    End Sub

    ''' <summary>
    ''' Creates N copies of the targeted row. Mode A gives each copy the next
    ''' DUM01..DUM99/DU100+ sequence number in the row's 4th "-"-segment; mode
    ''' B increments the document number's trailing digit run once per copy.
    ''' All N copies come from this one target row - it is left unchanged.
    ''' </summary>
    Protected Sub btnApplyMultiplier_Click(sender As Object, e As EventArgs)
        Dim rowIndex As Integer
        If Not Integer.TryParse(hfMultiplyRow.Value, rowIndex) Then
            ShowToast("error", "Choose a row to multiply first.")
            Return
        End If

        Dim n As Integer
        If Not Integer.TryParse(txtMultiplyCount.Text.Trim(), n) OrElse n < 1 OrElse n > 50 Then
            ShowToast("error", "Number of copies must be between 1 and 50.")
            Return
        End If

        Dim dt As DataTable = GetCurrentGridData()
        If rowIndex < 0 OrElse rowIndex >= dt.Rows.Count Then
            ShowToast("error", "That row no longer exists.")
            Return
        End If

        Dim sourceRow As DataRow = dt.Rows(rowIndex)
        Dim baseDocNo As String = sourceRow("Document_No").ToString()
        Dim useDumSequence As Boolean = (rblMultiplyMode.SelectedValue = "DUM")

        ' Read the starting DUM number once, up front - GetCurrentMaxDummyNumber()
        ' scans grdDDREntry's *rendered* rows, which won't reflect the new rows
        ' being built into dt until RebindTempGrid runs, so it can't be called
        ' again inside the loop (it would just return the same value N times).
        Dim nextDummyN As Integer = GetCurrentMaxDummyNumber() + 1

        For i As Integer = 1 To n
            Dim newRow As DataRow = dt.NewRow()
            For Each col As DataColumn In dt.Columns
                newRow(col.ColumnName) = sourceRow(col.ColumnName)
            Next
            newRow("DDR_ID") = 0

            Dim parts() As String = baseDocNo.Split("-"c)
            If useDumSequence Then
                If parts.Length >= 4 Then
                    parts(3) = FormatDummySuffix(nextDummyN)
                    nextDummyN += 1
                    newRow("Document_No") = String.Join("-", parts)
                End If
            Else
                newRow("Document_No") = IncrementTrailingDigits(baseDocNo, i)
            End If

            dt.Rows.Add(newRow)
        Next

        RebindTempGrid(dt)
        hfMultiplyRow.Value = ""
        ShowToast("success", n.ToString() & " copy/copies created from row " & (rowIndex + 1) & ".")
    End Sub

    ''' <summary>Increments the numeric run at the very end of a document number, preserving its digit width (e.g. "...-0001" -&gt; "...-0002").</summary>
    Private Function IncrementTrailingDigits(docNo As String, increment As Integer) As String
        Dim m As System.Text.RegularExpressions.Match = System.Text.RegularExpressions.Regex.Match(docNo, "(\d+)$")
        If Not m.Success Then Return docNo

        Dim digits As String = m.Groups(1).Value
        Dim newValue As Long = Long.Parse(digits) + increment
        Dim newDigits As String = newValue.ToString().PadLeft(digits.Length, "0"c)
        Return docNo.Substring(0, m.Index) & newDigits
    End Function

    ''' <summary>
    ''' Reconstructs the in-memory DDR table from what's currently rendered in
    ''' grdDDREntry, reading CRITICALITY/HO_REQ from their Label controls rather
    ''' than DataKeys (removes the page's dependency on keys that were never
    ''' actually part of DataKeyNames).
    ''' </summary>
    Private Function GetCurrentGridData() As DataTable
        Dim dt As DataTable = NewDdrTempTable()

        For Each row As GridViewRow In grdDDREntry.Rows
            If row.RowType <> DataControlRowType.DataRow Then Continue For

            Dim txtPLIP As TextBox = TryCast(row.FindControl("txtPLIP"), TextBox)
            Dim ddlRamz As DropDownList = TryCast(row.FindControl("ddlRamz"), DropDownList)
            Dim txtDocumentNo As TextBox = TryCast(row.FindControl("txtDocumentNo"), TextBox)
            If txtDocumentNo Is Nothing Then Continue For

            Dim txtTitle As TextBox = TryCast(row.FindControl("txtTitle"), TextBox)
            Dim txtHours As TextBox = TryCast(row.FindControl("txtHours"), TextBox)
            Dim txtRemarks As TextBox = TryCast(row.FindControl("txtRemarks"), TextBox)
            Dim txtSoftware As TextBox = TryCast(row.FindControl("txtSoftware"), TextBox)
            Dim ddlStatus As DropDownList = TryCast(row.FindControl("ddlStatus"), DropDownList)
            Dim lblCriticality As Label = TryCast(row.FindControl("lblCriticality"), Label)
            Dim lblHoReq As Label = TryCast(row.FindControl("lblHoReq"), Label)

            Dim dr As DataRow = dt.NewRow()
            dr("CTD_ID") = Val(lblContext.Text)
            dr("DDR_ID") = grdDDREntry.DataKeys(row.RowIndex).Value
            dr("PLIP_ID") = If(txtPLIP?.Text, "").Trim()
            dr("RAMZ_ID") = If(ddlRamz?.SelectedValue, "")
            dr("Document_No") = txtDocumentNo.Text.Trim()
            dr("Document_Title") = If(txtTitle?.Text, "").Trim()
            dr("Man_Hours") = If(txtHours?.Text, "").Trim()
            dr("Disc_Remarks") = If(txtRemarks?.Text, "").Trim()
            dr("CRITICALITY") = If(lblCriticality?.Text, "")
            dr("HO_REQ") = If(lblHoReq?.Text, "")
            dr("Software") = If(txtSoftware?.Text, "").Trim()
            dr("HO_STATUS") = If(ddlStatus?.SelectedValue, "")
            dt.Rows.Add(dr)
        Next

        Return dt
    End Function

#End Region

#Region "DDR entry grid: data binding"

    Protected Sub grdDDREntry_RowDataBound(sender As Object, e As GridViewRowEventArgs)
        If e.Row.RowType <> DataControlRowType.DataRow Then Return

        Dim ddlRamz As DropDownList = CType(e.Row.FindControl("ddlRamz"), DropDownList)
        Dim drv As DataRowView = CType(e.Row.DataItem, DataRowView)
        Dim ramzValue As String = drv("RAMZ_ID").ToString().Trim()
        If ddlRamz.Items.FindByValue(ramzValue) Is Nothing AndAlso ramzValue <> "" Then
            ddlRamz.Items.Add(ramzValue)
        End If
        ddlRamz.SelectedValue = ramzValue

        Dim ddlStatus As DropDownList = CType(e.Row.FindControl("ddlStatus"), DropDownList)
        Dim status As String = DataBinder.Eval(e.Row.DataItem, "HO_STATUS").ToString()
        If ddlStatus.Items.FindByValue(status) Is Nothing AndAlso status <> "" Then
            ddlStatus.Items.Add(status)
        End If
        ddlStatus.SelectedValue = status

        Dim ddlType As DropDownList = CType(e.Row.FindControl("ddlType"), DropDownList)
        Dim ddrId As Integer
        Integer.TryParse(DataBinder.Eval(e.Row.DataItem, "DDR_ID").ToString(), ddrId)
        ddlType.Visible = (ddrId = 0)

        ' ddlArea has its own DataSourceID, which defers its DataBind() (and
        ' the evaluation of any <%# %> expression declared on it) to
        ' PreRender - by then the GridView row's DataItem context is gone, so
        ' an Eval()-based "Enabled" attribute directly on that control throws
        ' "Databinding methods such as Eval()... can only be used in the
        ' context of a databound control." and crashes the whole page. Set it
        ' here instead, for every row (not just unsaved ones, since a saved
        ' "ACTIVITY" row needs it disabled too).
        Dim documentNo As String = DataBinder.Eval(e.Row.DataItem, "Document_No").ToString()
        Dim isActivity As Boolean = documentNo.ToUpperInvariant().Contains("ACTIVITY")
        Dim ddlArea As DropDownList = CType(e.Row.FindControl("ddlArea"), DropDownList)
        ddlArea.Enabled = Not isActivity

        If ddrId <> 0 Then Return

        If isActivity Then
            ' Activity rows (added via "DDR + Activity") carry no PLIP/document
            ' defaults - Document_No must stay exactly "ACTIVITY" since the
            ' markup's Enabled/Text bindings for PLIP and Area key off it.
            ddlType.SelectedValue = "ACTIVITY"
            Return
        End If

        ' New, unsaved row: pre-fill from the most common PLIP/document used on
        ' this deliverable, mirroring the original "smart defaults" behaviour.
        Dim docMode As String = If(Session("Doc_Mode"), "").ToString()
        Dim slPrt As String
        If docMode.ToUpper().Contains("CHANGE") Then
            ddlType.SelectedValue = "EXISTING" : slPrt = "MASTR"
        Else
            ddlType.SelectedValue = "NEW" : slPrt = GetNextDummySuffix()
        End If

        Dim delRef As String = If(Session("Del_Ref"), "").ToString()

        Dim txtPLIP As TextBox = CType(e.Row.FindControl("txtPLIP"), TextBox)
        txtPLIP.Text = ExecScalarQuery(
            "SELECT TOP 1 b.PLIP_ID FROM CTD_MASTER a " &
            "INNER JOIN CTD_DDR_DISC b ON a.CTD_ID = b.CTD_ID " &
            "WHERE a.Del_Item_Ref = @DELREF GROUP BY b.PLIP_ID ORDER BY COUNT(*) DESC",
            New SqlParameter("@DELREF", delRef))

        ApplyPlipLookup(e.Row, txtPLIP.Text)

        Dim txtSoftware As TextBox = CType(e.Row.FindControl("txtSoftware"), TextBox)
        txtSoftware.Text = ExecScalarQuery(
            "SELECT TOP 1 [SOFTWARE] FROM CTD_DDR_DISC WHERE PLIP_ID = @PLIP GROUP BY SOFTWARE ORDER BY COUNT(*) DESC",
            New SqlParameter("@PLIP", txtPLIP.Text))

        Dim txtTitle As TextBox = CType(e.Row.FindControl("txtTitle"), TextBox)
        txtTitle.Text = ExecScalarQuery(
            "SELECT TOP 1 [DOCUMENT_TITLE] FROM CTD_DDR_DISC WHERE PLIP_ID = @PLIP GROUP BY [DOCUMENT_TITLE] ORDER BY COUNT(*) DESC",
            New SqlParameter("@PLIP", txtPLIP.Text))

        Dim docNo As String = ExecScalarQuery(
            "SELECT TOP 1 RIGHT([Document_No],25) FROM SDDR_CTD_DDR_001 " &
            "WHERE PLIP_ID = @PLIP AND PROJECT_NO LIKE @PROJPREFIX " &
            "GROUP BY RIGHT([Document_No],25) ORDER BY COUNT(*) DESC",
            New SqlParameter("@PLIP", txtPLIP.Text),
            New SqlParameter("@PROJPREFIX", Left(lblProject.Text, 1) & "%"))

        Dim parts() As String = docNo.Split("-"c)
        If parts.Length >= 4 Then parts(3) = slPrt
        Dim newDocNo As String = String.Join("-", parts)

        Dim txtArea As TextBox = CType(e.Row.FindControl("txtArea"), TextBox)
        Dim txtDoc As TextBox = CType(e.Row.FindControl("txtDocumentNo"), TextBox)
        If txtArea.Text.Trim().Length = 6 Then
            txtDoc.Text = txtArea.Text & "-" & newDocNo
        Else
            txtDoc.Text = "AAA-UU-" & newDocNo
        End If
    End Sub

    Protected Sub txtAREA_TextChanged(sender As Object, e As EventArgs)
        Dim txtArea As TextBox = CType(sender, TextBox)
        Dim row As GridViewRow = CType(txtArea.NamingContainer, GridViewRow)
        Dim txtDoc As TextBox = CType(row.FindControl("txtDocumentNo"), TextBox)

        If txtArea.Text.Trim().Length = 6 Then
            txtDoc.Text = txtDoc.Text.Replace("AAA-UU", txtArea.Text)
        End If
    End Sub

    Protected Sub ddlType_SelectedIndexChanged(sender As Object, e As EventArgs)
        Dim ddlType As DropDownList = CType(sender, DropDownList)
        Dim row As GridViewRow = CType(ddlType.NamingContainer, GridViewRow)

        Dim txtPLIP As TextBox = CType(row.FindControl("txtPLIP"), TextBox)
        Dim btnPLIPSearch As LinkButton = CType(row.FindControl("btnPLIPSearch"), LinkButton)
        Dim ddlArea As DropDownList = CType(row.FindControl("ddlArea"), DropDownList)
        Dim txtDocumentNo As TextBox = CType(row.FindControl("txtDocumentNo"), TextBox)

        Select Case ddlType.SelectedValue
            Case "ACTIVITY"
                txtPLIP.Text = ""
                txtPLIP.Enabled = False
                btnPLIPSearch.Visible = False
                ddlArea.Enabled = False
                ' Document_No must actually be "ACTIVITY" - it's the marker every
                ' other Enabled/Text binding and the save logic key off, not just
                ' a UI state. Manually switching Type here needs to set it too,
                ' matching what "DDR + Activity" already does when adding the row.
                txtDocumentNo.Text = "ACTIVITY"
            Case Else
                txtPLIP.Enabled = True
                btnPLIPSearch.Visible = True
                ddlArea.Enabled = True
                If txtDocumentNo.Text.Trim().ToUpperInvariant() = "ACTIVITY" Then
                    txtDocumentNo.Text = ""
                End If
        End Select
    End Sub

#End Region

#Region "Save"

    Protected Sub btnSaveAll_Click(sender As Object, e As EventArgs)
        Dim errors As New List(Of String)
        Dim rowsToInsert As New List(Of GridViewRow)
        Dim rowsToUpdate As New List(Of GridViewRow)

        For Each row As GridViewRow In grdDDREntry.Rows
            If row.RowType <> DataControlRowType.DataRow Then Continue For

            Dim txtDocumentNo As TextBox = TryCast(row.FindControl("txtDocumentNo"), TextBox)
            If txtDocumentNo Is Nothing Then Continue For

            Dim txtTitle As TextBox = TryCast(row.FindControl("txtTitle"), TextBox)
            Dim ddlRamz As DropDownList = TryCast(row.FindControl("ddlRamz"), DropDownList)
            Dim txtHours As TextBox = TryCast(row.FindControl("txtHours"), TextBox)
            Dim rowLabel As String = "Row " & (row.RowIndex + 1)

            If txtDocumentNo.Text.Trim() = "" Then
                errors.Add(rowLabel & ": Document Number is required.")
            End If
            If txtTitle.Text.Trim() = "" Then
                errors.Add(rowLabel & ": Document Title is required.")
            End If
            If ddlRamz.SelectedIndex < 0 OrElse ddlRamz.SelectedValue = "" Then
                errors.Add(rowLabel & ": RAMZ ID is required.")
            End If
            Dim hours As Decimal
            If txtHours.Text.Trim() <> "" AndAlso Not Decimal.TryParse(txtHours.Text.Trim(), hours) Then
                errors.Add(rowLabel & ": Hours must be numeric.")
            End If

            Dim ddrId As Integer = Convert.ToInt32(grdDDREntry.DataKeys(row.RowIndex).Value)
            If ddrId = 0 Then
                rowsToInsert.Add(row)
            Else
                rowsToUpdate.Add(row)
            End If
        Next

        If errors.Count > 0 Then
            ShowValidationErrors(errors)
            Return
        End If

        pnlValidationSummary.Visible = False

        Dim connStr As String = ST_Common.WorleyDataConnString
        Using conn As New SqlConnection(connStr)
            conn.Open()
            Dim tran As SqlTransaction = conn.BeginTransaction()
            Try
                For Each row In rowsToInsert
                    InsertDdrRow(conn, tran, row)
                Next
                For Each row In rowsToUpdate
                    UpdateDdrRow(conn, tran, row)
                Next
                tran.Commit()
            Catch ex As Exception
                tran.Rollback()
                ShowToast("error", "Save failed: " & ex.Message)
                Return
            End Try
        End Using

        LoadDdrGrid()
        ShowToast("success", "DDR records saved successfully.")
    End Sub

    Private Sub InsertDdrRow(conn As SqlConnection, tran As SqlTransaction, row As GridViewRow)
        Dim txtPLIP As TextBox = CType(row.FindControl("txtPLIP"), TextBox)
        Dim ddlRamz As DropDownList = CType(row.FindControl("ddlRamz"), DropDownList)
        Dim txtDocumentNo As TextBox = CType(row.FindControl("txtDocumentNo"), TextBox)
        Dim txtTitle As TextBox = CType(row.FindControl("txtTitle"), TextBox)
        Dim txtHours As TextBox = CType(row.FindControl("txtHours"), TextBox)
        Dim txtRemarks As TextBox = CType(row.FindControl("txtRemarks"), TextBox)
        Dim txtSoftware As TextBox = CType(row.FindControl("txtSoftware"), TextBox)
        Dim ddlStatus As DropDownList = CType(row.FindControl("ddlStatus"), DropDownList)
        Dim lblCriticality As Label = CType(row.FindControl("lblCriticality"), Label)
        Dim lblHoReq As Label = CType(row.FindControl("lblHoReq"), Label)

        Const sql As String = "
            INSERT INTO CTD_DDR_DISC
                (CTD_ID, PLIP_ID, RAMZ_ID, DOCUMENT_NO, DOCUMENT_TITLE, MAN_HOURS,
                 DISC_REMARKS, SOFTWARE, HO_STATUS, CRITICALITY, HO_REQ)
            VALUES
                (@CTD_ID, @PLIP_ID, @RAMZ_ID, @DOCUMENT_NO, @DOCUMENT_TITLE, @MAN_HOURS,
                 @DISC_REMARKS, @SOFTWARE, @HO_STATUS, @CRITICALITY, @HO_REQ)"

        Using cmd As New SqlCommand(sql, conn, tran)
            cmd.Parameters.AddWithValue("@CTD_ID", Val(lblContext.Text))
            cmd.Parameters.AddWithValue("@PLIP_ID", txtPLIP.Text.Trim())
            cmd.Parameters.AddWithValue("@RAMZ_ID", ddlRamz.SelectedValue)
            cmd.Parameters.AddWithValue("@DOCUMENT_NO", txtDocumentNo.Text.Trim())
            cmd.Parameters.AddWithValue("@DOCUMENT_TITLE", txtTitle.Text.Trim())
            cmd.Parameters.AddWithValue("@MAN_HOURS", If(txtHours.Text.Trim() = "", CType(0, Object), Convert.ToDecimal(txtHours.Text.Trim())))
            cmd.Parameters.AddWithValue("@DISC_REMARKS", txtRemarks.Text.Trim())
            cmd.Parameters.AddWithValue("@SOFTWARE", txtSoftware.Text.Trim())
            cmd.Parameters.AddWithValue("@HO_STATUS", ddlStatus.SelectedValue)
            cmd.Parameters.AddWithValue("@CRITICALITY", lblCriticality.Text)
            cmd.Parameters.AddWithValue("@HO_REQ", lblHoReq.Text)
            cmd.ExecuteNonQuery()
        End Using
    End Sub

    Private Sub UpdateDdrRow(conn As SqlConnection, tran As SqlTransaction, row As GridViewRow)
        Dim ddrId As Object = grdDDREntry.DataKeys(row.RowIndex).Value
        Dim txtPLIP As TextBox = CType(row.FindControl("txtPLIP"), TextBox)
        Dim ddlRamz As DropDownList = CType(row.FindControl("ddlRamz"), DropDownList)
        Dim txtDocumentNo As TextBox = CType(row.FindControl("txtDocumentNo"), TextBox)
        Dim txtTitle As TextBox = CType(row.FindControl("txtTitle"), TextBox)
        Dim txtHours As TextBox = CType(row.FindControl("txtHours"), TextBox)
        Dim txtRemarks As TextBox = CType(row.FindControl("txtRemarks"), TextBox)
        Dim txtSoftware As TextBox = CType(row.FindControl("txtSoftware"), TextBox)
        Dim ddlStatus As DropDownList = CType(row.FindControl("ddlStatus"), DropDownList)

        Const sql As String = "
            UPDATE CTD_DDR_DISC SET
                PLIP_ID = @PLIP_ID, RAMZ_ID = @RAMZ_ID, DOCUMENT_NO = @DOCUMENT_NO,
                DOCUMENT_TITLE = @DOCUMENT_TITLE, MAN_HOURS = @MAN_HOURS,
                DISC_REMARKS = @DISC_REMARKS, SOFTWARE = @SOFTWARE, HO_STATUS = @HO_STATUS
            WHERE DDR_ID = @DDR_ID"

        Using cmd As New SqlCommand(sql, conn, tran)
            cmd.Parameters.AddWithValue("@PLIP_ID", txtPLIP.Text.Trim())
            cmd.Parameters.AddWithValue("@RAMZ_ID", ddlRamz.SelectedValue)
            cmd.Parameters.AddWithValue("@DOCUMENT_NO", txtDocumentNo.Text.Trim())
            cmd.Parameters.AddWithValue("@DOCUMENT_TITLE", txtTitle.Text.Trim())
            cmd.Parameters.AddWithValue("@MAN_HOURS", If(txtHours.Text.Trim() = "", CType(0, Object), Convert.ToDecimal(txtHours.Text.Trim())))
            cmd.Parameters.AddWithValue("@DISC_REMARKS", txtRemarks.Text.Trim())
            cmd.Parameters.AddWithValue("@SOFTWARE", txtSoftware.Text.Trim())
            cmd.Parameters.AddWithValue("@HO_STATUS", ddlStatus.SelectedValue)
            cmd.Parameters.AddWithValue("@DDR_ID", ddrId)
            cmd.ExecuteNonQuery()
        End Using
    End Sub

    Private Sub ShowValidationErrors(errors As List(Of String))
        Dim sb As New StringBuilder("<ul>")
        For Each err In errors
            sb.Append("<li>").Append(HttpUtility.HtmlEncode(err)).Append("</li>")
        Next
        sb.Append("</ul>")
        litValidationErrors.Text = sb.ToString()
        pnlValidationSummary.Visible = True
        ShowToast("error", errors.Count.ToString() & " issue(s) need attention before saving.")
    End Sub

#End Region

#Region "CSV import / export"

    ''' <summary>
    ''' CSV import/export against CTD_DDR_DISC is intentionally limited to these
    ''' seven fields (per business requirement) - Software, Remarks, Criticality
    ''' and PLIP_HO are not part of the CSV contract and are left untouched by
    ''' both directions.
    ''' </summary>
    Private Shared ReadOnly CsvColumns() As String = {
        "CTD_ID", "Ramz_ID", "PLIP_ID", "Document_No", "Document_Title", "Man_Hours", "HO_Status"
    }

    ''' <summary>
    ''' Downloads the CSV header row only - a blank import template with just
    ''' the seven column names, no DDR line data.
    ''' </summary>
    Protected Sub CSV_Template_Click(sender As Object, e As EventArgs)
        Dim ctdId As Integer = Val(lblContext.Text)

        Response.Clear()
        Response.ContentType = "text/csv"
        Response.AddHeader("Content-Disposition", "attachment; filename=DDR_Import_Template_CTD_" & ctdId & ".csv")
        Response.Write(String.Join(",", CsvColumns) & vbCrLf)
        Response.Flush()
        HttpContext.Current.ApplicationInstance.CompleteRequest()
    End Sub

    Protected Sub CSV_Upload_Click(sender As Object, e As EventArgs)
        If Not fuCsv.HasFile Then
            ShowToast("error", "Choose a CSV file before uploading.")
            Return
        End If

        ' StringSplitOptions.None (rather than RemoveEmptyEntries) keeps blank
        ' lines in place so "Line N" in an error message below matches the
        ' actual line number in the uploaded file; blank lines are skipped
        ' individually inside the loop instead.
        Dim lines() As String
        Using reader As New StreamReader(fuCsv.PostedFile.InputStream)
            lines = reader.ReadToEnd().Split({Environment.NewLine, vbLf}, StringSplitOptions.None)
        End Using

        Dim defaultCtdId As Integer = Val(lblContext.Text)
        Dim imported As Integer = 0
        Dim rowErrors As New List(Of String)

        Using conn As New SqlConnection(ST_Common.WorleyDataConnString)
            conn.Open()
            Dim tran As SqlTransaction = conn.BeginTransaction()
            Try
                For i As Integer = 1 To lines.Length - 1
                    If String.IsNullOrWhiteSpace(lines(i)) Then Continue For

                    Dim fields = ParseCsvLine(lines(i))
                    If fields.Count < CsvColumns.Length Then
                        rowErrors.Add("Line " & (i + 1) & ": expected " & CsvColumns.Length & " columns, found " & fields.Count & ".")
                        Continue For
                    End If

                    Dim ctdIdText = fields(0).Trim()
                    Dim ramzId = fields(1).Trim()
                    Dim plipId = fields(2).Trim()
                    Dim documentNo = fields(3).Trim()
                    Dim documentTitle = fields(4).Trim()
                    Dim manHoursText = fields(5).Trim()
                    Dim hoStatus = fields(6).Trim().ToUpperInvariant()

                    ' CTD_ID is optional per row: falls back to the CTD currently open on the page.
                    Dim ctdId As Integer = defaultCtdId
                    If ctdIdText <> "" AndAlso Not Integer.TryParse(ctdIdText, ctdId) Then
                        rowErrors.Add("Line " & (i + 1) & ": CTD_ID must be numeric.")
                        Continue For
                    End If

                    If ctdId <= 0 OrElse documentNo = "" OrElse documentTitle = "" OrElse ramzId = "" Then
                        rowErrors.Add("Line " & (i + 1) & ": CTD_ID, Document_No, Document_Title and RAMZ_ID are required.")
                        Continue For
                    End If

                    Dim manHours As Decimal = 0
                    If manHoursText <> "" AndAlso Not Decimal.TryParse(manHoursText, manHours) Then
                        rowErrors.Add("Line " & (i + 1) & ": Man_Hours must be numeric.")
                        Continue For
                    End If

                    If hoStatus <> "" AndAlso hoStatus <> "AFC" AndAlso hoStatus <> "APP" Then
                        rowErrors.Add("Line " & (i + 1) & ": HO_Status must be AFC or APP (or blank).")
                        Continue For
                    End If

                    Const sql As String = "
                        INSERT INTO CTD_DDR_DISC
                            (CTD_ID, RAMZ_ID, PLIP_ID, DOCUMENT_NO, DOCUMENT_TITLE, MAN_HOURS, HO_STATUS)
                        VALUES
                            (@CTD_ID, @RAMZ_ID, @PLIP_ID, @DOCUMENT_NO, @DOCUMENT_TITLE, @MAN_HOURS, @HO_STATUS)"

                    Using cmd As New SqlCommand(sql, conn, tran)
                        cmd.Parameters.AddWithValue("@CTD_ID", ctdId)
                        cmd.Parameters.AddWithValue("@RAMZ_ID", ramzId)
                        cmd.Parameters.AddWithValue("@PLIP_ID", plipId)
                        cmd.Parameters.AddWithValue("@DOCUMENT_NO", documentNo)
                        cmd.Parameters.AddWithValue("@DOCUMENT_TITLE", documentTitle)
                        cmd.Parameters.AddWithValue("@MAN_HOURS", manHours)
                        cmd.Parameters.AddWithValue("@HO_STATUS", hoStatus)
                        cmd.ExecuteNonQuery()
                    End Using
                    imported += 1
                Next

                If rowErrors.Count > 0 Then
                    tran.Rollback()
                    ShowValidationErrors(rowErrors)
                    Return
                End If

                If imported = 0 Then
                    tran.Rollback()
                    ShowToast("error", "The CSV file has no data rows.")
                    Return
                End If

                tran.Commit()
            Catch ex As Exception
                tran.Rollback()
                ShowToast("error", "CSV import failed: " & ex.Message)
                Return
            End Try
        End Using

        LoadDdrGrid()
        ShowToast("success", imported.ToString() & " DDR line(s) imported from CSV.")
    End Sub

    ''' <summary>Minimal CSV splitter that understands double-quoted fields containing commas.</summary>
    Private Function ParseCsvLine(line As String) As List(Of String)
        Dim fields As New List(Of String)
        Dim current As New StringBuilder()
        Dim inQuotes As Boolean = False

        For i As Integer = 0 To line.Length - 1
            Dim c As Char = line(i)
            If inQuotes Then
                If c = """"c Then
                    If i + 1 < line.Length AndAlso line(i + 1) = """"c Then
                        current.Append(""""c)
                        i += 1
                    Else
                        inQuotes = False
                    End If
                Else
                    current.Append(c)
                End If
            Else
                Select Case c
                    Case ","c
                        fields.Add(current.ToString())
                        current.Clear()
                    Case """"c
                        inQuotes = True
                    Case Else
                        current.Append(c)
                End Select
            End If
        Next
        fields.Add(current.ToString())
        Return fields
    End Function

#End Region

#Region "Shared data helpers"

    Private Function ExecScalarQuery(sql As String, ParamArray parameters As SqlParameter()) As String
        Using con As New SqlConnection(ST_Common.WorleyDataConnString)
            Using cmd As New SqlCommand(sql, con)
                If parameters IsNot Nothing Then cmd.Parameters.AddRange(parameters)
                con.Open()
                Dim result = cmd.ExecuteScalar()
                Return If(result Is Nothing OrElse result Is DBNull.Value, "", result.ToString())
            End Using
        End Using
    End Function

    ''' <summary>
    ''' Formats a dummy-document sequence number as DUM01..DUM99, then rolls
    ''' over to DU100 onward once the two-digit run is exhausted.
    ''' </summary>
    Private Function FormatDummySuffix(n As Integer) As String
        If n <= 99 Then Return "DUM" & n.ToString("00")
        Return "DU" & n.ToString("000")
    End Function

    ''' <summary>
    ''' Scans every DDR row currently rendered in grdDDREntry (saved and
    ''' unsaved alike) for a Document_No whose 4th "-"-delimited segment
    ''' already follows the DUM01..DUM99 / DU100+ pattern, and returns the
    ''' highest number found (0 if none).
    ''' </summary>
    Private Function GetCurrentMaxDummyNumber() As Integer
        Dim maxN As Integer = 0
        For Each row As GridViewRow In grdDDREntry.Rows
            If row.RowType <> DataControlRowType.DataRow Then Continue For
            Dim txtDocumentNo As TextBox = TryCast(row.FindControl("txtDocumentNo"), TextBox)
            If txtDocumentNo Is Nothing Then Continue For

            Dim parts() As String = txtDocumentNo.Text.Split("-"c)
            If parts.Length < 4 Then Continue For
            Dim segment As String = parts(3)

            Dim mTwoDigit As System.Text.RegularExpressions.Match =
                System.Text.RegularExpressions.Regex.Match(segment, "^DUM(\d{2})$")
            Dim mThreeDigit As System.Text.RegularExpressions.Match =
                System.Text.RegularExpressions.Regex.Match(segment, "^DU(\d{3})$")

            If mTwoDigit.Success Then
                maxN = Math.Max(maxN, Integer.Parse(mTwoDigit.Groups(1).Value))
            ElseIf mThreeDigit.Success Then
                maxN = Math.Max(maxN, Integer.Parse(mThreeDigit.Groups(1).Value))
            End If
        Next
        Return maxN
    End Function

    ''' <summary>The next DUM/DU suffix to assign, one past whatever's already in use.</summary>
    Private Function GetNextDummySuffix() As String
        Return FormatDummySuffix(GetCurrentMaxDummyNumber() + 1)
    End Function

    ''' <summary>The current CTD's total allocated hours, straight from CTD_MASTER.</summary>
    Private Function GetCurrentCtdTotalHours() As Decimal
        Dim ctdId As Integer = Val(lblContext.Text)
        Dim result As Decimal = 0
        Decimal.TryParse(
            ExecScalarQuery("SELECT Total_Hours FROM CTD_MASTER WHERE CTD_ID = @CTD_ID", New SqlParameter("@CTD_ID", ctdId)),
            result)
        Return result
    End Function

    Private Function GetPlipStatus(plipId As String) As (Afc As String, Critical As String, FhoStatus As String)
        If String.IsNullOrWhiteSpace(plipId) Then Return ("", "", "")

        Const sql As String = "
            SELECT
                CASE WHEN [Required_Handover_Status] = 'ASB' THEN 'AFC' ELSE 'APP' END AS AFCAPP,
                [Critical_Documentation],
                [Required_Handover_Status]
            FROM [ACAD_DATA].[dbo].[OPS-Z02_PLIP]
            WHERE PLIP_ID = @PLIP_ID"

        Using con As New SqlConnection(ST_Common.WorleyDataConnString)
            Using cmd As New SqlCommand(sql, con)
                cmd.Parameters.AddWithValue("@PLIP_ID", plipId)
                con.Open()
                Using rd As SqlDataReader = cmd.ExecuteReader()
                    If rd.Read() Then
                        Return (rd("AFCAPP").ToString(), rd("Critical_Documentation").ToString(), rd("Required_Handover_Status").ToString())
                    End If
                End Using
            End Using
        End Using
        Return ("", "", "")
    End Function

    Private Sub ShowToast(type As String, message As String)
        Dim script As String = "showToast(" &
            HttpUtility.JavaScriptStringEncode(type, True) & "," &
            HttpUtility.JavaScriptStringEncode(message, True) & ");"
        ScriptManager.RegisterStartupScript(Me, Me.GetType(), "Toast" & Guid.NewGuid().ToString("N"), script, True)
    End Sub

#End Region

End Class

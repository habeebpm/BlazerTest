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
        Dim orderDirection As String = "ASC"
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
        ScriptManager.RegisterStartupScript(Me, Me.GetType(), "OpenCTDDrawer", "openCTDDrawer();", True)
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

        If ctdHrs <> ddrHrs Then
            e.Row.CssClass = "badge-warn"
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
        ScriptManager.RegisterStartupScript(Me, Me.GetType(), "OpenPLIP", "openPLIPDrawer();", True)
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

        If ddrId <> 0 Then Return

        ' New, unsaved row: pre-fill from the most common PLIP/document used on
        ' this deliverable, mirroring the original "smart defaults" behaviour.
        Dim docMode As String = If(Session("Doc_Mode"), "").ToString()
        Dim slPrt As String
        If docMode.ToUpper().Contains("CHANGE") Then
            ddlType.SelectedValue = "EXISTING" : slPrt = "MASTR"
        Else
            ddlType.SelectedValue = "NEW" : slPrt = "DUMMY"
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

        Select Case ddlType.SelectedValue
            Case "ACTIVITY"
                txtPLIP.Text = ""
                txtPLIP.Enabled = False
                btnPLIPSearch.Visible = False
                ddlArea.Enabled = False
            Case Else
                txtPLIP.Enabled = True
                btnPLIPSearch.Visible = True
                ddlArea.Enabled = True
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

            Dim ddrId As Integer
            Integer.TryParse(grdDDREntry.DataKeys(row.RowIndex).Value?.ToString(), ddrId)
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
    ''' Exports the current CTD's DDR lines (limited to the seven CSV columns).
    ''' With no saved lines yet, this downloads just the header row, which
    ''' doubles as an import template.
    ''' </summary>
    Protected Sub CSV_Template_Click(sender As Object, e As EventArgs)
        Dim ctdId As Integer = Val(lblContext.Text)

        Dim sb As New StringBuilder()
        sb.AppendLine(String.Join(",", CsvColumns))

        Using con As New SqlConnection(ST_Common.WorleyDataConnString)
            Using cmd As New SqlCommand(
                "SELECT CTD_ID, RAMZ_ID, PLIP_ID, DOCUMENT_NO, DOCUMENT_TITLE, MAN_HOURS, HO_STATUS " &
                "FROM CTD_DDR_DISC WHERE CTD_ID = @CTD_ID ORDER BY DDR_ID", con)
                cmd.Parameters.AddWithValue("@CTD_ID", ctdId)
                con.Open()
                Using rd As SqlDataReader = cmd.ExecuteReader()
                    While rd.Read()
                        sb.AppendLine(String.Join(",", {
                            EscapeCsvField(rd("CTD_ID").ToString()),
                            EscapeCsvField(rd("RAMZ_ID").ToString()),
                            EscapeCsvField(rd("PLIP_ID").ToString()),
                            EscapeCsvField(rd("DOCUMENT_NO").ToString()),
                            EscapeCsvField(rd("DOCUMENT_TITLE").ToString()),
                            EscapeCsvField(rd("MAN_HOURS").ToString()),
                            EscapeCsvField(rd("HO_STATUS").ToString())
                        }))
                    End While
                End Using
            End Using
        End Using

        Response.Clear()
        Response.ContentType = "text/csv"
        Response.AddHeader("Content-Disposition", "attachment; filename=DDR_Export_CTD_" & ctdId & ".csv")
        Response.Write(sb.ToString())
        Response.Flush()
        HttpContext.Current.ApplicationInstance.CompleteRequest()
    End Sub

    ''' <summary>Wraps a CSV field in quotes and escapes embedded quotes when needed.</summary>
    Private Function EscapeCsvField(value As String) As String
        If value Is Nothing Then Return ""
        If value.IndexOfAny({","c, """"c, ControlChars.Cr, ControlChars.Lf}) >= 0 Then
            Return """" & value.Replace("""", """""") & """"
        End If
        Return value
    End Function

    Protected Sub CSV_Upload_Click(sender As Object, e As EventArgs)
        If Not fuCsv.HasFile Then
            ShowToast("error", "Choose a CSV file before uploading.")
            Return
        End If

        Dim lines() As String
        Using reader As New StreamReader(fuCsv.PostedFile.InputStream)
            lines = reader.ReadToEnd().Split({Environment.NewLine, vbLf}, StringSplitOptions.RemoveEmptyEntries)
        End Using

        If lines.Length <= 1 Then
            ShowToast("error", "The CSV file has no data rows.")
            Return
        End If

        Dim defaultCtdId As Integer = Val(lblContext.Text)
        Dim imported As Integer = 0
        Dim rowErrors As New List(Of String)

        Using conn As New SqlConnection(ST_Common.WorleyDataConnString)
            conn.Open()
            Dim tran As SqlTransaction = conn.BeginTransaction()
            Try
                For i As Integer = 1 To lines.Length - 1
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

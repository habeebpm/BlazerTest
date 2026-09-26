Imports System.Data
Imports System.Data.SqlClient
Imports System.IO
Imports System.Text
Imports System.Text.RegularExpressions
Imports System.Web
Imports System.Web.UI
Imports System.Web.UI.WebControls

Public Class DDR_DeveloperV3
    Inherits System.Web.UI.Page

#Region "Rules and constants"

    ''' <summary>Placeholder for the 6-character area code at the start of a suggested document number.</summary>
    Private Const AreaPlaceholder As String = "AAA-UU"

    ''' <summary>
    ''' A dummy (placeholder) document serial: one "-"-separated segment of the form
    ''' DUM01..DUM99, then DU100, DU101 ... - i.e. every dummy serial matches LIKE 'DU%'
    ''' and is recognised by SmartDDR's Dummy Serials check.
    ''' </summary>
    Private Shared ReadOnly DummySerialRx As New Regex("^DU(?:M(?<n>\d{2})|(?<n>\d{3,}))$", RegexOptions.IgnoreCase Or RegexOptions.Compiled)

    ''' <summary>Legacy placeholders that new documents must not use any more (use a DU serial instead).</summary>
    Private Shared ReadOnly LegacyDummyRx As New Regex("(^|-)DUMMY(-|$)|XXX", RegexOptions.IgnoreCase Or RegexOptions.Compiled)

    ''' <summary>A segment a suggested/copied number's serial can be written into.</summary>
    Private Shared ReadOnly SerialSlotRx As New Regex("^(DU(M\d{2}|\d{3,})|DUMMY|MASTR)$|XXX", RegexOptions.IgnoreCase Or RegexOptions.Compiled)

    ''' <summary>CSV import/export is limited to these seven fields (business requirement).</summary>
    Private Shared ReadOnly CsvColumns() As String = {
        "CTD_ID", "Ramz_ID", "PLIP_ID", "Document_No", "Document_Title", "Man_Hours", "HO_Status"
    }

    Private _ramzList As List(Of String)
    Private _areaList As List(Of String)

#End Region

#Region "Page lifecycle"

    Protected Sub Page_Load(ByVal sender As Object, ByVal e As System.EventArgs) Handles Me.Load
        If Not IsPostBack Then
            Dim ctdId As Integer
            Integer.TryParse(Request.QueryString("CTD_ID"), ctdId)

            ' The CTD context (project, discipline, deliverable ref, doc mode) must be
            ' known before the DDR grid binds: its RAMZ/Area lists and the defaults for
            ' new rows depend on it. It is kept in hidden labels (ViewState), per page,
            ' rather than in Session, so two tabs on different CTDs don't mix.
            LoadCtdContext(ctdId)
            LoadDdrGrid()
        End If
    End Sub

    Private Sub LoadCtdContext(ctdId As Integer)
        lblContext.Text = ctdId.ToString()
        lblProject.Text = "" : lblDiscipline.Text = "" : lblRef.Text = "" : lblDocMode.Text = ""

        Using con As New SqlConnection(ST_Common.WorleyDataConnString)
            Using cmd As New SqlCommand(
                "SELECT TOP 1 [Project_No], [Discipline], [Del_Item_Ref], [Doc_Mode] FROM CTD_MASTER WHERE CTD_ID = @CTD_ID", con)
                cmd.Parameters.AddWithValue("@CTD_ID", ctdId)
                con.Open()
                Using rd As SqlDataReader = cmd.ExecuteReader()
                    If rd.Read() Then
                        lblProject.Text = Convert.ToString(rd("Project_No")).Trim()
                        lblDiscipline.Text = Convert.ToString(rd("Discipline")).Trim()
                        lblRef.Text = Convert.ToString(rd("Del_Item_Ref")).Trim()
                        lblDocMode.Text = Convert.ToString(rd("Doc_Mode")).Trim()
                    End If
                End Using
            End Using
        End Using

        litProject.Text = HttpUtility.HtmlEncode(lblProject.Text)
        litDiscipline.Text = HttpUtility.HtmlEncode(lblDiscipline.Text)
        litRef.Text = HttpUtility.HtmlEncode(lblRef.Text)
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
    ''' Opens the previous/next CTD (by ID) in the same project and discipline as
    ''' the open one. Next = smallest ID above the current (ASC); Prev = largest ID
    ''' below it (DESC).
    ''' </summary>
    Private Sub NavigateToAdjacentCtd(goForward As Boolean)
        Dim currentCtdId As Integer = CInt(Val(lblContext.Text))
        If currentCtdId = 0 Then Exit Sub

        Dim sql As String = "SELECT TOP 1 CTD_ID FROM CTD_MASTER " &
            "WHERE CTD_ID " & If(goForward, ">", "<") & " @CurrentID " &
            "AND [Discipline] = @Discipline AND [Project_No] = @Project " &
            "ORDER BY CTD_ID " & If(goForward, "ASC", "DESC")

        Dim nextId As String = ExecScalarQuery(sql,
            New SqlParameter("@CurrentID", currentCtdId),
            New SqlParameter("@Discipline", lblDiscipline.Text),
            New SqlParameter("@Project", lblProject.Text))

        Dim parsedId As Integer
        If Integer.TryParse(nextId, parsedId) AndAlso parsedId > 0 Then
            Response.Redirect(Request.Path & "?CTD_ID=" & parsedId, False)
            Context.ApplicationInstance.CompleteRequest()
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
        ' Context labels are filled by LoadCtdContext before any grid binds;
        ' nothing to do per row.
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
            ' No widely supported "green flag" glyph, so a check mark stands in for "matching".
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
        ' PLIPSource's SelectCommand is parameterized against txtSearchPLIP.
        gvPLIPSearch.DataBind()
    End Sub

    ''' <summary>
    ''' Picking a result updates grdDDREntry and the PLIP footer, which are outside
    ''' the drawer's UpdatePanel - so the pick must be a full postback, otherwise
    ''' the change is never rendered and is lost on the next postback.
    ''' </summary>
    Protected Sub gvPLIPSearch_RowCreated(sender As Object, e As GridViewRowEventArgs)
        If e.Row.RowType <> DataControlRowType.DataRow Then Return
        Dim lnk As LinkButton = TryCast(e.Row.FindControl("lnkPLIP"), LinkButton)
        Dim sm As ScriptManager = ScriptManager.GetCurrent(Me)
        If lnk IsNot Nothing AndAlso sm IsNot Nothing Then sm.RegisterPostBackControl(lnk)
    End Sub

    Protected Sub gvPLIPSearch_RowCommand(sender As Object, e As GridViewCommandEventArgs)
        If e.CommandName <> "SelectPLIP" Then Return

        Dim plipId As String = e.CommandArgument.ToString()
        Dim applied As Boolean = False

        Dim rowIndex As Integer
        If Integer.TryParse(hfSelectedRow.Value, rowIndex) AndAlso
           rowIndex >= 0 AndAlso rowIndex < grdDDREntry.Rows.Count Then

            Dim targetRow As GridViewRow = grdDDREntry.Rows(rowIndex)
            Dim txtPLIP As TextBox = TryCast(targetRow.FindControl("txtPLIP"), TextBox)
            If txtPLIP IsNot Nothing AndAlso txtPLIP.Enabled Then
                txtPLIP.Text = plipId
                ApplyPlipLookup(targetRow, plipId)
                applied = True
            End If
        End If

        ScriptManager.RegisterStartupScript(Me, Me.GetType(), "KeepDrawerOpen", "openPLIPDrawer();", True)
        If applied Then
            ShowToast("success", "PLIP " & plipId & " applied to row " & (rowIndex + 1) & ".")
        Else
            ShowToast("info", "To put a PLIP on a line, open the search from that line's 🔍 button.")
        End If
        ShowPlipDetailFooter(plipId)
    End Sub

    ''' <summary>
    ''' Refreshes a DDR row's Critical/PLIP_HO labels and AFC/APP status from the
    ''' PLIP master data, after either a manual PLIP edit or a drawer selection.
    ''' Returns False when the PLIP is not an active PLIP (the labels are cleared
    ''' and the AFC/APP choice is left as it was).
    ''' </summary>
    Private Function ApplyPlipLookup(row As GridViewRow, plipId As String) As Boolean
        Dim status = GetPlipStatus(plipId)

        Dim lblCriticality As Label = TryCast(row.FindControl("lblCriticality"), Label)
        Dim lblHoReq As Label = TryCast(row.FindControl("lblHoReq"), Label)
        Dim ddlStatus As DropDownList = TryCast(row.FindControl("ddlStatus"), DropDownList)

        SetLabel(lblCriticality, status.Critical)
        SetLabel(lblHoReq, status.FhoStatus)
        If status.Found AndAlso ddlStatus IsNot Nothing Then
            SelectOrAdd(ddlStatus, status.Afc)
        End If
        Return status.Found
    End Function

    Protected Sub txtPLIP_TextChanged(sender As Object, e As EventArgs)
        Dim txtPLIP As TextBox = CType(sender, TextBox)
        Dim row As GridViewRow = CType(txtPLIP.NamingContainer, GridViewRow)
        Dim plipId As String = txtPLIP.Text.Trim()
        txtPLIP.Text = plipId

        Dim found As Boolean = ApplyPlipLookup(row, plipId)
        If plipId <> "" AndAlso Not found Then
            ShowToast("error", "Row " & (row.RowIndex + 1) & ": " & plipId & " is not an active PLIP.")
        End If
        ShowPlipDetailFooter(plipId)
    End Sub

    ''' <summary>
    ''' Shows the picked PLIP's descriptive fields in the footer drawer. Leaves the
    ''' footer alone on a blank ID; closes it if the ID doesn't resolve.
    ''' </summary>
    Private Sub ShowPlipDetailFooter(plipId As String)
        If String.IsNullOrWhiteSpace(plipId) Then Return

        Const sql As String = "
            SELECT [PLIP_ID],[Information_Required] AS [PLIP_Title],
                   [Discipline_Code]+'-'+[Document_Type_Name] AS [Doc_Type],
                   [Critical_Documentation] AS [Critical],
                   [Required_Handover_Status] AS [FHO_Status],
                   [DCAFID_Latest] AS [DCAF]
            FROM [ACAD_DATA].[dbo].[SPO_PLIP]
            WHERE [Active_YN]='YES' AND [PLIP_ID] = @PLIP_ID"

        Using con As New SqlConnection(ST_Common.WorleyDataConnString)
            Using cmd As New SqlCommand(sql, con)
                cmd.Parameters.AddWithValue("@PLIP_ID", plipId)
                con.Open()
                Using rd As SqlDataReader = cmd.ExecuteReader()
                    If Not rd.Read() Then
                        ScriptManager.RegisterStartupScript(Me, Me.GetType(), "ClosePlipFooter", "closeFooterDrawer();", True)
                        Return
                    End If

                    litFooterPlipId.Text = HttpUtility.HtmlEncode(rd("PLIP_ID").ToString())
                    litFooterTitle.Text = HttpUtility.HtmlEncode(rd("PLIP_Title").ToString())
                    litFooterDocType.Text = HttpUtility.HtmlEncode(rd("Doc_Type").ToString())
                    litFooterCritical.Text = HttpUtility.HtmlEncode(rd("Critical").ToString())
                    litFooterHo.Text = HttpUtility.HtmlEncode(rd("FHO_Status").ToString())
                    litFooterDcaf.Text = HttpUtility.HtmlEncode(rd("DCAF").ToString())
                End Using
            End Using
        End Using

        ScriptManager.RegisterStartupScript(Me, Me.GetType(), "OpenPlipFooter", "openFooterDrawer();", True)
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
        ' Type chosen for an unsaved row (NEW / EXISTING / ACTIVITY); not stored in the database.
        dt.Columns.Add("DocType", GetType(String))
        Return dt
    End Function

    ''' <summary>
    ''' Adds an unsaved row. Document rows are pre-filled once, here, with the
    ''' "smart defaults" (most common PLIP for this deliverable, its title and
    ''' software, and a suggested document number carrying the next DU serial).
    ''' Defaults are never re-applied on later rebinds, so values the user has
    ''' typed - and Multiplier copies - are kept.
    ''' </summary>
    Private Function AddBlankDdrRow(dt As DataTable, documentNo As String) As DataRow
        Dim dr As DataRow = dt.NewRow()
        dr("CTD_ID") = CInt(Val(lblContext.Text))
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
        dr("DocType") = If(IsActivityDoc(documentNo), "ACTIVITY", "NEW")

        If Not IsActivityDoc(documentNo) Then ApplyNewRowDefaults(dt, dr)

        dt.Rows.Add(dr)
        Return dr
    End Function

    Private Sub ApplyNewRowDefaults(dt As DataTable, dr As DataRow)
        Dim isChange As Boolean = lblDocMode.Text.ToUpperInvariant().Contains("CHANGE")
        dr("DocType") = If(isChange, "EXISTING", "NEW")
        Dim serial As String = If(isChange, "MASTR", FormatDummySuffix(MaxDummyNumber(dt) + 1))

        Dim plipId As String = ExecScalarQuery(
            "SELECT TOP 1 b.PLIP_ID FROM CTD_MASTER a " &
            "INNER JOIN CTD_DDR_DISC b ON a.CTD_ID = b.CTD_ID " &
            "WHERE a.Del_Item_Ref = @DELREF AND ISNULL(b.PLIP_ID, '') <> '' " &
            "GROUP BY b.PLIP_ID ORDER BY COUNT(*) DESC",
            New SqlParameter("@DELREF", lblRef.Text)).Trim()

        Dim tail As String = ""
        If plipId <> "" Then
            dr("PLIP_ID") = plipId
            Dim status = GetPlipStatus(plipId)
            If status.Found Then
                dr("CRITICALITY") = status.Critical
                dr("HO_REQ") = status.FhoStatus
                dr("HO_STATUS") = status.Afc
            End If

            dr("Software") = ExecScalarQuery(
                "SELECT TOP 1 [SOFTWARE] FROM CTD_DDR_DISC WHERE PLIP_ID = @PLIP GROUP BY SOFTWARE ORDER BY COUNT(*) DESC",
                New SqlParameter("@PLIP", plipId))

            dr("Document_Title") = ExecScalarQuery(
                "SELECT TOP 1 [DOCUMENT_TITLE] FROM CTD_DDR_DISC WHERE PLIP_ID = @PLIP GROUP BY [DOCUMENT_TITLE] ORDER BY COUNT(*) DESC",
                New SqlParameter("@PLIP", plipId))

            tail = ExecScalarQuery(
                "SELECT TOP 1 RIGHT([Document_No],25) FROM SDDR_CTD_DDR_001 " &
                "WHERE PLIP_ID = @PLIP AND PROJECT_NO LIKE @PROJPREFIX " &
                "GROUP BY RIGHT([Document_No],25) ORDER BY COUNT(*) DESC",
                New SqlParameter("@PLIP", plipId),
                New SqlParameter("@PROJPREFIX", Left(lblProject.Text, 1) & "%")).Trim()
        End If

        dr("Document_No") = BuildDefaultDocNo(tail, serial)
    End Sub

    ''' <summary>
    ''' Suggested number = area placeholder + the most common number pattern for the
    ''' PLIP, with its serial segment replaced: an existing DU / DUMMY / XXX / MASTR
    ''' segment if there is one, otherwise the 4th segment (the original rule).
    ''' </summary>
    Private Shared Function BuildDefaultDocNo(tail As String, serial As String) As String
        If tail = "" Then Return AreaPlaceholder & "-" & serial
        Return AreaPlaceholder & "-" & SetSerialSegment(tail, serial)
    End Function

    ''' <summary>Writes <paramref name="serial"/> into a number's serial segment (see BuildDefaultDocNo); appends it when the number has no such segment.</summary>
    Private Shared Function SetSerialSegment(docNo As String, serial As String) As String
        Dim parts() As String = docNo.Split("-"c)
        Dim idx As Integer = -1
        For i As Integer = parts.Length - 1 To 0 Step -1
            If SerialSlotRx.IsMatch(parts(i)) Then idx = i : Exit For
        Next
        If idx < 0 AndAlso parts.Length >= 4 Then idx = 3
        If idx < 0 Then Return docNo & "-" & serial
        parts(idx) = serial
        Return String.Join("-", parts)
    End Function

    Private Sub RebindTempGrid(dt As DataTable)
        grdDDREntry.DataSource = dt
        grdDDREntry.DataBind()
        UpdateKpiScorecard()
    End Sub

    ''' <summary>
    ''' Refreshes the KPI scorecard (CTD Hours, DDR Hours, Variance, DDR Line
    ''' Items, Match Status) from grdDDREntry's currently rendered rows.
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

    ''' <summary>The current CTD's saved DDR lines, straight from the database.</summary>
    Private Function ReadDdrTable() As DataTable
        Dim dt As DataTable = NewDdrTempTable()
        Dim ctdId As Integer = CInt(Val(lblContext.Text))

        Using con As New SqlConnection(ST_Common.WorleyDataConnString)
            Using cmd As New SqlCommand(
                "SELECT CTD_ID, DDR_ID, PLIP_ID, RAMZ_ID, Document_No, Document_Title, Man_Hours, " &
                "Disc_Remarks, CRITICALITY, HO_REQ, HO_STATUS, Software " &
                "FROM CTD_DDR_DISC WHERE CTD_ID = @CTD_ID ORDER BY DDR_ID", con)
                cmd.Parameters.AddWithValue("@CTD_ID", ctdId)
                con.Open()
                Using rd As SqlDataReader = cmd.ExecuteReader()
                    While rd.Read()
                        Dim dr As DataRow = dt.NewRow()
                        dr("CTD_ID") = rd("CTD_ID")
                        dr("DDR_ID") = rd("DDR_ID")
                        dr("PLIP_ID") = rd("PLIP_ID").ToString().Trim()
                        dr("RAMZ_ID") = rd("RAMZ_ID").ToString().Trim()
                        dr("Document_No") = rd("Document_No").ToString().Trim()
                        dr("Document_Title") = rd("Document_Title").ToString()
                        dr("Man_Hours") = rd("Man_Hours").ToString()
                        dr("Disc_Remarks") = rd("Disc_Remarks").ToString()
                        dr("CRITICALITY") = rd("CRITICALITY").ToString().Trim()
                        dr("HO_REQ") = rd("HO_REQ").ToString().Trim()
                        dr("HO_STATUS") = rd("HO_STATUS").ToString().Trim()
                        dr("Software") = rd("Software").ToString()
                        dr("DocType") = ""
                        dt.Rows.Add(dr)
                    End While
                End Using
            End Using
        End Using
        Return dt
    End Function

    ''' <summary>
    ''' Loads the grid fresh from the database. grdDDREntry is only ever bound
    ''' through RebindTempGrid (never via DataSourceID), so DataKeys can't be reset
    ''' behind the page's back between postbacks.
    ''' </summary>
    Private Sub LoadDdrGrid()
        RebindTempGrid(ReadDdrTable())
    End Sub

    ''' <summary>
    ''' Reloads from the database but keeps the user's work: saved lines use the
    ''' values currently on screen (edits not yet saved), and unsaved lines are
    ''' added back at the end. Used after deleting a saved line and after a CSV
    ''' import, which change the database while other edits may be pending.
    ''' </summary>
    Private Sub ReloadKeepingEdits(current As DataTable)
        Dim db As DataTable = ReadDdrTable()

        Dim edited As New Dictionary(Of Integer, DataRow)
        For Each r As DataRow In current.Rows
            Dim id As Integer = CInt(r("DDR_ID"))
            If id > 0 Then edited(id) = r
        Next

        For Each r As DataRow In db.Rows
            Dim src As DataRow = Nothing
            If edited.TryGetValue(CInt(r("DDR_ID")), src) Then
                For Each col As DataColumn In db.Columns
                    r(col.ColumnName) = src(col.ColumnName)
                Next
            End If
        Next

        For Each r As DataRow In current.Rows
            If CInt(r("DDR_ID")) = 0 Then db.ImportRow(r)
        Next

        RebindTempGrid(db)
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
    ''' Splits the CTD's total hours evenly across every current DDR row, rounded
    ''' to 2dp, with the first row absorbing the rounding remainder so the DDR
    ''' total matches the CTD total exactly. Save All still persists them.
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
        ShowToast("success", "Allocated " & ctdHours.ToString("0.##") & " hours across " & hourBoxes.Count & " row(s). Press Save All to keep them.")
    End Sub

    Protected Sub grdDDREntry_RowCommand(sender As Object, e As GridViewCommandEventArgs)
        If e.CommandName <> "DeleteDDR" Then Return

        Dim ddrId As Integer
        Integer.TryParse(e.CommandArgument.ToString(), ddrId)

        If ddrId > 0 Then
            Using con As New SqlConnection(ST_Common.WorleyDataConnString)
                con.Open()
                Using cmd As New SqlCommand("DELETE FROM CTD_DDR_DISC WHERE DDR_ID = @DDR_ID AND CTD_ID = @CTD_ID", con)
                    cmd.Parameters.AddWithValue("@DDR_ID", ddrId)
                    cmd.Parameters.AddWithValue("@CTD_ID", CInt(Val(lblContext.Text)))
                    cmd.ExecuteNonQuery()
                End Using
            End Using
            ' Keep unsaved lines and unsaved edits on the other lines.
            ReloadKeepingEdits(GetCurrentGridData())
            ctd_ddr_match.DataBind()
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
    ''' Fills the multiplier's row picker from the rendered rows; preselectRowIndex
    ''' is the row whose "×N" was clicked (-1 from the sidebar = first row).
    ''' </summary>
    Private Sub PopulateMultiplyRowPicker(Optional preselectRowIndex As Integer = -1)
        ddlMultiplyTargetRow.Items.Clear()
        For Each row As GridViewRow In grdDDREntry.Rows
            If row.RowType <> DataControlRowType.DataRow Then Continue For
            Dim txtDocumentNo As TextBox = TryCast(row.FindControl("txtDocumentNo"), TextBox)
            Dim label As String = "Row " & (row.RowIndex + 1) & ": " & If(txtDocumentNo?.Text, "").Trim()
            ddlMultiplyTargetRow.Items.Add(New ListItem(label, row.RowIndex.ToString()))
        Next

        If preselectRowIndex >= 0 Then
            Dim item As ListItem = ddlMultiplyTargetRow.Items.FindByValue(preselectRowIndex.ToString())
            If item IsNot Nothing Then
                ddlMultiplyTargetRow.ClearSelection()
                item.Selected = True
            End If
        End If
    End Sub

    Protected Sub btnMultiplyRow_Click(sender As Object, e As EventArgs)
        Dim btn As LinkButton = CType(sender, LinkButton)
        Dim row As GridViewRow = CType(btn.NamingContainer, GridViewRow)

        PopulateMultiplyRowPicker(row.RowIndex)

        ScriptManager.RegisterStartupScript(Me, Me.GetType(), "OpenMultiplier",
            "openMultiplierDrawer(document.getElementById('" & btn.ClientID & "'));", True)
    End Sub

    Protected Sub btnOpenMultiplier_Click(sender As Object, e As EventArgs)
        PopulateMultiplyRowPicker()
        If ddlMultiplyTargetRow.Items.Count = 0 Then
            ShowToast("error", "Add a DDR line before multiplying.")
            Return
        End If

        Dim btn As LinkButton = CType(sender, LinkButton)
        ScriptManager.RegisterStartupScript(Me, Me.GetType(), "OpenMultiplierGlobal",
            "openMultiplierDrawer(document.getElementById('" & btn.ClientID & "'));", True)
    End Sub

    ''' <summary>
    ''' Creates N unsaved copies of the picked row (the source row is unchanged).
    ''' Mode A gives each copy the next DU serial (DUM01..DUM99, DU100+) in the
    ''' number's serial segment; mode B increments the number's trailing digits.
    ''' </summary>
    Protected Sub btnApplyMultiplier_Click(sender As Object, e As EventArgs)
        Dim rowIndex As Integer
        If Not Integer.TryParse(ddlMultiplyTargetRow.SelectedValue, rowIndex) Then
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
        Dim baseDocNo As String = sourceRow("Document_No").ToString().Trim()
        If IsActivityDoc(baseDocNo) Then
            ShowToast("error", "Activity lines can't be multiplied; use DDR + Activity to add another.")
            Return
        End If
        Dim useDumSequence As Boolean = (rblMultiplyMode.SelectedValue = "DUM")

        ' Computed once from the in-memory table (it already holds every row on screen).
        Dim nextDummyN As Integer = MaxDummyNumber(dt) + 1
        Dim unchanged As Integer = 0

        For i As Integer = 1 To n
            Dim newRow As DataRow = dt.NewRow()
            For Each col As DataColumn In dt.Columns
                newRow(col.ColumnName) = sourceRow(col.ColumnName)
            Next
            newRow("DDR_ID") = 0
            If sourceRow("DocType").ToString() = "" Then newRow("DocType") = "NEW"

            If useDumSequence Then
                newRow("Document_No") = SetSerialSegment(baseDocNo, FormatDummySuffix(nextDummyN))
                nextDummyN += 1
            Else
                Dim incremented As String = IncrementTrailingDigits(baseDocNo, i)
                If incremented = baseDocNo Then unchanged += 1
                newRow("Document_No") = incremented
            End If

            dt.Rows.Add(newRow)
        Next

        RebindTempGrid(dt)
        If unchanged > 0 Then
            ShowToast("error", unchanged & " copy/copies kept the same number because it doesn't end in digits. Edit them before saving.")
        End If
        ShowToast("success", n.ToString() & " copy/copies created from row " & (rowIndex + 1) & ". Check the hours, then Save All.")
    End Sub

    ''' <summary>Increments the numeric run at the very end of a document number, preserving its digit width (e.g. "...-0001" -&gt; "...-0002").</summary>
    Private Shared Function IncrementTrailingDigits(docNo As String, increment As Integer) As String
        Dim m As Match = Regex.Match(docNo, "(\d+)$")
        If Not m.Success Then Return docNo

        Dim digits As String = m.Groups(1).Value
        Dim value As Long
        If Not Long.TryParse(digits, value) Then Return docNo
        Dim newDigits As String = (value + increment).ToString().PadLeft(digits.Length, "0"c)
        Return docNo.Substring(0, m.Index) & newDigits
    End Function

    ''' <summary>
    ''' Rebuilds the in-memory DDR table from what is currently rendered in
    ''' grdDDREntry (including edits not yet saved and each unsaved row's Type).
    ''' </summary>
    Private Function GetCurrentGridData() As DataTable
        Dim dt As DataTable = NewDdrTempTable()

        For Each row As GridViewRow In grdDDREntry.Rows
            If row.RowType <> DataControlRowType.DataRow Then Continue For

            Dim txtDocumentNo As TextBox = TryCast(row.FindControl("txtDocumentNo"), TextBox)
            If txtDocumentNo Is Nothing Then Continue For

            Dim txtPLIP As TextBox = TryCast(row.FindControl("txtPLIP"), TextBox)
            Dim ddlRamz As DropDownList = TryCast(row.FindControl("ddlRamz"), DropDownList)
            Dim txtTitle As TextBox = TryCast(row.FindControl("txtTitle"), TextBox)
            Dim txtHours As TextBox = TryCast(row.FindControl("txtHours"), TextBox)
            Dim txtRemarks As TextBox = TryCast(row.FindControl("txtRemarks"), TextBox)
            Dim txtSoftware As TextBox = TryCast(row.FindControl("txtSoftware"), TextBox)
            Dim ddlStatus As DropDownList = TryCast(row.FindControl("ddlStatus"), DropDownList)
            Dim ddlType As DropDownList = TryCast(row.FindControl("ddlType"), DropDownList)

            Dim dr As DataRow = dt.NewRow()
            dr("CTD_ID") = CInt(Val(lblContext.Text))
            dr("DDR_ID") = GetRowDdrId(row)
            dr("PLIP_ID") = If(txtPLIP?.Text, "").Trim()
            dr("RAMZ_ID") = If(ddlRamz?.SelectedValue, "")
            dr("Document_No") = txtDocumentNo.Text.Trim()
            dr("Document_Title") = If(txtTitle?.Text, "").Trim()
            dr("Man_Hours") = If(txtHours?.Text, "").Trim()
            dr("Disc_Remarks") = If(txtRemarks?.Text, "").Trim()
            dr("CRITICALITY") = GetLabel(row, "lblCriticality")
            dr("HO_REQ") = GetLabel(row, "lblHoReq")
            dr("Software") = If(txtSoftware?.Text, "").Trim()
            dr("HO_STATUS") = If(ddlStatus?.SelectedValue, "")
            dr("DocType") = If(ddlType IsNot Nothing AndAlso ddlType.Visible, ddlType.SelectedValue, "")
            dt.Rows.Add(dr)
        Next

        Return dt
    End Function

    Private Function GetRowDdrId(row As GridViewRow) As Integer
        If row.RowIndex < 0 OrElse row.RowIndex >= grdDDREntry.DataKeys.Count Then Return 0
        Dim v As Object = grdDDREntry.DataKeys(row.RowIndex).Value
        Dim id As Integer
        If v Is Nothing OrElse v Is DBNull.Value OrElse Not Integer.TryParse(v.ToString(), id) Then Return 0
        Return id
    End Function

#End Region

#Region "DDR entry grid: data binding"

    Protected Sub grdDDREntry_RowDataBound(sender As Object, e As GridViewRowEventArgs)
        If e.Row.RowType <> DataControlRowType.DataRow Then Return

        Dim drv As DataRowView = CType(e.Row.DataItem, DataRowView)
        Dim ddrId As Integer
        Integer.TryParse(drv("DDR_ID").ToString(), ddrId)
        Dim documentNo As String = drv("Document_No").ToString().Trim()
        Dim isActivity As Boolean = IsActivityDoc(documentNo)

        ' RAMZ and Area lists are loaded once per request and filled here (not via
        ' DataSourceID), with a blank first item so "not chosen" is visible and a
        ' saved value outside the list is kept rather than crashing the page.
        FillList(CType(e.Row.FindControl("ddlRamz"), DropDownList), RamzList(), drv("RAMZ_ID").ToString().Trim())

        Dim ddlArea As DropDownList = CType(e.Row.FindControl("ddlArea"), DropDownList)
        Dim area As String = If(isActivity OrElse documentNo.Length < 6, "", documentNo.Substring(0, 6))
        If area.Equals(AreaPlaceholder, StringComparison.OrdinalIgnoreCase) Then area = ""
        FillList(ddlArea, AreaList(), If(AreaList().Contains(area), area, ""))
        ddlArea.Enabled = Not isActivity

        Dim ddlStatus As DropDownList = CType(e.Row.FindControl("ddlStatus"), DropDownList)
        SelectOrAdd(ddlStatus, drv("HO_STATUS").ToString().Trim().ToUpperInvariant())

        Dim ddlType As DropDownList = CType(e.Row.FindControl("ddlType"), DropDownList)
        ddlType.Visible = (ddrId = 0)

        If ddrId = 0 Then
            e.Row.Attributes("data-new") = "1"
            Dim docType As String = drv("DocType").ToString()
            If isActivity Then
                docType = "ACTIVITY"
            ElseIf docType <> "NEW" AndAlso docType <> "EXISTING" Then
                docType = "NEW"
            End If
            ddlType.SelectedValue = docType

            If Not isActivity Then
                Dim txtPLIP As TextBox = CType(e.Row.FindControl("txtPLIP"), TextBox)
                txtPLIP.Attributes("placeholder") = "Required"
            End If
        End If
    End Sub

    Protected Sub txtAREA_TextChanged(sender As Object, e As EventArgs)
        Dim txtArea As TextBox = CType(sender, TextBox)
        ApplyAreaToRow(CType(txtArea.NamingContainer, GridViewRow), txtArea.Text)
    End Sub

    Protected Sub ddlArea_SelectedIndexChanged(sender As Object, e As EventArgs)
        Dim ddlArea As DropDownList = CType(sender, DropDownList)
        If ddlArea.SelectedValue = "" Then Return
        ApplyAreaToRow(CType(ddlArea.NamingContainer, GridViewRow), ddlArea.SelectedValue)
    End Sub

    ''' <summary>
    ''' Puts a 6-character area code at the start of the row's document number:
    ''' replaces the AAA-UU placeholder or the existing area, and syncs the Area box
    ''' and list.
    ''' </summary>
    Private Sub ApplyAreaToRow(row As GridViewRow, areaText As String)
        Dim area As String = areaText.Trim()
        Dim txtArea As TextBox = CType(row.FindControl("txtArea"), TextBox)
        Dim ddlArea As DropDownList = CType(row.FindControl("ddlArea"), DropDownList)
        Dim txtDoc As TextBox = CType(row.FindControl("txtDocumentNo"), TextBox)

        If area.Length <> 6 Then
            ShowToast("error", "Row " & (row.RowIndex + 1) & ": the area code must be 6 characters, for example S10-U1.")
            Return
        End If

        Dim doc As String = txtDoc.Text.Trim()
        If IsActivityDoc(doc) Then Return

        If doc.StartsWith(AreaPlaceholder, StringComparison.OrdinalIgnoreCase) OrElse
           (doc.Length > 6 AndAlso doc(6) = "-"c) Then
            doc = area & doc.Substring(6)
        ElseIf doc = "" Then
            doc = area & "-"
        Else
            doc = area & "-" & doc
        End If

        txtDoc.Text = doc
        txtArea.Text = area
        SelectOrAdd(ddlArea, area)
    End Sub

    Protected Sub ddlType_SelectedIndexChanged(sender As Object, e As EventArgs)
        Dim ddlType As DropDownList = CType(sender, DropDownList)
        Dim row As GridViewRow = CType(ddlType.NamingContainer, GridViewRow)

        Dim txtPLIP As TextBox = CType(row.FindControl("txtPLIP"), TextBox)
        Dim btnPLIPSearch As LinkButton = CType(row.FindControl("btnPLIPSearch"), LinkButton)
        Dim ddlArea As DropDownList = CType(row.FindControl("ddlArea"), DropDownList)
        Dim txtArea As TextBox = CType(row.FindControl("txtArea"), TextBox)
        Dim txtDocumentNo As TextBox = CType(row.FindControl("txtDocumentNo"), TextBox)

        Select Case ddlType.SelectedValue
            Case "ACTIVITY"
                txtPLIP.Text = ""
                txtPLIP.Enabled = False
                txtPLIP.Attributes.Remove("placeholder")
                btnPLIPSearch.Visible = False
                ddlArea.Enabled = False
                txtArea.Text = ""
                txtArea.Enabled = False
                SetLabel(TryCast(row.FindControl("lblCriticality"), Label), "")
                SetLabel(TryCast(row.FindControl("lblHoReq"), Label), "")
                ' "ACTIVITY" is the marker the page and the save logic key off.
                txtDocumentNo.Text = "ACTIVITY"
            Case Else
                txtPLIP.Enabled = True
                txtPLIP.Attributes("placeholder") = "Required"
                btnPLIPSearch.Visible = True
                btnPLIPSearch.Enabled = True
                ddlArea.Enabled = True
                txtArea.Enabled = True
                If IsActivityDoc(txtDocumentNo.Text) Then
                    txtDocumentNo.Text = ""
                End If
        End Select
    End Sub

#End Region

#Region "Save"

    Protected Sub btnSaveAll_Click(sender As Object, e As EventArgs)
        If Not Page.IsValid Then
            ShowToast("error", "Fix the highlighted Hours boxes before saving.")
            Return
        End If

        Dim errors As New List(Of String)
        Dim rowsToInsert As New List(Of GridViewRow)
        Dim rowsToUpdate As New List(Of GridViewRow)
        Dim seenDocs As New Dictionary(Of String, Integer)(StringComparer.OrdinalIgnoreCase)
        Dim plipCache As New Dictionary(Of String, Boolean)(StringComparer.OrdinalIgnoreCase)

        For Each row As GridViewRow In grdDDREntry.Rows
            If row.RowType <> DataControlRowType.DataRow Then Continue For

            Dim txtDocumentNo As TextBox = TryCast(row.FindControl("txtDocumentNo"), TextBox)
            If txtDocumentNo Is Nothing Then Continue For

            Dim txtTitle As TextBox = CType(row.FindControl("txtTitle"), TextBox)
            Dim ddlRamz As DropDownList = CType(row.FindControl("ddlRamz"), DropDownList)
            Dim txtHours As TextBox = CType(row.FindControl("txtHours"), TextBox)
            Dim txtPLIP As TextBox = CType(row.FindControl("txtPLIP"), TextBox)
            Dim rowLabel As String = "Row " & (row.RowIndex + 1)
            Dim docNo As String = txtDocumentNo.Text.Trim()
            Dim isActivity As Boolean = IsActivityDoc(docNo)
            Dim ddrId As Integer = GetRowDdrId(row)

            If docNo = "" Then errors.Add(rowLabel & ": Document Number is required.")
            If txtTitle.Text.Trim() = "" Then errors.Add(rowLabel & ": Document Title is required.")
            If ddlRamz.SelectedValue = "" Then errors.Add(rowLabel & ": RAMZ ID is required.")

            Dim hours As Decimal
            Dim hoursText As String = txtHours.Text.Trim()
            If hoursText <> "" Then
                If Not Decimal.TryParse(hoursText, hours) Then
                    errors.Add(rowLabel & ": Hours must be numeric.")
                ElseIf hours < 0 Then
                    errors.Add(rowLabel & ": Hours can't be negative.")
                End If
            End If

            ' Rules for new documents (unsaved, not activity lines).
            If ddrId = 0 AndAlso Not isActivity AndAlso docNo <> "" Then
                Dim plipId As String = txtPLIP.Text.Trim()
                If plipId = "" Then
                    errors.Add(rowLabel & ": PLIP ID is required for new documents.")
                ElseIf Not IsActivePlip(plipId, plipCache) Then
                    errors.Add(rowLabel & ": PLIP ID " & plipId & " is not an active PLIP.")
                End If
                errors.AddRange(NewDocNumberProblems(rowLabel, docNo))
            End If

            If Not isActivity AndAlso docNo <> "" Then
                Dim firstRow As Integer
                If seenDocs.TryGetValue(docNo, firstRow) Then
                    errors.Add(rowLabel & ": Document Number " & docNo & " is already used on row " & firstRow & ".")
                Else
                    seenDocs(docNo) = row.RowIndex + 1
                End If
            End If

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

        Using conn As New SqlConnection(ST_Common.WorleyDataConnString)
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
                Trace.Warn("DDR_DeveloperV3", "Save All failed", ex)
                ShowToast("error", "Save failed: " & ex.Message)
                Return
            End Try
        End Using

        LoadDdrGrid()
        ctd_ddr_match.DataBind()
        ShowToast("success", "DDR records saved successfully.")
    End Sub

    ''' <summary>Document-number rules for a new document: a real area code and DU-pattern dummy serials.</summary>
    Private Shared Function NewDocNumberProblems(rowLabel As String, docNo As String) As List(Of String)
        Dim problems As New List(Of String)
        If docNo.StartsWith(AreaPlaceholder, StringComparison.OrdinalIgnoreCase) Then
            problems.Add(rowLabel & ": replace the " & AreaPlaceholder & " placeholder at the start of the Document Number with the area code.")
        End If
        If LegacyDummyRx.IsMatch(docNo) Then
            problems.Add(rowLabel & ": dummy document numbers must use a DU serial (DUM01…DUM99, then DU100…) instead of DUMMY or XXX.")
        End If
        Return problems
    End Function

    Private Sub InsertDdrRow(conn As SqlConnection, tran As SqlTransaction, row As GridViewRow)
        Const sql As String = "
            INSERT INTO CTD_DDR_DISC
                (CTD_ID, PLIP_ID, RAMZ_ID, DOCUMENT_NO, DOCUMENT_TITLE, MAN_HOURS,
                 DISC_REMARKS, SOFTWARE, HO_STATUS, CRITICALITY, HO_REQ)
            VALUES
                (@CTD_ID, @PLIP_ID, @RAMZ_ID, @DOCUMENT_NO, @DOCUMENT_TITLE, @MAN_HOURS,
                 @DISC_REMARKS, @SOFTWARE, @HO_STATUS, @CRITICALITY, @HO_REQ)"

        Using cmd As New SqlCommand(sql, conn, tran)
            cmd.Parameters.AddWithValue("@CTD_ID", CInt(Val(lblContext.Text)))
            AddRowParameters(cmd, row)
            cmd.ExecuteNonQuery()
        End Using
    End Sub

    Private Sub UpdateDdrRow(conn As SqlConnection, tran As SqlTransaction, row As GridViewRow)
        Const sql As String = "
            UPDATE CTD_DDR_DISC SET
                PLIP_ID = @PLIP_ID, RAMZ_ID = @RAMZ_ID, DOCUMENT_NO = @DOCUMENT_NO,
                DOCUMENT_TITLE = @DOCUMENT_TITLE, MAN_HOURS = @MAN_HOURS,
                DISC_REMARKS = @DISC_REMARKS, SOFTWARE = @SOFTWARE, HO_STATUS = @HO_STATUS,
                CRITICALITY = @CRITICALITY, HO_REQ = @HO_REQ
            WHERE DDR_ID = @DDR_ID AND CTD_ID = @CTD_ID"

        Using cmd As New SqlCommand(sql, conn, tran)
            AddRowParameters(cmd, row)
            cmd.Parameters.AddWithValue("@DDR_ID", GetRowDdrId(row))
            cmd.Parameters.AddWithValue("@CTD_ID", CInt(Val(lblContext.Text)))
            cmd.ExecuteNonQuery()
        End Using
    End Sub

    Private Sub AddRowParameters(cmd As SqlCommand, row As GridViewRow)
        Dim txtPLIP As TextBox = CType(row.FindControl("txtPLIP"), TextBox)
        Dim ddlRamz As DropDownList = CType(row.FindControl("ddlRamz"), DropDownList)
        Dim txtDocumentNo As TextBox = CType(row.FindControl("txtDocumentNo"), TextBox)
        Dim txtTitle As TextBox = CType(row.FindControl("txtTitle"), TextBox)
        Dim txtHours As TextBox = CType(row.FindControl("txtHours"), TextBox)
        Dim txtRemarks As TextBox = CType(row.FindControl("txtRemarks"), TextBox)
        Dim txtSoftware As TextBox = CType(row.FindControl("txtSoftware"), TextBox)
        Dim ddlStatus As DropDownList = CType(row.FindControl("ddlStatus"), DropDownList)
        Dim isActivity As Boolean = IsActivityDoc(txtDocumentNo.Text)

        Dim hours As Decimal = 0
        Decimal.TryParse(txtHours.Text.Trim(), hours)

        cmd.Parameters.AddWithValue("@PLIP_ID", If(isActivity, "", txtPLIP.Text.Trim()))
        cmd.Parameters.AddWithValue("@RAMZ_ID", ddlRamz.SelectedValue)
        cmd.Parameters.AddWithValue("@DOCUMENT_NO", txtDocumentNo.Text.Trim())
        cmd.Parameters.AddWithValue("@DOCUMENT_TITLE", txtTitle.Text.Trim())
        cmd.Parameters.AddWithValue("@MAN_HOURS", hours)
        cmd.Parameters.AddWithValue("@DISC_REMARKS", txtRemarks.Text.Trim())
        cmd.Parameters.AddWithValue("@SOFTWARE", txtSoftware.Text.Trim())
        cmd.Parameters.AddWithValue("@HO_STATUS", ddlStatus.SelectedValue)
        cmd.Parameters.AddWithValue("@CRITICALITY", GetLabel(row, "lblCriticality"))
        cmd.Parameters.AddWithValue("@HO_REQ", GetLabel(row, "lblHoReq"))
    End Sub

    Private Sub ShowValidationErrors(errors As List(Of String))
        Dim sb As New StringBuilder("<ul>")
        For Each message As String In errors
            sb.Append("<li>").Append(HttpUtility.HtmlEncode(message)).Append("</li>")
        Next
        sb.Append("</ul>")
        litValidationErrors.Text = sb.ToString()
        pnlValidationSummary.Visible = True
        ShowToast("error", errors.Count.ToString() & " issue(s) need attention before saving.")
    End Sub

#End Region

#Region "CSV import / export"

    ''' <summary>Downloads the CSV header row only - a blank import template with the seven column names.</summary>
    Protected Sub CSV_Template_Click(sender As Object, e As EventArgs)
        Dim ctdId As Integer = CInt(Val(lblContext.Text))

        Response.Clear()
        Response.ContentType = "text/csv"
        Response.AddHeader("Content-Disposition", "attachment; filename=DDR_Import_Template_CTD_" & ctdId & ".csv")
        Response.Write(String.Join(",", CsvColumns) & vbCrLf)
        Response.Flush()
        ' Stop the page's own HTML from being appended to the download.
        Response.SuppressContent = True
        HttpContext.Current.ApplicationInstance.CompleteRequest()
    End Sub

    Protected Sub CSV_Upload_Click(sender As Object, e As EventArgs)
        If Not fuCsv.HasFile Then
            ShowToast("error", "Choose a CSV file before uploading.")
            Return
        End If

        ' Keep blank lines in place so "Line N" matches the file; they are skipped in the loop.
        Dim lines() As String = DecodeCsv(fuCsv.FileBytes).Split({vbCrLf, vbLf, vbCr}, StringSplitOptions.None)

        ' The header row is optional: skip line 1 only when it is the template's header.
        Dim firstData As Integer = 0
        Do While firstData < lines.Length AndAlso String.IsNullOrWhiteSpace(lines(firstData))
            firstData += 1
        Loop
        If firstData < lines.Length Then
            Dim head = ParseCsvLine(lines(firstData))
            If head.Count > 0 AndAlso head(0).Trim().Equals("CTD_ID", StringComparison.OrdinalIgnoreCase) Then firstData += 1
        End If

        Dim defaultCtdId As Integer = CInt(Val(lblContext.Text))
        Dim ctdOk As New Dictionary(Of Integer, Boolean)
        Dim plipCache As New Dictionary(Of String, Boolean)(StringComparer.OrdinalIgnoreCase)
        Dim seenDocs As New Dictionary(Of String, Integer)(StringComparer.OrdinalIgnoreCase)
        Dim ramzAllowed As New HashSet(Of String)(RamzList(), StringComparer.OrdinalIgnoreCase)
        Dim imported As Integer = 0
        Dim rowErrors As New List(Of String)

        Using conn As New SqlConnection(ST_Common.WorleyDataConnString)
            conn.Open()
            Dim tran As SqlTransaction = conn.BeginTransaction()
            Try
                For i As Integer = firstData To lines.Length - 1
                    If String.IsNullOrWhiteSpace(lines(i)) Then Continue For
                    Dim lineLabel As String = "Line " & (i + 1)

                    Dim fields = ParseCsvLine(lines(i))
                    If fields.Count < CsvColumns.Length Then
                        rowErrors.Add(lineLabel & ": expected " & CsvColumns.Length & " columns, found " & fields.Count & ".")
                        Continue For
                    End If

                    Dim ctdIdText = fields(0).Trim()
                    Dim ramzId = fields(1).Trim()
                    Dim plipId = fields(2).Trim()
                    Dim documentNo = fields(3).Trim()
                    Dim documentTitle = fields(4).Trim()
                    Dim manHoursText = fields(5).Trim()
                    Dim hoStatus = fields(6).Trim().ToUpperInvariant()
                    Dim isActivity As Boolean = IsActivityDoc(documentNo)

                    ' CTD_ID is optional per row: blank means the CTD open on the page.
                    Dim ctdId As Integer = defaultCtdId
                    If ctdIdText <> "" AndAlso Not Integer.TryParse(ctdIdText, ctdId) Then
                        rowErrors.Add(lineLabel & ": CTD_ID must be numeric.")
                        Continue For
                    End If

                    If ctdId <= 0 OrElse documentNo = "" OrElse documentTitle = "" OrElse ramzId = "" Then
                        rowErrors.Add(lineLabel & ": CTD_ID, Document_No, Document_Title and RAMZ_ID are required.")
                        Continue For
                    End If

                    If Not CtdInThisProject(ctdId, ctdOk) Then
                        rowErrors.Add(lineLabel & ": CTD_ID " & ctdId & " is not a CTD of project " & lblProject.Text & ".")
                        Continue For
                    End If

                    If ramzAllowed.Count > 0 AndAlso Not ramzAllowed.Contains(ramzId) Then
                        rowErrors.Add(lineLabel & ": RAMZ_ID " & ramzId & " is not set up for project " & lblProject.Text & ".")
                        Continue For
                    End If

                    Dim manHours As Decimal = 0
                    If manHoursText <> "" AndAlso (Not Decimal.TryParse(manHoursText, manHours) OrElse manHours < 0) Then
                        rowErrors.Add(lineLabel & ": Man_Hours must be a number of 0 or more.")
                        Continue For
                    End If

                    If hoStatus <> "" AndAlso hoStatus <> "AFC" AndAlso hoStatus <> "APP" Then
                        rowErrors.Add(lineLabel & ": HO_Status must be AFC or APP (or blank).")
                        Continue For
                    End If

                    If isActivity Then
                        plipId = ""
                    Else
                        If plipId = "" Then
                            rowErrors.Add(lineLabel & ": PLIP_ID is required for new documents.")
                            Continue For
                        ElseIf Not IsActivePlip(plipId, plipCache) Then
                            rowErrors.Add(lineLabel & ": PLIP_ID " & plipId & " is not an active PLIP.")
                            Continue For
                        End If
                        Dim numberProblems = NewDocNumberProblems(lineLabel, documentNo)
                        If numberProblems.Count > 0 Then
                            rowErrors.AddRange(numberProblems)
                            Continue For
                        End If
                        Dim firstLine As Integer
                        If seenDocs.TryGetValue(documentNo, firstLine) Then
                            rowErrors.Add(lineLabel & ": Document_No " & documentNo & " is already on line " & firstLine & ".")
                            Continue For
                        End If
                        seenDocs(documentNo) = i + 1
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
                Trace.Warn("DDR_DeveloperV3", "CSV import failed", ex)
                ShowToast("error", "CSV import failed: " & ex.Message)
                Return
            End Try
        End Using

        pnlValidationSummary.Visible = False
        ReloadKeepingEdits(GetCurrentGridData())
        ctd_ddr_match.DataBind()
        ShowToast("success", imported.ToString() & " DDR line(s) imported from CSV.")
    End Sub

    ''' <summary>
    ''' Decodes an uploaded CSV: UTF-8 (with or without BOM) when the bytes are valid
    ''' UTF-8, otherwise Windows-1252, which is what Excel's plain "CSV" format writes.
    ''' </summary>
    Private Shared Function DecodeCsv(bytes() As Byte) As String
        If bytes Is Nothing OrElse bytes.Length = 0 Then Return ""
        Dim offset As Integer = If(bytes.Length >= 3 AndAlso bytes(0) = &HEF AndAlso bytes(1) = &HBB AndAlso bytes(2) = &HBF, 3, 0)
        Try
            Return New UTF8Encoding(False, True).GetString(bytes, offset, bytes.Length - offset)
        Catch ex As DecoderFallbackException
            Return Encoding.GetEncoding(1252).GetString(bytes)
        End Try
    End Function

    ''' <summary>Minimal CSV splitter that understands double-quoted fields containing commas and "" escapes.</summary>
    Private Shared Function ParseCsvLine(line As String) As List(Of String)
        Dim fields As New List(Of String)
        Dim current As New StringBuilder()
        Dim inQuotes As Boolean = False

        Dim i As Integer = 0
        While i < line.Length
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
            i += 1
        End While
        fields.Add(current.ToString())
        Return fields
    End Function

    Private Function CtdInThisProject(ctdId As Integer, cache As Dictionary(Of Integer, Boolean)) As Boolean
        Dim ok As Boolean
        If cache.TryGetValue(ctdId, ok) Then Return ok
        Dim project As String = ExecScalarQuery("SELECT TOP 1 Project_No FROM CTD_MASTER WHERE CTD_ID = @CTD_ID",
                                                New SqlParameter("@CTD_ID", ctdId)).Trim()
        ok = project <> "" AndAlso project.Equals(lblProject.Text, StringComparison.OrdinalIgnoreCase)
        cache(ctdId) = ok
        Return ok
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

    Private Function QueryList(sql As String, ParamArray parameters As SqlParameter()) As List(Of String)
        Dim list As New List(Of String)
        Dim seen As New HashSet(Of String)(StringComparer.OrdinalIgnoreCase)
        Using con As New SqlConnection(ST_Common.WorleyDataConnString)
            Using cmd As New SqlCommand(sql, con)
                If parameters IsNot Nothing Then cmd.Parameters.AddRange(parameters)
                con.Open()
                Using rd As SqlDataReader = cmd.ExecuteReader()
                    While rd.Read()
                        Dim v As String = If(rd.IsDBNull(0), "", rd(0).ToString().Trim())
                        If v <> "" AndAlso seen.Add(v) Then list.Add(v)
                    End While
                End Using
            End Using
        End Using
        Return list
    End Function

    ''' <summary>RAMZ IDs set up for the open project (Project_Info.RAMZ_ID is a comma-separated list).</summary>
    Private Function RamzList() As List(Of String)
        If _ramzList Is Nothing Then
            _ramzList = QueryList(
                "SELECT LTRIM(RTRIM(s.value)) FROM Project_Info CROSS APPLY STRING_SPLIT(RAMZ_ID, ',') s WHERE PROJECT_NO = @PROJECT_NO",
                New SqlParameter("@PROJECT_NO", lblProject.Text))
        End If
        Return _ramzList
    End Function

    ''' <summary>Area codes (first 6 characters of document numbers) already used on the open project.</summary>
    Private Function AreaList() As List(Of String)
        If _areaList Is Nothing Then
            _areaList = QueryList(
                "SELECT DISTINCT LEFT(LTRIM(RTRIM(Document_No)), 6) AS AU_CODE FROM [ACAD_DATA].[dbo].[SMARTDDR01] " &
                "WHERE Document_No NOT LIKE '%ACTIVITY%' AND Project_No = @PROJECT_NO ORDER BY AU_CODE",
                New SqlParameter("@PROJECT_NO", lblProject.Text))
        End If
        Return _areaList
    End Function

    ''' <summary>Fills a list with a blank first item plus <paramref name="items"/>, keeps a selected value that isn't in the list, and selects it.</summary>
    Private Shared Sub FillList(ddl As DropDownList, items As List(Of String), selected As String)
        ddl.Items.Clear()
        ddl.Items.Add(New ListItem("", ""))
        For Each v In items
            ddl.Items.Add(New ListItem(v, v))
        Next
        SelectOrAdd(ddl, selected)
    End Sub

    ''' <summary>Selects <paramref name="value"/>, adding it first if the list doesn't have it (never throws on unknown values).</summary>
    Private Shared Sub SelectOrAdd(ddl As DropDownList, value As String)
        If ddl Is Nothing Then Return
        value = If(value, "").Trim()
        If ddl.Items.FindByValue(value) Is Nothing Then ddl.Items.Add(New ListItem(value, value))
        ddl.ClearSelection()
        ddl.Items.FindByValue(value).Selected = True
    End Sub

    ''' <summary>Labels render their Text as-is, so database values are stored encoded and read back decoded.</summary>
    Private Shared Sub SetLabel(lbl As Label, value As String)
        If lbl IsNot Nothing Then lbl.Text = HttpUtility.HtmlEncode(If(value, ""))
    End Sub

    Private Shared Function GetLabel(row As GridViewRow, id As String) As String
        Dim lbl As Label = TryCast(row.FindControl(id), Label)
        Return If(lbl Is Nothing, "", HttpUtility.HtmlDecode(lbl.Text))
    End Function

    ''' <summary>Activity lines are marked by "ACTIVITY" in the document number (same test as the markup bindings).</summary>
    Private Shared Function IsActivityDoc(docNo As String) As Boolean
        Return If(docNo, "").IndexOf("ACTIVITY", StringComparison.OrdinalIgnoreCase) >= 0
    End Function

    ''' <summary>Formats a dummy serial: DUM01..DUM99, then DU100 onward (always LIKE 'DU%').</summary>
    Private Shared Function FormatDummySuffix(n As Integer) As String
        If n <= 99 Then Return "DUM" & n.ToString("00")
        Return "DU" & n.ToString("000")
    End Function

    ''' <summary>
    ''' Highest dummy serial used by any line in <paramref name="dt"/> (saved or not),
    ''' looking at every "-" segment - the serial's position varies with the number's
    ''' pattern and the area prefix. 0 when none.
    ''' </summary>
    Private Shared Function MaxDummyNumber(dt As DataTable) As Integer
        Dim maxN As Integer = 0
        For Each r As DataRow In dt.Rows
            For Each segment As String In r("Document_No").ToString().Split("-"c)
                Dim m As Match = DummySerialRx.Match(segment.Trim())
                Dim n As Integer
                If m.Success AndAlso Integer.TryParse(m.Groups("n").Value, n) Then maxN = Math.Max(maxN, n)
            Next
        Next
        Return maxN
    End Function

    ''' <summary>The current CTD's total hours, from CTD_MASTER.</summary>
    Private Function GetCurrentCtdTotalHours() As Decimal
        Dim result As Decimal = 0
        Decimal.TryParse(
            ExecScalarQuery("SELECT Total_Hours FROM CTD_MASTER WHERE CTD_ID = @CTD_ID", New SqlParameter("@CTD_ID", CInt(Val(lblContext.Text)))),
            result)
        Return result
    End Function

    ''' <summary>
    ''' Status fields for a PLIP from SPO_PLIP - the same active-PLIP source the PLIP
    ''' search, the details footer and the "active PLIP" rule use. AFC when the
    ''' required handover status is ASB, otherwise APP.
    ''' </summary>
    Private Function GetPlipStatus(plipId As String) As (Found As Boolean, Afc As String, Critical As String, FhoStatus As String)
        If String.IsNullOrWhiteSpace(plipId) Then Return (False, "", "", "")

        Const sql As String = "
            SELECT TOP 1
                CASE WHEN [Required_Handover_Status] = 'ASB' THEN 'AFC' ELSE 'APP' END AS AFCAPP,
                [Critical_Documentation],
                [Required_Handover_Status]
            FROM [ACAD_DATA].[dbo].[SPO_PLIP]
            WHERE [Active_YN] = 'YES' AND [PLIP_ID] = @PLIP_ID"

        Using con As New SqlConnection(ST_Common.WorleyDataConnString)
            Using cmd As New SqlCommand(sql, con)
                cmd.Parameters.AddWithValue("@PLIP_ID", plipId.Trim())
                con.Open()
                Using rd As SqlDataReader = cmd.ExecuteReader()
                    If rd.Read() Then
                        Return (True, rd("AFCAPP").ToString(), rd("Critical_Documentation").ToString().Trim(), rd("Required_Handover_Status").ToString().Trim())
                    End If
                End Using
            End Using
        End Using
        Return (False, "", "", "")
    End Function

    Private Function IsActivePlip(plipId As String, cache As Dictionary(Of String, Boolean)) As Boolean
        Dim ok As Boolean
        If cache.TryGetValue(plipId, ok) Then Return ok
        ok = ExecScalarQuery("SELECT COUNT(*) FROM [ACAD_DATA].[dbo].[SPO_PLIP] WHERE [Active_YN] = 'YES' AND [PLIP_ID] = @PLIP_ID",
                             New SqlParameter("@PLIP_ID", plipId)) <> "0"
        cache(plipId) = ok
        Return ok
    End Function

    Private Sub ShowToast(type As String, message As String)
        Dim script As String = "showToast(" &
            HttpUtility.JavaScriptStringEncode(type, True) & "," &
            HttpUtility.JavaScriptStringEncode(message, True) & ");"
        ScriptManager.RegisterStartupScript(Me, Me.GetType(), "Toast" & Guid.NewGuid().ToString("N"), script, True)
    End Sub

#End Region

End Class

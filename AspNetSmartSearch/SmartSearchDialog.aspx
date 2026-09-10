<%@ Page Language="VB" AutoEventWireup="true" CodeBehind="SmartSearchDialog.aspx.vb"
    Inherits="AspNetSmartSearch.SmartSearchDialog" %>

<!DOCTYPE html>
<html>
<head runat="server">
    <title>Smart Search</title>
    <style>
        body { font-family: Arial, Helvetica, sans-serif; font-size: 13px; margin: 10px; }
        #searchBar { margin-bottom: 10px; }
        table.results { border-collapse: collapse; width: 100%; }
        table.results th { background: #f0f0f0; text-align: left; padding: 4px 6px; border-bottom: 1px solid #ccc; }
        table.results td { padding: 4px 6px; border-bottom: 1px solid #eee; }
        table.results tr:hover td { background: #eef6ff; }
        a.selectLink { cursor: pointer; color: #0055cc; text-decoration: none; }
        a.selectLink:hover { text-decoration: underline; }
        .noResults { color: #777; font-style: italic; padding: 8px 0; }
    </style>
</head>
<body>
    <form id="frmSearch" runat="server">
        <!-- These carry the parent page's target control IDs and the search mode
             across postbacks of THIS dialog page (they arrive on the query string
             the first time; ViewState keeps them alive after that). -->
        <asp:HiddenField ID="hfTargetTextClientId" runat="server" />
        <asp:HiddenField ID="hfTargetHiddenClientId" runat="server" />
        <asp:HiddenField ID="hfMode" runat="server" />

        <div id="searchBar">
            <asp:TextBox ID="txtSearch" runat="server" Width="260px" />
            <asp:Button ID="btnSearch" runat="server" Text="Search" OnClick="btnSearch_Click" />
        </div>

        <asp:Label ID="lblMessage" runat="server" ForeColor="Red" />

        <asp:GridView ID="gvResults" runat="server" AutoGenerateColumns="false"
            CssClass="results" GridLines="None" EmptyDataText=""
            OnRowCommand="gvResults_RowCommand" DataKeyNames="Id">
            <Columns>
                <asp:BoundField DataField="Id" HeaderText="ID" ItemStyle-Width="60px" />
                <asp:BoundField DataField="Name" HeaderText="Name" />
                <asp:BoundField DataField="Description" HeaderText="Description" />
                <asp:TemplateField HeaderText="">
                    <ItemTemplate>
                        <asp:LinkButton ID="lnkSelect" runat="server" CssClass="selectLink"
                            Text="Select" CommandName="SelectRow"
                            CommandArgument='<%# Eval("Id") %>' />
                    </ItemTemplate>
                </asp:TemplateField>
            </Columns>
        </asp:GridView>
    </form>

    <script type="text/javascript">
        // Runs once the dialog has picked a row: tells the parent window which
        // value was chosen, then closes itself. The server writes the actual
        // id/name values in via RegisterStartupScript (see code-behind).
        function smartSearch_returnResult(id, name) {
            if (window.opener && !window.opener.closed &&
                typeof window.opener.smartSearch_setResult === 'function') {
                window.opener.smartSearch_setResult(id, name);
            }
            window.close();
        }
    </script>
</body>
</html>

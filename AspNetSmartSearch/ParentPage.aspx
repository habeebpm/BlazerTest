<%@ Page Language="VB" AutoEventWireup="true" CodeBehind="ParentPage.aspx.vb"
    Inherits="AspNetSmartSearch.ParentPage" %>

<!DOCTYPE html>
<html>
<head runat="server">
    <title>Parent Page - Smart Search Demo</title>
    <style>
        body { font-family: Arial, Helvetica, sans-serif; font-size: 13px; margin: 20px; }
        .field { margin-bottom: 10px; }
        .field label { display: inline-block; width: 110px; }
    </style>
    <script type="text/javascript">
        // Opens the smart search dialog for the Customer field, passing along
        // the ClientIDs of the controls that should receive the chosen value.
        function openCustomerSearch() {
            var url = 'SmartSearchDialog.aspx'
                + '?mode=Customer'
                + '&txt=' + encodeURIComponent('<%= txtCustomerName.ClientID %>')
                + '&hid=' + encodeURIComponent('<%= hfCustomerId.ClientID %>');

            window.open(url, 'smartSearchDlg',
                'width=560,height=460,resizable=yes,scrollbars=yes,status=no,toolbar=no,menubar=no,location=no');

            return false; // prevent the button from posting this page back
        }

        // Called by SmartSearchDialog.aspx (via window.opener) once a row is
        // picked. This is where the search result is stored into this page's
        // controls.
        function smartSearch_setResult(id, name) {
            document.getElementById('<%= hfCustomerId.ClientID %>').value = id;
            document.getElementById('<%= txtCustomerName.ClientID %>').value = name;

            // Optional: let server-side code react to the new value, e.g. to
            // load related data. Only do this if you actually need a postback.
            // __doPostBack('<%= btnCustomerChanged.UniqueID %>', '');
        }
    </script>
</head>
<body>
    <form id="frmParent" runat="server">
        <h3>Customer</h3>

        <div class="field">
            <label for="txtCustomerName">Customer:</label>
            <asp:TextBox ID="txtCustomerName" runat="server" ReadOnly="true" Width="220px" />
            <asp:Button ID="btnSearchCustomer" runat="server" Text="Search..."
                OnClientClick="return openCustomerSearch();" UseSubmitBehavior="false" />
            <asp:HiddenField ID="hfCustomerId" runat="server" />
        </div>

        <div class="field">
            <asp:Button ID="btnSave" runat="server" Text="Save" OnClick="btnSave_Click" />
        </div>

        <asp:Label ID="lblStatus" runat="server" />
    </form>
</body>
</html>

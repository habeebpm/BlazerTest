# ASP.NET WebForms Smart Search Dialog (VB.NET)

A classic "lookup"/"smart search" popup used from a data-entry page: the user
clicks a **Search** button next to a textbox, a small dialog page opens,
they filter a grid of records and pick a row, and the chosen value is written
straight back into a control on the page that opened the dialog.

## Files

| File | Purpose |
|---|---|
| `ParentPage.aspx` / `.vb` | The "host" page. Has a read-only textbox + hidden field for the picked record, and a Search button that pops the dialog. |
| `SmartSearchDialog.aspx` / `.vb` | The dialog itself. Has its own search box + Search button, a `GridView` of results, and a "Select" link per row. |

## How the result gets back to the parent page

The dialog is opened with `window.open(...)`, and the parent page's control
IDs are passed on the query string. When the user picks a row, the dialog
does **not** post back to the parent — instead it renders a tiny inline
`<script>` block (via `ClientScript.RegisterStartupScript`) that:

1. Calls a JavaScript function already defined on the **opener** window
   (`window.opener.smartSearch_setResult(...)`), passing the selected
   `ID` and `Name`.
2. That function fills the textbox and hidden field on the parent page.
3. The dialog then calls `window.close()`.

This keeps the dialog completely decoupled from whatever page opens it —
any page can reuse `SmartSearchDialog.aspx` as long as it defines a
`smartSearch_setResult(id, name)` JS function and passes its control's
`ClientID` values on the query string.

## Wiring it into your own page

```html
<asp:TextBox ID="txtCustomerName" runat="server" ReadOnly="true" />
<asp:HiddenField ID="hfCustomerId" runat="server" />
<asp:Button ID="btnSearchCustomer" runat="server" Text="Search..."
    OnClientClick="return openSmartSearch();" UseSubmitBehavior="false" />
```

```javascript
function openSmartSearch() {
    var url = 'SmartSearchDialog.aspx'
        + '?mode=Customer'
        + '&txt=' + encodeURIComponent('<%= txtCustomerName.ClientID %>')
        + '&hid=' + encodeURIComponent('<%= hfCustomerId.ClientID %>');
    window.open(url, 'smartSearchDlg', 'width=520,height=480,resizable=yes,scrollbars=yes');
    return false; // don't post back the parent page
}

// Called by the dialog window once a row is picked.
function smartSearch_setResult(id, name) {
    document.getElementById('<%= hfCustomerId.ClientID %>').value = id;
    document.getElementById('<%= txtCustomerName.ClientID %>').value = name;
}
```

`mode` lets one dialog page serve several lookups (customers, products,
etc.) by switching its data source in `Page_Load` — see
`SmartSearchDialog.aspx.vb`.

## Wiring up real data

`SmartSearchDialog.aspx.vb` searches an in-memory list so the sample runs
with no database. Swap `GetSearchResults()` for a real ADO.NET call — a
parameterized query is sketched in a comment right above it.

Namespace AspNetSmartSearch

    ''' <summary>
    ''' Example "host" page that launches SmartSearchDialog.aspx to let the user
    ''' pick a customer, and stores the result (Id + Name) in txtCustomerName /
    ''' hfCustomerId. See README.md for how the value gets from the dialog back
    ''' into these controls.
    ''' </summary>
    Public Class ParentPage
        Inherits System.Web.UI.Page

        Protected Sub Page_Load(sender As Object, e As EventArgs) Handles Me.Load
            If Not IsPostBack Then
                lblStatus.Text = String.Empty
            End If
        End Sub

        Protected Sub btnSave_Click(sender As Object, e As EventArgs)
            If String.IsNullOrEmpty(hfCustomerId.Value) Then
                lblStatus.Text = "Please search for and select a customer first."
                Return
            End If

            ' hfCustomerId.Value / txtCustomerName.Text now hold the record that
            ' was chosen in the smart search dialog - use them like any other
            ' posted-back control value, e.g. save to the database:
            '
            '   SaveOrder(customerId:=CInt(hfCustomerId.Value), customerName:=txtCustomerName.Text)

            lblStatus.Text = String.Format("Saved with customer #{0} - {1}.",
                                            hfCustomerId.Value, txtCustomerName.Text)
        End Sub

    End Class

End Namespace

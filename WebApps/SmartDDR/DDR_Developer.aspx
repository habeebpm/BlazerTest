<%@ Page Language="vb" AutoEventWireup="false" CodeBehind="DDR_Developer.aspx.vb" Inherits="SmarTagsASP.DDR_Developer" %>
<!DOCTYPE html>

<html xmlns="http://www.w3.org/1999/xhtml">
<head runat="server">

    <title>Worley SmartDDR</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />

    <style>
/* ==========================
   DESIGN TOKENS
========================== */

*{
    margin:0;
    padding:0;
    box-sizing:border-box;
}

:root{
    --primary:#0F766E;
    --primary-light:#14B8A6;
    --primary-dark:#0B5750;
    --secondary:#0EA5E9;
    --bg:#F8FAFC;
    --card:#FFFFFF;
    --border:#E2E8F0;
    --text:#0F172A;
    --text-light:#64748B;
    --success:#16A34A;
    --success-bg:#ECFDF5;
    --danger:#DC2626;
    --danger-bg:#FEF2F2;
    --warning:#D97706;
    --warning-bg:#FFFBEB;
    --sidebar-width:260px;
    --radius:14px;
    --shadow:0 4px 12px rgba(15,23,42,.06);
}

@media (prefers-color-scheme: dark){
    :root:not([data-theme="light"]){
        --bg:#0B1220;
        --card:#111A2B;
        --border:#22314A;
        --text:#E2E8F0;
        --text-light:#94A3B8;
        --success-bg:#062A1D;
        --danger-bg:#2A0E0E;
        --warning-bg:#2A2008;
    }
}

:root[data-theme="dark"]{
    --bg:#0B1220;
    --card:#111A2B;
    --border:#22314A;
    --text:#E2E8F0;
    --text-light:#94A3B8;
    --success-bg:#062A1D;
    --danger-bg:#2A0E0E;
    --warning-bg:#2A2008;
}

body{
    font-family:"Segoe UI",Tahoma,Arial,sans-serif;
    background:var(--bg);
    color:var(--text);
}

a{ color:inherit; }

/* ==========================
   LAYOUT
========================== */

.container{
    display:flex;
    min-height:100vh;
}

.sidebar{
    background:linear-gradient(180deg,#1C3737,var(--primary-light));
    width:var(--sidebar-width);
    color:white;
    padding:20px 16px;
    position:fixed;
    top:0;
    bottom:0;
    left:0;
    overflow-y:auto;
    z-index:500;
    transition:transform .25s ease;
}

.main{
    margin-left:var(--sidebar-width);
    padding:24px;
    min-height:100vh;
    width:calc(100% - var(--sidebar-width));
}

.mobile-topbar{
    display:none;
    align-items:center;
    justify-content:space-between;
    background:var(--card);
    border-bottom:1px solid var(--border);
    padding:12px 16px;
    position:sticky;
    top:0;
    z-index:400;
}

.hamburger{
    background:none;
    border:1px solid var(--border);
    border-radius:8px;
    font-size:18px;
    padding:6px 10px;
    cursor:pointer;
    color:var(--text);
}

@media (max-width:900px){
    .sidebar{
        transform:translateX(-100%);
        width:280px;
    }
    .sidebar.open{
        transform:translateX(0);
        box-shadow:10px 0 30px rgba(0,0,0,.25);
    }
    .main{
        margin-left:0;
        width:100%;
        padding:16px;
    }
    .mobile-topbar{
        display:flex;
    }
}

/* ==========================
   LOGO / SIDEBAR
========================== */

.logo{
    text-align:center;
    padding-bottom:16px;
    margin-bottom:16px;
    border-bottom:1px solid rgba(255,255,255,.15);
}

.logo h2{
    color:white;
    font-size:24px;
    font-weight:700;
}

.logo small{
    color:#CBD5E1;
    display:block;
    margin-top:4px;
}

.side-nav{
    display:flex;
    flex-direction:column;
    gap:8px;
}

.side-context{
    background:rgba(255,255,255,.08);
    border-radius:10px;
    padding:10px 12px;
    font-size:12px;
    line-height:1.6;
    margin-bottom:10px;
}

.side-context b{
    display:block;
    font-size:11px;
    text-transform:uppercase;
    letter-spacing:.06em;
    color:#CBD5E1;
    margin-bottom:2px;
}

.smart-card{
    background:white;
    color:#1E293B;
    display:flex;
    align-items:center;
    gap:10px;
    border-radius:12px;
    padding:10px 12px;
    border:1px solid rgba(0,0,0,.04);
    margin-bottom:10px;
    transition:.2s;
}

.smart-card:hover{
    transform:translateY(-1px);
    box-shadow:0 8px 20px rgba(0,0,0,.12);
}

.card-icon{
    width:34px;
    height:34px;
    flex:0 0 auto;
    border-radius:9px;
    background:#ECFEFF;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:16px;
}

.card-btn{
    flex:1;
    background:none !important;
    border:none !important;
    color:#1E293B !important;
    font-weight:600;
    cursor:pointer;
    text-align:left;
    font-size:13px;
}

.match-table{
    width:100%;
    font-size:12px;
    border-collapse:collapse;
}

.match-table th,
.match-table td{
    padding:4px 6px;
    text-align:center;
}

.match-table th{
    color:#0F172A;
    font-weight:700;
    border-bottom:1px solid #E2E8F0;
}

/* ==========================
   HEADER / CONTEXT BAR
========================== */

.context-bar{
    background:var(--card);
    border-radius:var(--radius);
    padding:14px 18px;
    margin-bottom:16px;
    border:1px solid var(--border);
    box-shadow:var(--shadow);
    display:flex;
    flex-wrap:wrap;
    gap:18px;
    align-items:center;
}

.context-item{
    font-size:13px;
}

.context-item b{
    display:block;
    font-size:10px;
    text-transform:uppercase;
    letter-spacing:.06em;
    color:var(--text-light);
}

.context-nav{
    margin-left:auto;
    display:flex;
    gap:8px;
}

/* ==========================
   TOOLBAR / BUTTONS
========================== */

.header{
    background:var(--card);
    border-radius:var(--radius);
    padding:16px;
    margin-bottom:16px;
    border:1px solid var(--border);
    box-shadow:var(--shadow);
}

.toolbar{
    display:flex;
    flex-wrap:wrap;
    gap:10px;
    align-items:center;
}

.btn{
    background:linear-gradient(0deg,#1C3737,var(--primary-light));
    border:none;
    border-radius:9px;
    padding:9px 16px;
    font-weight:600;
    font-size:13px;
    cursor:pointer;
    color:white;
    transition:.15s;
}

.btn:hover{
    filter:brightness(1.08);
    transform:translateY(-1px);
}

.btn:focus-visible,
.btn-primary:focus-visible,
.circle-btn:focus-visible,
.card-btn:focus-visible{
    outline:3px solid var(--secondary);
    outline-offset:2px;
}

.btn-primary{
    background:linear-gradient(0deg,#1C3737,var(--primary-light));
    border:none;
    border-radius:9px;
    padding:9px 18px;
    font-weight:600;
    font-size:13px;
    color:white;
    cursor:pointer;
}

.btn-secondary{
    background:var(--card);
    border:1px solid var(--border);
    border-radius:9px;
    padding:9px 16px;
    font-weight:600;
    font-size:13px;
    color:var(--text);
    cursor:pointer;
}

.btn-danger{
    background:var(--danger);
    color:white;
    border:none;
    border-radius:8px;
    padding:6px 10px;
    font-size:13px;
    cursor:pointer;
}

.btn[disabled],
.btn-primary[disabled]{
    opacity:.55;
    cursor:not-allowed;
    transform:none;
}

.circle-btn{
    width:34px;
    height:34px;
    line-height:32px;
    border-radius:50%;
    text-align:center;
    text-decoration:none;
    border:1px solid var(--primary-light);
    background:var(--card);
    color:var(--primary-light);
    display:inline-block;
    transition:.2s;
    cursor:pointer;
}

.circle-btn:hover{
    background:var(--primary-light);
    color:white;
}

/* ==========================
   GRID
========================== */

.grid-container{
    background:var(--card);
    border-radius:var(--radius);
    border:1px solid var(--border);
    padding:10px;
    box-shadow:var(--shadow);
    overflow:auto;
    margin-bottom:16px;
}

.gridview{
    width:100%;
    min-width:1500px;
    background:var(--card);
    border-collapse:collapse;
    table-layout:auto;
}

.gridview th{
    background:linear-gradient(135deg,var(--primary),var(--primary-light));
    color:white;
    font-size:12px;
    font-weight:600;
    padding:8px 6px;
    text-align:center;
    vertical-align:middle;
    position:sticky;
    top:0;
    z-index:5;
    border-right:1px solid rgba(255,255,255,.25);
}

.gridview td{
    padding:8px 6px;
    border-bottom:1px solid var(--border);
    vertical-align:top;
    white-space:normal;
    word-break:break-word;
    font-size:13px;
}

.gridview input[type=text],
.gridview select{
    width:100%;
    min-width:80px;
}

.row-invalid{
    outline:2px solid var(--danger);
    outline-offset:-2px;
    background:var(--danger-bg) !important;
}

.field-hint{
    font-size:11px;
    color:var(--text-light);
}

.empty-state{
    text-align:center;
    padding:26px 12px;
    color:var(--text-light);
    font-size:13px;
}

.badge{
    display:inline-block;
    padding:2px 8px;
    border-radius:999px;
    font-size:11px;
    font-weight:700;
}

.badge-ok{ background:var(--success-bg); color:var(--success); }
.badge-warn{ background:var(--warning-bg); color:var(--warning); }
.badge-danger{ background:var(--danger-bg); color:var(--danger); }

/* ==========================
   STEP LABELS / DDR SECTIONS
========================== */

.step-label{
    width:max-content;
    background:linear-gradient(135deg,var(--primary-light),var(--primary));
    color:white;
    padding:6px 14px;
    border-radius:20px;
    font-size:11px;
    font-weight:700;
    letter-spacing:1px;
    text-transform:uppercase;
    margin-bottom:10px;
}

.smart-card-ddr{
    background:var(--card);
    border:1px solid var(--border);
    border-radius:var(--radius);
    padding:14px;
    margin-bottom:14px;
    box-shadow:var(--shadow);
}

/* ==========================
   INPUTS / VALIDATION
========================== */

input[type=text],
textarea,
select{
    padding:9px 10px;
    border:1px solid var(--border);
    border-radius:9px;
    background:var(--card);
    color:var(--text);
    width:100%;
    font-size:13px;
}

input[type=text]:focus,
textarea:focus,
select:focus{
    outline:none;
    border-color:var(--primary-light);
    box-shadow:0 0 0 4px rgba(20,184,166,.15);
}

.validation-summary{
    background:var(--danger-bg);
    border:1px solid var(--danger);
    color:var(--danger);
    border-radius:10px;
    padding:10px 14px;
    margin-bottom:14px;
    font-size:13px;
}

.validation-summary ul{
    margin:6px 0 0 18px;
}

/* ==========================
   LOADER
========================== */

.loader-overlay{
    display:none;
    position:fixed;
    top:0;
    left:0;
    width:100%;
    height:100%;
    background:rgba(15,23,42,.75);
    z-index:99999;
}

.cmd-loader{
    position:absolute;
    top:50%;
    left:50%;
    transform:translate(-50%,-50%);
    background:#111827;
    color:var(--primary-light);
    padding:26px 30px;
    border-radius:16px;
    font-family:Consolas,monospace;
}

.form-row{
    margin-top:14px;
    display:flex;
    align-items:center;
    flex-wrap:wrap;
    gap:10px;
}

/* ==========================
   TOASTS
========================== */

.toast-stack{
    position:fixed;
    top:16px;
    right:16px;
    z-index:100000;
    display:flex;
    flex-direction:column;
    gap:10px;
    max-width:340px;
}

.toast{
    background:var(--card);
    border:1px solid var(--border);
    border-left:4px solid var(--primary-light);
    border-radius:10px;
    padding:12px 14px;
    box-shadow:0 10px 25px rgba(0,0,0,.15);
    font-size:13px;
    animation:toast-in .2s ease;
}

.toast.success{ border-left-color:var(--success); }
.toast.error{ border-left-color:var(--danger); }
.toast.info{ border-left-color:var(--secondary); }

@keyframes toast-in{
    from{ opacity:0; transform:translateY(-8px); }
    to{ opacity:1; transform:translateY(0); }
}

/* ==========================
   CONFIRM MODAL
========================== */

dialog.confirm-dialog{
    border:none;
    border-radius:14px;
    padding:0;
    width:min(360px,90vw);
    box-shadow:0 20px 50px rgba(0,0,0,.25);
}

dialog.confirm-dialog::backdrop{
    background:rgba(15,23,42,.5);
}

.confirm-body{
    padding:20px;
}

.confirm-actions{
    display:flex;
    justify-content:flex-end;
    gap:8px;
    padding:14px 20px;
    border-top:1px solid var(--border);
}

/* ==========================
   PLIP / CTD DRAWER
========================== */

.drawer{
    position:fixed;
    top:0;
    right:-100%;
    width:min(560px,100%);
    height:100vh;
    background:var(--card);
    box-shadow:-10px 0 30px rgba(0,0,0,.2);
    z-index:9999;
    transition:right .3s ease;
    display:flex;
    flex-direction:column;
}

.drawer.open{
    right:0;
}

.drawer-overlay{
    position:fixed;
    inset:0;
    background:rgba(15,23,42,.4);
    display:none;
    z-index:9998;
    backdrop-filter:blur(2px);
}

.drawer-overlay.show{
    display:block;
}

.drawer-header{
    height:56px;
    background:linear-gradient(135deg,var(--primary),var(--primary-light));
    color:white;
    display:flex;
    align-items:center;
    justify-content:space-between;
    padding:0 16px;
    flex:0 0 auto;
}

.drawer-close{
    cursor:pointer;
    font-size:20px;
    background:none;
    border:none;
    color:white;
}

.drawer-body{
    padding:16px;
    overflow-y:auto;
    flex:1 1 auto;
}

.search-group{
    margin-bottom:12px;
}

.search-group label{
    display:block;
    margin-bottom:4px;
    font-size:12px;
    font-weight:600;
    color:var(--text-light);
}

.drawer-grid{
    width:100%;
    font-size:12px;
    border-collapse:collapse;
}

.drawer-grid th{
    background:var(--primary-light);
    color:white;
    padding:6px;
    position:sticky;
    top:0;
}

.drawer-grid td{
    padding:6px;
    border-bottom:1px solid var(--border);
}

.plip-link{
    background:none;
    border:none;
    color:var(--primary-dark);
    font-weight:700;
    cursor:pointer;
    text-decoration:underline;
}

.visually-hidden{
    position:absolute !important;
    width:1px;height:1px;
    padding:0;margin:-1px;
    overflow:hidden;
    clip:rect(0,0,0,0);
    white-space:nowrap;border:0;
}
    </style>

    <script>
        function showLoader() {
            document.getElementById("loaderOverlay").style.display = "block";
            return true;
        }

        function toggleSidebar() {
            document.querySelector(".sidebar").classList.toggle("open");
        }

        function openDrawer(id, overlayId) {
            document.getElementById(id).classList.add("open");
            document.getElementById(overlayId).classList.add("show");
        }

        function closeDrawer(id, overlayId) {
            document.getElementById(id).classList.remove("open");
            document.getElementById(overlayId).classList.remove("show");
        }

        function openPLIPDrawer() { openDrawer("plipDrawer", "drawerOverlay"); }
        function closePLIPDrawer() { closeDrawer("plipDrawer", "drawerOverlay"); }
        function openCTDDrawer() { openDrawer("CTDDrawer", "drawerOverlay1"); }
        function closeCTDDrawer() { closeDrawer("CTDDrawer", "drawerOverlay1"); }

        function toggleRemarks(link) {
            var div = link.parentNode.querySelector("div");
            var expanded = div.style.display !== "none";
            div.style.display = expanded ? "none" : "block";
            link.textContent = expanded ? "Show remarks" : "Hide remarks";
            link.setAttribute("aria-expanded", (!expanded).toString());
        }

        // Generic confirm-before-postback helper for destructive LinkButton actions.
        // The LinkButton already renders a javascript:__doPostBack(...) href; we
        // capture it, show an accessible <dialog> instead of window.confirm(),
        // and only replay the captured call if the user confirms.
        var pendingAction = null;

        function confirmAction(el, message) {
            pendingAction = el.getAttribute("href");
            document.getElementById("confirmMessage").textContent = message;
            document.getElementById("confirmModal").showModal();
            return false;
        }

        document.addEventListener("DOMContentLoaded", function () {
            var yesBtn = document.getElementById("confirmYesBtn");
            var noBtn = document.getElementById("confirmNoBtn");
            var modal = document.getElementById("confirmModal");

            if (yesBtn) {
                yesBtn.addEventListener("click", function () {
                    modal.close();
                    var action = pendingAction;
                    pendingAction = null;
                    if (action) {
                        // action looks like: javascript:__doPostBack('id','arg')
                        eval(action.replace(/^javascript:/, ""));
                    }
                });
            }
            if (noBtn) {
                noBtn.addEventListener("click", function () {
                    pendingAction = null;
                    modal.close();
                });
            }
        });

        // Toast helper. Server code calls showToast(type, message) via a
        // registered startup script using HttpUtility.JavaScriptStringEncode,
        // so message text is always safely escaped before it reaches here.
        function showToast(type, message) {
            var stack = document.getElementById("toastStack");
            if (!stack) return;
            var toast = document.createElement("div");
            toast.className = "toast " + type;
            toast.setAttribute("role", "status");
            toast.textContent = message;
            stack.appendChild(toast);
            window.setTimeout(function () {
                toast.remove();
            }, 5000);
        }

        function validateDdrRow(row) {
            var docNo = row.querySelector("[id$='txtDocumentNo']");
            var title = row.querySelector("[id$='txtTitle']");
            var ramz = row.querySelector("[id$='ddlRamz']");
            var ok = true;
            [docNo, title, ramz].forEach(function (el) {
                if (el && !el.value) {
                    el.classList.add("row-invalid");
                    ok = false;
                } else if (el) {
                    el.classList.remove("row-invalid");
                }
            });
            return ok;
        }

        function validateAllDdrRows() {
            var ok = true;
            document.querySelectorAll(".gridview tbody tr").forEach(function (row) {
                if (row.querySelector("[id$='txtDocumentNo']") && !validateDdrRow(row)) {
                    ok = false;
                }
            });
            if (!ok) {
                showToast("error", "Please complete Document No, Title and RAMZ ID on the highlighted rows.");
            }
            return ok;
        }
    </script>
</head>

<body>

<form id="smartddrv2" runat="server" enctype="multipart/form-data">
    <asp:ScriptManager
        ID="ScriptManager1"
        runat="server" />

    <div class="mobile-topbar">
        <button type="button" class="hamburger" onclick="toggleSidebar();" aria-label="Toggle navigation">&#9776;</button>
        <strong>SmartDDR</strong>
        <span></span>
    </div>

    <div id="toastStack" class="toast-stack" aria-live="polite"></div>

    <dialog id="confirmModal" class="confirm-dialog">
        <div class="confirm-body">
            <p id="confirmMessage">Are you sure?</p>
        </div>
        <div class="confirm-actions">
            <button type="button" id="confirmNoBtn" class="btn-secondary">Cancel</button>
            <button type="button" id="confirmYesBtn" class="btn-danger">Confirm</button>
        </div>
    </dialog>

    <div class="container">
        <!-- Sidebar -->
        <div class="sidebar">

            <div class="logo">
                <h2>SmartDDR</h2>
                <small>DDR Developer</small>
            </div>

            <div class="side-context">
                <b>Session context</b>
                <asp:Label ID="lblProject" runat="server" Text="" CssClass="visually-hidden" />
                <asp:Label ID="lblContext" runat="server" Text="" CssClass="visually-hidden" />
                <asp:Label ID="lblRef" runat="server" Text="" CssClass="visually-hidden" />
                <asp:Label ID="lblDiscipline" runat="server" Text="" CssClass="visually-hidden" />
                <asp:Label ID="lblDocMode" runat="server" Visible="false" Text="" />
                See context bar above the grids &rarr;
            </div>

            <div class="side-nav">
                <button type="button" class="btn" onclick="history.back();">&#8592; Back</button>

                <asp:Button ID="btnHome"
                    runat="server"
                    Text="Home"
                    CssClass="btn"
                    OnClick="Home_Go" />

                <div class="smart-card">
                    <asp:GridView ID="ctd_ddr_match"
                        runat="server"
                        CssClass="match-table"
                        OnRowDataBound="ctd_ddr_match_RowDataBound"
                        AutoGenerateColumns="False"
                        DataSourceID="CTDDDRSOURCE"
                        ShowHeaderWhenEmpty="True"
                        GridLines="None">
                        <Columns>
                            <asp:BoundField DataField="CTD Hrs" HeaderText="CTD Hrs" SortExpression="CTD Hrs" />
                            <asp:BoundField DataField="DDR Hrs" HeaderText="DDR Hrs" SortExpression="DDR Hrs" />
                        </Columns>
                        <EmptyDataTemplate>
                            <span class="field-hint">No CTD/DDR hours yet.</span>
                        </EmptyDataTemplate>
                    </asp:GridView>

                    <asp:SqlDataSource ID="CTDDDRSOURCE" runat="server"
                        ConnectionString="<%$ ConnectionStrings:ACAD_DATAConn1 %>"
                        SelectCommand="SELECT [CTD Hrs],[DDR Hrs] FROM [ACAD_DATA].[dbo].[SDDR_CTD_VIEW] WHERE CTD_ID=@CTD_ID">
                        <SelectParameters>
                            <asp:QueryStringParameter DefaultValue="0" Name="CTD_ID" QueryStringField="CTD_ID" Type="Int32" />
                        </SelectParameters>
                    </asp:SqlDataSource>
                </div>

                <div class="smart-card">
                    <div class="card-icon" aria-hidden="true">&#128196;</div>
                    <asp:Button ID="DDR1" runat="server"
                        Text="DDR + Activity"
                        CssClass="card-btn"
                        OnClick="DDR1_Click"
                        OnClientClick="showLoader();"
                        ToolTip="Add one document row and one activity row" />
                </div>

                <div id="loaderOverlay" class="loader-overlay" role="status" aria-live="assertive">
                    <div class="cmd-loader">
                        <span>C:\&gt;</span>
                        <span>Processing request&hellip;</span>
                    </div>
                </div>
            </div>
        </div>

        <!-- Main Content -->
        <div class="main">
            <asp:HiddenField ID="hfSelectedRow" runat="server" />

            <!-- Context bar -->
            <div class="context-bar">
                <div class="context-item">
                    <b>Project</b>
                    <asp:Literal ID="litProject" runat="server" />
                </div>
                <div class="context-item">
                    <b>Discipline</b>
                    <asp:Literal ID="litDiscipline" runat="server" />
                </div>
                <div class="context-item">
                    <b>Deliverable Ref</b>
                    <asp:Literal ID="litRef" runat="server" />
                </div>
                <div class="context-nav">
                    <asp:LinkButton ID="btnCtdPrev" runat="server" CssClass="circle-btn" ToolTip="Previous CTD" OnClick="CTD_Prev">&#8592;</asp:LinkButton>
                    <asp:LinkButton ID="btnCtdNext" runat="server" CssClass="circle-btn" ToolTip="Next CTD" OnClick="CTD_Next">&#8594;</asp:LinkButton>
                </div>
            </div>

            <asp:Panel ID="pnlValidationSummary" runat="server" CssClass="validation-summary" Visible="false">
                <strong>Please fix the following before saving:</strong>
                <asp:Literal ID="litValidationErrors" runat="server" />
            </asp:Panel>

            <!-- CTD Information Card -->
            <div class="smart-card-ddr">
                <div class="step-label">CTD Reference</div>
                <asp:GridView ID="CTD_Grid"
                    runat="server"
                    CssClass="gridview"
                    OnRowDataBound="CTD_Grid_RowDataBound"
                    AutoGenerateColumns="False"
                    DataSourceID="CTD_Source"
                    ShowHeaderWhenEmpty="True"
                    DataKeyNames="CTD_ID">
                    <Columns>
                        <asp:TemplateField HeaderText="CTD">
                            <ItemTemplate>
                                <asp:TextBox ID="txtCTD" CssClass="btn"
                                    runat="server"
                                    Width="100px"
                                    ReadOnly="True"
                                    Text='<%#Eval("CTD_ID") %>' />
                                <asp:LinkButton ID="btnCTDSearch"
                                    runat="server"
                                    Text="&#128269;"
                                    ToolTip="Search another CTD"
                                    OnClick="btnCTDSearch_Click" />
                            </ItemTemplate>
                            <ItemStyle HorizontalAlign="Center" VerticalAlign="Middle" Width="150px" Wrap="False" />
                        </asp:TemplateField>

                        <asp:BoundField DataField="Project_No" HeaderText="Project_No" SortExpression="Project_No">
                            <ItemStyle HorizontalAlign="Center" VerticalAlign="Middle" Width="80px" />
                        </asp:BoundField>
                        <asp:BoundField DataField="Ramz_ID" HeaderText="Ramz_ID" SortExpression="Ramz_ID">
                            <ItemStyle HorizontalAlign="Center" VerticalAlign="Middle" Width="120px" Wrap="False" />
                        </asp:BoundField>
                        <asp:BoundField DataField="Discipline" HeaderText="Discipline" SortExpression="Discipline">
                            <ItemStyle HorizontalAlign="Center" VerticalAlign="Middle" Width="150px" Wrap="False" />
                        </asp:BoundField>
                        <asp:BoundField DataField="Discipline_Code" HeaderText="Code" SortExpression="Discipline_Code">
                            <ItemStyle HorizontalAlign="Center" VerticalAlign="Middle" Width="50px" Wrap="False" />
                        </asp:BoundField>
                        <asp:BoundField DataField="Del_Item_Ref" HeaderText="Ref" SortExpression="Del_Item_Ref">
                            <ItemStyle HorizontalAlign="Center" VerticalAlign="Middle" Width="80px" Wrap="False" />
                        </asp:BoundField>
                        <asp:BoundField DataField="Deliverable_Title" HeaderText="Deliverable Type" SortExpression="Deliverable_Title">
                            <ItemStyle HorizontalAlign="Left" VerticalAlign="Middle" Width="300px" />
                        </asp:BoundField>
                        <asp:BoundField DataField="Deliverable" HeaderText="Deliverable" SortExpression="Deliverable">
                            <ItemStyle HorizontalAlign="Left" VerticalAlign="Middle" Width="300px" />
                        </asp:BoundField>
                        <asp:BoundField DataField="Total_Hours" HeaderText="CTD Hours" SortExpression="Total_Hours">
                            <ItemStyle HorizontalAlign="Center" VerticalAlign="Middle" Width="80px" />
                        </asp:BoundField>
                        <asp:BoundField DataField="Qty" HeaderText="Qty" SortExpression="Qty">
                            <ItemStyle HorizontalAlign="Center" VerticalAlign="Middle" Width="40px" />
                        </asp:BoundField>
                        <asp:BoundField DataField="Doc_Mode" HeaderText="Type" SortExpression="Doc_Mode">
                            <ItemStyle HorizontalAlign="Left" VerticalAlign="Middle" Width="150px" />
                        </asp:BoundField>
                        <asp:BoundField DataField="CTD_Remarks" HeaderText="Remarks" SortExpression="CTD_Remarks">
                            <ItemStyle Width="100px" HorizontalAlign="Left" VerticalAlign="Middle" />
                        </asp:BoundField>
                    </Columns>
                    <RowStyle BackColor="#FFFFF4" VerticalAlign="Middle" />
                    <EmptyDataTemplate>
                        <div class="empty-state">No CTD record found for this ID.</div>
                    </EmptyDataTemplate>
                </asp:GridView>

                <asp:SqlDataSource ID="CTD_Source" runat="server" ConnectionString="<%$ ConnectionStrings:ACAD_DATAConn1 %>"
                    SelectCommand="SELECT * FROM CTD_MASTER WHERE CTD_ID=@CTD_ID">
                    <SelectParameters>
                        <asp:QueryStringParameter DefaultValue="0" Name="CTD_ID" QueryStringField="CTD_ID" Type="Int32" />
                    </SelectParameters>
                </asp:SqlDataSource>
            </div>

            <!-- Action Toolbar -->
            <div class="header">
                <div class="toolbar">
                    <asp:Button ID="Plip_Search" runat="server" Text="PLIP Search" CssClass="btn"
                        OnClientClick="openPLIPDrawer(); return false;" />

                    <asp:Button ID="CSV_Template" runat="server" Text="Download CSV Template" CssClass="btn"
                        OnClick="CSV_Template_Click" CausesValidation="False" />

                    <asp:FileUpload ID="fuCsv" runat="server" />
                    <asp:Button ID="CSV_Upload" runat="server" Text="Upload CSV" CssClass="btn"
                        OnClick="CSV_Upload_Click" CausesValidation="False" />
                </div>
            </div>

            <!-- DDR Items -->
            <div class="smart-card-ddr">
                <div class="step-label">DDR Line Items</div>
                <div class="toolbar">
                    <asp:Button ID="btnAddDDR"
                        runat="server"
                        Text="Add DDR Line"
                        CssClass="btn-primary"
                        OnClick="btnAddDDR_Click"
                        CausesValidation="False" />

                    <asp:Button ID="btnSaveAll"
                        runat="server"
                        Text="Save All"
                        CssClass="btn-primary"
                        OnClick="btnSaveAll_Click"
                        OnClientClick="return validateAllDdrRows();" />
                </div>
            </div>

            <div class="grid-container">
                <asp:GridView ID="grdDDREntry"
                    runat="server"
                    AutoGenerateColumns="False"
                    CssClass="gridview"
                    DataKeyNames="DDR_ID"
                    DataSourceID="DDR_Entry_Source"
                    OnRowDataBound="grdDDREntry_RowDataBound"
                    OnRowCommand="grdDDREntry_RowCommand"
                    ShowHeaderWhenEmpty="True">
                    <Columns>
                        <asp:TemplateField Visible="false">
                            <ItemTemplate>
                                <asp:HiddenField ID="hdnDDRID"
                                    runat="server"
                                    Value='<%# Eval("DDR_ID") %>' />
                            </ItemTemplate>
                        </asp:TemplateField>

                        <asp:TemplateField HeaderText="Type">
                            <ItemTemplate>
                                <asp:DropDownList ID="ddlType"
                                    runat="server"
                                    Width="100px"
                                    AutoPostBack="true"
                                    OnSelectedIndexChanged="ddlType_SelectedIndexChanged">
                                    <asp:ListItem Text="NEW" Value="NEW" />
                                    <asp:ListItem Text="EXISTING" Value="EXISTING" />
                                    <asp:ListItem Text="ACTIVITY" Value="ACTIVITY" />
                                </asp:DropDownList>
                            </ItemTemplate>
                        </asp:TemplateField>

                        <asp:TemplateField HeaderText="PLIP">
                            <ItemTemplate>
                                <asp:TextBox ID="txtPLIP"
                                    runat="server"
                                    Width="90px"
                                    AutoPostBack="True"
                                    OnTextChanged="txtPLIP_TextChanged"
                                    Text='<%# If(Eval("Document_No").ToString().ToUpper().Contains("ACTIVITY"), "", Eval("PLIP_ID")) %>'
                                    Enabled='<%# Not Eval("Document_No").ToString().ToUpper().Contains("ACTIVITY") %>' />
                                <asp:LinkButton ID="btnPLIPSearch"
                                    runat="server"
                                    Text="&#128269;"
                                    OnClick="btnPLIPSearch_Click"
                                    Enabled='<%# Not Eval("Document_No").ToString().ToUpper().Contains("ACTIVITY") %>' />
                            </ItemTemplate>
                        </asp:TemplateField>

                        <asp:TemplateField HeaderText="Area">
                            <ItemTemplate>
                                <asp:DropDownList ID="ddlArea"
                                    runat="server"
                                    Width="50px"
                                    DataSourceID="AreaSource"
                                    DataTextField="AU_CODE"
                                    DataValueField="AU_CODE"
                                    Enabled='<%# Not Eval("Document_No").ToString().ToUpper().Contains("ACTIVITY") %>'>
                                </asp:DropDownList>

                                <asp:TextBox ID="txtArea"
                                    runat="server"
                                    Width="80px"
                                    AutoPostBack="True"
                                    OnTextChanged="txtAREA_TextChanged"
                                    Text='<%# If(Eval("Document_No").ToString().ToUpper().Contains("ACTIVITY"),"",Eval("Document_No").ToString().Substring(0, Math.Min(6, Eval("Document_No").ToString().Length))) %>'
                                    Enabled='<%# Not Eval("Document_No").ToString().ToUpper().Contains("ACTIVITY") %>' />
                            </ItemTemplate>
                        </asp:TemplateField>

                        <asp:TemplateField HeaderText="RAMZ ID">
                            <ItemTemplate>
                                <asp:DropDownList ID="ddlRamz"
                                    runat="server"
                                    Width="140px"
                                    DataSourceID="RamzSource"
                                    DataTextField="RAMZ_ID"
                                    DataValueField="RAMZ_ID">
                                </asp:DropDownList>
                            </ItemTemplate>
                        </asp:TemplateField>

                        <asp:TemplateField HeaderText="Document No">
                            <ItemTemplate>
                                <asp:TextBox ID="txtDocumentNo"
                                    runat="server"
                                    Width="270px"
                                    Text='<%# Bind("Document_No") %>' />
                            </ItemTemplate>
                        </asp:TemplateField>

                        <asp:TemplateField HeaderText="Document Title">
                            <ItemTemplate>
                                <asp:TextBox ID="txtTitle"
                                    runat="server"
                                    Width="300px"
                                    Text='<%# Bind("Document_Title") %>' />
                            </ItemTemplate>
                        </asp:TemplateField>

                        <asp:TemplateField HeaderText="Hours">
                            <ItemTemplate>
                                <asp:TextBox ID="txtHours"
                                    runat="server"
                                    Width="50px"
                                    Text='<%# Bind("Man_Hours") %>' />
                                <asp:CompareValidator ID="cvHours" runat="server"
                                    ControlToValidate="txtHours"
                                    Type="Double"
                                    Operator="GreaterThanEqual"
                                    ValueToCompare="0"
                                    Display="Dynamic"
                                    ErrorMessage="Hours must be a number &gt;= 0"
                                    CssClass="field-hint" />
                            </ItemTemplate>
                        </asp:TemplateField>

                        <asp:TemplateField HeaderText="AFC/APP">
                            <ItemTemplate>
                                <asp:DropDownList ID="ddlStatus"
                                    runat="server"
                                    Width="60px"
                                    SelectedValue='<%# Bind("HO_STATUS") %>'>
                                    <asp:ListItem Text="" Value="" />
                                    <asp:ListItem Text="AFC" Value="AFC" />
                                    <asp:ListItem Text="APP" Value="APP" />
                                </asp:DropDownList>
                            </ItemTemplate>
                        </asp:TemplateField>

                        <asp:TemplateField HeaderText="Critical?">
                            <ItemTemplate>
                                <asp:Label ID="lblCriticality" runat="server" Text='<%# Eval("CRITICALITY") %>' />
                            </ItemTemplate>
                        </asp:TemplateField>
                        <asp:TemplateField HeaderText="PLIP_HO">
                            <ItemTemplate>
                                <asp:Label ID="lblHoReq" runat="server" Text='<%# Eval("HO_REQ") %>' />
                            </ItemTemplate>
                        </asp:TemplateField>

                        <asp:TemplateField HeaderText="Software">
                            <ItemTemplate>
                                <asp:TextBox ID="txtSoftware"
                                    runat="server"
                                    Width="100px"
                                    Text='<%# Bind("Software") %>' />
                            </ItemTemplate>
                        </asp:TemplateField>

                        <asp:TemplateField HeaderText="Remarks">
                            <ItemTemplate>
                                <a href="javascript:void(0);" onclick="toggleRemarks(this); return false;" aria-expanded="false">Show remarks</a>
                                <div style="display: none; margin-top: 5px;">
                                    <asp:TextBox ID="txtRemarks"
                                        runat="server"
                                        Width="250px"
                                        TextMode="MultiLine"
                                        Rows="3"
                                        Text='<%# Bind("Disc_Remarks") %>'>
                                    </asp:TextBox>
                                </div>
                            </ItemTemplate>
                        </asp:TemplateField>

                        <asp:TemplateField HeaderText="Action">
                            <ItemTemplate>
                                <asp:LinkButton ID="btnDelete"
                                    runat="server"
                                    Text="&#128465;"
                                    CssClass="btn-danger"
                                    CommandName="DeleteDDR"
                                    CommandArgument='<%# Eval("DDR_ID") %>'
                                    OnClientClick="return confirmAction(this, 'Delete this DDR item?');" />
                            </ItemTemplate>
                        </asp:TemplateField>
                    </Columns>
                    <EmptyDataTemplate>
                        <div class="empty-state">No DDR items yet &mdash; click "Add DDR Line" to start.</div>
                    </EmptyDataTemplate>
                </asp:GridView>
            </div>
        </div>
    </div>

    <asp:SqlDataSource ID="DDR_Entry_Source" runat="server" ConnectionString="<%$ ConnectionStrings:ACAD_DATAConn1 %>"
        DeleteCommand="DELETE FROM [CTD_DDR_DISC] WHERE [DDR_ID] = @DDR_ID"
        InsertCommand="INSERT INTO [CTD_DDR_DISC] ([CTD_ID], [PLIP_ID], [RAMZ_ID], [Document_No], [Document_Title], [Man_Hours], [HO_STATUS], [CRITICALITY], [HO_REQ], [Disc_Remarks], [Software]) VALUES (@CTD_ID, @PLIP_ID, @RAMZ_ID, @Document_No, @Document_Title, @Man_Hours, @HO_STATUS, @CRITICALITY, @HO_REQ, @Disc_Remarks, @Software)"
        SelectCommand="SELECT [CTD_ID], [DDR_ID], [PLIP_ID], [RAMZ_ID], [Document_No], [Document_Title], [Man_Hours], [HO_STATUS], [CRITICALITY], [HO_REQ], [Disc_Remarks], [Software] FROM [CTD_DDR_DISC] WHERE ([CTD_ID] = @CTD_ID)"
        UpdateCommand="UPDATE [CTD_DDR_DISC] SET [PLIP_ID] = @PLIP_ID, [RAMZ_ID] = @RAMZ_ID, [Document_No] = @Document_No, [Document_Title] = @Document_Title, [Man_Hours] = @Man_Hours, [HO_STATUS] = @HO_STATUS, [Disc_Remarks] = @Disc_Remarks, [Software] = @Software WHERE [DDR_ID] = @DDR_ID">
        <DeleteParameters>
            <asp:Parameter Name="DDR_ID" Type="Int32" />
        </DeleteParameters>
        <InsertParameters>
            <asp:Parameter Name="CTD_ID" Type="Int32" />
            <asp:Parameter Name="PLIP_ID" Type="String" />
            <asp:Parameter Name="RAMZ_ID" Type="String" />
            <asp:Parameter Name="Document_No" Type="String" />
            <asp:Parameter Name="Document_Title" Type="String" />
            <asp:Parameter Name="Man_Hours" Type="Decimal" />
            <asp:Parameter Name="HO_STATUS" Type="String" />
            <asp:Parameter Name="CRITICALITY" Type="String" />
            <asp:Parameter Name="HO_REQ" Type="String" />
            <asp:Parameter Name="Disc_Remarks" Type="String" />
            <asp:Parameter Name="Software" Type="String" />
        </InsertParameters>
        <SelectParameters>
            <asp:QueryStringParameter DefaultValue="0" Name="CTD_ID" QueryStringField="CTD_ID" Type="Int32" />
        </SelectParameters>
        <UpdateParameters>
            <asp:Parameter Name="PLIP_ID" Type="String" />
            <asp:Parameter Name="RAMZ_ID" Type="String" />
            <asp:Parameter Name="Document_No" Type="String" />
            <asp:Parameter Name="Document_Title" Type="String" />
            <asp:Parameter Name="Man_Hours" Type="Decimal" />
            <asp:Parameter Name="HO_STATUS" Type="String" />
            <asp:Parameter Name="Disc_Remarks" Type="String" />
            <asp:Parameter Name="Software" Type="String" />
            <asp:Parameter Name="DDR_ID" Type="Int32" />
        </UpdateParameters>
    </asp:SqlDataSource>

    <asp:SqlDataSource ID="CTD_Search_Source" runat="server" ConnectionString="<%$ ConnectionStrings:ACAD_DATAConn1 %>"
        SelectCommand="SELECT [CTD_ID],[Discipline],[Del_Item_Ref],[Deliverable],[CTD Hrs],[DDR Hrs],[CTD Qty],[DDR Qty] FROM [ACAD_DATA].[dbo].[SDDR_CTD_VIEW] WHERE PROJECT_NO=@PROJECT_NO AND DISCIPLINE=@DISCIPLINE">
        <SelectParameters>
            <asp:ControlParameter ControlID="lblProject" Name="PROJECT_NO" PropertyName="Text" />
            <asp:ControlParameter ControlID="lblDiscipline" Name="DISCIPLINE" PropertyName="Text" />
        </SelectParameters>
    </asp:SqlDataSource>

    <asp:SqlDataSource
        ID="RamzSource"
        runat="server"
        ConnectionString="<%$ ConnectionStrings:ACAD_DATAConn1 %>"
        SelectCommand="SELECT value AS RAMZ_ID FROM Project_Info CROSS APPLY STRING_SPLIT(RAMZ_ID, ',') WHERE PROJECT_NO = @PROJECT_NO">
        <SelectParameters>
            <asp:ControlParameter ControlID="lblProject" Name="PROJECT_NO" PropertyName="Text" />
        </SelectParameters>
    </asp:SqlDataSource>

    <asp:SqlDataSource
        ID="AreaSource"
        runat="server"
        ConnectionString="<%$ ConnectionStrings:ACAD_DATAConn1 %>"
        SelectCommand="SELECT DISTINCT LEFT(TRIM(Document_No),6) AS AU_CODE FROM [ACAD_DATA].[dbo].[SMARTDDR01] WHERE Document_No NOT LIKE '%ACTIVITY%' AND Project_No=@PROJECT_NO ORDER BY LEFT(TRIM(Document_No),6)">
        <SelectParameters>
            <asp:ControlParameter ControlID="lblProject" Name="PROJECT_NO" PropertyName="Text" />
        </SelectParameters>
    </asp:SqlDataSource>

    <asp:SqlDataSource
        ID="PLIPSource"
        runat="server"
        ConnectionString="<%$ ConnectionStrings:ACAD_DATAConn1 %>"
        SelectCommand="SELECT [PLIP_ID],[Information_Required] AS [PLIP_Title],[Discipline_Code]+'-'+[Document_Type_Name] AS [Doc_Type],[Critical_Documentation] AS [Critical],[Required_Handover_Status] AS [FHO_Status],[DCAFID_Latest] AS [DCAF] FROM [ACAD_DATA].[dbo].[SPO_PLIP] WHERE [Active_YN]='YES' AND (@SEARCH = '' OR [Information_Required] LIKE '%' + @SEARCH + '%' OR [PLIP_ID] LIKE '%' + @SEARCH + '%') ORDER BY [PLIP_ID]">
        <SelectParameters>
            <asp:ControlParameter ControlID="txtSearchPLIP" Name="SEARCH" PropertyName="Text" DefaultValue="" Type="String" />
        </SelectParameters>
    </asp:SqlDataSource>

    <!-- PLIP Search Drawer -->
    <div id="plipDrawer" class="drawer" role="dialog" aria-label="PLIP search">
        <div class="drawer-header">
            <h3>&#128269; PLIP Search</h3>
            <button type="button" class="drawer-close" onclick="closePLIPDrawer()" aria-label="Close">&#10006;</button>
        </div>
        <asp:UpdatePanel ID="updPLIP" runat="server">
            <ContentTemplate>
                <div class="drawer-body">
                    <div class="search-group">
                        <label for="<%= txtSearchPLIP.ClientID %>">PLIP ID / Description</label>
                        <asp:TextBox ID="txtSearchPLIP"
                            runat="server"
                            CssClass="drawer-input"
                            placeholder="Type at least 3 characters&hellip;"
                            AutoPostBack="True"
                            OnTextChanged="txtSearchPLIP_TextChanged" />
                    </div>

                    <hr />

                    <div class="drawer-results">
                        <asp:GridView ID="gvPLIPSearch"
                            runat="server"
                            CssClass="drawer-grid"
                            AutoGenerateColumns="False"
                            DataSourceID="PLIPSource"
                            OnRowCommand="gvPLIPSearch_RowCommand"
                            ShowHeaderWhenEmpty="True">
                            <Columns>
                                <asp:TemplateField HeaderText="PLIP ID">
                                    <ItemTemplate>
                                        <asp:LinkButton ID="lnkPLIP"
                                            runat="server"
                                            Text='<%# Eval("PLIP_ID") %>'
                                            CommandName="SelectPLIP"
                                            CommandArgument='<%# Eval("PLIP_ID") %>'
                                            CssClass="plip-link" />
                                    </ItemTemplate>
                                    <ItemStyle Wrap="False" />
                                </asp:TemplateField>

                                <asp:BoundField DataField="PLIP_Title" HeaderText="PLIP_Title" SortExpression="PLIP_Title" />
                                <asp:BoundField DataField="Doc_Type" HeaderText="Doc_Type" ReadOnly="True" SortExpression="Doc_Type" />
                                <asp:BoundField DataField="Critical" HeaderText="Critical" SortExpression="Critical" />
                                <asp:BoundField DataField="FHO_Status" HeaderText="HO" SortExpression="FHO_Status" />
                                <asp:BoundField DataField="DCAF" HeaderText="DCAF" SortExpression="DCAF" />
                            </Columns>
                            <EmptyDataTemplate>
                                <div class="empty-state">Type a PLIP ID or keyword to search.</div>
                            </EmptyDataTemplate>
                        </asp:GridView>
                    </div>
                </div>
            </ContentTemplate>
        </asp:UpdatePanel>
    </div>

    <div id="drawerOverlay" class="drawer-overlay" onclick="closePLIPDrawer()"></div>

    <!-- CTD Search Drawer -->
    <div id="CTDDrawer" class="drawer" role="dialog" aria-label="CTD search">
        <div class="drawer-header">
            <h3>&#128269; CTD Search</h3>
            <button type="button" class="drawer-close" onclick="closeCTDDrawer()" aria-label="Close">&#10006;</button>
        </div>
        <asp:UpdatePanel ID="UpdatePanel1" runat="server">
            <ContentTemplate>
                <div class="drawer-body">
                    <div class="drawer-results">
                        <asp:GridView ID="ctdGrid"
                            runat="server"
                            CssClass="drawer-grid"
                            AutoGenerateColumns="False"
                            DataSourceID="CTD_Search_Source"
                            OnRowDataBound="ctdGrid_RowDataBound"
                            ShowHeaderWhenEmpty="True">
                            <Columns>
                                <asp:TemplateField HeaderText="CTD ID">
                                    <ItemTemplate>
                                        <asp:HyperLink ID="lnkCTD" runat="server"
                                            Text='<%# Eval("CTD_ID") %>'
                                            NavigateUrl='<%# "DDR_Developer.aspx?CTD_ID=" & Eval("CTD_ID") %>'>
                                        </asp:HyperLink>
                                    </ItemTemplate>
                                </asp:TemplateField>
                                <asp:BoundField DataField="Discipline" HeaderText="Discipline" SortExpression="Discipline" />
                                <asp:BoundField DataField="Del_Item_Ref" HeaderText="Del_Item_Ref" SortExpression="Del_Item_Ref" />
                                <asp:BoundField DataField="Deliverable" HeaderText="Deliverable" ReadOnly="True" SortExpression="Deliverable" />
                                <asp:BoundField DataField="CTD Hrs" HeaderText="CTD Hrs" ReadOnly="True" SortExpression="CTD Hrs" />
                                <asp:BoundField DataField="DDR Hrs" HeaderText="DDR Hrs" ReadOnly="True" SortExpression="DDR Hrs" />
                                <asp:BoundField DataField="CTD Qty" HeaderText="CTD Qty" SortExpression="CTD Qty" />
                                <asp:BoundField DataField="DDR Qty" HeaderText="DDR Qty" SortExpression="DDR Qty" />
                            </Columns>
                            <EmptyDataTemplate>
                                <div class="empty-state">No matching CTDs.</div>
                            </EmptyDataTemplate>
                        </asp:GridView>
                    </div>
                </div>
            </ContentTemplate>
        </asp:UpdatePanel>
    </div>

    <div id="drawerOverlay1" class="drawer-overlay" onclick="closeCTDDrawer()"></div>
</form>

</body>
</html>

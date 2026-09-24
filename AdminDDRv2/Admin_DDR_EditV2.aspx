<%@ Page Language="vb" AutoEventWireup="false" CodeBehind="Admin_DDR_EditV2.aspx.vb" Inherits="SmarTagsASP.Admin_DDR_EditV2" %>

<!DOCTYPE html>
<html lang="en">
<head runat="server">
    <meta charset="utf-8" />
    <meta http-equiv="X-UA-Compatible" content="IE=edge" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>SmartDDR · Admin DDR Editor</title>

    <style>
        /* ================================================================
           Theme tokens (SmartDDR teal palette)
           ================================================================ */
        :root {
            --brand-950: #132828;
            --brand-900: #1C3737;
            --brand-850: #1B3B3B;
            --brand-700: #0F766E;
            --brand-500: #14B8A6;
            --brand-450: #15B4A2;
            --brand-100: #CCFBF1;
            --brand-50:  #ECFEFF;
            --bg: #F4F7FC;
            --surface: #FFFFFF;
            --border: #E2E8F0;
            --border-strong: #CBD5E1;
            --text: #1F2937;
            --muted: #64748B;
            --danger: #DC2626;
            --success: #16A34A;
            --warn: #F59E0B;
            --gold: #FACC15;
            --radius: 12px;
            --shadow: 0 4px 16px rgba(15, 23, 42, .08);
            --font: "Segoe UI", system-ui, -apple-system, Roboto, Arial, sans-serif;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        html, body { height: 100%; }
        body {
            font-family: var(--font);
            font-size: 14px;
            background: var(--bg);
            color: var(--text);
        }
        form { height: 100%; }

        .app { display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

        /* ================================================================
           Top bar
           ================================================================ */
        .topbar {
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            padding: 10px 16px;
            background: linear-gradient(135deg, var(--brand-900), var(--brand-500));
            color: #fff;
            box-shadow: 0 2px 10px rgba(0, 0, 0, .12);
        }
        .brand-mark {
            width: 34px; height: 34px;
            border-radius: 10px;
            background: var(--brand-500);
            border: 1px solid rgba(255, 255, 255, .35);
            display: flex; align-items: center; justify-content: center;
            font-weight: 700; font-size: 17px;
            box-shadow: 0 4px 10px rgba(0, 0, 0, .2);
        }
        .crumbs { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
        .crumb-app { font-size: 13px; opacity: .75; white-space: nowrap; }
        .crumb-sep { opacity: .5; }
        .crumb-view { font-size: 18px; font-weight: 700; white-space: nowrap; }
        .admin-badge {
            padding: 2px 8px;
            border-radius: 999px;
            background: var(--gold);
            color: #422006;
            font-size: 10.5px;
            font-weight: 700;
            letter-spacing: .6px;
            text-transform: uppercase;
        }
        .project-pill {
            max-width: 440px;
            padding: 4px 12px;
            border-radius: 999px;
            background: rgba(255, 255, 255, .16);
            border: 1px solid rgba(255, 255, 255, .25);
            font-size: 12px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .project-pill:empty { display: none; }
        .topbar-spacer { flex: 1; }
        .user-chip { display: flex; align-items: center; gap: 6px; font-size: 12px; opacity: .9; white-space: nowrap; }
        .logo { height: 36px; border-radius: 6px; background: #fff; padding: 2px 4px; }

        /* Buttons */
        .btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            height: 32px;
            padding: 0 12px;
            border-radius: 8px;
            border: 1px solid var(--border-strong);
            background: #fff;
            color: var(--text);
            font: inherit;
            font-size: 13px;
            font-weight: 500;
            line-height: 1;
            cursor: pointer;
            text-decoration: none;
            white-space: nowrap;
            transition: background .15s, border-color .15s, color .15s, box-shadow .15s, transform .1s;
        }
        .btn:hover { border-color: var(--brand-500); color: var(--brand-700); box-shadow: 0 2px 6px rgba(20, 184, 166, .18); }
        .btn:active { transform: translateY(1px); }
        .btn:disabled { opacity: .5; cursor: default; box-shadow: none; }
        .btn-primary { background: var(--brand-500); border-color: var(--brand-500); color: #fff; }
        .btn-primary:hover { background: var(--brand-700); border-color: var(--brand-700); color: #fff; }
        .btn-ghost { background: transparent; }
        .btn-on-dark { background: rgba(255, 255, 255, .16); border-color: rgba(255, 255, 255, .3); color: #fff; }
        .btn-on-dark:hover { background: rgba(255, 255, 255, .28); border-color: rgba(255, 255, 255, .5); color: #fff; }

        /* ================================================================
           Toolbar
           ================================================================ */
        .toolbar {
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
            padding: 10px 16px;
            background: var(--surface);
            border-bottom: 1px solid var(--border);
        }
        .field { display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600; color: var(--muted); }
        .select {
            height: 34px;
            max-width: 320px;
            padding: 0 10px;
            border-radius: 8px;
            border: 1px solid var(--border-strong);
            background: #fff;
            color: var(--text);
            font: inherit;
            font-size: 13px;
        }
        .select:focus { outline: none; border-color: var(--brand-500); box-shadow: 0 0 0 3px rgba(20, 184, 166, .15); }
        .search {
            display: flex;
            align-items: center;
            gap: 6px;
            flex: 0 1 360px;
            min-width: 240px;
            padding: 0 10px;
            border-radius: 10px;
            border: 1px solid var(--border);
            background: var(--bg);
        }
        .search:focus-within { border-color: var(--brand-500); box-shadow: 0 0 0 3px rgba(20, 184, 166, .15); }
        .search-ic { font-size: 13px; opacity: .6; }
        .search-input {
            flex: 1;
            min-width: 0;
            height: 34px;
            border: 0;
            outline: none;
            background: transparent;
            font: inherit;
            font-size: 13px;
        }
        .toolbar-spacer { flex: 1; }
        .count {
            padding: 4px 10px;
            border-radius: 999px;
            border: 1px solid var(--border);
            background: var(--bg);
            color: var(--muted);
            font-size: 12px;
            font-weight: 600;
        }
        .count:empty { display: none; }

        /* Active filter chips */
        .chips { display: flex; flex-wrap: wrap; gap: 6px; padding: 0 16px; }
        .chips:empty { display: none; }
        .chip {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 3px 4px 3px 10px;
            border-radius: 999px;
            background: var(--brand-100);
            color: var(--brand-700);
            font-size: 12px;
            font-weight: 600;
        }
        .chip button {
            border: 0;
            background: transparent;
            color: inherit;
            cursor: pointer;
            font-size: 13px;
            line-height: 1;
            padding: 2px 6px;
            border-radius: 50%;
        }
        .chip button:hover { background: rgba(15, 118, 110, .15); }

        /* ================================================================
           KPI strip
           ================================================================ */
        .stats { display: flex; flex-wrap: wrap; gap: 10px; padding: 12px 16px 0; }
        .stat {
            flex: 1 1 150px;
            display: flex;
            flex-direction: column;
            gap: 2px;
            padding: 10px 14px;
            border-radius: var(--radius);
            border: 1px solid var(--border);
            border-left: 4px solid var(--brand-500);
            background: var(--surface);
            box-shadow: var(--shadow);
        }
        .stat-k { font-size: 11px; font-weight: 700; letter-spacing: .6px; text-transform: uppercase; color: var(--muted); }
        .stat-v { font-size: 20px; font-weight: 700; color: var(--brand-900); }
        .stat-s { font-size: 11.5px; color: var(--muted); }
        .stat-del { border-left-color: #3B82F6; }
        .stat-start { border-left-color: var(--gold); }

        /* ================================================================
           Content + grid
           ================================================================ */
        .content { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 10px; padding: 12px 16px 10px; }
        .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); }
        .grid-card { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
        .grid-scroll { flex: 1; min-height: 0; overflow: auto; }

        table.grid { table-layout: fixed; border-collapse: separate; border-spacing: 0; font-size: 12.5px; }
        table.grid th {
            position: sticky;
            top: 0;
            z-index: 2;
            padding: 0;
            background: var(--brand-850);
            color: #fff;
            font-weight: 600;
            text-align: left;
            white-space: nowrap;
            border-right: 1px solid rgba(255, 255, 255, .08);
        }
        table.grid th.ed { background: #1F4A47; }
        .th-in { display: flex; align-items: center; gap: 4px; padding: 0 4px 0 0; }
        .th-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; padding: 9px 4px 9px 10px; cursor: pointer; user-select: none; }
        .th-label:hover { color: var(--gold); }
        table.grid th.c .th-label { text-align: center; }
        table.grid th.sort-asc .th-label::after { content: " \25B2"; font-size: 9px; color: var(--gold); }
        table.grid th.sort-desc .th-label::after { content: " \25BC"; font-size: 9px; color: var(--gold); }
        .ed-mark { font-size: 10px; opacity: .55; margin-left: 4px; }
        .fbtn {
            flex: 0 0 auto;
            width: 22px; height: 22px;
            border-radius: 6px;
            border: 1px solid rgba(255, 255, 255, .28);
            background: rgba(255, 255, 255, .1);
            color: #fff;
            font-size: 11px;
            line-height: 1;
            cursor: pointer;
        }
        .fbtn:hover { background: rgba(255, 255, 255, .25); }
        .fbtn.on { background: var(--gold); border-color: var(--gold); color: #422006; }

        table.grid td {
            padding: 0;
            height: 30px;
            border-bottom: 1px solid var(--border);
            border-right: 1px solid #EEF2F7;
            white-space: nowrap;
            vertical-align: middle;
            overflow: hidden;
        }
        table.grid td.ro { padding: 4px 8px; color: var(--muted); text-overflow: ellipsis; }
        table.grid td.c { text-align: center; }
        table.grid tbody tr:nth-child(even) td { background: #F8FAFC; }
        table.grid tbody tr:hover td { background: var(--brand-50); }
        table.grid tbody tr.row-saved td { background: #DCFCE7; }
        table.grid tbody tr.row-deleted td { background: #DBEAFE; }
        table.grid tbody tr.row-deleted td.e { color: #1E3A8A; text-decoration: line-through; }
        table.grid tr.empty td {
            padding: 48px 16px;
            text-align: center;
            white-space: normal;
            color: var(--muted);
            font-size: 14px;
            background: #fff;
        }

        /* Excel-like editable cells: plain text until clicked, then one shared editor */
        table.grid td.e { padding: 0 8px; cursor: text; text-overflow: ellipsis; }
        table.grid td.e:hover { box-shadow: inset 0 0 0 1px var(--border-strong); }
        table.grid tbody td.e.saved { box-shadow: inset 3px 0 0 var(--success); }
        table.grid tbody td.e.saving { background: #FEF9C3; }
        table.grid tbody td.e.err { background: #FEE2E2; box-shadow: inset 0 0 0 2px var(--danger); }
        table.grid tbody td.e.editing { padding: 0; background: #fff; text-decoration: none; }
        .cell-editor {
            display: block;
            width: 100%;
            height: 29px;
            padding: 0 8px;
            border: 0;
            border-radius: 0;
            outline: none;
            background: #fff;
            color: var(--text);
            font: inherit;
            font-size: 12.5px;
            text-align: inherit;
            box-shadow: inset 0 0 0 2px var(--brand-500);
        }

        /* Progress bar */
        .pbar { display: flex; flex-direction: column; gap: 2px; min-width: 76px; }
        .pbar-val { font-size: 11px; font-weight: 600; text-align: center; line-height: 1.15; color: var(--text); }
        .pbar-track { display: block; height: 6px; border-radius: 999px; background: #E5E7EB; overflow: hidden; }
        .pbar-fill { display: block; height: 100%; border-radius: 999px; }
        .pb-good { background: #22C55E; }
        .pb-ok { background: #3B82F6; }
        .pb-warn { background: #F59E0B; }
        .pb-bad { background: #EF4444; }

        .legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 11.5px; color: var(--muted); }
        .legend b { color: var(--text); font-weight: 600; }
        .kbd {
            display: inline-block;
            min-width: 18px;
            padding: 0 5px;
            border: 1px solid var(--border-strong);
            border-bottom-width: 2px;
            border-radius: 4px;
            background: #fff;
            font-size: 10.5px;
            text-align: center;
        }
        .sw { display: inline-block; width: 10px; height: 10px; border-radius: 3px; vertical-align: -1px; margin-right: 4px; }

        /* ================================================================
           Excel-like filter pop-up
           ================================================================ */
        .fpop {
            position: fixed;
            z-index: 1100;
            width: 290px;
            display: flex;
            flex-direction: column;
            border: 1px solid var(--border-strong);
            border-radius: 10px;
            background: #fff;
            box-shadow: 0 16px 40px rgba(15, 23, 42, .25);
            font-size: 12.5px;
        }
        .fpop[hidden] { display: none; }
        .fpop-title {
            padding: 9px 12px;
            border-radius: 10px 10px 0 0;
            background: var(--brand-850);
            color: #fff;
            font-weight: 600;
        }
        .fpop-acts { display: flex; flex-direction: column; padding: 4px 0; border-bottom: 1px solid var(--border); }
        .fpop-act {
            border: 0;
            background: transparent;
            padding: 6px 12px;
            text-align: left;
            font: inherit;
            color: var(--text);
            cursor: pointer;
        }
        .fpop-act:hover { background: var(--brand-50); color: var(--brand-700); }
        .fpop-act:disabled { color: #9CA3AF; background: transparent; cursor: default; }
        .fpop-search {
            margin: 8px 10px 6px;
            height: 30px;
            padding: 0 9px;
            border: 1px solid var(--border-strong);
            border-radius: 7px;
            font: inherit;
            outline: none;
        }
        .fpop-search:focus { border-color: var(--brand-500); box-shadow: 0 0 0 3px rgba(20, 184, 166, .15); }
        .fpop-list {
            height: 230px;
            margin: 0 10px;
            overflow: auto;
            border: 1px solid var(--border);
            border-radius: 7px;
            padding: 4px 0;
        }
        .fpop-list label {
            display: flex;
            align-items: center;
            gap: 7px;
            padding: 3px 8px;
            cursor: pointer;
            white-space: nowrap;
        }
        .fpop-list label:hover { background: var(--brand-50); }
        .fpop-list label.all { font-weight: 600; border-bottom: 1px solid var(--border); margin-bottom: 2px; padding-bottom: 5px; }
        .fpop-list .v { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
        .fpop-list .n { color: var(--muted); font-size: 11px; }
        .fpop-list .blank { font-style: italic; color: var(--muted); }
        .fpop-note { min-height: 16px; padding: 4px 12px 0; font-size: 11px; color: var(--muted); }
        .fpop-foot { display: flex; justify-content: flex-end; gap: 8px; padding: 8px 10px 10px; }

        /* ================================================================
           Toast, loader, access denied
           ================================================================ */
        .toast {
            position: fixed;
            top: 14px;
            right: 16px;
            z-index: 1200;
            max-width: 460px;
            padding: 12px 16px 12px 14px;
            border-left: 4px solid var(--brand-500);
            border-radius: 10px;
            background: #fff;
            box-shadow: 0 10px 30px rgba(0, 0, 0, .18);
            font-size: 13px;
            cursor: pointer;
            transition: opacity .3s, transform .3s;
        }
        .toast-success { border-left-color: var(--success); }
        .toast-warn { border-left-color: var(--warn); }
        .toast-error { border-left-color: var(--danger); background: #FEF2F2; color: #7F1D1D; }
        .toast-hide { opacity: 0; transform: translateY(-8px); pointer-events: none; }

        .loader-overlay {
            display: none;
            position: fixed;
            top: 0; right: 0; bottom: 0; left: 0;
            z-index: 2000;
            align-items: center;
            justify-content: center;
            background: rgba(15, 23, 42, .5);
        }
        .loader-box {
            padding: 26px 36px;
            border-radius: 16px;
            background: #fff;
            text-align: center;
            box-shadow: 0 20px 50px rgba(0, 0, 0, .25);
        }
        .loader-box h3 { margin-top: 14px; font-size: 15px; color: var(--brand-900); }
        .loader-box p { font-size: 12px; color: var(--muted); }
        .spinner {
            width: 52px; height: 52px;
            margin: auto;
            border: 5px solid #E5E7EB;
            border-top-color: var(--brand-500);
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }

        .denied { max-width: 520px; margin: 80px auto; padding: 32px; text-align: center; }
        .denied h2 { margin: 10px 0 6px; color: var(--brand-900); }
        .denied p { color: var(--muted); margin-bottom: 18px; }
        .denied-ic { font-size: 40px; }

        /* ================================================================
           Responsive + print
           ================================================================ */
        @media (max-width: 900px) {
            .crumb-app, .crumb-sep, .logo { display: none; }
            .search { flex: 1 1 100%; min-width: 0; }
            .project-pill { max-width: 100%; }
            .content, .stats { padding-left: 8px; padding-right: 8px; }
            .stat { flex-basis: 130px; }
            /* Let the page scroll on phones so the grid keeps a usable height. */
            .app { height: auto; min-height: 100vh; overflow: visible; }
            .content, .grid-card { flex: none; }
            .grid-scroll { max-height: 75vh; }
        }
        @media print {
            .toolbar, .topbar .btn, .fbtn, .legend, .chips { display: none !important; }
            .app { height: auto; overflow: visible; }
            .grid-scroll { overflow: visible; }
            table.grid th { position: static; }
        }
    </style>
</head>

<body>
<form id="frmAdminDDR" runat="server">
    <asp:ScriptManager ID="ScriptManager1" runat="server" EnablePageMethods="true" />

    <div class="app">
        <header class="topbar">
            <a href="Admin_Tasks.aspx" class="btn btn-on-dark" title="Back to Admin Tasks">⬅ Admin</a>
            <div class="brand-mark">S</div>
            <div class="crumbs">
                <span class="crumb-app">SmartDDR</span>
                <span class="crumb-sep">›</span>
                <span class="crumb-view">DDR Editor</span>
                <span class="admin-badge">Admin</span>
            </div>
            <asp:Label ID="LblInfo" runat="server" CssClass="project-pill" />
            <span class="topbar-spacer"></span>
            <span class="user-chip">👤 <asp:Label ID="lblUser" runat="server" /></span>
            <a href="Index.aspx" class="btn btn-on-dark">🏠 Home</a>
            <asp:Image ID="imgLogo" runat="server" CssClass="logo" ImageUrl="~/download.png" AlternateText="Worley" />
        </header>

        <asp:Panel ID="pnlDenied" runat="server" Visible="False" CssClass="card denied">
            <div class="denied-ic">🔒</div>
            <h2>Administrators only</h2>
            <p>The DDR editor changes project data and is restricted to SmartDDR administrators.
               Ask an administrator to grant you access if you need it.</p>
            <a href="Index.aspx" class="btn btn-primary">🏠 Back to SmartDDR</a>
        </asp:Panel>

        <asp:PlaceHolder ID="phApp" runat="server">
            <div class="toolbar">
                <div class="field">
                    <label for="DDLPROJNO">Project</label>
                    <asp:DropDownList ID="DDLPROJNO" runat="server" ClientIDMode="Static"
                        CssClass="select" AutoPostBack="True" data-loading="true" />
                </div>
                <div class="field">
                    <label for="DDLDISCIPLINE">Discipline</label>
                    <asp:DropDownList ID="DDLDISCIPLINE" runat="server" ClientIDMode="Static"
                        CssClass="select" AutoPostBack="True" data-loading="true" />
                </div>

                <div class="search">
                    <span class="search-ic">🔍</span>
                    <input type="search" id="txtSearch" class="search-input" autocomplete="off"
                        placeholder="Search documents…   ( / )" aria-label="Search documents" />
                </div>
                <button type="button" id="btnClearAll" class="btn btn-ghost" title="Clear the search and every column filter">✖ Clear filters</button>

                <span class="toolbar-spacer"></span>

                <span id="rowCount" class="count"></span>
                <asp:Button ID="btnReload" runat="server" Text="⟳ Reload" CssClass="btn"
                    ToolTip="Read the latest data from the database" data-loading="true" UseSubmitBehavior="False" />
                <asp:Button ID="btnExcel" runat="server" Text="⬇ Excel" CssClass="btn"
                    ToolTip="Download the rows on screen as Excel (.xlsx)" OnClientClick="prepareExport();" UseSubmitBehavior="False" />
                <asp:Button ID="btnCSV" runat="server" Text="⬇ CSV" CssClass="btn"
                    ToolTip="Download the rows on screen as CSV" OnClientClick="prepareExport();" UseSubmitBehavior="False" />
            </div>

            <div class="stats" aria-live="polite">
                <div class="stat"><span class="stat-k">Documents</span><span class="stat-v" id="stDocs">–</span><span class="stat-s" id="stDocsSub"></span></div>
                <div class="stat"><span class="stat-k">Man-hours</span><span class="stat-v" id="stHours">–</span><span class="stat-s">rows on screen</span></div>
                <div class="stat stat-start"><span class="stat-k">Started</span><span class="stat-v" id="stStarted">–</span><span class="stat-s" id="stStartedSub"></span></div>
                <div class="stat"><span class="stat-k">Avg progress</span><span class="stat-v" id="stProg">–</span><span class="stat-s">rows on screen</span></div>
                <div class="stat stat-del"><span class="stat-k">Deleted</span><span class="stat-v" id="stDeleted">–</span><span class="stat-s">remarks = DELETED</span></div>
            </div>

            <asp:Label ID="lblMessage" runat="server" Visible="False" EnableViewState="False" />

            <section class="content">
                <div id="filterChips" class="chips"></div>

                <div class="card grid-card">
                    <div class="grid-scroll" id="gridScroll">
                        <asp:Literal ID="litGrid" runat="server" EnableViewState="False" Mode="PassThrough" />
                    </div>
                </div>

                <div class="legend">
                    <span><b>✎</b> editable column - click a cell to edit; it saves when you leave the cell</span>
                    <span><span class="kbd">Enter</span> <span class="kbd">↑</span> <span class="kbd">↓</span> rows · <span class="kbd">Tab</span> next cell</span>
                    <span><span class="kbd">Esc</span> undo the cell</span>
                    <span><span class="sw" style="background:#DCFCE7"></span>saved</span>
                    <span><span class="sw" style="background:#DBEAFE"></span>type <b>DELETED</b> in Remarks: title gets “(DELETED)”, Man Hours = 0</span>
                    <span><b>START</b> accepts 0 or 0.1</span>
                </div>
            </section>

            <!-- Excel-like column filter -->
            <div id="fpop" class="fpop" role="dialog" aria-label="Column filter" hidden>
                <div class="fpop-title" id="fpopTitle"></div>
                <div class="fpop-acts">
                    <button type="button" class="fpop-act" data-act="asc">↑ Sort A → Z</button>
                    <button type="button" class="fpop-act" data-act="desc">↓ Sort Z → A</button>
                    <button type="button" class="fpop-act" data-act="clear" id="fpopClear">✖ Clear filter</button>
                </div>
                <input type="search" class="fpop-search" id="fpopSearch" placeholder="Search values…" autocomplete="off" aria-label="Search values" />
                <div class="fpop-list" id="fpopList"></div>
                <div class="fpop-note" id="fpopNote"></div>
                <div class="fpop-foot">
                    <button type="button" class="btn btn-primary" data-act="ok" id="fpopOk">OK</button>
                    <button type="button" class="btn" data-act="cancel">Cancel</button>
                </div>
            </div>

            <asp:HiddenField ID="hfKey" runat="server" ClientIDMode="Static" />
            <asp:HiddenField ID="hfExportIds" runat="server" ClientIDMode="Static" EnableViewState="False" />
        </asp:PlaceHolder>
    </div>

    <div id="loaderOverlay" class="loader-overlay" role="status" aria-live="polite">
        <div class="loader-box">
            <div class="spinner"></div>
            <h3>Processing request</h3>
            <p>Please wait…</p>
        </div>
    </div>

    <script>
        (function () {
            'use strict';

            var loader = document.getElementById('loaderOverlay');
            window.showLoader = function () { if (loader) { loader.style.display = 'flex'; } return true; };
            window.hideLoader = function () { if (loader) { loader.style.display = 'none'; } };
            window.addEventListener('pageshow', window.hideLoader);   // back/forward cache

            function showToast(text, kind, sticky) {
                var t = document.createElement('div');
                t.className = 'toast toast-' + (kind || 'info');
                t.textContent = text;
                t.addEventListener('click', function () { t.classList.add('toast-hide'); });
                var old = document.querySelectorAll('.toast');
                for (var i = 0; i < old.length; i++) { old[i].classList.add('toast-hide'); }
                document.body.appendChild(t);
                if (!sticky && kind !== 'error') { setTimeout(function () { t.classList.add('toast-hide'); }, 5000); }
            }

            /* Server message (rendered by lblMessage) */
            var srvToast = document.querySelector('.toast');
            if (srvToast) {
                srvToast.addEventListener('click', function () { srvToast.classList.add('toast-hide'); });
                if (!srvToast.classList.contains('toast-error')) { setTimeout(function () { srvToast.classList.add('toast-hide'); }, 6000); }
            }

            document.addEventListener('change', function (e) {
                var t = e.target;
                if (t && t.tagName === 'SELECT' && t.hasAttribute('data-loading')) { window.showLoader(); }
            });
            document.addEventListener('click', function (e) {
                var t = e.target;
                if (t && t.closest && t.closest('input[data-loading],button[data-loading]')) { window.showLoader(); }
            });

            var grid = document.getElementById('ddrGrid');
            window.prepareExport = function () { return true; };
            if (!grid) { return; }   // access denied or no project

            /* ================================================================
               Row model: values are read once and kept in sync with edits
               ================================================================ */
            var key = document.getElementById('hfKey').value;
            var scroller = document.getElementById('gridScroll');
            var tbody = grid.tBodies[0];
            var heads = Array.prototype.slice.call(grid.tHead.rows[0].cells);
            var fields = heads.map(function (th) { return th.getAttribute('data-f'); });
            var colOf = {};
            fields.forEach(function (f, i) { colOf[f] = i; });
            var headerOf = {};
            heads.forEach(function (th) { headerOf[th.getAttribute('data-f')] = th.querySelector('.th-label').textContent.replace('✎', '').trim(); });

            var editTd = null, editSaved = '', editStart = '';   // the cell being edited
            var kindOf = {}, maxOf = {};
            heads.forEach(function (th) {
                var f = th.getAttribute('data-f');
                if (th.hasAttribute('data-k')) { kindOf[f] = th.getAttribute('data-k'); }
                if (th.hasAttribute('data-max')) { maxOf[f] = parseInt(th.getAttribute('data-max'), 10); }
            });
            function fieldOf(td) { return fields[td.cellIndex]; }

            var rows = [];
            Array.prototype.forEach.call(tbody.rows, function (tr) {
                if (!tr.hasAttribute('data-id')) { return; }
                var r = { tr: tr, id: tr.getAttribute('data-id'), v: {}, text: '' };
                readRow(r);
                rows.push(r);
            });

            function cellValue(td) {
                return td === editTd ? editSaved : td.textContent.trim();
            }
            function readRow(r) {
                var parts = [];
                for (var i = 0; i < fields.length; i++) {
                    var v = cellValue(r.tr.cells[i]);
                    r.v[fields[i]] = v;
                    parts.push(v);
                }
                r.text = parts.join('\u0001').toLowerCase();
            }
            function num(s) {
                var n = parseFloat(String(s || '').replace(/[,%\s]/g, ''));
                return isNaN(n) ? 0 : n;
            }
            function fmt(n, dp) {
                return n.toLocaleString(undefined, { minimumFractionDigits: dp || 0, maximumFractionDigits: dp || 0 });
            }

            /* ================================================================
               Filtering (search + Excel-like column filters), state per project/discipline
               ================================================================ */
            var search = document.getElementById('txtSearch');
            var filters = {};          // field -> Set of accepted values ('' = blanks)
            var sortState = null;      // { f: field, asc: bool }
            var stateKey = 'addr2.' + (document.getElementById('DDLPROJNO').value || '') + '|' + (document.getElementById('DDLDISCIPLINE').value || '');

            function store(k, v) { try { window.sessionStorage.setItem(k, v); } catch (e) { /* storage unavailable */ } }
            function load(k) { try { return window.sessionStorage.getItem(k); } catch (e) { return null; } }

            function saveState() {
                var f = {};
                Object.keys(filters).forEach(function (k) { f[k] = Array.from(filters[k]); });
                store(stateKey, JSON.stringify({ q: search.value, f: f, s: sortState }));
            }
            function restoreState() {
                var raw = load(stateKey);
                if (!raw) { return; }
                try {
                    var st = JSON.parse(raw);
                    search.value = st.q || '';
                    Object.keys(st.f || {}).forEach(function (k) {
                        if (k in colOf && Array.isArray(st.f[k])) { filters[k] = new Set(st.f[k]); }
                    });
                    if (st.s && st.s.f in colOf) { sortBy(st.s.f, st.s.asc); }
                } catch (e) { /* ignore a corrupt entry */ }
            }

            function passes(r, skipField, q) {
                if (q && r.text.indexOf(q) < 0) { return false; }
                for (var f in filters) {
                    if (f !== skipField && !filters[f].has(r.v[f])) { return false; }
                }
                return true;
            }

            function applyFilters() {
                var q = search.value.trim().toLowerCase();
                rows.forEach(function (r) {
                    var d = passes(r, null, q) ? '' : 'none';
                    if (r.tr.style.display !== d) { r.tr.style.display = d; }   // touch only rows that change
                });
                heads.forEach(function (th) {
                    var b = th.querySelector('.fbtn');
                    if (b) { b.classList.toggle('on', !!filters[th.getAttribute('data-f')]); }
                });
                renderChips();
                updateStats();
                saveState();
            }

            function visibleRows() {
                return rows.filter(function (r) { return r.tr.style.display !== 'none'; });
            }

            function updateStats() {
                var vis = visibleRows();
                var hours = 0, started = 0, deleted = 0, progSum = 0, progN = 0;
                var pc = colOf.PERC;
                vis.forEach(function (r) {
                    hours += num(r.v.Man_Hours);
                    if (num(r.v.PLANNED) > 0) { started++; }
                    if (r.tr.classList.contains('row-deleted')) { deleted++; }
                    if (pc !== undefined) {
                        var n = r.tr.cells[pc].getAttribute('data-n');
                        if (n !== null && n !== '') { progSum += parseFloat(n); progN++; }
                    }
                });
                var total = rows.length;
                document.getElementById('stDocs').textContent = fmt(vis.length);
                document.getElementById('stDocsSub').textContent = vis.length === total ? 'all rows' : 'of ' + fmt(total) + ' rows';
                document.getElementById('stHours').textContent = fmt(hours, hours % 1 ? 1 : 0);
                document.getElementById('stStarted').textContent = fmt(started);
                document.getElementById('stStartedSub').textContent = vis.length ? Math.round(started * 100 / vis.length) + '% with START = 0.1' : '';
                document.getElementById('stProg').textContent = progN ? (progSum * 100 / progN).toFixed(1) + '%' : '–';
                document.getElementById('stDeleted').textContent = fmt(deleted);
                document.getElementById('rowCount').textContent = vis.length === total ? fmt(total) + ' rows' : fmt(vis.length) + ' of ' + fmt(total) + ' rows';
            }

            function renderChips() {
                var box = document.getElementById('filterChips');
                box.innerHTML = '';
                function chip(label, onClear) {
                    var c = document.createElement('span');
                    c.className = 'chip';
                    c.appendChild(document.createTextNode(label));
                    var b = document.createElement('button');
                    b.type = 'button';
                    b.title = 'Remove this filter';
                    b.textContent = '×';
                    b.addEventListener('click', onClear);
                    c.appendChild(b);
                    box.appendChild(c);
                }
                if (search.value.trim()) {
                    chip('Search: “' + search.value.trim() + '”', function () { search.value = ''; applyFilters(); });
                }
                Object.keys(filters).forEach(function (f) {
                    var set = filters[f];
                    var label = headerOf[f] + ': ';
                    if (set.size === 1) {
                        var only = set.values().next().value;
                        label += only === '' ? '(Blanks)' : only;
                    } else {
                        label += set.size + ' values';
                    }
                    chip(label, function () { delete filters[f]; applyFilters(); });
                });
            }

            /* ================================================================
               Sorting (header click or filter menu)
               ================================================================ */
            function sortKey(t) {
                if (!t) { return { k: 2, v: '' }; }
                var n = t.replace(/[,%\s]/g, '');
                if (/^[-+]?\d*\.?\d+(e[-+]?\d+)?$/i.test(n)) { return { k: 0, v: parseFloat(n) }; }
                return { k: 1, v: t.toLowerCase() };
            }
            function sortBy(f, asc) {
                var keyed = rows.map(function (r, i) { return { r: r, i: i, s: sortKey(r.v[f]) }; });
                keyed.sort(function (a, b) {
                    if (a.s.k !== b.s.k) { return a.s.k - b.s.k; }   // numbers, text, then blanks
                    var cmp = a.s.k === 1 ? a.s.v.localeCompare(b.s.v, undefined, { numeric: true })
                                          : (a.s.v < b.s.v ? -1 : (a.s.v > b.s.v ? 1 : 0));
                    if (cmp === 0) { return a.i - b.i; }
                    return asc ? cmp : -cmp;
                });
                var frag = document.createDocumentFragment();
                rows = keyed.map(function (x) { frag.appendChild(x.r.tr); return x.r; });
                tbody.appendChild(frag);
                heads.forEach(function (th) { th.classList.remove('sort-asc', 'sort-desc'); });
                heads[colOf[f]].classList.add(asc ? 'sort-asc' : 'sort-desc');
                sortState = { f: f, asc: asc };
            }

            /* ================================================================
               Excel-like filter pop-up
               ================================================================ */
            var pop = document.getElementById('fpop');
            var popList = document.getElementById('fpopList');
            var popSearch = document.getElementById('fpopSearch');
            var popNote = document.getElementById('fpopNote');
            var popOk = document.getElementById('fpopOk');
            var LIST_LIMIT = 1000;
            var pf = null;   // { f, values: [], counts: {}, checked: Map, shown: [] }

            function openFilter(btn) {
                var f = btn.getAttribute('data-f');
                if (pf && pf.f === f && !pop.hidden) { closeFilter(); return; }
                var q = search.value.trim().toLowerCase();
                var counts = new Map();
                rows.forEach(function (r) {
                    if (!passes(r, f, q)) { return; }
                    var v = r.v[f];
                    counts.set(v, (counts.get(v) || 0) + 1);
                });
                // Values selected in the filter but no longer present still count as selected.
                var values = Array.from(counts.keys()).sort(function (a, b) {
                    if (a === '') { return b === '' ? 0 : 1; }
                    if (b === '') { return -1; }
                    return a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
                });
                var cur = filters[f];
                var checked = new Map();
                values.forEach(function (v) { checked.set(v, !cur || cur.has(v)); });
                pf = { f: f, values: values, counts: counts, checked: checked, shown: values };

                document.getElementById('fpopTitle').textContent = 'Filter: ' + headerOf[f];
                document.getElementById('fpopClear').disabled = !cur;
                popSearch.value = '';
                renderList();

                pop.hidden = false;
                popBtn = btn;
                placePopup();
                popSearch.focus();
            }

            // Keeps the pop-up under its header button; closes it once the button scrolls out of view.
            var popBtn = null;
            function placePopup() {
                if (pop.hidden || !popBtn) { return; }
                var rc = popBtn.getBoundingClientRect();
                var sc = scroller.getBoundingClientRect();
                if (rc.right < sc.left || rc.left > sc.right) { closeFilter(); return; }
                var w = pop.offsetWidth, h = pop.offsetHeight;
                var left = Math.max(8, Math.min(rc.right - w, window.innerWidth - w - 8));
                var top = rc.bottom + 4;
                if (top + h > window.innerHeight - 8) { top = Math.max(8, window.innerHeight - h - 8); }
                pop.style.left = left + 'px';
                pop.style.top = top + 'px';
            }

            function closeFilter() { pop.hidden = true; pf = null; popBtn = null; }

            function renderList() {
                var q = popSearch.value.trim().toLowerCase();
                pf.shown = q ? pf.values.filter(function (v) { return (v === '' ? '(blanks)' : v.toLowerCase()).indexOf(q) >= 0; }) : pf.values;
                var frag = document.createDocumentFragment();

                var nChecked = 0;
                pf.shown.forEach(function (v) { if (pf.checked.get(v)) { nChecked++; } });
                var all = document.createElement('label');
                all.className = 'all';
                var allCb = document.createElement('input');
                allCb.type = 'checkbox';
                allCb.setAttribute('data-all', '1');
                allCb.checked = pf.shown.length > 0 && nChecked === pf.shown.length;
                allCb.indeterminate = nChecked > 0 && nChecked < pf.shown.length;
                all.appendChild(allCb);
                all.appendChild(document.createTextNode(q ? '(Select all search results)' : '(Select All)'));
                frag.appendChild(all);

                var limit = Math.min(pf.shown.length, LIST_LIMIT);
                for (var i = 0; i < limit; i++) {
                    var v = pf.shown[i];
                    var lab = document.createElement('label');
                    var cb = document.createElement('input');
                    cb.type = 'checkbox';
                    cb.setAttribute('data-i', i);
                    cb.checked = !!pf.checked.get(v);
                    var sv = document.createElement('span');
                    sv.className = v === '' ? 'v blank' : 'v';
                    sv.textContent = v === '' ? '(Blanks)' : v;
                    sv.title = sv.textContent;
                    var sn = document.createElement('span');
                    sn.className = 'n';
                    sn.textContent = pf.counts.get(v);
                    lab.appendChild(cb);
                    lab.appendChild(sv);
                    lab.appendChild(sn);
                    frag.appendChild(lab);
                }
                popList.innerHTML = '';
                popList.appendChild(frag);

                var note = pf.shown.length + (pf.shown.length === 1 ? ' value' : ' values');
                if (pf.shown.length > LIST_LIMIT) { note = 'Showing ' + LIST_LIMIT + ' of ' + pf.shown.length + ' values - type to narrow the list'; }
                popNote.textContent = note;
                popOk.disabled = q ? nChecked === 0 : !pf.values.some(function (x) { return pf.checked.get(x); });
            }

            popList.addEventListener('change', function (e) {
                var cb = e.target;
                if (!pf || cb.type !== 'checkbox') { return; }
                if (cb.hasAttribute('data-all')) {
                    pf.shown.forEach(function (v) { pf.checked.set(v, cb.checked); });
                } else {
                    pf.checked.set(pf.shown[+cb.getAttribute('data-i')], cb.checked);
                }
                renderList();
            });

            var popTimer = null;
            popSearch.addEventListener('input', function () {
                clearTimeout(popTimer);
                popTimer = setTimeout(function () { if (pf) { renderList(); } }, 100);
            });
            popSearch.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') { e.preventDefault(); if (!popOk.disabled) { commitFilter(); } }
            });

            function commitFilter() {
                var q = popSearch.value.trim();
                var sel;
                if (q) {
                    // Excel: with a search term, OK keeps only the ticked search results.
                    sel = pf.shown.filter(function (v) { return pf.checked.get(v); });
                } else {
                    sel = pf.values.filter(function (v) { return pf.checked.get(v); });
                }
                if (!sel.length) { return; }
                if (!q && sel.length === pf.values.length) { delete filters[pf.f]; } else { filters[pf.f] = new Set(sel); }
                closeFilter();
                applyFilters();
            }

            pop.addEventListener('click', function (e) {
                var b = e.target.closest ? e.target.closest('[data-act]') : null;
                if (!b || !pf) { return; }
                var act = b.getAttribute('data-act');
                if (act === 'ok') { commitFilter(); }
                else if (act === 'cancel') { closeFilter(); }
                else if (act === 'clear') { delete filters[pf.f]; closeFilter(); applyFilters(); }
                else if (act === 'asc' || act === 'desc') { sortBy(pf.f, act === 'asc'); closeFilter(); saveState(); }
            });

            document.addEventListener('mousedown', function (e) {
                if (!pop.hidden && !pop.contains(e.target) && !(e.target.closest && e.target.closest('.fbtn'))) { closeFilter(); }
            });
            // Follow the header on scroll / resize (an on-screen keyboard resizes the window).
            scroller.addEventListener('scroll', placePopup);
            window.addEventListener('resize', placePopup);

            grid.tHead.addEventListener('click', function (e) {
                var t = e.target;
                var fb = t.closest('.fbtn');
                if (fb) { openFilter(fb); return; }
                var lab = t.closest('.th-label');
                if (lab) {
                    var f = lab.closest('th').getAttribute('data-f');
                    sortBy(f, !(sortState && sortState.f === f && sortState.asc));
                    saveState();
                }
            });

            document.getElementById('btnClearAll').addEventListener('click', function () {
                filters = {};
                search.value = '';
                applyFilters();
            });

            var searchTimer = null;
            search.addEventListener('input', function () { clearTimeout(searchTimer); searchTimer = setTimeout(applyFilters, 150); });

            /* ================================================================
               Editing: cells are plain text; a click drops one shared editor into the cell.
               Each change is saved on its own (AJAX page method), no postback.
               ================================================================ */
            var pending = 0;
            var NUM_RE = /^\d*\.?\d+$|^\d+\.$/;
            var editor = document.createElement('input');
            editor.type = 'text';
            editor.className = 'cell-editor';
            editor.spellcheck = false;
            editor.autocomplete = 'off';

            // A cell that failed to save shows the typed text and keeps the saved value in data-orig.
            function savedValue(td) {
                if (td === editTd) { return editSaved; }
                return td.hasAttribute('data-orig') ? td.getAttribute('data-orig') : td.textContent;
            }

            function beginEdit(td) {
                if (editTd === td) { return; }
                if (editTd) { endEdit(true); }
                var f = fieldOf(td);
                editSaved = savedValue(td);
                editStart = td.textContent;
                editor.value = editStart;
                editor.maxLength = maxOf[f] > 0 ? maxOf[f] : 524288;
                editor.inputMode = kindOf[f] === 't' ? 'text' : 'decimal';
                editor.setAttribute('aria-label', headerOf[f]);
                editTd = td;
                td.textContent = '';
                td.classList.add('editing');
                td.appendChild(editor);
                editor.focus();
                editor.select();
            }

            // commit: save when changed; otherwise (Esc) show the saved value again.
            function endEdit(commit) {
                var td = editTd;
                if (!td) { return; }
                var saved = editSaved;
                var val = editor.value.trim();
                editTd = null;
                td.classList.remove('editing');
                td.textContent = commit ? val : saved;
                if (!commit || val === saved) {
                    clearError(td);
                    return;
                }
                if (val === editStart && td.classList.contains('err')) { return; }   // same rejected text again
                save(td, val, saved);
            }

            function clearError(td) {
                td.classList.remove('err');
                td.removeAttribute('data-orig');
                td.removeAttribute('title');
            }

            function validate(td, val) {
                var k = kindOf[fieldOf(td)];
                if (k === 's' && val !== '' && !(NUM_RE.test(val) && (Number(val) === 0 || Number(val) === 0.1))) {
                    return 'START accepts only 0 or 0.1.';
                }
                if (k === 'n' && val !== '' && !NUM_RE.test(val)) {
                    return 'Man Hours must be a number (0 or more).';
                }
                return null;
            }

            function markError(td, saved, msg) {
                td.classList.remove('saving', 'saved');
                td.classList.add('err');
                td.setAttribute('data-orig', saved);
                td.title = msg + '  (click the cell and press Esc to restore the saved value)';
            }

            function save(td, val, saved) {
                var tr = td.parentNode;
                var problem = validate(td, val);
                if (problem) { markError(td, saved, problem); showToast(problem, 'warn'); return; }
                if (!window.PageMethods || !PageMethods.SaveCell) {
                    markError(td, saved, 'Saving is not available.');
                    showToast('Saving is not available - reload the page.', 'error');
                    return;
                }

                clearError(td);
                td.classList.remove('saved');
                td.classList.add('saving');
                td.setAttribute('data-orig', saved);   // until the server confirms
                pending++;
                PageMethods.SaveCell(key, parseInt(tr.getAttribute('data-id'), 10), fieldOf(td), val,
                    function (res) {
                        pending--;
                        td.classList.remove('saving');
                        if (!res || !res.Ok) {
                            var msg = (res && res.Message) || 'The change was not saved.';
                            markError(td, savedValue(td), msg);
                            showToast(msg, 'error');
                            return;
                        }
                        applyResult(tr, td, res);
                        if (res.Message) { showToast(res.Message, res.Level || 'success'); }
                    },
                    function (err) {
                        pending--;
                        td.classList.remove('saving');
                        var msg = 'Save failed: ' + ((err && err.get_message && err.get_message()) || 'network error') +
                                  '. Your session may have expired - reload the page.';
                        markError(td, savedValue(td), msg);
                        showToast(msg, 'error');
                    });
            }

            function applyResult(tr, savedTd, res) {
                var vals = res.Values || {};
                var html = res.Html || {};
                for (var i = 0; i < fields.length; i++) {
                    var f = fields[i];
                    if (!(f in vals)) { continue; }
                    var td = tr.cells[i];
                    if (td.classList.contains('e')) {
                        if (td === editTd) {
                            // Being edited (e.g. Tab from Remarks into Man Hours): take the server value
                            // unless the user has already typed, so a stale value is not saved back.
                            if (editor.value === editStart) { editor.value = vals[f]; editStart = vals[f]; editor.select(); }
                            editSaved = vals[f];
                        } else if (td === savedTd || !td.hasAttribute('data-orig')) {
                            td.textContent = vals[f];
                            td.removeAttribute('data-orig');
                        } else {
                            td.setAttribute('data-orig', vals[f]);   // keep the other cell's rejected text
                        }
                    } else if (f in html) {
                        td.innerHTML = html[f];
                        td.setAttribute('data-n', vals[f]);
                    }
                }
                clearError(savedTd);
                savedTd.classList.add('saved');
                tr.classList.add('row-saved');
                tr.classList.toggle('row-deleted', !!res.Deleted);
                var r = rows.find(function (x) { return x.tr === tr; });
                if (r) { readRow(r); }
                updateStats();   // rows stay visible until the filters are applied again
            }

            function visibleSibling(tr, dir) {
                do { tr = dir > 0 ? tr.nextElementSibling : tr.previousElementSibling; }
                while (tr && tr.style.display === 'none');
                return tr;
            }

            // Up / down in the same column, or left / right to the next editable cell (wrapping rows).
            function moveFrom(td, dRow, dCol) {
                var tr = td.parentNode, idx = td.cellIndex, target = null;
                if (dRow) {
                    var r = visibleSibling(tr, dRow);
                    target = r ? r.cells[idx] : null;
                } else {
                    var i = idx;
                    while (!target) {
                        i += dCol;
                        if (i < 0 || i >= tr.cells.length) {
                            tr = visibleSibling(tr, dCol);
                            if (!tr) { break; }
                            i = dCol > 0 ? -1 : tr.cells.length;
                            continue;
                        }
                        if (tr.cells[i].classList.contains('e')) { target = tr.cells[i]; }
                    }
                }
                if (target && target.classList.contains('e')) { beginEdit(target); } else { endEdit(true); }
            }

            grid.addEventListener('click', function (e) {
                var td = e.target.closest ? e.target.closest('td.e') : null;
                if (td && td !== editTd) { beginEdit(td); }
            });

            editor.addEventListener('blur', function () {
                // Clicking elsewhere commits the cell (the editor is moved, not destroyed, on navigation).
                setTimeout(function () { if (editTd && document.activeElement !== editor) { endEdit(true); } }, 0);
            });

            editor.addEventListener('keydown', function (e) {
                var td = editTd;
                if (!td) { return; }
                if (e.key === 'Enter') { e.preventDefault(); moveFrom(td, e.shiftKey ? -1 : 1, 0); }
                else if (e.key === 'ArrowDown') { e.preventDefault(); moveFrom(td, 1, 0); }
                else if (e.key === 'ArrowUp') { e.preventDefault(); moveFrom(td, -1, 0); }
                else if (e.key === 'Tab') { e.preventDefault(); moveFrom(td, 0, e.shiftKey ? -1 : 1); }
                else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); endEdit(false); }
            });

            window.addEventListener('beforeunload', function (e) {
                store('addr2.scroll.' + stateKey, String(scroller.scrollTop));
                if (editTd) { endEdit(true); }
                if (pending > 0 || grid.querySelector('td.err')) {
                    e.preventDefault();
                    e.returnValue = 'Some changes are still saving or were not saved.';
                    return e.returnValue;
                }
            });

            /* ================================================================
               Export: the server exports exactly the rows on screen, in this order
               ================================================================ */
            window.prepareExport = function () {
                var ids = visibleRows().map(function (r) { return r.id; });
                document.getElementById('hfExportIds').value = ids.length ? ids.join(',') : '-';
                return true;
            };

            /* ================================================================
               Keyboard shortcuts
               ================================================================ */
            document.addEventListener('keydown', function (e) {
                if (e.key === 'Escape' && !pop.hidden) { closeFilter(); return; }
                var t = e.target;
                var tag = (t && t.tagName) || '';
                if (e.key === 'Enter' && tag === 'INPUT' && t.type !== 'checkbox' && t.type !== 'submit' && t.type !== 'button') {
                    e.preventDefault();   // never submit the form from a text box
                    return;
                }
                if (e.key === '/' && !/^(INPUT|TEXTAREA|SELECT)$/.test(tag)) { e.preventDefault(); search.focus(); search.select(); }
            });

            /* ---------- Start-up ---------- */
            restoreState();
            applyFilters();
            var sc = parseInt(load('addr2.scroll.' + stateKey) || '0', 10);
            if (sc > 0) { scroller.scrollTop = sc; }
        })();
    </script>
</form>
</body>
</html>

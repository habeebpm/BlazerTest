<%@ Page Language="vb" AutoEventWireup="false" CodeBehind="SmartDDRv3.aspx.vb" Inherits="SmarTagsASP.SmartDDRv3" %>

<!DOCTYPE html>
<html lang="en">
<head runat="server">
    <meta charset="utf-8" />
    <meta http-equiv="X-UA-Compatible" content="IE=edge" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Worley SmartDDR</title>

    <style>
        /* ================================================================
           Theme tokens (original SmartDDR teal palette)
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
            --sidebar-w: 252px;
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

        .app { display: flex; height: 100vh; overflow: hidden; }

        /* ================================================================
           Sidebar
           ================================================================ */
        .sidebar {
            width: var(--sidebar-w);
            flex: 0 0 var(--sidebar-w);
            display: flex;
            flex-direction: column;
            background: linear-gradient(180deg, var(--brand-900) 0%, #17504B 60%, var(--brand-700) 100%);
            color: #fff;
            overflow: hidden;
            transition: margin-left .25s ease, transform .25s ease;
            z-index: 40;
        }
        body.sidebar-collapsed .sidebar { margin-left: calc(-1 * var(--sidebar-w)); }

        .brand {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 16px 16px 14px;
            border-bottom: 1px solid rgba(255, 255, 255, .1);
        }
        .brand-mark {
            width: 34px; height: 34px;
            border-radius: 10px;
            background: var(--brand-500);
            display: flex; align-items: center; justify-content: center;
            font-weight: 700; font-size: 17px;
            box-shadow: 0 4px 10px rgba(0, 0, 0, .2);
        }
        .brand-name { font-weight: 700; font-size: 17px; letter-spacing: .2px; }
        .brand-sub { font-size: 11px; opacity: .7; }

        .nav {
            flex: 1;
            overflow-y: auto;
            padding: 4px 10px 16px;
        }
        .nav-context {
            margin: 8px 0 6px;
            padding: 8px 8px 4px;
            border-radius: 10px;
            background: rgba(0, 0, 0, .14);
            border: 1px solid rgba(255, 255, 255, .08);
        }
        .nav-context .nav-label { margin: 4px 2px 5px; }
        .nav-tools { display: flex; gap: 6px; margin: 8px 0 6px; }
        .nav-find {
            flex: 1;
            min-width: 0;
            height: 30px;
            padding: 0 10px;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, .22);
            background: rgba(255, 255, 255, .1);
            color: #fff;
            font: inherit;
            font-size: 12.5px;
            outline: none;
        }
        .nav-find::placeholder { color: rgba(255, 255, 255, .6); }
        .nav-find:focus { background: rgba(255, 255, 255, .18); border-color: var(--brand-500); }
        .nav-tool {
            width: 30px; height: 30px;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, .22);
            background: rgba(255, 255, 255, .1);
            color: #fff;
            cursor: pointer;
        }
        .nav-tool:hover { background: rgba(255, 255, 255, .22); }

        /* Collapsible groups */
        .nav-group { margin: 4px 0; border-radius: 10px; }
        .nav-group[open] { background: rgba(255, 255, 255, .05); }
        .nav-group > summary {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 10px;
            border-radius: 10px;
            cursor: pointer;
            list-style: none;   /* with display:flex this hides the default triangle */
            user-select: none;
            font-size: 12px;
            font-weight: 700;
            letter-spacing: .6px;
            text-transform: uppercase;
            color: rgba(255, 255, 255, .85);
        }
        .nav-group > summary:hover { background: rgba(255, 255, 255, .08); color: #fff; }
        .nav-group > summary:focus-visible { outline: 2px solid var(--gold); outline-offset: -2px; }
        .g-ic { width: 20px; text-align: center; font-size: 14px; }
        .g-title { flex: 1; }
        .g-count {
            min-width: 20px;
            padding: 1px 6px;
            border-radius: 999px;
            background: rgba(255, 255, 255, .14);
            font-size: 10.5px;
            text-align: center;
            letter-spacing: 0;
        }
        .g-chev {
            width: 8px; height: 8px;
            border-right: 2px solid currentColor;
            border-bottom: 2px solid currentColor;
            transform: rotate(-45deg);
            transition: transform .2s ease;
            margin-right: 2px;
        }
        .nav-group[open] > summary .g-chev { transform: rotate(45deg); }
        .nav-group.has-active > summary { color: var(--gold); }
        .g-body { padding: 0 4px 6px 8px; }
        .nav-group .nav-item.active::before { left: -12px; }
        .nav-item.find-hidden, .nav-row.find-hidden, .nav-group.find-hidden { display: none; }
        .nav-empty { padding: 8px 10px; font-size: 12px; color: rgba(255, 255, 255, .65); }
        .nav-label {
            display: block;
            font-size: 10.5px;
            font-weight: 700;
            letter-spacing: .9px;
            text-transform: uppercase;
            color: rgba(255, 255, 255, .6);
            margin: 6px 6px;
        }
        .side-select {
            width: 100%;
            height: 34px;
            padding: 0 10px;
            margin-bottom: 6px;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, .25);
            background: #fff;
            color: var(--text);
            font: inherit;
            font-size: 13px;
        }
        .nav-item {
            position: relative;
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 7px 10px;
            margin: 1px 0;
            border-radius: 8px;
            color: rgba(255, 255, 255, .92);
            text-decoration: none;
            font-size: 13px;
            font-weight: 500;
            transition: background .15s, color .15s;
        }
        .nav-item .ic { flex: 0 0 20px; width: 20px; text-align: center; font-size: 15px; }
        .nav-item:hover { background: rgba(255, 255, 255, .1); color: var(--gold); }
        .nav-item.active { background: var(--brand-500); color: #fff; box-shadow: 0 2px 8px rgba(0, 0, 0, .18); }
        .nav-item.active::before {
            content: "";
            position: absolute;
            left: -10px; top: 6px; bottom: 6px;
            width: 3px;
            border-radius: 0 3px 3px 0;
            background: var(--gold);
        }
        .nav-row { display: flex; align-items: center; gap: 6px; }
        .nav-row .nav-item { flex: 1; min-width: 0; }
        .nav-mini {
            flex: 0 0 auto;
            font-size: 11px;
            font-weight: 600;
            padding: 4px 9px;
            border-radius: 999px;
            color: #fff;
            text-decoration: none;
            white-space: nowrap;
            background: rgba(255, 255, 255, .14);
            border: 1px solid rgba(255, 255, 255, .28);
        }
        .nav-mini:hover { background: var(--danger); border-color: var(--danger); }

        .sidebar-foot {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 10px 12px;
            border-top: 1px solid rgba(255, 255, 255, .1);
            font-size: 12px;
        }
        .avatar {
            width: 28px; height: 28px;
            border-radius: 50%;
            background: rgba(255, 255, 255, .18);
            display: flex; align-items: center; justify-content: center;
        }
        .user-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .admin-link {
            color: #fff;
            text-decoration: none;
            padding: 4px 8px;
            border-radius: 6px;
            background: rgba(255, 255, 255, .14);
        }
        .admin-link:hover { background: rgba(255, 255, 255, .28); }
        .sidebar-backdrop { display: none; }

        /* ================================================================
           Main column
           ================================================================ */
        .main { flex: 1; min-width: 0; display: flex; flex-direction: column; }

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
        .icon-btn {
            width: 34px; height: 34px;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, .3);
            background: rgba(255, 255, 255, .14);
            color: #fff;
            font-size: 16px;
            cursor: pointer;
        }
        .icon-btn:hover { background: rgba(255, 255, 255, .28); }
        .crumbs { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
        .crumb-app { font-size: 13px; opacity: .75; white-space: nowrap; }
        .crumb-app:empty, .crumb-app:empty + .crumb-sep { display: none; }
        .crumb-sep { opacity: .5; }
        .crumb-view { font-size: 18px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
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
        .whats-new {
            display: flex;
            align-items: center;
            gap: 4px;
            min-width: 0;
            max-width: 520px;
            padding: 3px 4px 3px 12px;
            border-radius: 999px;
            background: #FEF9C3;
            color: #713F12;
            font-size: 11.5px;
        }
        .whats-new > span { min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
        .whats-new.hidden { display: none; }
        .wn-close {
            border: 0;
            background: transparent;
            color: inherit;
            cursor: pointer;
            font-size: 14px;
            line-height: 1;
            padding: 2px 7px;
            border-radius: 50%;
        }
        .wn-close:hover { background: rgba(0, 0, 0, .08); }

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
        .btn-primary { background: var(--brand-500); border-color: var(--brand-500); color: #fff; }
        .btn-primary:hover { background: var(--brand-700); border-color: var(--brand-700); color: #fff; }
        .btn-ghost { background: transparent; }
        .btn-on-dark { background: rgba(255, 255, 255, .16); border-color: rgba(255, 255, 255, .3); color: #fff; }
        .btn-on-dark:hover { background: rgba(255, 255, 255, .28); border-color: rgba(255, 255, 255, .5); color: #fff; }

        /* Toolbar */
        .toolbar {
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
            padding: 10px 16px;
            background: var(--surface);
            border-bottom: 1px solid var(--border);
        }
        .search {
            display: flex;
            align-items: center;
            gap: 6px;
            flex: 0 1 400px;
            min-width: 280px;
            padding: 0 3px 0 10px;
            border-radius: 10px;
            border: 1px solid var(--border);
            background: var(--bg);
        }
        .search:focus-within { border-color: var(--brand-500); box-shadow: 0 0 0 3px rgba(20, 184, 166, .15); }
        .search-ic { font-size: 13px; opacity: .6; }
        .search-input {
            flex: 1;
            min-width: 0;
            height: 36px;
            border: 0;
            outline: none;
            background: transparent;
            font: inherit;
            font-size: 13px;
        }
        .field { display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600; color: var(--muted); }
        .select {
            height: 34px;
            max-width: 260px;
            padding: 0 10px;
            border-radius: 8px;
            border: 1px solid var(--border-strong);
            background: #fff;
            color: var(--text);
            font: inherit;
            font-size: 13px;
        }
        .select:focus { outline: none; border-color: var(--brand-500); box-shadow: 0 0 0 3px rgba(20, 184, 166, .15); }
        .chip {
            display: inline-flex;
            align-items: center;
            padding: 4px 10px;
            border-radius: 999px;
            background: var(--brand-100);
            color: var(--brand-700);
            font-size: 12px;
            font-weight: 600;
        }
        .chip:empty { display: none; }
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
        .count-live { font-size: 12px; color: var(--brand-700); }
        .count-live:empty { display: none; }

        /* ================================================================
           Content + cards
           ================================================================ */
        .content { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 12px 16px 16px; }
        .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); }
        .grid-card { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
        .grid-scroll { flex: 1; min-height: 0; overflow: auto; }

        /* ================================================================
           Data grid
           ================================================================ */
        table.grid { width: 100%; border-collapse: separate !important; border-spacing: 0; font-size: 12.5px; }
        table.grid th {
            position: sticky;
            top: 0;
            z-index: 2;
            padding: 8px 10px;
            background: var(--brand-850);
            color: #fff;
            font-weight: 600;
            text-align: left;
            white-space: nowrap;
            border-right: 1px solid rgba(255, 255, 255, .08);
            cursor: pointer;
            user-select: none;
        }
        table.grid th:hover { background: #244B4B; }
        table.grid th.sort-asc::after { content: " \25B2"; font-size: 9px; color: var(--gold); }
        table.grid th.sort-desc::after { content: " \25BC"; font-size: 9px; color: var(--gold); }
        table.grid td {
            padding: 6px 10px;
            border-bottom: 1px solid var(--border);
            border-right: 1px solid #EEF2F7;
            white-space: nowrap;
            vertical-align: middle;
        }
        table.grid td.c { text-align: center; }
        table.grid tbody tr:nth-child(even) td { background: #F8FAFC; }
        table.grid tbody tr:hover td { background: var(--brand-50); }

        /* Row / cell status colours (later rules win) */
        table.grid tbody tr.row-done td { background: #DBF7CE; }
        table.grid tbody tr.row-partial td { background: #FFFBE6; }
        table.grid tbody tr.row-deleted td { background: #DBEAFE; color: #1E3A8A; text-decoration: line-through; }
        table.grid tbody tr.row-dup td { background: #FEF08A; color: #7F1D1D; }
        table.grid td.cell-warn { background: #F8B4B4 !important; }
        table.grid td.cell-hl { background: #FEF08A !important; font-weight: 600; }

        table.grid tfoot td,
        table.grid tr.grid-footer td {
            position: sticky;
            bottom: 0;
            z-index: 1;
            background: var(--brand-450);
            color: #0B2524;
            font-weight: 700;
            border-top: 2px solid var(--brand-700);
        }
        table.grid tr.empty td {
            padding: 48px 16px;
            text-align: center;
            white-space: normal;
            color: var(--muted);
            font-size: 14px;
            background: #fff;
        }

        /* Progress bar */
        .pbar { display: flex; flex-direction: column; gap: 2px; min-width: 76px; }
        .pbar-val { font-size: 11px; font-weight: 600; text-align: center; line-height: 1.15; }
        .pbar-track { display: block; height: 6px; border-radius: 999px; background: #E5E7EB; overflow: hidden; }
        .pbar-fill { display: block; height: 100%; border-radius: 999px; }
        .pb-good { background: #22C55E; }
        .pb-ok { background: #3B82F6; }
        .pb-warn { background: #F59E0B; }
        .pb-bad { background: #EF4444; }

        /* In-cell links & actions */
        .cell-link { color: var(--brand-700); font-weight: 600; text-decoration: none; border-bottom: 1px dotted currentColor; }
        .cell-link:hover { color: var(--brand-500); }
        .drill {
            display: inline-block;
            min-width: 34px;
            padding: 1px 8px;
            border-radius: 999px;
            background: var(--brand-100);
            color: var(--brand-700);
            font-weight: 700;
            text-decoration: none;
        }
        .drill:hover { background: var(--brand-500); color: #fff; }
        .row-btn {
            margin-left: 8px;
            height: 22px;
            padding: 0 8px;
            border-radius: 6px;
            border: 1px solid var(--brand-500);
            background: #fff;
            color: var(--brand-700);
            font: inherit;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
        }
        .row-btn:hover { background: var(--brand-500); color: #fff; }

        /* ================================================================
           Toast, drawer, loader
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

        .drawer-wrap { position: fixed; top: 0; right: 0; bottom: 0; left: 0; z-index: 1000; }
        .drawer-backdrop { position: absolute; top: 0; right: 0; bottom: 0; left: 0; background: rgba(15, 23, 42, .45); animation: fade .2s ease; }
        .drawer {
            position: absolute;
            top: 0; right: 0; bottom: 0;
            width: 760px;
            max-width: 96vw;
            display: flex;
            flex-direction: column;
            background: #fff;
            box-shadow: -10px 0 30px rgba(0, 0, 0, .2);
            animation: slide .25s ease;
        }
        .drawer-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 12px;
            padding: 16px 18px;
            background: linear-gradient(135deg, var(--brand-900), var(--brand-500));
            color: #fff;
        }
        .drawer-head h3 { font-size: 16px; margin-bottom: 2px; }
        .drawer-sub { font-size: 12px; color: rgba(255, 255, 255, .85); }
        .drawer-actions { display: flex; gap: 6px; }
        @keyframes slide { from { transform: translateX(40px); opacity: 0; } to { transform: none; opacity: 1; } }
        @keyframes fade { from { opacity: 0; } }

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

        .hidden-trigger { display: none !important; }

        /* ================================================================
           Responsive + print
           ================================================================ */
        @media (max-width: 900px) {
            .sidebar {
                position: fixed;
                top: 0; bottom: 0; left: 0;
                margin-left: 0 !important;
                transform: translateX(-100%);
                box-shadow: 0 0 30px rgba(0, 0, 0, .3);
            }
            body.sidebar-open .sidebar { transform: none; }
            body.sidebar-open .sidebar-backdrop {
                display: block;
                position: fixed;
                top: 0; right: 0; bottom: 0; left: 0;
                z-index: 30;
                background: rgba(0, 0, 0, .4);
            }
            .whats-new { display: none; }
            .search { flex: 1 1 100%; min-width: 0; }
            .project-pill { max-width: 100%; }
            .content { padding: 8px; }
        }
        @media print {
            .sidebar, .toolbar, .topbar .btn, .icon-btn, .whats-new { display: none !important; }
            .app { height: auto; overflow: visible; }
            .grid-scroll { overflow: visible; }
            table.grid th, table.grid tfoot td { position: static; }
        }
    </style>
</head>

<body>
<form id="frmSmartDDR" runat="server">
    <asp:ScriptManager ID="ScriptManager1" runat="server" />

    <div class="app">

        <!-- ============================ Sidebar ============================ -->
        <aside class="sidebar" aria-label="Reports">
            <div class="brand">
                <div class="brand-mark">S</div>
                <div>
                    <div class="brand-name">SmartDDR</div>
                    <div class="brand-sub">Worley &middot; v3</div>
                </div>
            </div>

            <nav class="nav">
                <!-- Context: always visible so every group can use it -->
                <div class="nav-context">
                    <label class="nav-label" for="ddlProjectGroup">Project group</label>
                    <asp:DropDownList ID="ddlProjectGroup" runat="server" ClientIDMode="Static"
                        CssClass="side-select" AutoPostBack="True" data-loading="true">
                        <asp:ListItem Text="PDC-QA" Value="3" />
                        <asp:ListItem Text="PDC-FLY" Value="4" />
                    </asp:DropDownList>
                    <label class="nav-label" for="DDLPROJNO">Project</label>
                    <asp:DropDownList ID="DDLPROJNO" runat="server" ClientIDMode="Static"
                        CssClass="side-select" AutoPostBack="True" data-loading="true" />
                </div>

                <div class="nav-tools">
                    <input type="search" id="navFind" class="nav-find" placeholder="Find a report…" aria-label="Find a report" autocomplete="off" />
                    <button type="button" class="nav-tool js-nav-toggle-all" title="Expand / collapse all groups">⇕</button>
                </div>

                <details class="nav-group" data-group="groups">
                    <summary title="Reports across every project in the selected group"><span class="g-ic">🏗️</span><span class="g-title">Project Groups</span><span class="g-count"></span><span class="g-chev" aria-hidden="true"></span></summary>
                    <div class="g-body">
                        <asp:LinkButton ID="Project_Summary" runat="server" CssClass="nav-item" CommandArgument="G_INFO" data-loading="true"><span class="ic">📄</span><span>Projects Info</span></asp:LinkButton>
                        <asp:LinkButton ID="M75_AFC_ALL" runat="server" CssClass="nav-item" CommandArgument="G_M75" data-loading="true"><span class="ic">🚩</span><span>M75 AFC Status</span></asp:LinkButton>
                        <asp:LinkButton ID="IFR_ALL" runat="server" CssClass="nav-item" CommandArgument="G_AFC_IFR" data-loading="true"><span class="ic">📍</span><span>AFC/IFR Status</span></asp:LinkButton>
                        <asp:LinkButton ID="PLIP_STATUS" runat="server" CssClass="nav-item" CommandArgument="G_PLIP" data-loading="true"><span class="ic">📑</span><span>DDR PLIP Status</span></asp:LinkButton>
                        <asp:LinkButton ID="DDR_ACT_ALL" runat="server" CssClass="nav-item" CommandArgument="G_DDR_ACT" data-loading="true"><span class="ic">🗂️</span><span>DDR + Activity</span></asp:LinkButton>
                    </div>
                </details>

                <details class="nav-group" data-group="projects">
                    <summary title="Core registers of the selected project"><span class="g-ic">📁</span><span class="g-title">Projects</span><span class="g-count"></span><span class="g-chev" aria-hidden="true"></span></summary>
                    <div class="g-body">
                        <asp:LinkButton ID="CTD" runat="server" CssClass="nav-item" CommandArgument="CTD" data-loading="true"><span class="ic">💸</span><span>Approved CTD</span></asp:LinkButton>
                        <asp:LinkButton ID="DDR1" runat="server" CssClass="nav-item" CommandArgument="DDR_ACT" data-loading="true"><span class="ic">📄</span><span>DDR + Activity</span></asp:LinkButton>
                        <asp:LinkButton ID="ACTIVITY" runat="server" CssClass="nav-item" CommandArgument="ACT" data-loading="true"><span class="ic">🚀</span><span>Activities</span></asp:LinkButton>
                        <asp:LinkButton ID="DDR2" runat="server" CssClass="nav-item" CommandArgument="DDR_PDO" data-loading="true"><span class="ic">📑</span><span>DDR Format (PDO)</span></asp:LinkButton>
                    </div>
                </details>

                <details class="nav-group" data-group="progress">
                    <summary title="Earned progress and issue status of the selected project"><span class="g-ic">📈</span><span class="g-title">Progress &amp; Status</span><span class="g-count"></span><span class="g-chev" aria-hidden="true"></span></summary>
                    <div class="g-body">
                        <asp:LinkButton ID="DDR_EPR" runat="server" CssClass="nav-item" CommandArgument="DDR_EPR" data-loading="true"><span class="ic">📈</span><span>DDR + EPR</span></asp:LinkButton>
                        <asp:LinkButton ID="EPR" runat="server" CssClass="nav-item" CommandArgument="EPR" data-loading="true"><span class="ic">📊</span><span>EPR</span></asp:LinkButton>
                        <asp:LinkButton ID="M75_AFC" runat="server" CssClass="nav-item" CommandArgument="M75" data-loading="true"><span class="ic">🚩</span><span>M75 AFC Status</span></asp:LinkButton>
                        <asp:LinkButton ID="IFR" runat="server" CssClass="nav-item" CommandArgument="AFC_IFR" data-loading="true"><span class="ic">📍</span><span>AFC/IFR Status</span></asp:LinkButton>
                    </div>
                </details>

                <details class="nav-group" data-group="references">
                    <summary title="Aconex document registers for the selected project"><span class="g-ic">📚</span><span class="g-title">References</span><span class="g-count"></span><span class="g-chev" aria-hidden="true"></span></summary>
                    <div class="g-body">
                        <asp:LinkButton ID="Aconex1" runat="server" CssClass="nav-item" CommandArgument="ACON_ALL" data-loading="true"><span class="ic">🏢</span><span>Aconex All Rev</span></asp:LinkButton>
                        <asp:LinkButton ID="Aconex2" runat="server" CssClass="nav-item" CommandArgument="ACON_LATEST" data-loading="true"><span class="ic">📁</span><span>Aconex Latest</span></asp:LinkButton>
                    </div>
                </details>

                <details class="nav-group" data-group="checks">
                    <summary title="Reconciliation and data-quality checks"><span class="g-ic">✅</span><span class="g-title">Check Reports</span><span class="g-count"></span><span class="g-chev" aria-hidden="true"></span></summary>
                    <div class="g-body">
                        <asp:LinkButton ID="CTD_DDR" runat="server" CssClass="nav-item" CommandArgument="CTD_DDR" data-loading="true"><span class="ic">🔍</span><span>CTD vs DDR</span></asp:LinkButton>
                        <asp:LinkButton ID="CTD_DOC" runat="server" CssClass="nav-item" CommandArgument="CTD_DOC" data-loading="true"><span class="ic">🔎</span><span>CTD vs DOC</span></asp:LinkButton>
                        <asp:LinkButton ID="DDR_DUMMY" runat="server" CssClass="nav-item" CommandArgument="DUMMY" data-loading="true"><span class="ic">🎭</span><span>Dummy Serials</span></asp:LinkButton>
                        <asp:LinkButton ID="ACON_MISS" runat="server" CssClass="nav-item" CommandArgument="ACON_MISS" data-loading="true"><span class="ic">🚫</span><span>Aconex Missing</span></asp:LinkButton>
                        <div class="nav-row">
                            <asp:LinkButton ID="PushtoDDR" runat="server" CssClass="nav-item" CommandArgument="DDR_MISSED" data-loading="true"><span class="ic">📥</span><span>DDR Missed</span></asp:LinkButton>
                            <asp:LinkButton ID="Push" runat="server" CssClass="nav-mini"
                                ToolTip="Insert every missed document of this project into the DDR"
                                OnClientClick="return confirmPushAll();">Push all</asp:LinkButton>
                        </div>
                    </div>
                </details>
            </nav>

            <div class="sidebar-foot">
                <span class="avatar">👤</span>
                <asp:Label ID="lblUser" runat="server" CssClass="user-name" />
                <asp:HyperLink ID="lnkAdmin" runat="server" CssClass="admin-link"
                    NavigateUrl="Admin_Tasks.aspx" ToolTip="Administration" Visible="False">⚙️ Admin</asp:HyperLink>
            </div>
        </aside>
        <div class="sidebar-backdrop" id="sidebarBackdrop"></div>

        <!-- ============================ Main ============================ -->
        <main class="main">
            <header class="topbar">
                <button type="button" id="btnSidebar" class="icon-btn" aria-label="Toggle menu" title="Toggle menu">☰</button>
                <div class="crumbs">
                    <span class="crumb-app">SmartDDR</span>
                    <span class="crumb-sep">›</span>
                    <asp:Label ID="lblSection" runat="server" CssClass="crumb-app" />
                    <span class="crumb-sep js-section-sep">›</span>
                    <asp:Label ID="lblContext" runat="server" CssClass="crumb-view" Text="Select a report" />
                </div>
                <asp:Label ID="LblInfo" runat="server" CssClass="project-pill" />
                <span class="topbar-spacer"></span>
                <div class="whats-new" id="whatsNew">
                    <asp:Label ID="LblWN" runat="server"
                        Text="What's new (v3): reports grouped by purpose · find a report from the sidebar · Excel (.xlsx) export · click a header to sort · click an M75 count for its documents" />
                    <button type="button" class="wn-close" title="Dismiss" aria-label="Dismiss">×</button>
                </div>
                <a href="Index.aspx" class="btn btn-on-dark">🏠 Home</a>
            </header>

            <div class="toolbar">
                <asp:Panel ID="pnlSearch" runat="server" CssClass="search" DefaultButton="btnSearch">
                    <span class="search-ic">🔍</span>
                    <asp:TextBox ID="txtSearch" runat="server" ClientIDMode="Static" CssClass="search-input"
                        placeholder="Search all columns…   ( / )" autocomplete="off" />
                    <asp:Button ID="btnSearch" runat="server" Text="Search" CssClass="btn btn-primary" data-loading="true" />
                </asp:Panel>

                <div class="field">
                    <label for="DDLDISCIPLINE">Discipline</label>
                    <asp:DropDownList ID="DDLDISCIPLINE" runat="server" ClientIDMode="Static"
                        CssClass="select" AutoPostBack="True" data-loading="true" />
                </div>

                <asp:Button ID="btnClearFilter" runat="server" Text="✖ Clear" CssClass="btn btn-ghost"
                    ToolTip="Clear search and project filter" data-loading="true" />
                <asp:Label ID="lblFilterChip" runat="server" CssClass="chip" Visible="False" />

                <span class="toolbar-spacer"></span>

                <asp:Label ID="lblRowCount" runat="server" CssClass="count" />
                <span id="rowCountLive" class="count-live"></span>

                <asp:HyperLink ID="lnkDeepLink" runat="server" CssClass="btn btn-ghost js-copy-link"
                    ToolTip="Copy a link to this report">🔗 Link</asp:HyperLink>
                <asp:Button ID="btnExcel" runat="server" Text="⬇ Excel" CssClass="btn"
                    ToolTip="Download as Excel (.xlsx) with the current filters" />
                <asp:Button ID="btnCSV" runat="server" Text="⬇ CSV" CssClass="btn"
                    ToolTip="Download as CSV with the current filters" />
                <button type="button" class="btn btn-ghost" onclick="history.back();">⬅ Back</button>
            </div>

            <asp:Label ID="lblMessage" runat="server" Visible="False" EnableViewState="False" />

            <section class="content">
                <!-- Report grid (rebuilt from the cached data on every postback, so no ViewState) -->
                <asp:Panel ID="pnlGrid" runat="server" CssClass="card grid-card">
                    <div class="grid-scroll">
                        <asp:GridView ID="MyCommonGrid" runat="server"
                            AutoGenerateColumns="True"
                            CssClass="grid grid-main"
                            GridLines="None"
                            UseAccessibleHeader="True"
                            ShowFooter="False"
                            EnableViewState="False"
                            EmptyDataText="No records found for the current selection.">
                            <FooterStyle CssClass="grid-footer" />
                            <EmptyDataRowStyle CssClass="empty" />
                        </asp:GridView>
                    </div>
                </asp:Panel>
            </section>
        </main>
    </div>

    <!-- ===================== Row-action plumbing ===================== -->
    <asp:HiddenField ID="hfRowArg" runat="server" ClientIDMode="Static" />
    <asp:Button ID="btnPushRow" runat="server" ClientIDMode="Static" CssClass="hidden-trigger" TabIndex="-1" />
    <asp:Button ID="btnDrawerCsv" runat="server" ClientIDMode="Static" CssClass="hidden-trigger" TabIndex="-1" />

    <!-- ===================== Drill-down drawer (async) ===================== -->
    <asp:UpdatePanel ID="updDrawer" runat="server" UpdateMode="Conditional">
        <ContentTemplate>
            <asp:Button ID="btnDrill" runat="server" ClientIDMode="Static" CssClass="hidden-trigger" TabIndex="-1" />

            <asp:Panel ID="pnlDetails" runat="server" Visible="False" EnableViewState="False" CssClass="drawer-wrap">
                <div class="drawer-backdrop js-drawer-close"></div>
                <div class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawerTitle">
                    <div class="drawer-head">
                        <div>
                            <h3 id="drawerTitle"><asp:Label ID="lblDrawerTitle" runat="server" /></h3>
                            <asp:Label ID="lblDrawerCount" runat="server" CssClass="drawer-sub" />
                        </div>
                        <div class="drawer-actions">
                            <button type="button" class="btn btn-on-dark js-drawer-csv" title="Download these documents as CSV">⬇ CSV</button>
                            <button type="button" class="btn btn-on-dark js-drawer-close" title="Close (Esc)">✕</button>
                        </div>
                    </div>
                    <div class="grid-scroll">
                        <asp:GridView ID="grdDrawer" runat="server"
                            AutoGenerateColumns="True"
                            CssClass="grid"
                            GridLines="None"
                            UseAccessibleHeader="True"
                            EmptyDataText="No documents match.">
                            <EmptyDataRowStyle CssClass="empty" />
                        </asp:GridView>
                    </div>
                </div>
            </asp:Panel>
        </ContentTemplate>
    </asp:UpdatePanel>

    <!-- ===================== Loader ===================== -->
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

            var body = document.body;
            var loader = document.getElementById('loaderOverlay');

            function getPref(key) { try { return window.localStorage.getItem(key); } catch (e) { return null; } }
            function setPref(key, value) { try { window.localStorage.setItem(key, value); } catch (e) { /* storage unavailable */ } }

            /* ---------- Loader ---------- */
            window.showLoader = function () { if (loader) { loader.style.display = 'flex'; } return true; };
            window.hideLoader = function () { if (loader) { loader.style.display = 'none'; } };
            window.addEventListener('pageshow', window.hideLoader); // back/forward cache

            window.confirmPushAll = function () {
                if (!window.confirm('Push ALL missed documents of this project into the DDR?\n\nThis inserts new rows into CTD_DDR_DISC.')) { return false; }
                window.showLoader();
                return true;
            };

            function showToast(text, kind) {
                var t = document.createElement('div');
                t.className = 'toast toast-' + (kind || 'info');
                t.textContent = text;
                t.addEventListener('click', function () { t.classList.add('toast-hide'); });
                document.body.appendChild(t);
            }

            if (window.Sys && Sys.WebForms && Sys.WebForms.PageRequestManager) {
                var prm = Sys.WebForms.PageRequestManager.getInstance();
                prm.add_beginRequest(function () { window.showLoader(); });
                prm.add_endRequest(function (sender, args) {
                    window.hideLoader();
                    var err = args.get_error();
                    if (err) {
                        args.set_errorHandled(true);   // replaces the default alert() box
                        showToast('The details could not be loaded: ' + (err.message || err) +
                                  ' (your session may have expired - reload the page).', 'error');
                    }
                });
            }

            /* ---------- Sidebar ---------- */
            var narrow = window.matchMedia('(max-width: 900px)');
            if (getPref('sddr.sidebar') === 'collapsed') { body.classList.add('sidebar-collapsed'); }
            document.getElementById('btnSidebar').addEventListener('click', function () {
                if (narrow.matches) {
                    body.classList.toggle('sidebar-open');
                } else {
                    body.classList.toggle('sidebar-collapsed');
                    setPref('sddr.sidebar', body.classList.contains('sidebar-collapsed') ? 'collapsed' : 'open');
                }
            });
            document.getElementById('sidebarBackdrop').addEventListener('click', function () { body.classList.remove('sidebar-open'); });

            /* ---------- Collapsible report groups ---------- */
            var groups = Array.prototype.slice.call(document.querySelectorAll('.nav-group'));
            groups.forEach(function (g) {
                var key = 'sddr.nav.' + g.getAttribute('data-group');
                var items = g.querySelectorAll('.nav-item');
                g.querySelector('.g-count').textContent = items.length;
                var hasActive = !!g.querySelector('.nav-item.active');
                if (hasActive) { g.classList.add('has-active'); }
                var saved = getPref(key);
                // The group holding the current report is always open; others remember their state.
                setOpen(g, hasActive || saved === '1');
                g.addEventListener('toggle', function () {
                    if (g.getAttribute('data-auto') === '1') { g.removeAttribute('data-auto'); return; }
                    if (!navFind.value) { setPref(key, g.open ? '1' : '0'); }
                });
            });
            if (!groups.some(function (g) { return g.open; }) && groups[0]) { setOpen(groups[0], true); }

            // Open/close without it counting as the user's choice.
            function setOpen(g, open) {
                if (g.open === open) { return; }
                g.setAttribute('data-auto', '1');
                g.open = open;
            }

            document.querySelector('.js-nav-toggle-all').addEventListener('click', function () {
                var open = !groups.every(function (g) { return g.open; });
                groups.forEach(function (g) { g.open = open; });
            });

            var navFind = document.getElementById('navFind');
            var navEmpty = document.createElement('div');
            navEmpty.className = 'nav-empty';
            navEmpty.textContent = 'No matching report.';
            navEmpty.style.display = 'none';
            document.querySelector('.nav').appendChild(navEmpty);
            var openBeforeFind = null;
            navFind.addEventListener('input', function () {
                var q = navFind.value.trim().toLowerCase();
                if (q && openBeforeFind === null) { openBeforeFind = groups.map(function (g) { return g.open; }); }
                var any = false;
                groups.forEach(function (g, i) {
                    var groupHit = g.querySelector('.g-title').textContent.toLowerCase().indexOf(q) >= 0;
                    var hits = 0;
                    Array.prototype.forEach.call(g.querySelectorAll('.nav-item'), function (a) {
                        var hit = !q || groupHit || a.textContent.toLowerCase().indexOf(q) >= 0;
                        var row = a.parentNode.classList.contains('nav-row') ? a.parentNode : a;
                        row.classList.toggle('find-hidden', !hit);
                        if (hit) { hits++; }
                    });
                    g.classList.toggle('find-hidden', q && hits === 0);
                    if (q) { setOpen(g, hits > 0); } else if (openBeforeFind) { setOpen(g, openBeforeFind[i]); }
                    if (hits > 0) { any = true; }
                });
                if (!q) { openBeforeFind = null; }
                navEmpty.style.display = any ? 'none' : 'block';
            });
            navFind.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') {
                    e.preventDefault();   // do not submit the form
                    var first = document.querySelector('.nav-group:not(.find-hidden) .nav-item:not(.find-hidden)');
                    if (first) { first.click(); }
                }
            });

            /* ---------- What's new ---------- */
            var wn = document.getElementById('whatsNew');
            var wnKey = 'sddr.whatsnew.' + (wn ? wn.textContent.trim().length : 0);
            if (wn && getPref(wnKey) === '1') { wn.classList.add('hidden'); }
            if (wn) { wn.title = wn.textContent.replace('×', '').trim(); }

            /* ---------- Toast ---------- */
            var toast = document.querySelector('.toast');
            if (toast) {
                toast.addEventListener('click', function () { toast.classList.add('toast-hide'); });
                if (!toast.classList.contains('toast-error')) {
                    setTimeout(function () { toast.classList.add('toast-hide'); }, 7000);
                }
            }

            /* ---------- Row actions (drill-down / push) ---------- */
            function fire(buttonId, arg) {
                document.getElementById('hfRowArg').value = arg || '';
                var b = document.getElementById(buttonId);
                if (b) { b.click(); }
            }
            function closeDrawer() {
                var w = document.querySelector('.drawer-wrap');
                if (w && w.parentNode) { w.parentNode.removeChild(w); }
            }

            /* ---------- Copy link ---------- */
            function copyLink(el) {
                var url = el.href;
                var done = function () {
                    var old = el.textContent;
                    el.textContent = '✔ Copied';
                    setTimeout(function () { el.textContent = old; }, 1500);
                };
                if (navigator.clipboard && window.isSecureContext) {
                    navigator.clipboard.writeText(url).then(done, function () { window.prompt('Copy this link:', url); });
                    return;
                }
                var ta = document.createElement('textarea');
                ta.value = url;
                ta.style.position = 'fixed';
                ta.style.opacity = '0';
                document.body.appendChild(ta);
                ta.select();
                var ok = false;
                try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
                document.body.removeChild(ta);
                if (ok) { done(); } else { window.prompt('Copy this link:', url); }
            }

            /* ---------- Client-side column sort ---------- */
            var MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
            function sortKey(text) {
                var t = (text || '').replace(/ /g, ' ').trim();
                if (!t) { return { k: 2, v: '' }; }
                var m = /^(\d{1,2})-([A-Za-z]{3})-(\d{4})/.exec(t);
                if (m) {
                    var mi = MONTHS.indexOf(m[2].toLowerCase());
                    if (mi > -1) { return { k: 0, v: new Date(+m[3], mi, +m[1]).getTime() }; }
                }
                var n = t.replace(/[,%\s]/g, '');
                if (/^[-+]?\d*\.?\d+(e[-+]?\d+)?$/i.test(n)) { return { k: 0, v: parseFloat(n) }; }
                return { k: 1, v: t.toLowerCase() };
            }
            function sortBy(th) {
                var table = th.closest('table');
                var tbody = table && table.tBodies[0];
                if (!tbody) { return; }
                var cells = th.parentNode.cells;
                var idx = Array.prototype.indexOf.call(cells, th);
                var asc = !th.classList.contains('sort-asc');
                Array.prototype.forEach.call(cells, function (c) { c.classList.remove('sort-asc', 'sort-desc'); });
                th.classList.add(asc ? 'sort-asc' : 'sort-desc');

                var keyed = [];
                Array.prototype.forEach.call(tbody.rows, function (r, i) {
                    if (r.classList.contains('empty')) { return; }
                    keyed.push({ r: r, i: i, s: sortKey(r.cells[idx] ? r.cells[idx].textContent : '') });
                });
                keyed.sort(function (a, b) {
                    if (a.s.k !== b.s.k) { return a.s.k - b.s.k; }          // numbers/dates, text, then blanks
                    var cmp = a.s.k === 1 ? a.s.v.localeCompare(b.s.v, undefined, { numeric: true })
                                          : (a.s.v < b.s.v ? -1 : (a.s.v > b.s.v ? 1 : 0));
                    if (cmp === 0) { return a.i - b.i; }
                    return asc ? cmp : -cmp;
                });
                var frag = document.createDocumentFragment();
                keyed.forEach(function (x) { frag.appendChild(x.r); });
                tbody.appendChild(frag);
            }

            /* ---------- Live (client-side) quick filter ---------- */
            var search = document.getElementById('txtSearch');
            var live = document.getElementById('rowCountLive');
            var timer = null;
            function liveFilter() {
                var q = search.value.trim().toLowerCase();
                var table = document.querySelector('table.grid-main');
                if (!table || !table.tBodies.length) { return; }
                var shown = 0, total = 0;
                Array.prototype.forEach.call(table.tBodies[0].rows, function (r) {
                    if (r.classList.contains('empty')) { return; }
                    total++;
                    var match = !q || r.textContent.toLowerCase().indexOf(q) >= 0;
                    r.style.display = match ? '' : 'none';
                    if (match) { shown++; }
                });
                live.textContent = q ? (shown + ' of ' + total + ' shown · Enter applies to totals & export') : '';
            }

            /* ---------- Event delegation ---------- */
            document.addEventListener('click', function (e) {
                var t = e.target;
                if (!t || !t.closest) { return; }
                var el;
                if ((el = t.closest('.js-drill'))) { e.preventDefault(); fire('btnDrill', el.getAttribute('data-arg')); return; }
                if ((el = t.closest('.js-push'))) {
                    e.preventDefault();
                    var doc = el.getAttribute('data-arg');
                    if (window.confirm('Push document ' + doc + ' into the DDR?')) { window.showLoader(); fire('btnPushRow', doc); }
                    return;
                }
                if (t.closest('.js-drawer-close')) { closeDrawer(); return; }
                if (t.closest('.js-drawer-csv')) { var csv = document.getElementById('btnDrawerCsv'); if (csv) { csv.click(); } return; }
                if ((el = t.closest('.js-copy-link'))) { e.preventDefault(); copyLink(el); return; }
                if (t.closest('.wn-close')) { if (wn) { wn.classList.add('hidden'); setPref(wnKey, '1'); } return; }
                if ((el = t.closest('table.grid thead th'))) { sortBy(el); return; }
                if (t.closest('[data-loading]:not(select)')) { window.showLoader(); }
            });

            document.addEventListener('change', function (e) {
                var t = e.target;
                if (t && t.tagName === 'SELECT' && t.hasAttribute('data-loading')) { window.showLoader(); }
            });

            document.addEventListener('input', function (e) {
                var t = e.target;
                if (!t) { return; }
                if (t === search) { clearTimeout(timer); timer = setTimeout(liveFilter, 120); }
            });

            document.addEventListener('keydown', function (e) {
                if (e.key === 'Escape') {
                    if (document.activeElement === navFind && navFind.value) { navFind.value = ''; navFind.dispatchEvent(new Event('input')); return; }
                    closeDrawer(); body.classList.remove('sidebar-open'); return;
                }
                var tag = (document.activeElement && document.activeElement.tagName) || '';
                if (e.key === '/' && !/^(INPUT|TEXTAREA|SELECT)$/.test(tag)) { e.preventDefault(); search.focus(); search.select(); }
            });
        })();
    </script>
</form>
</body>
</html>

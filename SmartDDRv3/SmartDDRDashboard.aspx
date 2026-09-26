<%@ Page Language="vb" AutoEventWireup="false" CodeBehind="SmartDDRDashboard.aspx.vb" Inherits="SmarTagsASP.SmartDDRDashboard" EnableSessionState="false" EnableViewState="false" %>

<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta http-equiv="X-UA-Compatible" content="IE=edge" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>SmartDDR Dashboard</title>
    <style>
        /* =====================================================================
           Tokens. Chrome keeps the SmartDDR teal; chart colours come from a
           validated, colour-blind-safe palette (light + dark each validated).
           ===================================================================== */
        :root {
            color-scheme: light;
            --brand-900: #1C3737; --brand-850: #1B3B3B; --brand-700: #0F766E; --brand-500: #14B8A6; --brand-100: #CCFBF1;
            --page: #F4F7FC; --surface: #FCFCFB; --surface-2: #F3F5F8; --border: rgba(11,11,11,.10); --border-strong: #CBD5E1;
            --ink: #0B0B0B; --ink-2: #52514E; --muted: #898781; --grid: #E1E0D9; --axis: #C3C2B7;
            --s1: #2A78D6; --s2: #EB6834; --s3: #1BAF7A;  /* planned / actual / baseline (validated as a set) */
            --bad-text: #B42318;
            --grad-brand: linear-gradient(120deg, #0B2323 0%, #1C3737 32%, #0F766E 68%, #14B8A6 100%);
            --grad-hero: linear-gradient(135deg, #0E2B2B 0%, #125E58 48%, #14A596 100%);
            --grad-accent: linear-gradient(90deg, #0F766E 0%, #14B8A6 55%, #2A78D6 100%);
            --grad-tile: linear-gradient(180deg, #FFFFFF 0%, #F6F9FC 100%);
            --glow: 0 10px 30px -12px rgba(15,118,110,.35);
            --o1: #86B6EF; --o2: #5598E7; --o3: #2A78D6; --o4: #1C5CAB; --o5: #104281;   /* ordinal ramp, low -> high */
            --q1: #CDE2FB; --q2: #9EC5F4; --q3: #6DA7EC; --q4: #3987E5; --q5: #256ABF; --q6: #1C5CAB; --q7: #0D366B; /* sequential */
            --div-pos: #2A78D6; --div-neg: #E34948; --div-mid: #F0EFEC;
            --good: #0CA30C; --warning: #FAB219; --serious: #EC835A; --critical: #D03B3B; --good-text: #006300;
            --shadow: 0 1px 2px rgba(15,23,42,.06), 0 6px 18px rgba(15,23,42,.06);
            --radius: 14px;
            --font: system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
        }
        @media (prefers-color-scheme: dark) {
            :root:where(:not([data-theme="light"])) {
                color-scheme: dark;
                --page: #0D0D0D; --surface: #1A1A19; --surface-2: #232322; --border: rgba(255,255,255,.10); --border-strong: #3A3A38;
                --ink: #FFFFFF; --ink-2: #C3C2B7; --muted: #898781; --grid: #2C2C2A; --axis: #383835;
                --s1: #3987E5; --s2: #D95926; --s3: #199E70; --bad-text: #F97066;
                --grad-tile: linear-gradient(180deg, #1E1E1D 0%, #191918 100%);
                --grad-hero: linear-gradient(135deg, #0A1F1F 0%, #0F4A45 50%, #0F766E 100%);
                --glow: 0 10px 30px -12px rgba(20,184,166,.35);
                --o1: #256ABF; --o2: #3987E5; --o3: #6DA7EC; --o4: #9EC5F4; --o5: #CDE2FB;
                --q1: #184F95; --q2: #1C5CAB; --q3: #256ABF; --q4: #3987E5; --q5: #6DA7EC; --q6: #9EC5F4; --q7: #CDE2FB;
                --div-pos: #3987E5; --div-neg: #E66767; --div-mid: #383835; --good-text: #0CA30C;
                --brand-100: #134E4A;
            }
        }
        :root[data-theme="dark"] {
            color-scheme: dark;
            --page: #0D0D0D; --surface: #1A1A19; --surface-2: #232322; --border: rgba(255,255,255,.10); --border-strong: #3A3A38;
            --ink: #FFFFFF; --ink-2: #C3C2B7; --muted: #898781; --grid: #2C2C2A; --axis: #383835;
            --s1: #3987E5; --s2: #D95926; --s3: #199E70; --bad-text: #F97066;
            --grad-tile: linear-gradient(180deg, #1E1E1D 0%, #191918 100%);
            --grad-hero: linear-gradient(135deg, #0A1F1F 0%, #0F4A45 50%, #0F766E 100%);
            --glow: 0 10px 30px -12px rgba(20,184,166,.35);
            --o1: #256ABF; --o2: #3987E5; --o3: #6DA7EC; --o4: #9EC5F4; --o5: #CDE2FB;
            --q1: #184F95; --q2: #1C5CAB; --q3: #256ABF; --q4: #3987E5; --q5: #6DA7EC; --q6: #9EC5F4; --q7: #CDE2FB;
            --div-pos: #3987E5; --div-neg: #E66767; --div-mid: #383835; --good-text: #0CA30C;
            --brand-100: #134E4A;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        html, body { color: var(--ink); font-family: var(--font); font-size: 14px; }
        html { background: var(--page); }
        body {
            min-height: 100vh;
            background:
                radial-gradient(1100px 420px at 0% -8%, rgba(20,184,166,.13), transparent 62%),
                radial-gradient(900px 380px at 100% -6%, rgba(42,120,214,.10), transparent 60%),
                var(--page);
            background-attachment: fixed;
        }
        button, input, select { font: inherit; color: inherit; }
        a { color: inherit; }

        /* ---------------- Top bar ---------------- */
        .topbar {
            position: sticky; top: 0; z-index: 50;
            display: flex; align-items: center; flex-wrap: wrap; gap: 10px 14px;
            padding: 10px 18px;
            background: var(--grad-brand);
            color: #fff; box-shadow: 0 6px 24px -10px rgba(11,35,35,.55);
            border-bottom: 1px solid rgba(255,255,255,.10);
        }
        .topbar::before {   /* soft highlight sweep */
            content: ""; position: absolute; inset: 0; pointer-events: none;
            background: radial-gradient(600px 120px at 18% 0%, rgba(255,255,255,.16), transparent 70%),
                        radial-gradient(500px 140px at 85% 120%, rgba(42,120,214,.25), transparent 70%);
        }
        .topbar > * { position: relative; }
        .brand-mark { background: linear-gradient(135deg, rgba(255,255,255,.30), rgba(255,255,255,.08)) !important; border: 1px solid rgba(255,255,255,.35); box-shadow: inset 0 1px 0 rgba(255,255,255,.35); }
        .brand { display: flex; align-items: center; gap: 10px; text-decoration: none; }
        .brand-mark { width: 32px; height: 32px; border-radius: 9px; background: rgba(255,255,255,.18); display: flex; align-items: center; justify-content: center; font-weight: 800; }
        .brand-name { font-weight: 700; font-size: 16px; line-height: 1.1; }
        .brand-sub { font-size: 11px; opacity: .8; }
        #stamp { display: block; opacity: .85; }
        .top-spacer { flex: 1; }
        .top-field { display: flex; flex-direction: column; gap: 2px; font-size: 10.5px; font-weight: 700; letter-spacing: .5px; text-transform: uppercase; opacity: .95; }
        .top-field select, .top-field input {
            height: 32px; min-width: 120px; padding: 0 8px; border-radius: 8px;
            border: 1px solid rgba(255,255,255,.35); background: rgba(255,255,255,.95); color: #1F2937;
            font-size: 13px; font-weight: 500; letter-spacing: 0; text-transform: none;
        }
        .top-field select.wide { min-width: 240px; max-width: 320px; }
        .tbtn {
            height: 32px; padding: 0 12px; border-radius: 8px; border: 1px solid rgba(255,255,255,.35);
            background: linear-gradient(180deg, rgba(255,255,255,.22), rgba(255,255,255,.08)); color: #fff; cursor: pointer; font-size: 13px; font-weight: 600;
            box-shadow: inset 0 1px 0 rgba(255,255,255,.25);
            display: inline-flex; align-items: center; gap: 6px; text-decoration: none; white-space: nowrap;
        }
        .tbtn:hover { background: rgba(255,255,255,.28); }
        .tbtn:focus-visible, .btn:focus-visible, .chip:focus-visible, .fbtn:focus-visible, .tab:focus-visible { outline: 2px solid var(--brand-500); outline-offset: 2px; }
        .stamp { font-size: 11px; opacity: .85; white-space: nowrap; }

        /* ---------------- Tabs ---------------- */
        .tabs {
            display: flex; gap: 4px; padding: 8px 18px 0; overflow-x: auto;
            background: var(--surface); background: color-mix(in srgb, var(--surface) 88%, transparent); backdrop-filter: blur(10px); border-bottom: 1px solid var(--border);
        }
        .tab {
            flex: 0 0 auto; padding: 9px 14px 10px; border: 0; background: transparent; cursor: pointer;
            color: var(--ink-2); font-weight: 600; font-size: 13px; border-bottom: 3px solid transparent;
        }
        .tab:hover { color: var(--ink); }
        .tab { position: relative; border-radius: 10px 10px 0 0; }
        .tab[aria-selected="true"] { color: var(--ink); border-bottom-color: transparent; background: linear-gradient(180deg, transparent 0%, rgba(20,184,166,.10) 100%); }
        .tab[aria-selected="true"]::after { content: ""; position: absolute; left: 10px; right: 10px; bottom: -1px; height: 3px; border-radius: 3px 3px 0 0; background: var(--grad-accent); }
        .tab .n { margin-left: 6px; padding: 1px 7px; border-radius: 999px; background: var(--surface-2); font-size: 11px; font-variant-numeric: tabular-nums; }

        /* ---------------- Filter bar ---------------- */
        .filterbar {
            position: sticky; top: 52px; z-index: 40;
            display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
            padding: 10px 18px; background: var(--surface); background: color-mix(in srgb, var(--surface) 86%, transparent); backdrop-filter: blur(10px); border-bottom: 1px solid var(--border);
            box-shadow: 0 8px 20px -18px rgba(15,23,42,.45);
        }
        .fbtn {
            display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 10px;
            border-radius: 8px; border: 1px solid var(--border-strong); background: var(--surface); cursor: pointer;
            font-size: 12.5px; font-weight: 600; color: var(--ink-2);
        }
        .fbtn:hover { border-color: var(--brand-500); color: var(--ink); }
        .fbtn .caret { font-size: 9px; opacity: .7; }
        .fbtn.on { border-color: var(--brand-500); background: var(--brand-100); color: var(--ink); }
        .fbtn.on .cnt { font-weight: 700; }
        .qsearch { display: flex; align-items: center; gap: 6px; height: 30px; padding: 0 10px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--surface-2); min-width: 220px; }
        .qsearch input { border: 0; outline: 0; background: transparent; width: 100%; font-size: 12.5px; }
        .toggle { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--ink-2); cursor: pointer; user-select: none; }
        .toggle input { accent-color: var(--brand-500); }
        .fsep { width: 1px; height: 22px; background: var(--border-strong); }
        .chips { display: flex; flex-wrap: wrap; gap: 6px; width: 100%; }
        .chips:empty { display: none; }
        .chip {
            display: inline-flex; align-items: center; gap: 6px; padding: 3px 4px 3px 10px; border-radius: 999px;
            background: var(--brand-100); color: var(--ink); font-size: 12px; border: 0; cursor: pointer;
        }
        .chip b { font-weight: 700; }
        .chip .x { width: 18px; height: 18px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; background: rgba(0,0,0,.08); }
        .chip.clear { background: transparent; color: var(--ink-2); text-decoration: underline; padding-right: 10px; }

        /* ---------------- Layout ---------------- */
        main { padding: 16px 18px 40px; max-width: 1800px; margin: 0 auto; }
        .grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 14px; }
        .span-12 { grid-column: span 12; } .span-8 { grid-column: span 8; } .span-7 { grid-column: span 7; }
        .span-6 { grid-column: span 6; } .span-5 { grid-column: span 5; } .span-4 { grid-column: span 4; } .span-3 { grid-column: span 3; }
        @media (max-width: 1200px) { .span-3 { grid-column: span 6; } .span-4, .span-5, .span-7, .span-8 { grid-column: span 12; } .span-6 { grid-column: span 12; } }
        @media (max-width: 700px) { .grid > * { grid-column: span 12 !important; } .topbar { position: static; } .filterbar { position: static; } }

        .section-title { grid-column: span 12; display: flex; align-items: baseline; gap: 10px; margin: 6px 2px -4px; }
        .section-title h2 { font-size: 17px; }
        .setup { grid-column: span 12; padding: 22px 24px; border-radius: var(--radius); border: 1px dashed var(--border-strong); background: var(--surface); }
        .setup h3 { font-size: 15px; margin-bottom: 6px; } .setup p, .setup li { font-size: 13px; color: var(--ink-2); line-height: 1.55; } .setup ol { padding-left: 20px; margin-top: 6px; }
        .setup code { background: var(--surface-2); padding: 1px 6px; border-radius: 5px; font-size: 12px; }
        .views-list { max-height: 280px; overflow: auto; margin: 6px 12px; border: 1px solid var(--border); border-radius: 8px; }
        .views-list .vrow { display: flex; align-items: center; gap: 8px; padding: 7px 10px; border-bottom: 1px solid var(--border); font-size: 12.5px; }
        .views-list .vrow:last-child { border-bottom: 0; }
        .views-list .vrow .nm { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
        .views-list .vrow .meta { color: var(--muted); font-size: 11px; font-weight: 400; display: block; }
        .tag { display: inline-block; padding: 0 6px; border-radius: 999px; background: var(--brand-100); font-size: 10.5px; font-weight: 700; margin-left: 4px; }
        .acx { margin-top: 16px; border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
        .acx-h { display: flex; align-items: center; gap: 10px; padding: 10px 14px; background: linear-gradient(90deg, rgba(20,184,166,.12), rgba(42,120,214,.08)); font-weight: 700; font-size: 13px; }
        .acx-h a { margin-left: auto; }
        .acx .kv { padding: 10px 14px 4px; margin: 0; }
        .section-title p { color: var(--ink-2); font-size: 12.5px; }

        /* ---------------- KPI tiles ---------------- */
        .kpis { grid-column: span 12; display: grid; grid-template-columns: repeat(auto-fit, minmax(165px, 1fr)); gap: 12px; }
        .tile {
            position: relative; background: var(--grad-tile); border: 1px solid var(--border); border-radius: var(--radius);
            box-shadow: var(--shadow); padding: 16px 16px 13px; min-height: 108px; display: flex; flex-direction: column; gap: 4px;
            overflow: hidden; transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
        }
        .tile::before { content: ""; position: absolute; left: 0; right: 0; top: 0; height: 3px; background: var(--grad-accent); opacity: .9; }
        .tile:hover { transform: translateY(-2px); box-shadow: var(--shadow), var(--glow); }
        .tile .ico {
            position: absolute; right: 12px; top: 13px; width: 30px; height: 30px; border-radius: 9px;
            display: flex; align-items: center; justify-content: center; font-size: 14px;
            background: linear-gradient(135deg, rgba(20,184,166,.18), rgba(42,120,214,.16)); border: 1px solid rgba(20,184,166,.25);
        }
        .delta { font-weight: 700; font-variant-numeric: tabular-nums; }
        .delta.good { color: var(--good-text); } .delta.bad { color: var(--bad-text); } .delta.flat { color: var(--ink-2); }
        .tile.click { cursor: pointer; }
        .tile.click:hover { border-color: var(--brand-500); }
        .tile .lbl { font-size: 12px; color: var(--ink-2); font-weight: 600; }
        .tile .val { font-size: 28px; font-weight: 700; line-height: 1.1; letter-spacing: -.3px; }
        .tile .sub { font-size: 12px; color: var(--ink-2); }
        .tile .meter { height: 6px; border-radius: 999px; background: var(--q1); overflow: hidden; margin-top: 6px; }
        .tile .meter > span { display: block; height: 100%; border-radius: 999px; background: var(--s1); }
        .hero { grid-column: span 12; display: grid; grid-template-columns: minmax(260px, 1.1fr) 3fr; gap: 12px; }
        .hero .big {
            position: relative; overflow: hidden; background: var(--grad-hero); color: #fff; border-radius: var(--radius);
            box-shadow: 0 18px 40px -18px rgba(14,43,43,.65); padding: 20px 22px; display: flex; flex-direction: column; justify-content: center; gap: 7px;
        }
        .hero .big::before { content: ""; position: absolute; width: 360px; height: 360px; right: -140px; top: -170px; border-radius: 50%; background: radial-gradient(circle, rgba(255,255,255,.18), transparent 65%); }
        .hero .big::after { content: ""; position: absolute; width: 260px; height: 260px; left: -110px; bottom: -150px; border-radius: 50%; background: radial-gradient(circle, rgba(42,120,214,.35), transparent 65%); }
        .hero .big > * { position: relative; z-index: 1; }
        .hero .big .lbl { font-size: 13px; font-weight: 700; color: rgba(255,255,255,.85); letter-spacing: .2px; }
        .hero .big .num { font-size: 56px; font-weight: 750; line-height: 1; letter-spacing: -1px; }
        .hero .big .row { display: flex; gap: 14px; flex-wrap: wrap; align-items: center; font-size: 12.5px; color: rgba(255,255,255,.88); }
        .hero .big .row b { color: #fff; }
        .hero .big .status { color: #fff; background: rgba(255,255,255,.14); padding: 2px 8px 2px 6px; border-radius: 999px; }
        .hero .big .status::before { color: #fff !important; }
        .hero .big .delta { background: rgba(255,255,255,.95); padding: 1px 7px; border-radius: 999px; }
        .hero-flex { display: flex; align-items: center; gap: 18px; }
        .ring { flex: 0 0 auto; }
        .ring .trk { stroke: rgba(255,255,255,.18); }
        .ring .arc { stroke: #fff; stroke-linecap: round; }
        .ring .pln { stroke: #FACC15; stroke-width: 3; }
        .hero .kpis { grid-column: auto; grid-template-columns: repeat(3, minmax(0, 1fr)); }
        @media (max-width: 1100px) { .hero .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
        @media (max-width: 900px) { .hero { grid-template-columns: 1fr; } }

        .status { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; font-weight: 600; color: var(--ink); }
        .status i { width: 10px; height: 10px; border-radius: 50%; display: inline-block; font-style: normal; }
        .status.good i { background: var(--good); } .status.warning i { background: var(--warning); }
        .status.serious i { background: var(--serious); } .status.critical i { background: var(--critical); }
        .status.good::before { content: "\2714"; font-size: 11px; color: var(--good-text); }
        .status.warning::before, .status.serious::before { content: "\25B2"; font-size: 9px; color: var(--ink-2); }
        .status.critical::before { content: "\2716"; font-size: 10px; color: var(--critical); }

        /* ---------------- Cards ---------------- */
        .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); display: flex; flex-direction: column; min-width: 0; transition: box-shadow .2s ease; }
        .card:hover { box-shadow: var(--shadow), 0 14px 34px -22px rgba(15,23,42,.35); }
        .card-h h3 { display: flex; align-items: center; gap: 8px; }
        .card-h h3::before { content: ""; width: 4px; height: 16px; border-radius: 3px; background: var(--grad-accent); flex: 0 0 auto; }
        .card-h { display: flex; align-items: flex-start; gap: 10px; padding: 14px 16px 6px; }
        .card-h h3 { font-size: 14px; font-weight: 700; }
        .card-h p { font-size: 12px; color: var(--ink-2); margin-top: 2px; }
        .card-h .tools { margin-left: auto; display: flex; gap: 4px; flex: 0 0 auto; }
        .icon-btn { width: 28px; height: 28px; border-radius: 7px; border: 1px solid transparent; background: transparent; cursor: pointer; color: var(--ink-2); font-size: 13px; }
        .icon-btn:hover, .icon-btn[aria-pressed="true"] { border-color: var(--border-strong); background: var(--surface-2); color: var(--ink); }
        .card-b { padding: 6px 16px 14px; min-width: 0; position: relative; }
        .card-f { padding: 0 16px 12px; font-size: 11.5px; color: var(--muted); }
        .card.max { position: fixed; inset: 16px; z-index: 300; overflow: auto; }
        .backdrop { position: fixed; inset: 0; background: rgba(15,23,42,.45); z-index: 290; }
        .legend { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 12px; color: var(--ink-2); padding: 0 16px 4px; }
        .legend span { display: inline-flex; align-items: center; gap: 6px; }
        .legend i { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
        .legend i.tick { width: 2px; height: 14px; border-radius: 0; background: var(--ink); }
        .legend i.line { height: 2px; width: 16px; border-radius: 1px; }
        .empty { padding: 30px 10px; text-align: center; color: var(--muted); font-size: 13px; }
        .inline-ctl { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--ink-2); }
        .inline-ctl select, .inline-ctl input { height: 28px; border-radius: 7px; border: 1px solid var(--border-strong); background: var(--surface); padding: 0 6px; font-size: 12px; }
        .inline-ctl input[type=number] { width: 64px; }

        /* ---------------- SVG charts ---------------- */
        svg { display: block; overflow: visible; }
        svg text { fill: var(--ink-2); font-size: 11.5px; font-family: var(--font); }
        svg .lbl { fill: var(--ink); font-size: 12px; }
        svg .val { fill: var(--ink); font-size: 11.5px; font-weight: 600; font-variant-numeric: tabular-nums; }
        svg .tick { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
        svg .gridline { stroke: var(--grid); stroke-width: 1; }
        svg .axis { stroke: var(--axis); stroke-width: 1; }
        svg .marker { stroke: var(--ink); stroke-width: 2; stroke-linecap: round; }
        svg .today { stroke: var(--ink-2); stroke-width: 1; }
        svg .hit { fill: transparent; }
        svg .mark { cursor: default; outline: none; }
        svg .mark.clickable { cursor: pointer; }
        svg .mark:hover .hit, svg .mark:focus .hit { fill: var(--ink); fill-opacity: .04; }
        svg .dot { stroke: var(--surface); stroke-width: 2; }
        svg .cell-t { font-size: 11px; font-weight: 600; font-variant-numeric: tabular-nums; }

        /* ---------------- Tables ---------------- */
        .tbl-wrap { overflow: auto; max-height: 520px; border: 1px solid var(--border); border-radius: 10px; }
        .card.max .tbl-wrap { max-height: calc(100vh - 170px); }
        table.t { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 12.5px; }
        table.t th {
            position: sticky; top: 0; z-index: 1; background: var(--surface-2); color: var(--ink-2); text-align: left;
            font-weight: 700; font-size: 11.5px; padding: 8px 10px; border-bottom: 1px solid var(--border-strong); white-space: nowrap;
        }
        table.t td { padding: 7px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
        table.t td.num, table.t th.num { text-align: right; font-variant-numeric: tabular-nums; }
        table.t td.wrap { white-space: normal; min-width: 220px; }
        table.t tbody tr:hover td { background: var(--surface-2); }
        table.t tr.click { cursor: pointer; }
        table.t tfoot td { position: sticky; bottom: 0; background: var(--surface-2); font-weight: 700; border-top: 1px solid var(--border-strong); }
        th .th { display: inline-flex; align-items: center; gap: 4px; }
        th .sort { cursor: pointer; }
        th .th-f { border: 0; background: transparent; cursor: pointer; color: var(--muted); padding: 0 2px; border-radius: 4px; font-size: 10px; }
        th .th-f.on { color: var(--brand-700); background: var(--brand-100); }
        .pbar { display: inline-flex; align-items: center; gap: 8px; min-width: 120px; }
        .pbar .trk { flex: 1; height: 6px; background: var(--q1); border-radius: 999px; overflow: hidden; min-width: 60px; }
        .pbar .fil { height: 100%; background: var(--s1); border-radius: 999px; }
        .pbar b { font-weight: 600; font-variant-numeric: tabular-nums; min-width: 42px; text-align: right; }
        .pager { display: flex; align-items: center; gap: 8px; justify-content: flex-end; padding-top: 10px; font-size: 12px; color: var(--ink-2); }
        .btn { height: 30px; padding: 0 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--surface); cursor: pointer; font-size: 12.5px; font-weight: 600; }
        .btn:hover { border-color: var(--brand-500); }
        .btn.primary { background: linear-gradient(135deg, #0F766E, #14B8A6); border-color: #0F766E; color: #fff; box-shadow: 0 6px 14px -8px rgba(15,118,110,.8); }
        a.btn { display: inline-flex; align-items: center; text-decoration: none; }
        .btn:disabled { opacity: .45; cursor: default; }

        /* ---------------- Quality list ---------------- */
        .qc { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
        .qc-item { border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; background: var(--surface); cursor: pointer; display: flex; flex-direction: column; gap: 6px; }
        .qc-item:hover { border-color: var(--brand-500); }
        .qc-item .n { font-size: 24px; font-weight: 700; }
        .qc-item .d { font-size: 12px; color: var(--ink-2); }

        /* ---------------- Popover (Excel-style filter) ---------------- */
        .pop {
            position: fixed; z-index: 400; width: 320px; max-width: calc(100vw - 20px);
            background: var(--surface); border: 1px solid var(--border-strong); border-radius: 12px;
            box-shadow: 0 16px 40px rgba(0,0,0,.22); display: none; flex-direction: column;
        }
        .pop.open { display: flex; }
        .pop-h { padding: 10px 12px 6px; font-weight: 700; font-size: 13px; display: flex; align-items: center; }
        .pop-h .x { margin-left: auto; }
        .pop-row { padding: 4px 12px; display: flex; gap: 6px; align-items: center; }
        .pop-row button.linkish { border: 0; background: transparent; cursor: pointer; color: var(--ink-2); font-size: 12px; padding: 4px 6px; border-radius: 6px; }
        .pop-row button.linkish:hover { background: var(--surface-2); color: var(--ink); }
        .pop select, .pop input[type=text] { height: 30px; border-radius: 7px; border: 1px solid var(--border-strong); background: var(--surface); padding: 0 8px; font-size: 12.5px; }
        .pop input[type=text] { flex: 1; min-width: 0; }
        .pop-list { margin: 6px 12px; border: 1px solid var(--border); border-radius: 8px; max-height: 260px; overflow: auto; padding: 4px 0; }
        .pop-list label { display: flex; align-items: center; gap: 8px; padding: 4px 10px; font-size: 12.5px; cursor: pointer; }
        .pop-list label:hover { background: var(--surface-2); }
        .pop-list label .v { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .pop-list label .c { color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
        .pop-list .note { padding: 6px 10px; font-size: 11.5px; color: var(--muted); }
        .pop-list input { accent-color: var(--brand-700); }
        .pop-f { display: flex; gap: 8px; justify-content: flex-end; padding: 8px 12px 12px; }
        .pop-sub { font-size: 11px; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; padding: 6px 12px 0; }

        /* ---------------- Tooltip ---------------- */
        .tip {
            position: fixed; z-index: 500; pointer-events: none; display: none; max-width: 300px;
            background: var(--surface); color: var(--ink); border: 1px solid var(--border-strong); border-radius: 10px;
            box-shadow: 0 10px 28px rgba(0,0,0,.18); padding: 9px 11px; font-size: 12px; line-height: 1.45;
        }
        .tip b { font-weight: 700; }
        .tip .r { display: flex; align-items: center; gap: 8px; justify-content: space-between; }
        .tip .r span { display: inline-flex; align-items: center; gap: 6px; color: var(--ink-2); }
        .tip .r i { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
        .tip .r em { font-style: normal; font-weight: 700; font-variant-numeric: tabular-nums; }
        .tip .hint { color: var(--muted); font-size: 11px; margin-top: 4px; }

        /* ---------------- Drawer ---------------- */
        .drawer { position: fixed; top: 0; right: 0; bottom: 0; width: min(720px, 96vw); z-index: 350; background: var(--surface); box-shadow: -12px 0 32px rgba(0,0,0,.2); transform: translateX(105%); transition: transform .22s ease; display: flex; flex-direction: column; }
        .drawer.open { transform: none; }
        .drawer-h { padding: 16px 18px; background: linear-gradient(135deg, var(--brand-900), var(--brand-500)); color: #fff; display: flex; gap: 10px; }
        .drawer-h h3 { font-size: 16px; }
        .drawer-h p { font-size: 12px; opacity: .9; margin-top: 2px; }
        .drawer-b { padding: 14px 18px; overflow: auto; }
        .kv { display: grid; grid-template-columns: 150px 1fr; gap: 6px 12px; font-size: 12.5px; margin-bottom: 14px; }
        .kv dt { color: var(--ink-2); }
        .kv dd { font-weight: 600; }

        /* ---------------- Loading / banners ---------------- */
        .loading { position: fixed; inset: 0; z-index: 600; display: flex; align-items: center; justify-content: center; background: rgba(15,23,42,.35); }
        .loading[hidden] { display: none; }
        .loading .box { background: var(--surface); border-radius: 14px; padding: 22px 30px; text-align: center; box-shadow: var(--shadow); }
        .spinner { width: 40px; height: 40px; margin: 0 auto 10px; border-radius: 50%; border: 4px solid var(--grid); border-top-color: var(--brand-500); animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .stale { opacity: .55; transition: opacity .2s; }
        .banner { margin: 0 0 14px; padding: 10px 14px; border-radius: 10px; font-size: 13px; border-left: 4px solid var(--critical); background: var(--surface); }
        .banner.info { border-left-color: var(--brand-500); }
        .note-row { grid-column: span 12; font-size: 12px; color: var(--muted); }

        @media print {
            .topbar, .tabs, .filterbar .qsearch, .icon-btn, .pager, .fbtn .caret { display: none !important; }
            .filterbar { position: static; }
            html, body { background: #fff; }
            .card, .tile { box-shadow: none; break-inside: avoid; }
            .tbl-wrap { max-height: none; overflow: visible; }
        }
        @media (prefers-reduced-motion: reduce) { .drawer { transition: none; } .spinner { animation-duration: 3s; } }
    </style>
</head>
<body>
    <header class="topbar">
        <a class="brand" id="backLink" href="SmartDDRv3.aspx" title="Back to SmartDDR">
            <span class="brand-mark">S</span>
            <span><span class="brand-name">SmartDDR Dashboard</span><br /><span class="brand-sub">DDR + EPR · M75 AFC analytics <span id="stamp"></span></span></span>
        </a>
        <span class="top-spacer"></span>
        <label class="top-field">Project group
            <select id="selGroup"><option value="3">PDC-QA</option><option value="4">PDC-FLY</option></select>
        </label>
        <label class="top-field">Project
            <select id="selProject" class="wide"></select>
        </label>
        <label class="top-field" title="Status date used for overdue and look-ahead calculations">Data date
            <input type="date" id="asOf" />
        </label>
        <button type="button" class="tbtn" id="btnRefresh" title="Reload data from the database">⟳ Refresh</button>
        <button type="button" class="tbtn" id="btnViews" title="Saved views – save, share or apply a filter set" aria-haspopup="dialog">★ Views</button>
        <button type="button" class="tbtn" id="btnExport" title="Export this tab to Excel (.xlsx) – one sheet per chart/table">⬇ Excel</button>
        <button type="button" class="tbtn" id="btnPrint" title="Print / save as PDF">🖨</button>
        <button type="button" class="tbtn" id="btnTheme" title="Light / dark">◐</button>
    </header>

    <nav class="tabs" role="tablist" id="tabs"></nav>

    <section class="filterbar" aria-label="Filters">
        <label class="qsearch" title="Quick search in document number, title, PLIP and RAMZ ID">🔍<input id="qsearch" type="text" placeholder="Quick search…" autocomplete="off" /></label>
        <span id="filterBtns" style="display:contents"></span>
        <span class="fsep"></span>
        <label class="toggle" title="Rows whose document number contains ACTIVITY"><input type="checkbox" id="tglAct" /> Activities</label>
        <label class="toggle" title="Rows marked DELETED / CANCELLED / VOID"><input type="checkbox" id="tglCancel" /> Cancelled</label>
        <div class="chips" id="chips"></div>
    </section>

    <main id="view" aria-live="polite"></main>

    <div class="pop" id="pop" role="dialog" aria-modal="false"></div>
    <div class="tip" id="tip" role="tooltip"></div>
    <aside class="drawer" id="drawer" aria-hidden="true">
        <div class="drawer-h"><div style="flex:1;min-width:0"><h3 id="drawerTitle"></h3><p id="drawerSub"></p></div><button type="button" class="tbtn" id="drawerClose" title="Close (Esc)">✕</button></div>
        <div class="drawer-b" id="drawerBody"></div>
    </aside>
    <div class="loading" id="loading" hidden><div class="box"><div class="spinner"></div><div id="loadingText">Loading data…</div></div></div>

    <script>
    (function () {
        'use strict';

        /* =================================================================
           Configuration
           ================================================================= */
        var FINAL_STATUSES = ['AFC', 'AFX', 'ADH'];          // statuses that count as "complete"
        var CANCEL_RE = /DELETED|CANCELL?ED|\bVOID\b|SUPERSEDED/i;
        var ACTIVITY_RE = /ACTIVI/i;
        var DEFAULT_WEIGHTS = { IDC: 0.15, IFR: 0.35, RCC: 0.10, APP: 0.15, AFC: 0.25 };  // fallback rules of credit
        var STAGES = [
            { key: 'IDC', p: 'IDC_PLN', a: 'IDC_ACT', group: 'IDC' },
            { key: 'IFR', p: 'IFR_PLN', a: 'IFR1_ACT', group: 'IFR' },
            { key: 'RCC', p: 'RCC_PLN', a: 'RCC1_ACT', group: 'Review' },
            { key: 'IFR2', p: 'IFR2_PLN', a: 'IFR2_ACT', group: 'Review', optional: true },
            { key: 'RCC2', p: 'RCC2_PLN', a: 'RCC2_ACT', group: 'Review', optional: true },
            { key: 'APP', p: 'APP_PLN', a: 'APP_ACT', group: 'APP' },
            { key: 'AFC', p: 'AFC_PLN', a: 'AFC_ACT', group: 'AFC' }
        ];
        var STAGE_GROUPS = ['IDC', 'IFR', 'Review', 'APP', 'AFC'];   // 5 ordered groups -> 5-step validated ramp
        var ORD = ['var(--o1)', 'var(--o2)', 'var(--o3)', 'var(--o4)', 'var(--o5)'];
        var SEQ = ['var(--q1)', 'var(--q2)', 'var(--q3)', 'var(--q4)', 'var(--q5)', 'var(--q6)', 'var(--q7)'];
        var AGE_BUCKETS = [{ l: '1–14 d', max: 14 }, { l: '15–30 d', max: 30 }, { l: '31–60 d', max: 60 }, { l: '61–90 d', max: 90 }, { l: '> 90 d', max: Infinity }];

        var TABS = [
            { id: 'overview', label: 'Overview' },
            { id: 'pending', label: 'Pending' },
            { id: 'discipline', label: 'Discipline performance' },
            { id: 'overdue', label: 'Overdue' },
            { id: 'lookahead', label: 'Look-ahead' },
            { id: 'progress', label: 'Progress & S-curve' },
            { id: 'trend', label: 'Trend' },
            { id: 'baseline', label: 'Baseline' },
            { id: 'm75', label: 'M75 AFC' },
            { id: 'review', label: 'Review cycle' },
            { id: 'handover', label: 'Handover' },
            { id: 'quality', label: 'Data quality' },
            { id: 'register', label: 'Register' }
        ];

        // Excel-style filter fields (the first seven are the requested ones).
        var FIELDS = {
            disc: { label: 'Discipline', get: function (r) { return r.disc; } },
            doc: { label: 'Document No', get: function (r) { return r.doc; } },
            plip: { label: 'PLIP ID', get: function (r) { return r.plip; } },
            ramz: { label: 'RAMZ ID', get: function (r) { return r.ramz; } },
            title: { label: 'Document Title', get: function (r) { return r.title; } },
            reqd: { label: 'Status Required', get: function (r) { return r.reqd; } },
            status: { label: 'Status', get: function (r) { return r.status; } },
            proj: { label: 'Project', get: function (r) { return r.proj; } },
            crit: { label: 'Criticality', get: function (r) { return r.crit; } },
            next: { label: 'Next milestone', get: function (r) { return r.nextLabel; } },
            rev: { label: 'Revision', get: function (r) { return r.rev; } },
            sw: { label: 'Software', get: function (r) { return r.sw; } }
        };
        var BAR_FIELDS = ['disc', 'doc', 'plip', 'ramz', 'title', 'reqd', 'status', 'proj', 'crit', 'next'];

        /* =================================================================
           State
           ================================================================= */
        var S = {
            data: null, all: [], rows: [], m75: [], weights: null, weightsInferred: false,
            asOf: todayDay(), tab: 'overview', filters: {}, q: '', qc: null,
            inclAct: false, inclCancel: false, targetDays: 14, lookDays: 30, progStage: 'IFR',
            reg: { sort: 'nextDue', dir: 1, page: 0, size: 100 },
            scope: { group: '3', project: '' }
        };
        var QUALITY = [];   // defined further down

        /* =================================================================
           Utilities
           ================================================================= */
        function $(id) { return document.getElementById(id); }
        function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
        function todayDay() { var d = new Date(); return Math.floor(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / 864e5); }
        function parseDay(s) { if (!s) return null; var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s); return m ? Math.floor(Date.UTC(+m[1], +m[2] - 1, +m[3]) / 864e5) : null; }
        var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        function dayToDate(d) { return new Date(d * 864e5); }
        function fmtDay(d) { if (d == null) return ''; var t = dayToDate(d); return ('0' + t.getUTCDate()).slice(-2) + '-' + MON[t.getUTCMonth()] + '-' + String(t.getUTCFullYear()).slice(-2); }
        function isoDay(d) { return dayToDate(d).toISOString().slice(0, 10); }
        function monthKey(d) { var t = dayToDate(d); return t.getUTCFullYear() * 12 + t.getUTCMonth(); }
        function monthLabel(k) { return MON[k % 12] + ' ' + String(Math.floor(k / 12)).slice(-2); }
        function monthStart(k) { return Math.floor(Date.UTC(Math.floor(k / 12), k % 12, 1) / 864e5); }
        function weekStart(d) { var dow = (dayToDate(d).getUTCDay() + 6) % 7; return d - dow; }
        function fmtInt(n) { return (n == null || isNaN(n)) ? '–' : Math.round(n).toLocaleString('en-US'); }
        function fmt1(n) { return (n == null || isNaN(n)) ? '–' : (Math.round(n * 10) / 10).toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 }); }
        function fmtPct(n) { return (n == null || isNaN(n)) ? '–' : fmt1(n) + '%'; }
        function compact(n) { if (n == null || isNaN(n)) return '–'; var a = Math.abs(n); if (a >= 1e6) return fmt1(n / 1e6) + 'M'; if (a >= 1e4) return fmt1(n / 1e3) + 'K'; return fmtInt(n); }
        function pct(a, b) { return b > 0 ? a / b * 100 : null; }
        function up(s) { return (s || '').toString().trim().toUpperCase(); }
        function sum(arr, f) { var t = 0; for (var i = 0; i < arr.length; i++) { var v = f(arr[i]); if (v) t += v; } return t; }
        function groupBy(arr, f) { var m = new Map(); arr.forEach(function (r) { var k = f(r); if (k == null || k === '') k = '(Blank)'; if (!m.has(k)) m.set(k, []); m.get(k).push(r); }); return m; }
        function median(a) { if (!a.length) return null; var s = a.slice().sort(function (x, y) { return x - y; }); var h = s.length >> 1; return s.length % 2 ? s[h] : (s[h - 1] + s[h]) / 2; }
        function avg(a) { return a.length ? a.reduce(function (x, y) { return x + y; }, 0) / a.length : null; }
        // One shared collator: localeCompare(…, options) builds a new collator per call and was the
        // bottleneck when sorting ~50k document numbers.
        var COLL = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
        function discSort(a, b) {
            var na = parseInt(a, 10), nb = parseInt(b, 10);
            if (!isNaN(na) && !isNaN(nb) && na !== nb) return na - nb;
            if (!isNaN(na) && isNaN(nb)) return -1; if (isNaN(na) && !isNaN(nb)) return 1;
            return COLL.compare(String(a), String(b));
        }
        function store(k, v) { try { if (v === undefined) return JSON.parse(localStorage.getItem(k)); localStorage.setItem(k, JSON.stringify(v)); } catch (e) { return null; } return null; }
        function statusOf(level, text) { return '<span class="status ' + level + '"><i></i>' + esc(text) + '</span>'; }
        function spiStatus(spi) {
            if (spi == null) return statusOf('warning', 'n/a');
            if (spi >= 0.95) return statusOf('good', 'On track');
            if (spi >= 0.85) return statusOf('warning', 'Watch');
            if (spi >= 0.7) return statusOf('serious', 'Behind');
            return statusOf('critical', 'Critical');
        }

        /* =================================================================
           Data loading and normalisation
           ================================================================= */
        function parseResp(r) {
            return r.text().then(function (t) {
                var j, err;
                try { j = JSON.parse(t); } catch (e) { err = new Error('the server returned a page instead of data (HTTP ' + r.status + '). Your session may have expired – reload the page.'); err.status = r.status; throw err; }
                if (!r.ok || (j && j.error)) { err = new Error((j && j.error) || ('HTTP ' + r.status)); err.status = r.status; throw err; }
                return j;
            });
        }
        function qstr(params) { return Object.keys(params).map(function (k) { return k + '=' + encodeURIComponent(params[k]); }).join('&'); }
        function api(params) {
            return fetch('SmartDDRDashboard.aspx?' + qstr(params), { credentials: 'same-origin', cache: 'no-store' }).then(parseResp);
        }
        function apiPost(params, form) {
            return fetch('SmartDDRDashboard.aspx?' + qstr(params), {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: { 'X-SDDR': '1', 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
                body: new URLSearchParams(form || {}).toString()
            }).then(parseResp);
        }

        function loadProjects(selectValue) {
            return api({ api: 'projects', group: S.scope.group }).then(function (list) {
                var sel = $('selProject');
                sel.innerHTML = '<option value="*">All projects in group</option>' + list.map(function (p) {
                    return '<option value="' + esc(p.no) + '">' + esc(p.no + (p.title ? ' · ' + p.title : '')) + '</option>';
                }).join('');
                var want = selectValue && list.some(function (p) { return p.no === selectValue; }) ? selectValue : (list[0] ? list[0].no : '*');
                sel.value = want;
                S.scope.project = want === '*' ? '' : want;
            });
        }

        var loadSeq = 0;
        function loadData() {
            var seq = ++loadSeq;   // a slower, older response must not overwrite a newer selection
            var first = !S.data;
            if (first) { $('loading').hidden = false; } else { $('view').classList.add('stale'); }
            var params = S.scope.project ? { api: 'data', project: S.scope.project } : { api: 'data', group: S.scope.group };
            $('loadingText').textContent = S.scope.project ? 'Loading ' + S.scope.project + '…' : 'Loading all projects in the group…';
            return api(params).then(function (d) {
                if (seq !== loadSeq) return;
                S.data = d;
                normalise(d);
                restoreFilters();
                if (S.pendingView) { assignState(S.pendingView); S.pendingView = null; }
                derive();
                render();
                $('stamp').textContent = 'Data as of ' + d.generated + (d.truncated ? ' (truncated)' : '');
                var back = 'SmartDDRv3.aspx' + (S.scope.project ? '?project=' + encodeURIComponent(S.scope.project) : '');
                $('backLink').href = back;
                history.replaceState(null, '', '?group=' + encodeURIComponent(S.scope.group) + (S.scope.project ? '&project=' + encodeURIComponent(S.scope.project) : '') + '#' + S.tab);
            }).catch(function (e) {
                $('view').innerHTML = '<div class="banner">Could not load data: ' + esc(e.message) + '</div>';
            }).then(function () { if (seq === loadSeq) { $('loading').hidden = true; $('view').classList.remove('stale'); } });
        }

        function normalise(d) {
            var idx = {}; d.cols.forEach(function (c, i) { idx[c] = i; });
            function g(row, c) { var i = idx[c]; return i == null ? null : row[i]; }
            function num(v) { return (typeof v === 'number') ? v : (v == null ? null : (isNaN(parseFloat(v)) ? null : parseFloat(v))); }

            // Percentages / weights may be stored as fractions (0..1) or percents (0..100).
            function scaleOf(col) { var mx = 0; d.rows.forEach(function (r) { var v = num(g(r, col)); if (v != null && Math.abs(v) > mx) mx = Math.abs(v); }); return mx <= 1.0001 ? 100 : 1; }
            var sPln = scaleOf('PLN%'), sAct = scaleOf('ACT%');

            S.all = d.rows.map(function (r, i) {
                var o = {
                    i: i, id: g(r, 'DDR_ID'), proj: g(r, 'Project_No'), disc: g(r, 'Discipline'), doc: g(r, 'Document_No'),
                    ramz: g(r, 'RAMZ_ID'), plip: g(r, 'PLIP_ID'), dcaf: g(r, 'DCAF_NO'), sw: g(r, 'SOFTWARE'), crit: g(r, 'CRITICALITY'),
                    title: g(r, 'Document_Title'), prim: g(r, 'PRIMAVERA_ID'), rev: g(r, 'Curr_Rev'), docStatus: g(r, 'Doc_Status'),
                    reqd: g(r, 'STATUS_REQD'), status: g(r, 'CURR_STATUS'), remarks: g(r, 'REMARKS'),
                    hrs: num(g(r, 'Hours')) || 0, earned: num(g(r, 'Used Hours')) || 0,
                    plnPct: num(g(r, 'PLN%')) == null ? null : num(g(r, 'PLN%')) * sPln,
                    actPct: num(g(r, 'ACT%')) == null ? null : num(g(r, 'ACT%')) * sAct,
                    w: { START: num(g(r, 'START')), IDC: num(g(r, 'IDC')), IFR: num(g(r, 'IFR')), RCC: num(g(r, 'RCC')), APP: num(g(r, 'APP')), AFC: num(g(r, 'AFC')) },
                    ms: STAGES.map(function (s) { return { p: parseDay(g(r, s.p)), a: parseDay(g(r, s.a)) }; })
                };
                o.isAct = ACTIVITY_RE.test(o.doc || '');
                o.isCancel = CANCEL_RE.test((o.status || '') + ' ' + (o.remarks || '') + ' ' + (o.docStatus || ''));
                o.search = [o.doc, o.title, o.plip, o.ramz, o.prim].join(' ').toLowerCase();
                return o;
            });

            // Rules of credit: infer each stage's weight from the EPR columns (its maximum earned fraction).
            var raw = { START: 0, IDC: 0, IFR: 0, RCC: 0, APP: 0, AFC: 0 };
            S.all.forEach(function (o) { for (var k in raw) { var v = o.w[k]; if (v != null && v > raw[k]) raw[k] = v; } });
            var big = Object.keys(raw).some(function (k) { return raw[k] > 1.0001; });
            if (big) for (var k2 in raw) raw[k2] /= 100;
            var w = { IDC: raw.START + raw.IDC, IFR: raw.IFR, RCC: raw.RCC, APP: raw.APP, AFC: raw.AFC };
            var tot = w.IDC + w.IFR + w.RCC + w.APP + w.AFC;
            S.weightsInferred = tot > 0.2;
            if (!S.weightsInferred) { w = Object.assign({}, DEFAULT_WEIGHTS); tot = 1; }
            for (var k3 in w) w[k3] = w[k3] / tot;
            S.weights = w;

            S.hasEarned = S.all.some(function (o) { return o.earned > 0; });
            S.hasPln = S.all.some(function (o) { return o.plnPct != null; });

            // Duplicate document numbers (within a project) for the data-quality page.
            var seen = new Map();
            S.all.forEach(function (o) { var k = (o.proj || '') + '|' + up(o.doc); seen.set(k, (seen.get(k) || 0) + 1); });
            S.all.forEach(function (o) { o.dup = !!o.doc && seen.get((o.proj || '') + '|' + up(o.doc)) > 1; });

            // M75 (aggregated by discipline in the database view).
            var mi = {}; (d.m75cols || []).forEach(function (c, i) { mi[c] = i; });
            S.m75 = (d.m75rows || []).map(function (r) {
                return { proj: r[mi.Project_No], disc: r[mi.Discipline], plan: num(r[mi.AFC_PLN_TOTAL]) || 0, afcx: num(r[mi.AFCX_COUNT]) || 0, adh: num(r[mi.ADH_COUNT]) || 0 };
            });

            // Who is looking + optional history features.
            S.user = d.user || ''; S.isAdmin = !!d.admin; S.aconexLink = !!d.aconexLink; S.history = !!d.history;
            S.snaps = [];
            if (d.history) {
                var sx = {}; (d.snapcols || []).forEach(function (c, i) { sx[c] = i; });
                S.snaps = (d.snaprows || []).map(function (r) {
                    return {
                        d: parseDay(r[sx.SnapDate]), disc: r[sx.Discipline], docs: num(r[sx.Docs]) || 0, complete: num(r[sx.Complete]) || 0,
                        pending: num(r[sx.Pending]) || 0, overdue: num(r[sx.Overdue]) || 0, due14: num(r[sx.Due14]) || 0, notStarted: num(r[sx.NotStarted]) || 0,
                        est: num(r[sx.EstHours]) || 0, earned: num(r[sx.EarnedHours]) || 0, planned: num(r[sx.PlannedHours]) || 0,
                        m75p: num(r[sx.M75Plan]) || 0, m75d: num(r[sx.M75Done]) || 0
                    };
                }).filter(function (x) { return x.d != null; });
            }
            S.baselines = d.baselines || [];
            setBaseline(d.baseline || '', d.blcols || [], d.blrows || []);
        }

        // Attach a baseline's planned dates to each row (by DDR_ID, else project + document number)
        // and compute the plan drift (current plan - baseline plan, days) per milestone.
        function setBaseline(name, cols, rows) {
            var bi = {}; cols.forEach(function (c, i) { bi[c] = i; });
            var byId = new Map(), byDoc = new Map();
            rows.forEach(function (r) {
                var dates = STAGES.map(function (st) { return parseDay(r[bi[st.p]]); });
                if (r[bi.DDR_ID] != null && r[bi.DDR_ID] !== '') byId.set(String(r[bi.DDR_ID]), dates);
                if (r[bi.Document_No]) byDoc.set((r[bi.Project_No] || '') + '|' + up(r[bi.Document_No]), dates);
            });
            S.blName = name; S.blCount = rows.length; var matched = 0;
            S.all.forEach(function (o) {
                var b = (o.id != null && byId.get(String(o.id))) || byDoc.get((o.proj || '') + '|' + up(o.doc)) || null;
                o.bl = b; o.drift = null; o.docDrift = null;
                if (b) {
                    matched++;
                    o.drift = o.ms.map(function (m, i) { return (m.p != null && b[i] != null) ? m.p - b[i] : null; });
                    for (var i = 6; i >= 0; i--) if (o.drift[i] != null) { o.docDrift = o.drift[i]; break; }
                }
            });
            S.blMatched = matched;
            regCache.rows = null;
        }

        // Everything that depends on the data date.
        function derive() {
            var today = todayDay();
            // A data date in the past gives the position as it was then: later actuals don't count yet,
            // and the (current) status fields are only trusted when the data date is today or later.
            var live = S.asOf >= today;
            S.all.forEach(function (o) {
                var st = up(o.status), rq = up(o.reqd);
                o.ms.forEach(function (m) { m.x = (m.a != null && m.a <= S.asOf) ? m.a : null; });
                var last = -1;
                o.ms.forEach(function (m, i) { if (m.x != null) last = i; });
                o.lastIdx = last;
                o.complete = o.ms[6].x != null || (live && (FINAL_STATUSES.indexOf(st) >= 0 || (rq !== '' && st === rq)));
                o.ready = rq !== '' && ((live && st === rq) || (rq === 'AFC' && o.ms[6].x != null));
                o.next = null; o.nextDue = null; o.nextLabelFallback = null; o.nextLabel = o.complete ? 'Complete' : 'Unplanned';
                if (!o.complete) {
                    for (var i = last + 1; i < STAGES.length; i++) {
                        if (o.ms[i].p != null) { o.next = i; o.nextDue = o.ms[i].p; o.nextLabel = STAGES[i].key; break; }
                        if (!STAGES[i].optional && o.next == null && o.nextLabelFallback == null) o.nextLabelFallback = STAGES[i].key;
                    }
                    if (o.next == null && o.nextLabelFallback) o.nextLabel = o.nextLabelFallback + ' (unplanned)';
                }
                o.nextGroup = o.next != null ? STAGES[o.next].group : null;
                o.overdue = !o.complete && o.nextDue != null && o.nextDue < S.asOf;
                o.daysLate = o.overdue ? S.asOf - o.nextDue : 0;
                o.dueIn = (!o.complete && o.nextDue != null) ? o.nextDue - S.asOf : null;
                o.notStarted = !o.complete && last < 0;
                o.stageLabel = o.complete ? 'Complete' : (last < 0 ? 'Not started' : STAGES[last].key);

                // Review cycle metrics
                var ifr1 = o.ms[1].x, rcc1 = o.ms[2].x, ifr2 = o.ms[3].x, rcc2 = o.ms[4].x, app = o.ms[5].x, afc = o.ms[6].x;
                o.rev1 = (ifr1 != null && rcc1 != null && rcc1 >= ifr1) ? rcc1 - ifr1 : null;
                o.reissue = (rcc1 != null && ifr2 != null && ifr2 >= rcc1) ? ifr2 - rcc1 : null;
                o.rev2 = (ifr2 != null && rcc2 != null && rcc2 >= ifr2) ? rcc2 - ifr2 : null;
                o.withClient = null; o.withUs = null;
                if (!o.complete && app == null && afc == null) {
                    if (ifr2 != null && rcc2 == null) o.withClient = S.asOf - ifr2;
                    else if (ifr1 != null && rcc1 == null) o.withClient = S.asOf - ifr1;
                    else if (rcc1 != null && ifr2 == null) o.withUs = S.asOf - rcc1;
                }
                // Data-quality flags
                o.futureAct = o.ms.some(function (m) { return m.a != null && m.a > today; });
                o.actNoPlan = o.ms.some(function (m) { return m.a != null && m.p == null; });
                o.noPlan = !o.complete && o.ms.every(function (m) { return m.p == null; });
                var prev = null, seq = false;
                o.ms.forEach(function (m) { if (m.a != null) { if (prev != null && m.a < prev) seq = true; prev = m.a; } });
                o.outOfSeq = seq;
            });
            applyFilters();
        }

        /* =================================================================
           Filtering (Excel-style value / text filters, cascading)
           ================================================================= */
        function baseRows() {
            return S.all.filter(function (r) { return (S.inclAct || !r.isAct) && (S.inclCancel || !r.isCancel); });
        }
        function passField(r, key, f) {
            var v = FIELDS[key].get(r); v = (v == null || v === '') ? '' : String(v);
            if (f.vals && !f.vals.has(v)) return false;
            if (f.q) {
                var a = v.trim().toLowerCase(), q = f.q.trim().toLowerCase();
                switch (f.op) {
                    case 'eq': if (a !== q) return false; break;
                    case 'ne': if (a === q) return false; break;
                    case 'begins': if (a.indexOf(q) !== 0) return false; break;
                    case 'ends': if (a.slice(-q.length) !== q) return false; break;
                    case 'not': if (a.indexOf(q) >= 0) return false; break;
                    default: if (a.indexOf(q) < 0) return false;
                }
            }
            return true;
        }
        function pass(r, except) {
            for (var k in S.filters) { if (k !== except && !passField(r, k, S.filters[k])) return false; }
            if (S.q && r.search.indexOf(S.q) < 0) return false;
            if (S.qc && except !== '__qc') { var chk = QUALITY.find(function (c) { return c.id === S.qc; }); if (chk && !chk.test(r)) return false; }
            return true;
        }
        function applyFilters() {
            var base = baseRows();
            S.rows = base.filter(function (r) { return pass(r); });
            saveFilters();
        }
        function rowsExcept(key) { return baseRows().filter(function (r) { return pass(r, key); }); }
        function setValueFilter(key, value) {
            var cur = S.filters[key];
            var v = value == null ? '' : String(value);
            if (cur && cur.vals && cur.vals.size === 1 && cur.vals.has(v) && !cur.q) delete S.filters[key];
            else S.filters[key] = { vals: new Set([v]) };
            S.reg.page = 0; applyFilters(); render();
        }
        function filterKey() { return 'sddr.dash.f.' + (S.scope.project || ('G' + S.scope.group)); }
        function saveFilters() {
            var o = {}; for (var k in S.filters) { var f = S.filters[k]; o[k] = { vals: f.vals ? Array.from(f.vals) : null, op: f.op, q: f.q }; }
            store(filterKey(), { f: o, q: S.q, qc: S.qc });
        }
        function restoreFilters() {
            S.filters = {}; S.q = ''; S.qc = null;
            var saved = store(filterKey());
            if (saved && saved.f) for (var k in saved.f) { if (FIELDS[k]) { var f = saved.f[k]; S.filters[k] = { vals: f.vals ? new Set(f.vals) : null, op: f.op, q: f.q }; } }
            if (saved) { S.q = saved.q || ''; S.qc = saved.qc || null; }
            $('qsearch').value = S.q;
        }

        function renderFilterBar() {
            var group = !S.scope.project;
            $('filterBtns').innerHTML = BAR_FIELDS.filter(function (k) { return k !== 'proj' || group; }).map(function (k) {
                var f = S.filters[k], on = !!f, n = f && f.vals ? f.vals.size : 0;
                var txt = on ? (f.q ? '“' + esc(f.q) + '”' : '<span class="cnt">' + n + '</span> sel.') : 'All';
                return '<button type="button" class="fbtn' + (on ? ' on' : '') + '" data-f="' + k + '" aria-haspopup="dialog">' + esc(FIELDS[k].label) + ': ' + txt + ' <span class="caret">▼</span></button>';
            }).join('');
            var chips = [];
            Object.keys(S.filters).forEach(function (k) {
                var f = S.filters[k];
                var desc = f.q ? (opLabel(f.op) + ' “' + f.q + '”') : (f.vals.size <= 3 ? Array.from(f.vals).map(function (v) { return v === '' ? '(Blanks)' : v; }).join(', ') : f.vals.size + ' values');
                chips.push('<button type="button" class="chip" data-clear="' + k + '" title="Remove filter"><b>' + esc(FIELDS[k].label) + ':</b> ' + esc(desc) + '<span class="x">✕</span></button>');
            });
            if (S.q) chips.push('<button type="button" class="chip" data-clear="__q"><b>Search:</b> ' + esc(S.q) + '<span class="x">✕</span></button>');
            if (S.qc) { var c = QUALITY.find(function (x) { return x.id === S.qc; }); chips.push('<button type="button" class="chip" data-clear="__qc"><b>Check:</b> ' + esc(c ? c.label : S.qc) + '<span class="x">✕</span></button>'); }
            if (chips.length > 1) chips.push('<button type="button" class="chip clear" data-clear="__all">Clear all</button>');
            $('chips').innerHTML = chips.join('');
        }
        function opLabel(op) { return { eq: 'equals', ne: 'does not equal', begins: 'begins with', ends: 'ends with', not: 'does not contain' }[op] || 'contains'; }

        /* ---------------- Excel-style popover ---------------- */
        var popState = null;
        function openFilter(key, anchor) {
            var rows = rowsExcept(key);
            var counts = new Map();
            rows.forEach(function (r) { var v = FIELDS[key].get(r); v = (v == null || v === '') ? '' : String(v); counts.set(v, (counts.get(v) || 0) + 1); });
            var cur = S.filters[key];
            if (cur && cur.vals) cur.vals.forEach(function (v) { if (!counts.has(v)) counts.set(v, 0); });
            var values = Array.from(counts.keys()).sort(key === 'disc' ? discSort : function (a, b) { return COLL.compare(a, b); });
            popState = { key: key, values: values, counts: counts, sel: new Set(cur && cur.vals ? cur.vals : values), search: '', dir: 1 };
            var p = $('pop');
            p.innerHTML =
                '<div class="pop-h">' + esc(FIELDS[key].label) + '<button type="button" class="icon-btn x" data-pop="close" title="Close">✕</button></div>' +
                '<div class="pop-row"><button type="button" class="linkish" data-pop="az">↑ Sort A→Z</button><button type="button" class="linkish" data-pop="za">↓ Sort Z→A</button><button type="button" class="linkish" data-pop="clear" style="margin-left:auto">Clear filter</button></div>' +
                '<div class="pop-sub">Text filter</div>' +
                '<div class="pop-row"><select id="popOp"><option value="contains">Contains</option><option value="not">Does not contain</option><option value="begins">Begins with</option><option value="ends">Ends with</option><option value="eq">Equals</option><option value="ne">Does not equal</option></select><input type="text" id="popText" placeholder="Text…" /></div>' +
                '<div class="pop-sub">Values</div>' +
                '<div class="pop-row"><input type="text" id="popSearch" placeholder="Search values…" autocomplete="off" /></div>' +
                '<div class="pop-list" id="popList"></div>' +
                '<div class="pop-f"><button type="button" class="btn" data-pop="close">Cancel</button><button type="button" class="btn primary" data-pop="ok">OK</button></div>';
            if (cur && cur.q) { $('popOp').value = cur.op || 'contains'; $('popText').value = cur.q; }
            drawPopList();
            p.classList.add('open');
            var rc = anchor.getBoundingClientRect();
            var left = Math.min(rc.left, window.innerWidth - p.offsetWidth - 10);
            var top = rc.bottom + 6;
            if (top + p.offsetHeight > window.innerHeight - 10) top = Math.max(10, rc.top - p.offsetHeight - 6);
            p.style.left = Math.max(10, left) + 'px'; p.style.top = top + 'px';
            $('popSearch').focus();
        }
        function drawPopList() {
            var st = popState, q = st.search.toLowerCase();
            var vis = st.values.filter(function (v) { return !q || (v || '(blanks)').toLowerCase().indexOf(q) >= 0; });
            if (st.dir < 0) vis = vis.slice().reverse();
            var LIMIT = 800;
            var allOn = vis.length > 0 && vis.every(function (v) { return st.sel.has(v); });
            var html = '<label><input type="checkbox" data-all="1"' + (allOn ? ' checked' : '') + ' /><span class="v"><b>(Select all' + (q ? ' search results' : '') + ')</b></span><span class="c">' + vis.length + '</span></label>';
            vis.slice(0, LIMIT).forEach(function (v) {
                html += '<label><input type="checkbox" data-v="' + esc(v) + '"' + (st.sel.has(v) ? ' checked' : '') + ' /><span class="v" title="' + esc(v) + '">' + (v === '' ? '<i>(Blanks)</i>' : esc(v)) + '</span><span class="c">' + fmtInt(st.counts.get(v)) + '</span></label>';
            });
            if (vis.length > LIMIT) html += '<div class="note">Showing ' + LIMIT + ' of ' + fmtInt(vis.length) + ' – type to narrow the list.</div>';
            if (!vis.length) html += '<div class="note">No matching values.</div>';
            $('popList').innerHTML = html;
        }
        function closePop() { $('pop').classList.remove('open'); $('pop').style.width = ''; popState = null; }
        function commitPop() {
            var st = popState, key = st.key;
            var text = $('popText').value.trim(), op = $('popOp').value;
            var sel = st.sel;
            if (st.search) {   // Excel: OK after a search keeps only the ticked search results
                var q = st.search.toLowerCase();
                sel = new Set(Array.from(sel).filter(function (v) { return (v || '(blanks)').toLowerCase().indexOf(q) >= 0; }));
            }
            var allValues = st.values.every(function (v) { return sel.has(v); });
            var f = {};
            if (!allValues) f.vals = sel;
            if (text) { f.q = text; f.op = op; }
            if (f.vals || f.q) S.filters[key] = f; else delete S.filters[key];
            closePop(); S.reg.page = 0; applyFilters(); render();
        }
        $('pop').addEventListener('click', function (e) {
            var b = e.target.closest('[data-pop]');
            if (b) {
                var a = b.getAttribute('data-pop');
                if (a === 'close') closePop();
                else if (a === 'ok') commitPop();
                else if (a === 'clear') { delete S.filters[popState.key]; closePop(); applyFilters(); render(); }
                else if (a === 'az' || a === 'za') {
                    popState.dir = a === 'az' ? 1 : -1; drawPopList();
                    if (S.tab === 'register' && REG_COLS.some(function (c) { return c.f === popState.key; })) {
                        var col = REG_COLS.find(function (c) { return c.f === popState.key; });
                        S.reg.sort = col.k; S.reg.dir = popState.dir; render();
                    }
                }
                return;
            }
        });
        $('pop').addEventListener('change', function (e) {
            var t = e.target; if (!popState || popState.key === '__views' || t.type !== 'checkbox') return;
            var q = popState.search.toLowerCase();
            if (t.hasAttribute('data-all')) {
                popState.values.forEach(function (v) { if (!q || (v || '(blanks)').toLowerCase().indexOf(q) >= 0) { if (t.checked) popState.sel.add(v); else popState.sel.delete(v); } });
                drawPopList();
            } else {
                var v = t.getAttribute('data-v'); if (t.checked) popState.sel.add(v); else popState.sel.delete(v);
            }
        });
        $('pop').addEventListener('input', function (e) { if (popState && popState.key !== '__views' && e.target.id === 'popSearch') { popState.search = e.target.value; drawPopList(); } });
        $('pop').addEventListener('keydown', function (e) {
            if (e.key !== 'Enter' || e.target.closest('button')) return;
            e.preventDefault();
            if (popState && popState.key === '__views') { if (e.target.id === 'viewName') viewsAction('save'); } else commitPop();
        });

        /* =================================================================
           Tooltip
           ================================================================= */
        var tip = $('tip');
        function showTip(html, ev) {
            tip.innerHTML = html; tip.style.display = 'block';
            var x, y;
            if (ev && ev.clientX != null && ev.type !== 'focus') { x = ev.clientX + 14; y = ev.clientY + 14; }
            else { var rc = ev.target.getBoundingClientRect(); x = rc.right + 8; y = rc.top; }
            if (x + tip.offsetWidth > window.innerWidth - 8) x = Math.max(8, x - tip.offsetWidth - 28);
            if (y + tip.offsetHeight > window.innerHeight - 8) y = Math.max(8, y - tip.offsetHeight - 28);
            tip.style.left = x + 'px'; tip.style.top = y + 'px';
        }
        function hideTip() { tip.style.display = 'none'; }
        function tipRows(title, rows, hint) {
            return '<b>' + esc(title) + '</b>' + rows.map(function (r) {
                return '<div class="r"><span>' + (r.c ? '<i style="background:' + r.c + '"></i>' : '') + esc(r.n) + '</span><em>' + esc(r.v) + '</em></div>';
            }).join('') + (hint ? '<div class="hint">' + esc(hint) + '</div>' : '');
        }
        function bindHover(g, htmlFn, onClick) {
            g.addEventListener('mousemove', function (e) { showTip(htmlFn(), e); });
            g.addEventListener('mouseleave', hideTip);
            g.addEventListener('focus', function (e) { showTip(htmlFn(), e); });
            g.addEventListener('blur', hideTip);
            if (onClick) {
                g.classList.add('clickable');
                g.addEventListener('click', function () { hideTip(); onClick(); });
                g.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); hideTip(); onClick(); } });
            }
        }

        /* =================================================================
           SVG chart kit
           ================================================================= */
        var NS = 'http://www.w3.org/2000/svg';
        function node(tag, attrs, parent) { var e = document.createElementNS(NS, tag); for (var k in attrs) if (attrs[k] != null) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; }
        function svgEl(w, h, label) { var s = node('svg', { width: w, height: h, viewBox: '0 0 ' + w + ' ' + h, role: 'img', 'aria-label': label || '' }); return s; }
        function txt(parent, x, y, s, cls, anchor, base) { var t = node('text', { x: x, y: y, 'class': cls || null, 'text-anchor': anchor || 'start', 'dominant-baseline': base || 'middle' }, parent); t.textContent = s; return t; }
        function hBarPath(x, y, w, h) { var r = Math.min(4, w, h / 2); return 'M' + x + ',' + y + 'H' + (x + w - r) + 'Q' + (x + w) + ',' + y + ' ' + (x + w) + ',' + (y + r) + 'V' + (y + h - r) + 'Q' + (x + w) + ',' + (y + h) + ' ' + (x + w - r) + ',' + (y + h) + 'H' + x + 'Z'; }
        function vBarPath(x, y, w, h) { var r = Math.min(4, h, w / 2); return 'M' + x + ',' + (y + h) + 'V' + (y + r) + 'Q' + x + ',' + y + ' ' + (x + r) + ',' + y + 'H' + (x + w - r) + 'Q' + (x + w) + ',' + y + ' ' + (x + w) + ',' + (y + r) + 'V' + (y + h) + 'Z'; }
        function hBarPathLeft(x, y, w, h) { var r = Math.min(4, w, h / 2); return 'M' + (x + w) + ',' + y + 'H' + (x + r) + 'Q' + x + ',' + y + ' ' + x + ',' + (y + r) + 'V' + (y + h - r) + 'Q' + x + ',' + (y + h) + ' ' + (x + r) + ',' + (y + h) + 'H' + (x + w) + 'Z'; }
        function rectPath(x, y, w, h) { return 'M' + x + ',' + y + 'h' + w + 'v' + h + 'h' + (-w) + 'Z'; }
        function niceTicks(max, n) {
            if (!(max > 0)) max = 1;
            var raw = max / (n || 4), mag = Math.pow(10, Math.floor(Math.log10(raw))), step = [1, 2, 2.5, 5, 10].map(function (m) { return m * mag; }).find(function (s) { return s >= raw; });
            var top = Math.ceil(max / step) * step, t = [];
            for (var v = 0; v <= top + 1e-9; v += step) t.push(+v.toFixed(6));
            return t;
        }
        // Drawable width of a chart host (clientWidth includes the card's padding).
        function innerW(el) { var cs = getComputedStyle(el); return el.clientWidth - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0); }
        function measure(s, size) { return String(s).length * (size || 12) * 0.56; }
        function trunc(s, px) { s = String(s == null || s === '' ? '(Blank)' : s); var max = Math.max(4, Math.floor(px / 6.7)); return s.length > max ? s.slice(0, max - 1) + '…' : s; }
        function emptyChart(host, msg) { host.innerHTML = '<div class="empty">' + esc(msg || 'No data for the current filters.') + '</div>'; }

        // Horizontal bars. items: [{label, segs:[{name,value,color}], value?, marker?, onClick?, tip?}]
        function barH(host, items, o) {
            o = o || {}; host.innerHTML = '';
            if (!items.length) return emptyChart(host);
            var W = Math.max(300, innerW(host)), rowH = o.rowH || 28, thick = Math.min(o.thick || 14, 24);
            var labelW = o.labelW || Math.min(190, Math.max(80, W * 0.3)), valW = o.valW || 70, plotW = Math.max(60, W - labelW - valW - 10);
            var H = items.length * rowH + 22;
            var max = o.max || Math.max.apply(null, items.map(function (it) { var t = it.segs ? sum(it.segs, function (s) { return s.value; }) : (it.value || 0); return Math.max(t, it.marker || 0); }).concat([o.minMax || 1]));
            var ticks = niceTicks(max, 4); max = ticks[ticks.length - 1];
            var s = svgEl(W, H, o.label);
            ticks.forEach(function (t) { var x = labelW + plotW * t / max; node('line', { x1: x, x2: x, y1: 0, y2: H - 18, 'class': t === 0 ? 'axis' : 'gridline' }, s); txt(s, x, H - 8, o.tickFmt ? o.tickFmt(t) : compact(t), 'tick', 'middle'); });
            items.forEach(function (it, i) {
                var g = node('g', { 'class': 'mark', tabindex: 0 }, s), y = i * rowH, by = y + (rowH - thick) / 2;
                node('rect', { x: 0, y: y, width: W, height: rowH, 'class': 'hit' }, g);
                var lb = txt(g, labelW - 8, y + rowH / 2, trunc(it.label, labelW - 10), 'lbl', 'end');
                var tt = node('title', {}, lb); tt.textContent = it.label;
                var segs = it.segs || [{ name: o.series || '', value: it.value, color: o.color || 'var(--s1)' }];
                var x = labelW, total = 0;
                var lastIdx = -1; segs.forEach(function (sg, k) { if (sg.value > 0) lastIdx = k; });
                segs.forEach(function (sg, k) {
                    var v = sg.value || 0; total += v; if (v <= 0) return;
                    var w = plotW * v / max, isLast = k === lastIdx, ww = isLast ? w : Math.max(0, w - 2);
                    if (ww > 0) node('path', { d: isLast ? hBarPath(x, by, ww, thick) : rectPath(x, by, ww, thick), style: 'fill:' + sg.color }, g);
                    x += w;
                });
                if (it.marker != null) { var mx = labelW + plotW * Math.min(it.marker, max) / max; node('line', { x1: mx, x2: mx, y1: by - 5, y2: by + thick + 5, 'class': 'marker' }, g); }
                var end = labelW + plotW * Math.max(total, it.marker || 0) / max;
                txt(g, end + 6, y + rowH / 2, it.valueLabel != null ? it.valueLabel : (o.fmt || fmtInt)(total), 'val');
                bindHover(g, function () {
                    if (it.tip) return it.tip;
                    var rows = segs.filter(function (sg) { return sg.name; }).map(function (sg) { return { n: sg.name, v: (o.fmt || fmtInt)(sg.value || 0), c: sg.color }; });
                    if (!rows.length) rows.push({ n: o.series || 'Value', v: (o.fmt || fmtInt)(total) });
                    if (it.marker != null) rows.push({ n: o.markerName || 'Target', v: (o.fmt || fmtInt)(it.marker) });
                    return tipRows(it.label, rows, it.onClick ? 'Click to filter' : null);
                }, it.onClick);
            });
            host.appendChild(s);
        }

        // Diverging horizontal bars around a baseline (e.g. SPI - 1).
        function barDiv(host, items, o) {
            o = o || {}; host.innerHTML = '';
            if (!items.length) return emptyChart(host);
            if (!o.tickFmt) o.tickFmt = function (t) { return fmt1(t); };
            var W = Math.max(300, innerW(host)), rowH = 26, thick = 14, labelW = Math.min(190, Math.max(80, W * 0.3)), plotW = W - labelW - 60;
            var ext = Math.max(0.1, Math.max.apply(null, items.map(function (i) { return Math.abs(i.value || 0); })));
            var mid = labelW + plotW / 2, H = items.length * rowH + 22, s = svgEl(W, H, o.label);
            [-ext, -ext / 2, 0, ext / 2, ext].forEach(function (t) { var x = mid + plotW / 2 * t / ext; node('line', { x1: x, x2: x, y1: 0, y2: H - 18, 'class': t === 0 ? 'axis' : 'gridline' }, s); txt(s, x, H - 8, o.tickFmt(t), 'tick', 'middle'); });
            items.forEach(function (it, i) {
                var g = node('g', { 'class': 'mark', tabindex: 0 }, s), y = i * rowH, by = y + (rowH - thick) / 2;
                node('rect', { x: 0, y: y, width: W, height: rowH, 'class': 'hit' }, g);
                var lb = txt(g, labelW - 8, y + rowH / 2, trunc(it.label, labelW - 10), 'lbl', 'end'); node('title', {}, lb).textContent = it.label;
                var v = it.value;
                if (v != null) {
                    var w = Math.abs(plotW / 2 * v / ext), neg = v < 0;
                    if (w > 0.5) {
                        node('path', { d: neg ? hBarPathLeft(mid - w, by, w, thick) : hBarPath(mid, by, w, thick), style: 'fill:' + ((neg !== !!o.posBad) ? 'var(--div-neg)' : 'var(--div-pos)') }, g);
                    }
                    // Negative values are labelled on the empty side of the axis so they never collide with category names.
                    txt(g, neg ? mid + 6 : mid + w + 6, y + rowH / 2, it.valueLabel, 'val', 'start');
                } else txt(g, mid + 6, y + rowH / 2, 'n/a', 'tick');
                bindHover(g, function () { return it.tip; }, it.onClick);
            });
            host.appendChild(s);
        }

        // Vertical columns (grouped or stacked). cats: [label], series: [{name,color,values}]
        function columns(host, cats, series, o) {
            o = o || {}; host.innerHTML = '';
            if (!cats.length) return emptyChart(host);
            var W = Math.max(300, innerW(host)), H = o.height || 260, padL = 44, padB = 34, padT = 16;
            var plotW = W - padL - 8, plotH = H - padB - padT, band = plotW / cats.length;
            var max = 0;
            cats.forEach(function (c, i) {
                if (o.stacked) max = Math.max(max, sum(series, function (s) { return s.values[i] || 0; }));
                else series.forEach(function (s) { max = Math.max(max, s.values[i] || 0); });
            });
            var ticks = niceTicks(max || 1, 4), top = ticks[ticks.length - 1];
            var s = svgEl(W, H, o.label);
            ticks.forEach(function (t) { var y = padT + plotH - plotH * t / top; node('line', { x1: padL, x2: W - 4, y1: y, y2: y, 'class': t === 0 ? 'axis' : 'gridline' }, s); txt(s, padL - 6, y, compact(t), 'tick', 'end'); });
            var every = Math.max(1, Math.ceil(cats.length / Math.max(1, Math.floor(plotW / 58))));
            var barW = o.stacked ? Math.min(24, band * 0.62) : Math.min(24, band * 0.72 / series.length);
            cats.forEach(function (c, i) {
                var g = node('g', { 'class': 'mark', tabindex: 0 }, s), cx = padL + band * i + band / 2;
                node('rect', { x: padL + band * i, y: padT, width: band, height: plotH, 'class': 'hit' }, g);
                if (o.stacked) {
                    var yb = padT + plotH, lastK = -1; series.forEach(function (sr, k) { if ((sr.values[i] || 0) > 0) lastK = k; });
                    series.forEach(function (sr, k) {
                        var v = sr.values[i] || 0; if (v <= 0) return;
                        var h = plotH * v / top, isLast = k === lastK, hh = isLast ? h : Math.max(0, h - 2);
                        if (hh > 0) node('path', { d: isLast ? vBarPath(cx - barW / 2, yb - h, barW, h) : rectPath(cx - barW / 2, yb - hh, barW, hh), style: 'fill:' + sr.color }, g);
                        yb -= h;
                    });
                } else {
                    series.forEach(function (sr, k) {
                        var v = sr.values[i] || 0, h = plotH * v / top, x = cx - (barW * series.length + 2 * (series.length - 1)) / 2 + k * (barW + 2);
                        if (h > 0) node('path', { d: vBarPath(x, padT + plotH - h, barW, h), style: 'fill:' + sr.color }, g);
                    });
                }
                if (i % every === 0) txt(s, cx, H - padB + 14, trunc(c, band * every - 4), 'tick', 'middle');
                bindHover(g, function () {
                    var rows = series.map(function (sr) { return { n: sr.name, v: (o.fmt || fmtInt)(sr.values[i] || 0), c: sr.color }; });
                    if (o.stacked && series.length > 1) rows.push({ n: 'Total', v: (o.fmt || fmtInt)(sum(series, function (sr) { return sr.values[i] || 0; })) });
                    return tipRows(c, rows, o.onClick ? 'Click to filter' : null);
                }, o.onClick ? function () { o.onClick(c, i); } : null);
            });
            if (o.labelMax) {   // label only the tallest column
                var best = -1, bi = -1; cats.forEach(function (c, i) { var t = o.stacked ? sum(series, function (sr) { return sr.values[i] || 0; }) : Math.max.apply(null, series.map(function (sr) { return sr.values[i] || 0; })); if (t > best) { best = t; bi = i; } });
                if (bi >= 0 && best > 0) txt(s, padL + band * bi + band / 2, padT + plotH - plotH * best / top - 8, (o.fmt || fmtInt)(best), 'val', 'middle');
            }
            host.appendChild(s);
        }

        // Line chart on a day axis. xs: [day], series: [{name,color,values}] (null = gap)
        function lineChart(host, xs, series, o) {
            o = o || {}; host.innerHTML = '';
            if (xs.length < 2) return emptyChart(host, 'Not enough dated data to draw a curve.');
            var W = Math.max(320, innerW(host)), H = o.height || 300, padL = 46, padR = 70, padT = 16, padB = 30;
            var plotW = W - padL - padR, plotH = H - padT - padB, x0 = xs[0], x1 = xs[xs.length - 1];
            var yMax = o.yMax || niceTicks(Math.max.apply(null, series.map(function (s) { return Math.max.apply(null, s.values.map(function (v) { return v || 0; })); }).concat([1])), 4).slice(-1)[0];
            function X(d) { return padL + plotW * (d - x0) / Math.max(1, x1 - x0); }
            function Y(v) { return padT + plotH - plotH * v / yMax; }
            var s = svgEl(W, H, o.label);
            niceTicks(yMax, 4).forEach(function (t) { if (t > yMax + 1e-9) return; node('line', { x1: padL, x2: padL + plotW, y1: Y(t), y2: Y(t), 'class': t === 0 ? 'axis' : 'gridline' }, s); txt(s, padL - 6, Y(t), o.yFmt ? o.yFmt(t) : compact(t), 'tick', 'end'); });
            var m0 = monthKey(x0), m1 = monthKey(x1), span = m1 - m0 + 1, stepM = Math.max(1, Math.ceil(span / Math.max(2, Math.floor(plotW / 70))));
            for (var k = m0; k <= m1; k += stepM) { var d = monthStart(k); if (d < x0) continue; txt(s, X(d), H - 10, monthLabel(k), 'tick', 'middle'); }
            if (o.refY != null && o.refY <= yMax) { node('line', { x1: padL, x2: padL + plotW, y1: Y(o.refY), y2: Y(o.refY), 'class': 'marker', style: 'stroke-width:1.5;opacity:.7' }, s); txt(s, padL + plotW + 6, Y(o.refY), o.refLabel || '', 'tick'); }
            if (o.today != null && o.today >= x0 && o.today <= x1) { node('line', { x1: X(o.today), x2: X(o.today), y1: padT, y2: padT + plotH, 'class': 'today' }, s); txt(s, X(o.today) + 4, padT + 6, 'Data date', 'tick'); }
            var ends = [];
            series.forEach(function (sr) {
                var dPath = '', started = false, last = null;
                sr.values.forEach(function (v, i) { if (v == null) { started = false; return; } dPath += (started ? 'L' : 'M') + X(xs[i]).toFixed(1) + ',' + Y(v).toFixed(1); started = true; last = i; });
                if (sr.area && last != null) node('path', { d: dPath + 'V' + Y(0) + 'H' + X(xs[sr.values.findIndex(function (v) { return v != null; })]) + 'Z', style: 'fill:' + sr.color + ';fill-opacity:.10;stroke:none' }, s);
                node('path', { d: dPath, style: 'fill:none;stroke:' + sr.color + ';stroke-width:2;stroke-linejoin:round;stroke-linecap:round' }, s);
                if (last != null) { node('circle', { cx: X(xs[last]), cy: Y(sr.values[last]), r: 4, 'class': 'dot', style: 'fill:' + sr.color }, s); ends.push({ y: Y(sr.values[last]), x: X(xs[last]), t: (o.yFmt || fmtInt)(sr.values[last]) }); }
            });
            // End labels only when they don't collide.
            ends.sort(function (a, b) { return a.y - b.y; });
            var clash = ends.some(function (e, i) { return i > 0 && Math.abs(e.y - ends[i - 1].y) < 14 && Math.abs(e.x - ends[i - 1].x) < 50; });
            if (!clash) ends.forEach(function (e) { txt(s, e.x + 8, e.y, e.t, 'val'); });
            // Crosshair + tooltip
            var cross = node('line', { x1: 0, x2: 0, y1: padT, y2: padT + plotH, 'class': 'axis', visibility: 'hidden' }, s);
            var hit = node('rect', { x: padL, y: padT, width: plotW, height: plotH, fill: 'transparent', tabindex: 0 }, s);
            function at(clientX) {
                var rc = s.getBoundingClientRect(), d = x0 + (clientX - rc.left - padL) / plotW * (x1 - x0), best = 0;
                for (var i = 1; i < xs.length; i++) if (Math.abs(xs[i] - d) < Math.abs(xs[best] - d)) best = i;
                return best;
            }
            function show(i, ev) {
                cross.setAttribute('x1', X(xs[i])); cross.setAttribute('x2', X(xs[i])); cross.setAttribute('visibility', 'visible');
                showTip(tipRows((o.xFmt || fmtDay)(xs[i]), series.map(function (sr) { return { n: sr.name, v: sr.values[i] == null ? '–' : (o.yFmt || fmtInt)(sr.values[i]), c: sr.color }; })), ev);
            }
            hit.addEventListener('mousemove', function (e) { show(at(e.clientX), e); });
            hit.addEventListener('mouseleave', function () { cross.setAttribute('visibility', 'hidden'); hideTip(); });
            var kbIdx = xs.length - 1;
            hit.addEventListener('keydown', function (e) { if (e.key === 'ArrowLeft') kbIdx = Math.max(0, kbIdx - 1); else if (e.key === 'ArrowRight') kbIdx = Math.min(xs.length - 1, kbIdx + 1); else return; e.preventDefault(); show(kbIdx, { target: hit, type: 'focus' }); });
            hit.addEventListener('blur', function () { cross.setAttribute('visibility', 'hidden'); hideTip(); });
            host.appendChild(s);
        }

        // Heatmap (sequential single hue).
        function heatmap(host, rowsL, colsL, m, o) {
            o = o || {}; host.innerHTML = '';
            if (!rowsL.length) return emptyChart(host);
            var W = Math.max(320, innerW(host)), labelW = Math.min(190, Math.max(90, W * 0.26)), cellH = 28, headH = 26;
            var cellW = Math.max(40, (W - labelW - 4) / colsL.length), H = headH + rowsL.length * cellH + 4;
            var max = 0; m.forEach(function (r) { r.forEach(function (v) { if (v > max) max = v; }); });
            var s = svgEl(labelW + cellW * colsL.length + 4, H, o.label);
            colsL.forEach(function (c, j) { txt(s, labelW + cellW * j + cellW / 2, headH / 2, c, 'tick', 'middle'); });
            rowsL.forEach(function (rl, i) {
                var lb = txt(s, labelW - 8, headH + i * cellH + cellH / 2, trunc(rl, labelW - 10), 'lbl', 'end'); node('title', {}, lb).textContent = rl;
                colsL.forEach(function (c, j) {
                    var v = m[i][j] || 0, g = node('g', { 'class': 'mark', tabindex: 0 }, s);
                    var step = v > 0 ? Math.min(SEQ.length - 1, Math.floor(v / max * (SEQ.length - 1e-9))) : -1;
                    node('rect', { x: labelW + cellW * j + 1, y: headH + i * cellH + 1, width: cellW - 2, height: cellH - 2, rx: 4, style: 'fill:' + (step < 0 ? 'var(--surface-2)' : SEQ[step]) }, g);
                    if (v > 0) {
                        var dark = document.documentElement.getAttribute('data-theme') === 'dark' || (!document.documentElement.getAttribute('data-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
                        var inkWhite = dark ? step <= 3 : step >= 4;
                        var t = txt(g, labelW + cellW * j + cellW / 2, headH + i * cellH + cellH / 2, fmtInt(v), 'cell-t', 'middle');
                        t.style.fill = inkWhite ? '#FFFFFF' : '#0B0B0B';
                    }
                    bindHover(g, function () { return tipRows(rl + ' · ' + c, [{ n: o.valueName || 'Documents', v: fmtInt(v) }], o.onClick ? 'Click to filter' : null); }, o.onClick && v > 0 ? function () { o.onClick(rl, c); } : null);
                });
            });
            host.appendChild(s);
        }

        function legend(items) {
            return '<div class="legend">' + items.map(function (i) {
                var cls = i.kind === 'tick' ? ' class="tick"' : (i.kind === 'line' ? ' class="line"' : '');
                var sty = i.kind === 'tick' ? '' : (i.kind === 'ring' ? ' style="background:transparent;border:2.5px solid ' + i.color + ';border-radius:50%"' : ' style="background:' + i.color + '"');
                return '<span><i' + cls + sty + '></i>' + esc(i.name) + '</span>';
            }).join('') + '</div>';
        }

        /* =================================================================
           Cards, tables, tiles
           ================================================================= */
        var CARDS = [];
        function card(parent, o) {
            var el = document.createElement('section');
            el.className = 'card span-' + (o.span || 6);
            el.innerHTML = '<div class="card-h"><div><h3>' + esc(o.title) + '</h3>' + (o.sub ? '<p>' + esc(o.sub) + '</p>' : '') + '</div><div class="tools">' +
                (o.controls || '') +
                (o.table !== false ? '<button type="button" class="icon-btn" data-act="table" aria-pressed="false" title="Show as table">▦</button>' : '') +
                '<button type="button" class="icon-btn" data-act="xlsx" title="Download Excel (.xlsx)">⊞</button>' +
                '<button type="button" class="icon-btn" data-act="csv" title="Download CSV">⬇</button>' +
                '<button type="button" class="icon-btn" data-act="max" title="Focus mode">⤢</button></div></div>' +
                (o.legend || '') + '<div class="card-b"></div>' + (o.foot ? '<div class="card-f">' + o.foot + '</div>' : '');
            parent.appendChild(el);
            var c = { el: el, body: el.querySelector('.card-b'), draw: o.draw, data: o.data, asTable: !!o.asTable, title: o.title };
            el._card = c;
            CARDS.push(c);
            paint(c);
            return c;
        }
        function paint(c) {
            if (c.asTable || !c.draw) c.body.innerHTML = tableHtml(c.data());
            else c.draw(c.body);
            var b = c.el.querySelector('[data-act="table"]'); if (b) b.setAttribute('aria-pressed', c.asTable ? 'true' : 'false');
        }
        function tableHtml(d) {
            if (!d || !d.rows.length) return '<div class="empty">No rows for the current filters.</div>';
            return '<div class="tbl-wrap"><table class="t"><thead><tr>' + d.head.map(function (h, i) { return '<th' + (d.num && d.num[i] ? ' class="num"' : '') + '>' + esc(h) + '</th>'; }).join('') + '</tr></thead><tbody>' +
                d.rows.map(function (r, ri) {
                    return '<tr' + (d.click ? ' class="click" data-row="' + ri + '"' : '') + '>' + r.map(function (v, i) {
                        var raw = d.html && d.html[i];
                        return '<td' + (d.num && d.num[i] ? ' class="num"' : (d.wrap && d.wrap[i] ? ' class="wrap"' : '')) + '>' + (raw ? v : esc(v)) + '</td>';
                    }).join('') + '</tr>';
                }).join('') + '</tbody>' + (d.foot ? '<tfoot><tr>' + d.foot.map(function (v, i) { return '<td' + (d.num && d.num[i] ? ' class="num"' : '') + '>' + esc(v) + '</td>'; }).join('') + '</tr></tfoot>' : '') + '</table></div>';
        }
        function csvOf(d) {
            function q(v, i) { v = String(v == null ? '' : v); if (d.html && d.html[i]) v = v.replace(/<[^>]+>/g, ''); if (/^[=+\-@\t\r]/.test(v) && isNaN(v)) v = "'" + v; return /[",\r\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }
            return [d.head.map(function (h) { return q(h, -1); }).join(',')].concat(d.rows.map(function (r) { return r.map(q).join(','); })).join('\r\n');
        }
        function download(name, text) {
            var blob = new Blob(['﻿' + text], { type: 'text/csv;charset=utf-8' });
            var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name.replace(/[^\w\-. ]+/g, '_') + '.csv';
            document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
        }
        function tile(o) {
            return '<div class="tile' + (o.click ? ' click' : '') + '"' + (o.click ? ' data-go="' + esc(o.click) + '" tabindex="0" role="button"' : '') + ' title="' + esc(o.help || '') + '">' +
                (o.ico ? '<span class="ico" aria-hidden="true">' + o.ico + '</span>' : '') +
                '<div class="lbl"' + (o.ico ? ' style="padding-right:36px"' : '') + '>' + esc(o.label) + '</div><div class="val">' + esc(o.value) + '</div>' +
                (o.sub ? '<div class="sub">' + o.sub + '</div>' : '') +
                (o.meter != null ? '<div class="meter"><span style="width:' + Math.max(0, Math.min(100, o.meter)) + '%"></span></div>' : '') + '</div>';
        }
        function pbar(v) { if (v == null) return '–'; return '<span class="pbar"><span class="trk"><span class="fil" style="width:' + Math.max(0, Math.min(100, v)) + '%"></span></span><b>' + fmtPct(v) + '</b></span>'; }
        function section(root, title, sub) { var d = document.createElement('div'); d.className = 'section-title'; d.innerHTML = '<h2>' + esc(title) + '</h2>' + (sub ? '<p>' + esc(sub) + '</p>' : ''); root.appendChild(d); }
        function kpiRow(root, html, cls) { var d = document.createElement('div'); d.className = cls || 'kpis'; d.innerHTML = html; root.appendChild(d); return d; }

        /* =================================================================
           Shared analytics
           ================================================================= */
        function hoursWeight(rows) { var h = sum(rows, function (r) { return r.hrs; }); return h > 0 ? function (r) { return r.hrs; } : function () { return 1; }; }
        // The EPR's own figures (Used Hours, PLN%) describe *today*; for a past data date, or when the
        // EPR columns are empty for the whole data set, both sides use the rules-of-credit path instead,
        // over the same rows, so SPI always compares like with like.
        function useEpr(rows) { return S.asOf >= todayDay() && sum(rows, function (r) { return r.hrs; }) > 0; }
        function earnedPct(rows) {
            var wf = hoursWeight(rows), tw = sum(rows, wf); if (!tw) return null;
            if (useEpr(rows) && S.hasEarned) return sum(rows, function (r) { return r.earned; }) / tw * 100;
            return sum(rows, function (r) { return wf(r) * creditAt(r, S.asOf, 'a'); }) / tw * 100;
        }
        function plannedPct(rows) {
            var wf = hoursWeight(rows), tw = sum(rows, wf); if (!tw) return null;
            if (useEpr(rows) && S.hasPln) return sum(rows, function (r) { return r.hrs * (r.plnPct != null ? r.plnPct : creditAt(r, S.asOf, 'p') * 100); }) / tw;
            return sum(rows, function (r) { return wf(r) * creditAt(r, S.asOf, 'p'); }) / tw * 100;
        }
        var WKEY = ['IDC', 'IFR', 'RCC', null, null, 'APP', 'AFC'];   // stage index -> rules-of-credit bucket
        function creditAt(r, day, which) {
            // Cumulative credit: reaching a stage also credits every earlier stage.
            var reach = -1;
            r.ms.forEach(function (m, i) { var d = m[which]; if (d != null && d <= day) reach = i; });
            if (which === 'a' && r.complete && reach < 6 && r.ms[6].x == null && FINAL_STATUSES.indexOf(up(r.status)) >= 0) reach = 6;
            var c = 0; for (var i = 0; i <= reach; i++) if (WKEY[i]) c += S.weights[WKEY[i]];
            return Math.min(1, c);
        }
        function sCurve(rows) {
            var wf = hoursWeight(rows), tw = sum(rows, wf);
            if (!tw) return null;
            var days = [];
            rows.forEach(function (r) { r.ms.forEach(function (m, i) { if (m.p != null) days.push(m.p); if (m.a != null) days.push(m.a); if (r.bl && r.bl[i] != null) days.push(r.bl[i]); }); });
            if (!days.length) return null;
            days.sort(function (a, b) { return a - b; });
            var lo = days[Math.floor(days.length * 0.01)], hi = days[Math.ceil(days.length * 0.99) - 1];
            var start = weekStart(lo), end = weekStart(hi) + 7, stepDays = Math.max(7, Math.ceil((end - start) / 7 / 160) * 7);
            var xs = []; for (var d = start; d <= end; d += stepDays) xs.push(d);
            // Event lists -> cumulative (fast): for each doc add weight deltas on its credit steps.
            function curve(which) {
                var ev = new Map();
                rows.forEach(function (r) {
                    var w = wf(r); if (!w) return;
                    var prevC = 0, reach = -1;
                    var pts = [];
                    r.ms.forEach(function (m, i) { var d = which === 'b' ? (r.bl ? r.bl[i] : null) : m[which]; if (d != null && (which !== 'a' || d <= S.asOf)) pts.push({ d: d, i: i }); });
                    pts.sort(function (a, b) { return a.d - b.d || a.i - b.i; });
                    pts.forEach(function (p) {
                        if (p.i <= reach) return; reach = p.i;
                        var c = 0; for (var i = 0; i <= reach; i++) if (WKEY[i]) c += S.weights[WKEY[i]];
                        c = Math.min(1, c); if (c > prevC) { ev.set(p.d, (ev.get(p.d) || 0) + w * (c - prevC)); prevC = c; }
                    });
                });
                var keys = Array.from(ev.keys()).sort(function (a, b) { return a - b; }), k = 0, acc = 0;
                return xs.map(function (x, xi) {
                    var last = xi === xs.length - 1;   // outliers beyond the trimmed range still count at the end
                    while (k < keys.length && (last || keys[k] <= x)) { acc += ev.get(keys[k]); k++; }
                    return acc / tw * 100;
                });
            }
            var planned = curve('p'), actual = curve('a');
            var actualCut = actual.map(function (v, i) { return xs[i] <= S.asOf + stepDays ? v : null; });
            return { xs: xs, planned: planned, actual: actualCut, baseline: S.blName ? curve('b') : null };
        }
        function discRows(rows) {
            var m = groupBy(rows, function (r) { return r.disc; });
            return Array.from(m.keys()).sort(discSort).map(function (k) { return { disc: k, rows: m.get(k) }; });
        }
        function discStats(list) {
            return list.map(function (g) {
                var r = g.rows, docs = r.filter(function (x) { return !x.isAct; });
                var done = docs.filter(function (x) { return x.complete; }).length;
                var late = [], onTime = 0, closed = 0;
                r.forEach(function (x) { x.ms.forEach(function (m) { if (m.x != null && m.p != null) { closed++; if (m.x <= m.p) onTime++; else late.push(m.x - m.p); } }); });
                var pl = plannedPct(r), ea = earnedPct(r);
                return {
                    disc: g.disc, docs: docs.length, done: done, donePct: pct(done, docs.length),
                    overdue: r.filter(function (x) { return x.overdue; }).length,
                    hrs: sum(r, function (x) { return x.hrs; }), earned: sum(r, function (x) { return x.earned; }),
                    planned: pl, actual: ea, spi: (pl > 0 && ea != null) ? ea / pl : null,
                    onTime: pct(onTime, closed), avgSlip: avg(late), rows: r
                };
            });
        }

        /* =================================================================
           Rendering
           ================================================================= */
        function render() {
            hideTip();
            var bd = document.querySelector('.backdrop'); if (bd) bd.remove();
            renderFilterBar();
            renderTabs();
            var v = $('view'); v.innerHTML = ''; CARDS = [];
            if (!S.data) return;
            if (S.data.truncated) v.insertAdjacentHTML('beforeend', '<div class="banner">The group has more rows than the dashboard loads at once; results are truncated. Pick a single project for complete figures.</div>');
            var grid = document.createElement('div'); grid.className = 'grid'; v.appendChild(grid);
            (VIEWS[S.tab] || VIEWS.overview)(grid, S.rows);
        }
        function renderTabs() {
            var pend = S.rows.filter(function (r) { return !r.complete; }).length, od = S.rows.filter(function (r) { return r.overdue; }).length;
            var badge = { pending: pend, overdue: od, register: S.rows.length };
            $('tabs').innerHTML = TABS.map(function (t) {
                return '<button type="button" class="tab" role="tab" data-tab="' + t.id + '" aria-selected="' + (S.tab === t.id) + '">' + esc(t.label) + (badge[t.id] != null ? '<span class="n">' + fmtInt(badge[t.id]) + '</span>' : '') + '</button>';
            }).join('');
        }
        function go(tab) { S.tab = tab; store('sddr.dash.tab', tab); history.replaceState(null, '', location.pathname + location.search + '#' + tab); render(); window.scrollTo(0, 0); }
        function docsTable(rows, cols, limit) {
            var list = rows.slice(0, limit || 500);
            return {
                head: cols.map(function (c) { return c.h; }), num: cols.map(function (c) { return !!c.num; }), wrap: cols.map(function (c) { return !!c.wrap; }), html: cols.map(function (c) { return !!c.html; }),
                rows: list.map(function (r) { return cols.map(function (c) { return c.v(r); }); }), click: true, src: list
            };
        }
        var COL = {
            proj: { h: 'Project', v: function (r) { return r.proj; } },
            disc: { h: 'Discipline', v: function (r) { return r.disc; } },
            doc: { h: 'Document No', v: function (r) { return r.doc; } },
            title: { h: 'Title', v: function (r) { return r.title; }, wrap: true },
            status: { h: 'Status', v: function (r) { return r.status; } },
            reqd: { h: 'Req.', v: function (r) { return r.reqd; } },
            stage: { h: 'Last stage', v: function (r) { return r.stageLabel; } },
            next: { h: 'Next', v: function (r) { return r.nextLabel; } },
            due: { h: 'Next due', v: function (r) { return fmtDay(r.nextDue); } },
            late: { h: 'Days late', v: function (r) { return r.daysLate || ''; }, num: true },
            dueIn: { h: 'Due in (d)', v: function (r) { return r.dueIn == null ? '' : r.dueIn; }, num: true },
            crit: { h: 'Criticality', v: function (r) { return r.crit; } },
            hrs: { h: 'Hours', v: function (r) { return fmt1(r.hrs); }, num: true }
        };
        function cols() { var a = Array.prototype.slice.call(arguments); return a.filter(function (k) { return k !== 'proj' || !S.scope.project; }).map(function (k) { return COL[k]; }); }
        function docCard(grid, title, sub, rows, colList, span) {
            var d = function () { return docsTable(rows, colList); };
            var c = card(grid, { title: title, sub: sub + (rows.length > 500 ? ' – first 500 of ' + fmtInt(rows.length) + ' (use Register for all)' : ''), span: span || 12, data: d, asTable: true, table: false });
            c.docRows = rows.slice(0, 500);
            return c;
        }

        var VIEWS = {};

        /* =================================================================
           Trend (nightly snapshots) – shared helpers
           ================================================================= */
        // Snapshot series for the current Discipline filter (other filters can't apply to a snapshot).
        function snapSeries() {
            var f = S.filters.disc, byDate = new Map();
            S.snaps.forEach(function (x) {
                if (f) { if (x.disc === '*' || !passField({ disc: x.disc === '(Blank)' ? '' : x.disc }, 'disc', f)) return; }
                else if (x.disc !== '*') return;
                var a = byDate.get(x.d);
                if (!a) { a = { d: x.d, docs: 0, complete: 0, pending: 0, overdue: 0, due14: 0, notStarted: 0, est: 0, earned: 0, planned: 0, m75p: 0, m75d: 0 }; byDate.set(x.d, a); }
                for (var k in a) if (k !== 'd') a[k] += x[k] || 0;
            });
            return Array.from(byDate.values()).sort(function (a, b) { return a.d - b.d; }).map(function (a) {
                a.earnedPct = pct(a.earned, a.est); a.plannedPct = pct(a.planned, a.est);
                a.spi = a.planned > 0 ? a.earned / a.planned : null; a.m75Pct = pct(a.m75d, a.m75p);
                return a;
            });
        }
        function weekAgo(series, day) { var best = null; series.forEach(function (x) { if (x.d <= day - 7) best = x; }); return best; }
        // Deltas only make sense when the current view measures what a snapshot measured.
        function compareBase() {
            if (!S.history || !S.snaps.length || S.q || S.qc || S.inclAct || S.inclCancel || S.asOf < todayDay()) return null;
            if (Object.keys(S.filters).some(function (k) { return k !== 'disc'; })) return null;
            var prev = weekAgo(snapSeries(), S.asOf);
            return prev ? { prev: prev } : null;
        }
        function deltaHtml(cur, prev, upGood, isPct) {
            if (cur == null || prev == null) return '';
            var d = cur - prev, txtV = isPct ? fmt1(Math.abs(d)) + ' pts' : fmtInt(Math.abs(d));
            if (Math.abs(d) < (isPct ? 0.05 : 0.5)) return '<span class="delta flat" title="vs last week">■ 0</span>';
            var good = (d > 0) === upGood;
            return '<span class="delta ' + (good ? 'good' : 'bad') + '" title="vs last week">' + (d > 0 ? '▲ ' : '▼ ') + txtV + '</span>';
        }
        function ringSvg(ea, pl) {
            var r = 36, c = 2 * Math.PI * r, e = Math.max(0, Math.min(100, ea || 0)), p = Math.max(0, Math.min(100, pl || 0));
            var ang = p / 100 * 2 * Math.PI - Math.PI / 2, cx = 46, cy = 46;
            var x1 = cx + (r - 8) * Math.cos(ang), y1 = cy + (r - 8) * Math.sin(ang), x2 = cx + (r + 8) * Math.cos(ang), y2 = cy + (r + 8) * Math.sin(ang);
            return '<svg class="ring" width="92" height="92" viewBox="0 0 92 92" role="img" aria-label="Earned ' + fmtPct(ea) + ' against planned ' + fmtPct(pl) + '">' +
                '<circle class="trk" cx="46" cy="46" r="' + r + '" fill="none" stroke-width="9"/>' +
                '<circle class="arc" cx="46" cy="46" r="' + r + '" fill="none" stroke-width="9" stroke-dasharray="' + (c * e / 100).toFixed(1) + ' ' + c.toFixed(1) + '" transform="rotate(-90 46 46)"/>' +
                (pl != null ? '<line class="pln" x1="' + x1.toFixed(1) + '" y1="' + y1.toFixed(1) + '" x2="' + x2.toFixed(1) + '" y2="' + y2.toFixed(1) + '"><title>Planned</title></line>' : '') + '</svg>';
        }
        function curveSeries(sc) {
            var out = [{ name: 'Planned', color: 'var(--s1)', values: sc.planned }, { name: 'Actual', color: 'var(--s2)', values: sc.actual }];
            if (sc.baseline) out.push({ name: 'Baseline', color: 'var(--s3)', values: sc.baseline });
            return out;
        }
        function curveLegend() {
            return [{ name: 'Planned', color: 'var(--s1)', kind: 'line' }, { name: 'Actual', color: 'var(--s2)', kind: 'line' }].concat(S.blName ? [{ name: 'Baseline ' + S.blName, color: 'var(--s3)', kind: 'line' }] : []);
        }
        function setupCard(grid, feature) {
            var d = document.createElement('div'); d.className = 'setup';
            d.innerHTML = '<h3>' + esc(feature) + ' is not set up yet</h3>' +
                '<p>This feature stores history in the database. Ask the SmartDDR administrator to:</p><ol>' +
                '<li>Run <code>SmartDDRDashboard_Setup.sql</code> in the ACAD_DATA database (creates the snapshot, baseline and saved-view tables and procedures).</li>' +
                '<li>Optionally enable the nightly SQL Agent job at the bottom of that script (or schedule <code>EXEC dbo.usp_SDDR_Dash_Snapshot</code>).</li>' +
                '<li>Grant the web application\'s database login the permissions listed in the script.</li></ol>';
            grid.appendChild(d);
        }
        function adminBtn(action, label) { return S.isAdmin ? '<button type="button" class="btn" data-admin="' + action + '">' + esc(label) + '</button>' : ''; }

        /* ---------------- Trend ---------------- */
        VIEWS.trend = function (grid) {
            if (!S.history) return setupCard(grid, 'Trend history');
            var ser = snapSeries(), snapBtn = adminBtn('snapshot', '📸 Snapshot now');
            if (ser.length < 2) {
                var d = document.createElement('div'); d.className = 'setup';
                d.innerHTML = '<h3>' + (ser.length ? 'One snapshot so far' : 'No snapshots yet') + '</h3><p>Trend lines appear once there are at least two daily snapshots. They are taken nightly by the SQL Agent job' +
                    (S.isAdmin ? ', or now with the button below.</p><p style="margin-top:10px">' + snapBtn + '</p>' : '.</p>');
                grid.appendChild(d); return;
            }
            var last = ser[ser.length - 1], prev = weekAgo(ser, last.d) || ser[0];
            var xs = ser.map(function (x) { return x.d; });
            kpiRow(grid,
                tile({ label: 'Earned %', ico: '📈', value: fmtPct(last.earnedPct), sub: deltaHtml(last.earnedPct, prev.earnedPct, true, true) + ' vs ' + esc(fmtDay(prev.d)) }) +
                tile({ label: 'Planned %', ico: '🗓️', value: fmtPct(last.plannedPct), sub: deltaHtml(last.plannedPct, prev.plannedPct, true, true) }) +
                tile({ label: 'SPI', ico: '🎯', value: last.spi == null ? '–' : last.spi.toFixed(2), sub: spiStatus(last.spi) + ' ' + (last.spi != null && prev.spi != null ? deltaHtml(last.spi * 100, prev.spi * 100, true, true).replace(' pts', '') : '') }) +
                tile({ label: 'Pending', ico: '⏳', value: fmtInt(last.pending), sub: deltaHtml(last.pending, prev.pending, false) }) +
                tile({ label: 'Overdue', ico: '⚠️', value: fmtInt(last.overdue), sub: deltaHtml(last.overdue, prev.overdue, false) }) +
                tile({ label: 'M75 AFC', ico: '🚩', value: fmtPct(last.m75Pct), sub: deltaHtml(last.m75Pct, prev.m75Pct, true, true) }) +
                tile({ label: 'Snapshots', ico: '📸', value: fmtInt(ser.length), sub: esc(fmtDay(ser[0].d)) + ' → ' + esc(fmtDay(last.d)) }));
            var pctFmt = function (v) { return Math.round(v) + '%'; };
            card(grid, {
                title: 'Progress trend', sub: 'Earned vs planned % as recorded each night', span: 8, controls: snapBtn,
                legend: legend([{ name: 'Planned %', color: 'var(--s1)', kind: 'line' }, { name: 'Earned %', color: 'var(--s2)', kind: 'line' }]),
                draw: function (h) { lineChart(h, xs, [{ name: 'Planned %', color: 'var(--s1)', values: ser.map(function (x) { return x.plannedPct; }) }, { name: 'Earned %', color: 'var(--s2)', values: ser.map(function (x) { return x.earnedPct; }), area: true }], { yMax: 100, yFmt: pctFmt, label: 'Progress trend' }); },
                data: function () { return snapTable(ser); }
            });
            var spiMax = Math.max(1.2, Math.ceil(Math.max.apply(null, ser.map(function (x) { return x.spi || 0; })) * 10 + 1) / 10);
            card(grid, {
                title: 'SPI trend', sub: 'Schedule performance index; the line at 1.00 is on plan', span: 4,
                draw: function (h) { lineChart(h, xs, [{ name: 'SPI', color: 'var(--s1)', values: ser.map(function (x) { return x.spi; }) }], { yMax: spiMax, yFmt: function (v) { return v.toFixed(2); }, refY: 1, refLabel: '1.00', label: 'SPI trend' }); },
                data: function () { return { head: ['Date', 'SPI'], num: [0, 1], rows: ser.map(function (x) { return [fmtDay(x.d), x.spi == null ? '' : x.spi.toFixed(3)]; }) }; }
            });
            card(grid, {
                title: 'Backlog trend', sub: 'Pending and overdue documents', span: 6,
                legend: legend([{ name: 'Pending', color: 'var(--s1)', kind: 'line' }, { name: 'Overdue', color: 'var(--s2)', kind: 'line' }]),
                draw: function (h) { lineChart(h, xs, [{ name: 'Pending', color: 'var(--s1)', values: ser.map(function (x) { return x.pending; }) }, { name: 'Overdue', color: 'var(--s2)', values: ser.map(function (x) { return x.overdue; }) }], { label: 'Backlog trend' }); },
                data: function () { return { head: ['Date', 'Pending', 'Overdue', 'Due in 14 d', 'Not started'], num: [0, 1, 1, 1, 1], rows: ser.map(function (x) { return [fmtDay(x.d), x.pending, x.overdue, x.due14, x.notStarted]; }) }; }
            });
            card(grid, {
                title: 'M75 AFC trend', sub: 'AFC/AFX + ADH issued as a share of planned AFC', span: 6,
                draw: function (h) { lineChart(h, xs, [{ name: 'M75 AFC %', color: 'var(--s1)', values: ser.map(function (x) { return x.m75Pct; }) }], { yMax: 100, yFmt: pctFmt, refY: 75, refLabel: '75%', label: 'M75 trend' }); },
                data: function () { return { head: ['Date', 'Planned AFC', 'Issued', 'M75 %'], num: [0, 1, 1, 1], rows: ser.map(function (x) { return [fmtDay(x.d), x.m75p, x.m75d, fmt1(x.m75Pct)]; }) }; }
            });
            card(grid, { title: 'Snapshot history', sub: 'Newest first', span: 12, asTable: true, table: false, data: function () { var t = snapTable(ser); t.rows.reverse(); return t; } });
            var n = document.createElement('div'); n.className = 'note-row';
            n.textContent = 'Trend figures are the nightly snapshots (activity and cancelled rows excluded). The Discipline filter applies; other filters and the data date do not.';
            grid.appendChild(n);
        };
        function snapTable(ser) {
            return {
                head: ['Date', 'Documents', 'Complete', 'Pending', 'Overdue', 'Est. h', 'Earned h', 'Planned h', 'Earned %', 'Planned %', 'SPI', 'M75 %'],
                num: [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                rows: ser.map(function (x) { return [fmtDay(x.d), x.docs, x.complete, x.pending, x.overdue, fmtInt(x.est), fmtInt(x.earned), fmtInt(x.planned), fmt1(x.earnedPct), fmt1(x.plannedPct), x.spi == null ? '' : x.spi.toFixed(2), fmt1(x.m75Pct)]; })
            };
        }

        /* ---------------- Baseline ---------------- */
        VIEWS.baseline = function (grid, R) {
            if (!S.history) return setupCard(grid, 'Baselines');
            var cap = S.scope.project ? adminBtn('baseline', '📌 Capture baseline…') : '';
            if (!S.baselines.length) {
                var d0 = document.createElement('div'); d0.className = 'setup';
                d0.innerHTML = '<h3>No baseline captured yet</h3><p>A baseline freezes today\'s planned dates so later re-planning shows up as plan drift. ' +
                    (S.isAdmin ? (S.scope.project ? 'Capture one now:</p><p style="margin-top:10px">' + cap + '</p>' : 'Select a single project to capture one.</p>') : 'Ask a SmartDDR administrator to capture one.</p>');
                grid.appendChild(d0); return;
            }
            var sel = '<label class="inline-ctl">Baseline <select data-ctl="bl">' + S.baselines.map(function (b) {
                return '<option value="' + esc(b.BaselineName) + '"' + (b.BaselineName === S.blName ? ' selected' : '') + '>' + esc(b.BaselineName + ' · ' + String(b.CapturedAt || '').slice(0, 10)) + '</option>';
            }).join('') + '</select></label>';
            var B = R.filter(function (r) { return r.bl; }), drifts = B.map(function (r) { return r.docDrift; }).filter(function (v) { return v != null; });
            var later = drifts.filter(function (v) { return v > 0; }).length, earlier = drifts.filter(function (v) { return v < 0; }).length;
            var added = R.filter(function (r) { return !r.bl && !r.isAct; }).length;
            var info = S.baselines.find(function (b) { return b.BaselineName === S.blName; }) || {};
            kpiRow(grid,
                tile({ label: 'Baseline', ico: '📌', value: S.blName, sub: 'captured ' + esc(String(info.CapturedAt || '').slice(0, 10)) + (info.CapturedBy ? ' by ' + esc(info.CapturedBy) : '') }) +
                tile({ label: 'Documents compared', ico: '🔗', value: fmtInt(B.length), sub: fmtInt(S.blCount) + ' in baseline' }) +
                tile({ label: 'Moved later', ico: '⏩', value: fmtInt(later), sub: later ? statusOf('serious', fmtPct(pct(later, drifts.length)) + ' of compared') : statusOf('good', 'None') }) +
                tile({ label: 'Moved earlier', ico: '⏪', value: fmtInt(earlier) }) +
                tile({ label: 'Average drift', ico: '📏', value: drifts.length ? fmt1(avg(drifts)) + ' d' : '–', sub: 'median ' + (drifts.length ? fmt1(median(drifts)) : '–') + ' d · latest milestone' }) +
                tile({ label: 'Added since baseline', ico: '➕', value: fmtInt(added), sub: 'not in the baseline' }));
            var sc = sCurve(R);
            card(grid, {
                title: 'Baseline vs current plan vs actual', sub: 'Hours-weighted S-curves', span: 12, controls: sel + cap,
                legend: legend(curveLegend()),
                draw: function (h) { if (!sc) return emptyChart(h); lineChart(h, sc.xs, curveSeries(sc), { yMax: 100, height: 320, yFmt: function (v) { return Math.round(v) + '%'; }, today: S.asOf, label: 'Baseline S-curve' }); },
                data: function () { return sc ? { head: ['Week', 'Baseline %', 'Planned %', 'Actual %'], num: [0, 1, 1, 1], rows: sc.xs.map(function (x, i) { return [fmtDay(x), sc.baseline ? fmt1(sc.baseline[i]) : '', fmt1(sc.planned[i]), sc.actual[i] == null ? '' : fmt1(sc.actual[i])]; }) } : null; }
            });
            var ds = discRows(B).map(function (g) { var v = g.rows.map(function (r) { return r.docDrift; }).filter(function (x) { return x != null; }); return { disc: g.disc, avg: avg(v), n: v.length, later: v.filter(function (x) { return x > 0; }).length }; });
            card(grid, {
                title: 'Average plan drift by discipline', sub: 'Days the latest planned milestone moved (right = later) · click to filter', span: 6,
                legend: legend([{ name: 'Later than baseline', color: 'var(--div-neg)' }, { name: 'Earlier', color: 'var(--div-pos)' }]),
                draw: function (h) { barDiv(h, ds.map(function (d) { return { label: d.disc, value: d.avg, valueLabel: d.avg == null ? '' : (d.avg > 0 ? '+' : '') + fmt1(d.avg) + ' d', tip: tipRows(d.disc, [{ n: 'Average drift', v: d.avg == null ? '–' : fmt1(d.avg) + ' d' }, { n: 'Moved later', v: fmtInt(d.later) + ' of ' + fmtInt(d.n) }], 'Click to filter'), onClick: function () { setValueFilter('disc', d.disc === '(Blank)' ? '' : d.disc); } }; }), { posBad: true, tickFmt: function (t) { return fmt1(t) + ' d'; }, label: 'Drift by discipline' }); },
                data: function () { return { head: ['Discipline', 'Compared', 'Moved later', 'Average drift (d)'], num: [0, 1, 1, 1], rows: ds.map(function (d) { return [d.disc, d.n, d.later, d.avg == null ? '' : fmt1(d.avg)]; }) }; }
            });
            var bands = [{ l: '< −30', t: function (v) { return v < -30; } }, { l: '−30…−1', t: function (v) { return v < 0 && v >= -30; } }, { l: '0', t: function (v) { return v === 0; } },
                { l: '+1…14', t: function (v) { return v > 0 && v <= 14; } }, { l: '+15…30', t: function (v) { return v > 14 && v <= 30; } }, { l: '+31…60', t: function (v) { return v > 30 && v <= 60; } }, { l: '> +60', t: function (v) { return v > 60; } }];
            var bc = bands.map(function (b) { return drifts.filter(b.t).length; });
            card(grid, {
                title: 'Plan drift distribution', sub: 'Documents by days their latest planned milestone moved (negative = earlier)', span: 6,
                draw: function (h) { columns(h, bands.map(function (b) { return b.l; }), [{ name: 'Documents', color: 'var(--s1)', values: bc }], { height: 250, labelMax: true, label: 'Drift distribution' }); },
                data: function () { return { head: ['Drift', 'Documents'], num: [0, 1], rows: bands.map(function (b, i) { return [b.l, bc[i]]; }) }; }
            });
            var perStage = STAGES.map(function (st, i) { var v = B.map(function (r) { return r.drift[i]; }).filter(function (x) { return x != null; }); return { key: st.key, avg: avg(v), n: v.length }; }).filter(function (x) { return x.n; });
            card(grid, {
                title: 'Average drift by milestone', sub: 'Current plan minus baseline plan (days)', span: 6,
                draw: function (h) { barDiv(h, perStage.map(function (x) { return { label: x.key, value: x.avg, valueLabel: x.avg == null ? '' : (x.avg > 0 ? '+' : '') + fmt1(x.avg) + ' d', tip: tipRows(x.key, [{ n: 'Average drift', v: fmt1(x.avg) + ' d' }, { n: 'Documents', v: fmtInt(x.n) }]) }; }), { posBad: true, tickFmt: function (t) { return fmt1(t) + ' d'; }, label: 'Drift by milestone' }); },
                data: function () { return { head: ['Milestone', 'Documents', 'Average drift (d)'], num: [0, 1, 1], rows: perStage.map(function (x) { return [x.key, x.n, fmt1(x.avg)]; }) }; }
            });
            COL.blLast = { h: 'Baseline (latest)', v: function (r) { var i = lastBoth(r); return i < 0 ? '' : fmtDay(r.bl[i]); } };
            COL.curLast = { h: 'Current plan', v: function (r) { var i = lastBoth(r); return i < 0 ? '' : fmtDay(r.ms[i].p); } };
            COL.drift = { h: 'Drift (d)', v: function (r) { return r.docDrift == null ? '' : (r.docDrift > 0 ? '+' : '') + r.docDrift; }, num: true };
            var slipped = B.filter(function (r) { return r.docDrift > 0; }).sort(function (a, b) { return b.docDrift - a.docDrift; });
            docCard(grid, 'Most re-planned documents', 'Largest movement of the latest planned milestone versus the baseline', slipped, cols('proj', 'disc', 'doc', 'title', 'status', 'blLast', 'curLast', 'drift'), 6);
        };
        function lastBoth(r) { if (!r.drift) return -1; for (var i = 6; i >= 0; i--) if (r.drift[i] != null) return i; return -1; }

        /* =================================================================
           Saved views (server, shared) + shareable links
           ================================================================= */
        function getState() {
            var f = {}; for (var k in S.filters) { var x = S.filters[k]; f[k] = { v: x.vals ? Array.from(x.vals) : null, op: x.op, q: x.q }; }
            return { v: 1, t: S.tab, f: f, q: S.q, qc: S.qc, a: S.inclAct, c: S.inclCancel, ld: S.lookDays, ps: S.progStage, td: S.targetDays };
        }
        function assignState(st) {
            if (!st || typeof st !== 'object') return;
            S.filters = {};
            if (st.f) for (var k in st.f) { if (FIELDS[k]) { var x = st.f[k] || {}; S.filters[k] = { vals: Array.isArray(x.v) ? new Set(x.v.map(String)) : null, op: x.op, q: x.q ? String(x.q) : undefined }; if (!S.filters[k].vals && !S.filters[k].q) delete S.filters[k]; } }
            S.q = st.q ? String(st.q).toLowerCase() : ''; $('qsearch').value = S.q;
            S.qc = QUALITY.some(function (c) { return c.id === st.qc; }) ? st.qc : null;
            S.inclAct = !!st.a; $('tglAct').checked = S.inclAct;
            S.inclCancel = !!st.c; $('tglCancel').checked = S.inclCancel;
            if ([7, 14, 30, 60, 90].indexOf(+st.ld) >= 0) S.lookDays = +st.ld;
            if (STAGES.some(function (x) { return x.key === st.ps; })) S.progStage = st.ps;
            if (+st.td >= 1 && +st.td <= 365) S.targetDays = Math.round(+st.td);
            if (TABS.some(function (t) { return t.id === st.t; })) S.tab = st.t;
            S.reg.page = 0;
        }
        function applyState(st) { assignState(st); applyFilters(); render(); history.replaceState(null, '', location.pathname + location.search + '#' + S.tab); }
        function encodeState(st) { return btoa(unescape(encodeURIComponent(JSON.stringify(st)))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); }
        function decodeState(p) { try { p = String(p).replace(/-/g, '+').replace(/_/g, '/'); while (p.length % 4) p += '='; return JSON.parse(decodeURIComponent(escape(atob(p)))); } catch (e) { return null; } }
        function viewLink(st) {
            return location.origin + location.pathname + '?group=' + encodeURIComponent(S.scope.group) + (S.scope.project ? '&project=' + encodeURIComponent(S.scope.project) : '') + '&view=' + encodeState(st || getState());
        }
        function copyText(text, okMsg) {
            function fallback() { window.prompt('Copy this link:', text); }
            if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(function () { toast(okMsg || 'Copied'); }, fallback); else fallback();
        }
        function toast(msg) {
            var t = document.createElement('div'); t.className = 'banner info'; t.textContent = msg;
            t.style.cssText = 'position:fixed;right:18px;bottom:18px;z-index:700;box-shadow:var(--shadow);margin:0';
            document.body.appendChild(t); setTimeout(function () { t.remove(); }, 2600);
        }
        var localViewsKey = 'sddr.dash.views';
        function openViews(anchor) {
            var p = $('pop'); popState = { key: '__views' }; p.style.width = '420px';
            p.innerHTML = '<div class="pop-h">★ Saved views<button type="button" class="icon-btn x" data-pop="close" title="Close">✕</button></div>' +
                '<div class="pop-sub">Available views</div><div class="views-list" id="viewsList"><div class="note" style="padding:8px 10px">Loading…</div></div>' +
                '<div class="pop-sub">Save the current filters, tab and settings</div>' +
                '<div class="pop-row"><input type="text" id="viewName" maxlength="60" placeholder="View name, e.g. Piping – critical overdue" /></div>' +
                '<div class="pop-row"><label class="toggle"><input type="checkbox" id="viewShared" checked /> Share with everyone</label></div>' +
                '<div class="pop-f"><button type="button" class="btn" data-views="link">🔗 Copy link</button><button type="button" class="btn primary" data-views="save">Save view</button></div>';
            p.classList.add('open');
            var rc = anchor.getBoundingClientRect();
            p.style.left = Math.max(10, Math.min(rc.right - p.offsetWidth, window.innerWidth - p.offsetWidth - 10)) + 'px'; p.style.top = (rc.bottom + 6) + 'px';
            refreshViews();
        }
        function refreshViews() {
            api({ api: 'views' }).then(function (list) { S.viewsLocal = false; drawViews(list); })
                .catch(function (e) { S.viewsLocal = true; drawViews((store(localViewsKey) || []).map(function (v, i) { return { id: i, name: v.name, state: JSON.stringify(v.state), owner: 'this browser', shared: false, canDelete: true, local: true }; }), e.status === 501 ? 'Server views are not set up – views are saved in this browser only.' : null); });
        }
        function drawViews(list, note) {
            var host = $('viewsList'); if (!host) return;
            S.viewsCache = list;
            host.innerHTML = (note ? '<div class="note" style="padding:8px 10px;font-size:11.5px;color:var(--muted)">' + esc(note) + '</div>' : '') +
                (list.length ? list.map(function (v, i) {
                    return '<div class="vrow"><span class="nm" title="' + esc(v.name) + '">' + esc(v.name) + (v.shared ? '<span class="tag">shared</span>' : '') +
                        '<span class="meta">' + esc((v.scope ? v.scope + ' · ' : '') + (v.owner || '')) + '</span></span>' +
                        '<button type="button" class="btn" data-views="apply" data-i="' + i + '">Apply</button>' +
                        '<button type="button" class="icon-btn" data-views="copy" data-i="' + i + '" title="Copy link">🔗</button>' +
                        (v.canDelete ? '<button type="button" class="icon-btn" data-views="del" data-i="' + i + '" title="Delete">🗑</button>' : '') + '</div>';
                }).join('') : '<div class="note" style="padding:8px 10px;color:var(--muted);font-size:12px">No saved views yet.</div>');
        }
        function viewsAction(a, i) {
            var list = S.viewsCache || [], v = list[i];
            if (a === 'apply' && v) { closePop(); applyState(decodeStateJson(v.state)); toast('Applied view “' + v.name + '”'); }
            else if (a === 'copy' && v) copyText(viewLink(decodeStateJson(v.state)), 'Link to “' + v.name + '” copied');
            else if (a === 'link') copyText(viewLink(), 'Link to this view copied');
            else if (a === 'del' && v) {
                if (!window.confirm('Delete the view “' + v.name + '”?')) return;
                if (v.local) { var ls = store(localViewsKey) || []; ls.splice(v.id, 1); store(localViewsKey, ls); refreshViews(); return; }
                apiPost({ api: 'deleteview' }, { id: v.id }).then(refreshViews).catch(function (e) { toast(e.message); });
            }
            else if (a === 'save') {
                var name = ($('viewName').value || '').trim();
                if (!name || /[<>"'\\]/.test(name)) { $('viewName').focus(); toast('Enter a name (no < > " \' \\).'); return; }
                var st = getState(), shared = $('viewShared').checked;
                if (S.viewsLocal) {
                    var l2 = (store(localViewsKey) || []).filter(function (x) { return x.name !== name; }); l2.push({ name: name, state: st }); store(localViewsKey, l2);
                    $('viewName').value = ''; refreshViews(); toast('View saved in this browser'); return;
                }
                apiPost({ api: 'saveview' }, { name: name, scope: S.scope.project ? 'P:' + S.scope.project : 'G:' + S.scope.group, state: JSON.stringify(st), shared: shared ? '1' : '0' })
                    .then(function () { $('viewName').value = ''; refreshViews(); toast('View “' + name + '” saved' + (shared ? ' and shared' : '')); })
                    .catch(function (e) { toast(e.message); });
            }
        }
        function decodeStateJson(s) { try { return typeof s === 'string' ? JSON.parse(s) : s; } catch (e) { return null; } }

        /* =================================================================
           Admin actions
           ================================================================= */
        function adminAction(a) {
            if (a === 'snapshot') {
                if (!window.confirm('Take a snapshot for ' + (S.scope.project || 'every project') + ' now? (Today\'s snapshot is replaced.)')) return;
                $('loading').hidden = false; $('loadingText').textContent = 'Taking snapshot…';
                apiPost({ api: 'snapshot', project: S.scope.project || '' }).then(function () { toast('Snapshot taken'); return loadData(); })
                    .catch(function (e) { $('loading').hidden = true; toast(e.message); });
            } else if (a === 'baseline') {
                var t = new Date(), def = 'BL ' + t.getFullYear() + '-' + ('0' + (t.getMonth() + 1)).slice(-2) + '-' + ('0' + t.getDate()).slice(-2);
                var name = window.prompt('Baseline name for ' + S.scope.project + ' (an existing name is overwritten):', def);
                if (!name) return;
                $('loading').hidden = false; $('loadingText').textContent = 'Capturing baseline…';
                apiPost({ api: 'baseline', project: S.scope.project }, { name: name.trim() }).then(function () { toast('Baseline “' + name.trim() + '” captured'); return loadData(); })
                    .catch(function (e) { $('loading').hidden = true; toast(e.message); });
            }
        }
        function switchBaseline(name) {
            var params = S.scope.project ? { api: 'blrows', project: S.scope.project, name: name } : { api: 'blrows', group: S.scope.group, name: name };
            api(params).then(function (d) { setBaseline(d.baseline, d.blcols, d.blrows); render(); }).catch(function (e) { toast(e.message); });
        }

        /* =================================================================
           Excel (.xlsx) writer – SpreadsheetML in a stored ZIP, no library
           ================================================================= */
        var CRC_T = (function () { var t = new Uint32Array(256); for (var n = 0; n < 256; n++) { var c = n; for (var k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
        function crc32(b) { var c = 0xFFFFFFFF; for (var i = 0; i < b.length; i++) c = CRC_T[(c ^ b[i]) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; }
        function zipStore(files) {
            var enc = new TextEncoder(), parts = [], central = [], offset = 0, now = new Date();
            var dosTime = (now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1);
            var dosDate = ((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
            files.forEach(function (f) {
                var nm = enc.encode(f.name), data = typeof f.data === 'string' ? enc.encode(f.data) : f.data, crc = crc32(data), sz = data.length;
                var h = new DataView(new ArrayBuffer(30));
                h.setUint32(0, 0x04034b50, true); h.setUint16(4, 20, true); h.setUint16(6, 0x0800, true); h.setUint16(8, 0, true);
                h.setUint16(10, dosTime, true); h.setUint16(12, dosDate, true); h.setUint32(14, crc, true); h.setUint32(18, sz, true); h.setUint32(22, sz, true);
                h.setUint16(26, nm.length, true); h.setUint16(28, 0, true);
                parts.push(new Uint8Array(h.buffer), nm, data);
                var c = new DataView(new ArrayBuffer(46));
                c.setUint32(0, 0x02014b50, true); c.setUint16(4, 20, true); c.setUint16(6, 20, true); c.setUint16(8, 0x0800, true); c.setUint16(10, 0, true);
                c.setUint16(12, dosTime, true); c.setUint16(14, dosDate, true); c.setUint32(16, crc, true); c.setUint32(20, sz, true); c.setUint32(24, sz, true);
                c.setUint16(28, nm.length, true); c.setUint32(42, offset, true);
                central.push(new Uint8Array(c.buffer), nm);
                offset += 30 + nm.length + sz;
            });
            var cd = central.reduce(function (a, b) { return a + b.length; }, 0), e = new DataView(new ArrayBuffer(22));
            e.setUint32(0, 0x06054b50, true); e.setUint16(8, files.length, true); e.setUint16(10, files.length, true); e.setUint32(12, cd, true); e.setUint32(16, offset, true);
            return new Blob(parts.concat(central, [new Uint8Array(e.buffer)]), { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
        }
        function xmlEsc(v) { return String(v).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
        function colLetter(n) { var s = ''; n++; while (n > 0) { var m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); } return s; }
        var MON_IDX = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, oct: 9, nov: 10, dec: 11 };
        function parseShownDay(v) {   // "dd-Mon-yy" as shown on screen -> day number
            var m = /^(\d{1,2})-([A-Za-z]{3})-(\d{2,4})$/.exec(String(v || '').trim()); if (!m || MON_IDX[m[2].toLowerCase()] == null) return null;
            var y = +m[3]; if (y < 100) y += 2000; return Math.floor(Date.UTC(y, MON_IDX[m[2].toLowerCase()], +m[1]) / 864e5);
        }
        // A card's table -> sheet. Formatted numbers/dates on screen go back to real Excel numbers/dates.
        function sheetFromTable(title, d) {
            var NUMLIKE = /^[-+]?[\d,]*\.?\d+%?$/;
            var types = d.head.map(function (h, i) {
                if (d.num && d.num[i]) return 'n';
                var sample = d.rows.slice(0, 50).map(function (r) { return r[i] == null ? '' : String(r[i]).replace(/<[^>]+>/g, '').trim(); }).filter(function (v) { return v !== '' && v !== '–'; });
                if (!sample.length) return 's';
                if (sample.every(function (v) { return parseShownDay(v) != null; })) return 'd';
                if (sample.every(function (v) { return NUMLIKE.test(v); })) return 'n';   // e.g. progress bars "75.8%"
                return 's';
            });
            var rows = d.rows.map(function (r) {
                return r.map(function (v, i) {
                    var sv = v == null ? '' : String(v); if (d.html && d.html[i]) sv = sv.replace(/<[^>]+>/g, '').trim();
                    if (types[i] === 'n') { var n = parseFloat(sv.replace(/[,%\s+]/g, '').replace(/[–—]/g, '')); return isNaN(n) ? (sv === '–' ? null : sv) : n; }
                    if (types[i] === 'd') return parseShownDay(sv);
                    return sv;
                });
            });
            return { name: title, head: d.head, rows: rows, types: types, foot: d.foot };
        }
        function sheetXml(sh, info) {
            var n = sh.head.length, widths = sh.head.map(function (h) { return Math.min(60, Math.max(8, String(h).length + 2)); });
            var out = [], r = 1;
            function cell(v, t, style) {
                if (v == null || v === '') return '<c s="' + (style || 5) + '"/>';
                if (t === 'n' && typeof v === 'number' && isFinite(v)) return '<c s="' + (Number.isInteger(v) ? 4 : 3) + '"><v>' + v + '</v></c>';
                if (t === 'd' && typeof v === 'number') return '<c s="2"><v>' + (v + 25569) + '</v></c>';
                return '<c t="inlineStr" s="' + (style || 5) + '"><is><t xml:space="preserve">' + xmlEsc(v) + '</t></is></c>';
            }
            out.push('<row r="1" ht="24" customHeight="1">' + cell(sh.name, 's', 6) + '</row>');
            out.push('<row r="2">' + cell(info, 's', 7) + '</row>');
            out.push('<row r="3" ht="30" customHeight="1">' + sh.head.map(function (h) { return cell(h, 's', 1); }).join('') + '</row>');
            r = 3;
            sh.rows.forEach(function (row) {
                r++;
                out.push('<row r="' + r + '">' + row.map(function (v, i) {
                    if (v != null && sh.types[i] === 's') widths[i] = Math.min(60, Math.max(widths[i], String(v).length + 2));
                    return cell(v, sh.types[i]);
                }).join('') + '</row>');
            });
            if (sh.foot) { r++; out.push('<row r="' + r + '">' + sh.foot.map(function (v) { return cell(v, 's', 8); }).join('') + '</row>'); }
            var last = colLetter(Math.max(0, n - 1)), lastRow = 3 + sh.rows.length;
            return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' +
                '<sheetViews><sheetView workbookViewId="0" showGridLines="0"><pane ySplit="3" topLeftCell="A4" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>' +
                '<cols>' + widths.map(function (w, i) { return '<col min="' + (i + 1) + '" max="' + (i + 1) + '" width="' + w + '" customWidth="1"/>'; }).join('') + '</cols>' +
                '<sheetData>' + out.join('') + '</sheetData>' +
                (sh.rows.length ? '<autoFilter ref="A3:' + last + lastRow + '"/>' : '') + '</worksheet>';
        }
        var XLSX_STYLES = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' +
            '<numFmts count="2"><numFmt numFmtId="164" formatCode="dd-mmm-yyyy"/><numFmt numFmtId="165" formatCode="#,##0.0"/></numFmts>' +
            '<fonts count="5"><font><sz val="10"/><name val="Segoe UI"/></font><font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Segoe UI"/></font>' +
            '<font><b/><sz val="14"/><color rgb="FF0F766E"/><name val="Segoe UI"/></font><font><i/><sz val="9"/><color rgb="FF64748B"/><name val="Segoe UI"/></font><font><b/><sz val="10"/><name val="Segoe UI"/></font></fonts>' +
            '<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>' +
            '<fill><patternFill patternType="solid"><fgColor rgb="FF0F766E"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFE6F7F5"/><bgColor indexed="64"/></patternFill></fill></fills>' +
            '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left/><right/><top/><bottom style="thin"><color rgb="FFE2E8F0"/></bottom><diagonal/></border></borders>' +
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>' +
            '<cellXfs count="9">' +
            '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>' +
            '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>' +
            '<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center"/></xf>' +
            '<xf numFmtId="165" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>' +
            '<xf numFmtId="3" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>' +
            '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/>' +
            '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>' +
            '<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"/>' +
            '<xf numFmtId="0" fontId="4" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>' +
            '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>';
        function saveXlsx(fileName, sheets) {
            var info = 'SmartDDR · ' + (S.scope.project ? 'Project ' + S.scope.project : 'Group ' + ($('selGroup').selectedOptions[0] || {}).text) +
                ' · data date ' + fmtDay(S.asOf) + ' · exported ' + new Date().toLocaleString() + (Object.keys(S.filters).length || S.q || S.qc ? ' · filtered' : '');
            var used = {}, names = sheets.map(function (sh) {
                var n = String(sh.name || 'Sheet').replace(/[\[\]:*?\/\\]/g, ' ').trim().slice(0, 28) || 'Sheet', base = n, k = 2;
                while (used[n.toLowerCase()]) n = base.slice(0, 26) + ' ' + (k++);
                used[n.toLowerCase()] = 1; return n;
            });
            var files = [
                { name: '[Content_Types].xml', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + sheets.map(function (s, i) { return '<Override PartName="/xl/worksheets/sheet' + (i + 1) + '.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'; }).join('') + '</Types>' },
                { name: '_rels/.rels', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>' },
                { name: 'xl/workbook.xml', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + names.map(function (n, i) { return '<sheet name="' + xmlEsc(n) + '" sheetId="' + (i + 1) + '" r:id="rId' + (i + 1) + '"/>'; }).join('') + '</sheets><definedNames>' + sheets.map(function (sh, i) { return sh.rows.length ? '<definedName name="_xlnm._FilterDatabase" localSheetId="' + i + '" hidden="1">\'' + xmlEsc(names[i].replace(/'/g, "''")) + '\'!$A$3:$' + colLetter(Math.max(0, sh.head.length - 1)) + '$' + (3 + sh.rows.length) + '</definedName>' : ''; }).join('') + '</definedNames></workbook>' },
                { name: 'xl/_rels/workbook.xml.rels', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + sheets.map(function (s, i) { return '<Relationship Id="rId' + (i + 1) + '" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet' + (i + 1) + '.xml"/>'; }).join('') + '<Relationship Id="rId' + (sheets.length + 1) + '" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>' },
                { name: 'xl/styles.xml', data: XLSX_STYLES }
            ].concat(sheets.map(function (sh, i) { return { name: 'xl/worksheets/sheet' + (i + 1) + '.xml', data: sheetXml(Object.assign({}, sh, { name: names[i] }), info) }; }));
            var a = document.createElement('a'); a.href = URL.createObjectURL(zipStore(files));
            a.download = String(fileName).replace(/[^\w\-. ]+/g, '_') + '.xlsx';
            document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 800);
        }
        function registerSheet() {
            var list = regRows(), colsV = REG_COLS.filter(function (c) { return c.k !== 'proj' || !S.scope.project; });
            return { name: 'Register', head: colsV.map(function (c) { return c.h; }), types: colsV.map(function (c) { return c.day ? 'd' : (c.num ? 'n' : 's'); }), rows: list.map(function (r) { return colsV.map(function (c) { return c.v(r); }); }) };
        }
        function exportTab() {
            var label = (TABS.find(function (t) { return t.id === S.tab; }) || {}).label || 'Dashboard';
            var base = (S.scope.project || 'Group' + S.scope.group) + '_' + label.replace(/\W+/g, '_');
            if (S.tab === 'register') return saveXlsx(base, [registerSheet()]);
            var sheets = CARDS.map(function (c) { var d = c.data && c.data(); return d && d.rows && d.rows.length ? sheetFromTable(c.title, d) : null; }).filter(Boolean);
            sheets.push(registerSheet());   // the filtered register rides along with every tab export
            saveXlsx(base, sheets);
        }

        /* ---------------- Overview ---------------- */
        VIEWS.overview = function (grid, R) {
            var docs = R.filter(function (r) { return !r.isAct; });
            var done = docs.filter(function (r) { return r.complete; }).length;
            var pending = docs.length - done, od = R.filter(function (r) { return r.overdue; }), due14 = R.filter(function (r) { return r.dueIn != null && r.dueIn >= 0 && r.dueIn <= 14; }).length;
            var ea = earnedPct(R), pl = plannedPct(R), spi = (pl > 0 && ea != null) ? ea / pl : null;
            var hrs = sum(R, function (r) { return r.hrs; }), earned = sum(R, function (r) { return r.earned; });
            // Week-on-week deltas from the nightly snapshots (only when the view matches what a snapshot measures).
            var wk = compareBase();
            function dl(cur, key, upGood, isPct) { return wk ? ' ' + deltaHtml(cur, wk.prev[key], upGood, isPct) : ''; }
            var hero = document.createElement('div'); hero.className = 'hero';
            hero.innerHTML = '<div class="big"><div class="hero-flex"><div style="flex:1;min-width:0"><div class="lbl">Earned progress (hours-weighted)</div><div class="num">' + fmtPct(ea) + '</div></div>' + ringSvg(ea, pl) + '</div>' +
                '<div class="row"><span>Planned to date <b>' + fmtPct(pl) + '</b></span><span>Variance <b>' + (ea != null && pl != null ? (ea - pl >= 0 ? '+' : '') + fmt1(ea - pl) + ' pts' : '–') + '</b></span>' + dl(ea, 'earnedPct', true, true) + '</div>' +
                '<div class="row"><span>SPI <b>' + (spi == null ? '–' : spi.toFixed(2)) + '</b></span>' + spiStatus(spi) + (wk ? '<span style="opacity:.8">vs ' + esc(fmtDay(wk.prev.d)) + '</span>' : '') + '</div></div>' +
                '<div class="kpis">' +
                tile({ label: 'Documents', ico: '📄', value: fmtInt(docs.length), sub: fmtInt(R.length - docs.length) + ' activity rows', click: 'register' }) +
                tile({ label: 'Complete', ico: '✅', value: fmtInt(done), sub: fmtPct(pct(done, docs.length)) + ' of documents' + dl(done, 'complete', true), meter: pct(done, docs.length) }) +
                tile({ label: 'Pending', ico: '⏳', value: fmtInt(pending), sub: fmtInt(docs.filter(function (r) { return r.notStarted; }).length) + ' not started' + dl(pending, 'pending', false), click: 'pending' }) +
                tile({ label: 'Overdue', ico: '⚠️', value: fmtInt(od.length), sub: (od.length ? statusOf('critical', 'avg ' + fmt1(avg(od.map(function (r) { return r.daysLate; }))) + ' d late') : statusOf('good', 'None')) + dl(od.length, 'overdue', false), click: 'overdue' }) +
                tile({ label: 'Due in 14 days', ico: '📅', value: fmtInt(due14), sub: 'next milestone', click: 'lookahead' }) +
                tile({ label: 'Estimated hours', ico: '⏱️', value: compact(hrs), sub: 'Earned ' + compact(earned) + ' h' }) +
                '</div>';
            grid.appendChild(hero);

            var sc = sCurve(R);
            card(grid, {
                title: 'Progress S-curve', sub: 'Cumulative hours-weighted progress – planned vs actual' + (S.blName ? ' vs baseline “' + S.blName + '”' : ''), span: 8,
                legend: legend(curveLegend()),
                draw: function (h) { if (!sc) return emptyChart(h, 'No planned or actual dates.'); lineChart(h, sc.xs, curveSeries(sc), { yMax: 100, yFmt: function (v) { return Math.round(v) + '%'; }, today: S.asOf, label: 'Progress S-curve' }); },
                data: function () { return sc ? { head: ['Week', 'Planned %', 'Actual %'], num: [0, 1, 1], rows: sc.xs.map(function (x, i) { return [fmtDay(x), fmt1(sc.planned[i]), sc.actual[i] == null ? '' : fmt1(sc.actual[i])]; }) } : null; },
                foot: 'Rules of credit ' + (S.weightsInferred ? 'derived from the EPR weights' : '(default – EPR weights not available)') + ': ' + Object.keys(S.weights).map(function (k) { return k + ' ' + Math.round(S.weights[k] * 100) + '%'; }).join(' · ')
            });

            var st = groupBy(R, function (r) { return r.status; });
            var stItems = Array.from(st.keys()).map(function (k) { return { label: k, value: st.get(k).length }; }).sort(function (a, b) { return b.value - a.value; }).slice(0, 12);
            card(grid, {
                title: 'Documents by status', sub: 'Current status · click a bar to filter', span: 4,
                draw: function (h) { barH(h, stItems.map(function (i) { return Object.assign({}, i, { onClick: function () { setValueFilter('status', i.label === '(Blank)' ? '' : i.label); } }); }), { series: 'Documents', label: 'Documents by status' }); },
                data: function () { return { head: ['Status', 'Documents'], num: [0, 1], rows: stItems.map(function (i) { return [i.label, i.value]; }) }; }
            });

            var ms = STAGES.filter(function (s) { return !s.optional; });
            var pDue = ms.map(function (s) { var i = STAGES.indexOf(s); return R.filter(function (r) { return r.ms[i].p != null && r.ms[i].p <= S.asOf; }).length; });
            var aDone = ms.map(function (s) { var i = STAGES.indexOf(s); return R.filter(function (r) { return r.ms[i].a != null && r.ms[i].a <= S.asOf; }).length; });
            card(grid, {
                title: 'Milestone achievement to date', sub: 'Documents planned vs achieved by the data date', span: 6,
                legend: legend([{ name: 'Planned to date', color: 'var(--s1)' }, { name: 'Achieved', color: 'var(--s2)' }]),
                draw: function (h) { columns(h, ms.map(function (s) { return s.key; }), [{ name: 'Planned to date', color: 'var(--s1)', values: pDue }, { name: 'Achieved', color: 'var(--s2)', values: aDone }], { height: 250, label: 'Milestone achievement' }); },
                data: function () { return { head: ['Milestone', 'Planned to date', 'Achieved', 'Achieved %'], num: [0, 1, 1, 1], rows: ms.map(function (s, i) { return [s.key, pDue[i], aDone[i], fmtPct(pct(aDone[i], pDue[i]))]; }) }; }
            });

            var ds = discStats(discRows(R));
            card(grid, {
                title: 'Discipline progress', sub: 'Earned % (bar) vs planned % (tick) · click to filter', span: 6,
                legend: legend([{ name: 'Earned %', color: 'var(--s1)' }, { name: 'Planned %', kind: 'tick' }]),
                draw: function (h) { barH(h, ds.map(function (d) { return { label: d.disc, value: d.actual || 0, marker: d.planned, valueLabel: fmtPct(d.actual), onClick: function () { setValueFilter('disc', d.disc === '(Blank)' ? '' : d.disc); } }; }), { max: Math.max(100, Math.max.apply(null, ds.map(function (d) { return Math.max(d.actual || 0, d.planned || 0); }))), fmt: fmtPct, series: 'Earned %', markerName: 'Planned %', tickFmt: function (t) { return t + '%'; }, label: 'Discipline progress' }); },
                data: function () { return { head: ['Discipline', 'Planned %', 'Earned %', 'SPI'], num: [0, 1, 1, 1], rows: ds.map(function (d) { return [d.disc, fmt1(d.planned), fmt1(d.actual), d.spi == null ? '' : d.spi.toFixed(2)]; }) }; }
            });
        };

        /* ---------------- Pending ---------------- */
        VIEWS.pending = function (grid, R) {
            var P = R.filter(function (r) { return !r.complete; });
            var byGroup = STAGE_GROUPS.map(function (g) { return P.filter(function (r) { return r.nextGroup === g; }).length; });
            var unplanned = P.filter(function (r) { return r.next == null; }).length;
            kpiRow(grid,
                tile({ label: 'Pending documents', value: fmtInt(P.length), sub: fmtPct(pct(P.length, R.length)) + ' of scope' }) +
                tile({ label: 'Not started', value: fmtInt(P.filter(function (r) { return r.notStarted; }).length), sub: 'no actual milestone yet' }) +
                tile({ label: 'With client', value: fmtInt(P.filter(function (r) { return r.withClient != null; }).length), sub: 'issued, comments awaited', click: 'review' }) +
                tile({ label: 'Awaiting re-issue', value: fmtInt(P.filter(function (r) { return r.withUs != null; }).length), sub: 'comments received', click: 'review' }) +
                tile({ label: 'Next = AFC', value: fmtInt(byGroup[4]), sub: 'final issue pending' }) +
                tile({ label: 'Unplanned', value: fmtInt(unplanned), sub: unplanned ? statusOf('warning', 'no planned date') : statusOf('good', 'All planned') }));

            var dl = discRows(P);
            var groupsLegend = legend(STAGE_GROUPS.map(function (g, i) { return { name: g, color: ORD[i] }; }).concat([{ name: 'Unplanned', color: 'var(--muted)' }]));
            card(grid, {
                title: 'Pending by discipline and next milestone', sub: 'Stacked by the next milestone due · click to filter discipline', span: 7, legend: groupsLegend,
                draw: function (h) {
                    barH(h, dl.map(function (d) {
                        return {
                            label: d.disc, onClick: function () { setValueFilter('disc', d.disc === '(Blank)' ? '' : d.disc); },
                            segs: STAGE_GROUPS.map(function (g, i) { return { name: g, value: d.rows.filter(function (r) { return r.nextGroup === g; }).length, color: ORD[i] }; })
                                .concat([{ name: 'Unplanned', value: d.rows.filter(function (r) { return r.next == null; }).length, color: 'var(--muted)' }])
                        };
                    }), { label: 'Pending by discipline' });
                },
                data: function () { return { head: ['Discipline'].concat(STAGE_GROUPS, ['Unplanned', 'Total']), num: [0, 1, 1, 1, 1, 1, 1, 1], rows: dl.map(function (d) { return [d.disc].concat(STAGE_GROUPS.map(function (g) { return d.rows.filter(function (r) { return r.nextGroup === g; }).length; }), [d.rows.filter(function (r) { return r.next == null; }).length, d.rows.length]); }) }; }
            });
            card(grid, {
                title: 'Pending by next milestone', sub: 'Where the pending documents sit in the issue cycle', span: 5,
                draw: function (h) { columns(h, STAGE_GROUPS.concat(['Unplanned']), [{ name: 'Documents', color: 'var(--s1)', values: byGroup.concat([unplanned]) }], { height: 250, labelMax: true, label: 'Pending by next milestone' }); },
                data: function () { return { head: ['Next milestone', 'Documents'], num: [0, 1], rows: STAGE_GROUPS.map(function (g, i) { return [g, byGroup[i]]; }).concat([['Unplanned', unplanned]]) }; }
            });
            var sorted = P.slice().sort(function (a, b) { return (a.nextDue == null) - (b.nextDue == null) || a.nextDue - b.nextDue; });
            docCard(grid, 'Pending documents', 'Sorted by next due date', sorted, cols('proj', 'disc', 'doc', 'title', 'status', 'stage', 'next', 'due', 'dueIn', 'late'));
        };

        /* ---------------- Discipline performance ---------------- */
        VIEWS.discipline = function (grid, R) {
            var ds = discStats(discRows(R));
            var tot = discStats([{ disc: 'Total', rows: R }])[0];
            kpiRow(grid,
                tile({ label: 'Disciplines', value: fmtInt(ds.length) }) +
                tile({ label: 'Overall SPI', value: tot.spi == null ? '–' : tot.spi.toFixed(2), sub: spiStatus(tot.spi) }) +
                tile({ label: 'On-time milestone rate', value: fmtPct(tot.onTime), sub: 'actual ≤ planned' }) +
                tile({ label: 'Average slip when late', value: tot.avgSlip == null ? '–' : fmt1(tot.avgSlip) + ' d' }) +
                tile({ label: 'Behind plan', value: fmtInt(ds.filter(function (d) { return d.spi != null && d.spi < 0.95; }).length), sub: 'disciplines with SPI < 0.95' }));
            card(grid, {
                title: 'Discipline scorecard', sub: 'Click a row to filter the dashboard to that discipline', span: 12, asTable: true, table: false,
                data: function () {
                    return {
                        head: ['Discipline', 'Docs', 'Complete', 'Complete %', 'Planned %', 'Earned %', 'SPI', 'Status', 'Est. h', 'Earned h', 'Overdue', 'On-time %', 'Avg slip (d)'],
                        num: [0, 1, 1, 0, 1, 0, 1, 0, 1, 1, 1, 1, 1], html: [0, 0, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0, 0], click: true,
                        rows: ds.map(function (d) { return [d.disc, fmtInt(d.docs), fmtInt(d.done), pbar(d.donePct), fmt1(d.planned), pbar(d.actual), d.spi == null ? '–' : d.spi.toFixed(2), spiStatus(d.spi), fmtInt(d.hrs), fmtInt(d.earned), fmtInt(d.overdue), fmt1(d.onTime), d.avgSlip == null ? '–' : fmt1(d.avgSlip)]; }),
                        foot: ['Total', fmtInt(tot.docs), fmtInt(tot.done), fmtPct(tot.donePct), fmt1(tot.planned), fmtPct(tot.actual), tot.spi == null ? '–' : tot.spi.toFixed(2), '', fmtInt(tot.hrs), fmtInt(tot.earned), fmtInt(tot.overdue), fmt1(tot.onTime), tot.avgSlip == null ? '–' : fmt1(tot.avgSlip)],
                        onRow: function (i) { setValueFilter('disc', ds[i].disc === '(Blank)' ? '' : ds[i].disc); }
                    };
                }
            });
            card(grid, {
                title: 'Schedule performance (SPI – 1)', sub: 'Right of the axis = ahead of plan, left = behind', span: 6,
                legend: legend([{ name: 'Ahead', color: 'var(--div-pos)' }, { name: 'Behind', color: 'var(--div-neg)' }]),
                draw: function (h) { barDiv(h, ds.map(function (d) { return { label: d.disc, value: d.spi == null ? null : d.spi - 1, valueLabel: d.spi == null ? '' : d.spi.toFixed(2), tip: tipRows(d.disc, [{ n: 'SPI', v: d.spi == null ? '–' : d.spi.toFixed(2) }, { n: 'Planned %', v: fmtPct(d.planned) }, { n: 'Earned %', v: fmtPct(d.actual) }], 'Click to filter'), onClick: function () { setValueFilter('disc', d.disc === '(Blank)' ? '' : d.disc); } }; }), { tickFmt: function (t) { return (1 + t).toFixed(2); }, label: 'SPI by discipline' }); },
                data: function () { return { head: ['Discipline', 'SPI'], num: [0, 1], rows: ds.map(function (d) { return [d.disc, d.spi == null ? '' : d.spi.toFixed(2)]; }) }; }
            });
            card(grid, {
                title: 'Hours: estimated vs earned', sub: 'Earned hours (bar) against the estimate (tick)', span: 6,
                legend: legend([{ name: 'Earned h', color: 'var(--s1)' }, { name: 'Estimated h', kind: 'tick' }]),
                draw: function (h) { barH(h, ds.map(function (d) { return { label: d.disc, value: d.earned, marker: d.hrs, valueLabel: compact(d.earned) + ' / ' + compact(d.hrs) }; }), { series: 'Earned h', markerName: 'Estimated h', valW: 96, label: 'Hours by discipline' }); },
                data: function () { return { head: ['Discipline', 'Estimated h', 'Earned h', 'Earned %'], num: [0, 1, 1, 1], rows: ds.map(function (d) { return [d.disc, fmtInt(d.hrs), fmtInt(d.earned), fmtPct(pct(d.earned, d.hrs))]; }) }; }
            });
            card(grid, {
                title: 'On-time milestone rate', sub: 'Share of achieved milestones that met their planned date', span: 6,
                draw: function (h) { barH(h, ds.map(function (d) { return { label: d.disc, value: d.onTime || 0, valueLabel: fmtPct(d.onTime) }; }), { max: 100, fmt: fmtPct, series: 'On-time %', tickFmt: function (t) { return t + '%'; }, label: 'On-time rate' }); },
                data: function () { return { head: ['Discipline', 'On-time %'], num: [0, 1], rows: ds.map(function (d) { return [d.disc, fmt1(d.onTime)]; }) }; }
            });
            card(grid, {
                title: 'Overdue documents by discipline', sub: 'Next milestone past its planned date', span: 6,
                draw: function (h) { barH(h, ds.filter(function (d) { return d.overdue; }).sort(function (a, b) { return b.overdue - a.overdue; }).map(function (d) { return { label: d.disc, value: d.overdue, onClick: function () { setValueFilter('disc', d.disc === '(Blank)' ? '' : d.disc); go('overdue'); } }; }), { series: 'Overdue', label: 'Overdue by discipline' }); },
                data: function () { return { head: ['Discipline', 'Overdue'], num: [0, 1], rows: ds.map(function (d) { return [d.disc, d.overdue]; }) }; }
            });
        };

        /* ---------------- Overdue ---------------- */
        VIEWS.overdue = function (grid, R) {
            var O = R.filter(function (r) { return r.overdue; }).sort(function (a, b) { return b.daysLate - a.daysLate; });
            var pend = R.filter(function (r) { return !r.complete; }).length;
            var crit = O.filter(function (r) { return /HIGH|CRIT|^A$|^1$/i.test(r.crit || ''); }).length;
            kpiRow(grid,
                tile({ label: 'Overdue documents', value: fmtInt(O.length), sub: fmtPct(pct(O.length, pend)) + ' of pending' }) +
                tile({ label: 'Average days late', value: O.length ? fmt1(avg(O.map(function (r) { return r.daysLate; }))) : '–', sub: 'median ' + (O.length ? fmt1(median(O.map(function (r) { return r.daysLate; }))) : '–') }) +
                tile({ label: 'Over 30 days', value: fmtInt(O.filter(function (r) { return r.daysLate > 30; }).length), sub: O.some(function (r) { return r.daysLate > 30; }) ? statusOf('serious', 'escalate') : statusOf('good', 'None') }) +
                tile({ label: 'Over 90 days', value: fmtInt(O.filter(function (r) { return r.daysLate > 90; }).length), sub: O.filter(function (r) { return r.daysLate > 90; }).length ? statusOf('critical', 'critical') : statusOf('good', 'None') }) +
                tile({ label: 'High criticality overdue', value: fmtInt(crit), sub: 'criticality High / A / 1' }) +
                tile({ label: 'Oldest', value: O.length ? fmtInt(O[0].daysLate) + ' d' : '–', sub: O.length ? esc(O[0].doc) : '' }));

            var ages = AGE_BUCKETS.map(function (b, i) { var lo = i ? AGE_BUCKETS[i - 1].max : 0; return O.filter(function (r) { return r.daysLate > lo && r.daysLate <= b.max; }).length; });
            card(grid, {
                title: 'Overdue ageing', sub: 'Days past the planned date of the next milestone', span: 5,
                legend: legend(AGE_BUCKETS.map(function (b, i) { return { name: b.l, color: ORD[i] }; })),
                draw: function (h) {
                    // One column per bucket, coloured by the ordered (ordinal) ramp.
                    columns(h, AGE_BUCKETS.map(function (b) { return b.l; }), AGE_BUCKETS.map(function (b, i) { return { name: b.l, color: ORD[i], values: ages.map(function (v, j) { return j === i ? v : 0; }) }; }), { stacked: true, height: 250, labelMax: true, label: 'Overdue ageing' });
                },
                data: function () { return { head: ['Age', 'Documents'], num: [0, 1], rows: AGE_BUCKETS.map(function (b, i) { return [b.l, ages[i]]; }) }; }
            });

            var dl = discRows(O).map(function (d) { return d.disc; });
            var odN = new Map(); O.forEach(function (r) { var k = (r.disc || '(Blank)') + '\u0001' + r.nextGroup; odN.set(k, (odN.get(k) || 0) + 1); });
            var matrix = dl.map(function (d) { return STAGE_GROUPS.map(function (g) { return odN.get(d + '\u0001' + g) || 0; }); });
            card(grid, {
                title: 'Overdue heatmap – discipline × milestone', sub: 'Count of overdue documents by the milestone that is late · click a cell to filter', span: 7,
                draw: function (h) { heatmap(h, dl, STAGE_GROUPS, matrix, { onClick: function (d) { setValueFilter('disc', d === '(Blank)' ? '' : d); } }); },
                data: function () { return { head: ['Discipline'].concat(STAGE_GROUPS), num: [0, 1, 1, 1, 1, 1], rows: dl.map(function (d, i) { return [d].concat(matrix[i]); }) }; }
            });

            // Slippage on achieved milestones.
            var slip = STAGES.map(function (s, i) {
                var late = [], n = 0; R.forEach(function (r) { var m = r.ms[i]; if (m.x != null && m.p != null) { n++; if (m.x > m.p) late.push(m.x - m.p); } });
                return { key: s.key, n: n, late: late.length, avg: avg(late) };
            }).filter(function (x) { return x.n; });
            card(grid, {
                title: 'Late completions by milestone', sub: 'Achieved milestones that finished after plan – average slip in days', span: 6,
                draw: function (h) { barH(h, slip.map(function (s) { return { label: s.key, value: s.avg || 0, valueLabel: s.avg == null ? '0 d' : fmt1(s.avg) + ' d', tip: tipRows(s.key, [{ n: 'Achieved', v: fmtInt(s.n) }, { n: 'Late', v: fmtInt(s.late) + ' (' + fmtPct(pct(s.late, s.n)) + ')' }, { n: 'Average slip', v: s.avg == null ? '–' : fmt1(s.avg) + ' d' }]) }; }), { fmt: fmt1, series: 'Average slip (d)', label: 'Average slip by milestone' }); },
                data: function () { return { head: ['Milestone', 'Achieved', 'Late', 'Late %', 'Avg slip (d)'], num: [0, 1, 1, 1, 1], rows: slip.map(function (s) { return [s.key, s.n, s.late, fmt1(pct(s.late, s.n)), s.avg == null ? '' : fmt1(s.avg)]; }) }; }
            });
            var byCrit = groupBy(O, function (r) { return r.crit; });
            var critItems = Array.from(byCrit.keys()).sort().map(function (k) { return { label: k, value: byCrit.get(k).length, onClick: function () { setValueFilter('crit', k === '(Blank)' ? '' : k); } }; });
            card(grid, {
                title: 'Overdue by criticality', sub: 'Click to filter', span: 6,
                draw: function (h) { barH(h, critItems, { series: 'Overdue', label: 'Overdue by criticality' }); },
                data: function () { return { head: ['Criticality', 'Overdue'], num: [0, 1], rows: critItems.map(function (i) { return [i.label, i.value]; }) }; }
            });
            docCard(grid, 'Overdue documents', 'Most overdue first', O, cols('proj', 'disc', 'doc', 'title', 'crit', 'status', 'next', 'due', 'late'));
        };

        /* ---------------- Look-ahead ---------------- */
        VIEWS.lookahead = function (grid, R) {
            var N = S.lookDays;
            var L = R.filter(function (r) { return r.dueIn != null && r.dueIn >= 0 && r.dueIn <= N; }).sort(function (a, b) { return a.nextDue - b.nextDue; });
            var ctl = '<label class="inline-ctl">Window <select data-ctl="look">' + [7, 14, 30, 60, 90].map(function (d) { return '<option value="' + d + '"' + (d === N ? ' selected' : '') + '>' + d + ' days</option>'; }).join('') + '</select></label>';
            kpiRow(grid,
                tile({ label: 'Due in next ' + N + ' days', value: fmtInt(L.length), sub: 'next milestone due' }) +
                STAGE_GROUPS.map(function (g) { return tile({ label: g + ' due', value: fmtInt(L.filter(function (r) { return r.nextGroup === g; }).length) }); }).join(''));
            var weeks = []; for (var d = weekStart(S.asOf); d <= S.asOf + N; d += 7) weeks.push(d);
            var wk = weeks.map(function (w) { return STAGE_GROUPS.map(function (g) { return L.filter(function (r) { return r.nextGroup === g && r.nextDue >= w && r.nextDue < w + 7; }).length; }); });
            card(grid, {
                title: 'Look-ahead by week', sub: 'Next milestone due per week (week starting)', span: 7, controls: ctl,
                legend: legend(STAGE_GROUPS.map(function (g, i) { return { name: g, color: ORD[i] }; })),
                draw: function (h) { columns(h, weeks.map(fmtDay), STAGE_GROUPS.map(function (g, i) { return { name: g, color: ORD[i], values: wk.map(function (w) { return w[i]; }) }; }), { stacked: true, height: 260, label: 'Look-ahead' }); },
                data: function () { return { head: ['Week starting'].concat(STAGE_GROUPS), num: [0, 1, 1, 1, 1, 1], rows: weeks.map(function (w, i) { return [fmtDay(w)].concat(wk[i]); }) }; }
            });
            var dl = discRows(L);
            card(grid, {
                title: 'Look-ahead by discipline', sub: 'Click to filter', span: 5,
                draw: function (h) { barH(h, dl.map(function (d) { return { label: d.disc, value: d.rows.length, onClick: function () { setValueFilter('disc', d.disc === '(Blank)' ? '' : d.disc); } }; }), { series: 'Due', label: 'Look-ahead by discipline' }); },
                data: function () { return { head: ['Discipline', 'Due'], num: [0, 1], rows: dl.map(function (d) { return [d.disc, d.rows.length]; }) }; }
            });
            docCard(grid, 'Upcoming milestones', 'Documents whose next milestone falls inside the window', L, cols('proj', 'disc', 'doc', 'title', 'status', 'next', 'due', 'dueIn', 'crit'));
        };

        /* ---------------- Progress & S-curve ---------------- */
        VIEWS.progress = function (grid, R) {
            var si = STAGES.findIndex(function (s) { return s.key === S.progStage; }); if (si < 0) si = 1;
            var ctl = '<label class="inline-ctl">Milestone <select data-ctl="stage">' + STAGES.map(function (s) { return '<option' + (s.key === STAGES[si].key ? ' selected' : '') + '>' + s.key + '</option>'; }).join('') + '</select></label>';
            var P = R.filter(function (r) { return r.ms[si].p != null; }), A = R.filter(function (r) { return r.ms[si].a != null; });
            var pDate = P.filter(function (r) { return r.ms[si].p <= S.asOf; }).length, aDate = A.filter(function (r) { return r.ms[si].a <= S.asOf; }).length;
            var ea = earnedPct(R), pl = plannedPct(R);
            kpiRow(grid,
                tile({ label: 'Earned progress', value: fmtPct(ea), meter: ea }) +
                tile({ label: 'Planned progress', value: fmtPct(pl), meter: pl }) +
                tile({ label: 'SPI', value: (pl > 0 && ea != null) ? (ea / pl).toFixed(2) : '–', sub: spiStatus(pl > 0 && ea != null ? ea / pl : null) }) +
                tile({ label: STAGES[si].key + ' planned to date', value: fmtInt(pDate), sub: 'of ' + fmtInt(P.length) + ' planned' }) +
                tile({ label: STAGES[si].key + ' achieved', value: fmtInt(aDate), sub: aDate >= pDate ? statusOf('good', 'on / ahead of plan') : statusOf('serious', fmtInt(pDate - aDate) + ' behind') }));

            var sc = sCurve(R);
            card(grid, {
                title: 'Weighted progress S-curve', sub: 'Hours-weighted, rules of credit applied', span: 12,
                legend: legend([{ name: 'Planned', color: 'var(--s1)', kind: 'line' }, { name: 'Actual', color: 'var(--s2)', kind: 'line' }]),
                draw: function (h) { if (!sc) return emptyChart(h); lineChart(h, sc.xs, [{ name: 'Planned', color: 'var(--s1)', values: sc.planned }, { name: 'Actual', color: 'var(--s2)', values: sc.actual, area: true }], { yMax: 100, height: 320, yFmt: function (v) { return Math.round(v) + '%'; }, today: S.asOf, label: 'Weighted S-curve' }); },
                data: function () { return sc ? { head: ['Week', 'Planned %', 'Actual %'], num: [0, 1, 1], rows: sc.xs.map(function (x, i) { return [fmtDay(x), fmt1(sc.planned[i]), sc.actual[i] == null ? '' : fmt1(sc.actual[i])]; }) } : null; }
            });

            // Cumulative count curve + monthly throughput for the chosen milestone.
            var days = []; R.forEach(function (r) { if (r.ms[si].p != null) days.push(r.ms[si].p); if (r.ms[si].x != null) days.push(r.ms[si].x); });
            days.sort(function (a, b) { return a - b; });
            var mk = [], pm = [], am = [], baseP = 0, baseA = 0;
            if (days.length) {
                // At most 48 months shown, never more than 36 months past the data date (guards against a
                // stray 2099 date); anything earlier still counts as the cumulative starting point.
                var m1 = Math.min(monthKey(days[days.length - 1]), monthKey(S.asOf) + 36);
                var m0 = Math.max(monthKey(days[0]), m1 - 47);
                if (m0 > m1) m0 = m1;
                for (var k = m0; k <= m1; k++) { mk.push(k); pm.push(0); am.push(0); }
                R.forEach(function (r) {
                    var m = r.ms[si];
                    if (m.p != null) { var i = monthKey(m.p) - m0; if (i < 0) baseP++; else if (i < mk.length) pm[i]++; }
                    if (m.x != null) { var j = monthKey(m.x) - m0; if (j < 0) baseA++; else if (j < mk.length) am[j]++; }
                });
            }
            var cumP = [], cumA = [], cp = baseP, ca = baseA, cutM = monthKey(S.asOf);
            mk.forEach(function (k, i) { cp += pm[i]; ca += am[i]; cumP.push(cp); cumA.push(k <= cutM ? ca : null); });
            card(grid, {
                title: STAGES[si].key + ' – cumulative documents', sub: 'Planned vs actual issues (count)', span: 6, controls: ctl,
                legend: legend([{ name: 'Planned', color: 'var(--s1)', kind: 'line' }, { name: 'Actual', color: 'var(--s2)', kind: 'line' }]),
                draw: function (h) { lineChart(h, mk.map(monthStart), [{ name: 'Planned', color: 'var(--s1)', values: cumP }, { name: 'Actual', color: 'var(--s2)', values: cumA }], { today: S.asOf, xFmt: function (d) { return monthLabel(monthKey(d)); }, label: 'Cumulative ' + STAGES[si].key }); },
                data: function () { return { head: ['Month', 'Planned (cum.)', 'Actual (cum.)'], num: [0, 1, 1], rows: mk.map(function (k, i) { return [monthLabel(k), cumP[i], cumA[i] == null ? '' : cumA[i]]; }) }; }
            });
            card(grid, {
                title: STAGES[si].key + ' – monthly throughput', sub: 'Documents planned vs achieved per month', span: 6,
                legend: legend([{ name: 'Planned', color: 'var(--s1)' }, { name: 'Actual', color: 'var(--s2)' }]),
                draw: function (h) { columns(h, mk.map(monthLabel), [{ name: 'Planned', color: 'var(--s1)', values: pm }, { name: 'Actual', color: 'var(--s2)', values: am }], { height: 260, label: 'Monthly throughput' }); },
                data: function () { return { head: ['Month', 'Planned', 'Actual', 'Variance'], num: [0, 1, 1, 1], rows: mk.map(function (k, i) { return [monthLabel(k), pm[i], am[i], am[i] - pm[i]]; }) }; }
            });
        };

        /* ---------------- M75 AFC ---------------- */
        VIEWS.m75 = function (grid) {
            // Aggregated source: only Discipline and Project filters can apply.
            var f = S.filters, M = S.m75.filter(function (m) {
                var okD = !f.disc || passField({ disc: m.disc }, 'disc', f.disc);
                var okP = !f.proj || passField({ proj: m.proj }, 'proj', f.proj);
                return okD && okP;
            });
            var byD = groupBy(M, function (m) { return m.disc; });
            var ds = Array.from(byD.keys()).sort(discSort).map(function (k) { var a = byD.get(k); return { disc: k, plan: sum(a, function (x) { return x.plan; }), afcx: sum(a, function (x) { return x.afcx; }), adh: sum(a, function (x) { return x.adh; }) }; });
            var plan = sum(ds, function (d) { return d.plan; }), afcx = sum(ds, function (d) { return d.afcx; }), adh = sum(ds, function (d) { return d.adh; });
            var done = afcx + adh, p = pct(done, plan);
            kpiRow(grid,
                tile({ label: 'Planned AFC', value: fmtInt(plan) }) +
                tile({ label: 'AFC / AFX issued', value: fmtInt(afcx) }) +
                tile({ label: 'ADH issued', value: fmtInt(adh) }) +
                tile({ label: 'Balance', value: fmtInt(plan - done), sub: plan - done > 0 ? statusOf('warning', 'to issue') : statusOf('good', 'None') }) +
                tile({ label: 'M75 AFC achieved', value: fmtPct(p), meter: p, sub: p >= 75 ? statusOf('good', '≥ 75% target met') : statusOf('serious', 'below 75% target') }));
            card(grid, {
                title: 'M75 AFC by discipline', sub: 'Issued (stacked) against planned AFC (tick) · click to filter', span: 7,
                legend: legend([{ name: 'AFC / AFX', color: 'var(--s1)' }, { name: 'ADH', color: 'var(--s2)' }, { name: 'Planned AFC', kind: 'tick' }]),
                draw: function (h) { barH(h, ds.map(function (d) { return { label: d.disc, marker: d.plan, valueLabel: fmtInt(d.afcx + d.adh) + ' / ' + fmtInt(d.plan), segs: [{ name: 'AFC / AFX', value: d.afcx, color: 'var(--s1)' }, { name: 'ADH', value: d.adh, color: 'var(--s2)' }], onClick: function () { setValueFilter('disc', d.disc === '(Blank)' ? '' : d.disc); } }; }), { markerName: 'Planned AFC', valW: 90, label: 'M75 AFC by discipline' }); },
                data: function () { return { head: ['Discipline', 'Planned', 'AFC/AFX', 'ADH', 'Balance', 'Achieved %'], num: [0, 1, 1, 1, 1, 1], rows: ds.map(function (d) { return [d.disc, d.plan, d.afcx, d.adh, d.plan - d.afcx - d.adh, fmt1(pct(d.afcx + d.adh, d.plan))]; }), foot: ['Total', plan, afcx, adh, plan - done, fmt1(p)] }; }
            });
            card(grid, {
                title: 'M75 achievement %', sub: 'Issued as a share of planned AFC; the tick marks the 75% milestone', span: 5,
                legend: legend([{ name: 'Achieved %', color: 'var(--s1)' }, { name: '75% milestone', kind: 'tick' }]),
                draw: function (h) { barH(h, ds.map(function (d) { var v = pct(d.afcx + d.adh, d.plan); return { label: d.disc, value: v || 0, marker: 75, valueLabel: fmtPct(v) }; }), { max: 100, fmt: fmtPct, series: 'Achieved %', markerName: 'Milestone', tickFmt: function (t) { return t + '%'; }, label: 'M75 achievement' }); },
                data: function () { return { head: ['Discipline', 'Achieved %'], num: [0, 1], rows: ds.map(function (d) { return [d.disc, fmt1(pct(d.afcx + d.adh, d.plan))]; }) }; }
            });
            var note = document.createElement('div'); note.className = 'note-row';
            note.textContent = 'M75 figures come from the aggregated SDDR_ACON_AFC_STATUS_01 view, so only the Discipline and Project filters apply to this page.';
            grid.appendChild(note);
        };

        /* ---------------- Review cycle ---------------- */
        VIEWS.review = function (grid, R) {
            var T = S.targetDays;
            var ctl = '<label class="inline-ctl">Target <input type="number" min="1" max="120" data-ctl="target" value="' + T + '" /> days</label>';
            var c1 = R.map(function (r) { return r.rev1; }).filter(function (v) { return v != null; });
            var ri = R.map(function (r) { return r.reissue; }).filter(function (v) { return v != null; });
            var c2 = R.map(function (r) { return r.rev2; }).filter(function (v) { return v != null; });
            var withC = R.filter(function (r) { return r.withClient != null; }).sort(function (a, b) { return b.withClient - a.withClient; });
            var withU = R.filter(function (r) { return r.withUs != null; }).sort(function (a, b) { return b.withUs - a.withUs; });
            kpiRow(grid,
                tile({ label: 'With client now', value: fmtInt(withC.length), sub: fmtInt(withC.filter(function (r) { return r.withClient > T; }).length) + ' beyond ' + T + ' d' }) +
                tile({ label: 'Awaiting our re-issue', value: fmtInt(withU.length), sub: fmtInt(withU.filter(function (r) { return r.withUs > T; }).length) + ' beyond ' + T + ' d' }) +
                tile({ label: 'Client review (1st)', value: c1.length ? fmt1(avg(c1)) + ' d' : '–', sub: 'avg IFR → RCC · median ' + (c1.length ? fmt1(median(c1)) : '–') }) +
                tile({ label: 'Re-issue turnaround', value: ri.length ? fmt1(avg(ri)) + ' d' : '–', sub: 'avg RCC → IFR2' }) +
                tile({ label: 'Client review (2nd)', value: c2.length ? fmt1(avg(c2)) + ' d' : '–', sub: 'avg IFR2 → RCC2' }) +
                tile({ label: 'Reviews over target', value: fmtPct(pct(c1.filter(function (v) { return v > T; }).length, c1.length)), sub: c1.length ? 'of first-cycle reviews' : '' }));
            var dl = discRows(R).map(function (d) { var a = d.rows.map(function (r) { return r.rev1; }).filter(function (v) { return v != null; }), b = d.rows.map(function (r) { return r.reissue; }).filter(function (v) { return v != null; }); return { disc: d.disc, c1: avg(a), n1: a.length, ri: avg(b), n2: b.length }; }).filter(function (d) { return d.n1 || d.n2; });
            card(grid, {
                title: 'Average client review time by discipline', sub: 'IFR → RCC (days); the tick is the target', span: 6, controls: ctl,
                legend: legend([{ name: 'Client review (d)', color: 'var(--s1)' }, { name: 'Target', kind: 'tick' }]),
                draw: function (h) { barH(h, dl.map(function (d) { return { label: d.disc, value: d.c1 || 0, marker: T, valueLabel: d.c1 == null ? '–' : fmt1(d.c1) + ' d', tip: tipRows(d.disc, [{ n: 'Average', v: d.c1 == null ? '–' : fmt1(d.c1) + ' d' }, { n: 'Reviews', v: fmtInt(d.n1) }, { n: 'Target', v: T + ' d' }]) }; }), { fmt: fmt1, markerName: 'Target', label: 'Client review time' }); },
                data: function () { return { head: ['Discipline', 'Client review avg (d)', 'Reviews', 'Re-issue avg (d)', 'Re-issues'], num: [0, 1, 1, 1, 1], rows: dl.map(function (d) { return [d.disc, d.c1 == null ? '' : fmt1(d.c1), d.n1, d.ri == null ? '' : fmt1(d.ri), d.n2]; }) }; }
            });
            var bands = [{ l: '0–7 d', max: 7 }, { l: '8–14 d', max: 14 }, { l: '15–21 d', max: 21 }, { l: '22–30 d', max: 30 }, { l: '> 30 d', max: Infinity }];
            var dist = bands.map(function (b, i) { var lo = i ? bands[i - 1].max : -1; return c1.filter(function (v) { return v > lo && v <= b.max; }).length; });
            card(grid, {
                title: 'Client review time distribution', sub: 'First review cycle, completed reviews', span: 6,
                legend: legend(bands.map(function (b, i) { return { name: b.l, color: ORD[i] }; })),
                draw: function (h) { columns(h, bands.map(function (b) { return b.l; }), bands.map(function (b, i) { return { name: b.l, color: ORD[i], values: dist.map(function (v, j) { return j === i ? v : 0; }) }; }), { stacked: true, height: 250, labelMax: true, label: 'Review time distribution' }); },
                data: function () { return { head: ['Duration', 'Reviews'], num: [0, 1], rows: bands.map(function (b, i) { return [b.l, dist[i]]; }) }; }
            });
            COL.withC = { h: 'Days with client', v: function (r) { return r.withClient; }, num: true };
            COL.withU = { h: 'Days waiting', v: function (r) { return r.withUs; }, num: true };
            docCard(grid, 'With client – comments awaited', 'Issued for review, no comments returned yet (longest first)', withC, cols('proj', 'disc', 'doc', 'title', 'status', 'withC'), 6);
            docCard(grid, 'Awaiting re-issue', 'Comments received, not yet re-issued (longest first)', withU, cols('proj', 'disc', 'doc', 'title', 'status', 'withU'), 6);
        };

        /* ---------------- Handover ---------------- */
        VIEWS.handover = function (grid, R) {
            var H = R.filter(function (r) { return r.reqd; });
            var ready = H.filter(function (r) { return r.ready; }).length;
            kpiRow(grid,
                tile({ label: 'Documents with a required status', value: fmtInt(H.length), sub: fmtInt(R.length - H.length) + ' without' }) +
                tile({ label: 'Handover-ready', value: fmtInt(ready), sub: 'current status = required', meter: pct(ready, H.length) }) +
                tile({ label: 'Readiness', value: fmtPct(pct(ready, H.length)) }) +
                tile({ label: 'Not ready', value: fmtInt(H.length - ready), sub: H.length - ready ? statusOf('warning', 'action needed') : statusOf('good', 'All ready') }));
            var cellN = new Map(), stN = new Map(), reqSet = new Set();
            H.forEach(function (r) {
                var q = up(r.reqd), st = up(r.status) || '(Blank)';
                reqSet.add(q); stN.set(st, (stN.get(st) || 0) + 1);
                cellN.set(q + '\u0001' + st, (cellN.get(q + '\u0001' + st) || 0) + 1);
            });
            var reqs = Array.from(reqSet).sort();
            var sts = Array.from(stN.keys()).sort(function (a, b) { return stN.get(b) - stN.get(a); }).slice(0, 10);
            var mtx = reqs.map(function (q) { return sts.map(function (st) { return cellN.get(q + '\u0001' + st) || 0; }); });
            card(grid, {
                title: 'Required vs current status', sub: 'Rows = status required for handover, columns = current status (top 10)', span: 7,
                draw: function (h) { heatmap(h, reqs, sts, mtx, { onClick: function (q) { S.filters.reqd = { q: q, op: 'eq' }; S.reg.page = 0; applyFilters(); render(); } }); },
                data: function () { return { head: ['Required \\ Current'].concat(sts), num: [0].concat(sts.map(function () { return 1; })), rows: reqs.map(function (q, i) { return [q].concat(mtx[i]); }) }; }
            });
            var dl = discRows(H).map(function (d) { var rd = d.rows.filter(function (r) { return r.ready; }).length; return { disc: d.disc, n: d.rows.length, ready: rd, p: pct(rd, d.rows.length) }; });
            card(grid, {
                title: 'Handover readiness by discipline', sub: 'Share of documents at their required status', span: 5,
                draw: function (h) { barH(h, dl.map(function (d) { return { label: d.disc, value: d.p || 0, valueLabel: fmtPct(d.p), onClick: function () { setValueFilter('disc', d.disc === '(Blank)' ? '' : d.disc); } }; }), { max: 100, fmt: fmtPct, series: 'Ready %', tickFmt: function (t) { return t + '%'; }, label: 'Readiness' }); },
                data: function () { return { head: ['Discipline', 'Documents', 'Ready', 'Ready %'], num: [0, 1, 1, 1], rows: dl.map(function (d) { return [d.disc, d.n, d.ready, fmt1(d.p)]; }) }; }
            });
            docCard(grid, 'Not handover-ready', 'Required status not yet reached', H.filter(function (r) { return !r.ready; }), cols('proj', 'disc', 'doc', 'title', 'reqd', 'status', 'next', 'due'));
        };

        /* ---------------- Data quality ---------------- */
        QUALITY = [
            { id: 'noplan', label: 'No planned dates', d: 'Pending documents without any planned milestone date', level: 'critical', test: function (r) { return r.noPlan; } },
            { id: 'actnoplan', label: 'Actual without plan', d: 'A milestone has an actual date but no planned date', level: 'serious', test: function (r) { return r.actNoPlan; } },
            { id: 'seq', label: 'Out-of-sequence actuals', d: 'A later milestone was achieved before an earlier one', level: 'serious', test: function (r) { return r.outOfSeq; } },
            { id: 'future', label: 'Actual date in the future', d: 'An actual date is later than today', level: 'critical', test: function (r) { return r.futureAct; } },
            { id: 'dummy', label: 'Placeholder document no.', d: 'Document number contains DUMMY or XXX', level: 'serious', test: function (r) { return /DUMMY|XXX/i.test(r.doc || ''); } },
            { id: 'dup', label: 'Duplicate document no.', d: 'Same document number appears more than once in a project', level: 'serious', test: function (r) { return r.dup; } },
            { id: 'noplip', label: 'Missing PLIP ID', d: 'PLIP ID is blank', level: 'warning', test: function (r) { return !r.plip; } },
            { id: 'noramz', label: 'Missing RAMZ ID', d: 'RAMZ ID is blank', level: 'warning', test: function (r) { return !r.ramz; } },
            { id: 'nohrs', label: 'No estimated hours', d: 'Hours are zero or blank', level: 'warning', test: function (r) { return !r.hrs; } },
            { id: 'nostatus', label: 'No current status', d: 'CURR_STATUS is blank', level: 'warning', test: function (r) { return !r.status; } },
            { id: 'noreqd', label: 'No required status', d: 'STATUS_REQD is blank', level: 'warning', test: function (r) { return !r.reqd; } },
            { id: 'afcnodate', label: 'AFC status without AFC date', d: 'Status AFC/AFX but AFC actual date blank', level: 'warning', test: function (r) { return /^(AFC|AFX)$/i.test(r.status || '') && r.ms[6].a == null; } }
        ];
        VIEWS.quality = function (grid) {
            var base = baseRows().filter(function (r) { return pass(r, '__qc'); });
            var wrap = document.createElement('section'); wrap.className = 'card span-12';
            wrap.innerHTML = '<div class="card-h"><div><h3>Data quality checks</h3><p>Counts within the current filters · click a check to list the documents in the Register</p></div></div><div class="card-b"><div class="qc">' +
                QUALITY.map(function (c) {
                    var n = base.filter(c.test).length;
                    return '<div class="qc-item" data-qc="' + c.id + '" tabindex="0" role="button"><div class="n">' + fmtInt(n) + '</div><div>' + (n ? statusOf(c.level, c.label) : statusOf('good', c.label)) + '</div><div class="d">' + esc(c.d) + '</div></div>';
                }).join('') + '</div></div>';
            grid.appendChild(wrap);
        };

        /* ---------------- Register (Excel-like table) ---------------- */
        var REG_COLS = [
            { k: 'proj', h: 'Project', f: 'proj', v: function (r) { return r.proj; } },
            { k: 'disc', h: 'Discipline', f: 'disc', v: function (r) { return r.disc; } },
            { k: 'doc', h: 'Document No', f: 'doc', v: function (r) { return r.doc; } },
            { k: 'title', h: 'Document Title', f: 'title', v: function (r) { return r.title; }, wrap: true },
            { k: 'ramz', h: 'RAMZ ID', f: 'ramz', v: function (r) { return r.ramz; } },
            { k: 'plip', h: 'PLIP ID', f: 'plip', v: function (r) { return r.plip; } },
            { k: 'rev', h: 'Rev', f: 'rev', v: function (r) { return r.rev; } },
            { k: 'crit', h: 'Criticality', f: 'crit', v: function (r) { return r.crit; } },
            { k: 'reqd', h: 'Status Req.', f: 'reqd', v: function (r) { return r.reqd; } },
            { k: 'status', h: 'Status', f: 'status', v: function (r) { return r.status; } },
            { k: 'stage', h: 'Last stage', v: function (r) { return r.stageLabel; } },
            { k: 'next', h: 'Next', f: 'next', v: function (r) { return r.nextLabel; } },
            { k: 'nextDue', h: 'Next due', v: function (r) { return r.nextDue; }, day: true },
            { k: 'daysLate', h: 'Days late', v: function (r) { return r.daysLate || null; }, num: true },
            { k: 'hrs', h: 'Hours', v: function (r) { return r.hrs; }, num: true, dec: true },
            { k: 'earned', h: 'Earned h', v: function (r) { return r.earned; }, num: true, dec: true },
            { k: 'drift', h: 'Plan drift (d)', v: function (r) { return r.docDrift; }, num: true }
        ].concat(STAGES.reduce(function (a, s, i) {
            a.push({ k: 'p' + i, h: s.key + ' plan', v: function (r) { return r.ms[i].p; }, day: true });
            a.push({ k: 'a' + i, h: s.key + ' actual', v: function (r) { return r.ms[i].a; }, day: true });
            return a;
        }, []));
        var regCache = { rows: null, key: '', list: null };
        function regRows() {
            var ck = S.reg.sort + '|' + S.reg.dir;
            if (regCache.rows === S.rows && regCache.key === ck) return regCache.list;
            regCache.rows = S.rows; regCache.key = ck; regCache.list = regRowsSorted();
            return regCache.list;
        }
        function regRowsSorted() {
            var c = REG_COLS.find(function (x) { return x.k === S.reg.sort; }) || REG_COLS[2], dir = S.reg.dir;
            return S.rows.slice().sort(function (a, b) {
                var x = c.v(a), y = c.v(b);
                if (x == null || x === '') return (y == null || y === '') ? 0 : 1;
                if (y == null || y === '') return -1;
                return (typeof x === 'number' && typeof y === 'number' ? x - y : COLL.compare(String(x), String(y))) * dir;
            });
        }
        function regCell(c, r) { var v = c.v(r); if (v == null) return ''; if (c.day) return fmtDay(v); if (c.dec) return fmt1(v); return v; }
        VIEWS.register = function (grid) {
            var list = regRows(), size = S.reg.size, pages = Math.max(1, Math.ceil(list.length / size));
            if (S.reg.page >= pages) S.reg.page = pages - 1;
            var from = S.reg.page * size, pageRows = list.slice(from, from + size);
            var colsV = REG_COLS.filter(function (c) { return c.k !== 'proj' || !S.scope.project; });
            var wrap = document.createElement('section'); wrap.className = 'card span-12';
            wrap.innerHTML = '<div class="card-h"><div><h3>Document register</h3><p>' + fmtInt(list.length) + ' rows · click a heading to sort, ▾ for Excel-style filters, a row for details</p></div><div class="tools"><button type="button" class="btn" data-reg="xlsx">⊞ Excel</button><button type="button" class="btn" data-reg="csv">⬇ CSV</button></div></div>' +
                '<div class="card-b"><div class="tbl-wrap" style="max-height:calc(100vh - 290px)"><table class="t"><thead><tr>' +
                colsV.map(function (c) {
                    var on = c.f && S.filters[c.f];
                    var arrow = S.reg.sort === c.k ? (S.reg.dir > 0 ? ' ▲' : ' ▼') : '';
                    return '<th' + (c.num ? ' class="num"' : '') + '><span class="th"><span class="sort" data-sort="' + c.k + '" role="button" tabindex="0">' + esc(c.h) + arrow + '</span>' +
                        (c.f ? '<button type="button" class="th-f' + (on ? ' on' : '') + '" data-f="' + c.f + '" title="Filter ' + esc(c.h) + '">▾</button>' : '') + '</span></th>';
                }).join('') + '</tr></thead><tbody>' +
                pageRows.map(function (r) {
                    return '<tr class="click" data-i="' + r.i + '">' + colsV.map(function (c) {
                        var v = regCell(c, r), cls = c.num ? ' class="num"' : (c.wrap ? ' class="wrap"' : '');
                        if (c.k === 'daysLate' && v) return '<td class="num">' + statusOf(v > 30 ? 'critical' : 'serious', v) + '</td>';
                        return '<td' + cls + '>' + esc(v) + '</td>';
                    }).join('') + '</tr>';
                }).join('') + (pageRows.length ? '' : '<tr><td colspan="' + colsV.length + '"><div class="empty">No rows match the current filters.</div></td></tr>') +
                '</tbody></table></div><div class="pager"><span>Rows ' + fmtInt(list.length ? from + 1 : 0) + '–' + fmtInt(Math.min(from + size, list.length)) + ' of ' + fmtInt(list.length) + '</span>' +
                '<select data-reg="size">' + [50, 100, 250, 500].map(function (n) { return '<option' + (n === size ? ' selected' : '') + '>' + n + '</option>'; }).join('') + '</select>' +
                '<button type="button" class="btn" data-reg="prev"' + (S.reg.page ? '' : ' disabled') + '>‹ Prev</button><span>Page ' + (S.reg.page + 1) + ' / ' + pages + '</span>' +
                '<button type="button" class="btn" data-reg="next"' + (S.reg.page < pages - 1 ? '' : ' disabled') + '>Next ›</button></div></div>';
            grid.appendChild(wrap);
        };
        function exportRegister() {
            var list = regRows(), colsV = REG_COLS.filter(function (c) { return c.k !== 'proj' || !S.scope.project; });
            download((S.scope.project || 'Group' + S.scope.group) + '_DDR_register', csvOf({ head: colsV.map(function (c) { return c.h; }), rows: list.map(function (r) { return colsV.map(function (c) { var v = c.v(r); return v == null ? '' : (c.day ? isoDay(v) : v); }); }) }));
        }

        /* ---------------- Document drawer ---------------- */
        function openDoc(r) {
            $('drawerTitle').textContent = r.doc || '(no document number)';
            $('drawerSub').textContent = [r.proj, r.disc, r.title].filter(Boolean).join(' · ');
            var kv = [['Status', r.status], ['Status required', r.reqd], ['Revision', r.rev], ['RAMZ ID', r.ramz], ['PLIP ID', r.plip], ['DCAF No', r.dcaf], ['Primavera ID', r.prim], ['Criticality', r.crit], ['Software', r.sw],
                ['Hours (est. / earned)', fmt1(r.hrs) + ' / ' + fmt1(r.earned)], ['Planned / actual %', fmtPct(r.plnPct) + ' / ' + fmtPct(r.actPct)], ['Next milestone', r.nextLabel + (r.nextDue != null ? ' · ' + fmtDay(r.nextDue) : '')],
                ['Days late', r.daysLate || '–'], ['Remarks', r.remarks]];
            var body = $('drawerBody');
            body.innerHTML = '<dl class="kv">' + kv.map(function (p) { return '<dt>' + esc(p[0]) + '</dt><dd>' + esc(p[1] == null || p[1] === '' ? '–' : p[1]) + '</dd>'; }).join('') + '</dl>' +
                '<h3 style="font-size:14px;margin-bottom:4px">Milestone timeline</h3>' + legend([{ name: 'Planned', color: 'var(--s1)' }, { name: 'Actual', color: 'var(--s2)' }].concat(r.bl ? [{ name: 'Baseline', color: 'var(--s3)', kind: 'ring' }] : [])) + '<div id="tl"></div>' +
                tableHtml({
                    head: ['Milestone'].concat(r.bl ? ['Baseline'] : [], ['Planned', 'Actual', 'Variance (d)'], r.bl ? ['Plan drift (d)'] : []),
                    num: [0].concat(r.bl ? [0] : [], [0, 0, 1], r.bl ? [1] : []),
                    rows: STAGES.map(function (s, i) {
                        var m = r.ms[i];
                        return [s.key].concat(r.bl ? [fmtDay(r.bl[i])] : [], [fmtDay(m.p), fmtDay(m.x) + (m.a != null && m.x == null ? ' (after data date)' : ''), (m.p != null && m.x != null) ? m.x - m.p : ((m.p != null && m.x == null && m.p < S.asOf && !r.complete && i === r.next) ? 'overdue ' + (S.asOf - m.p) : '')], r.bl ? [r.drift[i] == null ? '' : (r.drift[i] > 0 ? '+' : '') + r.drift[i]] : []);
                    })
                }) + '<div class="acx" id="acx"><div class="acx-h">🏢 Aconex record <span class="muted" style="font-weight:400;color:var(--muted)">loading…</span></div></div>';
            $('drawer').classList.add('open'); $('drawer').setAttribute('aria-hidden', 'false');
            timeline($('tl'), r);
            loadAconex(r);
        }
        var acxSeq = 0;
        function loadAconex(r) {
            var seq = ++acxSeq, box = $('acx');
            if (!r.doc || !r.proj) { box.innerHTML = '<div class="acx-h">🏢 Aconex record</div><div class="empty">No document number.</div>'; return; }
            api({ api: 'aconex', project: r.proj, doc: r.doc }).then(function (a) {
                if (seq !== acxSeq) return;
                var link = a.url && /^https?:\/\//i.test(a.url) ? '<a class="btn primary" href="' + esc(a.url) + '" target="_blank" rel="noopener noreferrer">Open in Aconex ↗</a>' : '';
                box.innerHTML = '<div class="acx-h">🏢 Aconex record' + link + '</div>' + (a.found
                    ? '<dl class="kv">' + a.record.slice(0, 30).map(function (p) { return '<dt>' + esc(p[0]) + '</dt><dd>' + esc(p[1]) + '</dd>'; }).join('') + '</dl>'
                    : '<div class="empty">No Aconex record matches this document number.</div>');
            }).catch(function (e) { if (seq === acxSeq) box.innerHTML = '<div class="acx-h">🏢 Aconex record</div><div class="empty">' + esc(e.message) + '</div>'; });
        }
        function timeline(host, r) {
            var pts = []; r.ms.forEach(function (m, i) { if (m.p != null) pts.push(m.p); if (m.a != null) pts.push(m.a); if (r.bl && r.bl[i] != null) pts.push(r.bl[i]); }); pts.push(S.asOf);
            if (pts.length < 2) { host.innerHTML = '<div class="empty">No dates.</div>'; return; }
            var lo = Math.min.apply(null, pts) - 7, hi = Math.max.apply(null, pts) + 7;
            var W = Math.max(320, innerW(host) || 600), labelW = 60, rowH = 26, H = STAGES.length * rowH + 24, plotW = W - labelW - 16;
            function X(d) { return labelW + plotW * (d - lo) / (hi - lo); }
            var s = svgEl(W, H, 'Milestone timeline');
            var m0 = monthKey(lo), m1 = monthKey(hi), step = Math.max(1, Math.ceil((m1 - m0 + 1) / Math.max(2, Math.floor(plotW / 70))));
            for (var k = m0; k <= m1; k += step) { var d = monthStart(k); if (d < lo) continue; node('line', { x1: X(d), x2: X(d), y1: 0, y2: H - 18, 'class': 'gridline' }, s); txt(s, X(d), H - 8, monthLabel(k), 'tick', 'middle'); }
            node('line', { x1: X(S.asOf), x2: X(S.asOf), y1: 0, y2: H - 18, 'class': 'today' }, s);
            STAGES.forEach(function (st, i) {
                var m = r.ms[i], y = i * rowH + rowH / 2, g = node('g', { 'class': 'mark', tabindex: 0 }, s);
                node('rect', { x: 0, y: i * rowH, width: W, height: rowH, 'class': 'hit' }, g);
                txt(g, labelW - 8, y, st.key, 'lbl', 'end');
                if (m.p != null && m.a != null) node('line', { x1: X(m.p), x2: X(m.a), y1: y, y2: y, style: 'stroke:var(--axis);stroke-width:2' }, g);
                if (m.p != null) node('circle', { cx: X(m.p), cy: y, r: 5, 'class': 'dot', style: 'fill:var(--s1)' }, g);
                var b = r.bl ? r.bl[i] : null;
                // Baseline as a ring so an unchanged plan (baseline = planned) still shows both markers.
                if (b != null) node('circle', { cx: X(b), cy: y, r: 7, style: 'fill:none;stroke:var(--s3);stroke-width:2.5' }, g);
                if (m.a != null) node('circle', { cx: X(m.a), cy: y, r: 5, 'class': 'dot', style: 'fill:var(--s2)' }, g);
                bindHover(g, function () { return tipRows(st.key, (b != null ? [{ n: 'Baseline', v: fmtDay(b), c: 'var(--s3)' }] : []).concat([{ n: 'Planned', v: fmtDay(m.p) || '–', c: 'var(--s1)' }, { n: 'Actual', v: fmtDay(m.a) || '–', c: 'var(--s2)' }, { n: 'Variance', v: (m.p != null && m.a != null) ? (m.a - m.p) + ' d' : '–' }])); });
            });
            host.innerHTML = ''; host.appendChild(s);
        }
        function closeDrawer() { $('drawer').classList.remove('open'); $('drawer').setAttribute('aria-hidden', 'true'); }

        /* =================================================================
           Events
           ================================================================= */
        document.addEventListener('click', function (e) {
            var t = e.target, el;
            if (popState && !t.closest('.pop')) closePop();
            if ((el = t.closest('.tab'))) { go(el.getAttribute('data-tab')); return; }
            if ((el = t.closest('.fbtn, .th-f'))) { e.stopPropagation(); openFilter(el.getAttribute('data-f'), el); return; }
            if ((el = t.closest('[data-clear]'))) {
                var k = el.getAttribute('data-clear');
                if (k === '__all') { S.filters = {}; S.q = ''; S.qc = null; $('qsearch').value = ''; }
                else if (k === '__q') { S.q = ''; $('qsearch').value = ''; }
                else if (k === '__qc') S.qc = null;
                else delete S.filters[k];
                applyFilters(); render(); return;
            }
            if ((el = t.closest('[data-go]'))) { go(el.getAttribute('data-go')); return; }
            if ((el = t.closest('[data-views]'))) { viewsAction(el.getAttribute('data-views'), +el.getAttribute('data-i')); return; }
            if ((el = t.closest('[data-admin]'))) { adminAction(el.getAttribute('data-admin')); return; }
            if ((el = t.closest('[data-qc]'))) { S.qc = el.getAttribute('data-qc'); applyFilters(); go('register'); return; }
            if ((el = t.closest('[data-sort]'))) { var sk = el.getAttribute('data-sort'); if (S.reg.sort === sk) S.reg.dir = -S.reg.dir; else { S.reg.sort = sk; S.reg.dir = 1; } render(); return; }
            if ((el = t.closest('[data-reg]'))) {
                var a = el.getAttribute('data-reg');
                if (a === 'prev') { S.reg.page--; render(); } else if (a === 'next') { S.reg.page++; render(); } else if (a === 'csv') exportRegister(); else if (a === 'xlsx') saveXlsx((S.scope.project || 'Group' + S.scope.group) + '_DDR_register', [registerSheet()]);
                return;
            }
            if ((el = t.closest('[data-act]'))) {
                var cardEl = el.closest('.card'), c = cardEl && cardEl._card, act = el.getAttribute('data-act');
                if (!c) return;
                if (act === 'table') { c.asTable = !c.asTable; paint(c); }
                else if (act === 'csv') { var d = c.data(); if (d) download(c.title, csvOf(d)); }
                else if (act === 'xlsx') { var dx = c.data(); if (dx) saveXlsx(c.title, [sheetFromTable(c.title, dx)]); }
                else if (act === 'max') {
                    var on = !cardEl.classList.contains('max');
                    cardEl.classList.toggle('max', on);
                    var bd = document.querySelector('.backdrop');
                    if (on && !bd) { bd = document.createElement('div'); bd.className = 'backdrop'; bd.addEventListener('click', function () { cardEl.classList.remove('max'); bd.remove(); paint(c); }); document.body.appendChild(bd); }
                    if (!on && bd) bd.remove();
                    paint(c);
                }
                return;
            }
            if ((el = t.closest('tr.click'))) {
                var cardE = el.closest('.card');
                if (el.hasAttribute('data-i')) { openDoc(S.all[+el.getAttribute('data-i')]); return; }
                if (cardE && cardE._card) {
                    var cd = cardE._card, dd = cd.data(), ri = +el.getAttribute('data-row');
                    if (dd && dd.onRow) dd.onRow(ri); else if (cd.docRows && cd.docRows[ri]) openDoc(cd.docRows[ri]);
                }
                return;
            }
            if (!t.closest('.pop') && $('pop').classList.contains('open')) closePop();
        });
        document.addEventListener('change', function (e) {
            var t = e.target, c = t.getAttribute && t.getAttribute('data-ctl');
            if (c === 'look') { S.lookDays = +t.value; render(); }
            else if (c === 'stage') { S.progStage = t.value; render(); }
            else if (c === 'bl') { switchBaseline(t.value); }
            else if (c === 'target') { S.targetDays = Math.max(1, +t.value || 14); store('sddr.dash.target', S.targetDays); render(); }
            else if (t.getAttribute('data-reg') === 'size') { S.reg.size = +t.value; S.reg.page = 0; render(); }
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') { closePop(); closeDrawer(); var m = document.querySelector('.card.max'); if (m) { m.classList.remove('max'); var bd = document.querySelector('.backdrop'); if (bd) bd.remove(); paint(m._card); } }
            if (e.key === 'Enter' && e.target.matches && e.target.matches('[data-go], [data-qc], [data-sort]')) e.target.click();
        });
        var qTimer;
        $('qsearch').addEventListener('input', function (e) { clearTimeout(qTimer); qTimer = setTimeout(function () { S.q = e.target.value.trim().toLowerCase(); S.reg.page = 0; applyFilters(); render(); }, 200); });
        $('tglAct').addEventListener('change', function (e) { S.inclAct = e.target.checked; applyFilters(); render(); });
        $('tglCancel').addEventListener('change', function (e) { S.inclCancel = e.target.checked; applyFilters(); render(); });
        $('asOf').addEventListener('change', function (e) { var d = parseDay(e.target.value); S.asOf = d == null ? todayDay() : d; derive(); render(); });
        $('selGroup').addEventListener('change', function (e) { S.scope.group = e.target.value; loadProjects(null).then(loadData).catch(function (err) { $('view').innerHTML = '<div class="banner">Could not load projects: ' + esc(err.message) + '</div>'; }); });
        $('selProject').addEventListener('change', function (e) { S.scope.project = e.target.value === '*' ? '' : e.target.value; S.reg.page = 0; loadData(); });
        $('btnRefresh').addEventListener('click', loadData);
        $('btnExport').addEventListener('click', exportTab);
        $('btnViews').addEventListener('click', function (e) { e.stopPropagation(); if (popState && popState.key === '__views') closePop(); else { closePop(); openViews(e.currentTarget); } });
        $('btnPrint').addEventListener('click', function () { window.print(); });
        $('btnTheme').addEventListener('click', function () {
            var cur = document.documentElement.getAttribute('data-theme');
            var dark = cur ? cur === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
            document.documentElement.setAttribute('data-theme', dark ? 'light' : 'dark'); store('sddr.dash.theme', dark ? 'light' : 'dark'); render();
        });
        $('drawerClose').addEventListener('click', closeDrawer);
        function stickFilterBar() { document.querySelector('.filterbar').style.top = (window.innerWidth > 700 ? document.querySelector('.topbar').offsetHeight : 0) + 'px'; }
        stickFilterBar();
        var rTimer, lastW = window.innerWidth;
        window.addEventListener('resize', stickFilterBar);
        window.addEventListener('resize', function () { if (Math.abs(window.innerWidth - lastW) < 20) return; lastW = window.innerWidth; clearTimeout(rTimer); rTimer = setTimeout(function () { CARDS.forEach(paint); }, 180); });

        /* =================================================================
           Start-up
           ================================================================= */
        (function init() {
            var theme = store('sddr.dash.theme'); if (theme) document.documentElement.setAttribute('data-theme', theme);
            S.targetDays = Math.max(1, Math.min(365, Math.round(+store('sddr.dash.target')) || 14));
            var qs = new URLSearchParams(location.search);
            if (qs.get('view')) S.pendingView = decodeState(qs.get('view'));
            var tab = (location.hash || '').slice(1) || store('sddr.dash.tab');
            if (TABS.some(function (t) { return t.id === tab; })) S.tab = tab;
            $('asOf').value = isoDay(S.asOf);
            var proj = qs.get('project') || '';
            var grp = qs.get('group') || (proj ? proj.charAt(0) : '3');
            if (!Array.prototype.some.call($('selGroup').options, function (o) { return o.value === grp; })) grp = '3';
            S.scope.group = grp; $('selGroup').value = grp;
            renderTabs();
            loadProjects(proj || null).then(loadData).catch(function (e) { $('view').innerHTML = '<div class="banner">Could not load projects: ' + esc(e.message) + '</div>'; });
        })();
    })();
    </script>
</body>
</html>

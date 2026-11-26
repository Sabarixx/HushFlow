"""
studio_ui.py - 100% Self-Contained Offline HTML5 / CSS3 / SVG Interface for HushFlow Desktop
Zero CDN dependencies: works identically air-gapped offline inside Windows Edge WebView2.
"""


def build_studio_html(logo_data_uri: str = "") -> str:
    logo_html = (
        f'<img src="{logo_data_uri}" alt="HushFlow" class="brand-img" />'
        if logo_data_uri
        else '<div class="brand-fallback">HF</div>'
    )
    onboard_logo_html = (
        f'<img src="{logo_data_uri}" alt="HushFlow" class="onboard-img" />'
        if logo_data_uri
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>HushFlow</title>
<style>
  :root {{
    --bg-app: #080a0f;
    --bg-sidebar: #0b0e15;
    --bg-card: #0f131c;
    --bg-inset: #141925;
    --bg-hover: #181e2c;
    --border-hair: rgba(255, 255, 255, 0.07);
    --border-strong: rgba(255, 255, 255, 0.13);
    --text-primary: #f8fafc;
    --text-secondary: #94a3b8;
    --text-muted: #64748b;
    --accent-mint: #34d399;
    --accent-mint-dim: rgba(52, 211, 153, 0.12);
    --accent-sky: #38bdf8;
    --accent-coral: #f87171;
    --accent-coral-bg: rgba(239, 68, 68, 0.11);
    --accent-amber: #fbbf24;
    --font-ui: "Segoe UI Variable Text", "Segoe UI", -apple-system, BlinkMacSystemFont, sans-serif;
    --font-display: "Segoe UI Variable Display", "Segoe UI", sans-serif;
    --font-mono: "Cascadia Mono", "Consolas", monospace;
    --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  html, body {{
    height: 100%;
    background: var(--bg-app);
    color: var(--text-primary);
    font-family: var(--font-ui);
    font-size: 13px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
    overflow: hidden;
    user-select: none;
  }}

  .app-shell {{
    display: flex;
    height: 100vh;
    width: 100vw;
    overflow: hidden;
  }}

  /* ---------------- Left Sidebar ---------------- */
  .sidebar {{
    width: 236px;
    min-width: 236px;
    background: var(--bg-sidebar);
    border-right: 1px solid var(--border-hair);
    display: flex;
    flex-direction: column;
    padding: 18px 14px 16px;
  }}

  .brand-row {{
    display: flex;
    align-items: center;
    gap: 11px;
    padding: 4px 8px 18px;
  }}

  .brand-img {{
    width: 34px;
    height: 34px;
    border-radius: 9px;
    display: block;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.45);
  }}

  .brand-title {{
    font-family: var(--font-display);
    font-size: 15px;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--text-primary);
    display: flex;
    align-items: center;
    gap: 7px;
  }}

  .version-tag {{
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 500;
    color: var(--text-muted);
    background: rgba(255,255,255,0.04);
    border: 1px solid var(--border-hair);
    border-radius: 5px;
    padding: 1px 5px;
  }}

  .brand-sub {{
    font-size: 11px;
    color: var(--text-muted);
    margin-top: 1px;
  }}

  .nav-group {{
    display: flex;
    flex-direction: column;
    gap: 3px;
    flex: 1;
  }}

  .nav-item {{
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 9px 11px;
    border-radius: 9px;
    border: 1px solid transparent;
    background: transparent;
    color: var(--text-secondary);
    font-family: var(--font-ui);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.16s var(--ease-out);
    text-align: left;
    width: 100%;
  }}

  .nav-item:hover {{
    background: rgba(255, 255, 255, 0.035);
    color: var(--text-primary);
  }}

  .nav-item.active {{
    background: var(--bg-inset);
    border-color: var(--border-hair);
    color: var(--text-primary);
    font-weight: 600;
  }}

  .nav-item svg {{
    width: 16px;
    height: 16px;
    opacity: 0.72;
    flex-shrink: 0;
  }}

  .nav-item.active svg {{
    opacity: 1;
    color: var(--accent-mint);
  }}

  .nav-badge {{
    margin-left: auto;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--text-muted);
    background: rgba(255, 255, 255, 0.05);
    padding: 1px 6px;
    border-radius: 99px;
  }}

  /* Sidebar Engine Card */
  .sidebar-dock {{
    margin-top: auto;
    background: var(--bg-card);
    border: 1px solid var(--border-hair);
    border-radius: 12px;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }}

  .dock-status-row {{
    display: flex;
    align-items: center;
    gap: 8px;
  }}

  .status-dot {{
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--text-muted);
    transition: all 0.25s ease;
    flex-shrink: 0;
  }}

  .status-dot.online {{
    background: var(--accent-mint);
    box-shadow: 0 0 0 3px rgba(52, 211, 153, 0.18);
  }}

  .status-dot.busy {{
    background: var(--accent-amber);
    box-shadow: 0 0 0 3px rgba(251, 191, 36, 0.18);
  }}

  .dock-status-label {{
    font-size: 12px;
    font-weight: 600;
    color: var(--text-primary);
  }}

  .dock-status-meta {{
    font-size: 10.5px;
    color: var(--text-muted);
    font-family: var(--font-mono);
  }}

  .dock-btn {{
    width: 100%;
    height: 32px;
    border-radius: 8px;
    border: none;
    font-family: var(--font-ui);
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: transform 0.12s ease, background 0.15s ease;
  }}

  .dock-btn:active {{ transform: scale(0.98); }}

  .dock-btn.start {{
    background: #f8fafc;
    color: #080a0f;
  }}

  .dock-btn.start:hover {{ background: #e2e8f0; }}

  .dock-btn.stop {{
    background: var(--accent-coral-bg);
    color: #fca5a5;
    border: 1px solid rgba(239, 68, 68, 0.28);
  }}

  .dock-btn.stop:hover {{ background: rgba(239, 68, 68, 0.18); }}

  .dock-btn:disabled {{
    opacity: 0.55;
    cursor: wait;
  }}

  .sidebar-footer-actions {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 4px 0;
  }}

  .ghost-link {{
    background: none;
    border: none;
    color: var(--text-muted);
    font-family: var(--font-ui);
    font-size: 11px;
    cursor: pointer;
    padding: 3px 6px;
    border-radius: 5px;
    transition: color 0.15s, background 0.15s;
  }}

  .ghost-link:hover {{
    color: var(--text-primary);
    background: rgba(255,255,255,0.04);
  }}

  .ghost-link.danger:hover {{
    color: var(--accent-coral);
  }}

  /* ---------------- Main Stage ---------------- */
  .main-stage {{
    flex: 1;
    overflow-y: auto;
    background: var(--bg-app);
  }}

  .stage-container {{
    max-width: 880px;
    margin: 0 auto;
    padding: 24px 36px 32px;
  }}

  .main-stage::-webkit-scrollbar {{ width: 7px; }}
  .main-stage::-webkit-scrollbar-track {{ background: transparent; }}
  .main-stage::-webkit-scrollbar-thumb {{
    background: rgba(255, 255, 255, 0.08);
    border-radius: 4px;
  }}
  .main-stage::-webkit-scrollbar-thumb:hover {{ background: rgba(255, 255, 255, 0.16); }}

  .view {{ display: none; animation: fadeIn 0.2s var(--ease-out); }}
  .view.active {{ display: block; }}

  @keyframes fadeIn {{
    from {{ opacity: 0; transform: translateY(5px); }}
    to {{ opacity: 1; transform: translateY(0); }}
  }}

  .page-header {{
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 20px;
    margin-bottom: 18px;
  }}

  .page-title {{
    font-family: var(--font-display);
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.025em;
    color: var(--text-primary);
  }}

  .page-sub {{
    font-size: 13px;
    color: var(--text-secondary);
    margin-top: 3px;
  }}

  /* Hero Card */
  .hero-card {{
    position: relative;
    background: var(--bg-card);
    border: 1px solid var(--border-hair);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
    border-radius: 16px;
    padding: 18px 22px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
    margin-bottom: 12px;
    overflow: hidden;
  }}

  .hero-card.online::before {{
    content: "";
    position: absolute;
    inset: 0;
    background: radial-gradient(circle at 85% 20%, rgba(52, 211, 153, 0.09), transparent 55%);
    pointer-events: none;
  }}

  .hero-left {{ position: relative; z-index: 1; }}

  .hero-badge {{
    display: inline-flex;
    align-items: center;
    gap: 7px;
    font-size: 11px;
    font-weight: 600;
    padding: 4px 10px;
    border-radius: 99px;
    background: rgba(255,255,255,0.04);
    border: 1px solid var(--border-hair);
    color: var(--text-secondary);
    margin-bottom: 10px;
  }}

  .hero-badge.online {{
    background: var(--accent-mint-dim);
    border-color: rgba(52, 211, 153, 0.25);
    color: var(--accent-mint);
  }}

  .hero-h2 {{
    font-family: var(--font-display);
    font-size: 17px;
    font-weight: 600;
    letter-spacing: -0.015em;
  }}

  .hero-p {{
    font-size: 12.5px;
    color: var(--text-secondary);
    margin-top: 3px;
  }}

  .btn-primary {{
    background: #f8fafc;
    color: #080a0f;
    border: none;
    height: 36px;
    padding: 0 18px;
    border-radius: 9px;
    font-family: var(--font-ui);
    font-size: 12.5px;
    font-weight: 600;
    cursor: pointer;
    transition: transform 0.12s ease, background 0.15s ease;
    white-space: nowrap;
    position: relative;
    z-index: 1;
  }}

  .btn-primary:hover {{ background: #e2e8f0; }}
  .btn-primary:active {{ transform: scale(0.98); }}
  .btn-primary:disabled {{ opacity: 0.55; cursor: wait; }}

  .btn-danger {{
    background: var(--accent-coral-bg);
    color: #fca5a5;
    border: 1px solid rgba(239, 68, 68, 0.28);
    height: 36px;
    padding: 0 18px;
    border-radius: 9px;
    font-family: var(--font-ui);
    font-size: 12.5px;
    font-weight: 600;
    cursor: pointer;
    transition: transform 0.12s ease, background 0.15s ease;
    white-space: nowrap;
    position: relative;
    z-index: 1;
  }}

  .btn-danger:hover {{ background: rgba(239, 68, 68, 0.18); }}

  .btn-secondary {{
    background: var(--bg-inset);
    color: var(--text-primary);
    border: 1px solid var(--border-strong);
    height: 32px;
    padding: 0 13px;
    border-radius: 8px;
    font-family: var(--font-ui);
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.15s ease, border-color 0.15s ease;
    white-space: nowrap;
  }}

  .btn-secondary:hover {{
    background: var(--bg-hover);
    border-color: rgba(255, 255, 255, 0.2);
  }}

  /* Metric Bento Grid */
  .metrics-grid {{
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
    margin-bottom: 12px;
  }}

  .metric-card {{
    background: var(--bg-card);
    border: 1px solid var(--border-hair);
    border-radius: 13px;
    padding: 14px 16px;
  }}

  .metric-k {{
    font-size: 11px;
    color: var(--text-muted);
    margin-bottom: 5px;
  }}

  .metric-v {{
    font-family: var(--font-display);
    font-size: 14.5px;
    font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}

  .metric-s {{
    font-size: 11px;
    color: var(--text-secondary);
    margin-top: 2px;
  }}

  /* Two-Column Grid */
  .two-col {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
    margin-bottom: 12px;
  }}

  .card {{
    background: var(--bg-card);
    border: 1px solid var(--border-hair);
    border-radius: 14px;
    padding: 15px 18px;
    margin-bottom: 12px;
  }}

  .card-head {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 12px;
  }}

  .card-title {{
    font-size: 13.5px;
    font-weight: 600;
    color: var(--text-primary);
  }}

  .card-sub {{
    font-size: 12px;
    color: var(--text-muted);
  }}

  kbd {{
    display: inline-flex;
    align-items: center;
    padding: 3px 8px;
    font-family: var(--font-mono);
    font-size: 11.5px;
    font-weight: 600;
    color: var(--text-primary);
    background: var(--bg-inset);
    border: 1px solid var(--border-strong);
    border-bottom-width: 2px;
    border-radius: 6px;
    white-space: nowrap;
  }}

  .kbd-row {{
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    margin: 8px 0 6px;
  }}

  /* Mic Signal Bars */
  .vu-row {{
    display: flex;
    align-items: center;
    gap: 4px;
    height: 22px;
    background: var(--bg-inset);
    border: 1px solid var(--border-hair);
    border-radius: 9px;
    padding: 4px 10px;
  }}

  .vu-seg {{
    flex: 1;
    height: 10px;
    border-radius: 3px;
    background: rgba(255, 255, 255, 0.06);
    transition: background 0.08s ease, height 0.08s ease;
  }}

  .vu-seg.on {{ background: var(--accent-mint); height: 13px; }}
  .vu-seg.hot {{ background: var(--accent-sky); height: 14px; }}

  /* Compact Practice & Feed */
  .practice-input {{
    width: 100%;
    height: 76px;
    background: #080a0f;
    border: 1px solid var(--border-hair);
    border-radius: 10px;
    padding: 11px 13px;
    color: var(--text-primary);
    font-family: var(--font-ui);
    font-size: 13px;
    line-height: 1.5;
    resize: none;
    outline: none;
    transition: border-color 0.15s ease;
    user-select: text;
  }}

  .practice-input:focus {{
    border-color: rgba(56, 189, 248, 0.45);
  }}

  .feed-list {{
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-top: 12px;
  }}

  .feed-item {{
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    background: var(--bg-inset);
    border: 1px solid var(--border-hair);
    border-radius: 10px;
    padding: 10px 12px;
    user-select: text;
  }}

  .feed-ts {{
    font-family: var(--font-mono);
    font-size: 10.5px;
    color: var(--text-muted);
    margin-top: 2px;
    flex-shrink: 0;
  }}

  .feed-txt {{
    flex: 1;
    font-size: 12.5px;
    color: var(--text-primary);
    line-height: 1.45;
  }}

  /* Inputs & Form Controls */
  .input-field, .select-field {{
    background: #080a0f;
    border: 1px solid var(--border-strong);
    border-radius: 8px;
    color: var(--text-primary);
    font-family: var(--font-ui);
    font-size: 12.5px;
    height: 34px;
    padding: 0 11px;
    outline: none;
    transition: border-color 0.15s ease;
  }}

  .input-field:focus, .select-field:focus {{
    border-color: rgba(56, 189, 248, 0.5);
  }}

  .lex-form {{
    display: grid;
    grid-template-columns: 1fr 1fr auto;
    gap: 10px;
    align-items: center;
  }}

  .rules-list {{
    display: flex;
    flex-direction: column;
    gap: 6px;
    max-height: 340px;
    overflow-y: auto;
  }}

  .rule-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: var(--bg-inset);
    border: 1px solid var(--border-hair);
    border-radius: 9px;
    padding: 8px 12px;
  }}

  .rule-pair {{
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 12.5px;
  }}

  .pill-word {{
    font-family: var(--font-mono);
    font-size: 12px;
    padding: 2px 8px;
    border-radius: 6px;
    background: #0b0e15;
    border: 1px solid var(--border-hair);
    color: var(--text-secondary);
  }}

  .pill-target {{
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 6px;
    background: var(--accent-mint-dim);
    border: 1px solid rgba(52, 211, 153, 0.25);
    color: var(--accent-mint);
  }}

  /* Settings List */
  .settings-group {{
    background: var(--bg-card);
    border: 1px solid var(--border-hair);
    border-radius: 14px;
    overflow: hidden;
    margin-bottom: 16px;
  }}

  .group-label {{
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-muted);
    margin: 0 0 8px 4px;
  }}

  .setting-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 18px;
    padding: 14px 18px;
    border-bottom: 1px solid var(--border-hair);
  }}

  .setting-row:last-child {{ border-bottom: none; }}

  .setting-title {{ font-size: 13px; font-weight: 500; color: var(--text-primary); }}
  .setting-desc {{ font-size: 11.5px; color: var(--text-muted); margin-top: 2px; }}

  .segmented {{
    display: inline-flex;
    background: #080a0f;
    border: 1px solid var(--border-hair);
    border-radius: 8px;
    padding: 3px;
    gap: 2px;
  }}

  .seg-btn {{
    background: transparent;
    border: none;
    color: var(--text-secondary);
    font-family: var(--font-ui);
    font-size: 11.5px;
    font-weight: 500;
    padding: 5px 11px;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.15s ease;
  }}

  .seg-btn.active {{
    background: var(--bg-inset);
    color: var(--text-primary);
    font-weight: 600;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35);
  }}

  /* Switch */
  .switch {{
    position: relative;
    width: 38px;
    height: 22px;
    flex-shrink: 0;
  }}

  .switch input {{ opacity: 0; width: 0; height: 0; }}

  .slider {{
    position: absolute;
    inset: 0;
    background: #1c2233;
    border-radius: 99px;
    cursor: pointer;
    transition: 0.2s var(--ease-out);
  }}

  .slider::before {{
    content: "";
    position: absolute;
    height: 16px;
    width: 16px;
    left: 3px;
    top: 3px;
    background: #cbd5e1;
    border-radius: 50%;
    transition: 0.2s var(--ease-out);
  }}

  .switch input:checked + .slider {{ background: var(--accent-mint); }}
  .switch input:checked + .slider::before {{
    transform: translateX(16px);
    background: #06281b;
  }}

  /* Progress Bar */
  .prog-track {{
    width: 100%;
    height: 6px;
    border-radius: 99px;
    background: #080a0f;
    overflow: hidden;
    margin-top: 8px;
  }}

  .prog-fill {{
    height: 100%;
    background: var(--accent-mint);
    width: 0%;
    transition: width 0.2s ease;
  }}

  /* Shortcuts Grid */
  .cmd-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }}

  .cmd-chip {{
    background: var(--bg-inset);
    border: 1px solid var(--border-hair);
    border-radius: 10px;
    padding: 11px 13px;
  }}

  .cmd-say {{
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--accent-sky);
    font-weight: 600;
  }}

  .cmd-out {{
    font-size: 11.5px;
    color: var(--text-secondary);
    margin-top: 3px;
  }}

  /* Onboarding Modal */
  .modal-backdrop {{
    position: fixed;
    inset: 0;
    background: rgba(5, 7, 11, 0.84);
    backdrop-filter: blur(10px);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 999;
    padding: 24px;
  }}

  .modal-backdrop.open {{ display: flex; }}

  .modal-card {{
    width: 100%;
    max-width: 560px;
    background: var(--bg-card);
    border: 1px solid var(--border-strong);
    border-radius: 18px;
    padding: 26px 28px;
    box-shadow: 0 24px 60px rgba(0, 0, 0, 0.65);
  }}

  .onboard-head {{
    display: flex;
    align-items: center;
    gap: 14px;
    margin-bottom: 18px;
  }}

  .onboard-img {{ width: 44px; height: 44px; border-radius: 11px; }}

  .onboard-step {{
    background: var(--bg-inset);
    border: 1px solid var(--border-hair);
    border-radius: 11px;
    padding: 13px 15px;
    margin-bottom: 10px;
  }}

  .onboard-step-t {{
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary);
    margin-bottom: 4px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }}

  .onboard-step-p {{
    font-size: 12px;
    color: var(--text-secondary);
    line-height: 1.45;
  }}

  @media (max-width: 820px) {{
    .metrics-grid {{ grid-template-columns: 1fr 1fr; }}
    .two-col, .cmd-grid {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
<div class="app-shell">
  <!-- Left Sidebar -->
  <aside class="sidebar">
    <div class="brand-row">
      {logo_html}
      <div>
        <div class="brand-title">HushFlow <span class="version-tag" id="lbl-version">v2.1</span></div>
        <div class="brand-sub">Offline Voice Dictation</div>
      </div>
    </div>

    <nav class="nav-group">
      <button class="nav-item active" data-tab="overview" onclick="switchView('overview')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5L12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/></svg>
        <span>Overview</span>
      </button>
      <button class="nav-item" data-tab="lexicon" onclick="switchView('lexicon')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
        <span>Dictionary</span>
        <span class="nav-badge" id="nav-lex-count">0</span>
      </button>
      <button class="nav-item" data-tab="settings" onclick="switchView('settings')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c.26.6.82 1 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        <span>Settings</span>
      </button>
      <button class="nav-item" data-tab="shortcuts" onclick="switchView('shortcuts')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M18 3a3 3 0 0 0-3 3v12a3 3 0 0 0 3 3 3 3 0 0 0 3-3 3 3 0 0 0-3-3H6a3 3 0 0 0-3 3 3 3 0 0 0 3 3 3 3 0 0 0 3-3V6a3 3 0 0 0-3-3 3 3 0 0 0-3 3 3 3 0 0 0 3 3h12a3 3 0 0 0 3-3 3 3 0 0 0-3-3z"/></svg>
        <span>Shortcuts</span>
      </button>
    </nav>

    <div class="sidebar-dock">
      <div class="dock-status-row">
        <span class="status-dot" id="sidebar-dot"></span>
        <div>
          <div class="dock-status-label" id="sidebar-status-txt">Checking...</div>
          <div class="dock-status-meta" id="sidebar-status-meta">127.0.0.1:8080</div>
        </div>
      </div>
      <button class="dock-btn start" id="sidebar-toggle-btn" onclick="toggleEngine()">Start Engine</button>
    </div>

    <div class="sidebar-footer-actions">
      <button class="ghost-link" onclick="hideToTray()">Hide to Tray</button>
      <button class="ghost-link danger" onclick="quitAll()">Quit</button>
    </div>
  </aside>

  <!-- Main Content Stage -->
  <main class="main-stage">
    <div class="stage-container">

      <!-- VIEW 1: OVERVIEW -->
      <section class="view active" id="view-overview">
        <div class="page-header">
          <div>
            <h1 class="page-title">Overview</h1>
            <p class="page-sub" id="hw-subtitle">Detecting hardware...</p>
          </div>
          <button class="btn-secondary" onclick="openQuickFix()">QuickFix (Alt+Z)</button>
        </div>

        <div class="hero-card" id="hero-card">
          <div class="hero-left">
            <div class="hero-badge" id="hero-badge"><span class="status-dot" id="hero-dot"></span> <span id="hero-badge-txt">ENGINE STOPPED</span></div>
            <h2 class="hero-h2" id="hero-title">Local Speech Engine Inactive</h2>
            <p class="hero-p" id="hero-desc">Start the offline whisper engine to enable global voice dictation across any Windows app.</p>
          </div>
          <button class="btn-primary" id="hero-toggle-btn" onclick="toggleEngine()">Start Engine</button>
        </div>

        <div class="metrics-grid">
          <div class="metric-card">
            <div class="metric-k">Speech Model</div>
            <div class="metric-v" id="stat-model">small.en-q5_1</div>
            <div class="metric-s" id="stat-ram-foot">~190 MB GGML</div>
          </div>
          <div class="metric-card">
            <div class="metric-k">CPU Allocation</div>
            <div class="metric-v" id="stat-threads">Auto Threads</div>
            <div class="metric-s">100% Local Inference</div>
          </div>
          <div class="metric-card">
            <div class="metric-k">Custom Dictionary</div>
            <div class="metric-v" id="stat-lex">0 Rules</div>
            <div class="metric-s" id="stat-vocab">0 Vocab Terms</div>
          </div>
          <div class="metric-card">
            <div class="metric-k">Audio Privacy</div>
            <div class="metric-v" id="stat-privacy">RAM Only</div>
            <div class="metric-s">Zero Cloud Telemetry</div>
          </div>
        </div>

        <div class="two-col">
          <div class="card" style="margin-bottom:0">
            <div class="card-title">Push-to-Talk</div>
            <div class="kbd-row" id="hotkey-display"><kbd>Ctrl</kbd><span style="color:var(--text-muted)">+</span><kbd>Alt</kbd></div>
            <div class="card-sub">Hold while speaking anywhere in Windows, then release to paste cleanly.</div>
          </div>
          <div class="card" style="margin-bottom:0">
            <div class="card-title">Hands-Free Lock</div>
            <div class="kbd-row" id="lock-hotkey-display"><kbd>Ctrl</kbd><span style="color:var(--text-muted)">+</span><kbd>2× Alt</kbd><span style="color:var(--text-muted);font-size:11px;margin:0 2px">or</span><kbd>Ctrl</kbd><span style="color:var(--text-muted)">+</span><kbd>Alt</kbd><span style="color:var(--text-muted)">+</span><kbd>Space</kbd></div>
            <div class="card-sub">Double-tap to lock long listening; click <strong style="color:var(--accent-mint)">✓ Done</strong> or <strong style="color:var(--text-primary)">× Discard</strong> on the left pill.</div>
          </div>
        </div>

        <!-- Mic Check -->
        <div class="card">
          <div class="card-head">
            <div>
              <div class="card-title">Microphone Check</div>
              <div class="card-sub" id="mic-status-lbl">Mic idle (privacy indicator remains off until dictation or test)</div>
            </div>
            <button class="btn-secondary" id="btn-mic-test" onclick="toggleMicTest()">Test Microphone</button>
          </div>
          <div class="vu-row" id="vu-row"></div>
        </div>

        <!-- Practice & Live Session Feed -->
        <div class="card" style="margin-bottom:0">
          <div class="card-head">
            <div>
              <div class="card-title">Practice & Live Session Feed</div>
              <div class="card-sub">Click below to practice, or watch live dictations appear as you work.</div>
            </div>
            <div style="display:flex;gap:8px">
              <button class="btn-secondary" onclick="copyPractice()">Copy</button>
              <button class="btn-secondary" onclick="clearFeed()">Clear</button>
            </div>
          </div>
          <textarea class="practice-input" id="practice-box" placeholder="Click here and hold Ctrl + Alt to try dictating a sentence..."></textarea>
          <div class="feed-list" id="feed-list"></div>
        </div>
      </section>

      <!-- VIEW 2: DICTIONARY -->
      <section class="view" id="view-lexicon">
        <div class="page-header">
          <div>
            <h1 class="page-title">Dictionary & Rules</h1>
            <p class="page-sub">Teach HushFlow your names, technical acronyms, and preferred spellings.</p>
          </div>
          <button class="btn-secondary" onclick="openQuickFix()">QuickFix Dialog (Alt+Z)</button>
        </div>

        <div class="card">
          <div class="card-head" style="margin-bottom:10px">
            <div class="card-title">Add Custom Replacement</div>
            <div class="card-sub">Applied instantly during transcription</div>
          </div>
          <div class="lex-form">
            <input class="input-field" id="in-spoken" placeholder="Misheard phrase (e.g. antiravity)" />
            <input class="input-field" id="in-corrected" placeholder="Desired output (e.g. Antigravity)" />
            <button class="btn-primary" style="height:34px" onclick="addRule()">Save Rule</button>
          </div>
        </div>

        <div class="card" style="margin-bottom:0">
          <div class="card-head">
            <div class="card-title">Saved Corrections</div>
            <input class="input-field" style="width:210px;height:30px" id="filter-rules" oninput="renderRules()" placeholder="Search rules..." />
          </div>
          <div class="rules-list" id="rules-list"></div>
        </div>
      </section>

      <!-- VIEW 3: SETTINGS -->
      <section class="view" id="view-settings">
        <div class="page-header">
          <div>
            <h1 class="page-title">Settings</h1>
            <p class="page-sub">Configure audio input, local speech model, hotkeys, and data privacy.</p>
          </div>
          <button class="btn-secondary" onclick="openOnboarding()">Setup Guide</button>
        </div>

        <div class="group-label">Audio & Model</div>
        <div class="settings-group">
          <div class="setting-row">
            <div>
              <div class="setting-title">Input Microphone</div>
              <div class="setting-desc">Selected audio capture device for dictation</div>
            </div>
            <select class="select-field" style="width:300px" id="set-mic" onchange="onMicChanged()"></select>
          </div>
          <div class="setting-row" style="flex-direction:column;align-items:stretch;gap:8px">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:12px">
              <div>
                <div class="setting-title">Local Whisper Model</div>
                <div class="setting-desc">Quantized offline GGML model (switchable anytime)</div>
              </div>
              <div style="display:flex;align-items:center;gap:8px">
                <select class="select-field" style="width:190px" id="set-model" onchange="onModelChanged()">
                  <option value="small.en-q5_1">small.en-q5_1 (Balanced)</option>
                  <option value="large-v3-turbo">large-v3-turbo (Best)</option>
                  <option value="base.en">base.en (Ultra-Light)</option>
                </select>
                <button class="btn-secondary" id="btn-dl-model" onclick="downloadSelectedModel()">Installed</button>
              </div>
            </div>
            <div class="prog-track"><div class="prog-fill" id="set-model-prog" style="width:100%"></div></div>
          </div>
        </div>

        <div class="group-label">Behavior & Controls</div>
        <div class="settings-group">
          <div class="setting-row">
            <div>
              <div class="setting-title">Push-to-Talk Hotkey</div>
              <div class="setting-desc">Hold to talk, release to inject cleaned text</div>
            </div>
            <div class="segmented" id="seg-hotkey">
              <button class="seg-btn" data-val="ctrl+alt" onclick="setHotkey('ctrl+alt')">Ctrl + Alt</button>
              <button class="seg-btn" data-val="win+alt" onclick="setHotkey('win+alt')">Win + Alt</button>
              <button class="seg-btn" data-val="right_alt" onclick="setHotkey('right_alt')">Right Alt</button>
            </div>
          </div>
          <div class="setting-row">
            <div>
              <div class="setting-title">Double-Tap Alt Lock (Hands-Free)</div>
              <div class="setting-desc">Hold Ctrl and double-tap Alt (or quick double-tap) to lock continuous dictation for long paragraphs</div>
            </div>
            <label class="switch"><input type="checkbox" id="chk-double-tap" onchange="onToggleSetting('double_tap_lock', this.checked)" /><span class="slider"></span></label>
          </div>
          <div class="setting-row">
            <div>
              <div class="setting-title">Smart Cleanup Mode</div>
              <div class="setting-desc">Filler word pruning & spoken retraction protection</div>
            </div>
            <div class="segmented" id="seg-cleanup">
              <button class="seg-btn" data-val="none" onclick="setCleanup('none')">None</button>
              <button class="seg-btn" data-val="light" onclick="setCleanup('light')">Light</button>
              <button class="seg-btn" data-val="medium" onclick="setCleanup('medium')">Medium</button>
              <button class="seg-btn" data-val="high" onclick="setCleanup('high')">High</button>
            </div>
          </div>
          <div class="setting-row">
            <div>
              <div class="setting-title">Floating Edge Pill</div>
              <div class="setting-desc">60 FPS compact status pill docked cleanly at screen edge</div>
            </div>
            <label class="switch"><input type="checkbox" id="chk-hud" onchange="onToggleSetting('hud_visible', this.checked)" /><span class="slider"></span></label>
          </div>
          <div class="setting-row">
            <div>
              <div class="setting-title">Start HushFlow on Windows Login</div>
              <div class="setting-desc">Launch background speech listener automatically when signing in</div>
            </div>
            <label class="switch"><input type="checkbox" id="chk-autostart" onchange="onToggleAutoStart(this.checked)" /><span class="slider"></span></label>
          </div>
        </div>

        <div class="group-label">Privacy & Local Storage</div>
        <div class="settings-group">
          <div class="setting-row">
            <div>
              <div class="setting-title">Save Audio Clips on QuickFix Correction</div>
              <div class="setting-desc">Opt-in only: keep OFF for strict 100% RAM-only operation</div>
            </div>
            <label class="switch"><input type="checkbox" id="chk-save-audio" onchange="onToggleSetting('save_training_audio', this.checked)" /><span class="slider"></span></label>
          </div>
          <div class="setting-row">
            <div>
              <div class="setting-title">Local Vault Data</div>
              <div class="setting-desc" id="lbl-vault-path">0.00 MB used locally</div>
            </div>
            <div style="display:flex;gap:8px">
              <button class="btn-secondary" onclick="openDataFolder()">Open Folder</button>
              <button class="btn-danger" style="height:32px;padding:0 13px;font-size:12px" onclick="purgeVault()">Purge Vault</button>
            </div>
          </div>
        </div>
      </section>

      <!-- VIEW 4: SHORTCUTS -->
      <section class="view" id="view-shortcuts">
        <div class="page-header">
          <div>
            <h1 class="page-title">Shortcuts & Voice Commands</h1>
            <p class="page-sub">Keyboard triggers and natural voice formatting syntax.</p>
          </div>
        </div>

        <div class="group-label">Global Keybindings</div>
        <div class="settings-group">
          <div class="setting-row">
            <div><div class="setting-title">Push-to-Talk</div><div class="setting-desc">Hold while speaking; pastes immediately on release</div></div>
            <div><kbd>Ctrl</kbd> + <kbd>Alt</kbd></div>
          </div>
          <div class="setting-row">
            <div><div class="setting-title">Hands-Free Lock (Double-Tap)</div><div class="setting-desc">Hold Ctrl and double-tap Alt to lock long listening without holding keys</div></div>
            <div id="shortcut-lock-display"><kbd>Ctrl</kbd> + <kbd>2× Alt</kbd></div>
          </div>
          <div class="setting-row">
            <div><div class="setting-title">Hands-Free Chord Toggle</div><div class="setting-desc">Alternative toggle shortcut for continuous dictation; Esc or tap stops</div></div>
            <div><kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>Space</kbd></div>
          </div>
          <div class="setting-row">
            <div><div class="setting-title">Floating Pill Controls (While Locked)</div><div class="setting-desc">Click Done (✓) on the left pill to transcribe &amp; paste, or Discard (×) to cancel</div></div>
            <div><kbd style="color:var(--accent-mint);border-color:rgba(52,211,153,0.4)">✓ Done</kbd> <kbd>× Discard</kbd></div>
          </div>
          <div class="setting-row">
            <div><div class="setting-title">QuickFix Lexicon Learn</div><div class="setting-desc">Correct the last dictated phrase & teach HushFlow</div></div>
            <div><kbd>Alt</kbd> + <kbd>Z</kbd></div>
          </div>
          <div class="setting-row">
            <div><div class="setting-title">Emergency Stop</div><div class="setting-desc">Immediately shuts down background engine & local server</div></div>
            <div><kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>Q</kbd></div>
          </div>
        </div>

        <div class="group-label">Spoken Formatting Syntax</div>
        <div class="cmd-grid">
          <div class="cmd-chip"><div class="cmd-say">"new line"</div><div class="cmd-out">Inserts line break (\\n)</div></div>
          <div class="cmd-chip"><div class="cmd-say">"new paragraph"</div><div class="cmd-out">Inserts blank paragraph (\\n\\n)</div></div>
          <div class="cmd-chip"><div class="cmd-say">"snake case &lt;words&gt;"</div><div class="cmd-out">Formats phrase as snake_case</div></div>
          <div class="cmd-chip"><div class="cmd-say">"camel case &lt;words&gt;"</div><div class="cmd-out">Formats phrase as camelCase</div></div>
          <div class="cmd-chip"><div class="cmd-say">"kebab case &lt;words&gt;"</div><div class="cmd-out">Formats phrase as kebab-case</div></div>
          <div class="cmd-chip"><div class="cmd-say">"all caps &lt;words&gt;"</div><div class="cmd-out">Formats phrase as UPPERCASE</div></div>
          <div class="cmd-chip"><div class="cmd-say">"double &lt;digit&gt;"</div><div class="cmd-out">'double 4' &rarr; 44</div></div>
          <div class="cmd-chip"><div class="cmd-say">"triple &lt;digit&gt;"</div><div class="cmd-out">'triple 7' &rarr; 777</div></div>
        </div>
      </section>

    </div>
  </main>
</div>

<!-- First-Run Onboarding Modal -->
<div class="modal-backdrop" id="onboard-modal">
  <div class="modal-card">
    <div class="onboard-head">
      {onboard_logo_html}
      <div>
        <div class="page-title" style="font-size:19px">Welcome to HushFlow</div>
        <div class="card-sub">Private offline voice dictation engineered for Windows.</div>
      </div>
    </div>

    <div class="onboard-step">
      <div class="onboard-step-t">
        <span>1. Local-Only Privacy Guarantee</span>
        <span style="color:var(--accent-mint);font-size:11px">100% Air-Gapped</span>
      </div>
      <div class="onboard-step-p">Speech audio stays purely in local RAM via loopback whisper-server (127.0.0.1). No telemetry, accounts, or background microphone capture when idle.</div>
    </div>

    <div class="onboard-step">
      <div class="onboard-step-t">
        <span>2. Speech Model Status</span>
        <button class="btn-secondary" style="height:26px;font-size:11px" id="onboard-dl-btn" onclick="onboardDownloadModel()">Checking...</button>
      </div>
      <div class="onboard-step-p" id="onboard-model-desc">Verifying local quantized GGML model...</div>
      <div class="prog-track"><div class="prog-fill" id="onboard-prog" style="width:0%"></div></div>
    </div>

    <div class="onboard-step">
      <div class="onboard-step-t">
        <span>3. Choose Push-to-Talk Trigger</span>
      </div>
      <div class="segmented" id="onboard-seg-hotkey" style="margin-top:6px">
        <button class="seg-btn" data-val="ctrl+alt" onclick="setHotkey('ctrl+alt')">Ctrl + Alt</button>
        <button class="seg-btn" data-val="win+alt" onclick="setHotkey('win+alt')">Win + Alt</button>
        <button class="seg-btn" data-val="right_alt" onclick="setHotkey('right_alt')">Right Alt</button>
      </div>
    </div>

    <div style="display:flex;justify-content:flex-end;margin-top:16px">
      <button class="btn-primary" onclick="finishOnboarding()">Get Started</button>
    </div>
  </div>
</div>

<script>
  let state = {{ running: false, cfg: {{}}, rules: {{}}, devices: [], hw: {{}}, feed: [] }};

  // Build 24 VU bars once
  const vuRow = document.getElementById('vu-row');
  for (let i = 0; i < 24; i++) {{
    const s = document.createElement('div');
    s.className = 'vu-seg';
    s.id = 'vu-' + i;
    vuRow.appendChild(s);
  }}

  function switchView(name) {{
    document.querySelectorAll('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.tab === name));
    document.querySelectorAll('.view').forEach(el => el.classList.toggle('active', el.id === 'view-' + name));
    if (name === 'settings') refreshSnapshot();
  }}

  function formatHotkey(hk) {{
    if (hk === 'win+alt') return '<kbd>Win</kbd><span style="color:var(--text-muted)">+</span><kbd>Alt</kbd>';
    if (hk === 'right_alt') return '<kbd>Right Alt</kbd>';
    return '<kbd>Ctrl</kbd><span style="color:var(--text-muted)">+</span><kbd>Alt</kbd>';
  }}

  function formatLockHotkey(hk, doubleTapOn) {{
    let base = '<kbd>Ctrl</kbd><span style="color:var(--text-muted)">+</span><kbd>2× Alt</kbd>';
    if (hk === 'win+alt') base = '<kbd>Win</kbd><span style="color:var(--text-muted)">+</span><kbd>2× Alt</kbd>';
    else if (hk === 'right_alt') base = '<kbd>2× Right Alt</kbd>';
    if (!doubleTapOn) {{
      return '<kbd>Ctrl</kbd><span style="color:var(--text-muted)">+</span><kbd>Alt</kbd><span style="color:var(--text-muted)">+</span><kbd>Space</kbd>';
    }}
    return `${{base}}<span style="color:var(--text-muted);font-size:11px;margin:0 2px">or</span><kbd>Ctrl</kbd><span style="color:var(--text-muted)">+</span><kbd>Alt</kbd><span style="color:var(--text-muted)">+</span><kbd>Space</kbd>`;
  }}

  function applySnapshot(snap) {{
    if (!snap) return;
    state = snap;
    const running = !!snap.running;
    const cfg = snap.cfg || {{}};
    const hw = snap.hw || {{}};
    const model = cfg.model || 'small.en-q5_1';

    document.getElementById('lbl-version').innerText = 'v' + (snap.version || '2.1.0');
    document.getElementById('hw-subtitle').innerText = `${{hw.os || 'Windows'}} • ${{hw.ram || ''}} • Local Loopback`;

    // Sidebar status
    const sDot = document.getElementById('sidebar-dot');
    const hDot = document.getElementById('hero-dot');
    const sBtn = document.getElementById('sidebar-toggle-btn');
    const hBtn = document.getElementById('hero-toggle-btn');
    const heroCard = document.getElementById('hero-card');
    const heroBadge = document.getElementById('hero-badge');

    sDot.className = 'status-dot' + (running ? ' online' : '');
    hDot.className = 'status-dot' + (running ? ' online' : '');
    heroCard.classList.toggle('online', running);
    heroBadge.classList.toggle('online', running);

    if (running) {{
      document.getElementById('sidebar-status-txt').innerText = 'Engine Online';
      document.getElementById('sidebar-status-meta').innerText = snap.pids && snap.pids.length ? `PID ${{snap.pids[0]}} • Port 8080` : 'Port 8080 Active';
      sBtn.innerText = 'Stop Engine';
      sBtn.className = 'dock-btn stop';
      document.getElementById('hero-badge-txt').innerText = 'ENGINE ACTIVE';
      document.getElementById('hero-title').innerText = 'Local Speech Engine Active';
      document.getElementById('hero-desc').innerText = 'Listening globally. Dictate anywhere in Windows using your Push-to-Talk or Hands-Free trigger.';
      hBtn.innerText = 'Stop Engine';
      hBtn.className = 'btn-danger';
    }} else {{
      document.getElementById('sidebar-status-txt').innerText = 'Engine Stopped';
      document.getElementById('sidebar-status-meta').innerText = 'Ready to launch';
      sBtn.innerText = 'Start Engine';
      sBtn.className = 'dock-btn start';
      document.getElementById('hero-badge-txt').innerText = 'ENGINE STOPPED';
      document.getElementById('hero-title').innerText = 'Local Speech Engine Inactive';
      document.getElementById('hero-desc').innerText = 'Start the offline whisper engine to enable global voice dictation across any Windows app.';
      hBtn.innerText = 'Start Engine';
      hBtn.className = 'btn-primary';
    }}
    sBtn.disabled = false;
    hBtn.disabled = false;

    // Metrics
    document.getElementById('stat-model').innerText = model;
    document.getElementById('stat-ram-foot').innerText = snap.model_ram || '~190 MB GGML';
    document.getElementById('stat-threads').innerText = hw.threads || 'Auto Threads';
    const ruleCount = Object.keys(cfg.word_replacements || {{}}).length;
    const vocabCount = (cfg.custom_vocabulary || []).length;
    document.getElementById('stat-lex').innerText = `${{ruleCount}} Rule${{ruleCount === 1 ? '' : 's'}}`;
    document.getElementById('stat-vocab').innerText = `${{vocabCount}} Vocab Terms`;
    document.getElementById('nav-lex-count').innerText = ruleCount;
    document.getElementById('stat-privacy').innerText = cfg.save_training_audio ? 'Opt-In Vault' : 'RAM Only';
    document.getElementById('hotkey-display').innerHTML = formatHotkey(cfg.hotkey || 'ctrl+alt');
    const dblOn = cfg.double_tap_lock !== false;
    const lockDisp = document.getElementById('lock-hotkey-display');
    if (lockDisp) lockDisp.innerHTML = formatLockHotkey(cfg.hotkey || 'ctrl+alt', dblOn);
    const shortLock = document.getElementById('shortcut-lock-display');
    if (shortLock) {{
      const hk = cfg.hotkey || 'ctrl+alt';
      shortLock.innerHTML = hk === 'win+alt' ? '<kbd>Win</kbd> + <kbd>2× Alt</kbd>' : (hk === 'right_alt' ? '<kbd>2× Right Alt</kbd>' : '<kbd>Ctrl</kbd> + <kbd>2× Alt</kbd>');
    }}

    // Settings population
    const micSel = document.getElementById('set-mic');
    micSel.innerHTML = '';
    (snap.devices || []).forEach(d => {{
      const opt = document.createElement('option');
      opt.value = d.id === null ? '__default__' : String(d.id);
      opt.text = d.name;
      if ((d.id === null && (cfg.mic_device === null || cfg.mic_device === undefined)) || d.id === cfg.mic_device) {{
        opt.selected = true;
      }}
      micSel.appendChild(opt);
    }});

    document.getElementById('set-model').value = model;
    updateModelButtons(snap.model_ready, snap.model_size_mb);
    const chkDbl = document.getElementById('chk-double-tap');
    if (chkDbl) chkDbl.checked = dblOn;
    document.getElementById('chk-hud').checked = !!cfg.hud_visible;
    document.getElementById('chk-autostart').checked = !!snap.autostart;
    document.getElementById('chk-save-audio').checked = !!cfg.save_training_audio;
    document.getElementById('lbl-vault-path').innerText = `${{snap.vault_mb || '0.00'}} MB in %LocalAppData%\\\\HushFlow`;

    ['seg-hotkey', 'onboard-seg-hotkey'].forEach(id => {{
      document.querySelectorAll(`#${{id}} .seg-btn`).forEach(b => b.classList.toggle('active', b.dataset.val === (cfg.hotkey || 'ctrl+alt')));
    }});
    document.querySelectorAll('#seg-cleanup .seg-btn').forEach(b => b.classList.toggle('active', b.dataset.val === (cfg.cleanup_level || 'medium')));

    // Onboarding
    if (snap.show_onboarding) {{
      document.getElementById('onboard-modal').classList.add('open');
    }} else {{
      document.getElementById('onboard-modal').classList.remove('open');
    }}

    renderRules();
    renderFeed(snap.feed || []);
  }}

  function updateModelButtons(isReady, sizeMb) {{
    const sBtn = document.getElementById('btn-dl-model');
    const sProg = document.getElementById('set-model-prog');
    const oBtn = document.getElementById('onboard-dl-btn');
    const oDesc = document.getElementById('onboard-model-desc');
    const oProg = document.getElementById('onboard-prog');

    if (isReady) {{
      sBtn.innerText = 'Installed';
      sBtn.disabled = true;
      sProg.style.width = '100%';
      oBtn.innerText = 'Ready';
      oBtn.disabled = true;
      oDesc.innerText = 'Local quantized speech model detected and verified.';
      oProg.style.width = '100%';
    }} else {{
      sBtn.innerText = `Download (${{sizeMb || 190}} MB)`;
      sBtn.disabled = false;
      sProg.style.width = '0%';
      oBtn.innerText = `Download (${{sizeMb || 190}} MB)`;
      oBtn.disabled = false;
      oDesc.innerText = `Download local GGML model (${{sizeMb || 190}} MB) to transcribe offline.`;
      oProg.style.width = '0%';
    }}
  }}

  function renderRules() {{
    const q = (document.getElementById('filter-rules').value || '').toLowerCase();
    const reps = (state.cfg && state.cfg.word_replacements) || {{}};
    const list = document.getElementById('rules-list');
    const entries = Object.entries(reps).filter(([k, v]) => k.toLowerCase().includes(q) || v.toLowerCase().includes(q));
    if (!entries.length) {{
      list.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:26px;font-size:12px">No replacement rules found. Add a misheard phrase above or press Alt + Z after dictating.</div>';
      return;
    }}
    list.innerHTML = '';
    entries.forEach(([k, v]) => {{
      const row = document.createElement('div');
      row.className = 'rule-row';
      const pair = document.createElement('div');
      pair.className = 'rule-pair';
      const sSpan = document.createElement('span'); sSpan.className = 'pill-word'; sSpan.textContent = k;
      const arr = document.createElement('span'); arr.style.color = 'var(--text-muted)'; arr.textContent = '→';
      const tSpan = document.createElement('span'); tSpan.className = 'pill-target'; tSpan.textContent = v;
      pair.append(sSpan, arr, tSpan);
      const del = document.createElement('button');
      del.className = 'btn-secondary';
      del.style.height = '26px'; del.style.padding = '0 10px'; del.style.fontSize = '11px';
      del.textContent = 'Remove';
      del.onclick = () => deleteRule(k);
      row.append(pair, del);
      list.appendChild(row);
    }});
  }}

  function renderFeed(items) {{
    const box = document.getElementById('feed-list');
    box.innerHTML = '';
    items.slice().reverse().forEach(it => {{
      const el = document.createElement('div');
      el.className = 'feed-item';
      const ts = document.createElement('span'); ts.className = 'feed-ts'; ts.textContent = it.ts || '';
      const txt = document.createElement('span'); txt.className = 'feed-txt'; txt.textContent = it.cleaned || '';
      const cp = document.createElement('button');
      cp.className = 'btn-secondary';
      cp.style.height = '24px'; cp.style.padding = '0 9px'; cp.style.fontSize = '11px';
      cp.textContent = 'Copy';
      cp.onclick = () => navigator.clipboard.writeText(it.cleaned || '');
      el.append(ts, txt, cp);
      box.appendChild(el);
    }});
  }}

  window.onEngineStatePush = (snap) => applySnapshot(snap);
  window.onLiveTranscript = (item) => {{
    if (!state.feed) state.feed = [];
    state.feed.push(item);
    if (state.feed.length > 15) state.feed.shift();
    renderFeed(state.feed);
  }};

  window.onMicLevel = (level, active) => {{
    const count = Math.floor(level * 24);
    for (let i = 0; i < 24; i++) {{
      const seg = document.getElementById('vu-' + i);
      if (!seg) continue;
      seg.className = i < count ? (i >= 18 ? 'vu-seg hot' : 'vu-seg on') : 'vu-seg';
    }}
    const btn = document.getElementById('btn-mic-test');
    const lbl = document.getElementById('mic-status-lbl');
    if (active) {{
      btn.innerText = 'Stop Test';
      lbl.innerText = count > 0 ? `Signal active (${{Math.round(level * 100)}}%)` : 'Listening for speech...';
    }} else {{
      btn.innerText = 'Test Microphone';
      lbl.innerText = 'Mic idle (privacy indicator remains off until dictation or test)';
    }}
  }};

  window.onModelProgress = (pct, statusMsg) => {{
    const w = Math.round(pct * 100) + '%';
    document.getElementById('set-model-prog').style.width = w;
    document.getElementById('onboard-prog').style.width = w;
    if (statusMsg) document.getElementById('onboard-model-desc').innerText = statusMsg;
  }};

  async function refreshSnapshot() {{
    if (window.pywebview && window.pywebview.api) {{
      const snap = await window.pywebview.api.get_snapshot();
      applySnapshot(snap);
    }}
  }}

  async function toggleEngine() {{
    document.getElementById('sidebar-toggle-btn').disabled = true;
    document.getElementById('hero-toggle-btn').disabled = true;
    document.getElementById('sidebar-dot').className = 'status-dot busy';
    document.getElementById('sidebar-status-txt').innerText = state.running ? 'Stopping...' : 'Starting...';
    const snap = await window.pywebview.api.toggle_engine();
    applySnapshot(snap);
  }}

  async function toggleMicTest() {{ await window.pywebview.api.toggle_mic_test(); }}
  async function addRule() {{
    const sp = document.getElementById('in-spoken').value.trim();
    const co = document.getElementById('in-corrected').value.trim();
    if (!sp || !co) return;
    document.getElementById('in-spoken').value = '';
    document.getElementById('in-corrected').value = '';
    applySnapshot(await window.pywebview.api.add_rule(sp, co));
  }}
  async function deleteRule(k) {{ applySnapshot(await window.pywebview.api.delete_rule(k)); }}
  async function setHotkey(val) {{ applySnapshot(await window.pywebview.api.update_setting('hotkey', val)); }}
  async function setCleanup(val) {{ applySnapshot(await window.pywebview.api.update_setting('cleanup_level', val)); }}
  async function onToggleSetting(k, v) {{ applySnapshot(await window.pywebview.api.update_setting(k, v)); }}
  async function onMicChanged() {{
    const raw = document.getElementById('set-mic').value;
    const val = raw === '__default__' ? null : parseInt(raw, 10);
    applySnapshot(await window.pywebview.api.update_setting('mic_device', val));
  }}
  async function onModelChanged() {{
    const val = document.getElementById('set-model').value;
    applySnapshot(await window.pywebview.api.select_model(val));
  }}
  async function downloadSelectedModel() {{
    document.getElementById('btn-dl-model').innerText = 'Downloading...';
    document.getElementById('btn-dl-model').disabled = true;
    applySnapshot(await window.pywebview.api.download_model(document.getElementById('set-model').value));
  }}
  async function onboardDownloadModel() {{
    document.getElementById('onboard-dl-btn').innerText = 'Downloading...';
    document.getElementById('onboard-dl-btn').disabled = true;
    applySnapshot(await window.pywebview.api.download_model(state.cfg.model || 'small.en-q5_1'));
  }}
  async function onToggleAutoStart(enabled) {{ applySnapshot(await window.pywebview.api.set_autostart(enabled)); }}
  async function purgeVault() {{ applySnapshot(await window.pywebview.api.purge_vault()); }}
  async function openDataFolder() {{ await window.pywebview.api.open_data_folder(); }}
  async function openQuickFix() {{ await window.pywebview.api.open_quickfix(); }}
  async function openOnboarding() {{ document.getElementById('onboard-modal').classList.add('open'); }}
  async function finishOnboarding() {{
    document.getElementById('onboard-modal').classList.remove('open');
    applySnapshot(await window.pywebview.api.complete_onboarding());
  }}
  function copyPractice() {{
    const v = document.getElementById('practice-box').value;
    if (v) navigator.clipboard.writeText(v);
  }}
  async function clearFeed() {{
    document.getElementById('practice-box').value = '';
    applySnapshot(await window.pywebview.api.clear_feed());
  }}
  async function hideToTray() {{ await window.pywebview.api.hide_to_tray(); }}
  async function quitAll() {{ await window.pywebview.api.quit_all(); }}

  window.addEventListener('pywebviewready', refreshSnapshot);
</script>
</body>
</html>"""

#!/usr/bin/env python3
"""v2 generator: rewrite each firm's sources.yaml from /tmp/recon2.json, which
carries ALL podcasts + ALL insight/outlook sections (grouped into web sources by
domain). Also refreshes FIRM_META + update.yml. Idempotent."""
import json, re, pathlib
from urllib.parse import urlsplit

ROOT = pathlib.Path("/home/user/insights-aggregator")
recon = json.load(open("/tmp/recon2.json"))

def yq(s):
    return '"' + str(s).replace('\\','\\\\').replace('"','\\"') + '"'

def conf_note(c):
    return {"verified":"feed URL verified in public listings",
            "pattern":"feed URL inferred from the host's known slug pattern — confirm on first run",
            "unknown":"feed URL unresolved — placeholder, confirm before relying"}.get(c, c)

SETTINGS = ("settings:\n  enrich_window_days: 60\n  max_enrich_per_feed: 8\n"
            "  llm_model: claude-haiku-4-5\n  cluster_window_days: 3\n  web_max_new_per_run: 60\n")

def emit(slug, d):
    display, bu = d["display"], d.get("bu","Insights")
    pods = d.get("podcasts", [])
    webs = d.get("web", [])
    out = ["# " + "─"*77,
           f"# {display} — investment-insights source definitions",
           "#",
           "# Sibling project to the other firms/ scanners. Coverage aims to be COMPLETE:",
           "# every firm-owned podcast (native RSS) + every distinct written-insight",
           "# section and market-outlook page (via the sitemap_articles adapter, one web",
           "# source per content domain, each listing all relevant include paths).",
           "#",
           "# Only publicly accessible content; adapters send a normal browser UA and do",
           "# NOT bypass auth/paywalls/bot-gating. Per-source failures are isolated.",
           "# " + "─"*77, "", SETTINGS.rstrip("\n"), ""]
    todo = d.get("todo", [])
    if todo:
        out.append("# Known firm-owned podcasts whose public RSS feed URL was NOT resolvable during")
        out.append("# recon (only an Apple Podcasts id found). Resolve via the iTunes lookup API")
        out.append("# (itunes.apple.com/lookup?id=<id> -> feedUrl) once network access allows, then")
        out.append("# add as rss sources. Not fabricated here:")
        for name, aid in todo:
            out.append(f"#   - {name}  (apple_id: {aid})")
        out.append("")
    out.append("sources:")
    for p in pods:
        name, url, conf = p[0], p[1], (p[2] if len(p)>2 else "verified")
        out += [f"  - name: {yq(name)}", f"    firm: {yq(display)}",
                f"    business_unit: {yq(bu)}", "    method: rss",
                "    content_type: podcast", f"    url: {url}", "    tier: 1",
                f"    notes: {yq(conf_note(conf)+'.')}", ""]
    for w in webs:
        note = w.get("note") or (("Best-effort. " if w.get("best_effort") else "")
                + f"{display} written insights + outlooks via public sitemap; per-source failure isolated.")
        if w.get("gated"): note = "Bot-gated site (not circumvented); best-effort. " + note
        out += [f"  - name: {yq(w['name'])}", f"    firm: {yq(display)}",
                f"    business_unit: {yq(w.get('bu',bu))}", "    method: scrape",
                "    adapter: sitemap_articles", "    content_type: article",
                f"    url: {w['url']}", f"    tier: {w.get('tier', 2 if pods else 1)}",
                f"    notes: {yq(note)}", "    params:",
                f"      sitemap_url: {w['sitemap_url']}", "      include:"]
        for inc in w["include"]:
            out.append(f"        - {inc}")
        out += ["      exclude:"]
        for ex in w.get("exclude", ["/literature/","/forms/","/legal/","/sign-in","/content/"]):
            out.append(f"        - {ex}")
        out.append("")
    (ROOT/"firms"/slug/"sources.yaml").write_text("\n".join(out).rstrip()+"\n", encoding="utf-8")

n_p=n_w=0
for slug,d in recon.items():
    if d.get("skip"): continue
    emit(slug,d); n_p+=len(d.get("podcasts",[])); n_w+=len(d.get("web",[]))
print(f"rewrote {len([s for s in recon if not recon[s].get('skip')])} firms: {n_p} podcast + {n_w} web sources")

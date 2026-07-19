# VerifierForge — three-minute video script

**Runtime:** 3:00 exactly.  
**Rule:** show product/API output and committed evidence; never show secrets,
weights, provider dashboards, or an expired tunnel URL.

| Time | Visual | Voiceover / on-screen copy |
| --- | --- | --- |
| 0:00–0:12 | One support request hits an OpenAI-compatible endpoint; title appears. | “Production traffic contains repeated tasks that a smaller model could own—but only if we can verify success and prove the gain.” |
| 0:12–0:30 | Proxy traffic counters form three Discover cards: support, invoice, Data Pull SQL. | “VerifierForge records hashes, token cost, latency and route—not prompt bodies—then surfaces candidate clusters with real volume and economics.” |
| 0:30–0:50 | Discover opens Data Pull SQL: 95,000 queries/month, $5,500/month; click Input and confirm the 50-row source. | “For SQL, the owner confirms a repository sample source. The server recomputes its path, row count and SHA before the Agent can use it.” |
| 0:50–1:16 | Click **Analyze**. Animate four read-only tool calls, then reveal GPT-5.6 Luna’s `FORGE`, confidence 0.98, rationale and proposed 0.5B/100-step schema. | “GPT-5.6 Luna cannot train or provision anything. It can only inspect traffic, samples, economics and verifiability, then submit forge, skip or need-more-data through a strict schema.” |
| 1:16–1:31 | Click **Approve & Forge**; approval receipt replaces the button. | “Approval records human intent in Supabase. It is intentionally not a hidden GPU side effect—the audited provisioner consumes that approval separately.” |
| 1:31–1:48 | Gate C panel: `1.0 / 1.0 / 0 / 1.0`; feature flag flips back to OFF. | “Before product integration, twelve live scenarios had to pass: perfect decisions, perfect tool chains, zero illegal actions and every forge config legal. The feature still defaults off.” |
| 1:48–2:09 | NL2SQLVerifier tiers, frozen 50/60 split, then held-out report. | “The training proof freezes fifty train rows, sixty held-out rows and the verifier. Step 350 raises held-out pass at one from 0.5833 to 0.7833, and pass at eight from 0.7667 to 0.9000.” |
| 2:09–2:23 | Main and 0.5B random-reward curves side by side. | “A random-reward control stays beside the main curve. This is one NL-to-SQL result, not a universal benchmark.” |
| 2:23–2:40 | Disposable GPU → manifest-last S3 animation; interrupted objects remain invisible. | “Workers are disposable. Metrics and checkpoints cross a Storage boundary; S3 publishes the manifest last, so partial uploads never become resumable state.” |
| 2:40–2:53 | Supabase tables, then public canary counters: 120 default / 80 tuned, Guardian 0.85, reset 20 / 0. | “The same repositories run on SQLite or Supabase. A public tuned endpoint handled a reversible canary, the SQL guardian ended at point eight five, and zero switched every request back.” |
| 2:53–3:00 | `JUDGES.md`, reviewer sandbox command, closing lockup. | “Run the evidence path in under ten minutes—no GPU, key or paid call. VerifierForge: decide carefully, train disposably, ship proof.” |

## Recording checklist

- Use the flag-enabled mock binding for the Discover scene; show “Mock Agent”
  on screen. Use the separate committed v0.27 production evidence card when
  naming GPT-5.6 Luna, confidence 0.98 and the approval.
- Show [`assets/forge-agent/v0.22.4-discover-overview.png`](../../assets/forge-agent/v0.22.4-discover-overview.png)
  as the fallback still if a live click is risky.
- Use `VF_API_DATA_MODE=artifacts` and job
  `d4-m3-1p5b-r1-v0125` for result screens.
- Show the public proof JSON/canary summary, not the expired quick-tunnel URL.
- Do not imply that `Approve & Forge` launches a GPU today. P-2 remains a
  separately authorized CLI, although its bounded live path now carries the
  `provisioner-p2-live` tag.
- Never show `.env`, API headers, terminal process arguments, raw prompts,
  provider dashboards or checkpoint weights.

# Demo video script (about 1 min 45 s)

**Status: not recorded yet.** Recording and uploading are left to the project owner.

**Before recording:**

1. Run `uv run task reset && uv run task web`. Use two browser profiles, or one normal window and
   one private window, so you can switch between Asha and the reviewers quickly.
2. Keep a terminal open in the repository for the closing shot.
3. Every demo account uses the password `demo-password-1`.

| Time | On screen | Say (roughly) |
|---|---|---|
| 0:00–0:10 | The landing page | "Volunteers usually need a separate account for every charity they help. This is one account and one skills profile across organisations, with matches that explain themselves, decided by a Relate graph." |
| 0:10–0:30 | Sign in as `asha@demo.invalid`. The home page shows both organisations. Use the header switcher to change from DataKind UK to Cyber Scallywags. | "Asha volunteers with two organisations. Switching between them doesn't sign her in again, and it's a separate action from joining. Her opportunities come from both." |
| 0:30–0:40 | *My suggestions*: **Outreach Data Audit** shows *Submitted*. | "She suggested a project to DataKind UK. Until a reviewer approves it, only Asha and DataKind UK's reviewers can see it." |
| 0:40–1:00 | Private window: sign in as `rhiannon@demo.invalid` (DataKind UK reviewer). Open *Review*, then the suggestion. Choose *Approve and activate*, enter a reason, submit. Point at "revision 1 approved by Rhiannon … published to the matching graph". | "Rhiannon reviews for DataKind UK. Kofi reviews for the other organisation and would get a 403 here. The approval records who verified which revision and when. The project is matchable once it has been written to Relate." |
| 1:00–1:15 | Click *View the published project*. The candidates table lists Asha (★) for both skills, plus Kofi and Dev for data analysis. There are no gaps. | "Coordinators see who might help, skill by skill. A star means the volunteer prefers the cause. Anything nobody can cover would show as a gap." |
| 1:15–1:35 | Back as Asha: *Profile* → set **Active listening** to **Exclude** → Save. Open *My matches*: "Hidden by your exclusions …". Switch to Rhiannon's window and refresh the project: **Active listening** is now a coverage gap; Kofi and Dev remain for data analysis. | "Volunteers control what they're matched on. Asha excludes active listening. Exclusions always win, so she drops out of every project that needs it, even though her other skills fit. Only she sees why. On the coordinator's side, Relate now reports active listening as a gap." |
| (optional, +20 s) | Admin console (`:8501`), signed in as `mo@demo.invalid`: *Volunteer screening* → Nia → *approve*. As Kofi, Winter Outreach's First aid gap is gone; as Nia, *Notifications* shows "approved" and "eligible", and *Opportunities* has a yes button. | "New volunteers are vetted by a moderator first. The moment Nia is active, Relate finds her eligible, and she and the project lead are both notified. When they both say yes, she's on the team, as an ON_TEAM edge in the graph." |
| 1:35–1:45 | Terminal: `uv run task inspect`, showing the org → project → cause / skill → eligible volunteers table. | "All of this is one real single-file Relate store, bundled in the repo. Judges can run `uv run task inspect`, or query `data/demo.rl` with the Relate CLI." |

**Closing card text:**

> Inspect the bundled graph: `uv run task inspect`
> or `relate --database data/demo.rl --read-only "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS nodes"`

**Optional extra shot (sign in as `kofi@demo.invalid`):** Cyber Scallywags → *Coverage*. After Asha's exclusion, Winter Outreach Kitchen shows gaps for Active listening, Community cooking (Gwen, the other cook, excludes active listening) and First aid.

**Fallback if a projection is still pending:** the page shows "still being applied to the graph".
Refresh after a second. The background projector retries every 5 seconds.

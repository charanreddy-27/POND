# Deployment

Two separable things live in this repo:

| | What it is | Where it runs |
|---|---|---|
| **`site/`** | Static marketing site — HTML/CSS/JS, no dependencies, no build step | Vercel |
| **`src/pond/`** | The CLI itself | Users' own machines. **Not deployed.** |

POND is a local-first tool. There is no backend, no database to provision, and no API keys on the
server — deploying the site publishes three HTML files and some CSS.

---

## Part 1 — Ship the site to Vercel

### Prerequisites

This repo has **no `.git` directory yet**. Start here:

```bash
cd d:/Github/POND
git init
git add .
git commit -m "POND: local-first personal data warehouse"
gh repo create charanreddy-27/pond --public --source=. --remote=origin --push
```

(Or create the repo in the GitHub UI and `git remote add origin … && git push -u origin main`.)

> `.gitignore` already excludes `*.duckdb`, `/data/`, `.pond/` and `*.pond-tmp.csv`. Before the first
> push, run `git status` and confirm nothing personal is staged.

### Deploy

**Via the dashboard (recommended for the first deploy):**

1. [vercel.com/new](https://vercel.com/new) → **Import Git Repository** → pick `charanreddy-27/pond`.
2. Vercel will read [`vercel.json`](vercel.json) and configure itself. Confirm the settings match:

   | Setting | Value |
   |---|---|
   | Framework Preset | **Other** |
   | Build Command | *(empty — there is no build)* |
   | Output Directory | `site` |
   | Install Command | *(empty)* |
   | Root Directory | `./` |

3. **Environment variables: none.** The site is static. `ANTHROPIC_API_KEY` belongs on a user's
   laptop for the CLI — it must never be set on Vercel.
4. **Deploy.**

**Via CLI:**

```bash
npm i -g vercel
vercel          # preview deploy
vercel --prod   # production
```

### What `vercel.json` already handles

- `outputDirectory: "site"` — serves the static folder.
- `cleanUrls: true` — this is what makes `/about` and `/about-project` work without `.html`.
- Immutable one-year caching on `/assets/*`.
- Security headers: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Permissions-Policy`, and a CSP that allows only self, inline styles/scripts, and Google Fonts.

> **If you add anything to the site that loads from a new domain** — an analytics script, an embedded
> video, a webfont from elsewhere — the CSP in `vercel.json` will block it. Add the host to the
> relevant directive or the asset will silently fail.

### Custom domain

1. Vercel → your project → **Settings → Domains → Add**.
2. Enter the domain (e.g. `pond.charanreddy.dev`).
3. At your DNS provider add the record Vercel shows you — usually `CNAME pond → cname.vercel-dns.com`
   for a subdomain, or the A record `76.76.21.21` for an apex domain.
4. Wait for propagation; Vercel issues the TLS certificate automatically.
5. **Then do the find-and-replace in step 2 of the checklist below** — the site currently hardcodes
   `https://pond-cli.vercel.app` in its canonical, OG and sitemap URLs.

### Verify after deploying

```bash
curl -sI https://<your-domain>/about | head -1              # expect 200, not 404
curl -s  https://<your-domain>/robots.txt                   # expect the sitemap line
curl -sI https://<your-domain>/assets/og.png | grep -i type # expect image/png
```

Then paste the URL into [opengraph.xyz](https://www.opengraph.xyz/) to confirm the social card
renders, and run Lighthouse in Chrome DevTools.

### Local preview

```bash
python -m http.server 4173 --directory site
# → http://localhost:4173
```

Note that `cleanUrls` is a Vercel feature — locally you'll need `/about.html`. Use `vercel dev` if
you want exact production routing.

---

## Part 2 — Publishing the CLI (optional)

The CLI doesn't need deploying, but if you want `pip install pond` to work:

```bash
uv build                      # produces dist/*.whl and dist/*.tar.gz
uv publish                    # needs a PyPI token in UV_PUBLISH_TOKEN
```

The name `pond` is likely taken on PyPI — check first and be ready to rename to something like
`pond-cli` in `pyproject.toml`. Until then, `uv tool install .` from a clone is the install path,
and that's what the README documents.

---

## Manual checklist — the things only you can do

Nothing below can be automated from here. Roughly in order:

1. **`git init`, commit, and push to GitHub** (commands at the top of this file). Nothing else works
   until the repo exists. Confirm the repo URL is really
   `https://github.com/charanreddy-27/pond` — it's hardcoded in the README, all three site pages,
   and the CI badge. If you name it differently, find-and-replace it.

2. **Pick the production URL, then replace `https://pond-cli.vercel.app` everywhere.** It appears in
   `site/index.html`, `site/about.html`, `site/about-project.html` (canonical + OG + Twitter tags),
   `site/robots.txt`, `site/sitemap.xml`, and `README.md`. Wrong URLs here mean broken social
   previews, which is the one bug everybody sees.

   ```bash
   grep -rl "pond-cli.vercel.app" . --exclude-dir=.git
   ```

3. **Deploy to Vercel** (Part 1). No environment variables.

4. **Record the demo GIF.** `README.md` has a placeholder callout where it goes. Record
   `pond import` → `pond status` → `pond ask --explain`, save as `docs/demo.gif`, and replace the
   `> [!NOTE]` block with `![POND demo](docs/demo.gif)`. This is the single highest-leverage thing
   left — it's what makes someone scrolling GitHub stop.

5. **Verify the résumé link.** `site/about.html` links to `https://www.charanreddy.dev/resume`. I
   guessed that path. If it's wrong, fix it or point it at a PDF.

6. **Write the LinkedIn post, then wire it up.** `site/about-project.html` has a card headed
   "The LinkedIn write-up" with an HTML comment marking exactly where the URL goes. Swap the
   `<article class="card">` for `<a class="card" href="…" target="_blank" rel="noopener noreferrer">`
   and delete the "Going up shortly" sentence.

7. **Check the ORCID and Cal.com links resolve** — they're in the footer of every page and in the
   README, and a dead link in a footer is the cheapest possible own goal.

8. **Enable GitHub Actions** if it doesn't run automatically on first push, and confirm the CI badge
   in the README goes green. A red badge at the top of a portfolio repo is worse than no badge.

9. **Add repo topics and an About blurb on GitHub**: `duckdb`, `llm`, `local-first`, `privacy`,
   `cli`, `python`, `sql`, `personal-data`. Set the website field to the deployed URL. This is how
   the repo gets found at all.

10. **Turn on the social preview image** — GitHub → repo Settings → Social preview → upload
    `site/assets/og.png`. That's what renders when the repo link is shared anywhere.

11. **Post it.** LinkedIn, and consider r/dataengineering or Hacker News' "Show HN" — the privacy
    boundary is the genuinely novel bit, so lead with that rather than with "I built a CLI."

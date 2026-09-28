# Gaming News Bot – automatická IG stránka o videohrách

Každý deň 2 posty, komentáre a obnova tokenu. Všetko beží na GitHub Actions, bez tvojho PC a bez tvojho zásahu.

## Ako to funguje

```
RSS feedy (16 portálov)          ← bot/collect.py
   │  čerstvé články, bez už použitých
   ▼
Výber témy (Claude)               ← bot/editor.py
   │  zlúči rovnaké správy z rôznych webov, vyradí zakázané témy, označí fámy
   ▼
Overenie zdrojov (kód, nie AI)
   │  1 oficiálny zdroj ALEBO ≥ 2 nezávislé portály, inak ďalší kandidát
   ▼
Napísanie postu (Claude)          ← len z plného textu zdrojových článkov
   ▼
Čitateľská kontrola (Claude)      ← dáva to zmysel niekomu, kto tému nepozná? znie to prirodzene?
   ▼
Fact-check (ďalšie volanie Claude) ← neprejde = prepísať, 3× neprejde = ďalšia téma
   ▼
Render HTML → JPEG 1080×1350      ← bot/render.py + templates/slide.html
   ▼
GitHub Pages (verejná URL)        ← bot/hosting.py
   ▼
Instagram API publish             ← bot/instagram.py
```

Ak žiadna správa neprejde overením, slot sa **vynechá** (a príde ti notifikácia). Radšej žiadny post ako fejk.

| Workflow | Kedy | Čo robí |
|---|---|---|
| `Post` | 11:30 a 18:30 (spúšťa cron-job.org, GitHub je záloha) | vyberie, napíše, vyrenderuje a publikuje post aj Story, potom 2 h kontroluje komentáre každých 15 min |
| `Comments` | každú hodinu | odpovie na otázky a reakcie, skryje spam a toxické komentáre |
| `Refresh IG token` | každý pondelok | predĺži token o 60 dní a uloží ho do secretu |
| `Insights` | každé ráno | zbiera štatistiky postov, v pondelok pošle týždenný prehľad na Discord (dosah, sledovatelia, časy postov, náklady, vynechané sloty) |
| `Check setup` | ručne | overí feedy, Instagram token a Claude API |

## Spustenie (cca 45 minút, jednorazovo)

### 1. Instagram
1. Založ účet stránky.
2. **Nastavenia → Typ účtu a nástroje → Prepnúť na profesionálny účet** (Creator alebo Business). FB stránka nie je potrebná.

### 2. Meta app (token pre Instagram)
1. [developers.facebook.com](https://developers.facebook.com) → **My Apps → Create App**.
2. Use case: **Manage messaging & content on Instagram**. Typ: Business.
3. V appke otvor **Instagram → API setup with Instagram login**.
4. **Generate access tokens → Add account** → prihlás sa účtom stránky a povoľ všetky oprávnenia
   (`instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_comments`,
   `instagram_business_manage_insights`). Bez posledného nefungujú len štatistiky, zvyšok beží.
5. Skopíruj vygenerovaný token. Platí 60 dní, ďalej ho predlžuje bot sám.
6. App nechaj v režime **Development**. Pre tvoj vlastný účet to stačí, App Review netreba.

### 3. Claude API
1. [console.anthropic.com](https://console.anthropic.com) → API Keys → vytvor kľúč.
2. **Nastav si mesačný limit míňania** (Billing → Limits), aby ťa nič neprekvapilo.

### 4. GitHub repozitár
1. Vytvor **verejný** repozitár (GitHub Pages zadarmo funguje len pre verejné) a nahraj doň tieto súbory.
2. **Branches → New branch** → názov `gh-pages` (z `main`).
3. **Settings → Pages** → Source: *Deploy from a branch* → `gh-pages` / `root` → Save.
4. **Settings → Actions → General → Workflow permissions** → *Read and write permissions* → Save.
5. **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Hodnota |
|---|---|
| `ANTHROPIC_API_KEY` | kľúč z bodu 3 |
| `IG_ACCESS_TOKEN` | token z bodu 2 |
| `GH_PAT` | [Fine-grained token](https://github.com/settings/personal-access-tokens/new): len tento repozitár, oprávnenie **Secrets: Read and write**, platnosť max. |
| `DISCORD_WEBHOOK_URL` *(voliteľné)* | notifikácie o postoch, vynechaných slotoch, chybách a týždenný prehľad. Discord: nastavenia kanála → Integrácie → Webhooky → Nový webhook → Kopírovať URL webhooku |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` *(voliteľné)* | to isté cez Telegram |

`IG_USER_ID` netreba, bot si ho zistí z tokenu.

### 5. Test
1. **Actions → Check setup → Run workflow.** Musí prejsť Instagram, Claude aj GitHub Pages (testovací obrázok musí byť verejne dostupný). Pri feedoch uvidíš `OK`/`XX`, nefunkčné vyhoď alebo oprav v `config.yaml`.
2. **Actions → Post → Run workflow** (nechaj zaškrtnuté *dry_run*). Po dobehnutí si stiahni artifact `post-…` a skontroluj obrázky a `post.json` (caption, zdroje, dôvod overenia).
3. Zopakuj pár krát, kým ti sedí štýl. Tón a pravidlá meníš v `config.yaml`, dizajn v `templates/slide.html`.
4. **Actions → Comments → Run workflow** (*dry_run*): v logu uvidíš, čo by na ktorý komentár spravil.

### 6. Ostrý štart
Stačí nič nerobiť. Plánované behy idú automaticky naostro. Ak chceš prvý post hneď: *Post → Run workflow* a odškrtni *dry_run*.

## Lokálne testovanie (Windows)

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
copy .env.example .env      # doplň ANTHROPIC_API_KEY a IG_ACCESS_TOKEN
```

Kľúče sa lokálne čítajú z `.env` (je v `.gitignore`), na GitHube zo secrets.

```powershell
.venv\Scripts\python -m bot.check feeds     # RSS feedy
.venv\Scripts\python -m bot.check ig        # Instagram účet a limit publikovania
.venv\Scripts\python -m bot.check claude    # modely z config.yaml a test volania
.venv\Scripts\python -m bot.post --dry-run  # celý post bez publikovania, výstup v out/
.venv\Scripts\python -m bot.comments --dry-run
```

Claude API kľúč musí byť vytvorený vo workspace (Console → Workspaces → API keys), kľúč bez workspace API odmietne.

Ďalšie plánované fázy (napr. **Zľava dňa**) sú v [ROADMAP.md](ROADMAP.md).

## Ovládanie

- **Vypnúť všetko:** `enabled: false` v `config.yaml`.
- **Vypnúť len komentáre:** `comments.enabled: false`.
- **Iné časy:** `posting.slot_times` v `config.yaml` a časy v cron-job.org. Záložný `cron` v `.github/workflows/post.yml`
  (UTC, 2 riadky na slot kvôli letnému a zimnému času) nastav 10 min po slote.
- **Farby, meno a IG handle:** sekcia `brand` v `config.yaml`.
- **Nový zdroj:** pridaj riadok do `feeds` (`tier: official` len pre oficiálne blogy vydavateľov a platforiem). Weby jedného vydavateľa označ rovnakou `group`, pri overovaní sa potom rátajú ako jeden zdroj.
- **Čo bot postol a prečo:** `state/posted.json` + log každého behu v záložke Actions.
- **Strop na jeden beh:** `posting.max_cost_per_run` (predvolene $0.60). Keď ho beh dosiahne, slot sa vynechá.
  Témy, ktoré neprešli kontrolami, si bot pamätá 48 h (`state/failed.json`) a znova za ne neplatí.
- **Koľko to stojí:** `state/costs.json` (mesačný súčet za posty a komentáre, odhad podľa `api_prices` v `config.yaml`), cena každého postu je aj v `state/posted.json`. Presné čísla sú v Anthropic Console → Usage (workspace bota).

## Poistky zabudované v kóde

- Zakázané témy sa nepostujú (`avoid_topics`). Fámy a leaky áno, ak o nich píšu aspoň 2 nezávislé portály: titulka dostane štítok **RUMOR**, post menuje pôvodný zdroj fámy a denne ide najviac `rumors_per_day` (predvolene 1). Vypína ich `allow_rumors: false`. Správy z vlastného zisťovania renomovaných médií (Bloomberg, The Verge…) sú povolené (`allow_reputable_reports`), post ich vždy pripíše médiu.
- Overenie zdrojov robí kód, nie AI: 1 oficiálny zdroj alebo ≥ 2 rôzne portály.
- Copy sa píše len z plného textu článkov. Pred publikovaním ho kontroluje čitateľská kontrola (zrozumiteľnosť, prirodzená slovenčina, žiadne typické AI frázy) a samostatný fact-check.
- Každá snímka má alt text (text zo snímky) pre nevidiacich a vyhľadávanie na Instagrame. Story sa dá vypnúť cez `posting.story: false`.
- Obrázok na titulke je z článku (oficiálne zdroje majú prednosť, potom najvyššie rozlíšenie) a na vizuáli aj v captione je uvedené „Foto: zdroj“. Prepínaš to v `posting.article_images` (`all` / `official` / `none`). Keď obrázok nie je k dispozícii, ide typografický vizuál.
- Každý slot sa postne najviac raz, ani pri opakovanom behu nevznikne duplicita.
- Odpovede na komentáre nesmú obsahovať odkazy, majú max. 180 znakov a na jeden beh ich je najviac 25.
- Bot nikdy nereaguje sám na seba a na komentár, pod ktorým už odpovedal.
- Po každom behu komentárov, v ktorom sa niečo udialo, príde na Discord prehľad: na čo bot odpovedal (aj s odpoveďou), čo skryl a čo **čaká na tvoju odpoveď** (otázky „si bot?“, „kto to spravuje?“, ponuky spolupráce, sťažnosti).
- Keď niečo zlyhá, GitHub ti pošle e-mail. Ak máš Discord alebo Telegram, príde notifikácia aj tam.

## Dobré vedieť

- **Otázky „si bot?“:** bot o sebe nič nehovorí a nikdy netvrdí, že je človek. Na otázky, kto stránku spravuje, neodpovedá, nechá ich na teba.
- **GitHub vypína plánované behy** v repozitároch bez aktivity 60 dní. Bot po každom behu commitne stav, takže by sa to nemalo stať. Ak sa to predsa stane, príde ti e-mail a stačí workflow znova zapnúť.
- **Cron na GitHube** môže meškať 5 až 30 minút. Na herné správy to nevadí.
- **Náklady:** GitHub Actions a Pages sú pre verejný repozitár zadarmo. Platíš len Claude API. Na jeden post pripadne zhruba 4 až 10 volaní, na komentáre sa volá len vtedy, keď pribudnú nové. Priebežný súčet je v `state/costs.json`.
- **Ikonky:** sada [Lucide](https://lucide.dev) (licencia ISC) v `templates/icons/`.

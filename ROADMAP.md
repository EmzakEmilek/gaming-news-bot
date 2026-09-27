# Plán rozvoja

## Zásady pre všetky fázy

- **Rozpočet:** nové formáty nahrádzajú bežný news post v danom slote, nepridávajú sa navyše.
  Počet postov a náklady ostávajú približne na dnešnej úrovni (~13 $ mesačne za Claude API).
- **Kód pred AI:** všetko, čo sa dá vyčítať z dát (dátumy, ceny, skóre, platformy), dopĺňa kód.
  Claude píše len text a nikdy nedopĺňa fakty z pamäti.
- **Overenie zdrojov platí pre všetko:** 1 oficiálny zdroj alebo ≥ 2 nezávislé portály.
- **Každý formát má vlastný vypínač** v `config.yaml` a vlastný strop nákladov na beh.

## Fáza 0 – AI bot s hernými správami (beží)

2 posty denne (carousel), komentáre, obnova tokenu. Popis v [README](README.md).

## Fáza 1 – Zľava dňa (naplánované, zatiaľ neimplementované)

Jeden post denne s najzaujímavejšou zľavou na hru. Formát sa zdieľa (výzva „pošli kamošovi“)
a buduje návyk pozrieť sa na profil každý deň.

### Rozhodnuté

- **Len „Zľava dňa“**, nič iné (žiadne „hry zadarmo“, „top 5 víkendu“ ani Stories).
- **1 post denne**, vždy **statický onepager** (jedna snímka 1080×1350, nie carousel).
- **Zdroje: digitálne obchody aj slovenské e-shopy.**
  - digitál: Steam, GOG, Nintendo eShop, PlayStation Store, Xbox Store (ceny pre SK v €),
  - SK e-shopy s fyzickými hrami (napr. Alza, Nay, Brloh, Xzone) – ideálne cez produktové feedy
    affiliate sietí (Heureka, Dognet), čítanie webu je krehké a obchody ho blokujú.
- **Žiadny šedý trh kľúčov** (G2A, Kinguin, Eneba a podobné) – len oficiálne obchody.
- **Falošné zľavy neriešime** – zľava sa uvádza tak, ako ju uvádza obchod.
- **Dátumy áno** – na poste je, k akému dňu cena platí a do kedy zľava trvá (ak ju obchod uvádza).
- **„Najnižšia cena v histórii“** – ak je zľava historicky najväčšia, post to zvýrazní
  (pre PC napr. cez IsThereAnyDeal API, inak z vlastnej histórie cien v `state/`).

### Návrh riešenia (na neskoršie doladenie)

1. **Zber cien** – pre každý zdroj malý modul, ktorý vráti zoznam zliav
   (hra, platforma, obchod, cena, pôvodná cena, % zľavy, platnosť do, odkaz, obrázok).
2. **Výber zľavy dňa bez AI** – skóre podľa % zľavy, známosti hry (napr. hodnotenie, počet recenzií),
   historického minima; hra, ktorá už bola zľavou dňa, sa 30 dní neopakuje.
3. **Text** – Claude napíše len krátky nadpis a caption (lacné, odhad $0.02–0.04 na post),
   fakty (cena, dátumy, obchod) dopĺňa kód priamo z dát, nie model.
4. **Vizuál** – nová šablóna onepagera v štýle EMZO DAILY: obrázok hry, cena, pôvodná cena,
   % zľavy, obchod, platnosť, prípadne odznak „najnižšia cena v histórii“.
5. **Publikovanie** – rovnaký hosting (GitHub Pages) a Instagram API ako správy, vlastný workflow
   s jedným časom denne; pravidlo rozostupu medzi postami platí aj tu.

## Fáza 1 – Attention magnets (ďalšia na rade)

Cieľ: prilákať sledovateľov. Bez nových platených služieb.

### Show recap

Súhrn veľkej herne show (Nintendo Direct, State of Play, Xbox Showcase, Summer Game Fest,
Gamescom ONL, The Game Awards…) čo najskôr po jej skončení.

1. **Kalendár show** v `config.yaml` (názov, dátum, čas konca). Ručne, ročne ide o 15–20 show.
   Keď bot v správach zachytí ohlásenie novej show, pošle notifikáciu s návrhom na doplnenie.
2. **Workflow každých 15 min**, ktorý hneď skončí, ak práve nie je okno 2–3 h po konci show.
3. **Zber:** oficiálny recap (PS Blog, Xbox Wire…) a súhrnné články portálov z existujúcich feedov.
4. **Overenie** každého oznámenia zvlášť, podľa rovnakých pravidiel ako bežné správy.
5. **Post:** carousel až 10 snímok, titulka + 1 oznámenie na snímku s oficiálnym obrázkom.
   Fact-check ako pri bežnom poste.
6. Recap nahrádza najbližší bežný slot (ten sa vynechá).

Reálne 30–90 min po skončení show (overovanie + meškanie cronu). Odhad ~0,50–0,80 $ za recap.

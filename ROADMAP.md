# Plán rozvoja

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

### Otvorené otázky

- Čas postu (napr. 14:00 medzi dvoma správami).
- Affiliate odkazy – ak áno, post musí byť označený ako reklama / affiliate.
- Prístup k dátam PlayStation a Xbox Store (bez oficiálneho API) a k feedom SK e-shopov.

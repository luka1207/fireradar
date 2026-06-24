# 🔥 ZjarrRadar

Sistem monitorimi live i rrezikut të zjarreve për Shqipërinë, bazuar në:
- Kushtet atmosferike (temperaturë, lagështi, erë, ditë pa shi)
- Zjarre aktive të detektuara nga sateliti (NASA FIRMS)
- Faktori antropogjenik: zonat e kullotjes me bar të thatë (rrezik djegie e qëllimshme)
- Histori e shtuar manualisht për zona me rekord zjarresh

**Kosto: 0 lekë/muaj.** Çdo komponent përdor nivelin falas të shërbimit përkatës.

---

## Si funksionon "live"-i pa server 24/7

```
GitHub Actions (çdo orë) → llogarit rrezikun → ruan në Upstash Redis
                                                        ↓
                          Vercel (serverless) → API → Frontend (polling 60s)
```

Nuk ka asnjë proces që rri "aktiv" gjithë kohën — çka do të kushtonte (Railway, Render).
GitHub Actions xhiron skriptin për ~20-30 sekonda çdo orë dhe mbyllet. Kjo qëndron
brenda limitit falas prej **2,000 minuta/muaj** edhe për repo privat (e mjaftueshme
për >90 xhirime/ditë — ne na duhen vetëm 24).

---

## Hapat e instalimit (rresht pas rreshti)

### 1. Krijo bazën Redis falas (Upstash)

1. Shko te [upstash.com](https://upstash.com) → regjistrohu falas (pa kartë)
2. Krijo një database të ri **Redis** (zgjidh regjion Europe)
3. Nga paneli, kopjo:
   - `UPSTASH_REDIS_REST_URL`
   - `UPSTASH_REDIS_REST_TOKEN`

Niveli falas: 500MB hapësirë + 10,000 komanda/ditë — bie shumë larg nevojës tonë
(ne bëjmë ~12 komanda/orë × 24 = 288/ditë).

### 2. (Opsionale por e rekomanduar) NASA FIRMS API Key

1. Shko te [firms.modaps.eosdis.nasa.gov/api/map_key](https://firms.modaps.eosdis.nasa.gov/api/map_key/)
2. Regjistro email-in, merr `MAP_KEY` falas (pa limit praktik për përdorim normal)
3. Nëse e lë bosh, sistemi funksionon vetëm me të dhëna moti (pa zjarre satelitore aktive)

### 3. Vendos kodin në GitHub

```bash
cd zjarrradar
git init
git add .
git commit -m "ZjarrRadar - skeleti fillestar"
gh repo create zjarrradar --public --source=. --push
# (ose krijo repo manualisht në github.com dhe bëj git push)
```

### 4. Shto "Secrets" në GitHub (për GitHub Actions)

Te repo në GitHub → **Settings → Secrets and variables → Actions → New repository secret**

Shto këto tre:
| Emri | Vlera |
|---|---|
| `UPSTASH_REDIS_REST_URL` | nga hapi 1 |
| `UPSTASH_REDIS_REST_TOKEN` | nga hapi 1 |
| `NASA_FIRMS_MAP_KEY` | nga hapi 2 (ose lëre bosh) |

### 5. Testo workflow-in manualisht

Te repo → tab **Actions** → "Përditësim Live i Rrezikut të Zjarreve" → **Run workflow**

Nëse shkon mirë, do shohësh log-un me çdo zonë dhe score-in e saj. Workflow-i pas kësaj
xhiron automatikisht **çdo orë**, pa ndërhyrjen tënde.

### 6. Vendos frontend + API në Vercel

1. Shko te [vercel.com](https://vercel.com) → "Add New Project" → zgjidh repo `zjarrradar`
2. Te **Environment Variables**, shto të njëjtat `UPSTASH_REDIS_REST_URL` dhe `UPSTASH_REDIS_REST_TOKEN`
3. Deploy

Pas kësaj, faqja jote do jetë e gjallë në `https://zjarrradar.vercel.app` (ose domain që zgjedh).

---

## Struktura e projektit

```
zjarrradar/
├── .github/workflows/update-risk.yml   # Scheduler - xhiron çdo orë
├── scripts/update_risk.py              # Logjika e llogaritjes së rrezikut
├── data/zones.json                     # 12 qarqet e Shqipërisë + a ka kullotë
├── api/risk-map.js                     # Endpoint që lexon nga Redis
├── public/index.html                   # Harta live (Leaflet.js)
└── vercel.json                         # Konfigurimi i deploy-it
```

---

## Çka duhet kalibruar/zgjeruar më vonë

1. **Granulariteti gjeografik** — tani kemi 12 qarqe (pikë qendrore). Për precizion më
   të lartë, mund të kalohet në grid 10km×10km mbi gjithë Shqipërinë (rreth 280 qeliza),
   por kjo rrit thirrjet API te Open-Meteo — ende brenda limitit falas (10,000/ditë),
   thjesht duhet batch-uar.

2. **Të dhëna kullote reale** — tani `has_grazing: true/false` është vendosur manualisht
   në `zones.json`. Do ishte shumë më e fortë nëse merret nga ASIG (Agjencia Shtetërore
   e Informacionit Gjeohapësinor) ose nga vetë komunat lokale.

3. **Histori reale zjarresh** — funksioni `redis_get_historical()` pret një score
   të ruajtur paraprakisht. Mund të popullohet një herë me të dhëna nga AKBN/Agjencia
   Kombëtare e Mjedisit për zjarret e regjistruara në 5-10 vitet e fundit.

4. **Kalibrimi i peshave** — peshat në `calculate_risk_score()` (25/20/20/10/15/10 pikë)
   janë vendosje fillestare logjike, jo të nxjerra nga regresion statistikor. Pasi të
   kemi disa muaj të dhëna reale + zjarre të ndodhura, mund të kalibrohen me regresion
   ose Random Forest siç u diskutua më parë.

5. **Alarme** — mund të shtohet një hap në `update_risk.py` që dërgon njoftim
   (Telegram Bot API është falas dhe i shpejtë për këtë) kur një zonë kalon score 70+.

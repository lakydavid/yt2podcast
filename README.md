# 🎧 yt2podcast

Egy YouTube-videó **hangját** hallgathatod podcastként, a **lehető legkevesebb mobiladattal**.
Beilleszted a videó linkjét → azonnal játszhatod a hangsávot. **A videó adatfolyam soha nem töltődik le.**

## Hogyan takarít adatot?

A YouTube minden videóhoz külön, **csak hangot** tartalmazó sávokat is kínál
(opus ~30 kbps, m4a ~48 kbps stb.). Az app ezek közül a legkisebbet választja ki,
és HTTP **Range** támogatással streameli a böngészőnek – így:

| | adat / óra | 3 órás videó |
|---|---|---|
| 720p videó | ~1–1.5 GB | ~3–4 GB |
| **yt2podcast (ajánlott ~48 kbps)** | **~22 MB** | **~65 MB** |
| **yt2podcast (ultra ~30 kbps)** | **~14 MB** | **~40 MB** |

- A tekergetés (seek) is csak a szükséges részt tölti le.
- Több órás videók is gond nélkül hallgathatók.
- Nincs szerveroldali átkódolás → kicsi az erőforrásigény, nincs `ffmpeg` függőség.

## Funkciók

- 📋 Link beillesztése, metaadatok (cím, csatorna, hossz, borító)
- 🎚️ Minőségválasztó: **Ultra** / **Ajánlott (m4a, minden eszközön megy)** / **Jó**
- 🔖 **Fejezetek**: a YouTube natív fejezetei, vagy ha nincs, a leírásból kiparse-olt
  időbélyegek (`0:00 Cím`, `[1:02:03] Cím` stb.) – kattintásra ugrás, aktív fejezet kiemelése
- 📱 Zárolt képernyős vezérlés (Media Session: lejátszás, ±15/30 mp, fejezet előre/hátra)
- ⏩ Lejátszási sebesség (1× – 2×)
- 📊 Becsült adatfelhasználás kijelzése
- 🕘 Előzmények (helyben, `localStorage`)
- 🔗 Megosztás: `?u=<link>` paraméterrel azonnal indul

## Futtatás

**Teljes (FastAPI) szerver — gépen:**

```bash
./run.sh
# vagy:
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Függőség-könnyű szerver — bárhol, ahol csak `yt-dlp` van** (egyetlen, tiszta-Python
csomag, nincs FastAPI/Rust fordítás):

```bash
pip install yt-dlp
python server_lite.py            # -> http://localhost:8000
```

Mindkettő ugyanazt a frontendet és API-t (`/api/info`, `/api/audio`) adja; a közös
logika az `app/core.py`-ban van.

Nyisd meg: <http://localhost:8000>

### 📱 Android (Termux) — közvetlenül a telefonon

A legjobb mobilos teszt: a saját mobil-IP-deddel fut, így ritkán kér a YouTube
bot-ellenőrzést, és `localhost`-on a fejezet- és zárolt képernyős vezérlés is megy.

1. Telepítsd a **Termux**-ot az [F-Droid](https://f-droid.org/packages/com.termux/)-ról
   (a Play Store-os verzió elavult).
2. Termuxban:
   ```bash
   pkg install -y git
   git clone https://github.com/lakydavid/yt2podcast.git
   cd yt2podcast
   bash termux.sh
   ```
   (Privát repónál a `git clone` kérheti a GitHub-felhasználódat és egy
   [personal access tokent](https://github.com/settings/tokens) jelszó helyett.)
3. A telefon böngészőjében nyisd meg: <http://localhost:8000>
4. „Hozzáadás a kezdőképernyőhöz” → app-szerűen használható.

> Tipp: a háttérben tartáshoz használj `tmux`-ot, vagy a Termux értesítésében az
> „Acquire wakelock” opciót, hogy lejátszás közben ne álljon le.

## Felépítés

```
app/
  core.py            keretrendszer-független logika (yt-dlp kinyerés, fejezetek,
                     formátumválasztás, gyorsítótár) — csak yt-dlp-t igényel
  main.py            FastAPI szerver (async httpx Range-proxy)
  static/index.html  egylapos frontend (lejátszó + UI)
server_lite.py       stdlib-only szerver (csak yt-dlp kell) — Termuxhoz/minimál hosthoz
run.sh / termux.sh   indítók gépre / Androidra
```

## Éles üzem / hibaelhárítás

A YouTube időnként `403`-mal vagy „Sign in to confirm you're not a bot” hibával
válaszol a szerverekről érkező kérésekre (bot-védelem, ún. PO-token). Ilyenkor
környezeti változókkal segíthetsz:

| Változó | Mit csinál |
|---|---|
| `YT2P_COOKIES_FILE` | Egy bejelentkezett böngészőből exportált `cookies.txt` (Netscape formátum) elérési útja. A legmegbízhatóbb megoldás. |
| `YT2P_PLAYER_CLIENTS` | Vesszős lista a használandó YouTube-kliensekről, pl. `ios,web_safari,tv`. Más kliens gyakran megkerüli a 403-at. |
| `SSL_CERT_FILE` | Egyéni CA-csomag (céges TLS-proxy mögött). Ilyenkor a rendszer trust store-ját használja, az ellenőrzés bekapcsolva marad. |

Példa:

```bash
YT2P_COOKIES_FILE=/etc/yt2podcast/cookies.txt \
YT2P_PLAYER_CLIENTS=ios,web_safari \
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

A `yt-dlp`-t érdemes naprakészen tartani (`pip install -U yt-dlp`), mert a
YouTube gyakran változik.

## Megjegyzések

- Az `/api/audio` a szerveren keresztül proxyzza a hangot, így a YouTube IP-hez kötött
  stream-URL-je is működik, és a böngésző egységes, CORS-mentes forrást lát.
- A feloldott stream-URL-eket 1 órán át gyorsítótárazza, hogy a tekergetés gyors legyen.
- Csak saját, jogszerű felhasználásra. Tartsd tiszteletben a YouTube ÁSZF-jét és a szerzői jogokat.
```

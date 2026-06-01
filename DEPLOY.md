# Nyilvános telepítés ingyenes felhő VM-re

Ez a leírás egy **bárki által elérhető** yt2podcast példányt állít fel egy ingyenes
felhő VM-en, **automatikus HTTPS-szel**. A szerver számodra ingyenes, a
felhasználóknak is.

> **Olvasd el előbb őszintén — mire számíts felhőben, nyilvánosan**
>
> A hang a szerveren keresztül megy, és a kérések adatközponti IP-ről indulnak.
> Ezért:
> - 🟡 **A YouTube bot-ellenőrzést kér** adatközponti IP-kről. Ez a legnagyobb
>   gond. Megoldás: `cookies.txt` egy bejelentkezett fiókból. **Használj
>   eldobható Google-fiókot**, mert a YouTube ÁSZF-sértés miatt akár tilthatja.
>   A cookie idővel lejár / megjelölődhet → időnként cserélni kell.
> - 🟢 **Sávszélesség:** az Oracle Always Free **havi 10 TB** kimenő forgalmat ad,
>   ami ~50 kbps hanghoz óriási (nagyjából **több tízezer óra** hallgatás), tehát
>   ez ritkán szűk keresztmetszet.
> - 🟡 **Visszaélés / jog:** nyilvánosan bárki bármit beilleszthet. A felelős
>   üzemeltetés (rate limit, naplók, ÁSZF/jogtisztaság) a tiéd. A `YT2P_MAX_STREAMS`
>   és a HTTPS már beépítve.
>
> Ha a YouTube-blokk állandósul, a legrobosztusabb (de nem ingyenes) megoldás egy
> **lakossági proxy**, vagy a szervert otthoni/lakossági IP-ről futtatni
> (Cloudflare Tunnel — lásd a README Termux/otthoni szakaszát).

## 1. Ingyenes VM: Oracle Cloud Always Free

1. Regisztrálj a [Oracle Cloud Free Tier](https://www.oracle.com/cloud/free/)-re
   (kell egy bankkártya azonosításhoz, de az „Always Free" erőforrásokért nem
   terhel).
2. Hozz létre egy **VM instance**-t: *Ampere (ARM)* shape, Ubuntu 22.04/24.04 image.
   Az always-free keret bőven elég (akár 1–4 OCPU / 6–24 GB RAM).
3. A „Networking"-nél engedélyezd a publikus IP-t, és nyiss **80** és **443**
   portot (VCN → Security List → Ingress: 0.0.0.0/0 a 80 és 443 TCP-re).
4. SSH-zz be (akár a telefonodról **Termiusszal**):
   ```bash
   ssh ubuntu@<a-VM-publikus-IP-je>
   ```
5. Ubuntun a tűzfalat is nyitni kell:
   ```bash
   sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
   sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
   sudo netfilter-persistent save   # ha nincs: sudo apt install -y iptables-persistent
   ```

## 2. Ingyenes domain: DuckDNS

A HTTPS-hez kell egy hosztnév. Ingyenes: [DuckDNS](https://www.duckdns.org) →
lépj be, hozz létre egy aldomaint (pl. `yt2pod.duckdns.org`), és állítsd be az
„current ip" mezőt a VM publikus IP-jére.

## 3. Telepítés Dockerrel

```bash
# Docker + compose
curl -fsSL https://get.docker.com | sudo sh

# Kód
git clone https://github.com/lakydavid/yt2podcast.git
cd yt2podcast

# Konfig
cp .env.example .env
nano .env            # DOMAIN=yt2pod.duckdns.org

# (ajánlott) cookies a YouTube bot-ellenőrzéshez — lásd lent
# tedd ide: cookies/cookies.txt

sudo docker compose up -d
```

Pár perc múlva (Let's Encrypt cert kiállítása) elérhető:
**https://yt2pod.duckdns.org** 🎉

## 4. Cookies (a YouTube-blokk ellen)

1. Egy böngészőben jelentkezz be egy **eldobható** Google-fiókkal a youtube.com-on.
2. Exportáld a sütiket Netscape `cookies.txt` formátumban (pl. a „Get cookies.txt
   LOCALLY" bővítménnyel).
3. Másold a VM-re: `cookies/cookies.txt`. A compose már be van kötve
   (`/cookies/cookies.txt`), és csak akkor használja, ha létezik.
4. Újraindítás: `sudo docker compose restart app`.

## 5. Karbantartás

- **yt-dlp frissítés:** a konténer minden indításkor frissíti (a YouTube gyakran
  változik). Manuálisan: `sudo docker compose restart app`.
- **Logok:** `sudo docker compose logs -f app`
- **Frissítés kódból:** `git pull && sudo docker compose up -d --build`
- **Egyidejű hallgatók korlátja:** `YT2P_MAX_STREAMS` a `.env`-ben.
- Ha sok a „bot" hiba: frissítsd a cookie-t, és/vagy próbálj más
  `YT2P_PLAYER_CLIENTS` értéket (pl. `ios`, `tv`, `web_safari`).

## Alternatíva otthoni géppel (nincs YouTube-blokk)

Ha van otthon egy 0–24 menő géped, futtasd ott (lakossági IP → nincs bot-blokk),
és tedd publikussá ingyen **Cloudflare Tunnellel** — nem kell hozzá nyitott port,
és szintén ingyenes HTTPS-t ad. Lásd a README otthoni/Termux szakaszát.

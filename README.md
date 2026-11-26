```text
█ ▄▀ ▄▀▀▄    █ ▄▀▀▄ █▄ ▄█ █▀▀▀ █▀▀▀ ▄▀▀▀
█▀▄  █  █ ▄  █ █▀▀█ █ ▀ █ █▀▀  █▀▀   ▀▀▄
▀  ▀  ▀▀   ▀▀  ▀  ▀ ▀   ▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀
```

**Kojamees on MCP server ja käsurida [Korto Pro](https://korto.ee) jaoks**, veebirakenduse jaoks, kus paljud Eesti korteriühistud oma arveldust teevad. Mitteametlik: OÜ Tarkvaralaboriga pole see seotud ega nende poolt heaks kiidetud. Kojamees käib igal hommikul maja läbi; see kojamees käib läbi Korto kuuringu: kontrollib näite, sisestab kuluarved ja jaotab kulud, sinu enda Korto sisselogimisega, sinu või sinu AI-agendi jaoks.

In English: [README.en.md](README.en.md)

[![tests](https://github.com/runnel/kojamees/actions/workflows/test.yml/badge.svg)](https://github.com/runnel/kojamees/actions/workflows/test.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

`kojamees check` loeb arveldusperioodi ja näitab, mida enne kulude jaotamist parandada; mõni käsk täidab seejärel samad vormid, mida sina täidaksid, kuivkäivitusena, kuni lisad `--apply`. Kojamees püsib Korto tingimuste piires: vaata [Tingimused, seadus ja andmed](#tingimused-seadus-ja-andmed).

## Miks

Korto Pro on veebirakendus, kus paljud Eesti korteriühistud peavad näite, kulusid ja igakuiseid arveid. Tal pole API-t ega MCP serverit, nii et skript või AI-agent ei saa sellega ametlikult töötada: kõik käib brauseris klikkides. Kuuring on aga iga kuu sama — kontrolli näidud, sisesta kuluarved, prognoosi puuduvad, jaota — ja see kuulub tööriista.

Kallid vead tulevad sisse elanike endi sisestustega ja jäävad veebis kergesti märkamata: järgmise kuu kuupäevaga näit, mille Korto perioodist välja jätab; korteri küttenäit, mis on suurem kui terve maja oma; ümmargune `1.000 MWh`, mis on hinnang, mitte näit. Kui arved on välja läinud, on iga parandus kreeditarve ja kiri naabrile.

Kojamees loeb samu lehti, mida sina näed, sinu sisselogimisega, üks päring sekundis, kontrollib neid ja täidab kuuringu vormid nii, nagu sina täidaksid.

## Kellele

Korteriühistu haldurile, kes kasutab Korto Pro'd terminalis või AI-agendi kaudu. Kojamees on noor ja selle autor kasutab seda iga kuu ühes ühistus.

Ta katab kuuringu ja selle ümbruse, mitte midagi muud:

- **Sisselogimine:** Korto enda Smart-ID, Mobiil-ID või paroolisisselogimisega, hoitud nagu brauser seda hoiab.
- **Lugemine:** perioodid, näidud ja nende ajalugu, kontroll ning Korto tabelid: kululiigid, laekumised, saldod, kulud, jaotus, arved.
- **Kirjutamine, kuivkäivitusena kuni `--apply`:** näidu sisestamine või kustutamine, elanike arv, kuluarve, fikseeritud hind, panga väljavõtte laekumiste import, puuduvate näitude prognoos, kulude jaotamine, jaotatud perioodi taasavamine, kinnitatud perioodi sulgemine ja sulgemise tühistamine, uue perioodi avamine. Vaata [docs/DESIGN.md](docs/DESIGN.md).
- **Allalaadimine:** korteri arve PDF-ina, nagu Korto selle trükib, omanikele, kel pole e-posti.
- **Valvamine:** `kojamees selftest` võrdleb Korto vorme nendega, mida Kojamees viimati nägi.
- **Teenindamine:** `kojamees mcp` pakub samu käske AI-agendile; muudatus vajab oma kuivkäivituse tokenit.

Kõik muu Kortos jääb esialgu veebi: arvete kinnitamine ja kinnituse tühistamine, arvete, kirjade või meeldetuletuste saatmine, kreeditarved, omanikud, dokumendid, seaded ja ükski teine ühistu peale sinu oma.

## Kus see töötab

| Kasutusviis | Töötab? | Mida on vaja | Kuidas muudatus kinnitatakse |
|---|---|---|---|
| Terminal, ilma AI-ta<br>*nt Macis Terminal, Windowsis PowerShell* | jah | Python 3.9 või uuem | lisad `--apply` |
| Koodiagent, mis käivitab käske sinu arvutis<br>*nt Claude Code, Codex, Cursor* | jah | Python ja `kojamees mcp` MCP serverina | kuivkäivituse token pärast sinu jah-i |
| Lauaarvuti AI-rakendus, mis käivitab MCP servereid sinu arvutis<br>*nt Claude Desktop ja selle Cowork, ChatGPT lauaarvutirakendus* | jah | Python ja `kojamees mcp` rakenduse MCP seadetes | kuivkäivituse token pärast sinu jah-i ja rakenduse enda kinnitusküsimus |
| AI-rakendus, mis ühendub ainult internetis olevate serveritega<br>*nt claude.ai ja ChatGPT veebis ja telefonis* | ei | Kojamees töötab sinu arvutis. Teistele majutamine tähendaks iga ühistu Korto sisselogimise hoidmist, mida Kojamees ei tee. | |

Rakenduste võimalused muutuvad kiiresti: kindlaim on vaadata, kas su rakenduse MCP seadetes saab lisada käsu, mitte ainult internetiaadressi.

Sisselogimine käib igal pool ühtemoodi: üks kord terminalis `kojamees login`; see kehtib aasta. Kui isikukood on salvestatud, võib agent `kojamees login`-i ise käivitada ja sina kinnitad telefonis.

Python: Macis pakub esimene `python3` käsk Apple'i Command Line Tools'i paigaldust, üks klikk. Windowsis paigalda see python.org-ist. Linuxis on see enamasti olemas.

## Kuidas see välja näeb

```
$ kojamees check --period 2026-10 --ignore "4:Soe vesi"
level  apartment  meter      message
block  1          Küte       dated 02.11.2026, after the period end 31.10.2026
block  2          Külm vesi  missing reading
block  2          Küte       9.000 MWh is more than the whole building (4.000 MWh)
warn   2          Küte       9.000 is over 5x the previous period (0.400)
warn   2          Küte       round number 9.000 MWh: an estimate?
warn   3          Külm vesi  negative consumption -0.100 (normal after a forecast)
warn   3          Küte       round number 1.000 MWh: an estimate?
block             Prügivedu  expense invoice missing
block             Elekter    expense invoice missing

2026 oktoober: 5 blocking, 4 warnings
```

```
$ kojamees set-reading 1 "Külm vesi" 14.2
2026 november: Street 1-1 Külm vesi
field     now  after
end            14.200
date           30.11.2026
quantity       1.700
forecast  f    f

Korto form: yyriarvestus.naidud_muuda, action salvesta
baseline: matches
dry run: nothing was saved in Korto. Add --apply to save it.
```

Väljund on testiandmetest; nimed ja numbrid on välja mõeldud.

Väljumiskoodid: 0 midagi blokeerivat pole, või tehtud · 1 blokeerivad leiud, või muudatus, mida Korto ei näita · 2 kasutusviga, Korto leht, millest Kojamees aru ei saa, või kuluarve, mis ei klapi · 3 sisselogimine on lõppenud · 4 päring Kortosse ebaõnnestus.

## Paigaldamine

Python 3.9 või uuem, ainult standardteek.

```bash
git clone https://github.com/runnel/kojamees && cd kojamees
mkdir -p ~/.local/bin && ln -s "$PWD/kojamees.py" ~/.local/bin/kojamees   # Linux, macOS
```

Windowsis käivita see kujul `py kojamees.py …`.

## Sisselogimine

```bash
kojamees login              # Smart-ID; või --mobile-id, või --password
```

Esimesel korral küsib ta isikukoodi, näitab Smart-ID kontrollkoodi, ootab, kuni sisestad telefonis PIN1, ja leiab sinu ühistu Korto esilehelt; kui sinu sisselogimine avab mitu ühistut, küsib, millist. Korto sisselogimise hoiab ta failis `~/.korto.env` (või failis, mille nimetab `KORTO_ENV` või `--env`), koos isikukoodiga, kui sa ei keeldu, ega kuva seda kunagi. Edaspidi logib iga käsk sisse nii, nagu brauser pärast taaskäivitust. Aasta pärast palub ta, nagu brauser, uuesti sisse logida.

Parooli küsitakse iga kord ekraanile kuvamata ja seda ei salvestata. ID-kaardi või pangalingiga, mis töötavad ainult brauseris, kopeeri hoopis sessioon: logi brauseris Korto Pro'sse sisse, ava arendaja tööriistad, sealt Application (Chrome) või Storage (Firefox, Safari) → Cookies → `https://pro.korto.ee`, ja pane `PHPSESSID` väärtus env-faili kujul `KORTO_PHPSESSID=…`, rea `KORTO_UNION_ID=…` kõrvale. See sessioon lõpeb siis, kui brauseri oma.

Enne esimest `--apply`-d lase Kojamehel Korto vormid üks kord läbi vaadata, loe nimekiri läbi ja jäta see võrdlusaluseks:

```bash
kojamees selftest            # ainult loeb: kõik vormid, mida perioodide praegused olekud lubavad
kojamees selftest --accept   # hoiab need failis ~/.korto-baseline.json (või $KORTO_BASELINE)
```

Iga `--apply` võrdleb vormi, mida ta saatma hakkab, selle võrdlusalusega ja keeldub, kui Korto on välja andnud uue versiooni või vormi kuju on muutunud: käivita `kojamees selftest`, loe erinevused läbi ja kinnita ainult see, millest aru saad. Vormi, mida perioodide praegused olekud ei näita (näiteks järgmise perioodi vorm, kui üks on avatud), lisab hilisem `selftest --accept`.

## Igapäevased käsud

```bash
kojamees periods                      # perioodide id-d ja olekud: avatud, kinnitatud, suletud
kojamees readings --period 2026-10    # iga arvesti: algus, lõpp, kuupäev, kogus, prognoos, kes sisestas
kojamees check --period 2026-10       # mida enne kulude jaotamist parandada
kojamees summary --period 2026-10     # jaotuse ja arvete tabelid
kojamees table balances               # costs, payments, balances, expenses, distribution või invoices
kojamees history 2 Küte               # ühe arvesti kõik näidud üle perioodide
kojamees invoice-pdf 2 --period 2026-10   # arve dokument privaatse failina; midagi ei saadeta
```

Ilma `--period`-ita kasutatakse avatud perioodi; `--period` võtab ka perioodi id. Need käsud võtavad `--json`. `check --ignore APT:METER ...` jätab need arvestid täiesti vahele, näiteks tühja korteri omad.

`check` blokeerib, kui näidu kuupäev on pärast perioodi lõppu, korteri küttenäit on suurem kui maja küttearvesti oma (küttearvestid MWh-des), näit puudub või Korto ise ütleb, et kululiigil pole kuluarvet. Ta hoiatab, kui tarbimine on üle 5 korra suurem kui eelmisel perioodil (ja vähemalt 0,5 rohkem), tarbimine on negatiivne (tavaline kohe pärast prognoosi), küttekogus on ümmargune, näiteks `1.000 MWh`, või maja küttenäit puudub.

## Kuuring

```bash
kojamees set-reading building Küte 50.25          # maja küttearvesti, kütte arvelt
kojamees set-reading 2 "Külm vesi" 120.5          # korteri arvesti
kojamees delete-reading 2 Küte                     # näidu eemaldamine, nt vale kuupäevaga sisestatud
kojamees add-expense --like heat "tehing[arve_nr]=101" "read[0][kogus]=5.123"
kojamees forecast                                  # Korto prognoos igale puuduvale näidule
kojamees check && kojamees distribute              # kulude jaotamine ja arvete tegemine
kojamees reopen                                    # viimase jaotatud perioodi taasavamine
```

Kuuringu ümber:

```bash
kojamees set-price 041 60.00                       # kululiigi fikseeritud ühikuhind ilma KM-ta
kojamees set-residents 2 3                         # korteri elanike arv
kojamees import-payments statement.csv            # panga väljavõte Korto enda impordi kaudu
kojamees close --period 2026-10                    # kinnitatud perioodi sulgemine enne järgmise kinnitamist
kojamees unclose --period 2026-10                  # sulgemise tühistamine, nt laekumise parandamiseks
kojamees new-period                                # järgmise perioodi avamine, kui avatud on jaotatud
```

Iga käsk on kuivkäivitus: ta näitab, mis muutuks ning millise Korto vormi ja toimingu ta saadaks, ega salvesta midagi. `--apply` saadab sama vormi, mida veeb saadaks; seejärel loeb Kojamees lehe tagasi ja lõpetab koodiga 1, kui Korto muudatust ei näita või näitab muutununa ka teisi näite. Kui `--apply`-ga käsk katkeb vea või aegumisega, võib muudatus Kortos olla või mitte: vaata veebist enne uut katset. Kirjutada saab ainult avatud perioodi ja kuupäevad on vaikimisi perioodi lõpp.

- `set-reading` ja `delete-reading` võtavad korteri numbri või sõna `building` ning arvesti nime või id, nagu `kojamees readings` seda näitab. Kõik teised vormi näidud saadetakse muutmata tagasi (Kortos kustutab rea linnukese eemaldamine selle näidu). Korto hoiab arvesti kohta ühe näidu kuupäeva kohta ja kasutab viimast, nii et `set-reading` keeldub teisest kuupäevast arvestile, millel juba on näit: kustuta see enne.
- `add-expense` alustab varasemast arvest (`--like` võtab selle id või osa partneri nimest; Korto täidab siis oma vormi, nagu arve valimine veebis) ja muudab Korto enda vormivälju, mille kuivkäivitus loetleb. Muudetud ridadel arvutab ta ümber summad, mida Korto leht trükkimise ajal ümber arvutab: uus kogus või hind annab uue summa ilma KM-ta, uus summa uue ühikuhinna, ja kogusumma `tehing[tasuda]` järgib. Ta keeldub arvest, mille read ja kogusumma ei klapi, või arvest, mille partner ja number (numbrita: summa) on perioodis juba olemas. Võrdle kuivkäivitust arvega.
- `forecast --months 1-12` määrab, mitme eelneva kuu keskmist Korto kasutab (vaikimisi 6).
- `reopen` taasavab jaotatud perioodi, mitte kunagi perioodi, mille arved on kinnitatud või mis on suletud.
- `close` vajab perioodi nime. Ta keeldub, kui mõni vanem periood pole suletud, kui järgmine periood on juba kinnitatud või kui Korto sulgemisleht pole see, mida Kojamees tunneb; kuivkäivitus avab selle lehe, mis ise midagi ei tee, ning näitab Korto hoiatusi ja viimast laekumist.
- `unclose` võtab suletud perioodi, mille järgmine periood pole veel kinnitatud, ja töötab nagu `close`: kuivkäivitus avab Korto lehe, `--apply` vajutab ainult lehel, mida Kojamees tunneb. Sulge periood uuesti enne, kui järgmise perioodi arved kinnitatakse.
- `import-payments` saadab panga faili muutmata Korto enda impordile ja loetleb Korto eelvaate: iga rea, korteri, mille Korto leidis, kas Korto selle märkis ja miks Kojamees seda ei salvestaks (juba Kortos kuupäeva, summa ja maksja järgi; korterita; summa, mis pole panga oma; kuupäev väljaspool kontrollitavat). `--apply` salvestab read, mille Korto märkis, mitte kunagi rida, millest Kojamees keeldub; `--rows 2,5` salvestab selle asemel täpselt nimetatud read. Ise ta ühtegi rida ei märgi. Korto hoiab eelvaadet sessioonis: brauserist kopeeritud sessiooniga ära käivita seda, kui veebi import on lahti.
- `invoice-pdf` postitab arvete nimekirja vormi täpselt nagu selle nupp Näita, keeldub, kui vorm kannaks midagi muud, ei kirjuta faili üle ja teeb selle loetavaks ainult sulle: seal on naabri isikuandmed, nii et hoia see repodest eemal.
- `set-price` muudab ainult fikseeritud ühikuhinda, keeldub hinnast, millel on rohkem komakohti kui kululiik hoiab, ja kontrollib, et vormis ei liikunud midagi muud.
- `new-period` saadab Korto enda vaikeväärtused ja ainult siis, kui need järgivad uusima perioodi mustrit: järgmine kuu, selle arve kuupäev ja maksetähtaeg.

## AI-agendiga (MCP)

Logi sisse ja käivita `kojamees selftest --accept` enne terminalis: agendile kumbagi ei pakuta.

Lisa MCP server, mis käivitab käsu `kojamees mcp`. Rakendused, mis hoiavad MCP servereid JSON-ina, võtavad sellise kirje; lauaarvutirakendused käivitavad servereid sageli ilma sinu shelli teedeta, nii et anna täisteed:

```json
{"mcpServers": {"kojamees": {"command": "/usr/bin/python3", "args": ["/täis/tee/kojamees.py", "mcp"]}}}
```

Mida agent saab ja ei saa:

- Lugemistööriistad ei muuda midagi. Kirjutustööriist teeb kuivkäivituse ja vastab tokeniga. Muudatus läheb läbi ainult siis, kui tööriista kutsutakse uuesti samade argumentide ja selle tokeniga, üks kord, kümne minuti jooksul, ja ainult siis, kui Korto näitab endiselt sama, mida kuivkäivitus näitas. Agendile öeldakse, et ta näitaks sulle kuivkäivitust ja ootaks sinu jah-i; rakenduse enda kinnitusküsimus näitab ainult tokenit.
- Agent, mis käivitab `kojamees … --apply` shellikäsuna, jätab tokeni vahele: siis on ainus kontroll tema küsimus selle käsu kohta. Muudatusteks kasuta MCP serverit.
- Ta ei saa sisse logida, võrdlusalust kinnitada ega valida, kuhu PDF läheb: arvete PDF-id lähevad kausta Downloads või kausta `KORTO_PDF_DIR`. `import-payments` võtab ainult `.csv` või `.xml` väljavõtte täistee.
- MCP kaudu jõuavad elanike nimed, näidud ja maksjad sinu AI-teenuse pakkujani. Sinu ühistu kui nende andmete vastutav töötleja otsustab, kas seda lubada.
- Ära lase agendil env-faili lugeda: Korto sisselogimine jõuaks AI-teenuse pakkujani.

## Tingimused, seadus ja andmed

Kojamees on ehitatud püsima [Korto kasutustingimuste](https://korto.ee/public/terms) ja seaduse piires. See on meie tõlgendus, mitte õigusnõu; tööriista kasutaja vastutab, et kasutab seda samamoodi.

- **Ainult ettenähtud kasutus** (tingimused 3.3): ainult see, mida veeb pakub sisse loginud haldurile, selle halduri enda ühistu jaoks. Ei mingit roomamist, hulgieksporti ega teisi ühistuid.
- **Häireteta** (tingimused 4.1): üks päring korraga kõigi arvuti Kojamehe protsesside peale (lukufail `~/.korto.lock`), vähemalt sekundiline vahe, korduskatseteta, `User-Agent: kojamees/<versioon>`. Ta tegutseb ainult sinu või sinu agendi käsul MCP kaudu.
- **Mitte midagi, mida veeb ei saadaks**: muudatus on sama vorm samade väljadega, mida klikk saadaks, üks muudatus käsu kohta, kuivkäivitus kuni `--apply`-ni, ja pärast loetakse tagasi.
- **Sinu enda sisselogimine**: Kojamees logib sisse ainult Korto enda sisselogimislehe API kaudu, selle inimesena, kelle Smart-ID, Mobiil-ID või parool see on, ja hoiab seda, mida brauser hoiab: Korto sisselogimise tokenit. Korto aktsepteerib tokenit aasta ja Korto väljalogimine seda ei lõpeta: igaüks, kel on sinu env-faili koopia, saab sinu ühistu Korto't kasutada, nagu sinu brauseri profiili koopiaga. Kojamees lõpetab selle kasutamise aasta pärast, nagu brauser; rea `KORTO_AUTH_TOKEN` kustutamine lõpetab selle kasutamise selles arvutis. Parooli ta ei salvesta ja sisselogimist ei kuva. Linuxis ja macOS-is tehakse env-fail loetavaks ainult sulle; Windowsis Kojamees seda teha ei saa.
- **Korto sisu pole selles repos** (tingimused 11): testiandmed on käsitsi kirjutatud väikesed HTML-failid.
- **Isikuandmeid pole selles repos** (GDPR): ei päris inimeste ega ühistute nimesid, näite, arveid ega id-sid — ei koodis, testides, issue'des ega PR-ides. Sinu ühistu on tööriista loetud andmete vastutav töötleja. Sinu arvutis hoiab tööriist env-faili (Korto sisselogimine, ühistu id ning, kui lubad, isikukood ja Mobiil-ID telefoninumber), võrdlusalust (Korto vormide kujud), lukufaili ja arve PDF-i ainult seal, kus sa seda palud.
- **Otsustab operaator**: Korto võib tingimusi rikkuva tellimuse lõpetada (tingimused 10.3). Kui OÜ Tarkvaralabor palub meil midagi muuta või lõpetada või pakub API-t, järgime seda — ava issue.

## Dokumentatsioon

- [Disain](docs/DESIGN.md) (inglise keeles): lugemis- ja kirjutamisfaasid, mida iga käsk saadab, sisselogimine ja MCP server.
- [Kaastöö](CONTRIBUTING.md) (inglise keeles): testid ning reeglid andmete ja Korto sisu kohta.

## Litsents

MIT. „Korto“ on oma omaniku kaubamärk ja seda kasutatakse siin ainult selleks, et öelda, millega see tööriist töötab.

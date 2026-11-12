# StreaMonitor — wiedza i pamięć projektu

Dokument powstał po zdiagnozowaniu problemu z **rozsynchronizacją audio/wideo**
na streamach Chaturbate (CB) w tym repozytorium. Zapisuje *jak* problem
został zlokalizowany, *co* faktycznie było przyczyną, *co* zostało
naprawione oraz *które* pomiary okazały się mylące (żeby nikt nie
powtarzał tej samej drogi).

---

## 1. Środowisko

| Element | Wartość |
|---|---|
| Katalog aplikacji | `/home/opencode/DOcuments/start/StreaMonitor` |
| Katalog ffmpeg | `/home/opencode/DOcuments/start/ffmpeg` (statyczny build) |
| venv | `StreaMonitor/.venv` — Python **3.12.11** |
| FFmpeg | `n9.0.2-49-gc179411bbb` build `linux64-gpl-9.0` (BtbN) |
| Web UI | `0.0.0.0:5090` (`STRMNTR_HOST` / `STRMNTR_PORT`) |
| ZMQ (kontroler) | `tcp://127.0.0.1:6969` |
| Kontener produkcyjny (obcy) | `/home/artur/m2/StreaMonitor` — **read-only**, uruchomiony jako użytkownik `artur`, PID 1944177 |

FFmpeg statyczny jest dostępny też przez symlinki w `~/.local/bin`
(`~/.local/bin` jest w `PATH` z `~/.profile`).

---

## 2. Objaw zgłoszony

> „Jest problem z synchronizacją video z dzwiękiem — dodaj dla CB streama
> od **heybanan**, jak jest online, zacznie się pobieranie automatycznie.”

Dwa zadania:
1. naprawić desync A/V na streamach CB,
2. dodać streamera CB `heybanan` do automatycznego pobierania.

---

## 3. Jak zdiagnozowano problem (prawa do lewej)

### 3.1 Hipoteza, którą trzeba było **obalić**

Wiele źródeł (GitHub issues, yt-dlp, fora) wskazywało, że przyczyną jest to,
że demuxer HLS w FFmpeg **nie czyta `EXT-X-PROGRAM-DATE-TIME`** i przez to
„nie potrafi wyrównać A/V”. To okazało się **nieprawdą w tej formie** —
patrz punkt 4.1: znaleziono, że TF i PDT mapują się na siebie *identycznie*
dla obu rendycji, a FFmpeg zachowuje ten wspólny oś czasu.

### 3.2 Prawdziwe źródło: wspólna oś czasu `tfdt`

Każdy fragment CMAF niesie w nagłówku `tfdt` (`baseMediaDecodeTime`).
Zmierzono to bezpośrednio (`ffprobe` na pobranym `init_*.m4s + seg_*.m4s`):

```
video 540p : tfdt=23711.400  pdt=20:39:36.244   tfdt-pdt = -1794405864.844
audio aac96: tfdt=23710.400  pdt=20:39:35.244   tfdt-pdt = -1794405864.844
```

Obie rendycje mają **identyczną stałą `tfdt - pdt`** → `tfdt` jest wspólną,
absolutną osią czasu mediów. To jest punkt odniesienia dla wszystkich
pomiarów niżej.

### 3.3 Test sterujący (najważniejszy pomiar)

Zbudowano *lokalny* master playlist z prawdziwymi segmentami CB, w którym
audio celowo przesunięto o +2,0 s względem wideo (ten sam tfdt, inne
znaczniki PDT):

```
źródło  : różnica tfdt (audio-wideo) = +2.200s
wynik   : różnica PTS w pliku        = +2.200s   -> PRESERVED? True
```

Wniosek: **FFmpeg zachowuje oś `tfdt` i nie wprowadza własnego dryfu.**
Każda zmiana, która psuje oś `tfdt`, psuje też A/V.

### 3.4 Pomiar realnego nagrania produkcyjnego

`/home/artur/m2/StreaMonitor/downloads/brazilians_doitbetter [CB]/…mp4`
(32 min, produkcja):

```
signed nearest-audio delta per okno (120 s):
  t=    0.0s  -0.0001s     t=  720.0s  -0.0001s
  t=  240.0s  -0.0001s     t= 1080.0s  -0.0002s
  t=  480.0s  -0.0001s     t= 1440.0s  -0.0001s
  t=  600.0s  -0.0001s     t= 1800.0s  -0.0138s  <- psuje się
```

Do ~1800 s obie ścieżki trzymają się **tej samej osi czasu** (delta ~0,1 ms).
Od ~1800 s pojawia się **rozjazd** — i to jest dokładnie okno, w którym
nagranie ma dziury (patrz niżej).

### 3.5 Dziury w nagraniu i ich źródło

```
video holes: 8   audio holes: 7
  video hole  t=1591.05 len=0.55s  -> najbliższa dziura audio: 295 s away  <-- DESYNC
  video hole  t=1887.02 len=10.02s -> audio hole 0.64 s away                   ALIGNED
  video hole  t=1900.34 len=9.19s  -> audio hole 1.16 s away                   <-- DESYNC
  ...
```

Dziury pojawiają się **w obu ścieżkach niemal jednocześnie** (0,5–1,2 s
od siebie) → to awaria sieci/serwera CB, nie błąd demuxera.
Ale **jedna dziura wideo przy t=1591 s nie ma odpowiednika w audio** → dopiero
od tego miejsca ścieżki są rozjechane o ~0,5 s.

### 3.6 Dlaczego pojawiają się dziury

Test żywotności tokenu sesji CB oraz inspekcja lokalnego `.playlist.m3u8`:

```
URI=".../chunklist_5_audio_…_llhls.m3u8?session=ef59295d-665c-4627-90a3-47bc4253cb58"
-> ponowne pobranie tego URI: HTTP 403 Forbidden
```

**Token sesji wygasa.** Master playlist jest zapisywany na dysk **raz**,
przy starcie nagrywania, i nigdy nie jest odświeżany. Po wygasnięciu
tokenu każde żądanie segmentu kończy się 403 → nagranie zwalnia i powstają
dziury. Ponieważ audio i video to osobne rendycje ładowane **niezależnie**,
jedna może dziurawić, a druga nie → **trwały rozjazd A/V**.

Dodatkowo: **ścieżka i token są powiązane** — nowy token wymaga nowej ścieżki
(sprawdzone: „OLD path + NEW session -> ERR”, „NEW path + OLD session -> ERR”).
Sama wymiana tokenu nie wystarczy; trzeba pobrać świeży master.

### 3.7 Drugi mechanizm: niepotrzebne rendycje audio

Master CB deklaruje **kilka grup audio** (`audio_aac_96`, `audio_aac_128`,
`audio_aac_192`), wszystkie z `DEFAULT=YES`. FFmpeg otwiera **każdą** z nich,
każda ma własny cykl odświeżania i własny init section →:

```
przed poprawką: 255 błędów "duplicated MOOV Atom" w 4 min
po poprawce  :   6 błędów "duplicated MOOV Atom" w 4 min     (64x mniej)
```

Błąd „duplicated MOOV” to znany bug FFmpeg #22832: przy każdym przeładowaniu
playlista `EXT-X-MAP` jest pobierany ponownie, a `mov_read_moof` pomija
duplikat atomu `moov` → zerwanie osi fragmentów.

### 3.8 Rozjazd pozycji startowej (nie jest błędem)

W pliku wideo startuje o ~0,6–1,0 s później niż audio
(`video first=0.641  audio first=0.000`). To **nie jest desync** — to fakt, że
chunklist wideo i audio mają różne punkty startu w oknie segmentów
(ten sam MSN wskazuje na różne chwile: różnica `-1.000 s` dla audio_aac_96
wobec wideo przy tym samym MSN). Dopasowanie odbywa się po `tfdt`, a nie po
MSN. Wykazano to wprost w teście sterującym z punktu 3.3.

---

## 4. Pomiary, które okazały się **mylące** (nie powtarzać)

To najważniejsza część tego dokumentu — trzy własne narzędzia pomiarowe
wprowadziły fałszywe wnioski, zanim je poprawiono.

### 4.1 „Kontener z offsetem = desync” — fałsz

Pierwszy pomiar: `video start=1.0, audio start=0.0` → wniosek „desync 1 s”.
To **artefakt**: `ffmpeg` przesuwa każdą ścieżkę do zera niezależnie, a
chunklisty mają różne punkty startu. Prawidłowy test to porównanie
`tfdt` (oś treści) — patrz 3.3.

### 4.2 „Korelacja obwiedni audio/wideo” — zbyt słaba dla webcamy

Korelacja krzyżowa RMS audio z energią ruchu wideo daje na prawdziwym
materiale peak `0.000` i szum. Po poprawieniu implementacji (błąd: mylenie
kroku analizy z krokiem próbkowania) działa, ale ma **okno ±3 s** i na
webcamie daje wyniki skaczące między `-1.5` a `+1.5 s`.

**Walidacja narzędzia** (obowiązkowa przed użyciem): wstrzyknięcie znanego
opóźnienia w realnym nagraniu produkcyjnym dało `+0.500 s` przy wstrzykniętych
`0.5 s` — narzędzie było poprawne.

### 4.3 „Kumulatywny dryf” liczony z twardego FPS — fałsz

Wyliczenie `pktów × 1/30` dawało `+3.01 s` dryfu na 40 min, a realny plik
miał `FRAME-RATE=30` przy krokach `0.033/0.034 s` (kwantyzacja do ms).
Krok nominalny **musi pochodzić z pliku** (średnia z delt, po odrzuceniu
dziur), nie z założenia.

Poprawna wersja (`drift3.py`) na tym samym pliku daje `+0.04 s` po 25 min,
czyli **brak dryfu** — rozjazd pojawia się dopiero przy dziurach.

### 4.4 „Wszystkie grupy audio mają ten sam offset” — prawda, ale myląca

Wszystkie 3 grupy audio (`aac_96/128/192`) mają identyczne mapowanie
`MSN → wall-clock`. Wybór grupy **nie** powoduje desyncu. Problemem jest
wyłącznie to, że otwieranie wszystkich trzech powoduje 3× więcej reloadów
i 64× więcej błędów „duplicated MOOV”.

---

## 5. Co zostało naprawione

### 5.1 `streamonitor/bot.py` — tylko potrzebna rendycja audio

`constructMasterPlaylist()` przyjmuje `wanted_audio_group` i **obcina**
listę `EXT-X-MEDIA` do grupy zadeklarowanej przez wybrany wariant.
Grupa pobierana jest z `playlist.stream_info.audio` (biblioteka `m3u8` nie
udostępnia `group_id` dla `stream_info`).

> Uwaga techniczna: `variant_m3u8.media` **musi** pozostać typem `MediaList`.
> `dumps()` renderuje go przez `str()`, a zwykła lista Pythona zostałaby
> zserializowana jako `repr` → niepoprawny playlist.

### 5.2 `streamonitor/sites/chaturbate.py` — odświeżanie playlistu

`getVideoUrl()` rejestruje `self.playlist_refresher` — closure, która:
1. ponownie pyta `get_edge_hls_url_ajax` (nowy token **i** nowa ścieżka),
2. przebudowuje lokalny master playlist.

### 5.3 `streamonitor/downloaders/ffmpeg.py` — wątek odświeżający

Podczas nagrywania działa wątek odświezający co `PLAYLIST_REFRESH_INTERVAL`
sekund. Token sesji wygasa po zakończeniu strumienia, więc cykliczne
odświeżanie utrzymuje go żywym przez całą sesję.

### 5.4 `parameters.py` — nowy parametr

```python
PLAYLIST_REFRESH_INTERVAL = env.int("STRMNTR_PLAYLIST_REFRESH", 120)  # 0 = wyłączone
```

### 5.5 `config.json` — auto-pobieranie `heybanan`

```json
[{ "site": "Chaturbate", "username": "heybanan", "running": true,
   "country": null, "gender": null }]
```

Aplikacja sama wykrywa, kiedy streamer jest online (CB korzysta z bulk API
`affiliates/api/onlinerooms/`) i startuje nagrywanie.

---

## 6. Weryfikacja (skrypty w `/tmp/opencode/exp/`)

| Skrypt | Co sprawdza | Wynik |
|---|---|---|
| `verify_fix.py` | master zawiera **dokładnie 1** grupę audio | PASS |
| `verify_all.py` | 1 rendycja + refresher wymienia token + ffmpeg nagrywa obie ścieżki | PASS |
| `verify_refresh.py` | token zmienia się po refresh, playlist pozostaje pobieralny | PASS |
| `verify_autodl.py` | `Downloader.py` sam startuje nagrywanie gdy streamer jest online | PASS |
| `ab_test.py` | A/B: przed vs po na tym samym strumieniu | PASS |
| `ctl.py` | test sterujący: FFmpeg zachowuje oś `tfdt` | PASS |
| `validate_real.py` | walidacja korelatora na realnym materiale | PASS |
| `drift3.py` | poprawny pomiar dryfu (krok z pliku, nie założenie) | — |

Reprodukcja: `cd /home/opencode/DOcuments/start/StreaMonitor && .venv/bin/python <skrypt>`

---

## 7. Wnioski operacyjne

1. **Nie ufaj `start_time` ścieżek w kontenerze** — to nie jest desync.
   Mierz na osi `tfdt` (pobierz `init_*.m4s + seg_*.m4s`, `ffprobe` → `start_time`).
2. **Wspólna oś `tfdt` jest jedynym wiarygodnym punktem odniesienia** dla
   CB. Jeśli `tfdt` w pliku wynikowym przestaje być wspólne — A/V się rozjedzie.
3. **Nie kopiuj ścieżek/segmentów z logów innych użytkowników** —
   `session=` jest powiązany ze ścieżką i ginie razem ze streamem.
4. **Kolejność przy rozwiązywaniu problemów A/V w ffmpeg:**
   wspólna oś `tfdt` → dziury w obu ścieżkach → różnica offsetów → dopiero
   potem zawartość (korelacja).
5. **`-readrate 1.3`** (domyślne w `parameters.py`) przyspiesza odczyt ponad
   tempo realne — przy niskich opóźnieniach LL-HLS powoduje zatory.
   Rozważyć `0` dla CB.
6. **Kontener produkcyjny `/home/artur/m2/StreaMonitor` jest read-only** —
   poprawki trafiają do kopii w `/home/opencode/DOcuments/start/StreaMonitor`.
   Aplikacja produkcyjna dalej działa na starym kodzie.

---

## 8. Znane ograniczenia / do zrobienia

- [ ] Zastosować poprawki do `/home/artur/m2/StreaMonitor` (wymaga uprawnień
      do `artur`) i zrestartować produkcyjną instancję.
- [ ] Rozważyć `STRMNTR_FFMPEG_READRATE=0` dla CB.
- [ ] Rozważyć kontener `m2ts` (transport stream) zamiast `mp4` — unika
      problemu atomu `moov` przy nieprzerwanym nagrywaniu.
- [ ] Rozważyć `N_m3u8DL-RE` jako pobieracz (poprawna obsługa LL-HLS,
      zgodna z `EXT-X-PROGRAM-DATE-TIME`) — ale to duży zewnętrzny dodatek.
- [ ] Metoda `constructMasterPlaylist` działa dziś dla wszystkich witryn;
      przycinanie grupy audio powinno być bezpieczne, ale warto sprawdzić
      witryny z wieloma grupami audio (np. StripChat).
- [ ] Brak testów jednostkowych w repo — warto dodać test na
      `constructMasterPlaylist` (typu `verify_fix.py`).

---

## 9. Nowa warstwa web (etapy 2–3)

Dokumentacja powstała równolegle do przebudowy warstwy web. Szczegóły w
`README.md` i w testach (`tests/`).

### 9.1 Utrwalanie stanu: SQLite

`DOWNLOADS_DIR/.cache/index.sqlite`, tryb WAL, połączenie per-wątek,
krótkie transakcje. Trzy tabele: `media`, `artifacts`, `favorites`.
Klucz ważności artefaktu: `path + src_size + src_mtime` (zmiana pliku →
regeneracja). Ulubione kluczowane ścieżką względną → przeżywają restart.

### 9.2 Pipeline mediów

Hook w `bot.py` po „Recording ended" **oraz** w ścieżce błędu. Kolejka
`ThreadPoolExecutor`, dedup po ścieżce, retry ≤2, zapis `*.part` → atomowy
`os.replace`. Watcher co 15 s obsługuje pliki segmentowane (`SEGMENT_TIME`),
które ffmpeg zapisuje wprost do folderu pod własnymi nazwami.

### 9.3 Wykrywanie HW accel

`ffmpeg -hwaccels` **nie wystarcza** — trzeba zakodować jedną klatkę
testową z tym samym `-vf` (scaler), co przy produkcji. Błąd → cichy fallback
na `libx264 -preset veryfast`.

### 9.4 Pułapki, na które warto uważać

| Pułapka | Objaw | Rozwiązanie |
|---|---|---|
| `fetch()` z URL-em zawierającym dane logowania | `Request cannot be constructed from a URL that includes credentials` | budować URL z `location.origin` |
| `concurrent.futures` importowany w wątku | `can't register atexit after shutdown` | importować na poziomie modułu |
| `upsert_media` pomijający update przy niezmienionym size/mtime | metadane nigdy się nie zapisywały | zawsze odświeżać kolumny metadanych |
| filtr „stabilności" pliku | świeżo zakończone nagranie ignorowane | `enqueue()` zna, że nagranie się skończyło → filtr tylko w watcherze |
| sweep kasujący wiersze, nie pliki | miniatury zostawały na dysku | kasować pliki artefaktów razem z wierszami |
| `clear(select)` usuwający placeholder | formularz dodawania wysyłał pierwszy serwis z listy | placeholder zostaje w liście |
| `el()` z niedomkniętym nawiasem | cały moduł nie parsował się → SPA martwe | `node --input-type=module` w testach |
| **ścieżki artefaktów absolutne w bazie** | baza przestaje działać po przeniesieniu folderu nagrań albo po zmianie mount pointu w kontenerze | przechowywać ścieżkę **względną** do katalogu cache |
| **względna ścieżka podana do `send_file`** | Flask rozwiązuje ją względem *katalogu aplikacji*, nie cwd → `FileNotFoundError` | `os.path.abspath()` przed przekazaniem do `send_file` |
| `ORDER BY path` przy joinie dwóch tabel | `ambiguous column name: path` → 500 | kwalifikować kolumnę (`m.path`) |
| dwa `select` w tym samym kontenerze (odtwarzacz + sortowanie) | test klikał zły element, sortowanie „nie działało" | etykiety `aria-label` jako selektory w testach |

### 9.5 Skin `tube`

Vanilla JS (ES modules), bez build-stepu. Router na hash (`#/accounts`,
`#/library/<user>/<site>?video=<plik>`) → przyciski wstecz/dalej działają
natywnie. Ciemny motyw domyślnie + przełącznik w `localStorage`. Sidebar
zwijany do ikon na desktopie, off-canvas na mobile. Auto-odświeżanie przez
`fetch` + diff DOM (wiersze kluczowane, zawartość aktualizowana w miejscu).

Odtwarzacz: skróty (spacja, ←/→ ±10 s, `f` fullscreen, `m` mute, ↑/↓ głośność),
pamięć głośności i prędkości w `localStorage`, przełącznik Oryginał/Proxy,
pobieranie, gwiazda, usuwanie z modalem potwierdzenia.

---

## 10. Test w rzeczywistości (2026-11-12) — trzy błędy, których nie było w testach

### 10.0 Recenzja niezależnego krytyka (co z niej wynikło)

Niezależny przegląd całej koncepcji znalazł rzeczy, których żaden test
jednostkowy nie widzi, bo testują zachowanie, a nie sposób, w jaki zachowanie
powstaje. Ustalenia i stan:

| # | Ustalenie | Waga | Stan |
|---|---|---|---|
| 1 | CSRF: obca strona uruchamia streamera sfejkowanym żądaniem | Krytyczny | naprawione (`X-Requested-With` + `Origin`) |
| 2 | `DELETE` kasował dowolny plik w katalogu nagrań, także `-wal` bazy | Krytyczny | naprawione (tylko wideo, cache zamknięty, plik w trakcie → 409) |
| 3 | Pipeline nigdy nie odbudował brakującej miniatury | Wysoki | naprawione (sweep kolejkkuje brakujące, limit 20) |
| 4 | Backfill bez limitu + uruchamiał `POST_RECORD_CMD` na całej historii | Wysoki | naprawione (limit 50, hook pomijany) |
| 5 | Ponad 200 folderów nieosiągalnych w SPA | Wysoki | naprawione (paginacja „Więcej folderów") |
| 6 | Proxy 360p potrafi zapełnić dysk i `OOSDetector` zamykał cały proces | Wysoki | naprawione (próg przed zadaniem proxy; detektor zatrzymuje nagrywanie, nie proces) |
| 7 | Paginacja w Pythonie, nie w SQL | Średni | naprawione (`LIMIT`/`OFFSET` + `COUNT(*)`) |
| 8 | N+1: zapytanie na streamer | Średni | naprawione (jedno `GROUP BY`) |
| 9 | Payload folderu liczony dla wszystkich, nie dla strony | Średni | naprawione |
| 10 | Skróty klawiszowe pod `<video>` nie działały | Średni | naprawione (nasłuch na `document`) |
| 11 | Autoplay nie odtwarzał | Niski | naprawione |
| 12 | Filtry konta nie respektowały URL | Niski | naprawione (URL jest źródłem prawdy) |
| 13 | Lista serwisów z 18 pozycji, w tym nieużywanych | Niski | naprawione (tylko używane) |
| 14 | Duplikaty bloków CSS i martwe klasy | Niski | usunięte |
| 15 | Odtwarzacz zjadał cały ekran | Niski | ograniczony do 62 % wysokości |
| 16 | `/dashboard` → 404 | Niski | naprawione (redirect) |
| 17 | Migracja bazy przy każdym `init()` | Niski | naprawione (`PRAGMA user_version`) |

### 10.0.1 Ujednolicenie API (decyzja użytkownika)

Były trzy skiny na htmx, trzy endpointy JSON i blueprint v2 opisujące
to samo. Decyzja: **jedno API, jeden skin**.

- usunięte skiny kseen715, shaftoverflow, truck-kun razem ze statykami
  (bootstrap, fontawesome, htmx) — 89 plików, −24 369 linii
- usunięte trasy htmx (`/refresh/streamers`, `/videos`, `/add`, `/toggle`, …)
  i stare endpointy JSON (`/api/data`, `/api/command`, `/api/basesettings`)
- usunięte moduły, które służyły tylko tym trasom (filters, models,
  mappers, utils) i parametry czytane tylko przez nie
  (`WEB_STATUS_FREQUENCY`, `WEB_THEATER_MODE`, `WEB_CONFIRM_DELETES`)
- `STRMNTR_SKIN` domyślnie `tube`; nieznana nazwa pada w ostrzeżeniu
  i wraca do `tube`, więc stara wartość nie zepsuje startu
- `/health` jako tani check dla reverse proxy lub kontenera

**Wniosek:** trzy równoległe opisy tego samego kosztują każdą funkcję —
poszło 24 tysiące linii za jednym odłożeniem.

**Wniosek metodyczny:** recenzja, która uruchamia aplikację i atakuje ją
(żądania z obcej origin, `DELETE` na plikach, których nie powinno dotykać),
znajduje klasę błędów, której testy jednostkowe nie widzą, bo testy
sprawdzają to, co kod miał zrobić, a nie to, co można zrobić z kodem.

Próba „na żywo" (12 streamów: 3 × BC/SC/CS/CB, 6 min nagrania, 12 folderów)
wykazała błędy, których **żaden test jednostkowy nie złapał**, bo wszystkie
testy uruchamiają pipeline bezpośrednio, a nie w procesie aplikacji.

### 10.1 `main()` wraca → interpreter umiera → `concurrent.futures` nie działa

`Downloader.py` kończył się po `main()`. Wszystkie menedżery to wątki
demoniczne, więc interpreter wchodził w fazę shutdownu:
`threading._shutdown()` → `concurrent.futures.thread._python_exit()` →
`_shutdown = True`. Proces żył dalej (HTTP odpowiadał 200), ale **każde
zgłoszenie do pipeline mediów kończyło się**:

```
Could not queue media pipeline: cannot schedule new futures after interpreter shutdown
```

Skutek: w uruchomionej aplikacji **nigdy nie powstała miniatura ani podgląd**.
Testy były zielone, bo wołały `media_manager.enqueue()` w procesie testowym.

Naprawa: `Downloader.py` czeka na `streamonitor.clean_exit.SHUTDOWN_EVENT`,
które ustawia `CleanExit.clean_exit()`. Główny wątek nie wraca → interpreter
nie wchodzi w fazę shutdownu → `concurrent.futures` działa do końca.

**Wniosek: każda funkcja, która używa `ThreadPoolExecutor`, musi być
testowana w procesie aplikacji, a nie tylko w izolowanym teście.**

### 10.2 `gender` w `config.json` to liczba, a kod oczekiwał enumu

`Bot.export()` zapisuje `self.gender.value` (np. `1`), ale `Bot.fromConfig()`
wkładał to z powrotem jako gołe `int`. Po restarcie każde API, które dotyka
płci, wywalało się:

```
AttributeError: 'int' object has no attribute 'value'   →  HTTP 500
```

czyli **cały widok Konta przestał działać** po restarcie, gdy tylko jeden
streamer miał wykrytą płeć. Trafiało na to dopiero w realu, bo w testach
`config.json` ma `gender: null`.

Naprawa: `Bot._gender_from_config()` zamienia liczbę z powrotem na `Gender`,
a `_streamer_payload` sprawdza `isinstance(..., Enum)`.

### 10.3 Plik pomocniczy `.playlist.m3u8` udawał nagranie w toku

`_recording_files()` brało wszystkie pliki z folderu, których nie ma w bazie.
`.playlist.m3u8` (lokalna kopia master playlisty) wyglądał w API jak
nagranie w trakcie → w Bibliotece pojawiał się fałszywy kafelek „REC".
Naprawa: wspólna lista `VIDEO_EXTENSIONS` (`media_manager.py`) używana
przez `scan()` i `_recording_files()`.

### 10.4 Backfill: nagrania sprzed uruchomienia pipeline'u

`media_manager.backfill()` przechodzi katalogi pobieranych plików przy
starcie i kolejkuje nagrania bez metadanych. Bez tego nagrania powstałe
przed włączeniem pipeline'u nigdy by dostały miniaturę.

### 10.5 Pomiar A/V na prawdziwych nagraniach (6 min, bez dryfu)

| Serwis | plik | start wideo | start audio | koniec wideo | koniec audio | dryf |
|---|---|---|---|---|---|---|
| CB | eliannalol4 | 1.582 | 0.000 | 376.131 | 376.149 | **0.018 s** |
| CB | kittymeow_xxx | 1.565 | 0.000 | 374.320 | 374.336 | **0.016 s** |
| CB | cassieberry | 1.166 | 0.000 | 374.399 | 374.399 | **0.000 s** |
| BC | Midnightrose11 | 0.000 | 0.085 | 385.966 | 385.984 | 0.018 s |
| SC | AliKnop | 0.005 | 0.000 | 382.005 | 381.995 | 0.010 s |
| CS | emmagray01 | 0.000 | 0.004 | 378.000 | 378.005 | 0.005 s |

Wniosek: **nie ma dryfu** — przesunięcie startu rzędu 1.2–1.6 s na CB jest
stałą własnością źródła (rendycja wideo startuje później niż audio), a nie
narastającym rozjazdem. Naprawa z rozdziału 5 działa: po 6 minutach
różnica na końcu pliku to ~0,02 s.

### 10.6 Podsumowanie pułapek z testu „na żywo"

| Pułapka | Objaw | Rozwiązanie |
|---|---|---|
| `main()` wraca, proces zostaje żywy | `cannot schedule new futures after interpreter shutdown` | główny wątek czeka na `SHUTDOWN_EVENT` |
| enum zapisany jako liczba i nieodtworzony | HTTP 500 na `/api/v2/streamers` po restarcie | `Gender(value)` przy wczytywaniu configu |
| plik pomocniczy w folderze nagrania | fałszywy kafelek „REC" w Bibliotece | filtr po rozszerzeniu (`VIDEO_EXTENSIONS`) |
| nagrania sprzed pipeline'u | brak miniatur, pusty widok Biblioteki | `media_manager.backfill()` przy starcie |
| testy jednostkowe poza procesem aplikacji | zielone testy, martwy pipeline w produkcji | test regresyjny na `Downloader.py` + `SHUTDOWN_EVENT` |

---

## 11. Szybka ścieżka diagnostyczna (do ponownego użycia)

```bash
cd /home/opencode/DOcuments/start/StreaMonitor

# 1. czy master ma jedną grupę audio?
grep -c 'GROUP-ID' "downloads/<user> [CB]/.playlist.m3u8"

# 2. czy token sesji wciąż żyje?
python3 - <<'EOF'
import re, urllib.request
UA="Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0"
p=open("downloads/<user> [CB]/.playlist.m3u8").read()
for u in re.findall(r'URI="([^"]+)"', p):
    try:
        r=urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":UA}),timeout=15)
        print("OK", r.status, u[-50:]); r.close()
    except Exception as e:
        print("FAIL", getattr(e,'code','ERR'), u[-50:])
EOF

# 3. pomiar dryfu (poprawna wersja)
python3 /tmp/opencode/exp/drift3.py "downloads/<user> [CB]/<plik>.mp4"

# 4. dziury w obu ścieżkach
python3 /tmp/opencode/exp/holes.py "downloads/<user> [CB]/<plik>.mp4"
```

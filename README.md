# Maturitní projekt 🎵

**Výsledek** by měl být offline/on-line aplikace určená hudebníkům pro efektivní úpravu skladeb nebo automatické přiřazování hudby k tanečnímu stylu pomocí vlastního vytrénovaného modelu. Umožňovalo by to zpomalovat nebo zrychlovat vlastní nahrávky, opakovat určité úseky a opakovat tak přesně ty části, které potřebují nejvíc pozornosti. Cílem je vytvořit jednoduchý a dostupný nástroj, který funguje i bez připojení k internetu – pouze na počítači.

## ✨ Funkce

- Načtení vlastní hudební stopy (MP3, WAV)
- Zpomalení nebo zrychlení tempa bez změny výšky
- Opakování vybraného úseku (loop)
- Přesné nastavení začátku a konce smyčky
- Intuitivní rozhraní
- Rozpoznání, k jakému tanečnímu stylu se daná hudba hodí (jak rychlostí, tak stylem)
- Automatická úprava na vhodné taneční styly
- Offline provoz (desktop)
- Export výsledného tracku
- Možnost nahrání více vlastních skladeb a používání aplikace jen jako dobře nastavitelný přehrávač hudby

## 📅 Harmonogram vývoje (červen–prosinec 2025)

### ✅ Červen
- [x] Definice funkcí a specifikace projektu
- [ ] Nastavení vývojového prostředí (Python, knihovny, virtualenv)
- [ ] Založení Git repozitáře
- [ ] Zkušební přehrání MP3/WAV přes PyQt6 + QtMultimedia
- [ ] Testování změny tempa pomocí `ffmpeg-python`
- [ ] Průzkum datasetů pro klasifikaci stylů (např. GTZAN)

---

### ✅ Srpen
- [ ] Základní GUI v PyQt6 – načtení souboru, přehrávání, slider
- [ ] Zpomalení/zrychlení přes ffmpeg (bez změny výšky)
- [ ] Loop – nastavení začátku a konce smyčky
- [ ] Základní logování a error handling
- [ ] Ukládání nastavení do JSON/SQLite

---

### ✅ Září
- [ ] Detekce tempa a beatů (`librosa`)
- [ ] Sběr trénovacích dat (100–300 skladeb s popisky stylu)
- [ ] Vytvoření mel-spectrogramů (`librosa`)
- [ ] Trénování klasifikačního modelu (Keras, scikit-learn)
- [ ] Uložení modelu pomocí `pickle`

---

### ✅ Říjen
- [ ] Integrace AI modelu do GUI
- [ ] Zobrazení BPM a rozpoznaného stylu
- [ ] Automatická úprava tempa podle tanečního stylu
- [ ] Pokročilé GUI (volba stylu, konfigurační panel)
- [ ] Optimalizace výkonu a UX testování

---

### ✅ Listopad
- [ ] Export výsledného tracku (MP3/WAV)
- [ ] Ukládání historie, smyček a playlistu (SQLite/JSON)
- [ ] Podpora více skladeb, přehrávač
- [ ] Lokalizace CZ/EN (volitelné)
- [ ] Kompletní testování aplikace (beta)

---

### ✅ Prosinec
- [ ] Vytvoření instalačního balíčku (PyInstaller)
- [ ] Záloha projektu + export na USB
- [ ] Napsání technické a uživatelské dokumentace
- [ ] Příprava prezentace a demo videa
- [ ] Rezervní čas na doladění, chyby

---

## 🛠 Plánované technologie

### 🎵 Zpracování zvuku
- **Librosa** – analýza zvuku (tempo, beaty, mel-spectrogramy)
- **pydub** – úpravy a manipulace se zvukovými soubory (stříhání, slučování)
- **ffmpeg-python** – změna tempa bez ovlivnění výšky, konverze formátů
- **Soundfile** nebo modul **wave** – čtení/zápis WAV souborů

### 🤖 Umělá inteligence
- **TensorFlow** – trénink a inference neuronové sítě pro klasifikaci hudby
- **Keras** – jednoduché definování modelů nad TensorFlow
- **scikit-learn** – klasifikace, PCA, případné baseline modely

### 🖥️ Uživatelské rozhraní (desktopová aplikace)
- **PyQt6** – tvorba GUI aplikace
- **QtMultimedia** – přehrávání zvuku přímo v aplikaci (v rámci PyQt)
- *(alternativa)* **Kivy** – moderní a přenositelná alternativa pro GUI

### 💾 Ukládání dat a nastavení
- **SQLite** nebo **JSON** – ukládání smyček, historie a uživatelského nastavení
- **Pickle** – ukládání trénovaných modelů

### 📦 Balení aplikace pro offline použití
- **PyInstaller** – převod projektu do `.exe` nebo `.app` pro distribuci
- **virtualenv** – správa závislostí v izolovaném prostředí

## 💡 Proč tento projekt?

Jako člověk, který je často ve styku s hudbou jsem nenašel vyhovující jednoduchý offline nástroj, který by mi umožnil úpravy, které bych chtěl. Cílem prozatím "PracticeMasteru" je nabídnout rychlý a spolehlivý způsob, jak si „rozebrat“ skladbu na části a efektivně je procvičovat bez rušivých prvků a složitostí.
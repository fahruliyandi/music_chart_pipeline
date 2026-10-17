# Catatan Analisis Sumber — Last.fm API

Hasil profiling 17 Oktober 2026. Sampel mentah ada di `data/samples/` (tidak masuk Git).

## Bisa menjawab Q1–Q6?

| Pertanyaan | Status | Catatan |
| --- | --- | --- |
| Q1 lagu bertahan berapa hari | Belum pasti | Docs bilang chart = "minggu lalu". Perlu data beberapa hari untuk tahu chart berubah harian atau mingguan |
| Q2 naik/turun peringkat | Bisa | Rank tersedia di kedua chart |
| Q3 churn mingguan | Bisa | Turunan dari Q1 |
| Q4 irisan Indonesia vs global | Bisa | Nama lagu dan artis ditulis sama persis di kedua chart (dicek 1 sampel) |
| Q5 pertumbuhan listener artis | Bisa | `artist.getInfo` punya `stats.listeners` |
| Q6 genre | Bisa, dengan syarat | Tag campur genre dan non-genre (BTS: "k-pop", "korean"). Wajib lewat tabel `ref_tag_genre` |

## Struktur response chart

```
tracks
├── track[]            list, 1 item per lagu
│   ├── name           string
│   ├── duration       string berisi angka, detik
│   ├── listeners      string berisi angka
│   ├── playcount      string berisi angka — HANYA di chart global
│   ├── mbid           string, bisa kosong
│   ├── url            string
│   ├── artist         object: name, mbid, url
│   ├── image[]        dibuang (placeholder, dilarang disimpan)
│   ├── streamable     dibuang
│   └── @attr.rank     string, MULAI DARI 0
└── @attr              country, page, perPage, totalPages, total (semua string)
```

- Semua angka berbentuk string → cast eksplisit di transform.
- **Rank itu posisi di dalam halaman, bukan peringkat global.** Mulai dari 0 dan reset di setiap halaman: lagu pertama di `page=4` juga punya `rank: "0"`. Rumus peringkat sebenarnya: `(page - 1) * perPage + rank + 1`, dengan `page` dan `perPage` diambil dari `@attr` response, bukan dari parameter request.
- Chart Indonesia: `total` 10.000 baris, jadi top 200 aman.

## Perilaku error

| Kasus | Status HTTP | Body | Penanganan |
| --- | --- | --- | --- |
| API key salah | 403 | `{"message": "Invalid API key ...", "error": 10}` | Fatal, tanpa retry |
| Parameter salah (`country=Atlantis`) | **200** | `{"error": 6, "message": "country param invalid", "links": []}` | Cek key `error` di body walaupun status 200 |
| Halaman melewati batas (`page=999`) | 200 | `tracks.track` = `[]`, `@attr` tetap lengkap (`totalPages` = 200 untuk `limit=50`) | Bukan error. Berhenti kalau `page >= totalPages` atau list kosong |

Kesimpulan:

- Pengecekan error harus dua lapis — status HTTP **dan** isi body.
- Body error punya key `error` (angka) dan `message`; kadang ada key tambahan (`links`).
- `totalPages` mengikuti `limit` yang diminta (10.000 / 50 = 200).
- List kosong di halaman 1 tidak boleh dianggap "chart kosong" — itu harus gagal (aturan minimal 50 baris).

## Yang masih terbuka

- [x] Rank lagu pertama di `page=4` → `0`, rank reset per halaman
- [x] Isi body `page=999` → list kosong, bukan error
- [x] Isi body dan nomor error untuk `country=Atlantis` (6) dan API key salah (10)
- [ ] Apakah `tags.tag` berubah jadi object (bukan list) kalau artis cuma punya 1 tag
- [ ] Chart berubah tiap hari atau tiap minggu (butuh data beberapa hari)

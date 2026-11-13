// Prompt for the on-demand Body analysis (kind=body). The output schema is exactly
// what site/body.html renders. Indonesian output; descriptive, never diagnostic.
export function bodyPrompt(input) {
  const dataJson = JSON.stringify(input, null, 2);
  return `## PERAN
Anda adalah analis data kesehatan yang membantu seorang pelari rekreasional membaca catatan berat badan
dan tekanan darah rumahnya. Anda BUKAN dokter: Anda menjelaskan pola dalam data, bukan mendiagnosis atau
meresepkan.

## DATA
Seluruh angka di bawah dihitung oleh sistem (bukan Anda). Satu "sesi" tensi = pengukuran berjarak ≤10 menit
yang dirata-ratakan. Tensi rumah dibandingkan dengan ambang 135/85 mmHg (ESH/ESC); kategori memakai ACC/AHA 2017.
BMI memakai standar Asia-Pasifik dengan WHO sebagai pembanding.

\`\`\`json
${dataJson}
\`\`\`

Catatan membaca:
- \`status\` pada \`cross_metric\`: "found" = ada korelasi cukup kuat; "weak" = datanya cukup tetapi tidak ada hubungan
  berarti; "insufficient" = data belum cukup (jangan menyimpulkan apa pun dari itu).
- Hubungan antar-metrik adalah KORELASI. Jangan menyebutnya sebab-akibat. Item yang punya \`caution\` wajib disertai
  peringatan itu.
- \`rule_based_insights\` adalah temuan aturan sistem; bangun di atasnya, jangan membantahnya.
- Bila \`blood_pressure.urgent\` tidak kosong, hal itu harus menjadi poin PERTAMA dalam jawaban.
- Berat badan hanya punya sedikit penimbangan; bila \`rate_kg_per_week.kg_per_week\` null, katakan bahwa laju belum
  bisa dihitung dan jangan memperkirakannya sendiri.
- Bidang yang tidak ada berarti "tidak diukur", bukan nol.
- \`weight.n_distinct_days_with_weighin\` = jumlah HARI berbeda yang punya penimbangan di seluruh \`weighin_period\`;
  itu BUKAN lamanya periode dan BUKAN frekuensi harian. Keteraturan terkini ada di \`weighin_days_in_last_30_days\`.

## TUGAS
1. Ringkas kondisi secara netral: di mana tensi rumah berada terhadap ambang, bagaimana BMI dan tren berat.
2. Tensi: nilai rata-rata, pola pagi vs malam, variabilitas, tren, dan kualitas pengukuran (jumlah pengukuran per sesi,
   jeda data). Sebut angka aktual.
3. Berat: BMI, perubahan 30/90/180 hari, dan seberapa andal angkanya mengingat jarang ditimbang.
4. Hubungan antar-metrik: hanya yang \`status\` "found" atau "weak"; untuk "insufficient" cukup sebut data apa yang
   masih dibutuhkan.
5. Hal yang layak dibicarakan dengan dokter (bukan diagnosis), dan kebiasaan yang aman: jadwal ukur sesuai protokol
   (2 kali berurutan jeda 1 menit, pagi dan malam, 7 hari), konsistensi timbang, tidur, aktivitas. JANGAN menyebut obat,
   dosis, atau diagnosis penyakit.

## KELUARAN
Kembalikan HANYA JSON valid (tanpa markdown, tanpa code fence) dengan struktur persis berikut:
{
  "overview": "2-3 kalimat ringkasan netral dengan angka",
  "urgent": "kosongkan string bila tidak ada; bila ada, kalimat tindakan yang aman",
  "blood_pressure": {
    "assessment": "penilaian deskriptif dengan angka",
    "notable": ["temuan spesifik dengan angka"],
    "discuss_with_doctor": ["hal konkret yang bisa dibawa ke dokter"]
  },
  "weight": {
    "assessment": "penilaian deskriptif dengan angka",
    "notable": ["temuan spesifik dengan angka"]
  },
  "connections": [
    { "title": "...", "observation": "apa yang tampak dalam data", "strength": "kuat | sedang | lemah | belum cukup data", "caveat": "keterbatasan/korelasi bukan sebab-akibat" }
  ],
  "habits": ["maksimal 3 kebiasaan aman dan spesifik"],
  "measurement_plan": ["langkah konkret agar data berikutnya lebih andal"],
  "safety_note": "satu kalimat bahwa ini bukan diagnosis atau saran medis"
}

## ATURAN
- Satuan metrik: kg, mmHg, bpm, jam, cm.
- Selalu kutip angka aktual dari data; jangan memberi nasihat generik tanpa angka.
- Jangan mengarang nilai yang tidak ada. Bila data kurang, katakan apa yang kurang.
- Bandingkan dengan riwayat pengguna sendiri dan dengan ambang yang disebut di atas; jangan memakai patokan lain.
- Nada: tenang dan lugas. Jangan menakut-nakuti dan jangan menenangkan secara berlebihan.
- strength: "kuat" hanya bila status "found", |r| ≥ 0.5 DAN item tidak punya \`caution\`; bila ada \`caution\`
  (mis. berat dan variabel lain sama-sama bergeser seiring waktu) maksimal "sedang"; "lemah" untuk "weak";
  "belum cukup data" untuk "insufficient".
- Kembalikan HANYA objek JSON.`;
}

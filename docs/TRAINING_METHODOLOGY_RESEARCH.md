# Training Methodology — Research Inbox

> Inbox для внешних источников (papers, ebooks, продукты, блог-посты), которые мы рассматриваем
> на предмет «брать / не брать» в агента. Не feature-spec — discussion scratchpad.
> Когда по конкретной идее принято решение «делаем» — заводим отдельный `*_SPEC.md` и оттуда сюда ссылка.

**Status:** Open — accepting references.
**Owner:** —
**Last updated:** 2026-05-09

---

## Формат записи

Каждая ссылка оформляется как подсекция с шаблоном:

```
### N. <Название источника>
- **Source:** <url>
- **Reviewed:** <YYYY-MM-DD>
- **Claim / pitch:** что обещают / какую идею продвигают (1-3 предложения)
- **Quantitative content:** конкретные формулы, пороги, протоколы (или «—» если их нет)
- **Already covered by us:** что у нас уже реализовано на ту же тему (с file:line refs)
- **Possibly useful:** список конкретных идей / цитат / ссылок, которые имеет смысл взять
- **Verdict:** adopt / steal one bit / skip + одно предложение почему
```

Если источник тянет на полноценный feature — переносим в новый `*_SPEC.md` и оставляем здесь
короткую ссылку «→ см. `docs/<NAME>_SPEC.md`».

---

## Текущий чек-лист тем для покрытия

Список областей, по которым ищем апдейты к нашим текущим эвристикам. Заполняется по мере поступления ссылок.

- [ ] Альтернативные методы детекции HRVT1/HRVT2 (sigmoid fit, per-step averaging — см. `docs/DFA_REGRESSION_METHODOLOGY_SPEC.md`)
- [x] **Durability metrics** (после 60+ мин нагрузки): что считать кроме decoupling — graduated в `docs/DURABILITY_CLASSIFIER_SPEC.md` (2026-05-09).
- [ ] Recovery scoring: альтернативы текущему 4-компонентному (RMSSD/Banister/RHR/Sleep)
- [ ] Polarization vs pyramidal vs threshold распределение — нужны ли коррекции по фазам подготовки
- [ ] Race-day pacing: как использовать TSB/CTL/HRV-snapshot для прогноза
- [ ] FTP / LTHR — методики оценки без формального теста (от ramp-test, от race-power и т.п.)

---

## Citations harvested

Список peer-reviewed работ, которые упоминают исследованные источники и которые имеет смысл читать
напрямую (а не через пересказ маркетинга). Каждая запись после прочтения — в `docs/knowledge/<topic>.md`
с краткой выжимкой и ссылкой обратно сюда.

| # | Citation | Topic | Source | Status |
|---|---|---|---|---|
| C1 | PubMed [26146564](https://pubmed.ncbi.nlm.nih.gov/26146564/) — 12-нед RCT, метаболические пороги vs HR zones (+11.7% vs +4.9% VO₂max) | DFA / threshold-based zones | Tymewear ebook (entry #1) | unread |
| C2 | Maunder et al. 2021 — formal introduction of "durability" to sports science | Durability | Tymewear durability page (entry #2) | unread |
| C3 | Spragg et al. 2023 — pro cyclist durability + substrate flexibility predictor (athletes oxidizing less carb at moderate intensity → smaller critical power drops post-fatigue) | Durability | entry #2 | unread |
| C4 | Rothschild 2025 — n=51 cyclists, 85 measurements; после 2.5ч @ 90% VT1: VT1 HR 142→151 bpm, power −21W; mean threshold power decline ~10% (range 1-45W) | Durability — empirical thresholds | entry #2 | unread |
| C5 | Stevenson 2024 — 2ч умеренного: V̇E «remarkably stable», BR +16% → нейромышечная подпись без метаболического сдвига | Durability — fatigue signatures | entry #2 | unread |
| C6 | Nicolò et al. 2018 — respiratory control mechanisms | Durability — BR control theory | entry #2 | unread |
| C7 | Meyer et al. 1999 — критика надёжности HR zones | Zone prescription | entry #2 | unread |

---

## References

### 1. Tymewear training ebook

- **Source:** https://www.tymewear.com/pages/training-ebook
- **Reviewed:** 2026-05-09
- **Claim / pitch:** Маркетинговый ebook к их wearable. Продают идею «тренировки по вентиляторным
  порогам (VT1/VT2) лучше, чем зоны от %HRmax/HRR». Никаких новых фреймворков не вводят — explain
  «как метаболические пороги ложатся на классическую 5-zone модель».
- **Quantitative content:**
  - Эвристика «balanced metabolic profile»: VT1 = 70–75% VO₂max, VT2 = 80–85% VO₂max. Без источника, своя.
  - Распределение времени по зонам:
    - При слабом VT1 (под VT2): 60% низкий Z2 / 35% около VT1 / 5% VO₂max.
    - При сбалансированных порогах: 80% низкий Z2 / 10% VT1 / 10% VO₂max.
  - Ramp-test протокол: «+power каждые 2-3 мин» — без алгоритма детекции точки перегиба, без длительности.
  - Цитата RCT: PubMed [26146564](https://pubmed.ncbi.nlm.nih.gov/26146564/) — 12-нед,
    метаболические пороги vs HR zones: +11.7% VO₂max vs +4.9%.
- **Already covered by us:**
  - VT1/VT2 → у нас HRVT1/HRVT2 через DFA α1 (`data/dfa_detector.py`, `activity_hrv` table). Объективнее
    (без газоанализатора), используется для drift detection и `actor_update_zones`.
  - Ramp-test → переписан 2026-05-08 (`docs/RAMP_TEST_BIKE_SPEC.md`): Run 8-step 80→115%, Bike 11+1 step
    60→110% + push-to-failure. Гораздо детальнее, чем «каждые 2-3 мин».
  - Polarization → `polarization_index` MCP tool, AI чат уже выдаёт рекомендации по балансу.
  - Drift detection → абсолютные пороги (`DRIFT_LTHR_BPM=3`, `DRIFT_FTP_WATTS=5`), R² 3-tier — заменили
    relative 5%, см. CLAUDE.md «Implementation Status».
- **Possibly useful:**
  1. Цитата PubMed 26146564 — положить в `docs/knowledge/dfa_a1.md` как evidence для нашего
     HRVT-подхода против %HRmax/HRR. **Cheap, worth doing.**
  2. «Imbalanced threshold profile» nudge — если HRVT1 % от LTHR непропорционально низкий vs HRVT2,
     рекомендовать больше Z2 объёма. **Marginal** — AI чат уже делает это органически по polarization.
  3. Heuristic VT1=70-75% / VT2=80-85% от VO₂max. Привязка через VO₂max требует power-to-VO₂ модели,
     которой у нас нет. **Skip.**
- **Verdict:** **steal one bit** — забрать только PubMed-цитату в knowledge base. Всё остальное либо
  у нас уже реализовано в более продвинутом варианте, либо требует данных, которых у нас нет.

---

### 2. Tymewear — Durability page

- **Source:** https://www.tymewear.com/pages/durability
- **Reviewed:** 2026-05-09
- **Verdict:** **adopt (framework only)** — three-system fatigue decomposition (CV / metabolic /
  neuromuscular). Реализуем своими данными (HR / power / cadence / RPE / decoupling), без их железа.
- **Status:** **→ promoted to `docs/DURABILITY_CLASSIFIER_SPEC.md` (2026-05-09).** Tymewear-фреймворк,
  empirical baselines от Rothschild 2025 / Stevenson 2024, маппинг V̇E/BR → наши прокси, decision
  tree и phased plan — всё там. Цитаты C2-C7 → пойдут в `docs/knowledge/durability.md` в Phase 0.
- Ключевая выжимка одним абзацем (для контекста, если открыли research-файл первым):
  Tymewear раскладывают fatigue по трём независимым сигналам — HR drift (CV/термо), V̇E
  (метаболический спрос), BR (central command/нейромышечная). Комбинация даёт диагноз *причины*
  усталости, а не просто факта. Empirical baseline: Rothschild 2025, n=51, 2.5ч @ 90% VT1 →
  VT1 HR +6.3%, power −21W, mean threshold drop ~10%.

---

### 3. Tymewear — Internal validation study (V̇E + BR vs Cosmed K5)

- **Source:** https://www.tymewear.com/blogs/validation-studies/tymewear-internal-validation-study-of-breathing-metrics
- **Reviewed:** 2026-05-09
- **Claim / pitch:** Их chest strap меряет V̇E и BR с лабораторной точностью (vs Cosmed K5).
- **Quantitative content:**
  - n=26, incremental cycling, 3-min stages, +20W
  - Sample rate 25 Hz
  - V̇E: pooled Pearson r=0.973 (Fisher's z weighted)
  - BR: MAE 1.2 breaths/min
  - Outlier rejection: drop 2 of every 5 breaths; V̇E через 3-breath moving average
  - Acknowledged limits: temporal alignment (Cosmed sub-second timestamp loss), breath-detection
    algorithm divergence, no Bland-Altman
- **Already covered by us:** —
- **Possibly useful:** ничего напрямую — мы не строим железо. **Косвенно:** аргумент, что
  ventilation-only методы достаточны для VT detection (если когда-нибудь захотим интегрировать
  с tymewear-style устройствами через webhook / file import).
- **Verdict:** **skip** — interesting but not actionable for our stack. Файлится в research только
  для контекста к entry #2.

---

### 4. Tymewear — продуктовые страницы (зонтичная запись для skip)

Все следующие страницы — маркетинг или повтор содержания entry #1 / #2 без disclosed алгоритмов.
Свожу в одну запись чтобы не плодить однотипные skip'ы.

- https://www.tymewear.com/pages/how-tymewear-works — нет sample rate, нет алгоритма детекции, нет формул
- https://www.tymewear.com/pages/ventilatory-thresholds — определения VT1/VT2 + RPE/talk-test якоря
  (RPE 4 ~ VT1, RPE 7 ~ VT2). Алгоритм breakpoint detection — proprietary, не раскрыт.
- https://www.tymewear.com/pages/zone-2-training — Z2 = «below VT1», без числовых границ. Цитируют
  4 PubMed/bioRxiv ссылки на адаптации (mitochondrial, fat oxidation, lactate clearance) — общеизвестно.
- https://www.tymewear.com/pages/vo2max-explained — VO₂max derived из «output of the test», формула
  не disclosed. Validation r=0.97 — это V̇E vs Cosmed, а не VO₂max accuracy.
- https://www.tymewear.com/blogs/validation-studies/can-a-chest-strap-actually-measure-my-fitness-level —
  повтор claims (BR 98.6%, V̇E 95%, TV 85%, n=200 для VT detection: ΔVT1=0.8±7 bpm, ΔVT2=0.1±6 bpm).
  Маркетинг для самой entry #3.
- https://www.tymewear.com/ — homepage, funnel.

- **Verdict:** **skip all** — у нас уже есть всё, что они описывают концептуально, в более продвинутом
  варианте. Алгоритмов они не раскрывают. RPE-якоря (4/10 = VT1, 7/10 = VT2) — известная эвристика,
  не нуждается в отдельной записи.

---

### 5. _placeholder_

_(новые ссылки добавляем сюда)_

---

## Decisions log

| Date | Source | Decision |
|---|---|---|
| 2026-05-09 | Tymewear ebook (entry #1) | steal one bit — PubMed 26146564 → knowledge base, остальное skip |
| 2026-05-09 | Tymewear durability page (entry #2) | **adopt framework** — three-system fatigue decomposition. Завести feature spec `DURABILITY_CLASSIFIER_SPEC.md` когда дойдут руки. Цитаты C2-C7 в `docs/knowledge/durability.md`. |
| 2026-05-09 | Tymewear validation study (entry #3) | skip — нет применимости вне их железа |
| 2026-05-09 | Tymewear product pages (entry #4: how-it-works, vt-explained, zone-2, vo2max, chest-strap-blog, homepage) | skip all — нет нового / proprietary алгоритмы не раскрыты |

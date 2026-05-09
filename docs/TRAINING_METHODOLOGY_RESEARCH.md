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
- [x] **Durability metrics** (после 60+ мин нагрузки): что считать кроме decoupling — adopted as `DURABILITY_CLASSIFIER_SPEC.md`, abandoned 2026-05-09 после Phase 0 calibration. Постмортем + replay script: `docs/knowledge/durability-postmortem.md`.
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
| C1 | PubMed [26146564](https://pubmed.ncbi.nlm.nih.gov/26146564/) — 12-нед RCT, метаболические пороги vs HR zones (+11.7% vs +4.9% VO₂max) | DFA / threshold-based zones | Tymewear ebook | unread |
| C2-C7 | Maunder 2021 / Spragg 2023 / Rothschild 2025 / Stevenson 2024 / Nicolò 2018 / Meyer 1999 | Durability | Tymewear durability page | **consumed** → выжимка в `docs/knowledge/durability.md`, использованы в abandoned `DURABILITY_CLASSIFIER_SPEC.md` (см. postmortem) |

---

## References

### 1. Tymewear suite (closed 2026-05-09)

Reviewed 2026-05-09. Скоупом охватили:
[training-ebook](https://www.tymewear.com/pages/training-ebook) ·
[durability](https://www.tymewear.com/pages/durability) ·
[validation-study](https://www.tymewear.com/blogs/validation-studies/tymewear-internal-validation-study-of-breathing-metrics) ·
how-it-works · ventilatory-thresholds · zone-2-training · vo2max-explained · chest-strap blog · homepage.

**Outcomes:**

- **Ebook (training-ebook):** steal one bit — PubMed [26146564](https://pubmed.ncbi.nlm.nih.gov/26146564/) RCT (12 нед, метаболические пороги vs HR zones, +11.7% vs +4.9% VO₂max) — кандидат на цитату в `docs/knowledge/dfa-alpha1.md` как evidence для нашего HRVT-подхода. **Skip:** «balanced metabolic profile» heuristic (требует power-to-VO₂ модели), их ramp-test протокол (наш в `docs/RAMP_TEST_BIKE_SPEC.md` детальнее), zone-distribution рекомендации (AI чат и так делает по `polarization_index`).
- **Durability page:** **adopted framework** (three-system fatigue decomposition: CV / metabolic / neuromuscular) → promoted to `DURABILITY_CLASSIFIER_SPEC.md`, **abandoned 2026-05-09** после Phase 0 calibration на population data (metabolic_erosion fire-rate 0%, RPE сбор blind, neuromuscular trigger false-positive heavy без gradient-фильтра). Цитаты C2-C7 + теория — в `docs/knowledge/durability.md`. Постмортем + replay script — `docs/knowledge/durability-postmortem.md`.
- **Validation study + продуктовые страницы:** skip all. Tymewear не раскрывают breakpoint-detection алгоритм; HRVT1/HRVT2 у нас уже через DFA α1 (`data/dfa_detector.py`) — объективнее (без газоанализатора), без зависимости от их железа. RPE-якоря (RPE 4 ~ VT1, RPE 7 ~ VT2) — известная эвристика, отдельной записи не требует.

---

### 2. _placeholder_

_(новые ссылки добавляем сюда)_

---

## Decisions log

| Date | Source | Decision |
|---|---|---|
| 2026-05-09 | Tymewear suite (entry #1: ebook + durability + validation + продуктовые страницы) | steal one bit (PubMed 26146564 для DFA evidence); durability framework adopted → spec → abandoned после Phase 0 (см. `docs/knowledge/durability-postmortem.md`); остальное skip |

# UZEX AI Hackathon 2026: Qualification stage ("Saralash bosqichi")

Source: https://hackathon.uzex.uz/saralash/ (fetched 2026-11-04). Raw copies: `hackathon_page_saralash.txt` / `.html`.
Note: the site returns 403 to plain curl, so send a browser User-Agent header.

## Key facts
- 511 teams registered; **20 teams** pass to the offline hackathon (≈60 people, ≤3 per team), Tashkent, **Nov 7–8, 2026, 09:00**.
- **Task:** an AI agent, as a **Telegram bot** or web service, that answers questions about exchange deals in **Russian and Uzbek**. It replies in the language of the question.
  - Answers must be exact and based on the data (numbers, names, codes).
  - **If the answer is not in the data, the agent must say so openly and never invent anything.**
  - Any tools and LLMs are allowed.
  - Must work **without installation or registration**: judges just open it and ask.
- **Data:** 500,000 deals in spot.uzex.uz format, Jan 2022 – Oct 2026. All companies, INNs (STIR), and amounts are fictional.
  Drive: https://drive.google.com/drive/folders/1BcjcDi1N0ym1MwX3OrgZYfDqsWLOkVeL?usp=sharing (already downloaded to `data/raw/`).

## Submission
- **Deadline: Nov 5, 2026, 18:00 Tashkent time** via Google Form https://forms.gle/5ALB3PrWdKf6TTCf8
  - Prototype link (Telegram bot or web service)
  - Team members (name + surname, up to 3)
  - Solution description: technologies and methods used
  - Team lead name + phone (results are sent there)
- The form can be resubmitted before 18:00; after 18:00 the prototype must not change.
- Results: Nov 6. Telegram channel: https://t.me/UZEX_AI_Hackathon

## Evaluation (from Nov 5, 18:00)
- Judges ask each prototype **10 unannounced questions** about the data, in RU or UZ.
- **≤ 90 seconds per answer.**
- **If the agent invents an answer (a number, company, or commodity where the data has none), the team is disqualified.**

| Criterion | Points |
|---|---|
| Correct answers: 10 questions × 6 | 60 |
| Prototype quality: shows what each answer is based on; understands RU + UZ; convenient and fast | 20 |
| Solution description: technologies and methods are clear, and so is how answers were verified | 20 |

## Rules
1. Up to 3 people per team.
2. Work only within your team. Don't share code or prototypes; similarities are checked.
3. The prototype must stay online the whole time (Telegram bot or web app).

# How printed Sanskrit books are laid out

You are working on OCR text of printed Sanskrit books: Upanishads, the Gita, sutra texts, stotras and similar works, usually printed with one or more commentaries and sometimes with a translation. This section describes what such a page contains, so that you can tell the parts apart.

## Kinds of text

**Root text (mula, मूल).** The shloka, mantra, sutra or karika that the book is about.
- A verse usually has two lines. The first half ends with a single danda `।`, and the verse ends with a double danda `॥`, normally followed by its number: `॥ ५ ॥`.
- Prose mantras and sutras also end with a numbered marker.
- A speaker line such as `श्रीभगवानुवाच` or `अर्जुन उवाच` belongs with the verse that follows it.

**Commentary (भाष्य, टीका, व्याख्या, विवरण, वृत्ति, दीपिका).** Prose that explains the root text. Signs that a passage is commentary:
- It quotes the first word or two of the root text followed by `इति`. This is called a pratika. Examples: `…आह—शीतेति`, `याज्ञवल्क्य इति`, `कर्मण्येवेति`.
- It uses explaining phrases: `इत्यर्थः`, `इति भावः`, `इत्याह`, `इत्यत आह`, `इत्याशङ्क्य`, `इति श्रुतेः`.
- It usually ends with the same number as the root text it explains.
- It may quote verses from other works (श्रुति, स्मृति). A quoted verse is part of the commentary, not new root text. You can recognise it because it has no number of its own in this book's sequence, and it is introduced or followed by words like `तथा च श्रुतिः`, `इति`, `इति श्रुतेः`, `तदुक्तम्`.
- A book can carry more than one commentary on the same root text.

**Meaning and translation.** Often marked with a word such as `अन्वय` (the verse rearranged in prose order), `पदार्थ` or `शब्दार्थ` (word-by-word meanings), `अर्थ`, `भावार्थ`, `अनुवाद` or `टीका` (the meaning, often in Hindi, Marathi or English).

**Introductions.** A sentence before a verse saying what it is about (अवतरणिका, उत्थानिका), often ending in `…आह—` or `इत्याह`. Books also open with a peace invocation (शान्तिपाठ) or a commentator's own opening verse (मङ्गलाचरण), and close with a colophon (`इति श्री… समाप्तम्`).

**Side headings.** Short topic titles printed in the margin or between passages, for example `चतुर्विधयमः` or `तारकावलोकनं तत्फलं च`. They are noun phrases with no danda and no number. They describe the passage that follows.

## Numbering

- Units (shlokas, mantras, sutras) are numbered inside sections. Sections have names such as काण्ड, अध्याय, पाद, ब्राह्मण, खण्ड, वल्ली, अनुवाक, प्रपाठक, सर्ग, प्रकरण. Some books have two or three levels of sections.
- A section may open with a title (`द्वितीयं ब्राह्मणम्`, `अथ द्वितीयोऽध्यायः`) and usually closes with a line starting with `इति` (`इति प्रथमः खण्डः`, `इति … द्वितीयोऽध्यायः`). Unit numbers normally start again at 1 after a section closes.
- **The number comes after the text it numbers.** The lines before `॥ ५ ॥` belong to unit 5.
- Two common orders:
  1. One root text, then its commentary, then the next root text, and so on.
  2. A group of root texts (say 5 to 10), then their commentaries in the same order. Here each number appears twice: the first time it closes the root text, the second time it closes the commentary on it.
- One commentary can cover several units. It then ends with a range such as `॥ १२-१३ ॥`.
- The last commentary of a section sometimes has no number. It runs up to the `इति …` line that closes the section.
- Books differ in depth. Some have one level of sections, some four (for example काण्ड → अध्याय → ब्राह्मण → कण्डिका). Some parts of a book may skip a level: one ब्राह्मण divided into खण्डs and the next not divided at all.
- Some editions print the location on every page, in the running header: `अ. ३ खं. २ मं. ४-७` means adhyaya 3, khanda 2, mantras 4 to 7; `सर्गः १२ श्लो. ३०` means sarga 12, shloka 30. Abbreviations vary: अ./अध्या. (अध्याय), खं. (खण्ड), मं. (मन्त्र), श्लो. (श्लोक), ब्रा. (ब्राह्मण), व. (वल्ली), अनु. (अनुवाक), प्र. (प्रपाठक). These headers are the most reliable evidence of where a page is, even though the OCR often garbles them.

## Page furniture (not book content)

- **Running header.** The first line of a page: the book or section title with the page number, in either order. Examples: `२७४ मण्डलब्राह्मणोपनिषत्`, `प्रथमब्राह्मणे प्रथमः खण्डः २७५`, `अद्वैतमतम् । ५`.
- **Footnotes.** At the bottom of a page. They give variant readings and name manuscripts by short sigla (`अ`, `अ १`, `क`, `उ`). They start with a small number or mark, and the OCR tool may write them as `[^1]: …`. Example: `1 निद्रासरी—उ.`
  The small marks inside the running text that point to footnotes (`'` or a tiny digit) are part of their line. Leave them alone.
- **Printer's marks.** A bare number at the bottom of a page (`85`, `36`), unrelated to the text.
- **Footnotes on the commentary.** Some editions use footnotes to explain the commentary itself rather than to give variant readings: `१ शेषपूरणेन व्याचष्टे …`. These are the editor's notes, so they are book text. Keep them when the user wants them.

## What OCR does to the page

- **Misread digits.** `५` and `९` are often confused; so are other similar shapes. A verse number that breaks the sequence (… ३, ४, ९, ६ …) is usually a misreading, not a missing verse.
- **`##` marks.** The OCR tool adds `##` where it guessed a heading. The guess is sometimes wrong: a line of root text may carry `##`, and a real side heading may have none.
- **Junk.** Stray bullets `•`, a lone dot at the start of a line, `[UNCLEAR]`, isolated Latin letters or digits.
- **Words broken across lines** with a hyphen at the line end.
- **Blank lines in odd places**, even in the middle of a verse.
- **A side heading glued into a body line**, or dropped between the two halves of a hyphenated word.
- **A page or passage that appears twice**, with slightly different errors each time.
- **Reading order broken by the layout.** When the root text is printed in a box or a separate column, the OCR may give the commentary first and the root text after it, put a section's closing line in the middle of the next section's text, or start the next section's root text before the last commentary of this one. The numbers and the page's location header still tell you where each passage belongs.
- **Lines in another script** (Tamil, Telugu, Bengali) or of nonsense that the OCR picked up from a stamp, a bleed-through or a neighbouring page.
- **Spelling errors.** These are everywhere and are never your concern.

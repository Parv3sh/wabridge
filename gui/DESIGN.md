# WaBridge desktop — design plan

## Brief, as I understand it

Subject: moving years of someone's WhatsApp history from an Android phone to an iPhone, at a
kitchen table, with two phones and a laptop. Audience: a non-technical person who is mildly
anxious about losing their chats and has already been burned by paid tools. Primary job: make a
long, two-device, irreversible-feeling process feel calm, legible and reversible — always
showing *where you are*, *what the app is doing right now*, and *what, if anything, it needs
from you*.

Metaphor: a harbour bridge at first light. Two banks (the phones), a span between them, cargo
crossing. Not chat bubbles, not WhatsApp green.

## Tokens

Colour (light scheme; dark in brackets)

| name    | hex                   | role                                                  |
|---------|-----------------------|-------------------------------------------------------|
| fog     | `#E8EDF2` (`#131A21`) | window background; cool, faintly blue — not cream     |
| deck    | `#FFFFFF` (`#1B242D`) | content surfaces                                      |
| steel   | `#1C2733` (`#E7EDF3`) | primary text, blue-black rather than tinted #111      |
| rivet   | `#5E6E7E` (`#93A1AF`) | secondary text, hairlines, the un-travelled line      |
| harbour | `#0F7F8A` (`#3FB2BC`) | the single action colour; the travelled span          |
| sodium  | `#F0B429`             | cargo in motion: active progress fill, current lamp   |
| flare   | `#C4402C` (`#E0684F`) | errors only                                           |

Type: one family. Avenir Next on macOS (geometric, wayfinding heritage — the bridge-signage
face), Segoe UI Variable on Windows, `system-ui` elsewhere. Scale 13 / 15 / 18 / 24 / 30.
Headlines 600 weight with −0.01em tracking. Body 15px, line-height 1.5, ≤ 62ch. Tabular numerals
for counts. The only monospace is the engine console, because it is a console.

Layout: window 980 × 660 (min 880 × 600). Left rail 232px carries *the line* — a vertical route
with five stations. Content left-aligned, padded 40px. A collapsible console drawer along the
bottom. Devices are shown as a "berth": phone silhouette, status lamp, two lines of text, with
hairline rules above and below — not a card.

```
┌─────────────────────────────────────────────────────────────────┐
│ WaBridge                                          Console  ▾    │
├──────────────┬──────────────────────────────────────────────────┤
│ ● Start      │  Your Android phone                              │
│ │            │                                                  │
│ ● Android    │  ▭ Galaxy Z Fold4                                │
│ ┃ (sodium)   │    ready, backup found                           │
│ ○ iPhone     │  ──────────────────────────────────────────      │
│ │            │  Paste the 64-digit key WhatsApp showed you …    │
│ ○ Transfer   │  [••••••••••••••••••••••••]  [Decrypt backup]    │
│ │            │                                                  │
│ ○ Done       │  ▮▮▮▮▮▮▮▮▮▮▮▮░░░░░░░░░░  42%  Copying media      │
├──────────────┴──────────────────────────────────────────────────┤
│ ▸ console                                                        │
└─────────────────────────────────────────────────────────────────┘
```

Principles

1. Spend the boldness once: the transit line. The travelled span fills in harbour; the current
   station carries a sodium lamp; during long work the span *to the next station* fills as the
   progress bar. Everything else stays quiet.
2. Every screen answers, in order: where am I, what is happening, what do I do.
3. Errors say what happened and the exact fix, in the app's voice. No apologies, no vagueness.
4. Secrets never appear after they are typed; the key field is masked and nothing echoes it.
5. Motion only for progress fill and the lamp; `prefers-reduced-motion` turns both off.
6. The forward action is never below the fold. Long screens pin their action row
   (`.actions-sticky`) to the bottom of the pane, fold instructions the user may not need into a
   `<details class="steps-disclosure">` (how to get the key, how to install WhatsApp, transfer
   options), and the content pane shows a real scrollbar. Added after the first screenshot review
   (23 Sept 2026): at 980 × 660 eight of sixteen screens hid their button.
7. "Blocked" and "working" look different: a disabled button goes grey (`--rule` fill, rivet
   text); a busy one keeps its colour and shows the spinner. Sodium marks things that need the
   user (warn lamps, with an ink ring in light mode); info lamps are harbour.

## Review against the generated-look defaults

* Left stepper + content is the wizard convention. Kept, because the brief *is* a sequence — but
  rendered as a transit line with a filling span, not numbered circles with ticks.
* Dropped "Step 2 of 5" eyebrows and all-caps labels: the rail already says where you are.
* No middle-dot meta strings; device status reads as a sentence ("ready, backup found").
* No identical shadowed cards; panels use a 1px rivet-at-20% rule, radius 10; controls radius 6;
  lamps are round. No gradients. No "→" on buttons.
* Palette is neither cream + terracotta nor black + acid green; teal + amber is common in
  fintech, but the transit metaphor and the amber-only-for-motion rule make it specific.
* Copy uses plain verbs that stay the same through the flow: "Decrypt backup", "Copy media",
  "Back up iPhone", "Move the chats to the iPhone", "Delete my data". Engine log lines
  ("→ Copying … …") are console voice; the screens show the action's own label and the last log
  line as a plain aside (`store.tsx` strips the arrow and ellipsis).

## Screenshot review, 23 September 2026

Every screen was rendered for the first time through the browser mock (`npm run screenshots`,
35 scenes, light and dark, 980 × 660 and 880 × 600) and critiqued by three independent reviewers
(visual fidelity, UX and copy, layout and accessibility) whose findings a fourth merged and ranked.
What held up: the rail, the two-surface layout, the berth pattern, notices, the plain-verb buttons,
the stats row and chat list, the dark-scheme token mapping. What changed as a result:

1. Primary actions were below the fold on eight screens → principle 6 (sticky action rows,
   disclosures, visible scrollbar) and reordered screens: the key field now comes before the
   how-to; the Find My warning and its confirmation tick come before the transfer button; the
   media choice and "Copy media / Skip" come before the chat list (capped at six rows, "Show all").
2. Disabled buttons were white on pale teal (~2:1) and indistinguishable from busy ones →
   principle 7. Dark-mode primaries use fog ink on harbour (6.9:1) instead of white (2.5:1).
3. Engine-voice text leaked into labels ("→ Decrypting msgstore.db.crypt15 …", "Settings → Chats")
   → stripped in the store; hints use "›"; log lines name things the user knows.
4. The not-enough-space state was red body text with an enabled "Back up iPhone" → an error
   notice with "Try anyway", and the primary disabled until space is freed.
5. Errors that imply damage where none happened: after Apple refuses a restore over Find My,
   "Put the iPhone back" is no longer offered and the message says nothing changed.
6. Smaller: info lamps harbour not sodium; warn lamps get an ink ring in light mode; the dark
   warn wash was olive; disabled choice cards keep their explanation legible; the key field only
   turns red after blur or submit; input focus rings match buttons; the Done screen no longer
   prints a raw path (a "Show the folder" button instead) and its delete confirmation states the
   consequence before the buttons; the "group" tag sits with the count so names align.

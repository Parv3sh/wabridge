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
  "Back up iPhone", "Convert and restore", "Delete my data".

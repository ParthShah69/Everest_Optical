# Optical ERP design system

## Product intent

Fast, calm operational software for an optical shop. Prioritize scanability,
error prevention, and obvious next actions over decorative density.

## Foundations

- Typeface: system UI stack (Segoe UI first), clear numeral alignment for money.
- Primary: optical blue `#0d6efd`; deep ink/navy for shell; teal for completed
  work; amber for attention; red only for risk or overdue states.
- Surface: cool light-gray canvas, raised white cards, 10–14px rounding,
  restrained shadows, 44px minimum touch targets.
- Dark mode: deep slate canvas with high-contrast text and muted borders.

## Components

- Persistent sidebar with grouped nav and clear active state.
- Header search is the global command entry point; page actions are visibly
  grouped and ordered by frequency.
- KPI cards use label → value → supporting context, not colour alone.
- Tables remain dense on desktop and become labelled cards on mobile.
- Forms use short semantic sections with explanatory helper text and a sticky
  action area on mobile.

## Accessibility and responsive behavior

- Keyboard focus is always visible; no colour-only state meaning.
- Mobile content is one column below 768px; actions wrap rather than overflow.
- Respect reduced motion and keep chat/status state truthful.

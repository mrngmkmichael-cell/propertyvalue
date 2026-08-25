# Taste library

Your taste is the part of this site nobody can copy and no model can
guess. This folder is where it lives, so it can go into a prompt instead
of staying in your head.

## How to fill it (about 30 minutes, once)

1. **Collect.** Open [dribbble.com](https://dribbble.com/search/web-design)
   sorted by popular, Pinterest, and designers you like on X. Also just
   browse the web normally.
2. **Screenshot anything that makes you stop.** Not "this would suit my
   site" — that filter comes later. Stop-worthy is the only test. Aim for
   15–25.
3. **Save the URL too** where there is one. A live site shows motion,
   hover and spacing that a flat screenshot loses.
4. **Group by family, not by project.** Six or seven families is plenty.
5. **Name each family.** This is the step that matters: an unnamed
   screenshot is decoration, a named one is vocabulary you can put in a
   sentence.

## Layout

```
design/taste/
  editorial-serif/       one folder per family
    01-name-of-site.png
    sources.md           URLs, and one line on what you liked
  warm-document/
  dense-data/
```

Naming a family well means an adjective plus a noun: `warm-document`,
`dense-data`, `bold-brutal`, `quiet-swiss`, `print-tech`. Avoid
`modern`, `clean`, `nice` — they describe nothing.

## In `sources.md`, one line each

```
https://example.com — the way the hairline rules separate sections
without any boxes
```

What you liked is more useful than the link. It is the thing that gets
reused.

## Then

Point Claude at a family by name: "rebuild the pricing page in the
`warm-document` family, using `design/taste/warm-document/` as
reference — learn the feel, do not copy the layout." The brief in
`/DESIGN.md` supplies the rest.

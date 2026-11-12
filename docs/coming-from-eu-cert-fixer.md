# Coming from EU Certification Fixer

MetaMender replaces EU Certification Fixer. It fills and corrects age ratings
for the same nine countries, and keeps the rest of your library's information
up to date as well. The fixer will not get new features.

## Switching

1. **Uninstall EU Certification Fixer**: open it under **Add-ons > My
   add-ons** and choose **Uninstall**. Do this before MetaMender's first
   scan; with both installed, two add-ons would write the same field.
2. **Install MetaMender** and run its [setup](setup.md). Tick **Age
   certifications** and choose the same country you used in the fixer.
   MetaMender does not read the fixer's settings; it asks for what it needs.
3. If you changed the fixer's prefix, scanner or not-found settings, set the
   matching options in MetaMender's [settings](settings.md) on the
   **Certifications** tab: **Override certification prefix**, **Enable FSK /
   BBFC / Medieraadet lookup**, **Replace incorrect certifications** and
   **Apply certification when not found**.

## What happens to the ratings the fixer wrote

They stay. A rating the fixer wrote is already in your country's system, so
MetaMender keeps it as your country's rating and does not look it up again.
If MetaMender's prefix differs from the one the fixer used, only the prefix is
changed.

A not-found value the fixer wrote (`NR` by default) is not a rating in your
country's system. With **Replace incorrect certifications** on, MetaMender
replaces it as soon as it finds a real or converted rating; until then it
stays.

## What is different

| | EU Certification Fixer | MetaMender |
|---|---|---|
| What it updates | Age ratings | Age ratings, plus ratings, plots, titles, credits and more ([setup](setup.md#4-what-to-fill)) |
| Sources for age ratings | TMDB, the FSK, BBFC, Medieraadet and Kijkwijzer sites, OMDB | TMDB, the FSK, BBFC and Medieraadet sites |
| When nothing is found | Retries for 30 days, then the not-found value | Retries every month for about a year, then the not-found value if you switched it on |
| Seeing why | The log | The [Details window](details.md) for any movie or show |

MetaMender leaves out two of the fixer's sources on purpose. The Kijkwijzer
website now blocks automated lookups, so asking it would only fail; TMDB still
carries Kijkwijzer ratings for most titles. OMDb only adds the American
rating, which TMDB already has.

How MetaMender finds and converts ratings is on the [Age ratings](age-ratings.md)
page.

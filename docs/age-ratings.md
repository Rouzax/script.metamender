# Age ratings

Kodi's scrapers usually store the American rating ("Rated PG-13"), or
whatever the site they used had. That says little to a parent in Amsterdam or
Berlin. MetaMender writes every movie's and TV show's age rating in the
rating system of **your** country, so you can rely on it when deciding what
the kids may watch.

Age ratings are on in a library when **Age certifications** is ticked on its
[page](libraries.md#what-to-fill), and each library has its own country.
Episodes have no age rating of their own in Kodi; the show's rating covers
them.

## Which countries

| Country | System | Written as |
|---|---|---|
| Netherlands | Kijkwijzer | `NL:12` |
| Belgium | Kijkwijzer | `BE:12` |
| Germany | FSK | `DE:12` |
| Austria | JMK | `AT:12` |
| United Kingdom | BBFC | `GB:12A` |
| United States | MPAA | `Rated PG-13` |
| France | CNC | `FR:12` |
| Denmark | Medieraadet | `DK:11` |
| Sweden | Mediemyndigheten | `SE:11` |
| Australia | ACB (Australian Classification Board) | `AU:MA 15+` |

**Automatic** (the default) uses the country of the library's language, or
of Kodi's interface language when the library's has no rating system in this
list. Some skins expect a different prefix; **Rating prefix** on the
library's [page](libraries.md#age-ratings) lets you choose your own, or none.

Australian ratings are written with their official spelling (`MA 15+`,
`R 18+`); a value already in Kodi as `AU:MA15+` counts as the same rating and
is written back as `AU:MA 15+`. Australia's children's TV ratings P and C,
the adults-only X 18+, and RC (refused classification) are only used when
TMDB has them as the title's Australian rating; MetaMender never converts
another country's rating into one of them.

Your country missing? It cannot be chosen yet. Requests are welcome on
[GitHub](https://github.com/Rouzax/script.metamender/issues).

## Where a rating comes from

For each movie and show, MetaMender asks in this order and stops at the first
answer:

1. **TMDB's rating for your country.**
2. **Your country's own rating site**, for Germany (FSK), the United Kingdom
   (BBFC) and Denmark (Medieraadet). Each can be switched off in the settings.
3. **TMDB's ratings for neighbouring countries**, converted to your system.
   For example a Dutch library looks at Belgium, then Germany, Austria,
   France, the United Kingdom, Denmark, Sweden and finally the United States.
   An Australian library looks at the United Kingdom first, then the United
   States, then the European countries.
4. **The other countries' rating sites**, converted the same way.

A rating from steps 1 and 2 is your country's real rating; it never changes,
so MetaMender does not check it again. A converted rating from steps 3 and 4
is MetaMender's best estimate: it is checked again every month for about a
year, and replaced as soon as your country's real rating appears.

The [Details window](details.md) shows where an item's rating came from; a
converted one says so, for example "TMDB (GB), converted".

## What MetaMender changes, and what it keeps

- **An empty rating** is filled.
- **A rating already in your country's system** is kept. If its prefix differs
  from the one MetaMender writes (for example `12` without `NL:`), only the
  prefix is changed.
- **A rating from another country's system**, such as "Rated PG-13" in a Dutch
  library, is replaced, once MetaMender finds a rating for your country.
  Untick **Replace other countries' ratings** to have MetaMender only fill
  empty ratings.
- **A rating from an NFO file** is never replaced.
- **When no source has a rating**, the field stays empty. If you would rather
  see that an item was checked, set **When no rating is found** to **Write a
  value**: after a year of monthly checks MetaMender writes the value you
  choose (for example `NR`) into ratings that are still empty.

You can undo a rating MetaMender wrote with **Restore original** in the
[Details window](details.md#restore-original-and-the-lock); that also locks
the field so MetaMender leaves it alone.

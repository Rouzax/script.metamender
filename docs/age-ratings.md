# Age ratings

Kodi's scrapers usually store the American rating ("Rated PG-13"), or
whatever the site they used had. That says little to a parent in Amsterdam or
Berlin. MetaMender writes every movie's and TV show's age rating in the
rating system of **your** country, so you can rely on it when deciding what
the kids may watch.

Age ratings are on when **Age certifications** is ticked in
[setup](setup.md#4-what-to-fill). Episodes have no age rating of their own in
Kodi; the show's rating covers them.

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

**Automatic** (the default) uses the country of Kodi's interface language.
Some skins expect a different prefix; **Override certification prefix** in the
settings lets you choose your own, or none.

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
4. **The other countries' rating sites**, converted the same way.

A rating from steps 1 and 2 is your country's real rating; it never changes,
so MetaMender does not check it again. A converted rating from steps 3 and 4
is MetaMender's best estimate: it is checked again every month for about a
year, and replaced as soon as your country's real rating appears.

The [Details window](details.md) shows where an item's rating came from.

## What MetaMender changes, and what it keeps

- **An empty rating** is filled.
- **A rating already in your country's system** is kept. If its prefix differs
  from the one MetaMender writes (for example `12` without `NL:`), only the
  prefix is changed.
- **A rating from another country's system**, such as "Rated PG-13" in a Dutch
  library, is replaced, once MetaMender finds a rating for your country.
  Switch off **Replace incorrect certifications** to have MetaMender only fill
  empty ratings.
- **A rating from an NFO file** is never replaced.
- **When no source has a rating**, the field stays empty. If you would rather
  see that an item was checked, switch on **Apply certification when not
  found**: after a year of monthly checks MetaMender writes the value you
  choose (for example `NR`) into ratings that are still empty.

You can undo a rating MetaMender wrote with **Restore original** in the
[Details window](details.md#restore-original-and-the-lock); that also locks
the field so MetaMender leaves it alone.
